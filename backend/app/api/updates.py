# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin-only, explicit release checks and reviewed requests to a fixed system service."""
import json
import logging
import os
import platform
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app import VERSION, update_protocol as protocol
from app.auth import Admin, SessionDep
from app.config import settings
from app.models import AppSetting

router = APIRouter(prefix="/api/system/updates", tags=["updates"])
progress_router = APIRouter(tags=["updates"])
log = logging.getLogger(__name__)
STATE = Path("/var/lib/layer8-updater")
UNIT = Path("/etc/systemd/system/layer8-update.service")
CHECK_KEY = "github_release_check"


class ReleaseOut(BaseModel):
    version: str
    url: str
    notes: str
    published_at: str | None = None
    newer: bool
    verified: bool = False
    manifest_sha256: str | None = None
    package_size: int | None = None
    problem: str | None = None


class UpdateStatusOut(BaseModel):
    current_version: str
    repository: str
    enabled: bool
    installer_ready: bool
    backup_ready: bool
    checked_at: str | None = None
    release: ReleaseOut | None = None
    problem: str | None = None
    busy: bool
    operation: dict = Field(default_factory=dict)
    progress_url: str | None = None


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^v?(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: bool


def ready() -> bool:
    return platform.system() == "Linux" and UNIT.is_file() and (STATE / "inbox").is_dir() and protocol.KEYS.is_dir() and any(protocol.KEYS.glob("*.pem"))


def status(session) -> UpdateStatusOut:
    row = session.get(AppSetting, CHECK_KEY)
    cached = row.value if row and isinstance(row.value, dict) else {}
    operation = {}
    try:
        path = STATE / "status.json"
        if path.is_file() and path.stat().st_size <= 16384:
            operation = json.loads(path.read_text())
            if not isinstance(operation, dict):
                operation = {}
    except (OSError, ValueError):
        operation = {"phase": "unknown", "message": "Could not read updater status; inspect the server before retrying"}
    release = cached.get("release")
    if isinstance(release, dict):
        release = dict(release)
        try:
            release["newer"] = protocol.version(release["version"]) > protocol.version(VERSION)
        except (KeyError, protocol.UpdateError):
            release = None
    request_id = operation.get("request_id")
    pending = STATE / "inbox/request.json"
    try:
        if pending.is_file() and pending.stat().st_size <= 4096:
            queued = json.loads(pending.read_text())
            if queued.get("id") != request_id:
                request_id = queued.get("id")
                operation = {"phase": "queued", "message": "Waiting for the signed update service to start"}
    except (OSError, ValueError, AttributeError):
        pass
    progress_url = f"/api/update-progress/{request_id}.json" if isinstance(request_id, str) and re.fullmatch("[0-9a-f]{32}", request_id) else None
    return UpdateStatusOut(current_version=VERSION, repository=f"https://github.com/{protocol.REPOSITORY}", enabled=settings.updates_enabled,
                           installer_ready=ready(), backup_ready=bool(settings.backup_passphrase), checked_at=cached.get("checked_at"),
                           release=release, problem=cached.get("problem"),
                           busy=pending.exists() or (STATE / "maintenance").exists(), operation=operation, progress_url=progress_url)


@progress_router.get("/api/update-progress/{request_id}.json")
def get_update_progress(request_id: str):
    """Opaque capability URL returned only after admin review. Sanitized progress only.

    Nginx serves the identical root-owned file during API downtime. No directory
    listings, paths, usernames, counts, secrets or raw command output are exposed.
    """
    if not re.fullmatch("[0-9a-f]{32}", request_id):
        raise HTTPException(404)
    path = STATE / "progress" / (request_id + ".json")
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def check_release() -> ReleaseOut | None:
    latest = protocol.latest()
    if latest is None:
        return None
    release_tag = protocol.tag(latest["tag_name"])
    result = ReleaseOut(version=release_tag.removeprefix("v"), url=f"https://github.com/{protocol.REPOSITORY}/releases/tag/{release_tag}",
                        notes=str(latest.get("body") or "")[:20000], published_at=latest.get("published_at"), newer=protocol.version(release_tag) > protocol.version(VERSION))
    if result.newer:
        try:
            manifest, digest = protocol.signed_release(release_tag)
            protocol.compatible(manifest, VERSION)
            result.verified, result.manifest_sha256, result.package_size = True, digest, manifest["size"]
        except protocol.UpdateError as exc:
            result.problem = str(exc)
    return result


@router.get("", response_model=UpdateStatusOut)
def get_updates(_: Admin, session: SessionDep):
    # Page loads and polling never contact GitHub.
    return status(session)


@router.post("/check", response_model=UpdateStatusOut)
def check_updates(admin: Admin, session: SessionDep):
    if not settings.updates_enabled:
        raise HTTPException(409, "Enable GitHub release updates first")
    row = session.get(AppSetting, CHECK_KEY)
    if row and isinstance(row.value, dict):
        try:
            if (datetime.now(timezone.utc) - datetime.fromisoformat(row.value["checked_at"])).total_seconds() < 60:
                return status(session)
        except (KeyError, ValueError, TypeError):
            pass
    release, problem = None, None
    try:
        release = check_release()
    except protocol.UpdateError as exc:
        problem = str(exc)
    if row is None:
        row = AppSetting(key=CHECK_KEY)
        session.add(row)
    row.value = {"checked_at": datetime.now(timezone.utc).isoformat(), "release": release.model_dump() if release else None, "problem": problem}
    row.updated_by_id = admin.id
    session.commit()
    log.info("GitHub release check requested by user %s", admin.id, extra={"source": "updates"})
    return status(session)


@router.post("/apply", response_model=UpdateStatusOut, status_code=202)
def apply_update(body: ApplyIn, admin: Admin, session: SessionDep):
    if protocol.version(body.version) <= protocol.version(VERSION):
        raise HTTPException(409, "Only a newer stable release can be installed")
    if not body.confirm:
        raise HTTPException(422, "Confirm the backup and maintenance window before updating")
    if not settings.updates_enabled or not ready():
        raise HTTPException(409, "The appliance updater is not enabled or provisioned")
    if not settings.backup_passphrase:
        raise HTTPException(409, "Set a backup passphrase in Settings > Backup first")
    if status(session).busy:
        raise HTTPException(409, "An update is pending or recovery is required; inspect its status first")
    cached = session.get(AppSetting, CHECK_KEY)
    reviewed = cached.value.get("release", {}) if cached and isinstance(cached.value, dict) else {}
    if not reviewed or not reviewed.get("verified") or reviewed.get("manifest_sha256") != body.manifest_sha256 or protocol.tag(reviewed.get("version", "")) != protocol.tag(body.version):
        raise HTTPException(409, "Check and review this signed release before installing")
    # The service independently re-fetches and verifies this exact reviewed manifest.
    request = {"version": protocol.tag(body.version), "manifest_sha256": body.manifest_sha256, "requested_by": admin.id, "id": uuid.uuid4().hex}
    path = STATE / "inbox/request.json"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
        with os.fdopen(fd, "w") as output:
            json.dump(request, output)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        raise HTTPException(409, "An update has already been requested") from None
    except OSError:
        raise HTTPException(503, "Could not queue the update; check updater provisioning") from None
    try:
        done = subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "start", "--no-block", "layer8-update.service"], capture_output=True, timeout=10)
        if done.returncode:
            path.unlink(missing_ok=True)
            raise HTTPException(503, "Could not start the update service; check its installation and journal")
    except (OSError, subprocess.TimeoutExpired):
        # An uncertain launch is not safe to repeat; keep the request for local inspection.
        raise HTTPException(503, "Update launch could not be confirmed; inspect the server before retrying") from None
    log.warning("Signed release %s update requested by user %s; request %s", request["version"], admin.id, request["id"], extra={"source": "updates"})
    return status(session)
