# SPDX-License-Identifier: AGPL-3.0-or-later
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, case, func, select, union
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import aliased

from app import fields, numbering, permissions, search, services, sla, storage
from app.directory.sync import find_by_address
from app.auth import Agent, CurrentUser, SessionDep
from app.models import (
    Attachment,
    Comment,
    Device,
    FieldList,
    FieldOption,
    Group,
    Priority,
    Queue,
    Ticket,
    TicketEvent,
    TicketSource,
    TicketStatus,
    User,
    UserRole,
    group_members,
    queue_manager_groups,
    queue_managers,
    queue_owner_groups,
    queue_owners,
    queue_viewer_groups,
    queue_viewers,
)
from app.schemas import (
    AssignIn,
    UnacceptedOut,
    BulkAssignIn,
    BulkResultOut,
    ChildTicketOut,
    MergeIn,
    CommentCreate,
    CommentOut,
    EscalateIn,
    EventOut,
    RequesterChangeIn,
    SavedView,
    TicketScope,
    TicketCreate,
    TicketOut,
    TicketPage,
    TicketUpdate,
)

router = APIRouter(prefix="/api/tickets", tags=["tickets"])
log = logging.getLogger(__name__)
Merger = permissions.require("ticket_merge")
Deleter = permissions.require("ticket_delete")
BulkEditor = permissions.require("ticket_bulk")

OPEN = Ticket.status.not_in(sla.CLOSED_STATUSES)


def is_agent(user: User) -> bool:
    return user.role in services.AGENT_ROLES and not user.portal_only


def ticket_access(session, ticket_id: int, user: User) -> tuple[Ticket, bool]:
    """The ticket, and whether the user sees it as staff (internal notes, history, actions)
    or only as its requester. 404, not 403, for tickets they can't see at all."""
    ticket = session.get(Ticket, ticket_id)
    if ticket is not None:
        if is_agent(user) and ticket.queue.can_view(user):
            return ticket, True
        if ticket.requester_id == user.id:
            return ticket, False
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")


def load_ticket(session, ticket_id: int, user: User) -> Ticket:
    """Staff or requester access."""
    return ticket_access(session, ticket_id, user)[0]


def staff_ticket(session, ticket_id: int, user: User) -> Ticket:
    """Staff access only: agents who may see the ticket's queue."""
    ticket, staff = ticket_access(session, ticket_id, user)
    if not staff:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket


def visible_queue_ids(user: User):
    """Queues whose tickets this agent may see: unrestricted ones, plus restricted ones they own,
    manage or view (directly or through an active group). None for admins: everything."""
    if user.role == UserRole.admin:
        return None
    parts = [select(Queue.id).where(Queue.restricted.is_(False))]
    for users_link, groups_link in (
        (queue_owners, queue_owner_groups),
        (queue_managers, queue_manager_groups),
        (queue_viewers, queue_viewer_groups),
    ):
        parts.append(select(users_link.c.queue_id).where(users_link.c.user_id == user.id))
        parts.append(
            select(groups_link.c.queue_id)
            .join(group_members, group_members.c.group_id == groups_link.c.group_id)
            .join(Group, Group.id == groups_link.c.group_id)
            .where(group_members.c.user_id == user.id, Group.is_active)
        )
    return union(*parts)


def visible(stmt: Select, user: User) -> Select:
    ids = visible_queue_ids(user)
    return stmt if ids is None else stmt.where(Ticket.queue_id.in_(ids))


def owned_queue_ids(user: User):
    """Queues the user owns directly or through a group."""
    direct = select(queue_owners.c.queue_id).where(queue_owners.c.user_id == user.id)
    via_group = (
        select(queue_owner_groups.c.queue_id)
        .join(group_members, group_members.c.group_id == queue_owner_groups.c.group_id)
        .join(Group, Group.id == queue_owner_groups.c.group_id)
        .where(group_members.c.user_id == user.id, Group.is_active)
    )
    return direct.union(via_group)


def apply_scope(stmt: Select, scope: TicketScope, user: User) -> Select:
    if scope == TicketScope.mine:
        return stmt.where(Ticket.assignee_id == user.id)
    if scope == TicketScope.groups:
        return stmt.where(Ticket.queue_id.in_(owned_queue_ids(user)))
    return stmt


def apply_view(stmt: Select, view: SavedView, user: User) -> Select:
    match view:
        case SavedView.open:
            return stmt.where(OPEN)
        case SavedView.unassigned:
            return stmt.where(OPEN, Ticket.assignee_id.is_(None))
        case SavedView.mine:
            return stmt.where(OPEN, Ticket.assignee_id == user.id)
        case SavedView.my_queues:
            return stmt.where(OPEN, Ticket.queue_id.in_(owned_queue_ids(user)))
        case SavedView.sla_at_risk:
            # Includes breached tickets: both need attention now.
            return stmt.where(OPEN, Ticket.sla_at_risk_at <= sla.utcnow())
        case SavedView.waiting_on_user:
            return stmt.where(Ticket.status == TicketStatus.waiting_on_user)
        case SavedView.from_email:
            return stmt.where(OPEN, Ticket.source == TicketSource.email)
        case SavedView.from_portal:
            return stmt.where(OPEN, Ticket.source == TicketSource.portal)


def _computer(session, device_id: int | None) -> Device | None:
    if device_id is None:
        return None
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such device")
    return device


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def create_ticket(body: TicketCreate, user: Agent, session: SessionDep):
    """Agents log tickets, for themselves or on someone's behalf. Requesters use /api/portal/tickets."""
    parent = staff_ticket(session, body.parent_id, user) if body.parent_id is not None else None
    requester = parent.requester if parent is not None else user
    if body.requester_id is not None:
        requester = session.get(User, body.requester_id)
        if requester is None or not requester.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Requester not found")
    elif body.requester_email:
        requester = find_user(session, body.requester_email)
        if requester is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Requester not found")
    try:
        department = fields.choice(session, FieldList.department, body.department_id)
    except fields.FieldError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    queue = None
    if body.queue_id is not None:
        queue = session.get(Queue, body.queue_id)
        if queue is None or not queue.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Queue not found or inactive")

    ticket = services.create_ticket(
        session,
        subject=body.subject,
        description=body.description,
        category=body.category,
        requester=requester,
        source=body.source or TicketSource.agent,
        priority=body.priority or Priority.medium,
        actor=user,
        requested_queue=queue,
        # An explicit priority wins over the matrix.
        urgency=body.urgency if body.priority is None else None,
        impact=body.impact if body.priority is None else None,
        contact_phone=(body.contact_phone or "").strip() or None,
        department=department,
        computer=_computer(session, body.computer_id),
        parent=parent,
    )
    if body.priority is not None and (body.urgency or body.impact):
        ticket.urgency, ticket.impact = body.urgency, body.impact
    session.commit()
    return ticket


def find_user(session, email: str) -> User | None:
    return find_by_address(session, [email])


SortKey = Literal["due", "id", "subject", "requester", "source", "assignee", "queue", "priority", "status", "age", "updated"]
# Without a direction: what people usually want first for each column.
DEFAULT_DIR = {"due": "asc", "id": "desc", "subject": "asc", "requester": "asc", "source": "asc", "assignee": "asc",
               "queue": "asc", "priority": "desc", "status": "asc", "age": "desc", "updated": "desc"}
PRIORITY_RANK = {Priority.low: 1, Priority.medium: 2, Priority.high: 3, Priority.critical: 4}
STATUS_RANK = {TicketStatus.new: 1, TicketStatus.open: 2, TicketStatus.waiting_on_user: 3, TicketStatus.resolved: 4, TicketStatus.closed: 5}


def sorted_by(stmt: Select, sort: SortKey, direction: Literal["asc", "desc"] | None) -> Select:
    """Order a ticket query by a column of the queue. Ties: newest ticket first."""
    asc = (direction or DEFAULT_DIR[sort]) == "asc"
    if sort == "requester":
        who = aliased(User)
        stmt = stmt.join(who, who.id == Ticket.requester_id)
        key = func.lower(who.display_name)
    elif sort == "assignee":
        who = aliased(User)
        stmt = stmt.outerjoin(who, who.id == Ticket.assignee_id)
        key = func.lower(who.display_name)
    elif sort == "queue":
        q = aliased(Queue)
        stmt = stmt.join(q, q.id == Ticket.queue_id)
        key = func.lower(q.name)
    else:
        key = {
            "due": Ticket.resolution_due_at,
            "id": Ticket.id,
            "subject": func.lower(Ticket.subject),
            "source": Ticket.source,
            "priority": case(PRIORITY_RANK, value=Ticket.priority),
            "status": case(STATUS_RANK, value=Ticket.status),
            # Oldest first is "longest open first".
            "age": Ticket.created_at,
            "updated": Ticket.updated_at,
        }[sort]
        if sort == "age":
            asc = not asc
    # Empty values (no technician, no deadline) always go last.
    order = key.asc().nulls_last() if asc else key.desc().nulls_last()
    return stmt.order_by(order, Ticket.id.desc())


@router.get("", response_model=TicketPage)
def list_tickets(
    user: Agent,
    session: SessionDep,
    view: SavedView | None = None,
    scope: TicketScope = TicketScope.all,
    status_: list[TicketStatus] | None = Query(default=None, alias="status"),
    queue_id: int | None = None,
    assignee_id: int | None = None,
    source: TicketSource | None = None,
    requester_id: int | None = None,
    # Words to find in the subject, description and notes (all statuses unless filtered).
    q: str | None = Query(default=None, max_length=search.MAX_LENGTH),
    # Default: most urgent first (nearest resolution deadline).
    sort: SortKey = "due",
    dir: Literal["asc", "desc"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = apply_scope(visible(select(Ticket), user), scope, user)
    if view is not None:
        stmt = apply_view(stmt, view, user)
    if status_:
        stmt = stmt.where(Ticket.status.in_(status_))
    if queue_id is not None:
        stmt = stmt.where(Ticket.queue_id == queue_id)
    if assignee_id is not None:
        stmt = stmt.where(Ticket.assignee_id == assignee_id)
    if source is not None:
        stmt = stmt.where(Ticket.source == source)
    if requester_id is not None:
        stmt = stmt.where(Ticket.requester_id == requester_id)
    if q and (found := search.matches(q)) is not None:
        stmt = stmt.where(found)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = session.scalars(sorted_by(stmt, sort, dir).limit(limit).offset(offset)).all()
    return TicketPage(items=rows, total=total, limit=limit, offset=offset)


@router.get("/unaccepted", response_model=UnacceptedOut)
def unaccepted_count(user: Agent, session: SessionDep):
    """How many tickets this technician can see have waited past Settings > Desk hours > Not taken for."""
    from app import unaccepted

    return unaccepted.count_for(session, user, visible_queue_ids(user))


@router.get("/views", response_model=dict[SavedView, int])
def view_counts(user: Agent, session: SessionDep, scope: TicketScope = TicketScope.all, queue_id: int | None = None):
    """Counts for the saved views, within the chosen scope (and queue)."""
    base = apply_scope(visible(select(Ticket), user), scope, user)
    if queue_id is not None:
        base = base.where(Ticket.queue_id == queue_id)
    return {view: session.scalar(select(func.count()).select_from(apply_view(base, view, user).subquery())) for view in SavedView}


@router.post("/bulk/assign", response_model=BulkResultOut)
def bulk_assign(body: BulkAssignIn, user: BulkEditor, session: SessionDep):
    """Move and/or assign several tickets, each as /{id}/assign would (with its notes and history).
    Tickets that can't be changed (not visible, assignee not an owner...) are reported, not fatal."""
    done, failed = [], {}
    for ticket_id in dict.fromkeys(body.ticket_ids):
        try:
            ticket = staff_ticket(session, ticket_id, user)
            services.assign(session, ticket, user, queue_id=body.queue_id, assignee_id=body.assignee_id, unassign=body.unassign, note=body.note)
            session.commit()
            done.append(ticket_id)
        except (HTTPException, services.ServiceError) as exc:
            session.rollback()
            failed[ticket_id] = exc.detail if isinstance(exc, HTTPException) else str(exc)
    return BulkResultOut(done=done, failed=failed)


@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(ticket_id: int, user: Deleter, session: SessionDep):
    """Gone for good, with its replies, notes, history and files (for spam or a test; usually close it
    instead). Child tickets stay, on their own. The system log keeps who deleted what."""
    ticket = staff_ticket(session, ticket_id, user)
    label, subject = numbering.label(ticket.id, ticket.created_at), ticket.subject
    keys = list(session.scalars(select(Attachment.storage_key).where(Attachment.ticket_id == ticket.id)))
    session.expunge(ticket)
    # The database's own cascades remove what belongs to it.
    session.execute(sa_delete(Ticket).where(Ticket.id == ticket_id))
    session.commit()
    for key in keys:
        storage.delete(key)
    log.warning("%s deleted ticket %s %r (%s file(s))", user.display_name, label, subject, len(keys), extra={"source": "tickets"})


@router.post("/{ticket_id}/merge", response_model=TicketOut)
def merge_ticket(ticket_id: int, body: MergeIn, user: Merger, session: SessionDep):
    """Merge this ticket into another (both must be tickets you can see). Returns the target."""
    source = staff_ticket(session, ticket_id, user)
    target = staff_ticket(session, body.into_id, user)
    reason = session.get(FieldOption, body.reason_id)
    if reason is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick why these tickets are being merged")
    services.merge(session, source, target, user, reason, body.note)
    session.commit()
    return target


@router.get("/{ticket_id}/children", response_model=list[ChildTicketOut])
def list_children(ticket_id: int, user: Agent, session: SessionDep):
    """The ticket's child tickets that this person may see."""
    ticket = staff_ticket(session, ticket_id, user)
    return session.scalars(visible(select(Ticket).where(Ticket.parent_id == ticket.id), user).order_by(Ticket.id)).all()


@router.get("/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int, user: Agent, session: SessionDep):
    return staff_ticket(session, ticket_id, user)


@router.patch("/{ticket_id}", response_model=TicketOut)
def update_ticket(ticket_id: int, body: TicketUpdate, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("parent_id") is not None:
        staff_ticket(session, changes["parent_id"], user)  # only into a ticket you can see
    services.update_ticket(session, ticket, user, changes)
    session.commit()
    return ticket


@router.post("/{ticket_id}/assign", response_model=TicketOut)
def assign_ticket(ticket_id: int, body: AssignIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    services.assign(
        session, ticket, user, queue_id=body.queue_id, assignee_id=body.assignee_id, unassign=body.unassign, note=body.note
    )
    session.commit()
    return ticket


@router.post("/{ticket_id}/escalate", response_model=TicketOut)
def escalate_ticket(ticket_id: int, body: EscalateIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    services.escalate(session, ticket, user, reason=body.reason)
    session.commit()
    return ticket


@router.post("/{ticket_id}/requester", response_model=TicketOut)
def change_requester(ticket_id: int, body: RequesterChangeIn, user: Agent, session: SessionDep):
    """Link the ticket to the right person: pick a user, or give an email (an account is created
    if there's none)."""
    ticket = staff_ticket(session, ticket_id, user)
    if body.user_id is not None:
        requester = session.get(User, body.user_id)
        if requester is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "User not found")
    elif body.email is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a user or give an email")
    else:
        requester = find_user(session, body.email)
    if requester is None:
        requester = User(email=body.email, display_name=body.display_name or body.email.split("@")[0], role=UserRole.requester)
        session.add(requester)
        session.flush()
    services.change_requester(session, ticket, user, requester)
    session.commit()
    return ticket


@router.get("/{ticket_id}/comments", response_model=list[CommentOut])
def list_comments(ticket_id: int, user: CurrentUser, session: SessionDep):
    ticket, staff = ticket_access(session, ticket_id, user)
    stmt = select(Comment).where(Comment.ticket_id == ticket.id).order_by(Comment.created_at, Comment.id)
    if not staff:
        stmt = stmt.where(Comment.is_internal.is_(False))
    return session.scalars(stmt).all()


@router.post("/{ticket_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
def add_comment(ticket_id: int, body: CommentCreate, user: CurrentUser, session: SessionDep):
    ticket, staff = ticket_access(session, ticket_id, user)
    source = TicketSource.agent if staff else TicketSource.portal
    comment = services.add_comment(
        session, ticket, user, body=body.body, is_internal=body.is_internal, source=source, as_staff=staff, email_requester=body.email_requester
    )
    session.commit()
    return comment


@router.get("/{ticket_id}/events", response_model=list[EventOut])
def list_events(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    return session.scalars(
        select(TicketEvent).where(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.id)
    ).all()

