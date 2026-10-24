# SPDX-License-Identifier: AGPL-3.0-or-later
"""Action1 (patching, read-only): machines' patch state, matched to devices; status and a connection test."""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app import action1
from app.auth import Admin, Agent, SessionDep
from app.config import settings
from app.models import Device, DevicePatch, WorkerJob
from app.schemas import Action1StatusOut, Action1TestOut, DevicePatchOut

router = APIRouter(prefix="/api/action1", tags=["action1"])
log = logging.getLogger(__name__)


@router.get("/status", response_model=Action1StatusOut)
def action1_status(_: Agent, session: SessionDep):
    count = lambda *where: session.scalar(select(func.count()).select_from(DevicePatch).where(DevicePatch.in_action1, *where)) or 0  # noqa: E731
    job = session.get(WorkerJob, "action1")
    return Action1StatusOut(
        enabled=action1.enabled(), machines=count(), matched=count(DevicePatch.device_id.is_not(None)),
        critical=count(DevicePatch.missing_critical > 0), reboot=count(DevicePatch.reboot_required),
        last_sync_at=job.last_finished_at if job else None, last_sync_ok=job.last_ok if job else None,
        last_sync_summary=job.last_summary if job else None, last_sync_error=job.last_error if job else None,
    )


@router.get("/machines", response_model=list[DevicePatchOut])
def machines(_: Agent, session: SessionDep, problem: str | None = None):
    """Machines Action1 knows. problem: critical (critical updates missing), reboot (pending), unmatched (no device here)."""
    stmt = select(DevicePatch).where(DevicePatch.in_action1).order_by(DevicePatch.missing_critical.desc(), func.lower(DevicePatch.name))
    if problem == "critical":
        stmt = stmt.where(DevicePatch.missing_critical > 0)
    elif problem == "reboot":
        stmt = stmt.where(DevicePatch.reboot_required)
    elif problem == "unmatched":
        stmt = stmt.where(DevicePatch.device_id.is_(None))
    elif problem is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "problem is critical, reboot or unmatched")
    return session.scalars(stmt.limit(5000)).all()


@router.get("/devices/{device_id}", response_model=DevicePatchOut | None)
def device_patch(device_id: int, _: Agent, session: SessionDep):
    """The device's patch state from Action1, or null if Action1 doesn't know it (or isn't set up)."""
    if session.get(Device, device_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    if not settings.action1_enabled:
        return None
    return session.scalar(select(DevicePatch).where(DevicePatch.device_id == device_id))


@router.post("/test", response_model=Action1TestOut)
def test_connection(_: Admin):
    """Sign in with the saved credentials and list the organizations they can read."""
    try:
        orgs = action1.from_settings().organizations()
    except action1.Action1Error as exc:
        return Action1TestOut(ok=False, error=str(exc))
    visible = {o["id"] for o in orgs}
    return Action1TestOut(ok=True, organizations=orgs, missing_orgs=[o for o in action1.org_ids() if o not in visible])
