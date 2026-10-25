# SPDX-License-Identifier: AGPL-3.0-or-later
"""People: the requester card and person page. Staff only; counts respect queue visibility."""
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app import adchanges, presence, sla
from app.api.tickets import visible
from app.auth import Agent, SessionDep
from app.models import Device, Ticket, User
from app.schemas import PersonDeviceOut, PersonOut

router = APIRouter(prefix="/api/people", tags=["people"])


@router.get("/{user_id}", response_model=PersonOut)
def person(user_id: int, viewer: Agent, session: SessionDep):
    who = session.get(User, user_id)
    if who is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")
    theirs = visible(select(Ticket.status).where(Ticket.requester_id == who.id), viewer).subquery()
    counts = session.execute(select(func.count(), func.count().filter(theirs.c.status.not_in(sla.CLOSED_STATUSES))).select_from(theirs)).one()
    devices = session.scalars(select(Device).where(Device.assigned_user_id == who.id, Device.is_active).order_by(Device.name).limit(50)).all()
    shown = presence.shown(who, sla.utcnow())
    return PersonOut(
        id=who.id, display_name=who.display_name, email=who.email, role=who.role, is_active=who.is_active, auth_source=who.auth_source,
        title=who.title, department=who.department, phone=who.phone, upn=who.upn, sam_account_name=who.sam_account_name,
        in_directory=bool(who.directory_dn), ou=adchanges.parent_ou(who.directory_dn) if who.directory_dn else None,
        last_synced_at=who.last_synced_at, presence=None if shown == "unknown" else shown,
        open_tickets=counts[1], all_tickets=counts[0], devices=[PersonDeviceOut.model_validate(d) for d in devices],
    )
