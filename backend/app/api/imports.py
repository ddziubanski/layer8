# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin-only, preview-before-apply imports: KACE bundles, and Track-It! bundles (staged once, run in
the background; app/trackit_jobs.py). No provider credentials."""
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import kace, trackit, trackit_jobs
from app.models import Queue
from app.auth import Admin, SessionDep
from app.db import get_session_factory

router = APIRouter(prefix="/api/imports", tags=["imports"])
log = logging.getLogger(__name__)


class ImportReport(BaseModel):
    applied: bool
    queue: str
    counts: dict[str, int]
    warnings: list[str]
    fingerprint: str


def read(file: UploadFile):
    chunks, size = [], 0
    while chunk := file.file.read(1024 * 1024):
        size += len(chunk)
        if size > kace.MAX_UPLOAD:
            raise HTTPException(413, "KACE bundles are limited to 64 MiB; export a smaller batch")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/kace/preview", response_model=ImportReport)
def preview(_: Admin, session: SessionDep, file: Annotated[UploadFile, File()], queue_id: Annotated[int, Form()]):
    data = read(file)
    try:
        bundle = kace.Bundle(data)
        try:
            report = kace.run(session, bundle, queue_id)
        finally:
            bundle.zip.close()
        return {**report, "fingerprint":kace.fingerprint(data,queue_id)}
    except kace.KaceError as exc:
        raise HTTPException(422,str(exc)) from None


@router.post("/kace/apply", response_model=ImportReport)
def apply(admin: Admin, session: SessionDep, file: Annotated[UploadFile, File()], queue_id: Annotated[int, Form()], fingerprint: Annotated[str, Form()], confirm: Annotated[bool, Form()]):
    data = read(file)
    if not confirm or fingerprint != kace.fingerprint(data,queue_id):
        raise HTTPException(409,"The file or queue changed, or import was not confirmed. Preview again.")
    try:
        bundle = kace.Bundle(data)
        try:
            report = kace.run(session,bundle,queue_id,apply=True)
        finally:
            bundle.zip.close()
        log.info("KACE import applied by user ID %s: %s tickets",admin.id,report["counts"].get("tickets_created",0)+report["counts"].get("tickets_updated",0))
        return {**report,"fingerprint":fingerprint}
    except kace.KaceError as exc:
        raise HTTPException(422,str(exc)) from None


# --- Track-It!


class TrackitField(BaseModel):
    column: str
    name: str
    used: int
    total: int
    distinct: int
    top: list[tuple[str, int]]
    becomes: str


class TrackitReport(BaseModel):
    counts: dict[str, int]
    lines: list[str]
    problems: int


class TrackitUsed(BaseModel):
    queue: str | None = None
    groups: dict[str, str] = {}
    labels: dict[str, str] = {}
    skip: list[str] = []
    departments: Literal["match", "add", "skip"] = "match"


class TrackitJob(BaseModel):
    id: str
    server: str | None = None
    database: str | None = None
    exported_at: str | None = None
    since: str | None = None
    size: int
    fields: list[TrackitField]
    state: Literal["staged", "running", "done", "failed"]
    apply: bool = False
    error: str | None = None
    report: TrackitReport | None = None
    dry_run: TrackitReport | None = None
    used: TrackitUsed | None = Field(None, description="The choices of the last run (an import needs a dry run with the same)")


class TrackitChoices(BaseModel):
    queue: str | None = Field(None, description="Default queue for the tickets (blank: the default queue)")
    groups: dict[str, str] = Field(default_factory=dict, description="Track-It! opened group -> queue name")
    labels: dict[str, str] = Field(default_factory=dict, description="Track-It! field -> name on imported tickets")
    skip: list[str] = Field(default_factory=list, description="Track-It! fields to leave out")
    departments: Literal["match", "add", "skip"] = "match"
    apply: bool = False
    confirm: bool = False


def _job(job_id: str) -> dict:
    try:
        return {"id": job_id, **trackit_jobs.info(job_id), **trackit_jobs.state(job_id)}
    except (KeyError, FileNotFoundError):
        raise HTTPException(404, "No such staged Track-It! bundle (it may have expired). Upload it again.") from None


def _chunks(file: UploadFile):
    while chunk := file.file.read(1024 * 1024):
        yield chunk


@router.post("/trackit", response_model=TrackitJob)
def trackit_upload(admin: Admin, file: Annotated[UploadFile, File()]):
    """Stage a bundle made by tools/trackit-export/trackit-export.ps1 and list the fields it has."""
    try:
        job_id = trackit_jobs.stage(_chunks(file))
    except trackit.ImportError_ as exc:
        raise HTTPException(422, str(exc)) from None
    log.info("Track-It! bundle staged by user ID %s", admin.id)
    return _job(job_id)


@router.get("/trackit/{job_id}", response_model=TrackitJob)
def trackit_status(_: Admin, job_id: str):
    return _job(job_id)


@router.post("/trackit/{job_id}/run", response_model=TrackitJob)
def trackit_run(admin: Admin, session: SessionDep, job_id: str, body: TrackitChoices, factory=Depends(get_session_factory)):
    """A dry run (apply false), or the import: after a finished dry run with the same choices, and confirm."""
    _job(job_id)
    if body.apply and not body.confirm:
        raise HTTPException(409, "Confirm the import first.")
    names = {n.lower() for n in session.scalars(select(func.lower(Queue.name)).where(Queue.is_active.is_(True)))}
    for name in [body.queue, *body.groups.values()]:
        if name and name.strip().lower() not in names:
            raise HTTPException(422, f"No active queue called {name!r}")
    choices = body.model_dump(exclude={"apply", "confirm"})
    choices["groups"] = {k: v for k, v in body.groups.items() if v}
    choices["labels"] = {k.upper(): v for k, v in body.labels.items() if v}
    choices["skip"] = sorted({s.upper() for s in body.skip})
    try:
        trackit_jobs.start(job_id, choices, body.apply, admin.id, factory)
    except trackit.ImportError_ as exc:
        raise HTTPException(409, str(exc)) from None
    return _job(job_id)


@router.delete("/trackit/{job_id}", status_code=204)
def trackit_discard(_: Admin, job_id: str):
    _job(job_id)
    try:
        trackit_jobs.discard(job_id)
    except trackit.ImportError_ as exc:
        raise HTTPException(409, str(exc)) from None
