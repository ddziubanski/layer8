# SPDX-License-Identifier: AGPL-3.0-or-later
"""Juniper EX switches managed one by one (not Mist): opt-in and read only. Settings > Juniper.

A relay on the LAN (relay 1.2+, Python) reads each switch's Junos REST API: HTTPS, by default port
3443, enabled on the switch with `set system services rest https port 3443` and a server
certificate, and a login with a read-only class. It asks four RPCs as JSON (Accept:
application/json) at https://{switch}:{port}/rpc/{name}:

  get-software-information                  host name, model, Junos version
  get-arp-table-information                 MAC -> IP (on switches that route: irb interfaces)
  get-ethernet-switching-table-information  MAC -> VLAN and port (ELS and older output both read)
  (reachability)                            a switch that doesn't answer counts as offline

The login and password reach the relay sealed (on stdin), never in the run's values or output. The
relay sends back one compact line per switch; the server joins MAC -> port with MAC -> IP into the
vendor-neutral observations (app.network). Ports with many MACs are uplinks and are skipped.
"""

import json
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import network, runs, services, sla
from app.config import settings
from app.models import NetworkInventory, NetworkObservation, NetworkRead, Runner, RunStatus, Script, ScriptKind, ScriptLanguage, ScriptRun, ScriptVersion

PROVIDER = "junos"
SCRIPT_NAME = "Layer 8: Juniper switch read"
LINE = "LAYER8-JUNOS "
MAX_SWITCHES = 100


class JunosError(ValueError):
    pass


def parse_switches(text: str) -> list[dict]:
    """'core-1 10.0.0.2' or 'idf2-sw 10.0.2.2:8443', one per line."""
    out, names = [], set()
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", parts[0]):
            raise JunosError(f"\"{line}\": write a name, then the switch's address, e.g. core-1 10.0.0.2")
        host, port = parts[1], ""
        if m := re.fullmatch(r"([^:]+):(\d+)", parts[1]):
            host, port = m.group(1), m.group(2)
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
            raise JunosError(f"\"{line}\": {host!r} isn't a host name or IPv4 address")
        if port and not (port.isdigit() and 1 <= int(port) <= 65535):
            raise JunosError(f"\"{line}\": {port!r} isn't a port")
        if parts[0].lower() in names:
            raise JunosError(f"\"{line}\": the name {parts[0]} is used twice")
        names.add(parts[0].lower())
        out.append({"name": parts[0], "host": host, "port": int(port or 3443)})
    if len(out) > MAX_SWITCHES:
        raise JunosError(f"At most {MAX_SWITCHES} switches")
    return out


def on() -> bool:
    return bool(settings.junos_enabled and settings.junos_username and settings.junos_password and settings.junos_switches.strip())


def stale_after() -> timedelta:
    return timedelta(minutes=max(settings.junos_sync_minutes * 2, 30))


SCRIPT_BODY = r'''# Built in: Layer 8 Juniper switch read (Settings > Juniper). Standard library only; read only.
# Reads each switch's Junos REST API (HTTPS) with a read-only login that comes sealed on stdin.
import base64, json, ssl, sys, tempfile, urllib.request

s = json.loads(sys.stdin.read() or "{}")
if not s.get("username") or not s.get("switches"):
    print("No Juniper login or switches reached this script: check Settings > Juniper, and use runner 1.2 or later.")
    sys.exit(2)
ctx = ssl.create_default_context()
if s.get("ca"):
    ctx.load_verify_locations(cadata=s["ca"])
if not s.get("verify", True):
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
auth = "Basic " + base64.b64encode(f"{s['username']}:{s['password']}".encode()).decode()


def rpc(sw, name):
    url = f"https://{sw['host']}:{sw['port']}/rpc/{name}"
    req = urllib.request.Request(url, headers={"Authorization": auth, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def val(obj, *keys):
    # Junos JSON wraps every value: {"mac-address": [{"data": "..."}]}.
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if isinstance(v, list) and v and isinstance(v[0], dict) and "data" in v[0]:
            return str(v[0]["data"]).strip()
    return ""


def rows(obj, has):
    # Every dict anywhere in the answer that carries one of the `has` keys.
    found, todo = [], [obj]
    while todo:
        o = todo.pop()
        if isinstance(o, dict):
            if any(k in o for k in has):
                found.append(o)
            todo.extend(v for v in o.values() if isinstance(v, (list, dict)))
        elif isinstance(o, list):
            todo.extend(o)
    return found


for sw in s["switches"]:
    out = {"name": sw["name"], "host": sw["host"], "ok": False}
    try:
        info = rows(rpc(sw, "get-software-information"), ("host-name", "product-model"))
        if info:
            out.update(hostname=val(info[0], "host-name"), model=val(info[0], "product-model"), version=val(info[0], "junos-version"))
        out["arp"] = [[val(r, "mac-address"), val(r, "ip-address"), val(r, "interface-name")]
                      for r in rows(rpc(sw, "get-arp-table-information"), ("mac-address",)) if val(r, "ip-address")][:20000]
        macs = []
        for r in rows(rpc(sw, "get-ethernet-switching-table-information"), ("l2ng-l2-mac-address", "mac-address")):
            mac = val(r, "l2ng-l2-mac-address", "mac-address")
            port = val(r, "l2ng-l2-mac-logical-interface", "mac-interface")
            if not port:
                ifs = rows(r.get("mac-interfaces-list") or [], ("mac-interfaces",))
                port = val(ifs[0], "mac-interfaces") if ifs else ""
            if mac:
                macs.append([mac, val(r, "l2ng-l2-mac-vlan-name", "mac-vlan"), port])
        out["macs"] = macs[:20000]
        out["ok"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    print("LAYER8-JUNOS " + json.dumps(out, separators=(",", ":")), flush=True)
print("LAYER8-JUNOS-DONE")
'''


def ensure_script(session: Session) -> Script:
    script = session.scalar(select(Script).where(Script.name == SCRIPT_NAME))
    if script is None:
        script = Script(name=SCRIPT_NAME, kind=ScriptKind.action, language=ScriptLanguage.python, agents_can_run=True, current_version=1,
                        description="Built in: reads Juniper switches for Settings > Juniper. Needs a relay that takes Python, version 1.2 or later.")
        script.versions.append(ScriptVersion(number=1, language=script.language, body=SCRIPT_BODY, parameters=[], note="Built in"))
        session.add(script)
        session.flush()
    return script


def pending(session: Session) -> NetworkRead | None:
    return session.scalar(select(NetworkRead).join(ScriptRun, ScriptRun.id == NetworkRead.run_id).where(
        NetworkRead.provider == PROVIDER, ScriptRun.status.in_((RunStatus.queued, RunStatus.awaiting_approval, RunStatus.running))))


def request_read(session: Session, user, *, scheduled: bool) -> NetworkRead:
    if not on():
        raise JunosError("Juniper isn't set up (Settings > Juniper)")
    if pending(session) is not None:
        raise JunosError("A Juniper read is already waiting or running")
    runner_id = None
    if name := settings.junos_runner.strip():
        runner = session.scalar(select(Runner).where(Runner.name == name, Runner.is_active))
        if runner is None or "python" not in (runner.languages or []):
            raise JunosError(f"No active relay called {name!r} that takes Python (Settings > Juniper)")
        runner_id = runner.id
    ca = ""
    if settings.junos_ca_file:
        try:
            with open(settings.junos_ca_file, encoding="utf-8") as f:
                ca = f.read()
        except OSError:
            raise JunosError("The switches' CA certificate file can't be read; save it again in Settings > Juniper") from None
    sealed = {"username": settings.junos_username, "password": settings.junos_password, "switches": parse_switches(settings.junos_switches),
              "ca": ca, "verify": settings.junos_verify_tls}
    try:
        (run,) = runs.request(session, ensure_script(session), user, device_ids=[], on_runner=True, values={}, ticket=None,
                              only_runner_id=runner_id, sealed=sealed)
    except runs.RunError as exc:
        raise JunosError(str(exc)) from None
    read = NetworkRead(provider=PROVIDER, run_id=run.id, scheduled=scheduled)
    session.add(read)
    session.flush()
    return read


def parse(output: str) -> list[dict]:
    out = []
    for line in output.splitlines():
        if line.startswith(LINE):
            try:
                item = json.loads(line[len(LINE):])
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                out.append(item)
    return out


def _port(value: str) -> str:
    # ge-0/0/12.0 -> ge-0/0/12 (the logical unit isn't interesting here).
    return re.sub(r"\.0$", "", network.clip(value, 100))


def apply(session: Session, switches: list[dict]) -> str:
    """Store what the relay read. Switches that failed keep their previous clients (marked stale
    in time) and count as offline; answered switches replace theirs."""
    now = sla.utcnow()
    configured = {s["name"] for s in parse_switches(settings.junos_switches)}
    switches = [s for s in switches if s["name"] in configured]
    ip_by_mac = {}
    for s in switches:
        for row in s.get("arp") or []:
            if isinstance(row, list) and len(row) >= 2 and (m := network.mac(row[0])):
                ip_by_mac.setdefault(m, network.address(row[1]))
    observations, inventory = [], []
    for s in switches:
        name = s["name"]
        inventory.append(NetworkInventory(
            provider=PROVIDER, source_id=name, device_key=name, source_name=name, name=network.clip(s.get("hostname") or name, 255),
            model=network.clip(s.get("model"), 100), kind="switch", address=network.address(s.get("host")) or network.clip(s.get("host"), 45),
            state="online" if s.get("ok") else "offline", firmware=network.clip(s.get("version"), 100), observed_at=now,
        ))
        if not s.get("ok"):
            continue
        per_port: dict[str, int] = {}
        rows = [r for r in s.get("macs") or [] if isinstance(r, list) and len(r) >= 3]
        for r in rows:
            per_port[_port(r[2])] = per_port.get(_port(r[2]), 0) + 1
        for r in rows:
            m, port = network.mac(r[0]), _port(r[2])
            if not m or not port or per_port[port] >= settings.junos_uplink_macs or port.startswith(("ae", "irb", "vlan", "vme", "em")):
                continue
            observations.append(NetworkObservation(
                provider=PROVIDER, source_id=name, source_name=name, mac=m, address=ip_by_mac.get(m, ""),
                network=network.clip(r[1], 100), uplink_name=network.clip(s.get("hostname") or name, 255), port=port,
                connection="wired", last_seen_at=now, observed_at=now,
            ))
    answered = {s["name"] for s in switches if s.get("ok")}
    network.replace_sources(session, PROVIDER, answered, observations, inventory, all_inventory=True)
    counts = network.track_outages(session, PROVIDER, enabled=settings.junos_outage_tickets,
                                   after_minutes=settings.junos_outage_minutes, queue_id=settings.junos_outage_queue_id, now=now)
    failed = [s["name"] for s in switches if not s.get("ok")]
    out = f"{len(answered)} of {len(configured)} switches answered, {len(observations)} clients"
    if failed:
        out += f"; no answer from {', '.join(failed[:5])}{'…' if len(failed) > 5 else ''}"
    if counts["opened"] or counts["recovered"]:
        out += f"; outage tickets opened {counts['opened']}, recoveries noted {counts['recovered']}"
    return out


def on_finished(session: Session, run: ScriptRun) -> None:
    """Called when any run ends: if it was a Juniper read, store its results."""
    read = session.scalar(select(NetworkRead).where(NetworkRead.run_id == run.id, NetworkRead.provider == PROVIDER))
    if read is None or read.processed_at is not None:
        return
    read.processed_at = sla.utcnow()
    if run.status != RunStatus.succeeded:
        read.error = run.error or f"The read {run.status}"
        return
    try:
        read.summary = apply(session, parse(run.output))
    except JunosError as exc:
        read.error = str(exc)


def latest(session: Session) -> NetworkRead | None:
    return session.scalar(select(NetworkRead).where(NetworkRead.provider == PROVIDER).order_by(NetworkRead.id.desc()).limit(1))


def scheduled_read(session: Session) -> tuple[str, bool]:
    """The worker's job: ask for a read when one is due and none is waiting."""
    if not on():
        return "Off: Settings > Juniper", False
    if pending(session) is not None:
        return "A read is still waiting for its relay", False
    read = request_read(session, services.automation_user(session), scheduled=True)
    return f"Asked for a Juniper read (run #{read.run_id})", True
