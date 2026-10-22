import enum
from datetime import UTC, datetime, timedelta
from typing import Protocol

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


class SlaState(enum.StrEnum):
    none = "none"  # no policy for this priority
    ok = "ok"
    at_risk = "at_risk"
    breached = "breached"
    met = "met"  # resolved before the deadline


def apply_policy(ticket: Ticket, policy: SlaPolicy | None, start: datetime) -> None:
    """Set due dates from a policy, measured from `start` (the ticket's creation time)."""
    if policy is None:
        ticket.first_response_due_at = None
        ticket.resolution_due_at = None
        ticket.sla_at_risk_at = None
        return
    window = timedelta(minutes=policy.resolution_minutes)
    ticket.first_response_due_at = start + timedelta(minutes=policy.first_response_minutes)
    ticket.resolution_due_at = start + window
    ticket.sla_at_risk_at = start + window * (policy.at_risk_percent / 100)


def sla_state(ticket: HasSlaFields, now: datetime) -> SlaState:
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
