# SPDX-License-Identifier: AGPL-3.0-or-later
"""SLA deadlines. The clock runs in business time: the queue's hours (or Settings > Desk hours),
skipping closed days, unless Settings > Desk hours > SLA clock says around the clock. While a
ticket waits on the user the clock can stop (Settings > Desk hours): its due dates are cleared, so
nothing counts it as at risk or breached, and the business minutes that were left come back when
it's worked again."""
import enum
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from app import hours
from app.config import settings
from app.models import SlaPolicy, Ticket, TicketStatus

CLOSED_STATUSES = (TicketStatus.resolved, TicketStatus.closed)


def utcnow() -> datetime:
    return datetime.now(UTC)


class HasSlaFields(Protocol):
    status: TicketStatus
    resolution_due_at: datetime | None
    sla_at_risk_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    sla_paused_at: datetime | None


# The three deadlines, by the key their minutes are kept under while paused.
DEADLINES = {"first_response": "first_response_due_at", "resolution": "resolution_due_at", "at_risk": "sla_at_risk_at"}
# How far ahead business time is looked for (a long resolution window over many closed days).
HORIZON_DAYS = 3 * 366


Schedule = tuple[hours.Week, dict[date, str]]


def schedule(queue_hours: str | None) -> Schedule | None:
    """The hours the SLA clock runs in; None: around the clock (also when no hours are set)."""
    if settings.sla_clock == "always":
        return None
    try:
        week = hours.parse_week(queue_hours or settings.desk_hours)
        closed = hours.parse_closed(settings.desk_closed_days)
    except hours.HoursError:
        return None
    return (week, closed) if any(week.values()) else None


def _spans(sched: Schedule, start: datetime, days: int):
    week, closed = sched
    first = start.astimezone().date() - timedelta(days=1)
    yield from hours._stretches(week, closed, first, days + 2)


def add_business(start: datetime, minutes: float, sched: Schedule | None) -> datetime:
    """`start` plus this many minutes of business time. Negative minutes go back in wall time."""
    if sched is None or minutes <= 0:
        return start + timedelta(minutes=minutes)
    left = timedelta(minutes=minutes)
    for open_, close in _spans(sched, start, HORIZON_DAYS):
        if close <= start:
            continue
        begin = max(open_, start)
        if close - begin >= left:
            return (begin + left).astimezone(UTC)
        left -= close - begin
    return start + timedelta(minutes=minutes)  # no hours in years: fall back to wall time


def business_minutes(a: datetime, b: datetime, sched: Schedule | None) -> float:
    """Business minutes from a to b (negative when b is before a)."""
    if b < a:
        return -business_minutes(b, a, sched)
    if sched is None:
        return (b - a).total_seconds() / 60
    total = timedelta()
    days = (b.astimezone().date() - a.astimezone().date()).days + 1
    for open_, close in _spans(sched, a, min(days, HORIZON_DAYS)):
        lo, hi = max(open_, a), min(close, b)
        if hi > lo:
            total += hi - lo
    return total.total_seconds() / 60


def _queue_hours(ticket: Ticket) -> str | None:
    return ticket.queue.hours if ticket.queue is not None else None


class SlaState(enum.StrEnum):
    none = "none"  # no policy for this priority
    paused = "paused"  # waiting on the user: the clock is stopped
    ok = "ok"
    at_risk = "at_risk"
    breached = "breached"
    met = "met"  # resolved before the deadline


def apply_policy(ticket: Ticket, policy: SlaPolicy | None, start: datetime, queue_hours: str | None = None) -> None:
    """Set due dates from a policy, measured in business time from `start` (the ticket's creation),
    plus the time it has spent paused. A paused ticket keeps them as minutes left until it resumes."""
    if policy is None:
        ticket.first_response_due_at = None
        ticket.resolution_due_at = None
        ticket.sla_at_risk_at = None
        ticket.sla_resolution_minutes = None
        if ticket.sla_paused_at is not None:
            ticket.sla_left = {}
        return
    sched = schedule(queue_hours if queue_hours is not None else _queue_hours(ticket))
    paused = ticket.sla_paused_minutes or 0
    ticket.sla_resolution_minutes = policy.resolution_minutes
    ticket.first_response_due_at = add_business(start, policy.first_response_minutes + paused, sched)
    ticket.resolution_due_at = add_business(start, policy.resolution_minutes + paused, sched)
    ticket.sla_at_risk_at = add_business(start, policy.resolution_minutes * policy.at_risk_percent / 100 + paused, sched)
    if ticket.sla_paused_at is not None:
        _keep_left(ticket, ticket.sla_paused_at, sched)


def _keep_left(ticket: Ticket, at: datetime, sched: Schedule | None) -> None:
    ticket.sla_left = {key: business_minutes(at, getattr(ticket, field), sched)
                       for key, field in DEADLINES.items() if getattr(ticket, field) is not None}
    for field in DEADLINES.values():
        setattr(ticket, field, None)


def pause(ticket: Ticket, now: datetime) -> None:
    """Stop the clock (waiting on the user): keep what was left, clear the due dates."""
    if ticket.sla_paused_at is not None or not settings.sla_pause_waiting or ticket.resolution_due_at is None:
        return
    ticket.sla_paused_at = now
    _keep_left(ticket, now, schedule(_queue_hours(ticket)))


def resume(ticket: Ticket, now: datetime) -> None:
    """Start the clock again: due dates from what was left when it stopped."""
    if ticket.sla_paused_at is None:
        return
    sched = schedule(_queue_hours(ticket))
    ticket.sla_paused_minutes = round((ticket.sla_paused_minutes or 0) + max(0.0, business_minutes(ticket.sla_paused_at, now, sched)))
    for key, field in DEADLINES.items():
        if key in (ticket.sla_left or {}):
            setattr(ticket, field, add_business(now, ticket.sla_left[key], sched))
    ticket.sla_paused_at = None
    ticket.sla_left = None


def minutes_left(ticket: Ticket, now: datetime) -> float | None:
    """Business minutes until the resolution deadline (negative: past it); while paused, what's kept."""
    if ticket.sla_paused_at is not None:
        return (ticket.sla_left or {}).get("resolution")
    if ticket.resolution_due_at is None or ticket.status in CLOSED_STATUSES:
        return None
    return business_minutes(now, ticket.resolution_due_at, schedule(_queue_hours(ticket)))


def sla_state(ticket: HasSlaFields, now: datetime) -> SlaState:
    if ticket.sla_paused_at is not None and ticket.status not in CLOSED_STATUSES:
        return SlaState.paused
    if ticket.resolution_due_at is None:
        return SlaState.none
    if ticket.status in CLOSED_STATUSES:
        done = ticket.resolved_at or ticket.closed_at or now
        return SlaState.met if done <= ticket.resolution_due_at else SlaState.breached
    if now >= ticket.resolution_due_at:
        return SlaState.breached
    if ticket.sla_at_risk_at is not None and now >= ticket.sla_at_risk_at:
        return SlaState.at_risk
    return SlaState.ok
