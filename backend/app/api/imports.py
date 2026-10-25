# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin-only, preview-before-apply KACE bundle imports. No provider credentials."""
import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import kace
from app.auth import Admin, SessionDep

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
