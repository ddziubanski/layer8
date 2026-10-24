# SPDX-License-Identifier: AGPL-3.0-or-later
"""What vendor network integrations share (UniFi now, Juniper next): cached client observations and
equipment inventory, replaced one complete snapshot at a time, and outage tickets for equipment that
stays offline. Observations sit beside documented IP records; they never change them."""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import ipam, services, sla
from app.models import NetworkInventory, NetworkObservation, NetworkOutage, Priority, Queue, Ticket, TicketSource, TicketStatus

LABELS = {"unifi": "UniFi", "junos": "Juniper"}
OPEN = (TicketStatus.new, TicketStatus.open, TicketStatus.waiting_on_user)


def mac(value) -> str:
    """aa:bb:cc:dd:ee:ff, or "" when it isn't a MAC."""
    try:
        return ipam.normalize_mac(str(value or ""))
    except (ValueError, ipam.IpamError):
        return ""


def address(value) -> str:
    try:
        return str(ipam.parse_address(str(value))) if value else ""
    except (ValueError, ipam.IpamError):
        return ""


def clip(value, size: int) -> str:
    return "" if value is None or isinstance(value, (dict, list)) else str(value)[:size]


def replace_snapshot(session: Session, provider: str, observations: list[NetworkObservation], inventory: list[NetworkInventory]) -> None:
    """Swap in one provider's complete read. Call only after every source was read successfully."""
    seen, unique = set(), []
    for o in observations:
        if o.mac and (o.source_id, o.mac) not in seen:
            seen.add((o.source_id, o.mac))
            unique.append(o)
    session.execute(delete(NetworkObservation).where(NetworkObservation.provider == provider))
    session.execute(delete(NetworkInventory).where(NetworkInventory.provider == provider))
    session.add_all(unique)
    keys = set()
    for i in inventory:
        if (i.source_id, i.device_key) not in keys:
            keys.add((i.source_id, i.device_key))
            session.add(i)
    session.flush()


def replace_sources(session: Session, provider: str, sources: set[str], observations: list[NetworkObservation], inventory: list[NetworkInventory], *, all_inventory: bool = False) -> None:
    """Swap in the clients of the sources that answered (others keep theirs, going stale), and the
    inventory: of those sources, or of every source when the read reports all of them (`all_inventory`)."""
    if sources:
        session.execute(delete(NetworkObservation).where(NetworkObservation.provider == provider, NetworkObservation.source_id.in_(sources)))
    inv = delete(NetworkInventory).where(NetworkInventory.provider == provider)
    session.execute(inv if all_inventory else inv.where(NetworkInventory.source_id.in_(sources or {""})))
    seen = set()
    for o in observations:
        if o.mac and o.source_id in sources and (o.source_id, o.mac) not in seen:
            seen.add((o.source_id, o.mac))
            session.add(o)
    keys = set()
    for i in inventory:
        if (i.source_id, i.device_key) not in keys:
            keys.add((i.source_id, i.device_key))
            session.add(i)
    session.flush()


def track_outages(session: Session, provider: str, *, enabled: bool, after_minutes: int, queue_id: int, now: datetime | None = None) -> dict[str, int]:
    """After a complete read: equipment offline for `after_minutes` gets one ticket (when `enabled`);
    when it's back online, its open ticket gets an internal note for review, never a status change."""
    now = now or sla.utcnow()
    counts = {"opened": 0, "recovered": 0}
    states = {(i.source_id, i.device_key): i for i in session.scalars(select(NetworkInventory).where(NetworkInventory.provider == provider))}
    outages = {(o.source_id, o.device_key): o for o in session.scalars(select(NetworkOutage).where(NetworkOutage.provider == provider))}
    bot = None
    for key, item in states.items():
        outage = outages.get(key)
        down = item.state == "offline"
        if outage is None:
            if not down:
                continue
            outage = NetworkOutage(provider=provider, source_id=key[0], device_key=key[1], is_down=True, down_since=now, changed_at=now)
            session.add(outage)
            outages[key] = outage
        ticket = session.get(Ticket, outage.ticket_id) if outage.ticket_id else None
        active = ticket is not None and ticket.status in OPEN
        if down:
            if not outage.is_down:
                outage.is_down, outage.down_since, outage.changed_at = True, now, now
            if enabled and not active and outage.down_since <= now - timedelta(minutes=after_minutes):
                queue = session.get(Queue, queue_id) if queue_id else None
                bot = bot or services.automation_user(session)
                what = f"{LABELS.get(provider, provider)} {item.kind if item.kind != 'other' else 'device'} {item.name or item.mac or key[1]}"
                ticket = services.create_ticket(
                    session, subject=f"Offline: {what}"[:300],
                    description=(f"{what} at {item.source_name or key[0]} has been offline since "
                                 f"{outage.down_since.astimezone():%Y-%m-%d %H:%M} (as last read).\nModel: {item.model or 'unknown'} · "
                                 f"MAC: {item.mac or 'unknown'} · Address: {item.address or 'unknown'}\n\n"
                                 "When it's back online, an internal note is added here; the ticket stays open for review."),
                    category=None, requester=bot, actor=bot, source=TicketSource.api, priority=Priority.high,
                    preferred_queue=queue if queue is not None and queue.is_active else None,
                )
                outage.ticket_id = ticket.id
                counts["opened"] += 1
        elif outage.is_down:
            outage.is_down, outage.changed_at = False, now
            if active:
                bot = bot or services.automation_user(session)
                services.add_comment(session, ticket, bot, body=f"{item.name or key[1]} is back online (read {now.astimezone():%Y-%m-%d %H:%M}). Review and close this ticket if nothing else is needed.",
                                     is_internal=True, source=TicketSource.api, email_requester=False)
                counts["recovered"] += 1
    session.flush()
    return counts


def as_dict(o: NetworkObservation, stale_after: timedelta) -> dict:
    fields = ("provider", "source_id", "source_name", "mac", "address", "hostname", "network", "uplink_name", "port", "ssid", "connection", "last_seen_at", "observed_at")
    out = {f: getattr(o, f) for f in fields}
    out["provider_label"] = LABELS.get(o.provider, o.provider)
    out["stale"] = o.observed_at < sla.utcnow() - stale_after
    return out


def context_for(session: Session, macs, providers: dict[str, timedelta]) -> dict[str, list[dict]]:
    """Observations for exact MACs, from the providers that are on (name -> how old is stale).
    Hostnames and IPs never establish identity."""
    macs = {m for m in macs if m}
    if not macs or not providers:
        return {}
    out: dict[str, list[dict]] = {}
    rows = session.scalars(select(NetworkObservation).where(NetworkObservation.mac.in_(macs), NetworkObservation.provider.in_(list(providers)))
                           .order_by(NetworkObservation.observed_at.desc()))
    for o in rows:
        out.setdefault(o.mac, []).append(as_dict(o, providers[o.provider]))
    return out


def active_providers() -> dict[str, timedelta]:
    """Integrations that are on, with how old their observations may get before they're stale."""
    from app import junos, unifi

    out = {}
    if unifi.on():
        out[unifi.PROVIDER] = unifi.stale_after()
    if junos.on():
        out[junos.PROVIDER] = junos.stale_after()
    return out
