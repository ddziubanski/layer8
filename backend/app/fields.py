# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket dropdown fields: resolution type, closure reason, department (Settings > Ticket fields),
plus the contact details a ticket starts with from its requester's AD record."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FieldList, FieldOption, Ticket, User

REQUESTER_RESOLVED = "requester_resolved"


class FieldError(ValueError):
    """A choice can't be used; the message is safe to show."""


def options(session: Session, lst: FieldList, *, include_retired: bool = False) -> list[FieldOption]:
    stmt = select(FieldOption).where(FieldOption.list == lst).order_by(FieldOption.position, FieldOption.label)
    if not include_retired:
        stmt = stmt.where(FieldOption.is_active)
    return list(session.scalars(stmt))


def choice(session: Session, lst: FieldList, option_id: int | None) -> FieldOption | None:
    """An active option of this list, or FieldError."""
    if option_id is None:
        return None
    opt = session.get(FieldOption, option_id)
    if opt is None or opt.list != lst or not opt.is_active:
        raise FieldError(f"That isn't one of the {lst.value.replace('_', ' ')} choices")
    return opt


def department_for(session: Session, name: str | None) -> FieldOption | None:
    """The Departments entry matching an AD department (case-insensitive), added if allowed."""
    name = (name or "").strip()
    if not name:
        return None
    opt = session.scalar(
        select(FieldOption).where(FieldOption.list == FieldList.department, func.lower(FieldOption.label) == name.lower()).limit(1)
    )
    if opt is not None:
        return opt if opt.is_active else None
    if not settings.departments_from_ad:
        return None
    last = session.scalar(select(func.max(FieldOption.position)).where(FieldOption.list == FieldList.department)) or 0
    opt = FieldOption(list=FieldList.department, label=name[:100], position=last + 10)
    session.add(opt)
    session.flush()
    return opt


def fill_contact(session: Session, ticket: Ticket, requester: User, *, only_empty: bool = True) -> None:
    """Start the ticket's phone and department from the requester's AD record."""
    if requester.phone and not (only_empty and ticket.contact_phone):
        ticket.contact_phone = requester.phone
    if not (only_empty and ticket.department_id):
        dept = department_for(session, requester.department)
        if dept is not None:
            ticket.department = dept


def import_departments(session: Session) -> int:
    """Add every department found on active users (from AD) to the list. Returns how many were new."""
    names = session.scalars(select(func.distinct(User.department)).where(User.is_active, User.department.is_not(None))).all()
    existing = {o.label.lower() for o in options(session, FieldList.department, include_retired=True)}
    last = max((o.position for o in options(session, FieldList.department, include_retired=True)), default=0)
    added = 0
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=str.lower):
        if name.lower() not in existing:
            last += 10
            session.add(FieldOption(list=FieldList.department, label=name[:100], position=last))
            existing.add(name.lower())
            added += 1
    session.flush()
    return added


def check_closing(session: Session, ticket: Ticket) -> None:
    """Resolving or closing needs the fields Settings > Ticket fields makes required (while
    their list has choices; an emptied list never blocks closing)."""
    missing = []
    if settings.require_resolution_type and ticket.resolution_type_id is None and options(session, FieldList.resolution_type):
        missing.append("a resolution type")
    if settings.require_closure_reason and ticket.closure_reason_id is None and options(session, FieldList.closure_reason):
        missing.append("a closure reason")
    if missing:
        raise FieldError("Choose " + " and ".join(missing) + " to resolve or close this ticket")


def requester_resolved(session: Session) -> FieldOption | None:
    opt = session.scalar(select(FieldOption).where(FieldOption.key == REQUESTER_RESOLVED))
    return opt if opt is not None and opt.is_active else None
