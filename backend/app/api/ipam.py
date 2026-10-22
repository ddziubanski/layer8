"""IPAM: VLANs, subnets, documented IP addresses, and scans with their findings.

Technicians keep addresses up to date, scan and deal with findings; only admins delete subnets
and VLANs (their addresses stay).
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app import ipam, ipamscan, netdisco, permissions
from app.auth import Agent, SessionDep
from app.models import Device, FindingStatus, IpAddress, IpFinding, IpKind, IpScan, Runner, ScanKind, Subnet, System, Vlan
from app.schemas import (
    DeviceIpOut,
    IpCell,
    IpFindingOut,
    IpScanOut,
    ScanRunnerOut,
    IpAddressIn,
    IpAddressOut,
    SubnetDetailOut,
    SubnetIn,
    SubnetOut,
    SubnetRef,
    SubnetUsage,
    SwitchPortOut,
    VlanIn,
    VlanOut,
)

router = APIRouter(prefix="/api/ipam", tags=["ipam"])
IpamDeleter = permissions.require("ipam_delete")
log = logging.getLogger(__name__)


def _bad(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def _get(session, model, id_: int, what: str):
    obj = session.get(model, id_)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")
    return obj


# VLANs


def _vlan_out(session, v: Vlan) -> VlanOut:
    out = VlanOut.model_validate(v)
    out.subnet_count = session.scalar(select(func.count()).select_from(Subnet).where(Subnet.vlan_id == v.id)) or 0
    return out


@router.get("/vlans", response_model=list[VlanOut])
def list_vlans(_: Agent, session: SessionDep):
    return [_vlan_out(session, v) for v in session.scalars(select(Vlan).order_by(func.lower(Vlan.site), Vlan.number))]


def _apply_vlan(session, v: Vlan, body: VlanIn) -> None:
    site = body.site.strip()
    other = session.scalar(select(Vlan).where(Vlan.number == body.number, func.lower(Vlan.site) == site.lower()))
    if other is not None and other.id != v.id:
        where = f" at {other.site}" if other.site else ""
        raise HTTPException(status.HTTP_409_CONFLICT, f"VLAN {body.number}{where} is already there ({other.name or 'no name'})")
    v.number, v.name, v.site, v.notes = body.number, body.name.strip(), site, body.notes


@router.post("/vlans", response_model=VlanOut, status_code=status.HTTP_201_CREATED)
def create_vlan(body: VlanIn, user: Agent, session: SessionDep):
    v = Vlan()
    _apply_vlan(session, v, body)
    session.add(v)
    session.commit()
    log.info("VLAN %s %r added by %s", v.number, v.name, user.display_name, extra={"source": "settings"})
    return _vlan_out(session, v)


@router.put("/vlans/{vlan_id}", response_model=VlanOut)
def update_vlan(vlan_id: int, body: VlanIn, _: Agent, session: SessionDep):
    v = _get(session, Vlan, vlan_id, "VLAN")
    _apply_vlan(session, v, body)
    session.commit()
    return _vlan_out(session, v)


@router.delete("/vlans/{vlan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vlan(vlan_id: int, admin: IpamDeleter, session: SessionDep):
    """Its subnets stay, without a VLAN."""
    v = _get(session, Vlan, vlan_id, "VLAN")
    session.delete(v)
    session.commit()
    log.info("VLAN %s %r deleted by %s", v.number, v.name, admin.display_name, extra={"source": "settings"})


# Subnets


def _subnet_out(session, s: Subnet, model=SubnetOut, **extra):
    recs = ipam.records(session, s)
    out = model.model_validate({**SubnetOut.model_validate(s).model_dump(), **extra})
    out.usage = SubnetUsage(**ipam.usage(s, recs))
    for kind, attr in ((ScanKind.network, "last_scan"), (ScanKind.dhcp_dns, "last_dhcp_dns"), (ScanKind.snmp, "last_snmp")):
        last = session.scalar(select(IpScan).where(IpScan.subnet_id == s.id, IpScan.kind == kind).order_by(IpScan.id.desc()).limit(1))
        setattr(out, attr, _scan_out(last) if last is not None else None)
    out.dhcp_dns = ipamscan.dhcp_dns_configured()
    out.snmp = netdisco.configured()
    out.open_findings = session.scalar(select(func.count()).select_from(IpFinding).where(IpFinding.subnet_id == s.id, IpFinding.status == FindingStatus.open)) or 0
    return out, recs


def _scan_out(scan: IpScan) -> IpScanOut:
    out = IpScanOut.model_validate(scan)
    if scan.run is not None:
        out.status = scan.run.status
        out.runner = scan.run.runner.name if scan.run.runner else None
    return out


@router.get("/subnets", response_model=list[SubnetOut])
def list_subnets(_: Agent, session: SessionDep, q: str = "", vlan_id: int | None = None):
    """Search by network, name, site or VLAN; an IP address finds the subnets containing it."""
    stmt = select(Subnet).outerjoin(Vlan, Vlan.id == Subnet.vlan_id).order_by(Subnet.network)
    if vlan_id is not None:
        stmt = stmt.where(Subnet.vlan_id == vlan_id)
    for word in q.split():
        like = f"%{word}%"
        conds = [func.text(Subnet.network).ilike(like), Subnet.name.ilike(like), Subnet.site.ilike(like), Vlan.name.ilike(like)]
        if word.isdigit():
            conds.append(Vlan.number == int(word))
        try:
            conds.append(Subnet.network.op(">>=")(func.inet(str(ipam.parse_address(word)))))
        except ipam.IpamError:
            pass
        stmt = stmt.where(or_(*conds))
    return [_subnet_out(session, s)[0] for s in session.scalars(stmt)]


@router.get("/subnets/{subnet_id}", response_model=SubnetDetailOut)
def get_subnet(subnet_id: int, _: Agent, session: SessionDep):
    s = _get(session, Subnet, subnet_id, "Subnet")
    children = session.scalars(select(Subnet).where(Subnet.network.op("<<")(s.network)).order_by(Subnet.network)).all()
    out, recs = _subnet_out(session, s, SubnetDetailOut, addresses=[], cells=None, children=[SubnetRef.model_validate(c) for c in children])
    out.addresses = _addresses_out(session, recs, s)
    cells = ipam.cells(s, recs)
    out.cells = [IpCell(**c) for c in cells] if cells is not None else None
    return out


@router.get("/subnets/{subnet_id}/next-free")
def next_free(subnet_id: int, _: Agent, session: SessionDep) -> dict:
    """The lowest address that isn't documented, the gateway, or in the DHCP pool. Scans may still find it in use."""
    s = _get(session, Subnet, subnet_id, "Subnet")
    return {"address": ipam.next_free(s, ipam.records(session, s))}


def _apply_subnet(session, s: Subnet, body: SubnetIn) -> None:
    try:
        net = ipam.parse_network(body.network)
        points = {}
        for field in ("gateway", "dhcp_start", "dhcp_end"):
            value = (getattr(body, field) or "").strip()
            points[field] = ipam.parse_address(value) if value else None
            if points[field] is not None and points[field] not in net:
                raise ipam.IpamError(f"{points[field]} isn't in {net}")
    except ipam.IpamError as e:
        raise _bad(str(e)) from None
    if (points["dhcp_start"] is None) != (points["dhcp_end"] is None):
        raise _bad("Give both ends of the DHCP pool, or neither")
    if points["dhcp_start"] is not None and points["dhcp_start"] > points["dhcp_end"]:
        raise _bad("The DHCP pool starts after it ends")
    other = session.scalar(select(Subnet).where(Subnet.network == net))
    if other is not None and other.id != s.id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{net} is already there ({other.name or 'no name'})")
    if body.vlan_id is not None and session.get(Vlan, body.vlan_id) is None:
        raise _bad("No such VLAN")
    if body.scan_runner_id is not None and session.get(Runner, body.scan_runner_id) is None:
        raise _bad("No such runner")
    s.scan_every_hours, s.scan_runner_id = body.scan_every_hours, body.scan_runner_id
    s.network, s.vlan_id = net, body.vlan_id
    s.gateway, s.dhcp_start, s.dhcp_end = points["gateway"], points["dhcp_start"], points["dhcp_end"]
    s.name, s.dns_servers, s.site, s.notes = body.name.strip(), body.dns_servers.strip(), body.site.strip(), body.notes


@router.post("/subnets", response_model=SubnetOut, status_code=status.HTTP_201_CREATED)
def create_subnet(body: SubnetIn, user: Agent, session: SessionDep):
    s = Subnet()
    _apply_subnet(session, s, body)
    session.add(s)
    session.commit()
    log.info("Subnet %s %r added by %s", s.network, s.name, user.display_name, extra={"source": "settings"})
    return _subnet_out(session, s)[0]


@router.put("/subnets/{subnet_id}", response_model=SubnetOut)
def update_subnet(subnet_id: int, body: SubnetIn, _: Agent, session: SessionDep):
    s = _get(session, Subnet, subnet_id, "Subnet")
    _apply_subnet(session, s, body)
    session.commit()
    session.refresh(s)
    return _subnet_out(session, s)[0]


@router.delete("/subnets/{subnet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subnet(subnet_id: int, admin: IpamDeleter, session: SessionDep):
    """The addresses documented in it stay."""
    s = _get(session, Subnet, subnet_id, "Subnet")
    session.delete(s)
    session.commit()
    log.info("Subnet %s %r deleted by %s", s.network, s.name, admin.display_name, extra={"source": "settings"})


# Scans and findings


@router.post("/subnets/{subnet_id}/scan", response_model=IpScanOut, status_code=status.HTTP_201_CREATED)
def scan_subnet(subnet_id: int, user: Agent, session: SessionDep, kind: ScanKind = ScanKind.network):
    """Queue a scan. network: on the subnet's runner, ping sweep, reverse DNS and the runner's ARP
    table. dhcp_dns: on the Windows runner, the DHCP server's leases and reservations and the DNS
    server's A records (Settings > IP addresses). snmp: on the subnet's runner, network gear's
    identity, LLDP neighbours and MAC tables (see /api/network)."""
    s = _get(session, Subnet, subnet_id, "Subnet")
    try:
        scan = ipamscan.request(session, s, user, kind=kind)
    except ipamscan.ScanError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    session.commit()
    what = ipamscan.WHAT[kind].split(" ", 1)[1]
    log.info("%s of %s requested by %s (run #%s)", what, s.network, user.display_name, scan.run_id, extra={"source": "ipam"})
    return _scan_out(scan)


@router.get("/runners", response_model=list[ScanRunnerOut])
def scan_runners(_: Agent, session: SessionDep):
    """Runners that can scan (they take Python), to choose one per subnet."""
    return [r for r in session.scalars(select(Runner).where(Runner.is_active).order_by(Runner.name)) if "python" in (r.languages or [])]


@router.get("/subnets/{subnet_id}/scans", response_model=list[IpScanOut])
def subnet_scans(subnet_id: int, _: Agent, session: SessionDep, limit: int = 20):
    _get(session, Subnet, subnet_id, "Subnet")
    stmt = select(IpScan).where(IpScan.subnet_id == subnet_id).order_by(IpScan.id.desc()).limit(min(max(limit, 1), 200))
    return [_scan_out(x) for x in session.scalars(stmt)]


@router.get("/findings", response_model=list[IpFindingOut])
def list_findings(_: Agent, session: SessionDep, subnet_id: int | None = None, status_: FindingStatus | None = Query(FindingStatus.open, alias="status")):
    stmt = select(IpFinding).order_by(IpFinding.address, IpFinding.id)
    if subnet_id is not None:
        stmt = stmt.where(IpFinding.subnet_id == subnet_id)
    if status_ is not None:
        stmt = stmt.where(IpFinding.status == status_)
    return session.scalars(stmt.limit(2000)).all()


def _resolve(finding_id: int, user, session, apply: bool) -> IpFinding:
    f = _get(session, IpFinding, finding_id, "Finding")
    try:
        ipamscan.resolve(session, f, user, apply)
    except ipamscan.ScanError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    session.commit()
    return f


@router.post("/findings/{finding_id}/apply", response_model=IpFindingOut)
def apply_finding(finding_id: int, user: Agent, session: SessionDep):
    """Change the documentation as the finding suggests: add the new host, take the new host name
    or MAC, or free an address that's gone."""
    return _resolve(finding_id, user, session, True)


@router.post("/findings/{finding_id}/dismiss", response_model=IpFindingOut)
def dismiss_finding(finding_id: int, user: Agent, session: SessionDep):
    """Leave things as documented. The same difference isn't raised again."""
    return _resolve(finding_id, user, session, False)


# Addresses


def _address_out(session, r: IpAddress, subnet: Subnet | None = None, ports: dict | None = None) -> IpAddressOut:
    """`ports`: switch ports already looked up for a list (netdisco.ports_for); else looked up here."""
    out = IpAddressOut.model_validate(r)
    s = subnet if subnet is not None else ipam.subnet_of(session, r.address)
    out.subnet = SubnetRef.model_validate(s) if s is not None else None
    if r.mac:
        port = (ports if ports is not None else netdisco.ports_for(session, [r.mac])).get(r.mac)
        out.switch_port = SwitchPortOut(**port) if port else None
    return out


def _addresses_out(session, recs, subnet: Subnet | None = None) -> list[IpAddressOut]:
    recs = list(recs)
    ports = netdisco.ports_for(session, [r.mac for r in recs])
    return [_address_out(session, r, subnet, ports) for r in recs]


@router.get("/addresses", response_model=list[IpAddressOut])
def list_addresses(_: Agent, session: SessionDep, q: str = "", kind: IpKind | None = None, device_id: int | None = None, system_id: int | None = None, limit: int = 500):
    """Search by address (or its start: 10.1.20.), host name, MAC (any format), notes, device or system."""
    stmt = select(IpAddress).outerjoin(Device, Device.id == IpAddress.device_id).outerjoin(System, System.id == IpAddress.system_id).order_by(IpAddress.address)
    if kind is not None:
        stmt = stmt.where(IpAddress.kind == kind)
    if device_id is not None:
        stmt = stmt.where(IpAddress.device_id == device_id)
    if system_id is not None:
        stmt = stmt.where(IpAddress.system_id == system_id)
    for word in q.split():
        like = f"%{word}%"
        conds = [func.host(IpAddress.address).like(f"{word}%"), IpAddress.hostname.ilike(like), IpAddress.notes.ilike(like), Device.name.ilike(like), System.name.ilike(like), IpAddress.mac.ilike(like)]
        mac_digits = "".join(c for c in word.lower() if c in "0123456789abcdef")
        if len(mac_digits) >= 4 and len(mac_digits) == len(word.replace(":", "").replace("-", "").replace(".", "")):
            conds.append(func.replace(IpAddress.mac, ":", "").like(f"%{mac_digits}%"))
        try:
            conds.append(IpAddress.address.op("<<=")(func.inet(str(ipam.parse_network(word)))) if "/" in word else IpAddress.address == func.inet(str(ipam.parse_address(word))))
        except ipam.IpamError:
            pass
        stmt = stmt.where(or_(*conds))
    return _addresses_out(session, session.scalars(stmt.limit(min(max(limit, 1), 2000))))


@router.get("/devices/{device_id}/addresses", response_model=list[DeviceIpOut])
def device_addresses(device_id: int, _: Agent, session: SessionDep):
    """The device's addresses, plus unlinked ones whose host name matches it (pc-0042 or pc-0042.corp.example)."""
    d = _get(session, Device, device_id, "Device")
    names = {n.lower() for n in (d.name, d.dns_hostname) if n}
    short = d.name.lower()
    match = or_(func.lower(IpAddress.hostname).in_(names), func.split_part(func.lower(IpAddress.hostname), ".", 1) == short)
    stmt = select(IpAddress).where(or_(IpAddress.device_id == d.id, IpAddress.device_id.is_(None) & (IpAddress.hostname != "") & match)).order_by(IpAddress.address)
    recs = list(session.scalars(stmt))
    return [DeviceIpOut(address=a, linked=r.device_id == d.id) for r, a in zip(recs, _addresses_out(session, recs))]


@router.post("/addresses/{address_id}/link-device/{device_id}", response_model=IpAddressOut)
def link_device(address_id: int, device_id: int, _: Agent, session: SessionDep):
    r = _get(session, IpAddress, address_id, "Address")
    r.device_id = _get(session, Device, device_id, "Device").id
    session.commit()
    session.refresh(r)
    return _address_out(session, r)


@router.get("/addresses/{address_id}", response_model=IpAddressOut)
def get_address(address_id: int, _: Agent, session: SessionDep):
    return _address_out(session, _get(session, IpAddress, address_id, "Address"))


def _apply_address(session, r: IpAddress, body: IpAddressIn) -> None:
    try:
        addr = ipam.parse_address(body.address)
        mac = ipam.normalize_mac(body.mac)
    except ipam.IpamError as e:
        raise _bad(str(e)) from None
    other = session.scalar(select(IpAddress).where(IpAddress.address == func.inet(str(addr))))
    if other is not None and other.id != r.id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{addr} is already documented ({other.hostname or other.kind})")
    if body.device_id is not None and session.get(Device, body.device_id) is None:
        raise _bad("No such device")
    if body.system_id is not None and session.get(System, body.system_id) is None:
        raise _bad("No such system")
    r.address, r.mac, r.kind = addr, mac, IpKind(body.kind)
    r.hostname, r.notes = body.hostname.strip(), body.notes
    r.device_id, r.system_id = body.device_id, body.system_id


@router.post("/addresses", response_model=IpAddressOut, status_code=status.HTTP_201_CREATED)
def create_address(body: IpAddressIn, _: Agent, session: SessionDep):
    r = IpAddress()
    _apply_address(session, r, body)
    session.add(r)
    session.commit()
    return _address_out(session, r)


@router.put("/addresses/{address_id}", response_model=IpAddressOut)
def update_address(address_id: int, body: IpAddressIn, _: Agent, session: SessionDep):
    r = _get(session, IpAddress, address_id, "Address")
    _apply_address(session, r, body)
    session.commit()
    session.refresh(r)
    return _address_out(session, r)


@router.delete("/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(address_id: int, _: Agent, session: SessionDep):
    """Frees the address."""
    session.delete(_get(session, IpAddress, address_id, "Address"))
    session.commit()
