# SPDX-License-Identifier: AGPL-3.0-or-later
"""Device sweeps: stale devices (no sign of life anywhere for a while) and unknown ones (seen on the
network, or by Action1, but not a device here). They only flag; nothing is disabled or removed.

Signs of life: AD's last logon (replicated, up to ~14 days behind), Action1's last seen, and IPAM
scans seeing one of the device's addresses.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import ipam, services, sla
from app.config import settings
from app.models import Device, DevicePatch, FindingKind, FindingStatus, IpAddress, IpFinding, Priority, TicketSource


@dataclass
class Stale:
    device: Device
    # The latest of the signs below (None: never seen by any).
    last_seen_at: datetime | None
    ad_logon_at: datetime | None
    action1_at: datetime | None
    network_at: datetime | None


@dataclass
class Unknown:
    # address: scans see it, documented but not tied to a device; undocumented: a scan finding
    # nobody has dealt with; action1: a machine Action1 manages that isn't a device here.
    kind: str
    name: str
    address: str = ""
    mac: str = ""
    last_seen_at: datetime | None = None
    ip_address_id: int | None = None
    finding_id: int | None = None
    patch_id: int | None = None
    notes: list[str] = field(default_factory=list)


WHERE = {"address": "seen by scans", "undocumented": "seen by a scan, not in IPAM", "action1": "in Action1, no device here"}


def _latest(*times: datetime | None) -> datetime | None:
    known = [t for t in times if t is not None]
    return max(known) if known else None


def stale(session: Session, days: int | None = None, now: datetime | None = None) -> list[Stale]:
    """Active devices with no sign of life for `days`, longest quiet first. Devices added less than
    `days` ago and never seen aren't stale yet."""
    now = now or sla.utcnow()
    cutoff = now - timedelta(days=days or settings.sweep_stale_days)
    a1 = dict(session.execute(select(DevicePatch.device_id, DevicePatch.last_seen_at).where(DevicePatch.device_id.is_not(None), DevicePatch.in_action1)).all()) if settings.action1_enabled else {}
    net = dict(session.execute(select(IpAddress.device_id, func.max(IpAddress.last_seen_at)).where(IpAddress.device_id.is_not(None)).group_by(IpAddress.device_id)).all())
    out = []
    for d in session.scalars(select(Device).where(Device.is_active)):
        last = _latest(d.last_logon_at, a1.get(d.id), net.get(d.id))
        if (last is None and d.created_at < cutoff) or (last is not None and last < cutoff):
            out.append(Stale(d, last, d.last_logon_at, a1.get(d.id), net.get(d.id)))
    return sorted(out, key=lambda s: (s.last_seen_at is not None, s.last_seen_at or now, s.device.name.lower()))


def unknown(session: Session, days: int | None = None, now: datetime | None = None) -> list[Unknown]:
    """Things seen in the last `days` that aren't devices here."""
    now = now or sla.utcnow()
    since = now - timedelta(days=days or settings.sweep_recent_days)
    names: set[str] = set()
    for name, dns in session.execute(select(Device.name, Device.dns_hostname).where(Device.is_active)):
        names.add(name.lower())
        if dns:
            names.update({dns.lower(), dns.lower().split(".")[0]})
    known = lambda host: bool(host) and (host.lower() in names or host.lower().split(".")[0] in names)  # noqa: E731
    out: list[Unknown] = []
    for r in session.scalars(
        select(IpAddress).where(IpAddress.last_seen_at >= since, IpAddress.device_id.is_(None), IpAddress.system_id.is_(None)).order_by(IpAddress.address)
    ):
        if known(r.hostname):
            continue
        out.append(Unknown("address", r.hostname or "(no name)", str(ipam.address(r.address)), r.mac, r.last_seen_at, ip_address_id=r.id, notes=[f"documented as {r.kind}"]))
    for f in session.scalars(
        select(IpFinding).where(IpFinding.kind == FindingKind.new_host, IpFinding.status == FindingStatus.open, IpFinding.last_found_at >= since).order_by(IpFinding.address)
    ):
        host = (f.details or {}).get("hostname", "")
        if known(host):
            continue
        out.append(Unknown("undocumented", host or "(no name)", str(ipam.address(f.address)), (f.details or {}).get("mac", ""), f.last_found_at, finding_id=f.id, notes=[]))
    for p in session.scalars(select(DevicePatch).where(DevicePatch.in_action1, DevicePatch.device_id.is_(None)).order_by(func.lower(DevicePatch.name))) if settings.action1_enabled else []:
        if p.last_seen_at is not None and p.last_seen_at < since:
            continue
        out.append(Unknown("action1", p.name, last_seen_at=p.last_seen_at, patch_id=p.id, notes=[p.os] if p.os else []))
    return out


def summary_ticket(session: Session, now: datetime | None = None):
    """The weekly sweep as one ticket (when there's something to report). Doesn't commit."""
    s, u = stale(session, now=now), unknown(session, now=now)
    if not s and not u:
        return None
    lines = []
    if s:
        lines.append(f"Stale: {len(s)} device(s) with no sign of life for {settings.sweep_stale_days} days (AD logon, Action1, network scans):")
        lines += [f"- {x.device.name}: last seen {x.last_seen_at.astimezone():%d %B %Y}" if x.last_seen_at else f"- {x.device.name}: never seen" for x in s[:100]]
        lines.append("")
    if u:
        lines.append(f"Unknown: {len(u)} seen in the last {settings.sweep_recent_days} days that aren't devices here:")
        lines += [f"- {x.name} {x.address} {x.mac}".rstrip() + f" ({'; '.join([WHERE[x.kind], *x.notes])})" for x in u[:100]]
        lines.append("")
    lines.append("Devices > Sweeps has the full lists. Nothing was changed: retire, disable or document them as needed.")
    bot = services.automation_user(session)
    return services.create_ticket(
        session, subject=f"Device sweep: {len(s)} stale, {len(u)} unknown", description="\n".join(lines), category=None,
        requester=bot, source=TicketSource.api, priority=Priority.low, actor=bot,
    )
