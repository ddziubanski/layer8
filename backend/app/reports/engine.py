"""Reports: count and measure any dataset, grouped by up to two of its fields.

A dataset (tickets, ...) declares what can be grouped by (dimensions), measured
(measures) and filtered, as SQL expressions. A report is a ReportSpec over one dataset;
ready-made reports (presets.py) are just specs. Nothing outside these declarations can be
queried, and each dataset applies the viewer's access (agents see only their queues).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Select, and_, case, extract, false, func, or_, select
from sqlalchemy.orm import Session, aliased, join

from app import sla
from app.models import Comment, FieldOption, InboundEmail, OutboundEmail, POLine, POStatus, PurchaseOrder, Queue, Ticket, TicketStatus, TicketSurvey, User, UserRole, Vendor

MAX_ROWS = 1000


class ReportError(ValueError):
    """A report can't run as asked; the message is safe to show."""


@dataclass(frozen=True)
class Dimension:
    label: str
    expr: Callable[[Any], ColumnElement]  # given the dataset's table aliases
    # time: a date bucket (sorted by time, shown as a date); else text
    kind: Literal["text", "time"] = "text"
    # Shown for NULL, e.g. "Unassigned".
    empty: str = "(none)"
    # A code like waiting_on_user, shown as "Waiting on user".
    code: bool = False


@dataclass(frozen=True)
class Measure:
    label: str
    expr: Callable[[Any], ColumnElement]
    kind: Literal["count", "hours", "percent", "number", "money"] = "count"


@dataclass(frozen=True)
class Filter:
    label: str
    expr: Callable[[Any], ColumnElement]
    # Where the UI gets the choices: "enum:<name>", "queues", "agents" (IDs) or "text".
    choices: str

    @property
    def by_id(self) -> bool:
        return self.choices in ("queues", "agents") or self.choices.startswith("options:")


# How codes read in reports (others: underscores to spaces, capitalised).
CODE_LABELS = {"api": "API", "single": "Just me", "many": "Several people", "business": "Business impact"}

# A filter value meaning "empty" (no technician, no category...).
NONE = "none"

# The choices of "enum:<name>" filters, as (value, label).
ENUM_CHOICES: dict[str, list[tuple[str, str]]] = {
    "status": [("new", "New"), ("open", "Open"), ("waiting_on_user", "Waiting on user"), ("resolved", "Resolved"), ("closed", "Closed")],
    "priority": [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")],
    "source": [("email", "Email"), ("portal", "Portal"), ("agent", "Agent"), ("api", "API")],
    "role": [("requester", "Requester"), ("agent", "Agent"), ("admin", "Admin")],
    "inbound": [("processed", "Processed"), ("rejected", "Rejected"), ("failed", "Failed")],
    "outbound": [("queued", "Queued"), ("sent", "Sent"), ("failed", "Failed")],
    "yesno": [("true", "Yes"), ("false", "No")],
    "po_status": [("draft", "Draft"), ("requested", "Requested"), ("approved", "Approved"), ("ordered", "Ordered"),
                  ("partially_received", "Partially received"), ("received", "Received"), ("cancelled", "Cancelled"), ("closed", "Closed")],
}


@dataclass
class Dataset:
    name: str
    label: str
    # The FROM clause with its joins; returns (select_from, aliases).
    base: Callable[[], tuple[Any, Any]]
    dimensions: dict[str, Dimension]
    measures: dict[str, Measure]
    filters: dict[str, Filter]
    # Date fields a period can apply to: name -> (label, expr).
    dates: dict[str, tuple[str, Callable[[Any], ColumnElement]]]
    # Extra WHERE for what this viewer may see; None for everything.
    access: Callable[[Any, User], ColumnElement | None] = lambda a, u: None
    default_measures: list[str] = field(default_factory=lambda: ["count"])


class ReportSpec(BaseModel):
    dataset: str = "tickets"
    # Up to two: rows, then columns of a breakdown (each combination is a row).
    group_by: list[str] = Field(default_factory=list, max_length=2)
    measures: list[str] = Field(default_factory=lambda: ["count"], min_length=1, max_length=8)
    date_field: str | None = None
    date_from: date | None = None
    date_to: date | None = None  # inclusive
    # filter name -> values (any of)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    # Biggest first by the first measure; time groups are always oldest first.
    sort: Literal["measure", "label"] = "measure"
    limit: int = Field(default=200, ge=1, le=MAX_ROWS)
    # The query of an SQL report (dataset "sql", admins only); see sqlreport.py.
    sql: str | None = Field(default=None, max_length=20_000)


class ReportColumn(BaseModel):
    key: str
    label: str
    kind: str


class ReportResult(BaseModel):
    columns: list[ReportColumn]
    rows: list[list[Any]]
    # Totals of the count-type measures over everything matched (not just the rows shown).
    totals: dict[str, float | None]
    truncated: bool


# Tickets

_OPEN = Ticket.status.not_in([TicketStatus.resolved, TicketStatus.closed])


def _hours(later, earlier):
    return extract("epoch", later - earlier) / 3600.0


def _tickets_base():
    q = aliased(Queue, name="q")
    assignee = aliased(User, name="assignee")
    requester = aliased(User, name="requester")
    resolution = aliased(FieldOption, name="resolution")
    closure = aliased(FieldOption, name="closure")
    dept = aliased(FieldOption, name="dept")
    frm = (
        join(Ticket, q, q.id == Ticket.queue_id)
        .outerjoin(assignee, assignee.id == Ticket.assignee_id)
        .join(requester, requester.id == Ticket.requester_id)
        .outerjoin(resolution, resolution.id == Ticket.resolution_type_id)
        .outerjoin(closure, closure.id == Ticket.closure_reason_id)
        .outerjoin(dept, dept.id == Ticket.department_id)
    )
    return frm, {"q": q, "assignee": assignee, "requester": requester, "resolution": resolution, "closure": closure, "dept": dept}


def _response_breached(now):
    return or_(
        and_(Ticket.first_responded_at.is_not(None), Ticket.first_responded_at > Ticket.first_response_due_at),
        and_(Ticket.first_responded_at.is_(None), Ticket.first_response_due_at < now),
    )


def _resolution_breached(now):
    return or_(
        and_(Ticket.resolved_at.is_not(None), Ticket.resolved_at > Ticket.resolution_due_at),
        and_(Ticket.resolved_at.is_(None), Ticket.resolution_due_at < now),
    )


def _ticket_access(a, user: User):
    from app.api.tickets import visible_queue_ids

    ids = visible_queue_ids(user)
    return None if ids is None else Ticket.queue_id.in_(ids)


def _count_if(cond):
    return func.count(case((cond, 1)))


def tickets_dataset() -> Dataset:
    now = sla.utcnow()
    return Dataset(
        name="tickets",
        label="Tickets",
        base=_tickets_base,
        dimensions={
            "status": Dimension("Status", lambda a: Ticket.status, code=True),
            "priority": Dimension("Priority", lambda a: Ticket.priority, code=True),
            "source": Dimension("Source", lambda a: Ticket.source, code=True),
            "category": Dimension("Category", lambda a: Ticket.category),
            "queue": Dimension("Queue", lambda a: a["q"].name),
            "assignee": Dimension("Technician", lambda a: a["assignee"].display_name, empty="Unassigned"),
            "requester": Dimension("Requester", lambda a: a["requester"].display_name),
            "department": Dimension("Department", lambda a: a["dept"].label),
            "requester_ad_department": Dimension("Requester's AD department", lambda a: a["requester"].department),
            "resolution_type": Dimension("Resolution type", lambda a: a["resolution"].label, empty="Not set"),
            "closure_reason": Dimension("Closure reason", lambda a: a["closure"].label, empty="Not set"),
            "impact": Dimension("Who is affected", lambda a: Ticket.impact, code=True),
            "urgency": Dimension("How urgent", lambda a: Ticket.urgency, code=True),
            "created_day": Dimension("Opened (day)", lambda a: func.date_trunc("day", Ticket.created_at), "time"),
            "created_week": Dimension("Opened (week)", lambda a: func.date_trunc("week", Ticket.created_at), "time"),
            "created_month": Dimension("Opened (month)", lambda a: func.date_trunc("month", Ticket.created_at), "time"),
            "resolved_month": Dimension("Resolved (month)", lambda a: func.date_trunc("month", Ticket.resolved_at), "time", empty="Not resolved"),
        },
        measures={
            "count": Measure("Tickets", lambda a: func.count()),
            "open": Measure("Still open", lambda a: _count_if(_OPEN)),
            "resolved": Measure("Resolved or closed", lambda a: _count_if(Ticket.resolved_at.is_not(None))),
            "avg_hours_first_response": Measure(
                "Avg. hours to first response", lambda a: func.avg(_hours(Ticket.first_responded_at, Ticket.created_at)), "hours"
            ),
            "avg_hours_to_resolve": Measure("Avg. hours to resolve", lambda a: func.avg(_hours(Ticket.resolved_at, Ticket.created_at)), "hours"),
            "response_breached": Measure("First response SLA missed", lambda a: _count_if(_response_breached(now))),
            "resolution_breached": Measure("Resolution SLA missed", lambda a: _count_if(_resolution_breached(now))),
            "resolution_met_pct": Measure(
                "Resolution SLA met (%)",
                lambda a: 100.0 * _count_if(and_(Ticket.resolution_due_at.is_not(None), ~_resolution_breached(now)))
                / func.nullif(_count_if(Ticket.resolution_due_at.is_not(None)), 0),
                "percent",
            ),
        },
        filters={
            "status": Filter("Status", lambda a: Ticket.status, "enum:status"),
            "priority": Filter("Priority", lambda a: Ticket.priority, "enum:priority"),
            "source": Filter("Source", lambda a: Ticket.source, "enum:source"),
            "queue": Filter("Queue", lambda a: Ticket.queue_id, "queues"),
            "assignee": Filter("Technician", lambda a: Ticket.assignee_id, "agents"),
            "category": Filter("Category", lambda a: Ticket.category, "text"),
            "department": Filter("Department", lambda a: Ticket.department_id, "options:department"),
            "resolution_type": Filter("Resolution type", lambda a: Ticket.resolution_type_id, "options:resolution_type"),
            "closure_reason": Filter("Closure reason", lambda a: Ticket.closure_reason_id, "options:closure_reason"),
        },
        dates={
            "created": ("Opened", lambda a: Ticket.created_at),
            "resolved": ("Resolved", lambda a: Ticket.resolved_at),
            "closed": ("Closed", lambda a: Ticket.closed_at),
        },
        access=_ticket_access,
    )


# Ticket activity: replies and notes


def _activity_base():
    author = aliased(User, name="author")
    q = aliased(Queue, name="q")
    frm = join(Comment, Ticket, Ticket.id == Comment.ticket_id).join(author, author.id == Comment.author_id).join(q, q.id == Ticket.queue_id)
    return frm, {"author": author, "q": q}


def _activity_kind(a):
    staff = a["author"].role.in_([UserRole.agent, UserRole.admin])
    return case(
        (Comment.is_internal, "Internal note"),
        (and_(staff, Comment.author_id != Ticket.requester_id), "Reply to requester"),
        else_="Requester reply",
    )


def _ticket_access_via(a, user: User):
    from app.api.tickets import visible_queue_ids

    ids = visible_queue_ids(user)
    return None if ids is None else Ticket.queue_id.in_(ids)


def activity_dataset() -> Dataset:
    return Dataset(
        name="activity",
        label="Ticket activity",
        base=_activity_base,
        dimensions={
            "author": Dimension("Who", lambda a: a["author"].display_name),
            "kind": Dimension("Kind", _activity_kind),
            "queue": Dimension("Queue", lambda a: a["q"].name),
            "source": Dimension("Via", lambda a: Comment.source, code=True),
            "day": Dimension("Day", lambda a: func.date_trunc("day", Comment.created_at), "time"),
            "week": Dimension("Week", lambda a: func.date_trunc("week", Comment.created_at), "time"),
            "month": Dimension("Month", lambda a: func.date_trunc("month", Comment.created_at), "time"),
        },
        measures={
            "count": Measure("Replies and notes", lambda a: func.count()),
            "tickets": Measure("Tickets worked on", lambda a: func.count(func.distinct(Comment.ticket_id))),
            "notes": Measure("Internal notes", lambda a: _count_if(Comment.is_internal)),
            "emailed": Measure("Emailed to requester", lambda a: _count_if(Comment.emailed)),
        },
        filters={
            "author": Filter("Who", lambda a: Comment.author_id, "agents"),
            "queue": Filter("Queue", lambda a: Ticket.queue_id, "queues"),
            "internal": Filter("Internal notes only", lambda a: Comment.is_internal, "enum:yesno"),
        },
        dates={"created": ("Written", lambda a: Comment.created_at)},
        access=_ticket_access_via,
    )


# Users


def users_dataset() -> Dataset:
    return Dataset(
        name="users",
        label="Users",
        base=lambda: (User.__table__, {}),
        dimensions={
            "role": Dimension("Role", lambda a: User.role, code=True),
            "department": Dimension("Department (AD)", lambda a: User.department),
            "title": Dimension("Job title (AD)", lambda a: User.title),
            "auth_source": Dimension("Signs in with", lambda a: case((User.auth_source == "directory", "Directory (AD)"), else_="Local account")),
            "active": Dimension("Active", lambda a: case((User.is_active, "Active"), else_="Inactive")),
            "created_month": Dimension("Added (month)", lambda a: func.date_trunc("month", User.created_at), "time"),
        },
        measures={
            "count": Measure("People", lambda a: func.count()),
            "active": Measure("Active", lambda a: _count_if(User.is_active)),
            "staff": Measure("Technicians and admins", lambda a: _count_if(User.role.in_([UserRole.agent, UserRole.admin]))),
        },
        filters={
            "role": Filter("Role", lambda a: User.role, "enum:role"),
            "active": Filter("Active", lambda a: User.is_active, "enum:yesno"),
        },
        dates={"created": ("Added", lambda a: User.created_at)},
    )


# Purchase orders

PO_DONE = [POStatus.received, POStatus.closed, POStatus.cancelled]


def _po_value():
    """A PO's total: its lines with tax, plus shipping."""
    lines = (
        select(func.coalesce(func.sum(POLine.quantity * POLine.unit_price * (1 + POLine.tax_rate / 100)), 0))
        .where(POLine.po_id == PurchaseOrder.id)
        .correlate(PurchaseOrder)
        .scalar_subquery()
    )
    return lines + PurchaseOrder.shipping


def purchase_orders_dataset() -> Dataset:
    def base():
        dept = aliased(FieldOption)
        frm = PurchaseOrder.__table__.outerjoin(Vendor.__table__, Vendor.id == PurchaseOrder.vendor_id).outerjoin(dept, dept.id == PurchaseOrder.department_id)
        return frm, {"dept": dept}

    live = PurchaseOrder.status != POStatus.cancelled
    return Dataset(
        name="purchase_orders",
        label="Purchase orders",
        base=base,
        dimensions={
            "vendor": Dimension("Vendor", lambda a: Vendor.name),
            "department": Dimension("Department", lambda a: a["dept"].label),
            "status": Dimension("Status", lambda a: PurchaseOrder.status, code=True),
            "currency": Dimension("Currency", lambda a: PurchaseOrder.currency),
            "order_month": Dimension("Ordered (month)", lambda a: func.date_trunc("month", PurchaseOrder.order_date), "time"),
            "created_month": Dimension("Created (month)", lambda a: func.date_trunc("month", PurchaseOrder.created_at), "time"),
        },
        measures={
            "count": Measure("Purchase orders", lambda a: func.count()),
            "value": Measure("Value (not cancelled)", lambda a: func.sum(case((live, _po_value()), else_=0)), "money"),
            "avg_value": Measure("Average value", lambda a: func.avg(case((live, _po_value()))), "money"),
            "open": Measure("Still open", lambda a: _count_if(PurchaseOrder.status.not_in(PO_DONE))),
            "late": Measure("Late (past expected, not in)", lambda a: _count_if(and_(PurchaseOrder.status.not_in(PO_DONE), PurchaseOrder.expected_date < func.current_date()))),
        },
        filters={"status": Filter("Status", lambda a: PurchaseOrder.status, "enum:po_status")},
        dates={
            "created": ("Created", lambda a: PurchaseOrder.created_at),
            "ordered": ("Ordered", lambda a: PurchaseOrder.order_date),
            "expected": ("Expected", lambda a: PurchaseOrder.expected_date),
        },
    )


# Email


def _admins_only(a, user: User):
    return None if user.role == UserRole.admin else false()


def email_in_dataset() -> Dataset:
    return Dataset(
        name="email_in",
        label="Email received",
        base=lambda: (InboundEmail.__table__, {}),
        dimensions={
            "status": Dimension("What happened", lambda a: InboundEmail.status, code=True),
            "domain": Dimension("Sender's domain", lambda a: func.lower(func.split_part(InboundEmail.from_address, "@", 2))),
            "sender": Dimension("Sender", lambda a: func.lower(InboundEmail.from_address)),
            "reason": Dimension("Why", lambda a: InboundEmail.reason, empty="—"),
            "day": Dimension("Day", lambda a: func.date_trunc("day", InboundEmail.processed_at), "time"),
            "week": Dimension("Week", lambda a: func.date_trunc("week", InboundEmail.processed_at), "time"),
        },
        measures={
            "count": Measure("Emails", lambda a: func.count()),
            "tickets": Measure("New tickets", lambda a: _count_if(and_(InboundEmail.ticket_id.is_not(None), InboundEmail.comment_id.is_(None)))),
            "replies": Measure("Replies to tickets", lambda a: _count_if(InboundEmail.comment_id.is_not(None))),
        },
        filters={"status": Filter("What happened", lambda a: InboundEmail.status, "enum:inbound")},
        dates={"processed": ("Received", lambda a: InboundEmail.processed_at)},
        access=_admins_only,
    )


def email_out_dataset() -> Dataset:
    return Dataset(
        name="email_out",
        label="Email sent",
        base=lambda: (OutboundEmail.__table__, {}),
        dimensions={
            "status": Dimension("Status", lambda a: OutboundEmail.status, code=True),
            "kind": Dimension(
                "Kind",
                lambda a: case(
                    (OutboundEmail.ticket_id.is_(None), "Report"),
                    (OutboundEmail.is_staff_notice, "Notice to staff"),
                    else_="To requester",
                ),
            ),
            "domain": Dimension("Recipient's domain", lambda a: func.lower(func.split_part(OutboundEmail.to_address, "@", 2))),
            "day": Dimension("Day", lambda a: func.date_trunc("day", OutboundEmail.created_at), "time"),
            "week": Dimension("Week", lambda a: func.date_trunc("week", OutboundEmail.created_at), "time"),
        },
        measures={
            "count": Measure("Emails", lambda a: func.count()),
            "failed": Measure("Failed", lambda a: _count_if(OutboundEmail.status == "failed")),
            "attempts": Measure("Avg. attempts", lambda a: func.avg(OutboundEmail.attempts), "number"),
        },
        filters={"status": Filter("Status", lambda a: OutboundEmail.status, "enum:outbound")},
        dates={"created": ("Queued", lambda a: OutboundEmail.created_at)},
        access=_admins_only,
    )


def sql_dataset() -> Dataset:
    """An admin's own SELECT (sqlreport.py): its columns are whatever the query returns."""
    return Dataset(
        name="sql",
        label="SQL query",
        base=lambda: (None, {}),
        dimensions={},
        measures={},
        filters={},
        dates={"period": ("Period", lambda a: None)},
        access=_admins_only,
    )


# Satisfaction surveys


def _surveys_base():
    q = aliased(Queue, name="q")
    tech = aliased(User, name="tech")
    frm = (
        join(TicketSurvey, Ticket, Ticket.id == TicketSurvey.ticket_id)
        .outerjoin(q, q.id == TicketSurvey.queue_id)
        .outerjoin(tech, tech.id == TicketSurvey.assignee_id)
    )
    return frm, {"q": q, "tech": tech}


def _rating_label(a):
    return case(
        (TicketSurvey.rating == 5, "5 Very happy"), (TicketSurvey.rating == 4, "4 Happy"), (TicketSurvey.rating == 3, "3 OK"),
        (TicketSurvey.rating == 2, "2 Unhappy"), (TicketSurvey.rating == 1, "1 Very unhappy"), else_=None,
    )


def surveys_dataset() -> Dataset:
    answered = TicketSurvey.rating.is_not(None)
    return Dataset(
        name="surveys",
        label="Satisfaction surveys",
        base=_surveys_base,
        dimensions={
            "technician": Dimension("Technician", lambda a: a["tech"].display_name, empty="Unassigned"),
            "queue": Dimension("Queue", lambda a: a["q"].name),
            "category": Dimension("Category", lambda a: Ticket.category),
            "rating": Dimension("Rating", _rating_label, empty="Not answered"),
            "sent_week": Dimension("Week sent", lambda a: func.date_trunc("week", TicketSurvey.sent_at), "time"),
            "sent_month": Dimension("Month sent", lambda a: func.date_trunc("month", TicketSurvey.sent_at), "time"),
        },
        measures={
            "count": Measure("Surveys sent", lambda a: func.count()),
            "answered": Measure("Answered", lambda a: _count_if(answered)),
            "response_pct": Measure("Answered (%)", lambda a: 100.0 * _count_if(answered) / func.nullif(func.count(), 0), "percent"),
            "avg_rating": Measure("Average rating (1-5)", lambda a: func.avg(TicketSurvey.rating), "number"),
            "happy_pct": Measure("Happy (4-5, %)", lambda a: 100.0 * _count_if(TicketSurvey.rating >= 4) / func.nullif(_count_if(answered), 0), "percent"),
            "unhappy": Measure("Unhappy (1-2)", lambda a: _count_if(TicketSurvey.rating <= 2)),
        },
        filters={
            "technician": Filter("Technician", lambda a: TicketSurvey.assignee_id, "agents"),
            "queue": Filter("Queue", lambda a: TicketSurvey.queue_id, "queues"),
            "answered": Filter("Answered", lambda a: answered, "enum:yesno"),
        },
        dates={"sent": ("Sent", lambda a: TicketSurvey.sent_at), "answered": ("Answered", lambda a: TicketSurvey.answered_at)},
        access=_ticket_access_via,
        default_measures=["count", "answered", "avg_rating"],
    )


DATASETS: dict[str, Callable[[], Dataset]] = {
    "tickets": tickets_dataset,
    "activity": activity_dataset,
    "users": users_dataset,
    "email_in": email_in_dataset,
    "email_out": email_out_dataset,
    "purchase_orders": purchase_orders_dataset,
    "surveys": surveys_dataset,
    "sql": sql_dataset,
}


def dataset(name: str) -> Dataset:
    if name not in DATASETS:
        raise ReportError(f"Unknown dataset {name!r}")
    return DATASETS[name]()


def _where(ds: Dataset, a, spec: ReportSpec, user: User) -> list[ColumnElement]:
    conds = []
    access = ds.access(a, user)
    if access is not None:
        conds.append(access)
    if spec.date_from or spec.date_to:
        name = spec.date_field or next(iter(ds.dates))
        if name not in ds.dates:
            raise ReportError(f"{ds.label} have no date {name!r}")
        col = ds.dates[name][1](a)
        tz = datetime.now().astimezone().tzinfo
        if spec.date_from:
            conds.append(col >= datetime.combine(spec.date_from, time.min, tz))
        if spec.date_to:
            conds.append(col < datetime.combine(spec.date_to + timedelta(days=1), time.min, tz))
    for name, values in spec.filters.items():
        if name not in ds.filters:
            raise ReportError(f"Can't filter {ds.label.lower()} by {name!r}")
        if not values:
            continue
        f = ds.filters[name]
        expr = f.expr(a)
        actual: list = [v for v in values if v != NONE]
        if f.by_id:
            try:
                actual = [int(v) for v in actual]
            except ValueError:
                raise ReportError(f"{f.label}: expected IDs") from None
        elif f.choices == "enum:yesno":
            actual = [v == "true" for v in actual]
        parts = [expr.in_(actual)] if actual else []
        if NONE in values:
            parts.append(expr.is_(None))
        conds.append(or_(*parts) if parts else false())
    return conds


def validate(spec: ReportSpec) -> Dataset:
    """The spec's dataset, or ReportError naming what it doesn't have."""
    ds = dataset(spec.dataset)
    if ds.name == "sql":
        if not (spec.sql or "").strip():
            raise ReportError("Write the report's SQL query")
        return ds
    for g in spec.group_by:
        if g not in ds.dimensions:
            raise ReportError(f"Can't group {ds.label.lower()} by {g!r}")
    for m in spec.measures:
        if m not in ds.measures:
            raise ReportError(f"Unknown measure {m!r} for {ds.label.lower()}")
    for f in spec.filters:
        if f not in ds.filters:
            raise ReportError(f"Can't filter {ds.label.lower()} by {f!r}")
    if spec.date_field and spec.date_field not in ds.dates:
        raise ReportError(f"{ds.label} have no date {spec.date_field!r}")
    return ds


def run(session: Session, spec: ReportSpec, user: User) -> ReportResult:
    ds = validate(spec)
    if ds.name == "sql":
        if user.role != UserRole.admin:
            raise ReportError("SQL reports are for admins")
        from app.reports import sqlreport

        return sqlreport.run(spec.sql or "", spec.date_from, spec.date_to, spec.limit)
    frm, a = ds.base()
    where = _where(ds, a, spec, user)

    dims = [(g, ds.dimensions[g]) for g in spec.group_by]
    dim_cols = [d.expr(a).label(f"g{i}") for i, (_, d) in enumerate(dims)]
    measure_cols = [ds.measures[m].expr(a).label(f"m{i}") for i, m in enumerate(spec.measures)]
    stmt: Select = select(*dim_cols, *measure_cols).select_from(frm).where(*where)
    if dim_cols:
        stmt = stmt.group_by(*dim_cols)
        order = []
        for col, (_, d) in zip(dim_cols, dims, strict=True):
            if d.kind == "time":
                order.append(col.asc().nulls_last())
        if spec.sort == "measure" and not order:
            order.append(measure_cols[0].desc().nulls_last())
        order += [col.asc().nulls_last() for col, (_, d) in zip(dim_cols, dims, strict=True) if d.kind != "time"]
        stmt = stmt.order_by(*order).limit(spec.limit + 1)
    raw = session.execute(stmt).all()
    truncated = len(raw) > spec.limit
    raw = raw[: spec.limit]

    columns = [ReportColumn(key=g, label=d.label, kind=d.kind) for g, d in dims]
    columns += [ReportColumn(key=m, label=ds.measures[m].label, kind=ds.measures[m].kind) for m in spec.measures]
    rows = []
    for r in raw:
        out: list[Any] = []
        for i, (_, d) in enumerate(dims):
            v = r[i]
            if v is None:
                out.append(d.empty)
            elif d.kind == "time":
                out.append(v.date().isoformat())
            elif d.code:
                out.append(CODE_LABELS.get(str(v), str(v).replace("_", " ").capitalize()))
            else:
                out.append(str(v))
        for i, m in enumerate(spec.measures):
            v = r[len(dims) + i]
            kind = ds.measures[m].kind
            out.append(None if v is None else int(v) if kind == "count" else round(float(v), 2 if kind == "money" else 1))
        rows.append(out)

    totals: dict[str, float | None] = {}
    count_measures = [m for m in spec.measures if ds.measures[m].kind == "count"]
    if dims and count_measures:
        total_stmt = select(*[ds.measures[m].expr(a) for m in count_measures]).select_from(frm).where(*where)
        values = session.execute(total_stmt).one()
        totals = {m: int(v or 0) for m, v in zip(count_measures, values, strict=True)}
    elif not dims:
        totals = {m: rows[0][i] for i, m in enumerate(spec.measures)} if rows else {}
    return ReportResult(columns=columns, rows=rows, totals=totals, truncated=truncated)


def available(user: User) -> list[dict]:
    """The datasets this person can report on, as (name, label)."""
    out = []
    for name, make in DATASETS.items():
        ds = make()
        if ds.access is _admins_only and user.role != UserRole.admin:
            continue
        out.append({"name": name, "label": ds.label})
    return out


def describe(name: str) -> dict:
    ds = dataset(name)
    return {
        "name": ds.name,
        "label": ds.label,
        "dimensions": [{"key": k, "label": d.label, "kind": d.kind} for k, d in ds.dimensions.items()],
        "measures": [{"key": k, "label": m.label, "kind": m.kind} for k, m in ds.measures.items()],
        "filters": [
            {"key": k, "label": f.label, "choices": f.choices, "values": [{"value": v, "label": label} for v, label in ENUM_CHOICES.get(f.choices.removeprefix("enum:"), [])] if f.choices.startswith("enum:") else []}
            for k, f in ds.filters.items()
        ],
        "dates": [{"key": k, "label": label} for k, (label, _) in ds.dates.items()],
    }
