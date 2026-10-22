"""Purchase orders (the Purchase Orders page) and ticket expenses (a ticket's Expenses tab).

Technicians create and edit POs and receive deliveries; approving needs the PO's approver or an
admin; deleting a PO needs an admin. POs aren't restricted by queue, but a PO only lists the
linked tickets the viewer may see.
"""

import logging
import uuid
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from app import permissions, purchasing, storage
from app.api.tickets import staff_ticket, visible
from app.auth import Agent, SessionDep
from app.config import settings
from app.models import Device, POAttachment, POEvent, POLine, POStatus, PurchaseOrder, Ticket, TicketExpense, User, Vendor
from app.previews import preview_kind, serve_type
from app.schemas import (
    ExpenseIn,
    ExpenseOut,
    FieldOptionRef,
    HeadsUpIn,
    POAttachmentOut,
    PODetailOut,
    POEventOut,
    POIn,
    POLineOut,
    POPage,
    POReceiveIn,
    POStatusIn,
    POSummaryOut,
    POTicketRef,
    SystemDeviceRef,
    TicketExpensesOut,
    UserRef,
    VendorRef,
)

router = APIRouter(tags=["purchase orders"])
PODeleter = permissions.require("po_delete")
log = logging.getLogger(__name__)
CHUNK = 1024 * 1024


def _err(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _ref(model, obj):
    return model.model_validate(obj) if obj is not None else None


def _summary(po: PurchaseOrder, cls=POSummaryOut, **extra):
    _, _, total = purchasing.totals(po)
    return cls(
        id=po.id, label=purchasing.label(po), title=po.title, vendor=_ref(VendorRef, po.vendor), department=_ref(FieldOptionRef, po.department),
        requested_by=_ref(UserRef, po.requested_by), approver=_ref(UserRef, po.approver), status=po.status, order_date=po.order_date,
        expected_date=po.expected_date, currency=po.currency, total=float(total), lines_count=len(po.lines),
        lines_received=sum(1 for l in po.lines if purchasing.money(l.received_quantity) >= purchasing.money(l.quantity)),
        created_at=po.created_at, updated_at=po.updated_at, **extra,
    )


def _detail(session, po: PurchaseOrder, user: User) -> PODetailOut:
    subtotal, tax, _ = purchasing.totals(po)
    seen = {t.id for t in session.scalars(visible(select(Ticket).where(Ticket.id.in_([t.id for t in po.tickets])), user))}
    files = session.scalars(select(POAttachment).where(POAttachment.po_id == po.id).order_by(POAttachment.created_at)).all()
    events = session.scalars(select(POEvent).where(POEvent.po_id == po.id).order_by(POEvent.id.desc())).all()
    return _summary(
        po, PODetailOut,
        ship_to=po.ship_to, budget_code=po.budget_code, vendor_reference=po.vendor_reference, shipping=float(po.shipping or 0), terms=po.terms,
        notes=po.notes, approved_at=po.approved_at, subtotal=float(subtotal), tax=float(tax), receiving_code=po.receiving_code,
        lines=[POLineOut(id=l.id, item=l.item, description=l.description, part_number=l.part_number, quantity=float(l.quantity), unit_price=float(l.unit_price),
                         tax_rate=float(l.tax_rate or 0), received_quantity=float(l.received_quantity or 0), line_total=float(purchasing.line_total(l))) for l in po.lines],
        tickets=[POTicketRef.model_validate(t) for t in po.tickets if t.id in seen],
        devices=[SystemDeviceRef.model_validate(d) for d in po.devices],
        attachments=[POAttachmentOut.model_validate(f) for f in files],
        events=[POEventOut.model_validate(e) for e in events],
        can_approve=purchasing.can_approve(po, user),
        receiving_url=purchasing.receiving_link(po) if settings.public_receiving else None,
    )


def _po(session, po_id: int) -> PurchaseOrder:
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    return po


def _links(session, body: POIn, user: User) -> tuple[list[Ticket], list[Device]]:
    tickets = [staff_ticket(session, tid, user) for tid in dict.fromkeys(body.ticket_ids)]
    devices = []
    for did in dict.fromkeys(body.device_ids):
        d = session.get(Device, did)
        if d is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No device {did}")
        devices.append(d)
    return tickets, devices


@router.get("/api/purchase-orders", response_model=POPage)
def list_pos(
    _: Agent,
    session: SessionDep,
    q: str = "",
    po_status: list[str] | None = Query(default=None, alias="status"),
    vendor_id: int | None = None,
    department_id: int | None = None,
    open_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Search by title, vendor, vendor reference, line item or part number, or the PO number."""
    stmt = select(PurchaseOrder).outerjoin(Vendor, Vendor.id == PurchaseOrder.vendor_id)
    if po_status:
        stmt = stmt.where(PurchaseOrder.status.in_(po_status))
    if open_only:
        stmt = stmt.where(PurchaseOrder.status.not_in([POStatus.received, POStatus.closed, POStatus.cancelled]))
    if vendor_id is not None:
        stmt = stmt.where(PurchaseOrder.vendor_id == vendor_id)
    if department_id is not None:
        stmt = stmt.where(PurchaseOrder.department_id == department_id)
    for word in q.split():
        like = f"%{word}%"
        digits = "".join(ch for ch in word if ch.isdigit())
        stmt = stmt.where(
            or_(
                PurchaseOrder.title.ilike(like), Vendor.name.ilike(like), PurchaseOrder.vendor_reference.ilike(like),
                PurchaseOrder.id.in_(select(POLine.po_id).where(or_(POLine.item.ilike(like), POLine.part_number.ilike(like)))),
                *([PurchaseOrder.id == int(digits)] if digits else []),
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = session.scalars(stmt.order_by(PurchaseOrder.id.desc()).limit(limit).offset(offset)).all()
    return POPage(items=[_summary(p) for p in rows], total=total)


@router.get("/api/purchase-orders/{po_id}", response_model=PODetailOut)
def get_po(po_id: int, user: Agent, session: SessionDep):
    return _detail(session, _po(session, po_id), user)


@router.post("/api/purchase-orders", response_model=PODetailOut, status_code=status.HTTP_201_CREATED)
def create_po(body: POIn, user: Agent, session: SessionDep):
    tickets, devices = _links(session, body, user)
    po = PurchaseOrder(status=POStatus.draft, created_by_id=user.id, requested_by_id=user.id, shipping=0, title="", receiving_code=purchasing.new_code())
    data = body.model_dump()
    if data["requested_by_id"] is None:
        data["requested_by_id"] = user.id
    try:
        purchasing.apply(session, po, data, tickets, devices)
    except purchasing.POError as exc:
        raise _err(exc) from None
    session.add(po)
    session.flush()
    purchasing.event(session, po, user, "created")
    session.commit()
    log.info("%s created %s", user.display_name, purchasing.label(po), extra={"source": "settings"})
    return _detail(session, po, user)


@router.put("/api/purchase-orders/{po_id}", response_model=PODetailOut)
def update_po(po_id: int, body: POIn, user: Agent, session: SessionDep):
    po = _po(session, po_id)
    tickets, devices = _links(session, body, user)
    # Links to tickets this person can't see stay as they were.
    hidden = [t for t in po.tickets if t.id not in {x.id for x in session.scalars(visible(select(Ticket).where(Ticket.id.in_([t.id for t in po.tickets])), user))}]
    try:
        changed = purchasing.apply(session, po, body.model_dump(), tickets + hidden, devices)
    except purchasing.POError as exc:
        session.rollback()
        raise _err(exc) from None
    if changed:
        purchasing.event(session, po, user, "edited", fields=changed)
    session.commit()
    return _detail(session, po, user)


@router.post("/api/purchase-orders/{po_id}/status", response_model=PODetailOut)
def set_po_status(po_id: int, body: POStatusIn, user: Agent, session: SessionDep):
    po = _po(session, po_id)
    try:
        purchasing.set_status(session, po, POStatus(body.status), user, body.note)
    except purchasing.POError as exc:
        raise _err(exc) from None
    session.commit()
    log.info("%s set %s to %s", user.display_name, purchasing.label(po), body.status, extra={"source": "settings"})
    return _detail(session, po, user)


@router.post("/api/purchase-orders/{po_id}/receive", response_model=PODetailOut)
def receive_po(po_id: int, body: POReceiveIn, user: Agent, session: SessionDep):
    po = _po(session, po_id)
    try:
        summary = purchasing.receive(session, po, {r.line_id: Decimal(str(r.quantity)) for r in body.lines}, user, body.note)
        purchasing.notify_received(session, po, summary, user.display_name)
    except purchasing.POError as exc:
        session.rollback()
        raise _err(exc) from None
    session.commit()
    return _detail(session, po, user)


@router.post("/api/purchase-orders/{po_id}/heads-up", response_model=PODetailOut)
def send_heads_up(po_id: int, body: HeadsUpIn, user: Agent, session: SessionDep):
    """Email receiving (or another address) what's coming on this PO, with the receiving link and code."""
    po = _po(session, po_id)
    to = (body.to or settings.receiving_email).strip()
    if not to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give an address, or set receiving's address in Settings > Purchasing")
    try:
        purchasing.heads_up(session, po, to, user.display_name, body.message)
    except purchasing.POError as exc:
        raise _err(exc) from None
    purchasing.event(session, po, user, "heads_up", to=to)
    session.commit()
    return _detail(session, po, user)


@router.delete("/api/purchase-orders/{po_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_po(po_id: int, admin: PODeleter, session: SessionDep):
    """For mistakes. A real PO that won't go ahead is cancelled instead, keeping its history."""
    po = _po(session, po_id)
    keys = list(session.scalars(select(POAttachment.storage_key).where(POAttachment.po_id == po.id)))
    name = purchasing.label(po)
    session.delete(po)
    session.commit()
    for key in keys:
        storage.path(key).unlink(missing_ok=True)
    log.info("%s deleted %s", admin.display_name, name, extra={"source": "settings"})


# Attachments


@router.post("/api/purchase-orders/{po_id}/attachments", response_model=POAttachmentOut, status_code=status.HTTP_201_CREATED)
def upload_po_file(po_id: int, user: Agent, session: SessionDep, file: UploadFile = File(...)):
    po = _po(session, po_id)
    limit = settings.max_upload_mb * 1024 * 1024
    data = bytearray()
    while chunk := file.file.read(CHUNK):
        data += chunk
        if len(data) > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Files are limited to {settings.max_upload_mb} MB")
    stored = storage.save(bytes(data))
    att = POAttachment(
        po_id=po.id, filename=(file.filename or "file")[:255], content_type=(file.content_type or "application/octet-stream")[:255],
        size_bytes=stored.size, sha256=stored.sha256, storage_key=stored.key, uploaded_by_id=user.id,
    )
    session.add(att)
    purchasing.event(session, po, user, "file", filename=att.filename)
    session.commit()
    return att


def _po_file(session, file_id: uuid.UUID) -> tuple[POAttachment, object]:
    att = session.get(POAttachment, file_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    path = storage.path(att.storage_key)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "File is missing from storage")
    return att, path


@router.get("/api/po-files/{file_id}/view")
def view_po_file(file_id: uuid.UUID, _: Agent, session: SessionDep):
    """Images, PDFs and text in the browser (as ticket files); others download."""
    att, path = _po_file(session, file_id)
    kind = preview_kind(att.content_type, att.filename)
    if kind is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "This file type can only be downloaded")
    policy = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    if kind != "pdf":
        policy = "sandbox; " + policy
    return FileResponse(
        path, media_type=serve_type(kind, att.content_type, att.filename),
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(att.filename)}", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": policy, "X-Frame-Options": "SAMEORIGIN"},
    )


@router.get("/api/po-files/{file_id}/download")
def download_po_file(file_id: uuid.UUID, _: Agent, session: SessionDep):
    att, path = _po_file(session, file_id)
    return FileResponse(
        path, media_type=att.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(att.filename)}", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
    )


@router.delete("/api/po-files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_po_file(file_id: uuid.UUID, user: Agent, session: SessionDep):
    att, path = _po_file(session, file_id)
    po = _po(session, att.po_id)
    purchasing.event(session, po, user, "file_removed", filename=att.filename)
    session.delete(att)
    session.commit()
    path.unlink(missing_ok=True)


# Expenses on a ticket


def _expense_out(session, e: TicketExpense) -> ExpenseOut:
    po = session.get(PurchaseOrder, e.po_id) if e.po_id else None
    total = (purchasing.money(e.quantity) * purchasing.money(e.unit_cost)).quantize(Decimal("0.01"))
    return ExpenseOut(
        id=e.id, description=e.description, vendor=_ref(VendorRef, e.vendor), quantity=float(e.quantity), unit_cost=float(e.unit_cost), total=float(total),
        po_id=e.po_id, po_label=purchasing.label(po) if po else None, notes=e.notes, created_by=_ref(UserRef, e.created_by), created_at=e.created_at,
    )


@router.get("/api/tickets/{ticket_id}/expenses", response_model=TicketExpensesOut)
def ticket_expenses(ticket_id: int, user: Agent, session: SessionDep):
    """The ticket's expense lines, plus POs linked to it. The total counts each expense once,
    and a linked PO's total only when no expense line points at that PO."""
    ticket = staff_ticket(session, ticket_id, user)
    expenses = session.scalars(select(TicketExpense).where(TicketExpense.ticket_id == ticket.id).order_by(TicketExpense.id)).all()
    pos = session.scalars(select(PurchaseOrder).where(PurchaseOrder.tickets.any(Ticket.id == ticket.id)).order_by(PurchaseOrder.id)).all()
    outs = [_expense_out(session, e) for e in expenses]
    covered = {e.po_id for e in expenses if e.po_id}
    total = sum(Decimal(str(o.total)) for o in outs) + sum(
        (purchasing.totals(p)[2] for p in pos if p.id not in covered and p.status != POStatus.cancelled), Decimal(0)
    )
    return TicketExpensesOut(expenses=outs, purchase_orders=[_summary(p) for p in pos], total=float(total))


def _check_expense(session, body: ExpenseIn) -> None:
    if body.vendor_id is not None and session.get(Vendor, body.vendor_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such vendor")
    if body.po_id is not None and session.get(PurchaseOrder, body.po_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such purchase order")


@router.post("/api/tickets/{ticket_id}/expenses", response_model=TicketExpensesOut, status_code=status.HTTP_201_CREATED)
def add_expense(ticket_id: int, body: ExpenseIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    _check_expense(session, body)
    session.add(TicketExpense(ticket_id=ticket.id, created_by_id=user.id, **{**body.model_dump(), "description": body.description.strip()}))
    session.commit()
    return ticket_expenses(ticket_id, user, session)


def _expense(session, ticket_id: int, expense_id: int, user: User) -> TicketExpense:
    ticket = staff_ticket(session, ticket_id, user)
    e = session.get(TicketExpense, expense_id)
    if e is None or e.ticket_id != ticket.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expense not found")
    return e


@router.put("/api/tickets/{ticket_id}/expenses/{expense_id}", response_model=TicketExpensesOut)
def update_expense(ticket_id: int, expense_id: int, body: ExpenseIn, user: Agent, session: SessionDep):
    e = _expense(session, ticket_id, expense_id, user)
    _check_expense(session, body)
    for k, v in body.model_dump().items():
        setattr(e, k, v.strip() if k == "description" else v)
    session.commit()
    return ticket_expenses(ticket_id, user, session)


@router.delete("/api/tickets/{ticket_id}/expenses/{expense_id}", response_model=TicketExpensesOut)
def delete_expense(ticket_id: int, expense_id: int, user: Agent, session: SessionDep):
    session.delete(_expense(session, ticket_id, expense_id, user))
    session.commit()
    return ticket_expenses(ticket_id, user, session)
