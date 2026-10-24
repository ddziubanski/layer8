# SPDX-License-Identifier: AGPL-3.0-or-later
"""IPAM scans: a runner pings a subnet, looks up reverse DNS and reads its ARP table; another
(on Windows) reads the DHCP server's leases and reservations and the DNS server's A records. What
they find updates last-seen, fills empty host names and MACs, and raises findings for everything
else. Findings change nothing until someone applies them.
"""

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ipam, netdisco, runs, services, sla
from app.config import settings
from app.models import (
    FindingKind,
    FindingStatus,
    IpAddress,
    IpFinding,
    IpKind,
    IpScan,
    IpSource,
    Runner,
    RunStatus,
    ScanKind,
    Script,
    ScriptKind,
    ScriptLanguage,
    ScriptRun,
    ScriptVersion,
    Subnet,
    User,
)

SCRIPT_NAME = "Layer 8: IP scan"
MARKER = "LAYER8-SCAN-RESULT "
# A /20: about a minute of pings at 64 at a time.
MAX_HOSTS = 4096
SCRIPT_BODY = r'''# Built in: Layer 8 IP scan (IP addresses > a subnet > Scan now). Standard library only.
# Pings every address in the subnet, looks up reverse DNS, and reads this runner's ARP /
# neighbour table: on the runner's own LAN that also finds devices that ignore ping, with MACs.
import concurrent.futures
import ipaddress
import json
import os
import platform
import re
import socket
import subprocess

net = ipaddress.ip_network(os.environ["HD_NETWORK"], strict=False)
hosts = list(net.hosts()) if net.num_addresses > 2 else list(net)
windows = platform.system() == "Windows"
print(f"Scanning {net}: {len(hosts)} addresses", flush=True)


def ping(ip):
    cmd = ["ping", "-n", "1", "-w", "1000", str(ip)] if windows else ["ping", "-c", "1", "-W", "1", str(ip)]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # Windows ping exits 0 on "Destination host unreachable" too: a real reply has a TTL.
    return r.returncode == 0 and (not windows or "TTL=" in r.stdout.upper())


with concurrent.futures.ThreadPoolExecutor(64) as pool:
    alive = {str(ip) for ip, ok in zip(hosts, pool.map(ping, hosts)) if ok}
print(f"{len(alive)} answered ping", flush=True)

macs = {}
for cmd in (["ip", "neigh", "show"], ["arp", "-a"], ["arp", "-an"]):
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        continue
    for line in out.splitlines():
        ip = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]*:[0-9a-fA-F:]+)\b", line)
        mac = re.search(r"\b([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})\b", line)
        if not ip or not mac or "FAILED" in line or "INCOMPLETE" in line:
            continue
        try:
            addr = ipaddress.ip_address(ip.group(1))
        except ValueError:
            continue
        if addr in net and mac.group(1).lower() not in ("ff-ff-ff-ff-ff-ff", "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
            macs[str(addr)] = mac.group(1)
    if macs:
        break
only_arp = set(macs) - alive
if only_arp:
    print(f"{len(only_arp)} more found in the ARP table (they ignore ping)", flush=True)


def name(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


seen = sorted(alive | set(macs), key=ipaddress.ip_address)
with concurrent.futures.ThreadPoolExecutor(32) as pool:
    names = dict(zip(seen, pool.map(name, seen)))
for ip in seen:
    print(f"  {ip:<16} {macs.get(ip, ''):<18} {names[ip]}")
print("LAYER8-SCAN-RESULT " + json.dumps({"network": str(net), "hosts": [{"ip": ip, "hostname": names[ip], "mac": macs.get(ip, ""), "ping": ip in alive} for ip in seen]}))
'''
PARAMETERS = [{"name": "network", "label": "Subnet (CIDR)", "type": "text", "required": True, "default": None, "choices": []}]

DHCP_DNS_NAME = "Layer 8: DHCP and DNS read"
DHCP_DNS_BODY = r'''# Built in: Layer 8 DHCP and DNS read (IP addresses > a subnet > Read DHCP and DNS).
# Reads a Windows DHCP server's scopes, reservations and leases, and a Windows DNS server's A
# records, for one subnet. Changes nothing. Needs the DhcpServer and DnsServer modules (RSAT) and
# an account in "DHCP Users" that can read the DNS zones.
$ErrorActionPreference = 'Stop'
$base, $bits = $network.Split('/')
if ($base -like '*:*') { throw "Only IPv4 subnets can be read from DHCP and DNS" }
function Num([string]$ip) {
    $b = ([Net.IPAddress]::Parse($ip)).GetAddressBytes()
    [Array]::Reverse($b)
    [int64][BitConverter]::ToUInt32($b, 0)
}
$first = Num $base
$size = [int64][Math]::Pow(2, 32 - [int]$bits)
function InNet([string]$ip) { $n = Num $ip; ($n -ge $first) -and ($n -lt $first + $size) }
$hosts = @{}
function Entry([string]$ip) {
    if (-not $hosts.ContainsKey($ip)) { $hosts[$ip] = @{ ip = $ip; hostname = ''; mac = ''; kind = ''; active = $false; dns = @() } }
    $hosts[$ip]
}

$scopes = @()
if ($dhcp_server) {
    Write-Output "Reading DHCP on $dhcp_server"
    foreach ($s in Get-DhcpServerv4Scope -ComputerName $dhcp_server) {
        $start = $s.StartRange.IPAddressToString
        $end = $s.EndRange.IPAddressToString
        if ((Num $start) -ge $first + $size -or (Num $end) -lt $first) { continue }
        Write-Output "  scope $($s.ScopeId) $($s.Name): $start - $end"
        $scopes += @{ scope = $s.ScopeId.IPAddressToString; name = [string]$s.Name; start = $start; end = $end }
        foreach ($r in Get-DhcpServerv4Reservation -ComputerName $dhcp_server -ScopeId $s.ScopeId) {
            $ip = $r.IPAddress.IPAddressToString
            if (-not (InNet $ip)) { continue }
            $h = Entry $ip
            $h.kind = 'reserved'; $h.mac = [string]$r.ClientId; $h.hostname = [string]$r.Name
        }
        foreach ($l in Get-DhcpServerv4Lease -ComputerName $dhcp_server -ScopeId $s.ScopeId) {
            $ip = $l.IPAddress.IPAddressToString
            if (-not (InNet $ip)) { continue }
            $h = Entry $ip
            if (-not $h.kind) { $h.kind = 'dhcp'; $h.mac = [string]$l.ClientId }
            if (-not $h.hostname) { $h.hostname = [string]$l.HostName }
            if ([string]$l.AddressState -like 'Active*') { $h.active = $true }
        }
    }
}

if ($dns_server) {
    $zones = @("$dns_zones" -split '[,;\s]+' | Where-Object { $_ })
    if (-not $zones) {
        $zones = @(Get-DnsServerZone -ComputerName $dns_server | Where-Object {
            -not $_.IsReverseLookupZone -and -not $_.IsAutoCreated -and $_.ZoneType -eq 'Primary' -and $_.ZoneName -ne 'TrustAnchors' -and $_.ZoneName -notlike '_msdcs.*'
        } | ForEach-Object { $_.ZoneName })
    }
    foreach ($z in $zones) {
        Write-Output "Reading DNS zone $z on $dns_server"
        foreach ($rec in Get-DnsServerResourceRecord -ComputerName $dns_server -ZoneName $z -RRType A) {
            $name = [string]$rec.HostName
            if ($name -eq '@' -or $name -like '_*' -or $name -in @('DomainDnsZones', 'ForestDnsZones')) { continue }
            $ip = $rec.RecordData.IPv4Address.IPAddressToString
            if (-not (InNet $ip)) { continue }
            $h = Entry $ip
            $fqdn = "$name.$z".ToLower()
            if ($h.dns -notcontains $fqdn) { $h.dns += $fqdn }
        }
    }
}

$list = @($hosts.Values | Sort-Object { Num $_.ip })
foreach ($h in $list) {
    if (-not $h.hostname -and $h.dns.Count) { $h.hostname = $h.dns[0] }
    Write-Output ("  {0,-16} {1,-18} {2,-9} {3}" -f $h.ip, $h.mac, $(if ($h.kind) { $h.kind } else { 'dns' }), $h.hostname)
}
Write-Output ("{0} addresses: {1} reserved, {2} leased, {3} only in DNS" -f $list.Count, @($list | Where-Object { $_.kind -eq 'reserved' }).Count, @($list | Where-Object { $_.kind -eq 'dhcp' }).Count, @($list | Where-Object { -not $_.kind }).Count)
Write-Output ("LAYER8-SCAN-RESULT " + (ConvertTo-Json -Compress -Depth 5 @{ network = $network; scopes = @($scopes); hosts = $list }))
'''
DHCP_DNS_PARAMETERS = [
    *PARAMETERS,
    {"name": "dhcp_server", "label": "DHCP server", "type": "text", "required": False, "default": None, "choices": []},
    {"name": "dns_server", "label": "DNS server", "type": "text", "required": False, "default": None, "choices": []},
    {"name": "dns_zones", "label": "DNS zones", "type": "text", "required": False, "default": None, "choices": []},
]
# Per kind of scan: the built-in script's name, language, body, parameters and description.
SCRIPTS = {
    ScanKind.network: (SCRIPT_NAME, ScriptLanguage.python, SCRIPT_BODY, PARAMETERS, "Built in: scans a subnet for IP addresses > Scan now and scheduled scans. Needs a runner that takes Python."),
    ScanKind.dhcp_dns: (
        DHCP_DNS_NAME, ScriptLanguage.powershell, DHCP_DNS_BODY, DHCP_DNS_PARAMETERS,
        "Built in: reads Windows DHCP leases and reservations and DNS records for IP addresses. Needs a Windows runner with RSAT (DhcpServer, DnsServer).",
    ),
    ScanKind.snmp: (
        netdisco.SCRIPT_NAME, ScriptLanguage.python, netdisco.SCRIPT_BODY, PARAMETERS,
        "Built in: SNMP discovery of network gear for IP addresses > Discover (SNMP). Needs a runner that takes Python, version 1.2 or later.",
    ),
}
# What each kind is called in messages.
WHAT = {ScanKind.network: "A scan", ScanKind.dhcp_dns: "A DHCP and DNS read", ScanKind.snmp: "An SNMP discovery"}


class ScanError(ValueError):
    """The message is safe to show."""


def ensure_script(session: Session, kind: ScanKind = ScanKind.network) -> Script:
    name, language, body, parameters, description = SCRIPTS[kind]
    script = session.scalar(select(Script).where(Script.name == name))
    if script is None:
        script = Script(name=name, kind=ScriptKind.action, language=language, agents_can_run=True, current_version=1, description=description)
        script.versions.append(ScriptVersion(number=1, language=script.language, body=body, parameters=parameters, note="Built in"))
        session.add(script)
        session.flush()
    return script


def dhcp_dns_configured() -> bool:
    return bool(settings.ipam_dhcp_server.strip() or settings.ipam_dns_server.strip())


def pending(session: Session, subnet: Subnet, kind: ScanKind = ScanKind.network) -> IpScan | None:
    """A scan of this subnet that hasn't finished."""
    return session.scalar(
        select(IpScan).join(ScriptRun, ScriptRun.id == IpScan.run_id)
        .where(IpScan.subnet_id == subnet.id, IpScan.kind == kind, ScriptRun.status.in_(runs.OPEN))
        .order_by(IpScan.id.desc()).limit(1)
    )


def request(session: Session, subnet: Subnet, user: User, scheduled: bool = False, kind: ScanKind = ScanKind.network) -> IpScan:
    """Queue a scan: the network scan on the subnet's runner (or any that takes Python), the DHCP and
    DNS read on the runner named in Settings (or any that takes PowerShell). Doesn't commit."""
    net = ipam.network(subnet.network)
    values = {"network": str(net)}
    sealed = None
    if kind == ScanKind.snmp:
        if not netdisco.configured():
            raise ScanError("No SNMP community is set: add one under Settings > IP addresses")
        if net.version != 4:
            raise ScanError("SNMP discovery covers IPv4 subnets")
        if net.num_addresses > netdisco.MAX_HOSTS:
            raise ScanError(f"{net} is too big to discover in one go (at most {netdisco.MAX_HOSTS} addresses, a /22); discover its smaller subnets")
        runner_id = subnet.scan_runner_id
        sealed = {"communities": ",".join(netdisco.communities())}
    elif kind == ScanKind.network:
        if net.num_addresses > MAX_HOSTS:
            raise ScanError(f"{net} is too big to scan in one go (at most {MAX_HOSTS} addresses, a /20); scan its smaller subnets")
        runner_id = subnet.scan_runner_id
    else:
        if not dhcp_dns_configured():
            raise ScanError("No DHCP or DNS server is set: add them under Settings > IP addresses")
        if net.version != 4:
            raise ScanError("Only IPv4 subnets can be read from DHCP and DNS")
        runner_id = None
        if name := settings.ipam_windows_runner.strip():
            runner = session.scalar(select(Runner).where(Runner.name == name, Runner.is_active))
            if runner is None:
                raise ScanError(f"There's no active runner called {name!r} (Settings > IP addresses)")
            runner_id = runner.id
        values |= {"dhcp_server": settings.ipam_dhcp_server.strip(), "dns_server": settings.ipam_dns_server.strip(), "dns_zones": settings.ipam_dns_zones.strip()}
    if pending(session, subnet, kind) is not None:
        raise ScanError(f"{WHAT[kind]} of this subnet is already waiting or running")
    try:
        (run,) = runs.request(
            session, ensure_script(session, kind), user, device_ids=[], on_runner=True, values=values, ticket=None, only_runner_id=runner_id, sealed=sealed,
        )
    except runs.RunError as exc:
        raise ScanError(str(exc)) from None
    scan = IpScan(subnet_id=subnet.id, run_id=run.id, requested_by_id=user.id, scheduled=scheduled, kind=kind)
    session.add(scan)
    session.flush()
    return scan


def _list(value) -> list:
    """PowerShell can write a list of one as the one, and Windows PowerShell 5.1 a list as {"value": [...], "Count": n}."""
    if isinstance(value, dict) and isinstance(value.get("value"), list):
        return value["value"]
    return value if isinstance(value, list) else [value] if value else []


def parse_data(output: str) -> dict:
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            data = json.loads(line[len(MARKER):])
            if not isinstance(data, dict):
                break
            data["hosts"] = [h for h in _list(data.get("hosts")) if isinstance(h, dict) and h.get("ip")]
            for h in data["hosts"]:
                h["dns"] = _list(h.get("dns"))
            data["scopes"] = [x for x in _list(data.get("scopes")) if isinstance(x, dict)]
            return data
    raise ScanError("The scan's output has no results line")


def parse(output: str) -> list[dict]:
    return parse_data(output)["hosts"]


def on_finished(session: Session, run: ScriptRun) -> None:
    """Called when any run ends: if it was a scan, read its results."""
    from app import junos

    junos.on_finished(session, run)
    scan = session.scalar(select(IpScan).where(IpScan.run_id == run.id))
    if scan is None or scan.processed_at is not None:
        return
    if run.status != RunStatus.succeeded:
        scan.error = run.error or f"The scan {run.status}"
        return
    at = run.finished_at or sla.utcnow()
    if scan.kind == ScanKind.snmp:
        try:
            netdisco.apply_results(session, scan, netdisco.parse(run.output), at)
        except netdisco.DiscoveryError as exc:
            scan.error = str(exc)
        return
    try:
        data = parse_data(run.output)
    except (ScanError, ValueError) as exc:
        scan.error = str(exc)
        return
    if scan.kind == ScanKind.dhcp_dns:
        apply_dhcp_dns(session, scan, data, at)
    else:
        apply_results(session, scan, data["hosts"], at)


def _same_name(a: str, b: str) -> bool:
    """pc-0042 and pc-0042.corp.example are the same name."""
    a, b = a.strip().lower().rstrip("."), b.strip().lower().rstrip(".")
    if "." in a and "." in b:
        return a == b
    return a.split(".")[0] == b.split(".")[0]


def apply_results(session: Session, scan: IpScan, hosts: list[dict], at) -> None:
    subnet = session.get(Subnet, scan.subnet_id)
    net = ipam.network(subnet.network)
    seen: dict[str, dict] = {}
    for h in hosts:
        try:
            addr = ipam.parse_address(str(h["ip"]))
            mac = ipam.normalize_mac(str(h.get("mac") or ""))
        except ipam.IpamError:
            continue
        if addr in net:
            seen[str(addr)] = {"hostname": str(h.get("hostname") or "")[:255], "mac": mac}
    gateway = str(ipam.address(subnet.gateway)) if subnet.gateway is not None else None
    pool = ipam.pool_range(subnet)
    stale_before = at - timedelta(days=settings.ipam_stale_days)
    found: dict[tuple[str, FindingKind], tuple[dict, IpAddress | None]] = {}

    documented = {str(ipam.address(r.address)): r for r in ipam.records(session, subnet)}
    for text, r in documented.items():
        s = seen.get(text)
        if s is None:
            if r.last_seen_at is not None and r.last_seen_at < stale_before:
                found[(text, FindingKind.not_seen)] = ({"documented_hostname": r.hostname, "last_seen_at": r.last_seen_at.isoformat()}, r)
            continue
        r.first_seen_at = r.first_seen_at or at
        r.last_seen_at = at
        # Fill gaps; differences are findings.
        if not r.hostname and s["hostname"]:
            r.hostname = s["hostname"]
        elif r.hostname and s["hostname"] and not _same_name(r.hostname, s["hostname"]):
            found[(text, FindingKind.hostname_changed)] = ({"hostname": s["hostname"], "documented_hostname": r.hostname}, r)
        if not r.mac and s["mac"]:
            r.mac = s["mac"]
        elif r.mac and s["mac"] and r.mac != s["mac"]:
            found[(text, FindingKind.mac_changed)] = ({"mac": s["mac"], "documented_mac": r.mac, "hostname": s["hostname"]}, r)
        if r.kind == IpKind.do_not_use:
            found[(text, FindingKind.in_use_do_not_use)] = (s, r)
    for text, s in seen.items():
        if text in documented or text == gateway:
            continue
        if pool and pool[0] <= int(ipam.parse_address(text)) <= pool[1] and not settings.ipam_flag_pool_hosts:
            continue
        found[(text, FindingKind.new_host)] = (s, None)

    _record_findings(session, scan, subnet, found, at)
    scan.hosts_seen = len(seen)


def _record_findings(session: Session, scan: IpScan, subnet: Subnet, found: dict, at) -> None:
    """Open findings this kind of scan raised for the subnet: still true -> refreshed; no longer
    true -> cleared. New ones are raised unless dismissed before with the same details."""
    new = 0
    mine = (IpFinding.subnet_id == subnet.id, IpFinding.scan_kind == scan.kind)
    open_ = {(str(ipam.address(f.address)), f.kind): f for f in session.scalars(select(IpFinding).where(*mine, IpFinding.status == FindingStatus.open))}
    for key, f in open_.items():
        if key not in found:
            f.status, f.resolved_at = FindingStatus.cleared, at
    for (text, kind), (details, rec) in found.items():
        f = open_.get((text, kind))
        if f is None:
            dismissed = session.scalar(
                select(IpFinding).where(*mine, IpFinding.address == ipam.parse_address(text), IpFinding.kind == kind, IpFinding.status == FindingStatus.dismissed)
                .order_by(IpFinding.id.desc()).limit(1)
            )
            if dismissed is not None and _same_details(dismissed.details, details):
                continue
            f = IpFinding(subnet_id=subnet.id, address=ipam.parse_address(text), kind=kind, scan_kind=scan.kind, first_found_at=at)
            session.add(f)
            new += 1
        f.details, f.last_found_at, f.scan_id = details, at, scan.id
        f.ip_address_id = rec.id if rec is not None else None
    scan.processed_at, scan.new_findings, scan.error = at, new, None


def _same_details(a: dict, b: dict) -> bool:
    keys = ("hostname", "mac", "documented_hostname", "documented_mac", "kind", "documented_kind")
    return all((a or {}).get(k, "") == (b or {}).get(k, "") for k in keys)


def _mac(text) -> str:
    """DHCP client IDs that aren't Ethernet MACs are left out."""
    try:
        return ipam.normalize_mac(str(text or ""))
    except ipam.IpamError:
        return ""


def apply_dhcp_dns(session: Session, scan: IpScan, data: dict, at) -> None:
    """What DHCP and DNS say. An active lease counts as seen; a reservation or DNS record alone
    doesn't (they outlive the device), and nothing is flagged as gone from them."""
    subnet = session.get(Subnet, scan.subnet_id)
    net = ipam.network(subnet.network)
    # Fill an empty DHCP pool from the server's scope, when exactly one scope covers this subnet.
    scopes = []
    for sc in data.get("scopes", []):
        try:
            start, end = ipam.parse_address(str(sc.get("start", ""))), ipam.parse_address(str(sc.get("end", "")))
        except ipam.IpamError:
            continue
        if start in net and end in net:
            scopes.append((start, end))
    if subnet.dhcp_start is None and subnet.dhcp_end is None and len(scopes) == 1:
        subnet.dhcp_start, subnet.dhcp_end = str(scopes[0][0]), str(scopes[0][1])
    seen: dict[str, dict] = {}
    for h in data["hosts"]:
        try:
            addr = ipam.parse_address(str(h["ip"]))
        except ipam.IpamError:
            continue
        if addr not in net:
            continue
        dns = [str(n)[:255] for n in _list(h.get("dns")) if n]
        kind = str(h.get("kind") or "")
        seen[str(addr)] = {
            "hostname": str(h.get("hostname") or (dns[0] if dns else ""))[:255], "mac": _mac(h.get("mac")), "kind": kind if kind in ("reserved", "dhcp") else "",
            "active": bool(h.get("active")), "dns_names": dns,
        }
    gateway = str(ipam.address(subnet.gateway)) if subnet.gateway is not None else None
    pool = ipam.pool_range(subnet)
    found: dict[tuple[str, FindingKind], tuple[dict, IpAddress | None]] = {}

    def details(s: dict, **extra) -> dict:
        return {"hostname": s["hostname"], "mac": s["mac"], "kind": s["kind"], "dns_names": s["dns_names"], **extra}

    documented = {str(ipam.address(r.address)): r for r in ipam.records(session, subnet)}
    for text, r in documented.items():
        s = seen.get(text)
        if s is None:
            continue
        if s["active"]:
            r.first_seen_at = r.first_seen_at or at
            r.last_seen_at = at
        names = [n for n in (s["hostname"], *s["dns_names"]) if n]
        if not r.hostname and names:
            r.hostname = names[0]
        elif r.hostname and names and not any(_same_name(r.hostname, n) for n in names):
            found[(text, FindingKind.hostname_changed)] = (details(s, documented_hostname=r.hostname), r)
        if not r.mac and s["mac"]:
            r.mac = s["mac"]
        elif r.mac and s["mac"] and r.mac != s["mac"]:
            found[(text, FindingKind.mac_changed)] = (details(s, documented_mac=r.mac), r)
        if r.kind == IpKind.do_not_use:
            found[(text, FindingKind.in_use_do_not_use)] = (details(s), r)
        elif (s["kind"] == "reserved" and r.kind != IpKind.reserved) or (s["kind"] == "dhcp" and r.kind == IpKind.static):
            # A reservation documented as static, or a dynamic lease on an address documented as static.
            found[(text, FindingKind.kind_changed)] = (details(s, documented_kind=str(r.kind)), r)
    for text, s in seen.items():
        if text in documented or text == gateway:
            continue
        in_pool = pool is not None and pool[0] <= int(ipam.parse_address(text)) <= pool[1]
        # Dynamic leases (and DNS names inside the pool) come and go: like pool hosts in a network scan.
        if (s["kind"] == "dhcp" or in_pool and s["kind"] != "reserved") and not settings.ipam_flag_pool_hosts:
            continue
        found[(text, FindingKind.new_host)] = (details(s), None)
    _record_findings(session, scan, subnet, found, at)
    scan.hosts_seen = len(seen)


def resolve(session: Session, f: IpFinding, user: User, apply: bool) -> None:
    """Apply (change the documentation as the finding suggests) or dismiss. Doesn't commit."""
    if f.status != FindingStatus.open:
        raise ScanError("This finding has already been dealt with")
    if apply:
        d = f.details or {}
        rec = session.get(IpAddress, f.ip_address_id) if f.ip_address_id else None
        if f.kind == FindingKind.new_host:
            if session.scalar(select(IpAddress).where(IpAddress.address == f.address)) is not None:
                raise ScanError("That address has been documented since")
            from_dhcp_dns = f.scan_kind == ScanKind.dhcp_dns
            seen = not from_dhcp_dns or d.get("active")
            session.add(IpAddress(
                address=f.address, hostname=d.get("hostname", ""), mac=d.get("mac", ""), kind=IpKind(d.get("kind") or "static"), source=IpSource.scan,
                first_seen_at=f.first_found_at if seen else None, last_seen_at=f.last_found_at if seen else None,
                notes="Found in DHCP or DNS" if from_dhcp_dns else "Found by a scan",
            ))
        elif rec is None:
            raise ScanError("The documented address has gone since")
        elif f.kind == FindingKind.hostname_changed:
            rec.hostname = d.get("hostname", "")
        elif f.kind == FindingKind.mac_changed:
            rec.mac = d.get("mac", "")
        elif f.kind == FindingKind.kind_changed:
            rec.kind = IpKind(d.get("kind") or "static")
        elif f.kind == FindingKind.not_seen:
            session.delete(rec)
        else:
            raise ScanError("Nothing to apply: check what's using the address, then dismiss this")
    f.status = FindingStatus.applied if apply else FindingStatus.dismissed
    f.resolved_by_id, f.resolved_at = user.id, sla.utcnow()


def run_due(session: Session) -> int:
    """Queue scheduled scans that are due. Returns how many. Doesn't commit."""
    now = sla.utcnow()
    made = 0
    kinds = [ScanKind.network] + ([ScanKind.dhcp_dns] if dhcp_dns_configured() else []) + ([ScanKind.snmp] if netdisco.configured() else [])
    for subnet in session.scalars(select(Subnet).where(Subnet.scan_every_hours > 0)):
        for kind in kinds:
            last = session.scalar(select(IpScan.created_at).where(IpScan.subnet_id == subnet.id, IpScan.kind == kind).order_by(IpScan.id.desc()).limit(1))
            if last is not None and last > now - timedelta(hours=subnet.scan_every_hours):
                continue
            try:
                request(session, subnet, services.automation_user(session), scheduled=True, kind=kind)
                made += 1
            except ScanError:
                continue
    return made
