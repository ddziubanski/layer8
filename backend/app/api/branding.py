# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branding: public (the sign-in page needs it before anyone signs in), and the image uploads."""

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app import branding
from app.auth import Admin, SessionDep
from app.schemas import BrandingOut

router = APIRouter(tags=["branding"])
log = logging.getLogger("app.api.settings")


@router.get("/api/branding", response_model=BrandingOut)
def get_branding(session: SessionDep):
    return branding.public(session)


@router.get("/api/branding/files/{name}")
def branding_file(name: str, session: SessionDep):
    found = branding.file_for(session, name)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such image")
    path, content_type = found
    return FileResponse(
        path,
        media_type=content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            # The URL carries a version that changes on upload.
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@router.put("/api/settings/branding/files/{name}", response_model=BrandingOut)
def upload_image(name: str, admin: Admin, session: SessionDep, file: UploadFile = File(...)):
    data = file.file.read(branding.MAX_BYTES + 1)
    try:
        branding.store(session, name, data, admin)
    except branding.BrandingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    session.commit()
    log.info("%s image uploaded by %s", branding.IMAGES[name], admin.display_name, extra={"source": "settings"})
    return branding.public(session)


@router.delete("/api/settings/branding/files/{name}", response_model=BrandingOut)
def remove_image(name: str, admin: Admin, session: SessionDep):
    if name not in branding.IMAGES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such image")
    branding.remove(session, name, admin)
    session.commit()
    log.info("%s image removed by %s", branding.IMAGES[name], admin.display_name, extra={"source": "settings"})
    return branding.public(session)
