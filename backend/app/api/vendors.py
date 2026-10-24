# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vendors (who we call, and how) and the systems they support; linking systems to tickets.

Technicians keep vendors and systems up to date; only admins delete them.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select

from app import permissions, services
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.models import Device, EventType, System, SystemAddress, TicketSystem, User, Vendor, VendorContact
from app.schemas import SystemIn, SystemOut, TicketSystemIn, TicketSystemOut, UserRef, VendorIn, VendorOut

router = APIRouter(tags=["vendors"])
VendorDeleter = permissions.require("vendors_delete")
log = logging.getLogger(__name__)


def _vendor(session, vendor_id: int) -> Vendor:
    v = session.get(Vendor, vendor_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor not found")
    return v


def _system(session, system_id: int) -> System:
    s = session.get(System, system_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "System not found")
    return s


def _unique(session, model, name: str, own_id: int | None, what: str) -> None:
    other = session.scalar(select(model).where(func.lower(model.name) == name.lower()))
    if other is not None and other.id != own_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a {what} called {other.name!r}")


def _vendor_out(session, v: Vendor) -> VendorOut:
    out = VendorOut.model_validate(v)
    out.system_count = session.scalar(select(func.count()).select_from(System).where(System.vendor_id == v.id)) or 0
    return out


# Vendors


@router.get("/api/vendors", response_model=list[VendorOut])
def list_vendors(_: Agent, session: SessionDep, q: str = "", include_inactive: bool = False):
    stmt = select(Vendor).order_by(func.lower(Vendor.name))
    if not include_inactive:
        stmt = stmt.where(Vendor.is_active)
    for word in q.split():
        like = f"%{word}%"
        stmt = stmt.where(or_(Vendor.name.ilike(like), Vendor.account_number.ilike(like), Vendor.notes.ilike(like)))
    return [_vendor_out(session, v) for v in session.scalars(stmt)]


@router.get("/api/vendors/{vendor_id}", response_model=VendorOut)
def get_vendor(vendor_id: int, _: Agent, session: SessionDep):
    return _vendor_out(session, _vendor(session, vendor_id))


def _apply_vendor(session, v: Vendor, body: VendorIn) -> None:
    for field, value in body.model_dump(exclude={"contacts"}).items():
        setattr(v, field, value.strip() if isinstance(value, str) and field != "notes" else value)
    existing = {c.id: c for c in v.contacts}
    keep = []
    for pos, c in enumerate(body.contacts):
        contact = existing.get(c.id) if c.id is not None else None
        if c.id is not None and contact is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Contact {c.id} isn't this vendor's")
        contact = contact or VendorContact()
        for field, value in c.model_dump(exclude={"id"}).items():
            setattr(contact, field, value.strip() if isinstance(value, str) and field != "notes" else value)
        contact.position = pos
        keep.append(contact)
    v.contacts = keep


@router.post("/api/vendors", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
def create_vendor(body: VendorIn, user: Agent, session: SessionDep):
    _unique(session, Vendor, body.name.strip(), None, "vendor")
    v = Vendor()
    _apply_vendor(session, v, body)
    session.add(v)
    session.commit()
    log.info("Vendor %r added by %s", v.name, user.display_name, extra={"source": "settings"})
    return _vendor_out(session, v)


@router.put("/api/vendors/{vendor_id}", response_model=VendorOut)
def update_vendor(vendor_id: int, body: VendorIn, user: Agent, session: SessionDep):
    v = _vendor(session, vendor_id)
    _unique(session, Vendor, body.name.strip(), v.id, "vendor")
    _apply_vendor(session, v, body)
    session.commit()
    return _vendor_out(session, v)


@router.delete("/api/vendors/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vendor(vendor_id: int, admin: VendorDeleter, session: SessionDep):
    """Its systems stay, without a vendor. To keep the history, mark it not in use instead."""
    v = _vendor(session, vendor_id)
    session.delete(v)
    session.commit()
    log.info("Vendor %r deleted by %s", v.name, admin.display_name, extra={"source": "settings"})


# Systems


@router.get("/api/systems", response_model=list[SystemOut])
def list_systems(_: Agent, session: SessionDep, q: str = "", vendor_id: int | None = None, include_inactive: bool = False):
    """Search by name, description, vendor, address or device."""
    stmt = select(System).outerjoin(Vendor, Vendor.id == System.vendor_id).order_by(func.lower(System.name))
    if not include_inactive:
        stmt = stmt.where(System.is_active)
    if vendor_id is not None:
        stmt = stmt.where(System.vendor_id == vendor_id)
    for word in q.split():
        like = f"%{word}%"
        stmt = stmt.where(
            or_(
                System.name.ilike(like),
                System.description.ilike(like),
                Vendor.name.ilike(like),
                System.id.in_(select(SystemAddress.system_id).where(SystemAddress.address.ilike(like))),
                System.devices.any(Device.name.ilike(like)),
            )
        )
    return session.scalars(stmt).unique().all()


@router.get("/api/systems/{system_id}", response_model=SystemOut)
def get_system(system_id: int, _: Agent, session: SessionDep):
    return _system(session, system_id)


def _apply_system(session, s: System, body: SystemIn) -> None:
    vendor = session.get(Vendor, body.vendor_id) if body.vendor_id is not None else None
    if body.vendor_id is not None and vendor is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such vendor")
    if body.contact_id is not None:
        contact = session.get(VendorContact, body.contact_id)
        if contact is None or vendor is None or contact.vendor_id != vendor.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The contact must be one of the vendor's")
    devices = []
    for device_id in dict.fromkeys(body.device_ids):
        d = session.get(Device, device_id)
        if d is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No device {device_id}")
        devices.append(d)
    s.name, s.description, s.notes, s.is_active = body.name.strip(), body.description, body.notes, body.is_active
    s.vendor_id, s.contact_id = body.vendor_id, body.contact_id
    s.devices = devices
    s.addresses = [SystemAddress(address=a.address.strip(), kind=a.kind, notes=a.notes.strip(), position=i) for i, a in enumerate(body.addresses)]


@router.post("/api/systems", response_model=SystemOut, status_code=status.HTTP_201_CREATED)
def create_system(body: SystemIn, user: Agent, session: SessionDep):
    _unique(session, System, body.name.strip(), None, "system")
    s = System()
    _apply_system(session, s, body)
    session.add(s)
    session.commit()
    log.info("System %r added by %s", s.name, user.display_name, extra={"source": "settings"})
    return s


@router.put("/api/systems/{system_id}", response_model=SystemOut)
def update_system(system_id: int, body: SystemIn, user: Agent, session: SessionDep):
    s = _system(session, system_id)
    _unique(session, System, body.name.strip(), s.id, "system")
    _apply_system(session, s, body)
    session.commit()
    return s


@router.delete("/api/systems/{system_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_system(system_id: int, admin: VendorDeleter, session: SessionDep):
    s = _system(session, system_id)
    session.delete(s)
    session.commit()
    log.info("System %r deleted by %s", s.name, admin.display_name, extra={"source": "settings"})


# On tickets


def _links(session, ticket_id: int) -> list[TicketSystemOut]:
    out = []
    for link in session.scalars(select(TicketSystem).where(TicketSystem.ticket_id == ticket_id).order_by(TicketSystem.linked_at)):
        by = session.get(User, link.linked_by_id) if link.linked_by_id else None
        out.append(TicketSystemOut(system=SystemOut.model_validate(link.system), linked_at=link.linked_at, linked_by=UserRef.model_validate(by) if by else None))
    return out


@router.get("/api/tickets/{ticket_id}/systems", response_model=list[TicketSystemOut])
def ticket_systems(ticket_id: int, user: Agent, session: SessionDep):
    return _links(session, staff_ticket(session, ticket_id, user).id)


@router.post("/api/tickets/{ticket_id}/systems", response_model=list[TicketSystemOut], status_code=status.HTTP_201_CREATED)
def link_system(ticket_id: int, body: TicketSystemIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    s = _system(session, body.system_id)
    if session.get(TicketSystem, (ticket.id, s.id)) is None:
        session.add(TicketSystem(ticket_id=ticket.id, system_id=s.id, linked_by_id=user.id))
        services._event(session, ticket, EventType.system_linked, user, system_id=s.id, system=s.name)
        session.commit()
    return _links(session, ticket.id)


@router.delete("/api/tickets/{ticket_id}/systems/{system_id}", response_model=list[TicketSystemOut])
def unlink_system(ticket_id: int, system_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    link = session.get(TicketSystem, (ticket.id, system_id))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That system isn't linked to this ticket")
    services._event(session, ticket, EventType.system_unlinked, user, system_id=system_id, system=link.system.name)
    session.delete(link)
    session.commit()
    return _links(session, ticket.id)
