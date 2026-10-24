# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select

from app import services, storage
from app.api.tickets import ticket_access
from app.auth import CurrentUser, SessionDep
from app.config import settings
from app.models import Attachment, Comment
from app.previews import preview_kind, serve_type
from app.schemas import AttachmentOut

router = APIRouter(prefix="/api", tags=["attachments"])

CHUNK = 1024 * 1024


def _visible(stmt, staff: bool):
    """Only staff see files attached to internal notes."""
    if staff:
        return stmt
    return stmt.outerjoin(Comment, Attachment.comment_id == Comment.id).where(
        or_(Comment.id.is_(None), Comment.is_internal.is_(False))
    )


@router.get("/tickets/{ticket_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(ticket_id: int, user: CurrentUser, session: SessionDep):
    ticket, staff = ticket_access(session, ticket_id, user)
    stmt = select(Attachment).where(Attachment.ticket_id == ticket.id).order_by(Attachment.created_at, Attachment.filename)
    return session.scalars(_visible(stmt, staff)).all()


@router.post("/tickets/{ticket_id}/attachments", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
def upload_attachment(
    ticket_id: int,
    user: CurrentUser,
    session: SessionDep,
    file: UploadFile = File(...),
    comment_id: int | None = Form(default=None),
):
    ticket, staff = ticket_access(session, ticket_id, user)
    comment = session.get(Comment, comment_id) if comment_id is not None else None
    if comment_id is not None and comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")

    limit = settings.max_upload_mb * 1024 * 1024
    data = bytearray()
    while chunk := file.file.read(CHUNK):
        data += chunk
        if len(data) > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Files are limited to {settings.max_upload_mb} MB")

    attachment = services.add_attachment(
        session,
        ticket,
        user,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        data=bytes(data),
        comment=comment,
        as_staff=staff,
    )
    session.commit()
    return attachment


def _load_file(session, attachment_id: uuid.UUID, user):
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    # Same rules as the ticket itself, and internal-note files for staff only.
    _, staff = ticket_access(session, attachment.ticket_id, user)
    if session.scalar(_visible(select(Attachment.id).where(Attachment.id == attachment_id), staff)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    path = storage.path(attachment.storage_key)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "File is missing from storage")
    return attachment, path


@router.get("/attachments/{attachment_id}/view")
def view_attachment(attachment_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    """Show an image, PDF or text file in the browser. Other types are refused; download those."""
    attachment, path = _load_file(session, attachment_id, user)
    kind = preview_kind(attachment.content_type, attachment.filename)
    if kind is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "This file type can only be downloaded")
    # PDF viewers refuse to run in a sandbox; PDFs get a no-script policy instead.
    policy = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    if kind != "pdf":
        policy = "sandbox; " + policy
    return FileResponse(
        path,
        media_type=serve_type(kind, attachment.content_type, attachment.filename),
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(attachment.filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": policy,
            # Shown inside the ticket page's preview frame.
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    attachment, path = _load_file(session, attachment_id, user)
    return FileResponse(
        path,
        media_type=attachment.content_type,
        headers={
            # Always a download, never rendered: an uploaded .html or .svg can't run in our origin.
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )

