# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket timers: start, stop (logging the time), and stopping them all when a ticket is closed."""
import math

from sqlalchemy import select

from app import sla
from app.models import EventType, Ticket, TicketTimer, TimeEntry, User

MAX_MINUTES = 24 * 60


def stop(session, timer: TicketTimer, actor: User | None, note: str = "", reason: str = "") -> TimeEntry:
    """Log the elapsed time (rounded up to whole minutes, at least one) as the timer owner's entry."""
    from app import services

    elapsed = (sla.utcnow() - timer.started_at).total_seconds()
    minutes = max(1, math.ceil(elapsed / 60))
    notes = [note.strip()] if note.strip() else []
    if minutes > MAX_MINUTES:
        notes.append(f"timer ran {minutes // 60} h; capped at 24 h, edit if needed")
        minutes = MAX_MINUTES
    if reason:
        notes.append(reason)
    ticket = session.get(Ticket, timer.ticket_id)
    entry = TimeEntry(ticket_id=timer.ticket_id, user_id=timer.user_id, minutes=minutes, worked_on=sla.utcnow().astimezone().date(),
                      note="; ".join(notes)[:500])
    session.add(entry)
    services._event(session, ticket, EventType.updated, actor, time_logged=minutes, timer=True,
                    **({"timer_of": timer.user.display_name} if actor is None or actor.id != timer.user_id else {}))
    session.delete(timer)
    session.flush()
    return entry


def stop_all(session, ticket: Ticket, actor: User | None, why: str) -> int:
    """Closing rule: every timer still running on the ticket stops and its time is logged."""
    timers = session.scalars(select(TicketTimer).where(TicketTimer.ticket_id == ticket.id)).all()
    for timer in timers:
        stop(session, timer, actor, reason=f"stopped when the ticket was {why}")
    return len(timers)
