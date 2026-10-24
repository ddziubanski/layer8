# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nutanix in Layer 8: admins set it up and test it; technicians read the cached VMs and link them
to tickets; people with the permissions restart, power on or power-cycle a ticket's VMs (each noted
on the ticket, and followed until Prism Central says how it ended)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, or_, select

from app import nutanix, permissions, services, sla
from app.api.tickets import staff_ticket
from app.auth import Admin, Agent, SessionDep
from app.config import settings
from app.models import EventType, NutanixAction, NutanixVm, TicketDevice, TicketNutanixVm, TicketSource, WorkerJob

router = APIRouter(prefix="/api/nutanix", tags=["nutanix"])


class NutanixStatusOut(BaseModel):
    enabled: bool
    configured: bool
    power_enabled: bool
    vms: int
    running: int
    last_sync_at: datetime | None
    last_sync_ok: bool | None
    last_sync_error: str | None
    last_sync_summary: str | None


class NutanixTestOut(BaseModel):
    clusters: list[str]
    vms: int


class NutanixVmOut(BaseModel):
    ext_id: str
    name: str
    power_state: str
    cluster_name: str
    host_name: str
    vcpus: int
    memory_mb: int
    ips: list[str]
    macs: list[str]
    guest_tools: bool
    guest_tools_reachable: bool
    device_id: int | None
    device_name: str | None
    present: bool
    stale: bool
    observed_at: datetime


class NutanixActionOut(BaseModel):
    id: int
    vm_id: str
    vm_name: str
    action: str
    status: str
    detail: str
    requested_by: str | None
    requested_at: datetime
    finished_at: datetime | None


class TicketVmOut(NutanixVmOut):
    # Linked to the ticket itself; otherwise it's here through the ticket's computer or a linked device.
    linked: bool


class TicketVmsOut(BaseModel):
    vms: list[TicketVmOut]
    actions: list[NutanixActionOut]
    power_enabled: bool
    may_power: bool
    may_power_cycle: bool


class LinkIn(BaseModel):
    ext_id: str = Field(min_length=1, max_length=64)


class PowerIn(BaseModel):
    action: Literal["restart", "power_on", "power_cycle"]


def _visible():
    if not nutanix.on():
        raise HTTPException(404, "Nutanix isn't on (Settings > Nutanix)")


def _out(vm: NutanixVm, **extra) -> dict:
    stale = not vm.present or sla.utcnow() - vm.observed_at > nutanix.stale_after()
    return {**{f: getattr(vm, f) for f in NutanixVmOut.model_fields if hasattr(vm, f)},
            "device_name": vm.device.name if vm.device else None, "stale": stale, **extra}


def _action_out(a: NutanixAction) -> dict:
    return {"id": a.id, "vm_id": a.vm_id, "vm_name": a.vm.name if a.vm else a.vm_id, "action": a.action, "status": a.status,
            "detail": a.detail, "requested_by": a.requested_by.display_name if a.requested_by else None,
            "requested_at": a.requested_at, "finished_at": a.finished_at}


@router.get("/status", response_model=NutanixStatusOut)
def status(_: Admin, session: SessionDep):
    job = session.get(WorkerJob, "nutanix")
    return dict(
        enabled=settings.nutanix_enabled, configured=nutanix.on(), power_enabled=settings.nutanix_power_enabled, **nutanix.counts(session),
        last_sync_at=job.last_finished_at if job else None, last_sync_ok=job.last_ok if job else None,
        last_sync_error=job.last_error if job else None, last_sync_summary=job.last_summary if job else None,
    )


@router.post("/test", response_model=NutanixTestOut)
def test(_: Admin):
    """Signs in and lists the clusters and how many VMs the account can see. Stores nothing."""
    client = None
    try:
        client = nutanix.from_settings()
        clusters = sorted(c["name"] for c in client.paged(nutanix.CLUSTERS) if isinstance(c.get("name"), str))
        return {"clusters": clusters, "vms": len(client.paged(nutanix.VMS))}
    except nutanix.NutanixError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        if client:
            client.close()


@router.get("/vms", response_model=list[NutanixVmOut])
def list_vms(_: Agent, session: SessionDep, q: str = "", state: str = "", limit: int = 500):
    _visible()
    stmt = select(NutanixVm).where(NutanixVm.present)
    if state == "on":
        stmt = stmt.where(NutanixVm.power_state == "on")
    elif state == "off":
        stmt = stmt.where(NutanixVm.power_state != "on")
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(NutanixVm.name.ilike(like), NutanixVm.cluster_name.ilike(like), NutanixVm.host_name.ilike(like),
                              cast(NutanixVm.ips, String).ilike(like)))
    rows = session.scalars(stmt.order_by(NutanixVm.power_state != "on", NutanixVm.name).limit(max(1, min(limit, 2000))))
    return [_out(v) for v in rows]


@router.get("/devices/{device_id}/vms", response_model=list[NutanixVmOut])
def device_vms(device_id: int, _: Agent, session: SessionDep):
    if not nutanix.on():
        return []
    return [_out(v) for v in session.scalars(select(NutanixVm).where(NutanixVm.device_id == device_id).order_by(NutanixVm.name))]


def _ticket_vms(session, ticket) -> dict[str, tuple[NutanixVm, bool]]:
    """The ticket's VMs: linked ones, then those matched to its computer or linked devices."""
    out = {link.vm_id: (link.vm, True) for link in session.scalars(select(TicketNutanixVm).where(TicketNutanixVm.ticket_id == ticket.id))}
    device_ids = set(session.scalars(select(TicketDevice.device_id).where(TicketDevice.ticket_id == ticket.id)))
    if ticket.computer_id:
        device_ids.add(ticket.computer_id)
    if device_ids:
        for vm in session.scalars(select(NutanixVm).where(NutanixVm.device_id.in_(device_ids))):
            out.setdefault(vm.ext_id, (vm, False))
    return out


@router.get("/tickets/{ticket_id}/vms", response_model=TicketVmsOut)
def ticket_vms(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    _visible()
    vms = sorted(_ticket_vms(session, ticket).values(), key=lambda pair: (not pair[1], pair[0].name.lower()))
    actions = session.scalars(select(NutanixAction).where(NutanixAction.ticket_id == ticket.id).order_by(NutanixAction.id.desc()).limit(20))
    return {"vms": [_out(vm, linked=linked) for vm, linked in vms], "actions": [_action_out(a) for a in actions],
            "power_enabled": settings.nutanix_power_enabled,
            "may_power": permissions.allowed(session, user, "nutanix_power"),
            "may_power_cycle": permissions.allowed(session, user, "nutanix_power_cycle")}


@router.post("/tickets/{ticket_id}/vms", status_code=204)
def link_vm(ticket_id: int, body: LinkIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    _visible()
    vm = session.get(NutanixVm, body.ext_id)
    if vm is None or not vm.present:
        raise HTTPException(422, "Pick one of the VMs Layer 8 has read from Prism Central")
    if session.get(TicketNutanixVm, (ticket.id, vm.ext_id)) is None:
        session.add(TicketNutanixVm(ticket_id=ticket.id, vm_id=vm.ext_id, linked_by_id=user.id))
        services._event(session, ticket, EventType.updated, user, nutanix_vm_linked=vm.name)
    session.commit()


@router.delete("/tickets/{ticket_id}/vms/{ext_id}", status_code=204)
def unlink_vm(ticket_id: int, ext_id: str, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    link = session.get(TicketNutanixVm, (ticket.id, ext_id))
    if link is not None:
        services._event(session, ticket, EventType.updated, user, nutanix_vm_unlinked=link.vm.name)
        session.delete(link)
    session.commit()


@router.post("/tickets/{ticket_id}/vms/{ext_id}/power", response_model=NutanixActionOut)
def power(ticket_id: int, ext_id: str, body: PowerIn, user: Agent, session: SessionDep):
    """Restart (gracefully), power on or power-cycle one of the ticket's VMs. Noted on the ticket;
    the worker follows Prism Central's task and notes how it ended."""
    ticket = staff_ticket(session, ticket_id, user)
    _visible()
    if not settings.nutanix_power_enabled:
        raise HTTPException(409, "Power actions are off (Settings > Nutanix)")
    prism_action, what, permission = nutanix.ACTIONS[body.action]
    if not permissions.allowed(session, user, permission):
        raise HTTPException(403, "You don't have the permission for this (Settings > Permissions)")
    pair = _ticket_vms(session, ticket).get(ext_id)
    if pair is None or not pair[0].present:
        raise HTTPException(404, "That VM isn't linked to this ticket")
    vm = pair[0]
    running = vm.power_state == "on"
    if body.action == "power_on" and running:
        raise HTTPException(409, f"{vm.name} is already running")
    if body.action != "power_on" and not running:
        raise HTTPException(409, f"{vm.name} isn't running (as last read); power it on instead")
    if session.scalar(select(NutanixAction.id).where(NutanixAction.vm_id == vm.ext_id, NutanixAction.status.in_(("submitted", "running"))).limit(1)):
        raise HTTPException(409, f"A power action on {vm.name} is still under way")
    if body.action == "restart" and vm.guest_tools_reachable:
        prism_action, what = "guest-reboot", "graceful restart (through Nutanix Guest Tools)"
    elif body.action == "restart":
        what = "graceful restart (ACPI)"
    client = None
    try:
        client = nutanix.from_settings()
        task = client.act(vm.ext_id, prism_action)
    except nutanix.NutanixError as exc:
        services.add_comment(session, ticket, user, body=f"Nutanix: asked for a {what} of VM {vm.name}; Prism Central refused: {exc}",
                             is_internal=True, source=TicketSource.agent, as_staff=True, email_requester=False)
        session.commit()
        raise HTTPException(502, str(exc)) from None
    finally:
        if client:
            client.close()
    action = NutanixAction(ticket_id=ticket.id, vm_id=vm.ext_id, action=body.action, task_id=task, requested_by_id=user.id)
    session.add(action)
    services.add_comment(session, ticket, user, body=f"Nutanix: asked for a {what} of VM {vm.name} (on {vm.host_name or 'its host'}). Layer 8 notes here how it ends.",
                         is_internal=True, source=TicketSource.agent, as_staff=True, email_requester=False)
    services._event(session, ticket, EventType.updated, user, nutanix_power=body.action, vm=vm.name)
    session.commit()
    session.refresh(action)
    return _action_out(action)
