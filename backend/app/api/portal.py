"""Self-service portal for requesters: pick a queue, file a ticket, follow it, say it's fixed.

Replies and attachments use the shared /api/tickets/{id}/comments and
/attachments endpoints, which already hide internal notes from requesters.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app import hours, presence, services, sla
from app.auth import CurrentUser, SessionDep
from app.config import settings
from app.models import Device, Queue, Ticket, TicketSource, User, UserRole
from app.schemas import PortalDeviceOut, PortalHoursOut, PortalPersonOut, PortalQueueOut, PortalTicketCreate, PortalTicketOut, PortalTicketPage

router = APIRouter(prefix="/api/portal", tags=["portal"])


def portal_queues(session) -> list[Queue]:
    return list(
        session.scalars(
            select(Queue).where(Queue.show_in_portal, Queue.is_active).order_by(Queue.portal_position, Queue.name)
        )
    )


def queue_label(queue: Queue) -> str:
    return queue.portal_name or queue.name


def portal_view(t: Ticket) -> PortalTicketOut:
    return PortalTicketOut(
        id=t.id,
        subject=t.subject,
        description=t.description,
        category=t.category,
        status=t.status,
        urgency=t.urgency,
        impact=t.impact,
        queue_name=queue_label(t.queue),
        assignee_name=t.assignee.display_name if t.assignee else None,
        attachment_count=t.attachment_count,
        created_at=t.created_at,
        updated_at=t.updated_at,
        resolved_at=t.resolved_at,
        closed_at=t.closed_at,
    )


def own_ticket(session, ticket_id: int, user: User) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None or ticket.requester_id != user.id:
        # Agents use the agent API; this one is only about "my tickets".
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket


def opening(name: str, week: str | None, now: datetime) -> PortalHoursOut:
    """Open or closed now, with the after-hours message filled in when closed."""
    text = week or settings.desk_hours
    try:
        st = hours.status(text, settings.desk_closed_days, now)
        lines = hours.lines(text)
    except hours.HoursError:
        # Hours that don't parse (e.g. a bad HELPDESK_DESK_HOURS) never break the portal.
        return PortalHoursOut(lines=[], open_now=True, closes_at=None, next_open_at=None, closed_for=None, message=None)
    message = None
    if not st.open:
        message = settings.after_hours_message.replace("{next}", hours.when(st.next_open_at, now)).replace("{queue}", name)
    return PortalHoursOut(lines=lines, open_now=st.open, closes_at=st.closes_at, next_open_at=st.next_open_at, closed_for=st.closed_for, message=message)


STATUS_ORDER = ("available", "busy", "away", "offline", "unknown")


def team(queue: Queue, now: datetime) -> list[PortalPersonOut] | None:
    if not settings.presence_on_portal:
        return None
    people = [PortalPersonOut(name=u.display_name, status=presence.shown(u, now)) for u in queue.owners if u.role in (UserRole.agent, UserRole.admin)]
    return sorted(people, key=lambda p: (STATUS_ORDER.index(p.status), p.name))


@router.get("/queues", response_model=list[PortalQueueOut])
def list_portal_queues(_: CurrentUser, session: SessionDep):
    now = sla.utcnow()
    return [
        PortalQueueOut(
            id=q.id, name=queue_label(q), description=q.portal_description, categories=q.categories,
            hours=opening(queue_label(q), q.hours, now), technicians=team(q, now),
        )
        for q in portal_queues(session)
    ]


@router.get("/hours", response_model=PortalHoursOut)
def desk_hours(_: CurrentUser):
    """The desk's own hours, for a portal without queues to choose from."""
    return opening("The service desk", None, sla.utcnow())


def own_devices(session, user: User) -> list[Device]:
    return list(session.scalars(select(Device).where(Device.assigned_user_id == user.id, Device.is_active).order_by(Device.name)))


@router.get("/devices", response_model=list[PortalDeviceOut])
def my_devices(user: CurrentUser, session: SessionDep):
    """The computers assigned to me, for "Which computer?" on a new request."""
    return [PortalDeviceOut(id=d.id, name=d.name, os=d.os) for d in own_devices(session, user)]


@router.post("/tickets", response_model=PortalTicketOut, status_code=status.HTTP_201_CREATED)
def create_portal_ticket(body: PortalTicketCreate, user: CurrentUser, session: SessionDep):
    computer = None
    if body.computer_id is not None:
        computer = next((d for d in own_devices(session, user) if d.id == body.computer_id), None)
        if computer is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That isn't one of your computers")
    queue = None
    if body.queue_id is not None:
        queue = next((q for q in portal_queues(session) if q.id == body.queue_id), None)
        if queue is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That queue isn't available in the portal")
        # One of the queue's options, or the requester's own words ("Something else...").
        if queue.categories and not (body.category or "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Say what the issue is with: pick an option or describe it")
    category = (body.category or "").strip() or None
    if queue and category:
        # Match the queue's own spelling when it's one of the options.
        category = next((c for c in queue.categories if c.casefold() == category.casefold()), category)
    # The chosen problem option may point at a more specific queue (IT / Wi-Fi -> Network).
    target = None
    if queue and category and category in queue.category_routes:
        target = session.get(Queue, queue.category_routes[category])
        if target is not None and not target.is_active:
            target = None
    ticket = services.create_ticket(
        session,
        subject=body.subject,
        description=body.description,
        category=category,
        requester=user,
        source=TicketSource.portal,
        actor=user,
        requested_queue=queue,
        preferred_queue=target,
        urgency=body.urgency,
        impact=body.impact,
        computer=computer,
    )
    session.commit()
    return portal_view(ticket)


@router.get("/tickets", response_model=PortalTicketPage)
def my_tickets(
    user: CurrentUser,
    session: SessionDep,
    closed: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """The signed-in user's own tickets: open ones, or resolved and closed with closed=true."""
    done = Ticket.status.in_(sla.CLOSED_STATUSES)
    stmt = select(Ticket).where(Ticket.requester_id == user.id, done if closed else ~done)
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = session.scalars(stmt.order_by(Ticket.updated_at.desc(), Ticket.id.desc()).limit(limit).offset(offset))
    return PortalTicketPage(items=[portal_view(t) for t in rows], total=total, limit=limit, offset=offset)


@router.get("/tickets/{ticket_id}", response_model=PortalTicketOut)
def my_ticket(ticket_id: int, user: CurrentUser, session: SessionDep):
    return portal_view(own_ticket(session, ticket_id, user))


@router.post("/tickets/{ticket_id}/resolve", response_model=PortalTicketOut)
def resolve_my_ticket(ticket_id: int, user: CurrentUser, session: SessionDep):
    ticket = own_ticket(session, ticket_id, user)
    services.requester_resolve(session, ticket, user)
    session.commit()
    return portal_view(ticket)
