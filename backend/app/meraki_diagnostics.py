"""Explicit read-only diagnostics. Discovery/IPAM data is never updated here."""
import hashlib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select, text

from app import ipam, meraki, sla
from app.config import settings
from app.models import MerakiClient, MerakiDiagnostic
from app.schemas import MerakiDiagnosticData, MerakiPortOut, MerakiWirelessEventOut

WINDOW_SECONDS = 900
CLIENT_SECONDS = 60
GLOBAL_SECONDS = 5
MAX_CACHE = 500


def selected(session, network_id, mac):
    if not settings.meraki_diagnostics_enabled or not meraki.on() or network_id not in meraki.network_ids():
        raise HTTPException(404, "Meraki diagnostics are not available for this client")
    try:
        mac = ipam.normalize_mac(mac)
    except ValueError:
        raise HTTPException(404, "No such Meraki client") from None
    item = session.get(MerakiClient, (network_id, mac)) if mac else None
    if item is None or item.connection.lower() not in ("wired", "wireless"):
        raise HTTPException(404, "No such Meraki client with a supported connection")
    if not re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", item.client_id):
        raise HTTPException(409, "Run Meraki discovery again to identify this client")
    if item.connection.lower() == "wired" and (not re.fullmatch(r"[A-Za-z0-9-]{1,100}", item.switch_serial) or not item.port):
        raise HTTPException(409, "This client has no last-known switch port; run Meraki discovery again")
    return item


def identity(item):
    return hashlib.sha256(json.dumps([item.network_id, item.mac, item.client_id, item.switch_serial,
                                      item.port, item.connection.lower()]).encode()).hexdigest()


def output(item, row, *, cached):
    return dict(network_id=item.network_id, mac=item.mac, client_id=item.client_id,
                client_observed_at=item.observed_at, attempted_at=row.attempted_at,
                retry_at=row.attempted_at + timedelta(seconds=CLIENT_SECONDS), cached=cached,
                error=row.last_error,
                data=MerakiDiagnosticData.model_validate_json(json.dumps(row.data)) if row.data else None)


def cached(session, item):
    row = session.get(MerakiDiagnostic, (item.network_id, item.mac))
    if row is None or row.identity_hash != identity(item) or row.attempted_at < sla.utcnow() - timedelta(days=1):
        return None
    return output(item, row, cached=True)


def _time(value):
    if not isinstance(value, str):
        raise ValueError()
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError()
    return value


def fetch(client, item, now):
    """Verify current client identity and attachment before reading port/AP evidence."""
    path = f"/networks/{quote(item.network_id, safe='')}/clients/{quote(item.client_id, safe='')}"
    try:
        _, current = client.get(path)
        if (not isinstance(current, dict) or current.get("id") != item.client_id
                or ipam.normalize_mac(current.get("mac", "")) != item.mac
                or current.get("recentDeviceConnection", "").lower() != item.connection.lower()
                or current.get("recentDeviceSerial", "") != item.switch_serial
                or str(current.get("switchport") or "") != item.port):
            raise meraki.MerakiError("Client identity or location changed; run Meraki discovery again")
        kind = item.connection.lower()
        base = dict(kind=kind, read_at=sla.utcnow(), window_start=now - timedelta(seconds=WINDOW_SECONDS), window_end=now)
        if kind == "wired":
            serial = quote(item.switch_serial, safe="")
            _, device = client.get(f"/devices/{serial}")
            if not isinstance(device, dict) or device.get("serial") != item.switch_serial or device.get("networkId") != item.network_id:
                raise meraki.MerakiError("The last-known switch is no longer in this selected network; run discovery again")
            _, ports = client.get(f"/devices/{serial}/switch/ports/statuses", {"timespan": WINDOW_SECONDS})
            if not isinstance(ports, list) or len(ports) > 1024 or any(not isinstance(p, dict) for p in ports):
                raise ValueError()
            matches = [p for p in ports if p.get("portId") == item.port]
            if len(matches) != 1:
                raise meraki.MerakiError("Meraki did not identify one matching switch port")
            p = matches[0]
            port = MerakiPortOut(port_id=p["portId"], enabled=p.get("enabled"), status=p.get("status"),
                                 speed=p.get("speed"), duplex=p.get("duplex"), is_uplink=p.get("isUplink"),
                                 client_count=p.get("clientCount"), errors=p.get("errors", []), warnings=p.get("warnings", []))
            return MerakiDiagnosticData(**{**base, "read_at": sla.utcnow()}, switch_serial=item.switch_serial, port=port)
        response, events = client.get(f"/networks/{quote(item.network_id, safe='')}/wireless/clients/{quote(item.client_id, safe='')}/connectivityEvents",
                                      {"t0": base["window_start"].isoformat(), "t1": now.isoformat(), "perPage": 50, "sortOrder": "descending"})
        if not isinstance(events, list) or len(events) > 50 or any(not isinstance(e, dict) for e in events):
            raise ValueError()
        parsed = []
        for e in events:
            occurred = _time(e.get("occurredAt"))
            if not base["window_start"] <= occurred <= now + timedelta(seconds=5):
                raise ValueError()
            parsed.append(MerakiWirelessEventOut(occurred_at=occurred, type=e.get("type"), subtype=e.get("subtype"),
                          severity=e.get("severity"), device_serial=e.get("deviceSerial"), band=e.get("band"),
                          channel=e.get("channel"), rssi=e.get("rssi"), duration_ms=e.get("durationMs")))
        parsed.sort(key=lambda e: e.occurred_at, reverse=True)
        # Only one bounded page; never follow provider-supplied URLs.
        return MerakiDiagnosticData(**{**base, "read_at": sla.utcnow()}, events=parsed,
                                    events_truncated=bool(response.links.get("next")))
    except (ValueError, TypeError, AttributeError, ValidationError):
        raise meraki.MerakiError("Meraki returned invalid diagnostics; the previous result was kept") from None


def read(session, network_id, mac):
    # A separate lock from OAuth and discovery. Reject concurrent work immediately.
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(82638116)")):
        raise HTTPException(409, "A Meraki diagnostic read is already running; try again shortly")
    item = selected(session, network_id, mac)
    now = sla.utcnow()
    row = session.get(MerakiDiagnostic, (item.network_id, item.mac))
    # Attempts, including failures, consume the cooldown even if the attachment changed.
    if row and now < row.attempted_at + timedelta(seconds=CLIENT_SECONDS):
        if row.identity_hash == identity(item):
            return output(item, row, cached=True)
        wait = max(1, int((row.attempted_at + timedelta(seconds=CLIENT_SECONDS) - now).total_seconds()) + 1)
        raise HTTPException(429, "Wait before reading this client again", headers={"Retry-After": str(wait)})
    latest = session.scalar(select(MerakiDiagnostic.attempted_at).order_by(MerakiDiagnostic.attempted_at.desc()).limit(1))
    if latest and now < latest + timedelta(seconds=GLOBAL_SECONDS):
        wait = max(1, int((latest + timedelta(seconds=GLOBAL_SECONDS) - now).total_seconds()) + 1)
        raise HTTPException(429, "Meraki diagnostic requests are limited; try again shortly", headers={"Retry-After": str(wait)})
    if row is None:
        row = MerakiDiagnostic(network_id=item.network_id, mac=item.mac, identity_hash=identity(item), attempted_at=now, data={})
        session.add(row)
    elif row.identity_hash != identity(item):
        row.identity_hash, row.data = identity(item), {}
    row.attempted_at, row.last_error = now, None
    client = None
    try:
        client = meraki.from_settings()
        data = fetch(client, item, now)
        row.data = data.model_dump(mode="json")
    except meraki.MerakiError as exc:
        row.last_error = str(exc)
    finally:
        if client:
            client.close()
    session.flush()
    # Bound retained state by age and count. Removed networks remain inaccessible.
    session.execute(delete(MerakiDiagnostic).where(MerakiDiagnostic.attempted_at < now - timedelta(days=1)))
    for old in session.scalars(select(MerakiDiagnostic).order_by(MerakiDiagnostic.attempted_at.desc(), MerakiDiagnostic.network_id, MerakiDiagnostic.mac).offset(MAX_CACHE)):
        session.delete(old)
    result = output(item, row, cached=False)
    session.commit()
    return result
