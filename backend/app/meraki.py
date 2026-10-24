"""Optional read-only Dashboard discovery. Observations never edit documented IPAM data."""

import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from sqlalchemy import delete, func, select, text

from app import ipam, sla
from app.config import settings
from app.models import IpAddress, MerakiClient

BASE = "https://api.meraki.com/api/v1"
MAX_NETWORKS = 20
MAX_CLIENTS = 40000
MAX_PAGES = 100


class MerakiError(RuntimeError):
    """Only fixed, credential-free messages may leave this boundary."""


def network_ids():
    ids = list(dict.fromkeys(settings.meraki_network_ids))
    if len(ids) > MAX_NETWORKS or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", n) for n in ids):
        raise MerakiError("Choose at most 20 valid Meraki network IDs")
    return ids


def on():
    return bool(settings.meraki_enabled and settings.meraki_api_key and network_ids())


class Client:
    def __init__(self, key, http=None, sleep=time.sleep):
        self.http = http or httpx.Client(timeout=20, follow_redirects=False)
        self.key, self.sleep = key, sleep

    def close(self):
        self.http.close()

    def get(self, path, params=None):
        for attempt in range(3):
            try:
                response = self.http.get(BASE + path, params=params, headers={"X-Cisco-Meraki-API-Key": self.key}, follow_redirects=False)
            except httpx.HTTPError:
                raise MerakiError("Cannot reach Meraki Dashboard; the previous observations were kept") from None
            if response.status_code == 429 and attempt < 2:
                try:
                    delay = float(response.headers.get("Retry-After", "1"))
                except ValueError:
                    delay = 1
                self.sleep(min(max(delay, 1), 5))
                continue
            if response.status_code in (401, 403):
                raise MerakiError("Meraki refused access; check the read-only API key and network access")
            if response.status_code != 200:
                raise MerakiError(f"Meraki returned HTTP {response.status_code}; the previous observations were kept")
            try:
                return response, response.json()
            except ValueError:
                raise MerakiError("Meraki returned an invalid response; the previous observations were kept") from None
        raise MerakiError("Meraki is rate limited; try again later")

    def network(self, network_id):
        _, body = self.get(f"/networks/{quote(network_id, safe='')}")
        if not isinstance(body, dict) or body.get("id") != network_id or not isinstance(body.get("name"), str):
            raise MerakiError("Meraki returned invalid network details")
        return body

    def clients(self, network_id):
        path = f"/networks/{quote(network_id, safe='')}/clients"
        params = {"perPage": 1000, "timespan": 86400}
        seen = set()
        for _ in range(MAX_PAGES):
            response, body = self.get(path, params)
            if not isinstance(body, list) or any(not isinstance(row, dict) for row in body):
                raise MerakiError("Meraki returned invalid client data")
            yield from body
            link = response.links.get("next", {}).get("url")
            if not link:
                return
            # Never send credentials to a URL from a response, including redirects.
            parsed = urlsplit(link)
            expected = urlsplit(BASE + path)
            cursor = parse_qs(parsed.query).get("startingAfter", [])
            if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path) or parsed.fragment or len(cursor) != 1 or not cursor[0] or cursor[0] in seen:
                raise MerakiError("Meraki returned an invalid pagination link")
            seen.add(cursor[0])
            params = {**params, "startingAfter": cursor[0]}
        raise MerakiError("Meraki discovery exceeded the page limit; select fewer networks")


def from_settings():
    if not settings.meraki_api_key or not network_ids():
        raise MerakiError("Save a read-only API key and explicit network IDs in Settings > Meraki")
    return Client(settings.meraki_api_key)


def _string(row, key, limit):
    value = row.get(key)
    if value is None:
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool) or len(str(value)) > limit:
        raise MerakiError("Meraki returned invalid client data")
    return str(value)


def observation(row, network, now):
    try:
        mac = ipam.normalize_mac(_string(row, "mac", 32))
        address = _string(row, "ip", 45)
        if address:
            address = str(ipam.parse_address(address))
        last = row.get("lastSeen")
        if isinstance(last, bool):
            raise ValueError()
        if isinstance(last, (int, float)):
            last = datetime.fromtimestamp(last, timezone.utc)
        elif isinstance(last, str):
            last = datetime.fromisoformat(last.replace("Z", "+00:00"))
        elif last is not None:
            raise ValueError()
        if last is not None and (last.tzinfo is None or last > now + timedelta(minutes=5)):
            raise ValueError()
    except (ValueError, OverflowError, OSError):
        raise MerakiError("Meraki returned an invalid client identity or timestamp") from None
    if not mac:
        raise MerakiError("Meraki returned a client without a MAC address")
    return MerakiClient(
        network_id=network["id"], network_name=network["name"][:255],
        client_id=_string(row, "id", 100), mac=mac, address=address,
        description=_string(row, "description", 255), status=_string(row, "status", 32),
        vlan=_string(row, "vlan", 32), switch_name=_string(row, "recentDeviceName", 255),
        switch_serial=_string(row, "recentDeviceSerial", 100), port=_string(row, "switchport", 100),
        connection=_string(row, "recentDeviceConnection", 32), ssid=_string(row, "ssid", 100),
        last_seen_at=last, observed_at=now,
    )


def sync(session, client):
    """One atomic snapshot, serialised across workers. Failure leaves the last snapshot intact."""
    if not on():
        return 0
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(82638114)")):
        raise MerakiError("Meraki discovery is already running")
    rows, identities = [], set()
    now = sla.utcnow()
    for network_id in network_ids():
        network = client.network(network_id)
        for body in client.clients(network_id):
            item = observation(body, network, now)
            identity = (network_id, item.mac)
            if identity in identities:
                raise MerakiError("Meraki returned duplicate client identities; the previous observations were kept")
            identities.add(identity)
            rows.append(item)
            if len(rows) > MAX_CLIENTS:
                raise MerakiError("Meraki discovery exceeded the client limit; select fewer networks")
    session.execute(delete(MerakiClient))
    session.add_all(rows)
    session.flush()
    return len(rows)


def visible(session):
    return select(MerakiClient).where(MerakiClient.network_id.in_(network_ids()))


def context_for(session, macs):
    """Only exact, unique MAC matches. Hostnames and IPs cannot establish identity."""
    if not on():
        return {}
    macs = {m for m in macs if m}
    if not macs:
        return {}
    ambiguous = set(session.scalars(select(IpAddress.mac).where(IpAddress.mac.in_(macs)).group_by(IpAddress.mac).having(func.count() > 1)))
    result = {}
    for item in session.scalars(visible(session).where(MerakiClient.mac.in_(macs))):
        if item.mac in result:
            ambiguous.add(item.mac)
        result[item.mac] = as_dict(item)
    return {mac: value for mac, value in result.items() if mac not in ambiguous}


def as_dict(item):
    fields = ("network_id", "network_name", "client_id", "mac", "address", "description", "status", "vlan", "switch_name", "switch_serial", "port", "connection", "ssid", "last_seen_at", "observed_at")
    result = {field: getattr(item, field) for field in fields}
    now = sla.utcnow()
    result["stale"] = item.observed_at < now - timedelta(minutes=max(settings.meraki_sync_minutes * 2, 30)) or item.last_seen_at is None or item.last_seen_at < now - timedelta(days=1)
    return result
