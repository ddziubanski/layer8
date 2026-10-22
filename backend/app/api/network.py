"""Network gear found by SNMP discovery (IP addresses > a subnet > Discover (SNMP)): identity,
LLDP neighbours and switch MAC tables. Discovery keeps it up to date; technicians link gear to
an asset, and those who may delete subnets may forget gear that's gone.
"""

import logging
import re

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select

from app import ipam, netdisco, permissions
from app.auth import Agent, SessionDep
from app.models import Device, IpAddress, NetworkDevice, NetworkNeighbor, SwitchPortMac
from app.schemas import (
    NetworkDeviceDetailOut,
    NetworkDeviceLinkIn,
    NetworkDeviceOut,
    NetworkNeighborOut,
    SwitchPortGroupOut,
    SwitchPortMacOut,
    SwitchPortOut,
    SystemDeviceRef,
)

router = APIRouter(prefix="/api/network", tags=["network"])
Deleter = permissions.require("ipam_delete")
log = logging.getLogger(__name__)
# A port with more MACs than this is taken as an uplink (folded up in the port list).
UPLINK_MACS = 5


def _get(session, id_: int) -> NetworkDevice:
    nd = session.get(NetworkDevice, id_)
    if nd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Network device not found")
    return nd


def _out(session, nd: NetworkDevice, model=NetworkDeviceOut, **extra):
    out = model.model_validate({**NetworkDeviceOut.model_validate(nd).model_dump(), **extra})
    out.neighbor_count = session.scalar(select(func.count()).select_from(NetworkNeighbor).where(NetworkNeighbor.network_device_id == nd.id)) or 0
    out.mac_count = session.scalar(select(func.count()).select_from(SwitchPortMac).where(SwitchPortMac.network_device_id == nd.id)) or 0
    return out


@router.get("/devices", response_model=list[NetworkDeviceOut])
def list_devices(_: Agent, session: SessionDep, q: str = "", subnet_id: int | None = None):
    """Discovered gear; q matches name, address, model, serial, vendor or location."""
    stmt = select(NetworkDevice).order_by(NetworkDevice.sys_name, NetworkDevice.address)
    if subnet_id is not None:
        stmt = stmt.where(NetworkDevice.subnet_id == subnet_id)
    for word in q.split():
        like = f"%{word}%"
        stmt = stmt.where(or_(
            NetworkDevice.sys_name.ilike(like), func.host(NetworkDevice.address).like(f"{word}%"), NetworkDevice.model.ilike(like),
            NetworkDevice.serial.ilike(like), NetworkDevice.vendor.ilike(like), NetworkDevice.location.ilike(like),
        ))
    return [_out(session, nd) for nd in session.scalars(stmt.limit(1000))]


@router.get("/devices/{device_id}", response_model=NetworkDeviceDetailOut)
def get_device(device_id: int, _: Agent, session: SessionDep):
    """With its LLDP neighbours and, per port, the MACs seen there (and what's documented for them)."""
    nd = _get(session, device_id)
    known = {str(ipam.address(a)): i for i, a in session.execute(select(NetworkDevice.id, NetworkDevice.address))}
    by_name = {n.lower().split(".")[0]: i for i, n in session.execute(select(NetworkDevice.id, NetworkDevice.sys_name)) if n}
    neighbors = []
    for n in nd.neighbors:
        out = NetworkNeighborOut.model_validate(n)
        out.remote_id = (known.get(str(ipam.address(n.remote_address))) if n.remote_address else None) or by_name.get(n.remote_name.lower().split(".")[0])
        neighbors.append(out)
    uplinks = {n.local_port: n.remote_name or n.remote_chassis for n in nd.neighbors}
    to_gear = {n.local_port for n, out in zip(nd.neighbors, neighbors) if out.remote_id}
    rows = list(session.scalars(select(SwitchPortMac).where(SwitchPortMac.network_device_id == nd.id).order_by(SwitchPortMac.port, SwitchPortMac.mac)))
    docs: dict[str, IpAddress] = {}
    for r in session.scalars(select(IpAddress).where(IpAddress.mac.in_({r.mac for r in rows} or {""}))):
        docs.setdefault(r.mac, r)
    ports: dict[str, SwitchPortGroupOut] = {}
    for r in rows:
        g = ports.setdefault(r.port, SwitchPortGroupOut(port=r.port, neighbor=uplinks.get(r.port, ""), macs=[]))
        d = docs.get(r.mac)
        g.macs.append(SwitchPortMacOut(
            mac=r.mac, vlan=r.vlan, address=str(ipam.address(d.address)) if d else None, hostname=d.hostname if d else "",
            device=SystemDeviceRef.model_validate(d.device) if d and d.device else None,
        ))
    # Ports in their natural order (Gi1/0/2 before Gi1/0/10), edge ports first.
    for g in ports.values():
        g.uplink = g.port in to_gear or len(g.macs) > UPLINK_MACS

    def order(g: SwitchPortGroupOut):
        return (g.uplink, [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", g.port)])

    return _out(session, nd, NetworkDeviceDetailOut, neighbors=neighbors, ports=sorted(ports.values(), key=order))


@router.patch("/devices/{device_id}", response_model=NetworkDeviceOut)
def link_device(device_id: int, body: NetworkDeviceLinkIn, user: Agent, session: SessionDep):
    """Link the gear to an asset (Devices), or unlink it (null)."""
    nd = _get(session, device_id)
    if body.device_id is not None and session.get(Device, body.device_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such device")
    nd.device_id = body.device_id
    session.commit()
    session.refresh(nd)
    return _out(session, nd)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def forget_device(device_id: int, user: Deleter, session: SessionDep):
    """Forget gear that's gone (a later discovery that finds it adds it again)."""
    nd = _get(session, device_id)
    log.info("Network device %s (%s) forgotten by %s", nd.sys_name, nd.address, user.display_name, extra={"source": "ipam"})
    session.delete(nd)
    session.commit()


@router.get("/where", response_model=SwitchPortOut | None)
def where_is(mac: str, _: Agent, session: SessionDep):
    """Which switch port a MAC (any format) is on, from the last discoveries; null if not seen."""
    try:
        m = ipam.normalize_mac(mac)
    except ipam.IpamError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    port = netdisco.ports_for(session, [m]).get(m)
    return SwitchPortOut(**port) if port else None
