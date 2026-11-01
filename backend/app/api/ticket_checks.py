# SPDX-License-Identifier: AGPL-3.0-or-later
"""A ticket's watched conditions: list, add, verify, stop watching (app/ticket_checks.py)."""
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app import ticket_checks
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.models import TicketCheck
from app.schemas import UserRef

router = APIRouter(prefix="/api/tickets/{ticket_id}/checks", tags=["tickets"])


class TicketCheckOut(BaseModel):
    id: int
    kind: str
    label: str
    state: str
    detail: str
    device_id: int | None
    user_id: int | None
    created_at: datetime
    checked_at: datetime | None
    cleared_at: datetime | None
    confirmed_at: datetime | None
    created_by: UserRef | None
    confirmed_by: UserRef | None

    model_config = {"from_attributes": True}


class CheckOption(BaseModel):
    kind: str
    device_id: int | None
    user_id: int | None
    label: str
    now: str


class TicketChecksOut(BaseModel):
    checks: list[TicketCheckOut]
    available: list[CheckOption]


class CheckIn(BaseModel):
    kind: str
    device_id: int | None = None
    user_id: int | None = None


def _out(session, ticket) -> TicketChecksOut:
    rows = session.scalars(select(TicketCheck).where(TicketCheck.ticket_id == ticket.id, TicketCheck.state != "cancelled").order_by(TicketCheck.id)).all()
    return TicketChecksOut(checks=rows, available=ticket_checks.available(session, ticket))


def _check(session, ticket, check_id: int) -> TicketCheck:
    check = session.get(TicketCheck, check_id)
    if check is None or check.ticket_id != ticket.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such check on this ticket")
    return check


@router.get("", response_model=TicketChecksOut)
def list_checks(ticket_id: int, user: Agent, session: SessionDep):
    return _out(session, staff_ticket(session, ticket_id, user))


@router.post("", response_model=TicketChecksOut, status_code=status.HTTP_201_CREATED)
def add_check(ticket_id: int, body: CheckIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    try:
        ticket_checks.add(session, ticket, user, body.kind, device_id=body.device_id, user_id=body.user_id)
    except ticket_checks.CheckError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    session.commit()
    return _out(session, ticket)


@router.post("/{check_id}/confirm", response_model=TicketChecksOut)
def confirm_check(ticket_id: int, check_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    try:
        ticket_checks.confirm(session, _check(session, ticket, check_id), user)
    except ticket_checks.CheckError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    session.commit()
    return _out(session, ticket)


@router.delete("/{check_id}", response_model=TicketChecksOut)
def cancel_check(ticket_id: int, check_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    try:
        ticket_checks.cancel(session, _check(session, ticket, check_id), user)
    except ticket_checks.CheckError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    session.commit()
    return _out(session, ticket)
