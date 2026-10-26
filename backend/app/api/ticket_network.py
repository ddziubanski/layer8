# SPDX-License-Identifier: AGPL-3.0-or-later
"""Network-discovered entries in the same ticket Devices picker as managed assets.

    Only local cached records are read. Linking never scans, writes to a vendor,
    invents a managed asset, or changes a documented IP/device mapping.
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from app import ipam, services, sla, unifi
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.models import EventType, IpAddress, NetworkInventory, NetworkObservation, Ticket, TicketNetworkLink

router = APIRouter(prefix="/api/tickets/{ticket_id}", tags=["devices"])
Kind = Literal["unifi_device", "unifi_client", "ip_address"]


class NetworkTargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    source_id: str = Field(default="", max_length=200)
    key: str = Field(min_length=1, max_length=200)


class NetworkTargetOut(NetworkTargetIn):
    source_label: str
    name: str
    address: str
    mac: str
    source_name: str
    details: str
    observed_at: datetime | None
    stale: bool = False


class TicketNetworkLinkOut(BaseModel):
    id: int
    target: NetworkTargetOut
    current: NetworkTargetOut | None
    linked_at: datetime


def as_target(row):
    if isinstance(row, IpAddress):
        return NetworkTargetOut(kind="ip_address", key=str(row.id), name=row.hostname or str(ipam.address(row.address)),
            address=str(ipam.address(row.address)), mac=row.mac, source_name="IPAM",
            source_label={"scan": "IPAM scan", "imported": "IPAM import", "manual": "IPAM"}.get(row.source, "IPAM"),
            details="IP record", observed_at=row.last_seen_at)
    device = isinstance(row, NetworkInventory)
    return NetworkTargetOut(kind="unifi_device" if device else "unifi_client", source_id=row.source_id,
        key=row.device_key if device else row.mac, name=(row.name if device else row.hostname) or row.address or row.mac or (row.device_key if device else "Client"),
        address=row.address, mac=row.mac, source_name=row.source_name or row.source_id, source_label="UniFi",
        details=" · ".join(filter(None, ("UniFi", row.model, row.state) if device else ("UniFi", row.uplink_name, row.port, row.network, row.ssid))),
        observed_at=row.observed_at, stale=row.observed_at < sla.utcnow() - unifi.stale_after())


def resolve(session, ref):
    if ref.kind == "ip_address":
        if ref.source_id or not ref.key.isascii() or not ref.key.isdigit() or len(ref.key) > 18:
            return None
        return session.get(IpAddress, int(ref.key))
    if not unifi.on():
        return None
    model = NetworkInventory if ref.kind == "unifi_device" else NetworkObservation
    return session.get(model, ("unifi", ref.source_id, ref.key))


def output(session, row):
    snapshot = NetworkTargetOut.model_validate(row.snapshot)
    current = resolve(session, snapshot)
    return TicketNetworkLinkOut(id=row.id, target=snapshot, current=as_target(current) if current else None, linked_at=row.linked_at)


@router.get("/device-candidates", response_model=list[NetworkTargetOut])
def candidates(ticket_id: int, user: Agent, session: SessionDep, q: str = Query(min_length=2, max_length=200)):
    staff_ticket(session, ticket_id, user)
    words = q.strip().split()
    if not words:
        return []
    # Bound each source independently so large IPAM inventories cannot hide UniFi hits.
    targets = []
    for model in (IpAddress, NetworkInventory, NetworkObservation):
        if model != IpAddress and not unifi.on():
            continue
        stmt = select(model)
        if model == IpAddress:
            # Explicitly mapped assets are already found through the managed-device search.
            stmt = stmt.where(model.device_id.is_(None))
            columns = [model.hostname, model.mac, func.host(model.address)]
            order = [model.address]
        else:
            stmt = stmt.where(model.provider == "unifi")
            columns = [model.name, model.model] if model == NetworkInventory else [model.hostname, model.uplink_name, model.network, model.ssid]
            columns += [model.address, model.mac, model.source_name]
            order = [model.source_name, model.name if model == NetworkInventory else model.hostname, model.source_id]
        for word in words:
            # Treat %, _ and backslash as search text, not SQL wildcards.
            like = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            stmt = stmt.where(or_(*(c.ilike(like, escape="\\") for c in columns)))
        targets.extend(as_target(row) for row in session.scalars(stmt.order_by(*order).limit(10)))
    return targets


@router.get("/network-links", response_model=list[TicketNetworkLinkOut])
def list_links(ticket_id: int, user: Agent, session: SessionDep):
    staff_ticket(session, ticket_id, user)
    return [output(session, row) for row in session.scalars(select(TicketNetworkLink).where(TicketNetworkLink.ticket_id == ticket_id).order_by(TicketNetworkLink.linked_at, TicketNetworkLink.id))]


@router.post("/network-links", response_model=list[TicketNetworkLinkOut], status_code=201)
def link(ticket_id: int, body: NetworkTargetIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    session.scalar(select(Ticket).where(Ticket.id == ticket.id).with_for_update())
    source = resolve(session, body)
    if source is None:
        raise HTTPException(404, "Scanned device or IP record is no longer available; refresh the search")
    target = as_target(source)
    existing = session.scalar(select(TicketNetworkLink).where(TicketNetworkLink.ticket_id == ticket.id,
        TicketNetworkLink.kind == target.kind, TicketNetworkLink.source_id == target.source_id, TicketNetworkLink.object_key == target.key))
    if existing is None:
        session.add(TicketNetworkLink(ticket_id=ticket.id, kind=target.kind, source_id=target.source_id, object_key=target.key,
            snapshot=target.model_dump(mode="json"), linked_by_id=user.id))
        services._event(session, ticket, EventType.device_linked, user, network_target=target.model_dump(mode="json"), device=target.name)
        session.commit()
    return list_links(ticket_id, user, session)


@router.delete("/network-links/{link_id}", response_model=list[TicketNetworkLinkOut])
def unlink(ticket_id: int, link_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    row = session.get(TicketNetworkLink, link_id)
    if row is None or row.ticket_id != ticket.id:
        raise HTTPException(404, "That device is not linked to this ticket")
    services._event(session, ticket, EventType.device_unlinked, user, network_target=row.snapshot, device=row.snapshot["name"])
    session.delete(row)
    session.commit()
    return list_links(ticket_id, user, session)
