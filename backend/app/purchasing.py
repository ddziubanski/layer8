"""Purchase orders and ticket expenses: reference and receiving, not accounting.

A PO moves draft -> requested -> approved -> ordered -> partially received / received -> closed
(or cancelled). When it names an approver, only they (or an admin) can approve it, and it must
be approved before it's ordered. Receiving adds to each line's received quantity and sets the
status; linked tickets get an internal note saying what arrived.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app import numbering, services, sla
from app.config import settings
from app.models import (
    Device,
    FieldList,
    FieldOption,
    POEvent,
    POLine,
    POStatus,
    PurchaseOrder,
    Ticket,
    TicketSource,
    User,
    UserRole,
    Vendor,
)

OPEN_FOR_RECEIVING = (POStatus.ordered, POStatus.partially_received, POStatus.received)
STATUS_LABELS = {
    "draft": "Draft",
    "requested": "Requested",
    "approved": "Approved",
    "ordered": "Ordered",
    "partially_received": "Partially received",
    "received": "Received",
    "cancelled": "Cancelled",
    "closed": "Closed",
}


# Receiving codes: easy to read out and type (no 0/O, 1/I/L).
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_code() -> str:
    import secrets

    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def find_by_number(session: Session, text: str) -> PurchaseOrder | None:
    """A PO by its label (PO-00012) or plain number (12)."""
    text = text.strip()
    m = numbering.pattern(settings.po_label_format).fullmatch(text)
    if m:
        number = int(m.group(1))
    elif text.isdigit():
        number = int(text)
    else:
        return None
    return session.get(PurchaseOrder, number)


def notify_received(session: Session, po: PurchaseOrder, summary: str, who: str) -> int:
    """Email the requester, the approver and Settings > Purchasing's address that something arrived."""
    from sqlalchemy import select as _select

    from app.models import OutboundEmail

    if not settings.mailbox:
        return 0
    to = {u.email.lower() for u in (po.requested_by, po.approver) if u is not None and u.is_active and u.email and "@" in u.email}
    if settings.receiving_notify:
        to.add(settings.receiving_notify.lower())
    link = f"{settings.portal_url.rstrip('/')}/purchase-orders/{po.id}" if settings.portal_url else ""
    body = "\n".join(filter(None, [f"{label(po)} \"{po.title}\"" + (f" from {po.vendor.name}" if po.vendor else ""), "", summary, f"Received by {who}.", "", link]))
    for addr in sorted(to):
        session.add(OutboundEmail(to_address=addr, subject=f"Delivered: {label(po)} {po.title}", body=body))
    return len(to)


def receiving_link(po: PurchaseOrder) -> str:
    """The public receiving page, with this PO's number and code filled in."""
    from urllib.parse import urlencode

    base = settings.portal_url.rstrip("/") if settings.portal_url else ""
    return f"{base}/receive?" + urlencode({"po": label(po), "code": po.receiving_code or ""})


def heads_up(session: Session, po: PurchaseOrder, to: str, sender: str, message: str = "") -> None:
    """Tell receiving what's coming: lines and quantities (no prices), when, where, and how to receive it."""
    from app.models import OutboundEmail

    if not settings.mailbox:
        raise POError("Email isn't set up (Settings > Email), so receiving can't be emailed")
    lines = [message.strip(), ""] if message.strip() else []
    lines += [f"{label(po)} \"{po.title}\"" + (f" from {po.vendor.name}" if po.vendor else "") + " has been ordered."]
    if po.expected_date:
        lines.append(f"Expected: {po.expected_date:%A %d %B %Y}")
    if po.ship_to.strip():
        lines += ["Deliver to:", po.ship_to.strip()]
    lines += ["", "What's coming:"]
    lines += [f"  {qty(l.quantity)} × {l.item}" + (f" ({l.part_number})" if l.part_number else "") for l in po.lines]
    if settings.public_receiving:
        lines += [
            "",
            "When it arrives, mark it received here:",
            receiving_link(po),
            f"or open {settings.portal_url.rstrip('/') if settings.portal_url else ''}/receive and enter PO {label(po)} with code {po.receiving_code}.",
        ]
    lines += ["", f"Sent by {sender}."]
    session.add(OutboundEmail(to_address=to, subject=f"Heads-up: {label(po)} {po.title} is on its way", body="\n".join(lines)))


class POError(ValueError):
    """The message is safe to show."""


def label(po: PurchaseOrder) -> str:
    return numbering.label(po.id, po.created_at, settings.po_label_format)


def money(v) -> Decimal:
    return Decimal(str(v or 0))


def qty(v) -> str:
    """2, 2.5 or 0.125: no trailing zeros."""
    d = money(v).normalize()
    return format(d, "f")


def line_total(line: POLine) -> Decimal:
    net = money(line.quantity) * money(line.unit_price)
    return (net * (1 + money(line.tax_rate) / 100)).quantize(Decimal("0.01"))


def totals(po: PurchaseOrder) -> tuple[Decimal, Decimal, Decimal]:
    """(subtotal before tax, tax, total with tax and shipping)."""
    subtotal = sum((money(l.quantity) * money(l.unit_price) for l in po.lines), Decimal(0)).quantize(Decimal("0.01"))
    with_tax = sum((line_total(l) for l in po.lines), Decimal(0))
    return subtotal, with_tax - subtotal, with_tax + money(po.shipping)


def event(session: Session, po: PurchaseOrder, actor: User | None, type_: str, **data) -> None:
    session.add(POEvent(po_id=po.id, actor_id=actor.id if actor else None, type=type_, data=data))


def can_approve(po: PurchaseOrder, user: User) -> bool:
    return user.role == UserRole.admin or (po.approver_id is not None and po.approver_id == user.id)


def apply(session: Session, po: PurchaseOrder, data: dict, tickets: list[Ticket], devices: list[Device]) -> list[str]:
    """Set a PO's fields and lines from a POIn dump. Returns which parts changed (for history)."""
    if data.get("vendor_id") is not None and session.get(Vendor, data["vendor_id"]) is None:
        raise POError("No such vendor")
    if data.get("department_id") is not None:
        dept = session.get(FieldOption, data["department_id"])
        if dept is None or dept.list != FieldList.department:
            raise POError("No such department")
    for who in ("requested_by_id", "approver_id"):
        if data.get(who) is not None:
            u = session.get(User, data[who])
            if u is None or not u.is_active:
                raise POError("No such person")
            if who == "approver_id" and u.role == UserRole.requester:
                raise POError("The approver must be a technician or admin (someone who can sign in to the console)")
    changed = []
    for field in ("title", "vendor_id", "department_id", "requested_by_id", "approver_id", "order_date", "expected_date", "ship_to",
                  "budget_code", "vendor_reference", "shipping", "currency", "terms", "notes"):
        value = data[field]
        if isinstance(value, str) and field not in ("ship_to", "terms", "notes"):
            value = value.strip()
        if field == "currency":
            value = value.upper()
        old = getattr(po, field)
        if (money(old) != money(value)) if field == "shipping" else old != value:
            setattr(po, field, value)
            changed.append(field)

    def snapshot(lines):
        return [(l.id, l.item, l.description, l.part_number, money(l.quantity), money(l.unit_price), money(l.tax_rate)) for l in lines]

    before_lines = snapshot(po.lines)
    existing = {l.id: l for l in po.lines}
    keep, kept_ids = [], set()
    for pos, ln in enumerate(data["lines"]):
        line = existing.get(ln["id"]) if ln.get("id") is not None else None
        if ln.get("id") is not None and line is None:
            raise POError(f"Line {ln['id']} isn't on this PO")
        if line is not None and money(ln["quantity"]) < money(line.received_quantity):
            raise POError(f"{line.item}: {qty(line.received_quantity)} already received; the quantity can't be less")
        line = line or POLine()
        for f in ("item", "description", "part_number", "quantity", "unit_price", "tax_rate"):
            v = ln[f].strip() if isinstance(ln[f], str) and f != "description" else ln[f]
            setattr(line, f, v)
        line.position = pos
        keep.append(line)
        if line.id is not None:
            kept_ids.add(line.id)
    for gone in set(existing) - kept_ids:
        if money(existing[gone].received_quantity) > 0:
            raise POError(f"{existing[gone].item} has been (partly) received, so it can't be removed")
    if snapshot(keep) != before_lines:
        changed.append("lines")
    po.lines = keep
    if [t.id for t in po.tickets] != [t.id for t in tickets]:
        changed.append("tickets")
    po.tickets = tickets
    if [d.id for d in po.devices] != [d.id for d in devices]:
        changed.append("devices")
    po.devices = devices
    return changed


def set_status(session: Session, po: PurchaseOrder, status: POStatus, actor: User, note: str = "") -> None:
    if status == po.status:
        return
    if status in (POStatus.partially_received, POStatus.received):
        raise POError("Receiving sets that: mark what arrived instead")
    if status == POStatus.approved:
        if not can_approve(po, actor):
            raise POError(f"Only {po.approver.display_name if po.approver else 'an admin'} (or an admin) can approve this PO")
        po.approved_at = sla.utcnow()
    if status == POStatus.ordered and po.approver_id is not None and po.status not in (POStatus.approved, POStatus.ordered):
        raise POError("This PO names an approver: it needs approving before it's ordered")
    if status == POStatus.ordered and not po.lines:
        raise POError("Add what's being ordered first")
    if status == POStatus.ordered and po.order_date is None:
        po.order_date = datetime.now().astimezone().date()
    before = po.status
    po.status = status
    event(session, po, actor, "status", before=before.value, after=status.value, note=note)
    if status == POStatus.ordered and settings.receiving_heads_up and settings.receiving_email and settings.mailbox:
        heads_up(session, po, settings.receiving_email, actor.display_name)
        event(session, po, actor, "heads_up", to=settings.receiving_email)


def receive(session: Session, po: PurchaseOrder, quantities: dict[int, Decimal], actor: User, note: str = "") -> str:
    """Add what arrived; returns a one-line summary (also noted on linked tickets)."""
    if po.status not in OPEN_FOR_RECEIVING:
        raise POError("Only an ordered PO can be received")
    lines = {l.id: l for l in po.lines}
    got = []
    for line_id, amount in quantities.items():
        line = lines.get(line_id)
        if line is None:
            raise POError(f"Line {line_id} isn't on this PO")
        new = money(line.received_quantity) + money(amount)
        if new < 0:
            raise POError(f"{line.item}: can't receive less than nothing")
        if new > money(line.quantity):
            raise POError(f"{line.item}: {qty(line.quantity)} ordered, so {qty(new)} can't have arrived")
        if amount:
            line.received_quantity = new
            got.append(f"{qty(amount)} × {line.item}")
    if not got:
        raise POError("Nothing to receive")
    done = all(money(l.received_quantity) >= money(l.quantity) for l in po.lines)
    some = any(money(l.received_quantity) > 0 for l in po.lines)
    before = po.status
    po.status = POStatus.received if done else POStatus.partially_received if some else POStatus.ordered
    summary = f"Received {', '.join(got)}." + (" Everything on it has arrived." if done else "")
    event(session, po, actor, "received", lines={str(k): float(v) for k, v in quantities.items()}, summary=summary, note=note, before=before.value, after=po.status.value)
    for ticket in po.tickets:
        services.add_comment(
            session, ticket, actor, body=f"{label(po)} \"{po.title}\": {summary}" + (f"\n{note}" if note else ""),
            is_internal=True, source=TicketSource.agent, as_staff=True,
        )
    return summary
