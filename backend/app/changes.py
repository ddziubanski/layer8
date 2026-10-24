# SPDX-License-Identifier: AGPL-3.0-or-later
"""Change management, light: risk-scored changes with plans (the rollback plan is required),
approval when the risk calls for it, and a record of what happened.

A change moves draft -> awaiting approval -> approved (or rejected) -> in progress -> completed,
failed or rolled back; it can be cancelled before it ends. A standard change (from a pre-approved
template) skips approval; an emergency change may start at once and is approved afterwards.
Linked tickets get a note when a change starts and when it ends.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import numbering, services, sla
from app.config import settings
from app.models import Change, ChangeEvent, ChangeKind, ChangeStatus, Group, TicketSource, User, UserRole, group_members

LEVELS = ("low", "medium", "high")
OPEN = (ChangeStatus.draft, ChangeStatus.awaiting_approval, ChangeStatus.approved, ChangeStatus.in_progress)
EDITABLE = (ChangeStatus.draft, ChangeStatus.awaiting_approval, ChangeStatus.rejected)


class ChangeError(ValueError):
    """The message is safe to show."""


def label(c: Change) -> str:
    return numbering.label(c.id, c.created_at, settings.change_label_format)


def risk(c: Change) -> int:
    return c.impact * c.likelihood


def risk_level(c: Change) -> str:
    r = risk(c)
    return "low" if r <= 2 else "medium" if r <= 4 else "high"


def needs_approval(c: Change) -> bool:
    if c.kind == ChangeKind.standard:
        return False
    level = settings.change_approval_level if settings.change_approval_level in LEVELS else "medium"
    return LEVELS.index(risk_level(c)) >= LEVELS.index(level)


def is_approver(session: Session, user: User) -> bool:
    name = settings.change_approvers_group.strip().lower()
    if not name:
        return user.role == UserRole.admin
    return session.scalar(
        select(Group.id).join(group_members, group_members.c.group_id == Group.id)
        .where(func.lower(Group.name) == name, Group.is_active, group_members.c.user_id == user.id).limit(1)
    ) is not None


def can_approve(session: Session, c: Change, user: User) -> bool:
    awaiting = c.status == ChangeStatus.awaiting_approval or (c.kind == ChangeKind.emergency and c.approved_at is None and c.status in (ChangeStatus.in_progress, ChangeStatus.completed, ChangeStatus.failed, ChangeStatus.rolled_back))
    return awaiting and is_approver(session, user) and user.id not in (c.owner_id, c.requested_by_id)


def conflicts(session: Session, c: Change) -> list[tuple[Change, list[str]]]:
    """Other changes not ended or cancelled whose windows overlap this one's, each with what they share
    (devices, systems). A window without an end counts as an hour."""
    if c.planned_start is None:
        return []
    from datetime import timedelta

    end = c.planned_end or c.planned_start + timedelta(hours=1)
    live = (ChangeStatus.draft, ChangeStatus.awaiting_approval, ChangeStatus.approved, ChangeStatus.in_progress)
    out = []
    for other in session.scalars(select(Change).where(Change.id != c.id, Change.status.in_(live), Change.planned_start.is_not(None), Change.planned_start < end).order_by(Change.planned_start)):
        other_end = other.planned_end or other.planned_start + timedelta(hours=1)
        if other_end <= c.planned_start:
            continue
        shared = [d.name for d in other.devices if d in c.devices] + [s.name for s in other.systems if s in c.systems]
        out.append((other, shared))
    return out


def event(c: Change, actor: User | None, type_: str, note: str = "") -> None:
    c.events.append(ChangeEvent(actor_id=actor.id if actor else None, type=type_, note=note))


def _ready(c: Change) -> None:
    missing = [what for what, v in (("an implementation plan", c.implementation_plan), ("a rollback plan", c.rollback_plan)) if not v.strip()]
    if c.planned_start is None:
        missing.append("a planned start")
    if missing:
        raise ChangeError("It needs " + ", ".join(missing) + " first")


def _tell_tickets(session: Session, c: Change, actor: User, text: str) -> None:
    for t in c.tickets:
        services.add_comment(session, t, actor, body=f"Change {label(c)} \"{c.title}\" {text}", is_internal=True, source=TicketSource.agent, as_staff=True)


def act(session: Session, c: Change, actor: User, action: str, note: str = "") -> None:
    """submit, approve, reject, start, complete, fail, roll_back, cancel, reopen. Doesn't commit."""
    note = note.strip()
    now = sla.utcnow()
    s = c.status
    if action == "submit":
        if s != ChangeStatus.draft:
            raise ChangeError("Only a draft can be submitted")
        _ready(c)
        if needs_approval(c):
            c.status = ChangeStatus.awaiting_approval
        else:
            c.status = ChangeStatus.approved
            note = note or ("Pre-approved (a standard change)" if c.kind == ChangeKind.standard else f"No approval needed at {risk_level(c)} risk")
    elif action in ("approve", "reject"):
        if not can_approve(session, c, actor):
            raise ChangeError("You can't approve this change (approvers: Settings > Changes; nobody approves their own)")
        if action == "reject":
            if c.status != ChangeStatus.awaiting_approval:
                raise ChangeError("An emergency change that has started can't be rejected; record what happened instead")
            if not note:
                raise ChangeError("Say why it's rejected")
            c.status = ChangeStatus.rejected
        else:
            if c.status == ChangeStatus.awaiting_approval:
                c.status = ChangeStatus.approved
            c.approved_by_id, c.approved_at = actor.id, now
    elif action == "start":
        if s == ChangeStatus.approved or (c.kind == ChangeKind.emergency and s in (ChangeStatus.draft, ChangeStatus.awaiting_approval)):
            if c.kind == ChangeKind.emergency and s != ChangeStatus.approved:
                if not c.rollback_plan.strip():
                    raise ChangeError("Even an emergency change needs a rollback plan")
                note = note or "Emergency: started before approval"
            c.status, c.started_at = ChangeStatus.in_progress, now
            _tell_tickets(session, c, actor, "has started.")
        else:
            raise ChangeError("It must be approved before it starts" if s in (ChangeStatus.draft, ChangeStatus.awaiting_approval) else "It can't start from here")
    elif action in ("complete", "fail", "roll_back"):
        if s != ChangeStatus.in_progress:
            raise ChangeError("Only a change in progress can end")
        if action != "complete" and not note:
            raise ChangeError("Say what happened")
        c.status = {"complete": ChangeStatus.completed, "fail": ChangeStatus.failed, "roll_back": ChangeStatus.rolled_back}[action]
        c.finished_at, c.outcome = now, note or c.outcome
        word = {"complete": "is complete", "fail": "failed", "roll_back": "was rolled back"}[action]
        _tell_tickets(session, c, actor, word + (f": {note}" if note else "."))
    elif action == "cancel":
        if s not in OPEN:
            raise ChangeError("It has already ended")
        c.status = ChangeStatus.cancelled
        if s == ChangeStatus.in_progress:
            _tell_tickets(session, c, actor, "was cancelled" + (f": {note}" if note else "."))
    elif action == "reopen":
        if s not in (ChangeStatus.rejected, ChangeStatus.cancelled):
            raise ChangeError("Only a rejected or cancelled change can be reopened")
        c.status, c.approved_by_id, c.approved_at = ChangeStatus.draft, None, None
    else:
        raise ChangeError(f"Unknown action {action!r}")
    event(c, actor, {"roll_back": "rolled_back", "fail": "failed", "complete": "completed", "start": "started", "approve": "approved",
                     "reject": "rejected", "submit": "submitted", "cancel": "cancelled", "reopen": "reopened"}[action], note)
