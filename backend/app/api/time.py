# SPDX-License-Identifier: AGPL-3.0-or-later
"""Time spent on a ticket: technicians log it (with a reply or on its own, in the Time panel); the
Time spent report adds it up."""

from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import services, sla
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.models import Comment, EventType, TimeEntry, UserRole

router = APIRouter(tags=["time"])


class TimeEntryIn(BaseModel):
    minutes: int = Field(ge=1, le=24 * 60)
    worked_on: date | None = None
    note: str = Field(default="", max_length=500)
    comment_id: int | None = None


class TimeEntryOut(BaseModel):
    id: int
    minutes: int
    worked_on: date
    note: str
    user_id: int | None
    user_name: str | None
    comment_id: int | None
    created_at: datetime
    may_delete: bool


class TicketTimeOut(BaseModel):
    total_minutes: int
    entries: list[TimeEntryOut]


def _out(e: TimeEntry, user) -> dict:
    return {"id": e.id, "minutes": e.minutes, "worked_on": e.worked_on, "note": e.note, "user_id": e.user_id,
            "user_name": e.user.display_name if e.user else None, "comment_id": e.comment_id, "created_at": e.created_at,
            "may_delete": e.user_id == user.id or user.role == UserRole.admin}


@router.get("/api/tickets/{ticket_id}/time", response_model=TicketTimeOut)
def ticket_time(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    entries = session.scalars(select(TimeEntry).where(TimeEntry.ticket_id == ticket.id).order_by(TimeEntry.worked_on.desc(), TimeEntry.id.desc()))
    rows = [_out(e, user) for e in entries]
    return {"total_minutes": sum(r["minutes"] for r in rows), "entries": rows}


@router.post("/api/tickets/{ticket_id}/time", response_model=TimeEntryOut, status_code=201)
def log_time(ticket_id: int, body: TimeEntryIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    today = sla.utcnow().astimezone().date()
    worked_on = body.worked_on or today
    if worked_on > today or (today - worked_on).days > 366:
        raise HTTPException(422, "Pick a day in the last year, not in the future")
    if body.comment_id is not None:
        comment = session.get(Comment, body.comment_id)
        if comment is None or comment.ticket_id != ticket.id:
            raise HTTPException(422, "That reply isn't on this ticket")
    entry = TimeEntry(ticket_id=ticket.id, user_id=user.id, minutes=body.minutes, worked_on=worked_on, note=body.note.strip(), comment_id=body.comment_id)
    session.add(entry)
    services._event(session, ticket, EventType.updated, user, time_logged=body.minutes)
    session.commit()
    session.refresh(entry)
    return _out(entry, user)


@router.delete("/api/tickets/{ticket_id}/time/{entry_id}", status_code=204)
def delete_time(ticket_id: int, entry_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    entry = session.get(TimeEntry, entry_id)
    if entry is None or entry.ticket_id != ticket.id:
        raise HTTPException(404, "Time entry not found")
    if entry.user_id != user.id and user.role != UserRole.admin:
        raise HTTPException(403, "Only the person who logged it, or an admin, can remove it")
    services._event(session, ticket, EventType.updated, user, time_removed=entry.minutes)
    session.delete(entry)
    session.commit()


def total_minutes(session, ticket_id: int) -> int:
    return session.scalar(select(func.coalesce(func.sum(TimeEntry.minutes), 0)).where(TimeEntry.ticket_id == ticket_id)) or 0
