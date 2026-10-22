"""Devices: AD computers (kept by directory sync) and devices added by hand, and linking them to tickets."""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app import lifecycle, permissions, services, sweeps, warranty
from app.api.tickets import staff_ticket, visible
from app.auth import Agent, SessionDep
from app.config import settings
from app.models import Device, DeviceSource, DeviceWarranty, EventType, Ticket, TicketDevice, User
from app.schemas import (
    DeviceWarrantyOut,
    DeviceDetailOut,
    DeviceIn,
    DeviceOut,
    DevicePage,
    DeviceTicketOut,
    DeviceUpdateIn,
    LifecycleDeviceOut,
    LifecycleOut,
    StaleDeviceOut,
    SweepOut,
    TicketDeviceIn,
    TicketDeviceOut,
    UnknownDeviceOut,
    UserRef,
)

router = APIRouter(tags=["devices"])
DeviceAdder = permissions.require("devices_add")
DeviceDeleter = permissions.require("devices_delete")
log = logging.getLogger(__name__)

# What sync keeps up to date on AD computers; only notes can be changed there.
SYNCED_FIELDS = {"name", "dns_hostname", "os", "os_version", "description", "assigned_user_id"}
# Kept by technicians (warranty and replacement), on any device.
LIFECYCLE_FIELDS = {"manufacturer", "model", "serial", "purchased_on", "warranty_ends", "replace_by"}


def _device(session, device_id: int) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    return device


def _check_user(session, user_id: int | None) -> None:
    if user_id is not None and session.get(User, user_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such user")


@router.get("/api/devices", response_model=DevicePage)
def list_devices(
    _: Agent,
    session: SessionDep,
    q: str = "",
    include_inactive: bool = False,
    assigned_user_id: int | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Search by name, host name, OS, description or assigned person."""
    stmt = select(Device).outerjoin(User, User.id == Device.assigned_user_id)
    if not include_inactive:
        stmt = stmt.where(Device.is_active)
    if assigned_user_id is not None:
        stmt = stmt.where(Device.assigned_user_id == assigned_user_id)
    for word in q.split():
        like = f"%{word}%"
        stmt = stmt.where(
            or_(Device.name.ilike(like), Device.dns_hostname.ilike(like), Device.os.ilike(like), Device.description.ilike(like), User.display_name.ilike(like))
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    items = session.scalars(stmt.order_by(func.lower(Device.name), Device.id).limit(limit).offset(offset)).all()
    return DevicePage(items=items, total=total)


@router.get("/api/devices/lifecycle", response_model=LifecycleOut)
def device_lifecycle(_: Agent, session: SessionDep):
    """Warranties ending soon or ended, devices due for replacement within a year, and a forecast by quarter."""
    r = lifecycle.report(session)
    return LifecycleOut(
        life_years=settings.device_life_years, warn_days=settings.warranty_warn_days,
        warranty_soon=r.warranty_soon, warranty_ended=r.warranty_ended,
        replace_soon=[LifecycleDeviceOut(device=DeviceOut.model_validate(d), replace_due=due) for d, due in r.replace_soon],
        forecast=r.forecast, overdue=r.overdue, unknown=r.unknown,
    )


@router.get("/api/devices/sweep", response_model=SweepOut)
def sweep(_: Agent, session: SessionDep, stale_days: int | None = Query(None, ge=1, le=3650), recent_days: int | None = Query(None, ge=1, le=365)):
    """Stale devices (no sign of life from AD, Action1 or network scans for stale_days) and unknown ones
    (seen in the last recent_days but not a device here). Defaults from Settings > Device sweeps."""
    s, u = sweeps.stale(session, stale_days), sweeps.unknown(session, recent_days)
    return SweepOut(
        stale_days=stale_days or settings.sweep_stale_days, recent_days=recent_days or settings.sweep_recent_days,
        stale=[StaleDeviceOut(device=DeviceOut.model_validate(x.device), last_seen_at=x.last_seen_at, ad_logon_at=x.ad_logon_at, action1_at=x.action1_at, network_at=x.network_at) for x in s],
        unknown=[UnknownDeviceOut(**vars(x)) for x in u],
    )


@router.get("/api/devices/{device_id}", response_model=DeviceDetailOut)
def get_device(device_id: int, user: Agent, session: SessionDep):
    device = _device(session, device_id)
    out = DeviceDetailOut.model_validate(device)
    rows = session.execute(
        visible(
            select(Ticket, TicketDevice.linked_at).join(TicketDevice, TicketDevice.ticket_id == Ticket.id).where(TicketDevice.device_id == device.id),
            user,
        ).order_by(Ticket.created_at.desc())
    ).all()
    out.tickets = [DeviceTicketOut(id=t.id, subject=t.subject, status=t.status, created_at=t.created_at, linked_at=at) for t, at in rows]
    return out


def _warranty_out(session, device: Device) -> DeviceWarrantyOut:
    vendor = warranty.vendor_of(device.manufacturer)
    reason = ""
    if vendor is None:
        reason = "Only Dell and Lenovo warranties can be looked up (from the manufacturer field)"
    elif not device.serial.strip():
        reason = "Add the serial number (Dell: the service tag) to look it up"
    elif not warranty.enabled(vendor):
        reason = f"Set up {vendor.title()} under Settings > Warranty lookups to look it up"
    out = DeviceWarrantyOut(vendor=vendor, available=not reason, reason=reason)
    w = session.get(DeviceWarranty, device.id)
    if w is not None:
        fields = ("serial", "checked_at", "error", "product", "shipped_on", "purchased_on", "warranty_ends", "entitlements")
        out = DeviceWarrantyOut(vendor=w.vendor, available=not reason, reason=reason, **{f: getattr(w, f) for f in fields})
        bought = w.purchased_on or w.shipped_on
        out.differs = not w.error and (
            (w.warranty_ends is not None and device.warranty_ends != w.warranty_ends) or (bought is not None and device.purchased_on not in (None, bought))
        )
    return out


@router.get("/api/devices/{device_id}/warranty", response_model=DeviceWarrantyOut)
def device_warranty(device_id: int, _: Agent, session: SessionDep):
    """What the maker says about the device's warranty (last lookup), and whether it can be looked up."""
    return _warranty_out(session, _device(session, device_id))


@router.post("/api/devices/{device_id}/warranty/lookup", response_model=DeviceWarrantyOut)
def lookup_warranty(device_id: int, _: Agent, session: SessionDep):
    """Ask the maker now. Fills the device's empty purchase and warranty dates; differences are shown, not applied."""
    device = _device(session, device_id)
    out = _warranty_out(session, device)
    if not out.available:
        raise HTTPException(status.HTTP_409_CONFLICT, out.reason)
    try:
        warranty.lookup(session, [device], strict=True)
    except warranty.WarrantyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    session.commit()
    return _warranty_out(session, device)


@router.post("/api/devices/{device_id}/warranty/apply", response_model=DeviceWarrantyOut)
def apply_warranty(device_id: int, user: Agent, session: SessionDep):
    """Take the maker's warranty end and purchase (or ship) date onto the device."""
    device = _device(session, device_id)
    w = session.get(DeviceWarranty, device.id)
    if w is None or w.error:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's no answer from the maker to take")
    if w.warranty_ends:
        device.warranty_ends = w.warranty_ends
    if w.purchased_on or w.shipped_on:
        device.purchased_on = w.purchased_on or w.shipped_on
    session.commit()
    log.info("Device %s: warranty dates taken from %s by %s", device.name, w.vendor.title(), user.display_name, extra={"source": "devices"})
    return _warranty_out(session, device)


@router.post("/api/devices", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def create_device(body: DeviceIn, admin: DeviceAdder, session: SessionDep):
    _check_user(session, body.assigned_user_id)
    device = Device(source=DeviceSource.manual, **body.model_dump())
    device.name = device.name.strip()
    session.add(device)
    session.commit()
    log.info("Device %s added by %s", device.name, admin.display_name, extra={"source": "settings"})
    return device


@router.patch("/api/devices/{device_id}", response_model=DeviceOut)
def update_device(device_id: int, body: DeviceUpdateIn, user: Agent, session: SessionDep):
    """Agents can change notes and lifecycle details; admins can change devices added by hand (AD computers follow AD)."""
    device = _device(session, device_id)
    changes = body.model_dump(exclude_unset=True)
    if set(changes) - {"notes"} - LIFECYCLE_FIELDS and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can change devices")
    if device.source == DeviceSource.directory and (synced := set(changes) & SYNCED_FIELDS):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{', '.join(sorted(synced))} come(s) from AD: change it there")
    if "assigned_user_id" in changes:
        _check_user(session, changes["assigned_user_id"])
    if changes.get("name") is not None:
        changes["name"] = changes["name"].strip()
    for k, v in changes.items():
        if k == "name" and v is None:
            continue
        setattr(device, k, (v or "") if k in ("notes", "manufacturer", "model", "serial") else v)
    session.commit()
    return device


@router.delete("/api/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, admin: DeviceDeleter, session: SessionDep):
    device = _device(session, device_id)
    if device.source == DeviceSource.directory:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "AD computers go when they're removed from AD (or from the computer search scope)")
    session.delete(device)
    session.commit()
    log.info("Device %s removed by %s", device.name, admin.display_name, extra={"source": "settings"})


# On tickets


def _ticket_device_out(link: TicketDevice, session) -> TicketDeviceOut:
    by = session.get(User, link.linked_by_id) if link.linked_by_id else None
    return TicketDeviceOut(device=DeviceOut.model_validate(link.device), linked_at=link.linked_at, linked_by=UserRef.model_validate(by) if by else None)


@router.get("/api/tickets/{ticket_id}/devices", response_model=list[TicketDeviceOut])
def ticket_devices(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    links = session.scalars(select(TicketDevice).where(TicketDevice.ticket_id == ticket.id).order_by(TicketDevice.linked_at))
    return [_ticket_device_out(link, session) for link in links]


@router.post("/api/tickets/{ticket_id}/devices", response_model=list[TicketDeviceOut], status_code=status.HTTP_201_CREATED)
def link_device(ticket_id: int, body: TicketDeviceIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    device = _device(session, body.device_id)
    if session.get(TicketDevice, (ticket.id, device.id)) is None:
        session.add(TicketDevice(ticket_id=ticket.id, device_id=device.id, linked_by_id=user.id))
        services._event(session, ticket, EventType.device_linked, user, device_id=device.id, device=device.name)
        session.commit()
    return ticket_devices(ticket_id, user, session)


@router.delete("/api/tickets/{ticket_id}/devices/{device_id}", response_model=list[TicketDeviceOut])
def unlink_device(ticket_id: int, device_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    link = session.get(TicketDevice, (ticket.id, device_id))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That device isn't linked to this ticket")
    services._event(session, ticket, EventType.device_unlinked, user, device_id=device_id, device=link.device.name)
    session.delete(link)
    session.commit()
    return ticket_devices(ticket_id, user, session)
