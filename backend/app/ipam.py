# SPDX-License-Identifier: AGPL-3.0-or-later
"""IPAM helpers: parsing what people type (and what spreadsheets hold), and how full a subnet is."""

import ipaddress
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import IpAddress, IpKind, Subnet

# Subnets up to this many addresses get a cell-by-cell map (a /22).
MAP_LIMIT = 1024
# Above this, free addresses aren't counted (a /16).
COUNT_LIMIT = 65536

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class IpamError(ValueError):
    pass


def parse_network(text: str) -> Network:
    """10.1.20.0/24; host bits set (10.1.20.1/24) is accepted and means that subnet."""
    try:
        return ipaddress.ip_network(text.strip(), strict=False)
    except ValueError:
        raise IpamError(f"{text!r} isn't a subnet like 10.1.20.0/24") from None


def parse_address(text: str) -> Address:
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        raise IpamError(f"{text!r} isn't an IP address") from None


def network(value) -> Network:
    return value if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)) else ipaddress.ip_network(str(value), strict=False)


def address(value) -> Address:
    if isinstance(value, (ipaddress.IPv4Interface, ipaddress.IPv6Interface)):
        return value.ip
    return value if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ipaddress.ip_address(str(value))


def normalize_mac(text: str) -> str:
    """aa:bb:cc:dd:ee:ff from Windows (AA-BB-...), Cisco/Arista (aabb.ccdd.eeff) or bare hex."""
    text = text.strip()
    if not text:
        return ""
    digits = re.sub(r"[^0-9a-fA-F]", "", text)
    if len(digits) != 12 or re.search(r"[^0-9a-fA-F:.\-\s]", text):
        raise IpamError(f"{text!r} isn't a MAC address")
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2)).lower()


def hosts_range(net: Network) -> tuple[int, int]:
    """First and last usable host as integers (a /31 or /32 uses every address)."""
    first, last = int(net.network_address), int(net.broadcast_address)
    if net.version == 4 and net.prefixlen < 31:
        return first + 1, last - 1
    if net.version == 6 and net.prefixlen < 127:
        return first + 1, last  # no broadcast in IPv6; the all-zeros address is the router anycast
    return first, last


def pool_range(s: Subnet) -> tuple[int, int] | None:
    if s.dhcp_start is None or s.dhcp_end is None:
        return None
    return int(address(s.dhcp_start)), int(address(s.dhcp_end))


def in_subnet(stmt_column, s: Subnet):
    return stmt_column.op("<<=")(s.network)


def records(session: Session, s: Subnet) -> list[IpAddress]:
    return list(session.scalars(select(IpAddress).where(in_subnet(IpAddress.address, s)).order_by(IpAddress.address)))


def subnet_of(session: Session, addr) -> Subnet | None:
    """The most specific subnet containing the address."""
    return session.scalar(
        select(Subnet).where(Subnet.network.op(">>=")(func.inet(str(address(addr))))).order_by(func.masklen(Subnet.network).desc()).limit(1)
    )


def usage(s: Subnet, recs: list[IpAddress]) -> dict:
    net = network(s.network)
    first, last = hosts_range(net)
    hosts = last - first + 1
    counts = {k.value: 0 for k in IpKind}
    taken = set()
    for r in recs:
        counts[r.kind] += 1
        taken.add(int(address(r.address)))
    pool = pool_range(s)
    pool_size = pool[1] - pool[0] + 1 if pool else 0
    free = None
    if hosts <= COUNT_LIMIT:
        if s.gateway is not None:
            taken.add(int(address(s.gateway)))
        in_pool = sum(1 for a in taken if pool and pool[0] <= a <= pool[1])
        free = hosts - len({a for a in taken if first <= a <= last}) - (pool_size - in_pool)
    return {"hosts": hosts, "pool": pool_size, "free": max(free, 0) if free is not None else None, **counts}


def cells(s: Subnet, recs: list[IpAddress]) -> list[dict] | None:
    net = network(s.network)
    if net.num_addresses > MAP_LIMIT:
        return None
    first, last = hosts_range(net)
    by_int = {int(address(r.address)): r for r in recs}
    gateway = int(address(s.gateway)) if s.gateway is not None else None
    pool = pool_range(s)
    out = []
    for i in range(int(net.network_address), int(net.broadcast_address) + 1):
        text = str(ipaddress.ip_address(i) if net.version == 4 else ipaddress.IPv6Address(i))
        r = by_int.get(i)
        if r is not None:
            out.append({"address": text, "state": r.kind.value if hasattr(r.kind, "value") else r.kind, "hostname": r.hostname, "id": r.id})
        elif i < first:
            out.append({"address": text, "state": "network"})
        elif i > last:
            out.append({"address": text, "state": "broadcast"})
        elif i == gateway:
            out.append({"address": text, "state": "gateway"})
        elif pool and pool[0] <= i <= pool[1]:
            out.append({"address": text, "state": "pool"})
        else:
            out.append({"address": text, "state": "free"})
    return out


def next_free(s: Subnet, recs: list[IpAddress]) -> str | None:
    """The lowest host address that isn't documented, the gateway, or in the DHCP pool."""
    net = network(s.network)
    first, last = hosts_range(net)
    taken = {int(address(r.address)) for r in recs}
    if s.gateway is not None:
        taken.add(int(address(s.gateway)))
    pool = pool_range(s)
    i = first
    while i <= last and i - first < COUNT_LIMIT:
        if pool and pool[0] <= i <= pool[1]:
            i = pool[1] + 1
            continue
        if i not in taken:
            return str(ipaddress.ip_address(i) if net.version == 4 else ipaddress.IPv6Address(i))
        i += 1
    return None
