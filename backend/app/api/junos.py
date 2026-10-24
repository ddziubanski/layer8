# SPDX-License-Identifier: AGPL-3.0-or-later
"""Juniper switches in Layer 8: admins set them up and ask for a read; technicians see what was read."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app import junos, network
from app.api.unifi import VendorDeviceOut
from app.auth import Admin, Agent, SessionDep
from app.config import settings
from app.models import NetworkInventory, NetworkObservation
from app.schemas import NetworkObservationOut

router = APIRouter(prefix="/api/junos", tags=["junos"])


class JunosStatusOut(BaseModel):
    enabled: bool
    configured: bool
    switches: int
    offline: int
    clients: int
    waiting: bool
    last_read_at: datetime | None
    last_summary: str
    last_error: str


class JunosReadOut(BaseModel):
    run_id: int


@router.get("/status", response_model=JunosStatusOut)
def status(_: Admin, session: SessionDep):
    inv = select(NetworkInventory).where(NetworkInventory.provider == junos.PROVIDER)
    last = junos.latest(session)
    try:
        configured = len(junos.parse_switches(settings.junos_switches))
    except junos.JunosError:
        configured = 0
    return dict(
        enabled=settings.junos_enabled, configured=junos.on(), switches=configured,
        offline=session.scalar(select(func.count()).select_from(inv.where(NetworkInventory.state == "offline").subquery())) or 0,
        clients=session.scalar(select(func.count()).select_from(NetworkObservation).where(NetworkObservation.provider == junos.PROVIDER)) or 0,
        waiting=junos.pending(session) is not None,
        last_read_at=last.processed_at if last else None, last_summary=last.summary if last else "", last_error=last.error if last else "",
    )


@router.post("/read", response_model=JunosReadOut)
def read_now(admin: Admin, session: SessionDep):
    try:
        read = junos.request_read(session, admin, scheduled=False)
    except junos.JunosError as exc:
        raise HTTPException(409, str(exc)) from None
    session.commit()
    return {"run_id": read.run_id}


def _visible():
    if not junos.on():
        raise HTTPException(404, "Juniper isn't on (Settings > Juniper)")


@router.get("/devices", response_model=list[VendorDeviceOut])
def list_switches(_: Agent, session: SessionDep):
    _visible()
    return list(session.scalars(select(NetworkInventory).where(NetworkInventory.provider == junos.PROVIDER)
                                .order_by(NetworkInventory.state != "offline", NetworkInventory.name)))


@router.get("/clients", response_model=list[NetworkObservationOut])
def list_clients(_: Agent, session: SessionDep, q: str = "", limit: int = 200):
    _visible()
    stmt = select(NetworkObservation).where(NetworkObservation.provider == junos.PROVIDER)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(NetworkObservation.address.ilike(like) | NetworkObservation.mac.ilike(like) | NetworkObservation.port.ilike(like)
                          | NetworkObservation.uplink_name.ilike(like) | NetworkObservation.network.ilike(like))
    rows = session.scalars(stmt.order_by(NetworkObservation.source_name, NetworkObservation.port, NetworkObservation.mac).limit(max(1, min(limit, 1000))))
    return [network.as_dict(o, junos.stale_after()) for o in rows]
