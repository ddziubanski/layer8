# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ubiquiti UniFi: opt-in and read-only (clients, equipment, outage tickets); guest vouchers are a
separate opt-in write. Settings > UniFi.

Two ways in, both with an API key in the X-API-KEY header:
- cloud: the Site Manager API (https://api.ui.com/v1/hosts lists the consoles the key can see) and
  Ubiquiti's Connector Proxy, which reaches each selected console's local Network Integration API
  through the cloud: https://api.ui.com/v1/connector/consoles/{id}/proxy/network/integration/v1/...
- local: one UniFi OS console's Network Integration API: https://{console}/proxy/network/integration/v1/...

The Network Integration API pages with offset/limit and answers {offset, limit, count, totalCount,
data}. Sites: /sites; equipment: /sites/{id}/devices; clients: /sites/{id}/clients; guest vouchers:
POST /sites/{id}/hotspot/vouchers. Responses are read defensively: only what's needed is taken, and
unexpected shapes stop the read (the last complete snapshot is kept).

Credit: Ubiquiti's developer documentation (https://developer.ui.com) and the map of UniFi's APIs in
"UniFi best practices" by uchkunr (https://github.com/uchkunr/unifi-best-practices, CC BY 4.0).
"""

import time
from datetime import timedelta
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import text

from app import network, sla, tls
from app.config import settings
from app.models import NetworkInventory, NetworkObservation

PROVIDER = "unifi"
CLOUD = "https://api.ui.com"
INTEGRATION = "/proxy/network/integration/v1"
PAGE = 200
MAX_PAGES = 100
MAX_CLIENTS = 40000
LOCK = 82638130


class UnifiError(RuntimeError):
    """Safe to show: never includes the API key."""


def on() -> bool:
    if not (settings.unifi_enabled and settings.unifi_api_key):
        return False
    return bool(settings.unifi_console_ids) if settings.unifi_mode == "cloud" else bool(settings.unifi_console_url)


def stale_after() -> timedelta:
    return timedelta(minutes=max(settings.unifi_sync_minutes * 2, 30))


class Client:
    def __init__(self, key: str, http: httpx.Client | None = None, sleep=time.sleep):
        self.key, self.sleep = key, sleep
        verify = tls.verify(settings.unifi_ca_file) if settings.unifi_mode == "local" else True
        self.http = http or httpx.Client(timeout=20, follow_redirects=False, trust_env=False, verify=verify)

    def close(self) -> None:
        self.http.close()

    def _call(self, method: str, url: str, **kw):
        for attempt in range(3):
            try:
                r = self.http.request(method, url, headers={"X-API-KEY": self.key, "Accept": "application/json"}, **kw)
            except httpx.HTTPError as exc:
                raise UnifiError(f"Can't reach UniFi: {tls.reason(exc)}. The last complete read was kept") from None
            if r.status_code == 429 and attempt < 2:
                try:
                    self.sleep(min(max(float(r.headers.get("Retry-After", "2")), 1), 10))
                except ValueError:
                    self.sleep(2)
                continue
            if r.status_code in (401, 403):
                parts = urlsplit(url)
                cloud = parts.hostname == "api.ui.com"
                connector = cloud and parts.path.startswith("/v1/connector/")
                stage = "cloud console Network API" if connector else "Site Manager console discovery" if cloud else "local console Network API"
                if r.status_code == 401:
                    hint = "Use a Site Manager key generated at unifi.ui.com for Cloud mode" if cloud else "Use the key from this console's Network Settings > Integrations for Local mode"
                    raise UnifiError(f"UniFi {stage}: HTTP 401 (authentication rejected). {hint}; check that it hasn't expired or been revoked")
                hint = ("Personal keys can access only consoles owned by the key's UI account; organization keys need access to this console's organization. Check Network access and console firmware 5.0.3 or later"
                        if connector else "Check this key's Site Manager access and organization permissions" if cloud else "Check this key's Network permissions on the selected console and site")
                raise UnifiError(f"UniFi {stage}: HTTP 403 (access forbidden). {hint}")
            if r.status_code == 404:
                raise UnifiError("UniFi answered 404: check the console ID or address, and that UniFi Network is 9 or later")
            if r.status_code not in (200, 201):
                raise UnifiError(f"UniFi answered HTTP {r.status_code}; the last complete read was kept")
            try:
                return r.json()
            except ValueError:
                raise UnifiError("UniFi sent something that isn't JSON") from None
        raise UnifiError("UniFi is rate limiting; try again later")

    # The cloud: consoles the key can see.

    def hosts(self) -> list[dict]:
        out, token = [], None
        for _ in range(MAX_PAGES):
            body = self._call("GET", f"{CLOUD}/v1/hosts", params={"pageSize": 100, **({"nextToken": token} if token else {})})
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list):
                raise UnifiError("UniFi Site Manager sent an unexpected host list")
            out += [h for h in data if isinstance(h, dict)]
            token = body.get("nextToken")
            if not token:
                return out
        raise UnifiError("Too many UniFi consoles to list")

    # A console's Network Integration API.

    def paged(self, base: str, path: str) -> list[dict]:
        out, offset = [], 0
        for _ in range(MAX_PAGES):
            body = self._call("GET", base + path, params={"offset": offset, "limit": PAGE})
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
                raise UnifiError("UniFi Network sent an unexpected list")
            out += data
            total = body.get("totalCount")
            offset += len(data)
            if not data or not isinstance(total, int) or offset >= total:
                return out
            if len(out) > MAX_CLIENTS:
                raise UnifiError("More UniFi clients than Layer 8 reads at once; choose fewer sites")
        raise UnifiError("UniFi list too long; choose fewer sites")

    def post(self, base: str, path: str, body: dict):
        return self._call("POST", base + path, json=body)


def from_settings() -> Client:
    if not settings.unifi_api_key:
        raise UnifiError("Save a UniFi API key in Settings > UniFi")
    return Client(settings.unifi_api_key)


def bases() -> list[tuple[str, str]]:
    """(console key, Network Integration API base URL) for each console to read."""
    if settings.unifi_mode == "cloud":
        if not settings.unifi_console_ids:
            raise UnifiError("Choose the consoles to read (Settings > UniFi > Consoles)")
        # Match the current Network endpoint examples; Connector GET prose differs.
        return [(c, f"{CLOUD}/v1/connector/consoles/{quote(c, safe='')}{INTEGRATION}") for c in settings.unifi_console_ids]
    url = settings.unifi_console_url.rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.path:
        raise UnifiError("Save the console's https address in Settings > UniFi")
    return [(parts.hostname, url + INTEGRATION)]


def sites(client: Client, base: str) -> list[dict]:
    chosen = set(settings.unifi_site_ids)
    out = []
    for s in client.paged(base, "/sites"):
        if isinstance(s.get("id"), str) and s["id"] and (not chosen or s["id"] in chosen):
            out.append(s)
    return out


def _kind(d: dict) -> str:
    features = {str(f).lower() for f in d.get("features") or [] if isinstance(f, str)}
    model = str(d.get("model") or "").upper()
    if "gateway" in features or model.startswith(("UDM", "UXG", "UCG", "USG", "UDR", "UX")):
        return "gateway"
    if "switching" in features or model.startswith(("USW", "US-", "US8", "US24", "US48")):
        return "switch"
    if "accesspoint" in features or model.startswith(("U6", "U7", "UAP", "UAL", "UAC", "UAP-", "E7")):
        return "ap"
    return "other"


def _state(d: dict) -> str:
    value = str(d.get("state") or "").upper()
    return "online" if value == "ONLINE" else "offline" if value in ("OFFLINE", "DISCONNECTED", "ISOLATED") else "other"


def read(client: Client) -> tuple[list[NetworkObservation], list[NetworkInventory]]:
    """Every selected console and site, or an error (nothing stored)."""
    now = sla.utcnow()
    observations, inventory = [], []
    for console, base in bases():
        for site in sites(client, base):
            source = f"{console}:{site['id']}"
            label = network.clip(site.get("name") or site.get("internalReference") or site["id"], 255)
            names = {}
            for d in client.paged(base, f"/sites/{quote(site['id'], safe='')}/devices"):
                key = network.clip(d.get("id"), 200)
                if not key:
                    raise UnifiError("UniFi sent a device without an ID")
                names[key] = network.clip(d.get("name") or d.get("model") or key, 255)
                inventory.append(NetworkInventory(
                    provider=PROVIDER, source_id=source, device_key=key, source_name=label, name=names[key],
                    model=network.clip(d.get("model"), 100), kind=_kind(d), mac=network.mac(d.get("macAddress")),
                    address=network.address(d.get("ipAddress")), state=_state(d),
                    firmware=network.clip(d.get("firmwareVersion"), 100), observed_at=now,
                ))
            for c in client.paged(base, f"/sites/{quote(site['id'], safe='')}/clients"):
                mac = network.mac(c.get("macAddress"))
                if not mac:
                    continue  # VPN and teleport clients have no MAC
                kind = str(c.get("type") or "").upper()
                observations.append(NetworkObservation(
                    provider=PROVIDER, source_id=source, source_name=label, mac=mac,
                    address=network.address(c.get("ipAddress")), hostname=network.clip(c.get("name") or c.get("hostname"), 255),
                    network=network.clip(c.get("networkName") or c.get("network"), 100),
                    uplink_name=names.get(network.clip(c.get("uplinkDeviceId"), 200), ""),
                    port=network.clip(c.get("uplinkPortIdx") or c.get("portIdx"), 100),
                    ssid=network.clip(c.get("wirelessNetworkName") or c.get("ssid"), 100),
                    connection="wireless" if kind == "WIRELESS" else "wired" if kind == "WIRED" else network.clip(kind.lower(), 16),
                    last_seen_at=now, observed_at=now,
                ))
    return observations, inventory


def sync(session, client: Client) -> str:
    """One complete read, swapped in atomically; then outage tickets."""
    if not on():
        return "Off: Settings > UniFi"
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK}):
        raise UnifiError("A UniFi read is already running")
    observations, inventory = read(client)
    network.replace_snapshot(session, PROVIDER, observations, inventory)
    counts = network.track_outages(session, PROVIDER, enabled=settings.unifi_outage_tickets,
                                   after_minutes=settings.unifi_outage_minutes, queue_id=settings.unifi_outage_queue_id)
    offline = sum(1 for i in inventory if i.state == "offline")
    out = f"{len(observations)} clients, {len(inventory)} UniFi devices ({offline} offline)"
    if counts["opened"] or counts["recovered"]:
        out += f"; outage tickets opened {counts['opened']}, recoveries noted {counts['recovered']}"
    return out


def voucher(client: Client, source_id: str, *, minutes: int, guests: int, name: str) -> list[str]:
    """Make one hotspot voucher on a site already read (its source is console:site). Returns the codes."""
    console, _, site = source_id.partition(":")
    base = dict(bases()).get(console)
    if base is None or not site:
        raise UnifiError("That UniFi site isn't one Layer 8 reads")
    body = client.post(base, f"/sites/{quote(site, safe='')}/hotspot/vouchers",
                       {"count": 1, "name": name[:100], "timeLimitMinutes": minutes, "authorizedGuestLimit": guests})
    items = body.get("vouchers") or body.get("data") if isinstance(body, dict) else body
    codes = [str(v["code"]) for v in items or [] if isinstance(v, dict) and v.get("code")]
    if not codes:
        raise UnifiError("UniFi made no voucher (or answered in an unexpected way): check UniFi before trying again")
    return codes
