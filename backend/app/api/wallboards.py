"""Wallboards: a read-only page for a big screen in the IT office (live counts, alerts, and the
tickets that need someone), opened by a secret link with no sign-in.

Admins make them in Settings > Wallboards. The link is /wallboard#l8w_...: the token sits in the
URL fragment, so browsers never send it in a request line, a Referer or a server log; the page sends
it as "Authorization: Bearer l8w_..." to /api/public/wallboard. Only a hash of it is stored.
Revoking a board, or making it a new link, stops the old link at once.
"""

import hashlib
import logging
import secrets
from datetime import datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import numbering, sla
from app.auth import Admin, SessionDep
from app.models import Priority, Queue, ServiceHeartbeat, Ticket, TicketStatus, User, Wallboard

router = APIRouter(tags=["wallboards"])
log = logging.getLogger(__name__)
PREFIX = "l8w_"
# last_seen_at is written at most this often per board.
TOUCH_EVERY = timedelta(minutes=5)
# The worker writes a heartbeat every pass; older than this, the board says it's down.
WORKER_STALE = timedelta(minutes=5)
LIST_LENGTH = 8


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> str:
    return PREFIX + secrets.token_urlsafe(32)


# Settings > Wallboards


class WallboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    queue_ids: list[int] = Field(default_factory=list, max_length=200)
    unassigned_alert_minutes: int = Field(default=30, ge=1, le=1440)


class WallboardOut(BaseModel):
    id: int
    name: str
    prefix: str
    queue_ids: list[int]
    unassigned_alert_minutes: int
    is_active: bool
    created_at: datetime
    created_by: str | None
    last_seen_at: datetime | None


class WallboardMadeOut(WallboardOut):
    # Shown once, never stored.
    token: str


def _out(b: Wallboard, model=WallboardOut, **extra):
    return model(
        id=b.id, name=b.name, prefix=b.prefix, queue_ids=list(b.queue_ids), unassigned_alert_minutes=b.unassigned_alert_minutes,
        is_active=b.is_active, created_at=b.created_at, created_by=b.created_by.display_name if b.created_by else None,
        last_seen_at=b.last_seen_at, **extra,
    )


def _board(session, board_id: int) -> Wallboard:
    board = session.get(Wallboard, board_id)
    if board is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wallboard not found")
    return board


def _queues_exist(session, ids: list[int]) -> list[int]:
    ids = list(dict.fromkeys(ids))
    found = set(session.scalars(select(Queue.id).where(Queue.id.in_(ids)))) if ids else set()
    if missing := [i for i in ids if i not in found]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No such queue: {missing[0]}")
    return ids


@router.get("/api/wallboards", response_model=list[WallboardOut])
def list_boards(_: Admin, session: SessionDep):
    return [_out(b) for b in session.scalars(select(Wallboard).order_by(Wallboard.is_active.desc(), Wallboard.name))]


@router.post("/api/wallboards", response_model=WallboardMadeOut, status_code=status.HTTP_201_CREATED)
def create_board(body: WallboardIn, admin: Admin, session: SessionDep):
    """A new board. Its link is shown once."""
    token = _new_token()
    board = Wallboard(
        name=body.name.strip(), token_hash=_hash(token), prefix=token[: len(PREFIX) + 4], queue_ids=_queues_exist(session, body.queue_ids),
        unassigned_alert_minutes=body.unassigned_alert_minutes, created_by_id=admin.id,
    )
    session.add(board)
    session.commit()
    log.info("Wallboard %r made by %s", board.name, admin.display_name, extra={"source": "settings"})
    return _out(board, WallboardMadeOut, token=token)


@router.put("/api/wallboards/{board_id}", response_model=WallboardOut)
def update_board(board_id: int, body: WallboardIn, admin: Admin, session: SessionDep):
    board = _board(session, board_id)
    board.name, board.queue_ids = body.name.strip(), _queues_exist(session, body.queue_ids)
    board.unassigned_alert_minutes = body.unassigned_alert_minutes
    session.commit()
    log.info("Wallboard %r changed by %s", board.name, admin.display_name, extra={"source": "settings"})
    return _out(board)


@router.post("/api/wallboards/{board_id}/link", response_model=WallboardMadeOut)
def new_link(board_id: int, admin: Admin, session: SessionDep):
    """A new link (shown once); the old one stops working at once. Also brings a revoked board back."""
    board = _board(session, board_id)
    token = _new_token()
    board.token_hash, board.prefix, board.is_active = _hash(token), token[: len(PREFIX) + 4], True
    session.commit()
    log.warning("Wallboard %r given a new link by %s", board.name, admin.display_name, extra={"source": "settings"})
    return _out(board, WallboardMadeOut, token=token)


@router.delete("/api/wallboards/{board_id}", response_model=WallboardOut)
def revoke_board(board_id: int, admin: Admin, session: SessionDep):
    """Revoke: the link stops working at once (the screen shows that it was switched off)."""
    board = _board(session, board_id)
    board.is_active = False
    session.commit()
    log.info("Wallboard %r revoked by %s", board.name, admin.display_name, extra={"source": "settings"})
    return _out(board)


# The board itself (no sign-in: the link is the key)


class BoardTicket(BaseModel):
    id: int
    label: str
    subject: str
    priority: str
    queue: str
    requester: str
    assignee: str | None
    created_at: datetime
    due_at: datetime | None
    breached: bool


class QueueCount(BaseModel):
    name: str
    open: int
    unassigned: int
    breached: int


class PersonCount(BaseModel):
    name: str
    open: int
    needs_attention: int


class Alert(BaseModel):
    level: str  # "critical" | "warning"
    text: str


class BoardData(BaseModel):
    name: str
    generated_at: datetime
    unassigned_alert_minutes: int
    totals: dict[str, int]
    alerts: list[Alert]
    unassigned: list[BoardTicket]
    sla: list[BoardTicket]
    queues: list[QueueCount]
    people: list[PersonCount]


def _board_by_token(session, authorization: str | None) -> Wallboard:
    token = (authorization or "").removeprefix("Bearer ").strip()
    board = session.scalar(select(Wallboard).where(Wallboard.token_hash == _hash(token))) if token.startswith(PREFIX) else None
    if board is None or not board.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This wallboard link was switched off or replaced. Ask an admin for the new one.", headers={"WWW-Authenticate": "Bearer"})
    return board


def _queues(session, board: Wallboard) -> list[Queue]:
    """The board's queues: the chosen ones (restricted ones only when chosen by name), else every
    active queue that isn't restricted."""
    stmt = select(Queue).where(Queue.is_active)
    stmt = stmt.where(Queue.id.in_(board.queue_ids)) if board.queue_ids else stmt.where(Queue.restricted.is_(False))
    return list(session.scalars(stmt.order_by(Queue.name)))


def _ticket(t: Ticket, now: datetime) -> BoardTicket:
    return BoardTicket(
        id=t.id, label=numbering.label(t.id, t.created_at), subject=t.subject, priority=t.priority.value, queue=t.queue.name,
        requester=t.requester.display_name, assignee=t.assignee.display_name if t.assignee else None, created_at=t.created_at,
        due_at=t.resolution_due_at, breached=t.resolution_due_at is not None and t.resolution_due_at <= now,
    )


def board_data(session, board: Wallboard, now: datetime | None = None) -> BoardData:
    now = now or sla.utcnow()
    queues = _queues(session, board)
    qids = [q.id for q in queues]
    open_ = Ticket.status.not_in(sla.CLOSED_STATUSES)
    mine = Ticket.queue_id.in_(qids)
    at_risk = open_ & (Ticket.sla_at_risk_at <= now)
    breached = open_ & (Ticket.resolution_due_at <= now)
    unassigned = open_ & Ticket.assignee_id.is_(None)
    start_of_day = datetime.combine(now.astimezone().date(), time.min).astimezone()

    def count(*where) -> int:
        return session.scalar(select(func.count()).select_from(Ticket).where(mine, *where)) or 0

    totals = {
        "open": count(open_),
        "unassigned": count(unassigned),
        "sla_at_risk": count(at_risk, ~breached),
        "sla_breached": count(breached),
        "waiting_on_user": count(Ticket.status == TicketStatus.waiting_on_user),
        "new_today": count(Ticket.created_at >= start_of_day),
        "resolved_today": count(Ticket.resolved_at >= start_of_day),
    }

    by_queue = {q.id: QueueCount(name=q.name, open=0, unassigned=0, breached=0) for q in queues}
    for qid, n_open, n_unassigned, n_breached in session.execute(
        select(Ticket.queue_id, func.count(), func.count().filter(Ticket.assignee_id.is_(None)), func.count().filter(Ticket.resolution_due_at <= now))
        .where(mine, open_).group_by(Ticket.queue_id)
    ):
        by_queue[qid] = by_queue[qid].model_copy(update={"open": n_open, "unassigned": n_unassigned, "breached": n_breached})

    people = [
        PersonCount(name=name, open=n_open, needs_attention=n_attention)
        for name, n_open, n_attention in session.execute(
            select(User.display_name, func.count(), func.count().filter(Ticket.sla_at_risk_at <= now))
            .join(Ticket, Ticket.assignee_id == User.id).where(mine, open_)
            .group_by(User.id, User.display_name).order_by(func.count().desc(), User.display_name).limit(12)
        )
    ]

    oldest_unassigned = list(session.scalars(select(Ticket).where(mine, unassigned).order_by(Ticket.created_at).limit(LIST_LENGTH)))
    sla_list = list(session.scalars(select(Ticket).where(mine, at_risk).order_by(Ticket.resolution_due_at.nulls_last()).limit(LIST_LENGTH)))

    alerts: list[Alert] = []
    worker = session.get(ServiceHeartbeat, "worker")
    if worker is None or now - worker.last_seen > WORKER_STALE:
        alerts.append(Alert(level="critical", text="The background worker isn't running: email, SLA and scheduled jobs are stopped"))
    if totals["sla_breached"]:
        n = totals["sla_breached"]
        alerts.append(Alert(level="critical", text=f"{n} ticket{'s' if n != 1 else ''} past {'their' if n != 1 else 'its'} SLA"))
    urgent = count(unassigned, Ticket.priority.in_((Priority.high, Priority.critical)))
    if urgent:
        alerts.append(Alert(level="critical", text=f"{urgent} high or critical priority ticket{'s' if urgent != 1 else ''} with nobody assigned"))
    waiting = count(unassigned, Ticket.created_at <= now - timedelta(minutes=board.unassigned_alert_minutes))
    if waiting:
        alerts.append(Alert(level="warning", text=f"{waiting} ticket{'s' if waiting != 1 else ''} unassigned for over {board.unassigned_alert_minutes} min"))
    if totals["sla_at_risk"]:
        n = totals["sla_at_risk"]
        alerts.append(Alert(level="warning", text=f"{n} ticket{'s' if n != 1 else ''} at risk of missing {'their' if n != 1 else 'its'} SLA"))

    return BoardData(
        name=board.name, generated_at=now, unassigned_alert_minutes=board.unassigned_alert_minutes, totals=totals, alerts=alerts,
        unassigned=[_ticket(t, now) for t in oldest_unassigned], sla=[_ticket(t, now) for t in sla_list],
        queues=sorted(by_queue.values(), key=lambda q: (-q.open, q.name)), people=people,
    )


@router.get("/api/public/wallboard", response_model=BoardData)
def wallboard(session: SessionDep, authorization: Annotated[str | None, Header()] = None):
    board = _board_by_token(session, authorization)
    now = sla.utcnow()
    if board.last_seen_at is None or now - board.last_seen_at > TOUCH_EVERY:
        board.last_seen_at = now
        session.commit()
    return board_data(session, board, now)
