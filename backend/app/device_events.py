# SPDX-License-Identifier: AGPL-3.0-or-later
"""Device events: what the regular syncs notice changing about a device, for its timeline.

Only changes are recorded, never a device's first sighting, so a fresh install or a newly
connected integration doesn't flood timelines. Network events need a known MAC (an IP record
linked to the device). Events older than RETAIN_DAYS are pruned by the directory sync.
"""
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app import sla
from app.models import DeviceEvent, IpAddress

RETAIN_DAYS = 730
PROVIDER_LABEL = {"unifi": "UniFi", "meraki": "Meraki", "juniper": "Juniper", "snmp": "SNMP"}


def record(session, device_id: int | None, kind: str, title: str, detail: str = "", tone: str = "info", *,
           at: datetime | None = None, source: str = "") -> None:
    if device_id is None:
        return
    session.add(DeviceEvent(device_id=device_id, at=at or sla.utcnow(), kind=kind, title=title[:300], detail=detail[:500],
                            tone=tone, source=source[:32]))


def prune(session) -> None:
    session.execute(delete(DeviceEvent).where(DeviceEvent.at < sla.utcnow() - timedelta(days=RETAIN_DAYS)))


def devices_by_mac(session, macs) -> dict[str, int]:
    macs = {m for m in macs if m}
    if not macs:
        return {}
    rows = session.execute(select(IpAddress.mac, IpAddress.device_id).where(IpAddress.mac.in_(macs), IpAddress.device_id.is_not(None)))
    return {mac: device_id for mac, device_id in rows}


def _where(o) -> str:
    """Where a client is connected: switch and port, or SSID, and the network (VLAN)."""
    place = f"{o.uplink_name} port {o.port}" if o.uplink_name and o.port else (o.uplink_name or (f"Wi-Fi {o.ssid}" if o.ssid else ""))
    return " · ".join(x for x in (place, o.network) if x)


def snapshot(session, model, provider: str, sources: set[str] | None = None) -> dict[tuple[str, str], tuple[str, str, str, str]]:
    """Where each client was before a read replaces them: (source, mac) -> (switch, port, network, ssid)."""
    stmt = select(model).where(model.provider == provider)
    if sources is not None:
        stmt = stmt.where(model.source_id.in_(sources or {""}))
    return {(o.source_id, o.mac): _place(o) for o in session.scalars(stmt)}


def _place(o) -> tuple[str, str, str, str]:
    # Unset fields are None on a fresh read and "" once stored: compare them the same.
    return (o.uplink_name or "", o.port or "", o.network or "", o.ssid or "")


def note_moves(session, provider: str, before: dict, after: list) -> int:
    """Clients of known devices that moved switch, port, network or SSID since the last read."""
    by_mac = devices_by_mac(session, {o.mac for o in after})
    moved = 0
    for o in after:
        device_id = by_mac.get(o.mac)
        old = before.get((o.source_id, o.mac))
        if device_id is None or old is None or old == _place(o):
            continue
        was = " · ".join(x for x in ((f"{old[0]} port {old[1]}" if old[0] and old[1] else old[0] or (f"Wi-Fi {old[3]}" if old[3] else "")), old[2]) if x)
        record(session, device_id, "network", f"Moved to {_where(o) or 'another connection'}", f"Was {was or 'elsewhere'} ({PROVIDER_LABEL.get(provider, provider)})",
               source=provider)
        moved += 1
    return moved
