# SPDX-License-Identifier: AGPL-3.0-or-later
"""UniFi in Layer 8: admins set it up and test it; technicians read the cached clients and
equipment; people with the permission make guest Wi-Fi vouchers from a ticket (logged there)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import network, numbering, services, unifi
from app.api.tickets import staff_ticket
from app.auth import Admin, Agent, SessionDep
from app.config import settings
from app.models import EventType, NetworkInventory, NetworkObservation, TicketSource, WorkerJob
from app.permissions import require
from app.schemas import NetworkObservationOut

router = APIRouter(prefix="/api/unifi", tags=["unifi"])
Voucherer = require("unifi_vouchers")


class UnifiStatusOut(BaseModel):
    enabled: bool
    configured: bool
    mode: str
    clients: int
    devices: int
    offline: int
    vouchers_enabled: bool
    last_sync_at: datetime | None
    last_sync_ok: bool | None
    last_sync_error: str | None
    last_sync_summary: str | None


class UnifiTestOut(BaseModel):
    consoles: list[str]
    sites: list[str]


class NetworkSiteOut(BaseModel):
    source_id: str
    name: str


class VendorDeviceOut(BaseModel):
    source_id: str
    source_name: str
    device_key: str
    name: str
    model: str
    kind: str
    mac: str
    address: str
    state: str
    firmware: str
    observed_at: datetime


class VoucherIn(BaseModel):
    source_id: str = Field(min_length=3, max_length=200)
    minutes: int = Field(default=480, ge=30, le=60 * 24 * 30)
    guests: int = Field(default=1, ge=1, le=20)


class VoucherOut(BaseModel):
    codes: list[str]
    minutes: int
    site: str


@router.get("/status", response_model=UnifiStatusOut)
def status(_: Admin, session: SessionDep):
    job = session.get(WorkerJob, "unifi")
    inv = select(NetworkInventory).where(NetworkInventory.provider == unifi.PROVIDER)
    return dict(
        enabled=settings.unifi_enabled, configured=unifi.on(), mode=settings.unifi_mode,
        clients=session.scalar(select(func.count()).select_from(NetworkObservation).where(NetworkObservation.provider == unifi.PROVIDER)) or 0,
        devices=session.scalar(select(func.count()).select_from(inv.subquery())) or 0,
        offline=session.scalar(select(func.count()).select_from(inv.where(NetworkInventory.state == "offline").subquery())) or 0,
        vouchers_enabled=settings.unifi_vouchers_enabled,
        last_sync_at=job.last_finished_at if job else None, last_sync_ok=job.last_ok if job else None,
        last_sync_error=job.last_error if job else None, last_sync_summary=job.last_summary if job else None,
    )


@router.post("/test", response_model=UnifiTestOut)
def test(_: Admin):
    """Cloud: the consoles the key can see. Both: the sites on the selected consoles. Reads nothing else."""
    client = None
    try:
        client = unifi.from_settings()
        consoles = []
        if settings.unifi_mode == "cloud":
            for h in client.hosts():
                state = h.get("reportedState") if isinstance(h.get("reportedState"), dict) else {}
                consoles.append(f"{state.get('name') or state.get('hostname') or 'console'} ({h.get('id')})")
        found = []
        for console, base in unifi.bases():
            found += [f"{s.get('name') or s['id']} ({console}:{s['id']})" for s in unifi.sites(client, base)]
        return {"consoles": consoles, "sites": found}
    except unifi.UnifiError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        if client:
            client.close()


def _visible():
    if not unifi.on():
        raise HTTPException(404, "UniFi isn't on (Settings > UniFi)")


@router.get("/sites", response_model=list[NetworkSiteOut])
def list_sites(_: Agent, session: SessionDep):
    _visible()
    rows = session.execute(select(NetworkInventory.source_id, func.max(NetworkInventory.source_name)).where(NetworkInventory.provider == unifi.PROVIDER)
                           .group_by(NetworkInventory.source_id).order_by(func.max(NetworkInventory.source_name)))
    return [{"source_id": s, "name": n or s} for s, n in rows]


@router.get("/devices", response_model=list[VendorDeviceOut])
def list_devices(_: Agent, session: SessionDep, q: str = "", state: str = ""):
    _visible()
    stmt = select(NetworkInventory).where(NetworkInventory.provider == unifi.PROVIDER)
    if state in ("online", "offline", "other"):
        stmt = stmt.where(NetworkInventory.state == state)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(NetworkInventory.name.ilike(like) | NetworkInventory.model.ilike(like) | NetworkInventory.mac.ilike(like) | NetworkInventory.address.ilike(like))
    order = NetworkInventory.state != "offline", NetworkInventory.source_name, NetworkInventory.name
    return list(session.scalars(stmt.order_by(*order).limit(2000)))


@router.get("/clients", response_model=list[NetworkObservationOut])
def list_clients(_: Agent, session: SessionDep, q: str = "", limit: int = 200):
    _visible()
    stmt = select(NetworkObservation).where(NetworkObservation.provider == unifi.PROVIDER)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(NetworkObservation.hostname.ilike(like) | NetworkObservation.address.ilike(like) | NetworkObservation.mac.ilike(like)
                          | NetworkObservation.uplink_name.ilike(like) | NetworkObservation.ssid.ilike(like) | NetworkObservation.network.ilike(like))
    rows = session.scalars(stmt.order_by(NetworkObservation.source_name, NetworkObservation.hostname, NetworkObservation.mac).limit(max(1, min(limit, 1000))))
    return [network.as_dict(o, unifi.stale_after()) for o in rows]


@router.post("/tickets/{ticket_id}/vouchers", response_model=VoucherOut)
def make_voucher(ticket_id: int, body: VoucherIn, user: Voucherer, session: SessionDep):
    """A guest Wi-Fi voucher for this ticket's requester (or their visitor); the code is noted on the ticket."""
    ticket = staff_ticket(session, ticket_id, user)
    if not (unifi.on() and settings.unifi_vouchers_enabled):
        raise HTTPException(409, "Guest vouchers are off (Settings > UniFi)")
    site = session.scalar(select(func.max(NetworkInventory.source_name)).where(NetworkInventory.provider == unifi.PROVIDER, NetworkInventory.source_id == body.source_id))
    if site is None:
        raise HTTPException(422, "Pick one of the UniFi sites Layer 8 reads")
    client = None
    try:
        client = unifi.from_settings()
        codes = unifi.voucher(client, body.source_id, minutes=body.minutes, guests=body.guests,
                              name=f"Layer 8 {numbering.label(ticket.id, ticket.created_at)}")
    except unifi.UnifiError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        if client:
            client.close()
    hours = f"{body.minutes // 60} h" + (f" {body.minutes % 60} min" if body.minutes % 60 else "") if body.minutes >= 60 else f"{body.minutes} min"
    services.add_comment(session, ticket, user, is_internal=True, source=TicketSource.agent, as_staff=True, email_requester=False,
                         body=f"Guest Wi-Fi voucher on UniFi site {site}: {', '.join(codes)} (valid {hours}, {body.guests} guest{'s' if body.guests != 1 else ''}).")
    services._event(session, ticket, EventType.updated, user, unifi_voucher=True, site=site, minutes=body.minutes)
    session.commit()
    return {"codes": codes, "minutes": body.minutes, "site": site}
