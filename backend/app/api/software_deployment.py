# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit, reviewed software deployments through Action1: one device (software_deployment.py), or
several machines / an Action1 group, bundles and a set time (software_batch.py)."""
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import action1, software_batch as batch, software_deployment as deploy
from app.api.tickets import staff_ticket
from app.auth import SessionDep
from app.models import Device, DevicePatch, SoftwareBundle, SoftwareDeployment, TicketDevice, UserRole
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


def record(session, user, id, *, kind=None):
    row = session.get(SoftwareDeployment, str(id))
    if row is None or (row.actor_id != user.id and user.role != UserRole.admin) or row.snapshot.get("kind") != kind:
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


# --- Several machines or an Action1 group, bundles, a set time


class Action1OrganizationOut(BaseModel):
    id: str
    name: str


class Action1GroupOut(BaseModel):
    id: str
    name: str
    description: str


class Action1MachineOut(BaseModel):
    device_id: int
    name: str
    os: str
    last_seen: datetime | None


class SoftwareBundlePackage(BaseModel):
    package_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)


class SoftwareBundleIn(BaseModel):
    organization_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    packages: list[SoftwareBundlePackage] = Field(min_length=1, max_length=batch.MAX_PACKAGES)


class SoftwareBundleOut(BaseModel):
    id: int
    organization_id: str
    name: str
    platform: str
    packages: list[dict]
    updated_at: datetime


class SoftwareBatchPreviewIn(BaseModel):
    organization_id: str = Field(min_length=1, max_length=200)
    device_ids: list[int] = Field(default_factory=list, max_length=batch.MAX_MACHINES)
    group_id: str | None = Field(default=None, max_length=200)
    packages: list[SoftwareBundlePackage] = Field(default_factory=list, max_length=batch.MAX_PACKAGES)
    bundle_id: int | None = None
    ticket_id: int | None = Field(default=None, gt=0)
    run_at: datetime | None = Field(default=None, description="Once at this time (with its time zone); empty: now")
    retry_minutes: str = Field(default="30", description="How long Action1 keeps trying offline machines: 30, 480 or 1440 minutes")


class SoftwareBatchOut(BaseModel):
    id: str
    ticket_id: int | None
    state: str
    message: str
    created_at: datetime
    submitted_at: datetime | None
    instance_id: str | None
    schedule_id: str | None
    organization: str
    platform: str
    targets: list[dict]
    members: list[dict]
    member_count: int
    packages: list[dict]
    run_at: str | None
    retry_minutes: str
    results: list[dict]


def batch_out(row):
    snap = row.snapshot
    return {"id": row.id, "ticket_id": row.ticket_id, "state": row.state, "message": row.message, "created_at": row.created_at,
            "submitted_at": row.submitted_at, "instance_id": row.instance_id, "schedule_id": row.schedule_id,
            "organization": snap["organization"], "platform": snap["platform"],
            "targets": [{k: v for k, v in t.items() if k != "platform"} for t in snap["targets"]],
            "members": snap.get("members", []), "member_count": snap.get("member_count", 0),
            "packages": [{k: v for k, v in p.items() if not k.endswith("_digest")} for p in snap["packages"]],
            "run_at": snap.get("run_at"), "retry_minutes": snap["retry_minutes"], "results": row.results or []}


def allowed(org):
    from app.config import settings

    if org not in settings.action1_deploy_org_ids:
        raise HTTPException(404, "That organization isn't allowed for deployment")


@router.get("/deploy/organizations", response_model=list[Action1OrganizationOut])
def deploy_organizations(user: Deployer):
    from app.config import settings

    with provider() as api:
        deploy.configuration()
        return [o for o in api.organizations() if o["id"] in settings.action1_deploy_org_ids]


@router.get("/deploy/organizations/{org}/groups", response_model=list[Action1GroupOut])
def deploy_groups(org: str, user: Deployer):
    allowed(org)
    with provider() as api:
        return batch.groups(api, org)


@router.get("/deploy/organizations/{org}/machines", response_model=list[Action1MachineOut])
def deploy_machines(org: str, user: Deployer, session: SessionDep):
    """Devices matched to Action1 endpoints in this organization (from the last Action1 read)."""
    allowed(org)
    rows = session.execute(select(Device, DevicePatch).join(DevicePatch, DevicePatch.device_id == Device.id)
                           .where(DevicePatch.in_action1, DevicePatch.organization_id == org, Device.is_active).order_by(Device.name)).all()
    return [{"device_id": d.id, "name": d.name, "os": p.os or d.os or "", "last_seen": p.last_seen_at} for d, p in rows]


@router.get("/deploy/organizations/{org}/software", response_model=SoftwareCatalogOut)
def deploy_software(org: str, user: Deployer, q: str = Query(min_length=2, max_length=100),
                    platform: str = Query(pattern="^(Windows|Mac)$"), offset: int = Query(default=0, ge=0, le=2000)):
    allowed(org)
    with provider() as api:
        deploy.configuration()
        page = api.get(f"/software-repository/{deploy.segment(org)}", {"filter": q.strip(), "platform": platform, "fields": "*", "from": offset, "limit": 50})
    items = []
    for p in page.get("items", []):
        versions = [{"id": v["id"], "version": v["version"]} for v in p.get("versions", [])
                    if v.get("id") and v.get("version") and v.get("status", "Published") == "Published" and str(v["version"]).lower() != "latest"]
        if p.get("platform") == platform and versions and p.get("id"):
            items.append({"id": p["id"], "name": p.get("name") or p["id"], "vendor": p.get("vendor") or "", "platform": platform, "versions": versions})
    return {"items": items, "offset": offset, "more": bool(page.get("next_page")) and offset < 2000}


@router.get("/deploy/bundles", response_model=list[SoftwareBundleOut])
def bundles(user: Deployer, session: SessionDep, organization_id: str | None = None):
    query = select(SoftwareBundle).order_by(SoftwareBundle.name)
    if organization_id:
        query = query.where(SoftwareBundle.organization_id == organization_id)
    return list(session.scalars(query))


def _admin(user):
    if user.role != UserRole.admin:
        raise HTTPException(403, "Only admins change software bundles")


@router.post("/deploy/bundles", response_model=SoftwareBundleOut)
def bundle_create(body: SoftwareBundleIn, user: Deployer, session: SessionDep):
    _admin(user)
    allowed(body.organization_id)
    with provider() as api:
        return batch.save_bundle(session, user, api, organization_id=body.organization_id, name=body.name,
                                 packages=[(p.package_id, p.version_id) for p in body.packages])


@router.put("/deploy/bundles/{bundle_id}", response_model=SoftwareBundleOut)
def bundle_update(bundle_id: int, body: SoftwareBundleIn, user: Deployer, session: SessionDep):
    _admin(user)
    allowed(body.organization_id)
    with provider() as api:
        return batch.save_bundle(session, user, api, organization_id=body.organization_id, name=body.name,
                                 packages=[(p.package_id, p.version_id) for p in body.packages], bundle_id=bundle_id)


@router.delete("/deploy/bundles/{bundle_id}", status_code=204)
def bundle_delete(bundle_id: int, user: Deployer, session: SessionDep):
    _admin(user)
    bundle = session.get(SoftwareBundle, bundle_id)
    if bundle is None:
        raise HTTPException(404, "No such bundle")
    session.delete(bundle)
    session.commit()


@router.post("/batches/preview", response_model=SoftwareBatchOut)
def batch_preview(body: SoftwareBatchPreviewIn, user: Deployer, session: SessionDep):
    allowed(body.organization_id)
    if body.ticket_id:
        staff_ticket(session, body.ticket_id, user)
    with provider() as api:
        row = batch.preview(session, user, api, organization_id=body.organization_id, device_ids=body.device_ids, group_id=body.group_id or None,
                            packages=[(p.package_id, p.version_id) for p in body.packages], bundle_id=body.bundle_id,
                            ticket_id=body.ticket_id, run_at=body.run_at, retry_minutes=body.retry_minutes)
    return batch_out(row)


@router.post("/batches/{id}/submit", response_model=SoftwareBatchOut)
def batch_submit(id: UUID, user: Deployer, session: SessionDep):
    row = record(session, user, id, kind="batch")
    if row.actor_id != user.id:
        raise HTTPException(403, "Only the technician who reviewed this deployment may submit it")
    if row.ticket_id:
        staff_ticket(session, row.ticket_id, user)
    with provider() as api:
        row = batch.submit(session, row, user, api)
    return batch_out(row)


@router.get("/batches/{id}", response_model=SoftwareBatchOut)
def batch_status(id: UUID, user: Deployer, session: SessionDep):
    return batch_out(record(session, user, id, kind="batch"))


@router.post("/batches/{id}/refresh", response_model=SoftwareBatchOut)
def batch_refresh(id: UUID, user: Deployer, session: SessionDep):
    row = record(session, user, id, kind="batch")
    with provider() as api:
        session.refresh(row, with_for_update=True)
        batch.refresh(session, row, api)
    return batch_out(row)


@router.get("/batches", response_model=list[SoftwareBatchOut])
def batch_list(user: Deployer, session: SessionDep):
    query = select(SoftwareDeployment).where(SoftwareDeployment.snapshot["kind"].astext == "batch", SoftwareDeployment.state != "review")
    if user.role != UserRole.admin:
        query = query.where(SoftwareDeployment.actor_id == user.id)
    return [batch_out(r) for r in session.scalars(query.order_by(SoftwareDeployment.created_at.desc()).limit(30))]
