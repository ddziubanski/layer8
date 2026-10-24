# SPDX-License-Identifier: AGPL-3.0-or-later
"""Network discovery over SNMP: a relay asks every address in a subnet for its identity (name,
model, serial), its LLDP neighbours and, for switches, its MAC forwarding table. Discovered gear
is kept as network devices; MAC tables say which switch port a documented address is on.

SNMP v2c, read-only, standard library only on the relay. The communities reach the relay
sealed (relay 1.2 hands them to the script on stdin). Unlike IPAM scans this changes no
documentation: what gear says about itself is simply its latest state.
"""

import json
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import ipam
from app.config import settings
from app.models import Device, IpAddress, IpScan, NetworkDevice, NetworkNeighbor, Subnet, SwitchPortMac

SCRIPT_NAME = "Layer 8: SNMP discovery"
HOST, MACS, DONE = "LAYER8-SNMP ", "LAYER8-SNMP-MACS ", "LAYER8-SNMP-DONE "
# A /22: every address is asked, 64 at a time.
MAX_HOSTS = 1024
SCRIPT_BODY = r'''# Built in: Layer 8 SNMP discovery (IP addresses > a subnet > Discover (SNMP)). Standard library only.
# Asks every address in the subnet over SNMP v2c (read-only) for its name, model and serial, its
# LLDP neighbours and, on switches, the MAC forwarding table. The communities come on stdin (runner 1.2+).
import concurrent.futures
import ipaddress
import json
import os
import random
import socket
import sys

secrets = json.loads(sys.stdin.read() or "{}")
communities = [c.strip() for c in str(secrets.get("communities") or "").replace("\n", ",").split(",") if c.strip()]
if not communities:
    print("No SNMP community reached this script: set one in Settings > IP addresses, and use runner 1.2 or later.")
    sys.exit(2)
net = ipaddress.ip_network(os.environ["HD_NETWORK"], strict=False)
hosts = [str(h) for h in (net.hosts() if net.num_addresses > 2 else net)]
print(f"Asking {len(hosts)} addresses in {net} over SNMP", flush=True)


def tlv(tag, value):
    n = len(value)
    length = bytes([n]) if n < 0x80 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag]) + length + value


def enc_int(i):
    return tlv(0x02, i.to_bytes(max(1, (i.bit_length() + 8) // 8), "big", signed=True))


def enc_oid(oid):
    parts = [int(x) for x in oid.split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(chunk))
    return tlv(0x06, bytes(out))


def dec(buf, i):
    tag, n = buf[i], buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    return tag, buf[i:i + n], i + n


def dec_oid(v):
    parts, n = [v[0] // 40, v[0] % 40], 0
    for b in v[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(n)
            n = 0
    return ".".join(map(str, parts))


def value(tag, v):
    if tag in (0x02, 0x41, 0x42, 0x43, 0x46):
        return int.from_bytes(v, "big", signed=tag == 0x02)
    if tag == 0x06:
        return dec_oid(v)
    if tag == 0x40:
        return ".".join(map(str, v))
    if tag == 0x04:
        return bytes(v)
    return None  # NULL, noSuchObject, noSuchInstance, endOfMibView


def ask(sock, ip, community, pdu_type, oids, max_rep=0):
    """One request; [(oid, tag, value)] or None when nothing answered."""
    rid = random.randint(1, 2**31 - 1)
    varbinds = b"".join(tlv(0x30, enc_oid(o) + tlv(0x05, b"")) for o in oids)
    pdu = tlv(pdu_type, enc_int(rid) + enc_int(0) + enc_int(max_rep) + tlv(0x30, varbinds))
    msg = tlv(0x30, enc_int(1) + tlv(0x04, community.encode()) + pdu)
    for _ in range(2):
        try:
            sock.sendto(msg, (ip, 161))
            while True:
                data, _ = sock.recvfrom(65535)
                _, body, _ = dec(data, 0)
                _, _, i = dec(body, 0)
                _, _, i = dec(body, i)
                _, p, _ = dec(body, i)
                _, got_rid, j = dec(p, 0)
                if int.from_bytes(got_rid, "big", signed=True) != rid:
                    continue
                _, err, j = dec(p, j)
                _, _, j = dec(p, j)
                if int.from_bytes(err, "big"):
                    return []
                _, vbl, _ = dec(p, j)
                out, k = [], 0
                while k < len(vbl):
                    _, vb, k = dec(vbl, k)
                    _, o, m = dec(vb, 0)
                    t, v, _ = dec(vb, m)
                    out.append((dec_oid(o), t, value(t, v)))
                return out
        except socket.timeout:
            continue
        except (OSError, IndexError, ValueError):
            return None
    return None


def walk(sock, ip, community, base, limit=20000):
    """{index suffix: value} under base, with GETBULK."""
    out, oid = {}, base
    while len(out) < limit:
        got = ask(sock, ip, community, 0xA5, [oid], 25)
        if not got:
            break
        for o, t, v in got:
            if t == 0x82 or not o.startswith(base + "."):
                return out
            out[o[len(base) + 1:]] = v
            oid = o
    return out


def text(v):
    if isinstance(v, bytes):
        try:
            s = v.decode("utf-8")
        except UnicodeDecodeError:
            return v.hex(":")
        return "".join(c for c in s if c.isprintable()).strip()
    return "" if v is None else str(v)


def mac_text(v):
    """LLDP IDs: six raw bytes are a MAC, unless they read as text ("port 1")."""
    if isinstance(v, bytes) and len(v) == 6 and not all(32 <= b < 127 for b in v):
        return v.hex(":")
    return text(v)


SYS = "1.3.6.1.2.1.1."


def probe(ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    try:
        for community in communities:
            got = ask(sock, ip, community, 0xA0, [SYS + f"{n}.0" for n in (1, 2, 3, 4, 5, 6)])
            if got and any(v is not None for _, _, v in got):
                return community
        return None
    finally:
        sock.close()


with concurrent.futures.ThreadPoolExecutor(64) as pool:
    found = [(ip, c) for ip, c in zip(hosts, pool.map(probe, hosts)) if c]
print(f"{len(found)} answered SNMP", flush=True)


def details(ip, community):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    try:
        sysv = {o: v for o, _, v in (ask(sock, ip, community, 0xA0, [SYS + f"{n}.0" for n in (1, 2, 3, 4, 5, 6)]) or [])}
        h = {
            "ip": ip, "sys_descr": text(sysv.get(SYS + "1.0")), "sys_object_id": text(sysv.get(SYS + "2.0")),
            "uptime": (sysv.get(SYS + "3.0") or 0) // 100 if isinstance(sysv.get(SYS + "3.0"), int) else None,
            "contact": text(sysv.get(SYS + "4.0")), "sys_name": text(sysv.get(SYS + "5.0")), "location": text(sysv.get(SYS + "6.0")),
            "model": "", "serial": "", "kind": "other", "neighbors": [], "macs": [],
        }
        # ENTITY-MIB: the chassis' model and serial (else the first part that has a serial).
        classes = walk(sock, ip, community, "1.3.6.1.2.1.47.1.1.1.1.5", 500)
        serials = walk(sock, ip, community, "1.3.6.1.2.1.47.1.1.1.1.11", 500)
        models = walk(sock, ip, community, "1.3.6.1.2.1.47.1.1.1.1.13", 500)
        order = [i for i, c in classes.items() if c == 3] + [i for i in serials if text(serials[i])]
        for i in order:
            if text(serials.get(i)) or text(models.get(i)):
                h["serial"], h["model"] = text(serials.get(i)), text(models.get(i))
                break
        # Printers: Printer-MIB serial and HOST-RESOURCES model.
        printer = {o: v for o, _, v in (ask(sock, ip, community, 0xA0, ["1.3.6.1.2.1.43.5.1.1.17.1", "1.3.6.1.2.1.25.3.2.1.3.1"]) or [])}
        if text(printer.get("1.3.6.1.2.1.43.5.1.1.17.1")):
            h["kind"] = "printer"
            h["serial"] = h["serial"] or text(printer["1.3.6.1.2.1.43.5.1.1.17.1"])
            h["model"] = h["model"] or text(printer.get("1.3.6.1.2.1.25.3.2.1.3.1"))
        # Interface names, by ifIndex.
        ifname = {i: text(v) for i, v in walk(sock, ip, community, "1.3.6.1.2.1.31.1.1.1.1", 5000).items()}
        if not ifname:
            ifname = {i: text(v) for i, v in walk(sock, ip, community, "1.3.6.1.2.1.2.2.1.2", 5000).items()}
        # LLDP: local port names, then neighbours (index: timeMark.localPort.remIndex).
        loc = "1.0.8802.1.1.2.1.3.7.1."
        loc_id, loc_desc = walk(sock, ip, community, loc + "3", 2000), walk(sock, ip, community, loc + "4", 2000)
        rem = "1.0.8802.1.1.2.1.4.1.1."
        cols = {name: walk(sock, ip, community, rem + n, 2000) for name, n in (("chassis", "5"), ("port", "7"), ("port_desc", "8"), ("name", "9"))}
        addrs = {}
        for idx in walk(sock, ip, community, "1.0.8802.1.1.2.1.4.2.1.3", 2000):
            p = idx.split(".")
            # timeMark.localPort.remIndex.addrSubtype(1 = IPv4).length(4).a.b.c.d
            if len(p) == 9 and p[3] == "1" and p[4] == "4":
                addrs.setdefault(".".join(p[:3]), ".".join(p[5:9]))
        for idx in sorted(set().union(*[set(c) for c in cols.values()])):
            local = idx.split(".")[1] if idx.count(".") >= 2 else ""
            port_name = text(loc_desc.get(local)) or text(loc_id.get(local)) or ifname.get(local, local)
            h["neighbors"].append({
                "local_port": port_name, "name": text(cols["name"].get(idx)), "port": mac_text(cols["port"].get(idx)),
                "port_desc": text(cols["port_desc"].get(idx)), "chassis": mac_text(cols["chassis"].get(idx)), "address": addrs.get(idx),
            })
        # Bridge MAC table: Q-BRIDGE (index: vlan.mac) or the plain BRIDGE-MIB (index: mac).
        base_if = {i: str(v) for i, v in walk(sock, ip, community, "1.3.6.1.2.1.17.1.4.1.2", 5000).items()}
        if base_if:
            h["kind"] = "switch" if h["kind"] == "other" else h["kind"]
            fdb, status = walk(sock, ip, community, "1.3.6.1.2.1.17.7.1.2.2.1.2"), walk(sock, ip, community, "1.3.6.1.2.1.17.7.1.2.2.1.3")
            entries = [(k.split(".")[0], k.split(".")[1:], v) for k, v in fdb.items()]
            if not fdb:
                fdb, status = walk(sock, ip, community, "1.3.6.1.2.1.17.4.3.1.2"), walk(sock, ip, community, "1.3.6.1.2.1.17.4.3.1.3")
                entries = [(None, k.split("."), v) for k, v in fdb.items()]
            for key, (vlan, mac, port) in zip(fdb, entries):
                # 3: learned (not the switch's own, 4, or static, 5).
                if status and status.get(key) not in (None, 3):
                    continue
                if len(mac) != 6 or not port:
                    continue
                name = ifname.get(base_if.get(str(port), ""), "") or f"port {port}"
                h["macs"].append(["".join(f"{int(x):02x}" for x in mac), name, int(vlan) if vlan else None])
        return h
    finally:
        sock.close()


answered = 0
with concurrent.futures.ThreadPoolExecutor(8) as pool:
    for h in pool.map(lambda f: details(*f), found):
        answered += 1
        macs = h.pop("macs")
        print(f"  {h['ip']:<16} {h['sys_name'] or '-':<24} {h['model'] or h['sys_descr'][:40]}  {len(h['neighbors'])} neighbours, {len(macs)} MACs", flush=True)
        print("LAYER8-SNMP " + json.dumps({**h, "macs": macs[:2000]}), flush=True)
        for i in range(2000, len(macs), 2000):
            print("LAYER8-SNMP-MACS " + json.dumps({"ip": h["ip"], "macs": macs[i:i + 2000]}), flush=True)
print("LAYER8-SNMP-DONE " + json.dumps({"network": str(net), "asked": len(hosts), "answered": answered}))
'''

# Enterprise numbers (1.3.6.1.4.1.N) of common makers, for gear that doesn't say.
VENDORS = {
    9: "Cisco", 29671: "Cisco Meraki", 11: "HP", 25506: "HPE", 47196: "HPE Aruba", 14823: "Aruba", 30065: "Arista", 2636: "Juniper",
    4526: "Netgear", 171: "D-Link", 41112: "Ubiquiti", 4413: "Ubiquiti", 674: "Dell", 6027: "Dell", 12356: "Fortinet", 25461: "Palo Alto Networks",
    2011: "Huawei", 318: "APC", 534: "Eaton", 705: "Vertiv", 367: "Ricoh", 1602: "Canon", 2435: "Brother", 1347: "Kyocera", 253: "Xerox",
    1248: "Epson", 641: "Lexmark", 18334: "Konica Minolta", 2001: "OKI", 236: "Samsung", 6876: "VMware", 311: "Microsoft", 8072: "Net-SNMP",
    14988: "MikroTik", 3375: "F5", 6486: "Alcatel-Lucent", 1916: "Extreme Networks", 1991: "Brocade", 207: "Allied Telesis", 5624: "Enterasys",
    6889: "Avaya", 10002: "Ubiquiti", 21839: "Synology", 24681: "QNAP", 8741: "SonicWall", 3224: "Juniper (NetScreen)",
}


class DiscoveryError(ValueError):
    """The message is safe to show."""


def configured() -> bool:
    return bool(communities())


def communities() -> list[str]:
    return [c.strip() for c in settings.snmp_communities.replace("\n", ",").split(",") if c.strip()]


def vendor_of(sys_object_id: str) -> str:
    parts = sys_object_id.split(".")
    if len(parts) > 6 and parts[:6] == ["1", "3", "6", "1", "4", "1"] and parts[6].isdigit():
        return VENDORS.get(int(parts[6]), "")
    return ""


def parse(output: str) -> list[dict]:
    """The hosts in a discovery's output, with their MAC tables put back together."""
    hosts: dict[str, dict] = {}
    done = False
    for line in output.splitlines():
        try:
            if line.startswith(HOST):
                h = json.loads(line[len(HOST):])
                if isinstance(h, dict) and h.get("ip"):
                    hosts[str(h["ip"])] = h
            elif line.startswith(MACS):
                m = json.loads(line[len(MACS):])
                if isinstance(m, dict) and str(m.get("ip")) in hosts:
                    hosts[str(m["ip"])].setdefault("macs", []).extend(m.get("macs") or [])
            elif line.startswith(DONE):
                done = True
        except ValueError:
            continue
    if not done:
        raise DiscoveryError("The discovery's output has no results line (cut off, or the script failed)")
    return list(hosts.values())


def _s(value, n: int) -> str:
    return str(value or "").strip()[:n]


def _match_device(session: Session, nd: NetworkDevice) -> int | None:
    """The asset this gear is: one device with its serial, else one with its name."""
    if nd.serial:
        ids = list(session.scalars(select(Device.id).where(func.lower(Device.serial) == nd.serial.lower()).limit(2)))
        if len(ids) == 1:
            return ids[0]
    if nd.sys_name:
        short = nd.sys_name.lower().split(".")[0]
        ids = list(session.scalars(select(Device.id).where(func.lower(Device.name) == short).limit(2)))
        if len(ids) == 1:
            return ids[0]
    return None


def apply_results(session: Session, scan: IpScan, hosts: list[dict], at: datetime) -> None:
    subnet = session.get(Subnet, scan.subnet_id)
    net = ipam.network(subnet.network)
    seen = 0
    for h in hosts:
        try:
            addr = ipam.parse_address(str(h["ip"]))
        except ipam.IpamError:
            continue
        if addr not in net:
            continue
        seen += 1
        nd = session.scalar(select(NetworkDevice).where(NetworkDevice.address == func.inet(str(addr))))
        if nd is None:
            nd = NetworkDevice(address=str(addr), first_seen_at=at)
            session.add(nd)
        nd.sys_name, nd.sys_descr, nd.sys_object_id = _s(h.get("sys_name"), 255), _s(h.get("sys_descr"), 5000), _s(h.get("sys_object_id"), 255)
        nd.location, nd.contact = _s(h.get("location"), 255), _s(h.get("contact"), 255)
        nd.model, nd.serial = _s(h.get("model"), 255), _s(h.get("serial"), 100)
        nd.vendor = vendor_of(nd.sys_object_id)
        nd.kind = h.get("kind") if h.get("kind") in ("switch", "printer", "other") else "other"
        nd.uptime_seconds = h["uptime"] if isinstance(h.get("uptime"), int) else None
        nd.subnet_id, nd.last_seen_at = subnet.id, at
        session.flush()
        if nd.device_id is None:
            nd.device_id = _match_device(session, nd)
        # Neighbours and the MAC table are as of now.
        nd.neighbors = []
        for n in h.get("neighbors") or []:
            if not isinstance(n, dict):
                continue
            try:
                remote_addr = str(ipam.parse_address(str(n["address"]))) if n.get("address") else None
            except ipam.IpamError:
                remote_addr = None
            nd.neighbors.append(NetworkNeighbor(
                local_port=_s(n.get("local_port"), 100), remote_name=_s(n.get("name"), 255), remote_port=_s(n.get("port"), 255),
                remote_port_desc=_s(n.get("port_desc"), 255), remote_chassis=_s(n.get("chassis"), 100), remote_address=remote_addr,
            ))
        session.execute(delete(SwitchPortMac).where(SwitchPortMac.network_device_id == nd.id))
        rows = []
        for m in h.get("macs") or []:
            if not isinstance(m, list) or len(m) < 2:
                continue
            try:
                mac = ipam.normalize_mac(str(m[0]))
            except ipam.IpamError:
                continue
            vlan = m[2] if len(m) > 2 and isinstance(m[2], int) else None
            rows.append((mac, _s(m[1], 100), vlan))
        per_port: dict[str, int] = {}
        for _, port, _ in rows:
            per_port[port] = per_port.get(port, 0) + 1
        session.add_all(SwitchPortMac(network_device_id=nd.id, mac=mac, port=port, vlan=vlan, port_mac_count=per_port[port], seen_at=at) for mac, port, vlan in rows)
        # The gear's own documented address counts as seen.
        rec = session.scalar(select(IpAddress).where(IpAddress.address == func.inet(str(addr))))
        if rec is not None:
            rec.first_seen_at = rec.first_seen_at or at
            rec.last_seen_at = at
            if not rec.hostname and nd.sys_name:
                rec.hostname = nd.sys_name
    scan.processed_at, scan.hosts_seen, scan.new_findings, scan.error = at, seen, 0, None


def ports_for(session: Session, macs) -> dict[str, dict]:
    """Where each MAC plugs in: the switch port with the fewest MACs on it (an edge port rather
    than an uplink), the most recently seen first. {mac: {switch_id, switch, port, vlan, seen_at}}."""
    macs = {m for m in macs if m}
    if not macs:
        return {}
    rows = session.execute(
        select(SwitchPortMac, NetworkDevice).join(NetworkDevice, NetworkDevice.id == SwitchPortMac.network_device_id)
        .where(SwitchPortMac.mac.in_(macs)).order_by(SwitchPortMac.port_mac_count, SwitchPortMac.seen_at.desc())
    ).all()
    out: dict[str, dict] = {}
    for spm, nd in rows:
        if spm.mac not in out:
            out[spm.mac] = {
                "switch_id": nd.id, "switch": nd.sys_name or str(ipam.address(nd.address)), "port": spm.port, "vlan": spm.vlan,
                "seen_at": spm.seen_at, "shared": spm.port_mac_count > 1,
            }
    return out
