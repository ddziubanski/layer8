# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nutanix AHV through Prism Central's v4 REST API: opt-in. Settings > Nutanix.

Reads (basic authentication, JSON, paged with $page and $limit up to 100):
- VMs: GET /api/vmm/v4.0/ahv/config/vms (name, powerState, cluster and host references, vCPUs,
  memory, NICs with their MAC and IPs, Nutanix Guest Tools state)
- cluster and host names: GET /api/clustermgmt/v4.0/config/clusters and .../hosts

Power actions (a separate opt-in, from tickets): POST /api/vmm/v4.0/ahv/config/vms/{extId}/$actions/
guest-reboot (through Nutanix Guest Tools), reboot (ACPI), power-on or power-cycle. Each needs the
VM's current ETag in If-Match and a fresh NTNX-Request-Id, and answers with a task, which the worker
follows at GET /api/prism/v4.0/config/tasks/{extId} and reports on the ticket.

Responses are read defensively: only what's needed is taken, and an unexpected shape stops the read
(the last complete read is kept). Built from Nutanix's published API reference and tested against a
mocked Prism Central. Nutanix, Prism and AHV are trademarks of Nutanix, Inc.
"""

import uuid
from datetime import timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import func, select, text

from app import network, sla
from app.config import settings
from app.models import Device, NetworkObservation, NutanixAction, NutanixVm

PROVIDER = "nutanix"
VMS = "/api/vmm/v4.0/ahv/config/vms"
CLUSTERS = "/api/clustermgmt/v4.0/config/clusters"
HOSTS = "/api/clustermgmt/v4.0/config/hosts"
TASKS = "/api/prism/v4.0/config/tasks"
PAGE = 100
MAX_PAGES = 200
LOCK = 82638131
# What a ticket may ask for: (Prism Central action, what the ticket note calls it, permission).
ACTIONS = {
    "restart": ("reboot", "graceful restart", "nutanix_power"),
    "power_on": ("power-on", "power on", "nutanix_power"),
    "power_cycle": ("power-cycle", "hard power cycle", "nutanix_power_cycle"),
}
# How long the worker keeps asking about a task before calling the result unknown.
TASK_PATIENCE = timedelta(minutes=30)


class NutanixError(RuntimeError):
    """Safe to show: never includes the password."""


def on() -> bool:
    return bool(settings.nutanix_enabled and settings.nutanix_url and settings.nutanix_username and settings.nutanix_password)


def stale_after() -> timedelta:
    return timedelta(minutes=max(settings.nutanix_sync_minutes * 2, 30))


class Client:
    def __init__(self, base: str, username: str, password: str, http: httpx.Client | None = None):
        self.base = base.rstrip("/")
        verify = (settings.nutanix_ca_file or True) if settings.nutanix_verify_tls else False
        self.http = http or httpx.Client(timeout=30, follow_redirects=False, trust_env=False, verify=verify, auth=(username, password))

    def close(self) -> None:
        self.http.close()

    def _call(self, method: str, path: str, *, headers: dict | None = None, **kw) -> httpx.Response:
        try:
            r = self.http.request(method, self.base + path, headers={"Accept": "application/json", **(headers or {})}, **kw)
        except httpx.HTTPError as exc:
            raise NutanixError(f"Can't reach Prism Central ({type(exc).__name__})") from None
        if r.status_code == 401:
            raise NutanixError("Prism Central refused the login (Settings > Nutanix)")
        if r.status_code == 403:
            raise NutanixError("Prism Central says this account may not do that")
        if r.status_code == 404:
            raise NutanixError("Prism Central answered 404: check the address, and that it's pc.2024.1 or later (v4 API)")
        if r.status_code == 412:
            raise NutanixError("The VM changed in the meantime; try again")
        if r.status_code not in (200, 201, 202):
            raise NutanixError(f"Prism Central answered HTTP {r.status_code}")
        return r

    def _json(self, r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            raise NutanixError("Prism Central sent something that isn't JSON") from None
        if not isinstance(body, dict):
            raise NutanixError("Prism Central sent an unexpected answer")
        return body

    def paged(self, path: str) -> list[dict]:
        out = []
        for page in range(MAX_PAGES):
            body = self._json(self._call("GET", path, params={"$page": page, "$limit": PAGE}))
            data = body.get("data", [])
            if data is None:
                data = []
            if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
                raise NutanixError("Prism Central sent an unexpected list")
            out += data
            total = (body.get("metadata") or {}).get("totalAvailableResults") if isinstance(body.get("metadata"), dict) else None
            if len(data) < PAGE or (isinstance(total, int) and len(out) >= total):
                return out
        raise NutanixError("Too many items to read from Prism Central")

    def vm(self, ext_id: str) -> tuple[dict, str]:
        r = self._call("GET", f"{VMS}/{quote(ext_id, safe='')}")
        data = self._json(r).get("data")
        if not isinstance(data, dict):
            raise NutanixError("Prism Central sent an unexpected VM")
        return data, r.headers.get("ETag", "")

    def act(self, ext_id: str, action: str) -> str:
        """Start a power action; returns its task's ID."""
        _, etag = self.vm(ext_id)
        headers = {"NTNX-Request-Id": str(uuid.uuid4()), **({"If-Match": etag} if etag else {})}
        r = self._call("POST", f"{VMS}/{quote(ext_id, safe='')}/$actions/{action}", headers=headers, json={})
        data = self._json(r).get("data")
        task = data.get("extId") if isinstance(data, dict) else None
        if not isinstance(task, str) or not task or len(task) > 200:
            raise NutanixError("Prism Central accepted the action but sent no task to follow")
        return task

    def task(self, task_id: str) -> dict:
        data = self._json(self._call("GET", f"{TASKS}/{quote(task_id, safe='')}")).get("data")
        if not isinstance(data, dict):
            raise NutanixError("Prism Central sent an unexpected task")
        return data


def from_settings() -> Client:
    if not on():
        raise NutanixError("Turn on and fill in Settings > Nutanix first")
    return Client(settings.nutanix_url, settings.nutanix_username, settings.nutanix_password)


def _ref(v) -> str:
    return v.get("extId", "") if isinstance(v, dict) and isinstance(v.get("extId"), str) else ""


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2**31 else 0


def _addresses(nic: dict) -> tuple[str, list[str]]:
    """A NIC's MAC and IPs: the one set in Nutanix first, then those it learned."""
    backing = nic.get("backingInfo") if isinstance(nic.get("backingInfo"), dict) else {}
    info = nic.get("networkInfo") if isinstance(nic.get("networkInfo"), dict) else {}
    ips = []
    config = info.get("ipv4Config") if isinstance(info.get("ipv4Config"), dict) else {}
    if isinstance(config.get("ipAddress"), dict):
        ips.append(config["ipAddress"].get("value"))
    learned = info.get("ipv4Info") if isinstance(info.get("ipv4Info"), dict) else {}
    for ip in learned.get("learnedIpAddresses") or [] if isinstance(learned.get("learnedIpAddresses"), list) else []:
        if isinstance(ip, dict):
            ips.append(ip.get("value"))
    clean = []
    for ip in ips:
        a = network.address(ip) if isinstance(ip, str) else ""
        if a and a not in clean:
            clean.append(a)
    return network.mac(backing.get("macAddress") if isinstance(backing.get("macAddress"), str) else ""), clean


def parse_vm(d: dict, clusters: dict[str, str], hosts: dict[str, str]) -> dict | None:
    ext_id = d.get("extId")
    if not isinstance(ext_id, str) or not 0 < len(ext_id) <= 64:
        return None
    sockets, cores, threads = _int(d.get("numSockets")) or 1, _int(d.get("numCoresPerSocket")) or 1, _int(d.get("numThreadsPerCore")) or 1
    macs, ips = [], []
    for nic in d.get("nics") or [] if isinstance(d.get("nics"), list) else []:
        if isinstance(nic, dict):
            mac, addresses = _addresses(nic)
            if mac and mac not in macs:
                macs.append(mac)
            ips += [a for a in addresses if a not in ips]
    tools = d.get("guestTools") if isinstance(d.get("guestTools"), dict) else {}
    state = d.get("powerState")
    memory = d.get("memorySizeBytes")
    return {
        "ext_id": ext_id,
        "name": network.clip(d.get("name"), 255) or ext_id,
        "power_state": state.lower() if isinstance(state, str) and state.isalpha() and len(state) <= 20 else "unknown",
        "cluster_name": network.clip(clusters.get(_ref(d.get("cluster")), ""), 255),
        "host_name": network.clip(hosts.get(_ref(d.get("host")), ""), 255),
        "vcpus": min(sockets * cores * threads, 100000),
        "memory_mb": int(memory // (1024 * 1024)) if isinstance(memory, int) and 0 < memory < 2**60 else 0,
        "ips": ips[:20], "macs": macs[:20],
        "guest_tools": tools.get("isInstalled") is True,
        "guest_tools_reachable": tools.get("isInstalled") is True and tools.get("isReachable") is True,
        "nics": [_addresses(n) for n in d.get("nics") or [] if isinstance(n, dict)] if isinstance(d.get("nics"), list) else [],
    }


def read(client: Client) -> list[dict]:
    """Everything, or an error before anything is stored. Cluster and host names are a nicety: an
    account that can't list them still reads VMs."""
    names = {}
    for path, field in ((CLUSTERS, "name"), (HOSTS, "hostName")):
        try:
            for item in client.paged(path):
                if isinstance(item.get("extId"), str) and isinstance(item.get(field), str):
                    names[(path, item["extId"])] = item[field]
        except NutanixError:
            pass
    clusters = {k: v for (p, k), v in names.items() if p == CLUSTERS}
    hosts = {k: v for (p, k), v in names.items() if p == HOSTS}
    return [vm for d in client.paged(VMS) if (vm := parse_vm(d, clusters, hosts))]


def _devices(session) -> dict[str, int | None]:
    """Lower-case name -> device ID; None where two active devices share a name (no guessing)."""
    out: dict[str, int | None] = {}
    for device_id, name, dns in session.execute(select(Device.id, Device.name, Device.dns_hostname).where(Device.is_active)):
        for key in {(name or "").lower(), (dns or "").split(".")[0].lower()} - {""}:
            out[key] = None if key in out and out[key] != device_id else device_id
    return out


def sync(session, client: Client) -> str:
    """One complete read: VMs updated in place (gone ones kept, marked absent) and their addresses
    swapped in as network observations."""
    if not on():
        return "Off: Settings > Nutanix"
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK}):
        raise NutanixError("A Nutanix read is already running")
    vms = read(client)
    now = sla.utcnow()
    devices = _devices(session)
    known = {v.ext_id: v for v in session.scalars(select(NutanixVm))}
    observations, seen = [], set()
    for vm in vms:
        if vm["ext_id"] in seen:
            continue
        seen.add(vm["ext_id"])
        row = known.get(vm["ext_id"]) or NutanixVm(ext_id=vm["ext_id"], first_seen_at=now)
        for field in ("name", "power_state", "cluster_name", "host_name", "vcpus", "memory_mb", "ips", "macs", "guest_tools", "guest_tools_reachable"):
            setattr(row, field, vm[field])
        row.device_id = devices.get(vm["name"].lower())
        row.present, row.observed_at = True, now
        session.add(row)
        for mac, addresses in vm["nics"]:
            if mac:
                observations.append(NetworkObservation(
                    provider=PROVIDER, source_id=network.clip(vm["cluster_name"] or "nutanix", 200), mac=mac,
                    source_name=vm["cluster_name"], address=addresses[0] if addresses else "", hostname=vm["name"],
                    uplink_name=vm["host_name"], connection="vm", last_seen_at=now, observed_at=now))
    for ext_id, row in known.items():
        if ext_id not in seen:
            row.present = False
    network.replace_snapshot(session, PROVIDER, observations, [])
    off = sum(1 for vm in vms if vm["power_state"] != "on")
    matched = sum(1 for vm in vms if devices.get(vm["name"].lower()))
    return f"{len(seen)} VMs ({off} not running), {matched} matched to devices"


def note_result(session, action: NutanixAction) -> None:
    """The end of a power action, as an internal note on its ticket."""
    from app import services
    from app.models import Ticket, TicketSource

    ticket = session.get(Ticket, action.ticket_id)
    if ticket is None:
        return
    what = ACTIONS.get(action.action, (action.action, action.action, ""))[1]
    vm = action.vm.name if action.vm else action.vm_id
    end = {"succeeded": "finished", "failed": "failed", "unknown": "has no known result"}[action.status]
    body = f"Nutanix: the {what} of VM {vm} {end}" + (f": {action.detail}" if action.detail and action.status != "succeeded" else ".")
    services.add_comment(session, ticket, services.automation_user(session), body=body, is_internal=True,
                         source=TicketSource.api, email_requester=False)


def follow_tasks(session, client: Client) -> str:
    """Ask Prism Central about power actions still under way, and note each finished one on its ticket."""
    pending = list(session.scalars(select(NutanixAction).where(NutanixAction.status.in_(("submitted", "running"))).order_by(NutanixAction.id).limit(50)))
    done = 0
    for action in pending:
        try:
            task = client.task(action.task_id)
        except NutanixError as exc:
            task = {"status": "UNKNOWN", "_error": str(exc)}
        status = task.get("status") if isinstance(task.get("status"), str) else ""
        if status in ("QUEUED", "RUNNING", "CANCELING"):
            action.status = "running"
        elif status == "SUCCEEDED":
            action.status = "succeeded"
        elif status in ("FAILED", "CANCELED"):
            action.status = "failed"
            errors = task.get("errorMessages") if isinstance(task.get("errorMessages"), list) else []
            messages = [e.get("message") for e in errors if isinstance(e, dict) and isinstance(e.get("message"), str)]
            action.detail = network.clip("; ".join(messages) or status.lower(), 1000)
        if action.status in ("submitted", "running") and sla.utcnow() - action.requested_at > TASK_PATIENCE:
            action.status = "unknown"
            action.detail = task.get("_error") or "Prism Central didn't report an end to the task in time"
        if action.status in ("succeeded", "failed", "unknown"):
            action.finished_at = sla.utcnow()
            note_result(session, action)
            done += 1
    return f"{len(pending)} power actions followed, {done} finished"


def counts(session) -> dict[str, int]:
    rows = dict(session.execute(select(NutanixVm.power_state, func.count()).where(NutanixVm.present).group_by(NutanixVm.power_state)).all())
    return {"vms": sum(rows.values()), "running": rows.get("on", 0)}
