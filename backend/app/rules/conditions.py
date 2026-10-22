"""Builder conditions: a list of {"field", "op", "value"}, all of which must match.

Ticket conditions become SQL, so scheduled rules and test runs are one query.
Change conditions ("what changed") only apply to on_update rules and are checked
against the events of the save that triggered the rule.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select

from app import sla
from app.models import Comment, Impact, Priority, Ticket, TicketSource, TicketStatus, Urgency, User, UserRole

TICKET_FIELDS: dict[str, dict] = {
    "queue": {"ops": ["in", "not_in"], "value": "queue_ids", "label": "Queue"},
    "status": {"ops": ["in", "not_in"], "value": [s.value for s in TicketStatus], "label": "Status"},
    "priority": {"ops": ["in", "not_in"], "value": [p.value for p in Priority], "label": "Priority"},
    "source": {"ops": ["in", "not_in"], "value": [s.value for s in TicketSource], "label": "Source"},
    "category": {"ops": ["in", "not_in", "contains", "is_empty", "is_set"], "value": "text", "label": "Category"},
    "assignee": {"ops": ["in", "is_empty", "is_set"], "value": "user_ids", "label": "Assignee"},
    "requester": {"ops": ["eq", "domain"], "value": "text", "label": "Requester email"},
    "text": {"ops": ["contains_any"], "value": "keywords", "label": "Subject or description"},
    "age_minutes": {"ops": ["gt", "lt"], "value": "minutes", "label": "Minutes since created"},
    "idle_minutes": {"ops": ["gt", "lt"], "value": "minutes", "label": "Minutes since last update"},
    "status_minutes": {"ops": ["gt", "lt"], "value": "minutes", "label": "Minutes in current status"},
    "sla_state": {"ops": ["in"], "value": ["ok", "at_risk", "breached"], "label": "SLA state"},
    "urgency": {"ops": ["in", "not_in"], "value": [u.value for u in Urgency], "label": "Urgency"},
    "impact": {"ops": ["in", "not_in"], "value": [i.value for i in Impact], "label": "Business impact"},
    "last_reply_by": {"ops": ["eq"], "value": ["requester", "agent"], "label": "Last public reply by"},
}

CHANGES = [
    "status",
    "priority",
    "queue",
    "assignee",
    "requester",
    "requester_reply",
    "agent_reply",
    "internal_note",
    "attachment",
    "escalated",
    "edited",
]
CHANGE_FIELDS: dict[str, dict] = {
    "change": {"ops": ["in"], "value": CHANGES, "label": "What changed (any of)"},
    "status_changed_to": {"ops": ["in"], "value": [s.value for s in TicketStatus], "label": "Status changed to"},
}

OPEN = Ticket.status.not_in(sla.CLOSED_STATUSES)


class ConditionError(ValueError):
    pass


def validate(conditions: list[dict], *, allow_changes: bool) -> None:
    for i, c in enumerate(conditions, 1):
        field, op, value = c.get("field"), c.get("op"), c.get("value")
        spec = TICKET_FIELDS.get(field) or (CHANGE_FIELDS.get(field) if allow_changes else None)
        if spec is None:
            hint = " (change conditions only work on on_update rules)" if field in CHANGE_FIELDS else ""
            raise ConditionError(f"Condition {i}: unknown field {field!r}{hint}")
        if op not in spec["ops"]:
            raise ConditionError(f"Condition {i}: {field} supports {', '.join(spec['ops'])}, not {op!r}")
        if op in ("is_empty", "is_set"):
            continue
        allowed = spec["value"]
        if op in ("in", "not_in", "contains_any"):
            if not isinstance(value, list) or not value:
                raise ConditionError(f"Condition {i}: {field} {op} needs a non-empty list")
            if isinstance(allowed, list) and not set(value) <= set(allowed):
                raise ConditionError(f"Condition {i}: {field} values must be from {', '.join(allowed)}")
            if allowed in ("queue_ids", "user_ids") and not all(isinstance(v, int) for v in value):
                raise ConditionError(f"Condition {i}: {field} needs ids")
        elif allowed == "minutes":
            if not isinstance(value, int) or value < 0:
                raise ConditionError(f"Condition {i}: {field} needs a number of minutes")
        elif isinstance(allowed, list):
            if value not in allowed:
                raise ConditionError(f"Condition {i}: {field} must be one of {', '.join(allowed)}")
        elif not isinstance(value, str) or not value.strip():
            raise ConditionError(f"Condition {i}: {field} needs a value")


def _list(column, op: str, value: list[Any]) -> ColumnElement:
    return column.in_(value) if op == "in" else or_(column.is_(None), column.not_in(value))


def _minutes(column, op: str, minutes: int, now: datetime) -> ColumnElement:
    cutoff = now - timedelta(minutes=minutes)
    # "More than N minutes ago" means the timestamp is older than the cutoff.
    return column < cutoff if op == "gt" else column >= cutoff


def _last_reply_role():
    latest = (
        select(User.role)
        .join(Comment, Comment.author_id == User.id)
        .where(Comment.ticket_id == Ticket.id, Comment.is_internal.is_(False))
        .order_by(Comment.created_at.desc(), Comment.id.desc())
        .limit(1)
        .correlate(Ticket)
        .scalar_subquery()
    )
    return latest


def clause(c: dict, now: datetime) -> ColumnElement | None:
    """SQL for one ticket condition, or None for change conditions (checked separately)."""
    field, op, value = c["field"], c["op"], c.get("value")
    match field:
        case "queue":
            return _list(Ticket.queue_id, op, value)
        case "status":
            return _list(Ticket.status, op, value)
        case "priority":
            return _list(Ticket.priority, op, value)
        case "source":
            return _list(Ticket.source, op, value)
        case "urgency":
            return _list(Ticket.urgency, op, value)
        case "impact":
            return _list(Ticket.impact, op, value)
        case "category":
            if op == "is_empty":
                return or_(Ticket.category.is_(None), Ticket.category == "")
            if op == "is_set":
                return and_(Ticket.category.is_not(None), Ticket.category != "")
            if op == "contains":
                return Ticket.category.ilike(f"%{value}%")
            return _list(func.lower(Ticket.category), op, [v.lower() for v in value])
        case "assignee":
            if op == "is_empty":
                return Ticket.assignee_id.is_(None)
            if op == "is_set":
                return Ticket.assignee_id.is_not(None)
            return Ticket.assignee_id.in_(value)
        case "requester":
            email = select(func.lower(User.email)).where(User.id == Ticket.requester_id).correlate(Ticket).scalar_subquery()
            v = value.strip().lower()
            return email.endswith("@" + v.lstrip("@")) if op == "domain" else email == v
        case "text":
            words = [w.strip() for w in value if w.strip()]
            return or_(*[or_(Ticket.subject.ilike(f"%{w}%"), Ticket.description.ilike(f"%{w}%")) for w in words])
        case "age_minutes":
            return _minutes(Ticket.created_at, op, value, now)
        case "idle_minutes":
            return _minutes(Ticket.updated_at, op, value, now)
        case "status_minutes":
            return _minutes(func.coalesce(Ticket.status_changed_at, Ticket.created_at), op, value, now)
        case "sla_state":
            states = []
            if "breached" in value:
                states.append(and_(OPEN, Ticket.resolution_due_at <= now))
            if "at_risk" in value:
                states.append(and_(OPEN, Ticket.sla_at_risk_at <= now, Ticket.resolution_due_at > now))
            if "ok" in value:
                states.append(and_(OPEN, Ticket.resolution_due_at > now, or_(Ticket.sla_at_risk_at.is_(None), Ticket.sla_at_risk_at > now)))
            return or_(*states)
        case "last_reply_by":
            role = _last_reply_role()
            if value == "requester":
                return role == UserRole.requester
            return role.in_([UserRole.agent, UserRole.admin])
        case "change" | "status_changed_to":
            return None
    raise ConditionError(f"Unknown field {field!r}")


def where(conditions: list[dict], now: datetime) -> list[ColumnElement]:
    return [w for c in conditions if (w := clause(c, now)) is not None]


def describe_changes(events: list[tuple[str, dict, str | None]]) -> tuple[set[str], set[str]]:
    """From the save's events -> (what changed, statuses it changed to).

    Each event is (type, data, author_role) where author_role is the role of the
    comment author for comment events.
    """
    changed: set[str] = set()
    statuses: set[str] = set()
    for type_, data, author_role in events:
        match type_:
            case "status_changed":
                changed.add("status")
                statuses.add(str(data.get("after")))
            case "priority_changed":
                changed.add("priority")
            case "assigned":
                before, after = data.get("before", {}), data.get("after", {})
                if before.get("queue_id") != after.get("queue_id"):
                    changed.add("queue")
                if before.get("assignee_id") != after.get("assignee_id"):
                    changed.add("assignee")
            case "requester_changed":
                changed.add("requester")
            case "comment_added":
                if data.get("is_internal"):
                    changed.add("internal_note")
                elif author_role == UserRole.requester:
                    changed.add("requester_reply")
                else:
                    changed.add("agent_reply")
            case "attachment_added":
                changed.add("attachment")
            case "escalated":
                changed.add("escalated")
                changed.add("priority")
            case "updated":
                changed.add("edited")
    return changed, statuses


def changes_match(conditions: list[dict], changed: set[str], statuses: set[str]) -> bool:
    for c in conditions:
        if c["field"] == "change" and not changed & set(c["value"]):
            return False
        if c["field"] == "status_changed_to" and not statuses & set(c["value"]):
            return False
    return True


def uses_changes(conditions: list[dict]) -> bool:
    return any(c.get("field") in CHANGE_FIELDS for c in conditions)

