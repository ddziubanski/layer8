# SPDX-License-Identifier: AGPL-3.0-or-later
"""The public receiving page's API (/receive): no sign-in, for the receiving clerk.

The PO number alone isn't enough: each PO has a receiving code, printed on its receiving sheet
(with a QR code and the address). The clerk sees what's expected (no prices), enters what
arrived and their name, and can add a photo of the packing slip; the PO, its tickets and the
people on it hear about it. Settings > Purchasing turns the page off. Wrong codes from one
address are limited (MAX_FAILURES in WINDOW) so codes can't be guessed.
"""

import logging
import secrets
import time
from collections import defaultdict, deque
from decimal import Decimal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from app import purchasing, services, storage
from app.auth import SessionDep
from app.config import settings
from app.models import POAttachment, PurchaseOrder
from app.schemas import PublicPOIn, PublicPOLine, PublicPOOut, PublicReceiveIn

router = APIRouter(prefix="/api/public/receiving", tags=["receiving (public)"])
log = logging.getLogger("app.api.purchasing")

MAX_FAILURES = 10
WINDOW = 15 * 60
PHOTO_TYPES = {"image/jpeg", "image/png", "image/heic", "image/webp", "application/pdf"}
PHOTO_MAX = 15 * 1024 * 1024
_failures: dict[str, deque] = defaultdict(deque)


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _po(session, request: Request, number: str, code: str) -> PurchaseOrder:
    if not settings.public_receiving:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receiving here is turned off; ask the service desk")
    who = _client(request)
    recent = _failures[who]
    while recent and recent[0] < time.monotonic() - WINDOW:
        recent.popleft()
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong tries. Wait a few minutes, or ask the service desk")
    po = purchasing.find_by_number(session, number)
    given = code.strip().upper().replace("-", "").replace(" ", "")
    if po is None or not po.receiving_code or not secrets.compare_digest(po.receiving_code, given):
        recent.append(time.monotonic())
        log.warning("Receiving page: wrong PO number or code from %s", who, extra={"source": "settings"})
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That PO number and code don't match. Check the receiving sheet")
    if po.status not in purchasing.OPEN_FOR_RECEIVING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{purchasing.label(po)} isn't waiting for a delivery ({purchasing.STATUS_LABELS[po.status]})")
    return po


def _view(po: PurchaseOrder) -> PublicPOOut:
    return PublicPOOut(
        label=purchasing.label(po), title=po.title, vendor=po.vendor.name if po.vendor else None, expected_date=po.expected_date,
        ship_to=po.ship_to, status=purchasing.STATUS_LABELS[po.status],
        lines=[PublicPOLine(id=l.id, item=l.item, part_number=l.part_number, quantity=float(l.quantity), received_quantity=float(l.received_quantity or 0)) for l in po.lines],
    )


@router.post("/lookup", response_model=PublicPOOut)
def lookup(body: PublicPOIn, request: Request, session: SessionDep):
    return _view(_po(session, request, body.po, body.code))


@router.post("/receive", response_model=PublicPOOut)
def receive(body: PublicReceiveIn, request: Request, session: SessionDep):
    po = _po(session, request, body.po, body.code)
    actor = services.automation_user(session)
    name = body.name.strip()
    note = f"Received by {name} (receiving page)." + (f" {body.note.strip()}" if body.note.strip() else "")
    try:
        summary = purchasing.receive(session, po, {r.line_id: Decimal(str(r.quantity)) for r in body.lines}, actor, note)
    except purchasing.POError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    purchasing.notify_received(session, po, summary, f"{name} (receiving page)")
    session.commit()
    log.info("%s received on %s by %s (from %s)", summary, purchasing.label(po), name, _client(request), extra={"source": "settings"})
    return _view(po)


@router.post("/photo", status_code=status.HTTP_201_CREATED)
def photo(request: Request, session: SessionDep, po: str = Form(...), code: str = Form(...), name: str = Form(""), file: UploadFile = File(...)):
    """A photo or PDF of the packing slip, kept with the PO."""
    order = _po(session, request, po, code)
    if (file.content_type or "") not in PHOTO_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Send a photo (JPG, PNG, HEIC) or a PDF")
    data = file.file.read(PHOTO_MAX + 1)
    if len(data) > PHOTO_MAX:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Up to 15 MB")
    stored = storage.save(data)
    session.add(POAttachment(po_id=order.id, filename=(file.filename or "packing-slip")[:255], content_type=file.content_type, size_bytes=stored.size, sha256=stored.sha256, storage_key=stored.key))
    purchasing.event(session, order, services.automation_user(session), "file", filename=file.filename, by=name.strip() or "receiving page")
    session.commit()
    return {"ok": True}
