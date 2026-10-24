# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit, reviewed single-device software deployments through Action1."""
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import action1, software_deployment as deploy
from app.api.tickets import staff_ticket
from app.auth import SessionDep
from app.models import Device, SoftwareDeployment, TicketDevice, UserRole
from app.permissions import require

router = APIRouter(prefix="/api/action1", tags=["action1"])
Deployer = require("action1_deploy")


class SoftwareVersionOut(BaseModel):
    id: str
    version: str


class SoftwarePackageOut(BaseModel):
    id: str
    name: str
    vendor: str
    platform: str
    versions: list[SoftwareVersionOut]


class SoftwareCatalogOut(BaseModel):
    items: list[SoftwarePackageOut]
    more: bool
    offset: int


class DeploymentPreviewIn(BaseModel):
    device_id: int = Field(gt=0)
    ticket_id: int | None = Field(default=None, gt=0)
    package_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)


class InstalledSoftwareMatchOut(BaseModel):
    name: str
    version: str


class DeploymentDetailsOut(BaseModel):
    organization_id: str
    organization: str
    endpoint_id: str
    endpoint_name: str
    device_name: str
    platform: str
    connection: str
    package_id: str
    package_name: str
    vendor: str
    version_id: str
    version: str
    installed: list[InstalledSoftwareMatchOut]


class DeploymentOut(BaseModel):
    id: str
    device_id: int | None
    ticket_id: int | None
    state: str
    message: str
    created_at: datetime
    instance_id: str | None
    details: DeploymentDetailsOut


def output(row):
    return {"id": row.id, "device_id": row.device_id, "ticket_id": row.ticket_id, "state": row.state,
            "message": row.message, "created_at": row.created_at, "instance_id": row.instance_id,
            "details": {k: v for k, v in row.snapshot.items() if not k.endswith("_digest")}}


def access(session, user, device_id, ticket_id, *, linked=False):
    if not session.get(Device, device_id):
        raise HTTPException(404, "Device not found")
    if ticket_id:
        ticket = staff_ticket(session, ticket_id, user)
        if linked and ticket.computer_id != device_id and not session.scalar(select(TicketDevice).where(
                TicketDevice.ticket_id == ticket_id, TicketDevice.device_id == device_id)):
            raise HTTPException(409, "Choose a computer currently linked to this ticket")


def record(session, user, id):
    row = session.get(SoftwareDeployment, str(id))
    if row is None or (row.actor_id != user.id and user.role != UserRole.admin):
        raise HTTPException(404, "Deployment not found")
    if row.ticket_id:
        staff_ticket(session, row.ticket_id, user)
    return row


@contextmanager
def provider():
    api = None
    try:
        api = deploy.client()
        yield api
    except action1.Action1Error as exc:
        raise HTTPException(409, str(exc)) from None
    except (ValueError, KeyError, TypeError):
        raise HTTPException(502, "Action1 returned an unexpected response; nothing will be automatically retried") from None
    finally:
        if api:
            api._http.close()


@router.get("/devices/{device_id}/software", response_model=SoftwareCatalogOut)
def catalog(device_id: int, user: Deployer, session: SessionDep,
            q: str = Query(min_length=2, max_length=100), offset: int = Query(default=0, ge=0, le=2000),
            ticket_id: int | None = None):
    access(session, user, device_id, ticket_id, linked=True)
    with provider() as api:
        t = deploy.live_target(session, device_id, api)
        page = api.get(f"/software-repository/{deploy.segment(t['organization_id'])}",
                       {"filter": q.strip(), "platform": t["platform"], "fields": "*", "from": offset, "limit": 50})
    items = []
    for p in page.get("items", []):
        if p.get("platform") != t["platform"]:
            continue
        versions = [{"id": v["id"], "version": v["version"]} for v in p.get("versions", [])
                    if v.get("id") and v.get("version") and v.get("status", "Published") == "Published"
                    and str(v["version"]).lower() != "latest"]
        if versions and p.get("id"):
            items.append({"id": p["id"], "name": p.get("name") or p["id"], "vendor": p.get("vendor") or "",
                          "platform": t["platform"], "versions": versions})
    return {"items": items, "offset": offset, "more": bool(page.get("next_page")) and offset < 2000}


@router.post("/deployments/preview", response_model=DeploymentOut)
def preview(body: DeploymentPreviewIn, user: Deployer, session: SessionDep):
    access(session, user, body.device_id, body.ticket_id, linked=True)
    with provider() as api:
        row = deploy.preview(session, user, body.device_id, body.ticket_id, body.package_id, body.version_id, api)
    return output(row)


@router.post("/deployments/{id}/submit", response_model=DeploymentOut)
def submit(id: UUID, user: Deployer, session: SessionDep):
    row = record(session, user, id)
    if row.actor_id != user.id:
        raise HTTPException(403, "Only the technician who reviewed this deployment may submit it")
    access(session, user, row.device_id, row.ticket_id, linked=True)
    with provider() as api:
        row = deploy.submit(session, row, user, api)
    return output(row)


@router.get("/deployments/{id}", response_model=DeploymentOut)
def status(id: UUID, user: Deployer, session: SessionDep):
    return output(record(session, user, id))


@router.post("/deployments/{id}/refresh", response_model=DeploymentOut)
def refresh(id: UUID, user: Deployer, session: SessionDep):
    row = record(session, user, id)
    with provider() as api:
        # Serialize API/worker result updates and final ticket notes.
        session.refresh(row, with_for_update=True)
        deploy.refresh(session, row, api)
    return output(row)


@router.get("/devices/{device_id}/deployments", response_model=list[DeploymentOut])
def history(device_id: int, user: Deployer, session: SessionDep, ticket_id: int | None = None):
    access(session, user, device_id, ticket_id)
    query = select(SoftwareDeployment).where(SoftwareDeployment.device_id == device_id,
                                           SoftwareDeployment.ticket_id == ticket_id,
                                           SoftwareDeployment.state != "review")
    if user.role != UserRole.admin:
        query = query.where(SoftwareDeployment.actor_id == user.id)
    return [output(row) for row in session.scalars(query.order_by(SoftwareDeployment.created_at.desc()).limit(20))]
