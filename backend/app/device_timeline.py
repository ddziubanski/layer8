# SPDX-License-Identifier: AGPL-3.0-or-later
"""One history per device, merged from everything Layer 8 already records about it.

Read-only and local: no vendor calls. Each source keeps its own rules: tickets (and the remote
sessions and deployments made from them) appear only when the viewer may see the ticket; the rest
are counted in `hidden`, never named. Script output, deployment details and notes stay on their
own pages; the timeline says what happened, when and by whom, and links there.

Entries come from dated records (tickets, changes, script runs, remote sessions, software
deployments, purchase orders, IP records, the device's own dates) and from device events: what the
directory, Action1 and network syncs noticed changing (app/device_events.py). State that's only kept as
"now" (Action1's status, pending reboot, critical patches) goes in `now`, and dates still ahead
(warranty end, replace by) in `upcoming`.
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import or_, select

from app import numbering, sla
from app.api.tickets import visible
from app.config import settings
from app.models import (
    Change,
    Device,
    DeviceEvent,
    DevicePatch,
    IpAddress,
    PurchaseOrder,
    RemoteSession,
    RunStatus,
    ScriptRun,
    SoftwareDeployment,
    Ticket,
    TicketDevice,
    User,
    change_devices,
    po_devices,
)

KINDS = ("ticket", "change", "script", "remote", "deployment", "purchase", "network", "patch", "directory", "lifecycle")
LIMIT = 300
REMOTE_TOOLS = {"msra": "Remote Assistance", "rdp": "Remote Desktop", "action1": "Action1 remote desktop"}


def _entry(at, kind, title, detail="", tone="info", link=None):
    if at is None:
        return None
    if isinstance(at, date) and not isinstance(at, datetime):
        at = datetime.combine(at, time(12), tzinfo=timezone.utc)
    return {"at": at, "kind": kind, "title": title, "detail": detail, "tone": tone, "link": link}


def _who(user: User | None) -> str:
    return f" by {user.display_name}" if user else ""


def build(session, device: Device, user: User, kinds: set[str] | None = None) -> dict:
    want = set(KINDS) if not kinds else kinds & set(KINDS)
    entries, hidden = [], 0

    # Tickets: linked to the device, or naming it as their computer. Only those the viewer may see.
    linked_ids = select(TicketDevice.ticket_id).where(TicketDevice.device_id == device.id)
    about = select(Ticket.id).where(or_(Ticket.id.in_(linked_ids), Ticket.computer_id == device.id))
    all_ids = set(session.scalars(about))
    seen_ids = set(session.scalars(visible(select(Ticket.id).where(Ticket.id.in_(all_ids)), user))) if all_ids else set()
    hidden += len(all_ids - seen_ids)
    if "ticket" in want and seen_ids:
        for t in session.scalars(select(Ticket).where(Ticket.id.in_(seen_ids))):
            label = numbering.label(t.id, t.created_at)
            entries.append(_entry(t.created_at, "ticket", f"Ticket {label} opened", t.subject, "info", f"/tickets/{t.id}"))
            if t.resolved_at:
                entries.append(_entry(t.resolved_at, "ticket", f"Ticket {label} resolved", t.subject, "ok", f"/tickets/{t.id}"))
            elif t.closed_at:
                entries.append(_entry(t.closed_at, "ticket", f"Ticket {label} closed", t.subject, "ok", f"/tickets/{t.id}"))

    def ticket_ok(ticket_id):
        return ticket_id is None or ticket_id in seen_ids or ticket_id in _visible_ticket(session, ticket_id, user)

    # Remote sessions are always started from a ticket: only shown with it.
    if "remote" in want:
        for rs in session.scalars(select(RemoteSession).where(RemoteSession.device_id == device.id)):
            if not ticket_ok(rs.ticket_id):
                hidden += 1
                continue
            tool = REMOTE_TOOLS.get(rs.tool, rs.tool)
            length = f", {max(1, round((rs.ended_at - rs.started_at).total_seconds() / 60))} min" if rs.ended_at and rs.tool != "action1" else ""
            entries.append(_entry(rs.started_at, "remote", f"{tool}{_who(rs.started_by)}", f"From ticket {_ticket_label(session, rs.ticket_id)}{length}", "info", f"/tickets/{rs.ticket_id}"))

    if "script" in want:
        for run in session.scalars(select(ScriptRun).where(ScriptRun.device_id == device.id).order_by(ScriptRun.id.desc()).limit(LIMIT)):
            if not ticket_ok(run.ticket_id):
                hidden += 1
                continue
            state = {RunStatus.succeeded: ("ok", "succeeded"), RunStatus.failed: ("bad", "failed"), RunStatus.rejected: ("warn", "rejected"),
                     RunStatus.cancelled: ("warn", "cancelled")}.get(run.status, ("info", run.status.value.replace("_", " ")))
            code = f", exit code {run.exit_code}" if run.exit_code is not None else ""
            entries.append(_entry(run.finished_at or run.created_at, "script", f'Script "{run.script.name}" {state[1]}',
                                  f"Version {run.version.number}{code}{_who(session.get(User, run.requested_by_id) if run.requested_by_id else None)}",
                                  state[0], f"/tickets/{run.ticket_id}" if run.ticket_id else "/scripts"))

    if "deployment" in want:
        for d in session.scalars(select(SoftwareDeployment).where(SoftwareDeployment.device_id == device.id)):
            if not ticket_ok(d.ticket_id):
                hidden += 1
                continue
            tone = {"success": "ok", "warning": "warn", "error": "bad", "stopped": "bad"}.get(d.state, "info")
            snap = d.snapshot or {}
            package = " ".join(x for x in (str(snap.get("package_name") or ""), str(snap.get("version") or "")) if x)
            entries.append(_entry(d.checked_at if d.state in ("success", "warning", "error", "stopped") and d.checked_at else d.submitted_at or d.created_at,
                                  "deployment", f"Software deployment {d.state.replace('_', ' ')}", package, tone,
                                  f"/tickets/{d.ticket_id}" if d.ticket_id else None))

    if "change" in want:
        q = select(Change).join(change_devices, change_devices.c.change_id == Change.id).where(change_devices.c.device_id == device.id)
        for c in session.scalars(q):
            label = numbering.label(c.id, c.created_at, settings.change_label_format)
            status = c.status.value.replace("_", " ")
            tone = "ok" if status == "completed" else "bad" if status in ("failed", "rolled back") else "warn" if status == "in progress" else "info"
            at = c.finished_at or c.started_at or c.planned_start or c.created_at
            entries.append(_entry(at, "change", f"Change {label} {status}", c.title, tone, f"/changes/{c.id}"))

    if "purchase" in want:
        q = select(PurchaseOrder).join(po_devices, po_devices.c.po_id == PurchaseOrder.id).where(po_devices.c.device_id == device.id)
        for po in session.scalars(q):
            label = numbering.label(po.id, po.created_at, settings.po_label_format)
            entries.append(_entry(po.created_at, "purchase", f"Purchase order {label}", po.status.value.replace("_", " "), "info", f"/purchase-orders/{po.id}"))

    ips = session.scalars(select(IpAddress).where(IpAddress.device_id == device.id).order_by(IpAddress.last_seen_at.desc().nulls_last())).all()
    if "network" in want:
        for ip in ips:
            entries.append(_entry(ip.first_seen_at, "network", f"First seen at {ip.address}", ip.hostname, "info", "/ipam"))

    # What the syncs noticed changing (OS, OU, restarts, updates, switch-port moves, outages).
    noticed = want & {"network", "patch", "directory"}
    if noticed:
        q = select(DeviceEvent).where(DeviceEvent.device_id == device.id, DeviceEvent.kind.in_(noticed)).order_by(DeviceEvent.at.desc()).limit(LIMIT)
        for ev in session.scalars(q):
            entries.append(_entry(ev.at, ev.kind, ev.title, ev.detail, ev.tone if ev.tone in ("info", "ok", "warn", "bad") else "info"))

    if "lifecycle" in want:
        how = {"directory": "from Active Directory", "manual": "by hand"}.get(device.source.value, f"from {device.source.value}")
        entries.append(_entry(device.created_at, "lifecycle", f"Added to Layer 8 {how}", device.os or "", "info"))
        entries.append(_entry(device.purchased_on, "lifecycle", "Purchased", device.manufacturer and f"{device.manufacturer} {device.model}".strip(), "info"))
        if device.last_logon_at:
            entries.append(_entry(device.last_logon_at, "lifecycle", "Last sign-in recorded by Active Directory", "", "info"))

    entries = [e for e in entries if e is not None]
    now = sla.utcnow()
    entries = [e for e in entries if e["at"] <= now]
    entries.sort(key=lambda e: e["at"], reverse=True)

    patch = session.scalar(select(DevicePatch).where(DevicePatch.device_id == device.id))
    current = []
    if patch is not None:
        current.append({"label": "Action1", "value": patch.status or "unknown", "at": patch.last_seen_at,
                        "tone": "bad" if (patch.status or "").lower() not in ("connected", "online") else "ok"})
        if patch.reboot_required:
            current.append({"label": "Reboot", "value": "pending", "at": patch.last_boot_at, "tone": "warn"})
        if patch.missing_critical:
            current.append({"label": "Critical updates missing", "value": str(patch.missing_critical), "at": patch.critical_since, "tone": "bad"})
        if patch.last_boot_at:
            current.append({"label": "Last boot", "value": "", "at": patch.last_boot_at, "tone": "info"})
    for ip in ips[:5]:
        current.append({"label": "IP address", "value": str(ip.address), "at": ip.last_seen_at, "tone": "info"})

    upcoming = []
    for when, what in ((device.warranty_ends, "Warranty ends"), (device.replace_by, "Due for replacement")):
        if when:
            entry = _entry(when, "lifecycle", what, "", "warn" if when <= now.date() + timedelta(days=365) else "info")
            (upcoming if entry["at"] > now else entries).append(entry)
    entries.sort(key=lambda e: e["at"], reverse=True)
    upcoming.sort(key=lambda e: e["at"])
    return {"entries": entries[:LIMIT], "now": current, "upcoming": upcoming, "hidden": hidden, "truncated": len(entries) > LIMIT}


def _visible_ticket(session, ticket_id: int, user: User) -> set[int]:
    return set(session.scalars(visible(select(Ticket.id).where(Ticket.id == ticket_id), user)))


def _ticket_label(session, ticket_id: int) -> str:
    created = session.scalar(select(Ticket.created_at).where(Ticket.id == ticket_id))
    return numbering.label(ticket_id, created)
