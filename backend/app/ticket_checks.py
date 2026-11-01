# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-checking tickets: a ticket can wait on a condition Layer 8 already measures (a pending
restart cleared, critical updates installed, the Action1 agent back, an AD account unlocked or
enabled, a VM powered on). The worker checks them; when one clears the ticket records it and shows
Verify until a technician confirms. Nothing here closes a ticket.

Only data the syncs already hold is read: no vendor call is made to check a condition.
"""
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select

from app import sla
from app.models import (
    Device,
    DevicePatch,
    EventType,
    NutanixVm,
    ProactiveAlert,
    Ticket,
    TicketCheck,
    TicketDevice,
    User,
)

# (cleared?, what was seen). cleared None: no data to tell (e.g. the device left Action1).
Reading = tuple[bool | None, str]


@dataclass(frozen=True)
class Kind:
    key: str
    target: str  # "device", "user", or "alert" (a proactive ticket's own condition; never offered)
    label: Callable[[str], str]
    measure: Callable[..., Reading]


def _patch(session, device_id: int) -> DevicePatch | None:
    return session.scalar(select(DevicePatch).where(DevicePatch.device_id == device_id))


def _reboot(session, device: Device) -> Reading:
    row = _patch(session, device.id)
    if row is None:
        return None, "Not in Action1"
    return (not row.reboot_required), ("No restart pending" if not row.reboot_required else "Restart still pending")


def _updates(session, device: Device) -> Reading:
    row = _patch(session, device.id)
    if row is None:
        return None, "Not in Action1"
    return row.missing_critical == 0, ("No critical updates missing" if row.missing_critical == 0 else f"{row.missing_critical} critical update(s) still missing")


def _agent(session, device: Device) -> Reading:
    row = _patch(session, device.id)
    if row is None:
        return None, "Not in Action1"
    on = (row.status or "").lower() == "connected"
    return on, f"Action1 agent {row.status or 'unknown'}"


def _device_enabled(session, device: Device) -> Reading:
    if not device.directory_id:
        return None, "Not an Active Directory computer"
    return bool(device.enabled), "Enabled in Active Directory" if device.enabled else "Still disabled in Active Directory"


def _vm(session, device: Device) -> Reading:
    vm = session.scalar(select(NutanixVm).where(NutanixVm.device_id == device.id, NutanixVm.present.is_(True)))
    if vm is None:
        return None, "No Nutanix VM matched"
    on = (vm.power_state or "").upper() == "ON"
    return on, f"VM {vm.name} is {vm.power_state or 'unknown'}"


def _unlocked(session, user: User) -> Reading:
    if not user.directory_id:
        return None, "Not a directory account"
    return (not user.ad_locked), "Account unlocked" if not user.ad_locked else "Account still locked"


def _enabled(session, user: User) -> Reading:
    if not user.directory_id:
        return None, "Not a directory account"
    return bool(user.is_active), "Account enabled" if user.is_active else "Account still disabled"


def _alert(session, alert: ProactiveAlert) -> Reading:
    if alert.cleared_at is None:
        return False, "Still applies"
    return True, alert.cleared_detail or "No longer applies"


KINDS: dict[str, Kind] = {k.key: k for k in (
    Kind("proactive_cleared", "alert", lambda n: f"Fixed: {n}", _alert),
    Kind("reboot_done", "device", lambda n: f"Restart pending clears on {n}", _reboot),
    Kind("updates_installed", "device", lambda n: f"Critical updates installed on {n}", _updates),
    Kind("agent_online", "device", lambda n: f"Action1 agent back online on {n}", _agent),
    Kind("device_enabled", "device", lambda n: f"{n} enabled in Active Directory", _device_enabled),
    Kind("vm_powered_on", "device", lambda n: f"VM {n} powered on", _vm),
    Kind("account_unlocked", "user", lambda n: f"{n}'s account unlocked", _unlocked),
    Kind("account_enabled", "user", lambda n: f"{n}'s account enabled", _enabled),
)}


class CheckError(ValueError):
    """The message is safe to show."""


def _target(session, check: TicketCheck):
    if KINDS[check.kind].target == "alert":
        return session.scalar(select(ProactiveAlert).where(ProactiveAlert.ticket_id == check.ticket_id))
    return session.get(Device, check.device_id) if KINDS[check.kind].target == "device" else session.get(User, check.user_id)


def measure(session, check: TicketCheck) -> Reading:
    kind = KINDS.get(check.kind)
    target = _target(session, check) if kind else None
    if kind is None or target is None:
        return None, "Nothing to check any more"
    return kind.measure(session, target)


def ticket_devices(session, ticket: Ticket) -> list[Device]:
    ids = [ticket.computer_id] if ticket.computer_id else []
    ids += [d for d in session.scalars(select(TicketDevice.device_id).where(TicketDevice.ticket_id == ticket.id)) if d not in ids]
    return [d for d in (session.get(Device, i) for i in ids) if d is not None]


def available(session, ticket: Ticket) -> list[dict]:
    """What this ticket could wait on now: measurable conditions that haven't cleared yet, for its
    linked devices and its requester, not already being watched."""
    watching = {(c.kind, c.device_id, c.user_id) for c in session.scalars(
        select(TicketCheck).where(TicketCheck.ticket_id == ticket.id, TicketCheck.state.in_(("waiting", "cleared"))))}
    out = []
    targets: list[tuple[str, object]] = [("device", d) for d in ticket_devices(session, ticket)]
    requester = session.get(User, ticket.requester_id)
    if requester is not None:
        targets.append(("user", requester))
    for target_type, target in targets:
        for kind in KINDS.values():
            if kind.target != target_type:
                continue
            key = (kind.key, target.id if target_type == "device" else None, target.id if target_type == "user" else None)
            if key in watching:
                continue
            cleared, seen = kind.measure(session, target)
            if cleared is False:
                name = target.name if target_type == "device" else target.display_name
                out.append({"kind": kind.key, "device_id": key[1], "user_id": key[2], "label": kind.label(name), "now": seen})
    return out


def add(session, ticket: Ticket, actor: User | None, kind: str, *, device_id: int | None = None, user_id: int | None = None) -> TicketCheck:
    from app import services

    k = KINDS.get(kind)
    if k is None:
        raise CheckError("Unknown condition")
    if k.target == "alert":
        alert = session.scalar(select(ProactiveAlert).where(ProactiveAlert.ticket_id == ticket.id))
        if alert is None:
            raise CheckError("Only proactive tickets watch their own condition")
        target, device_id, user_id, name = alert, None, None, alert.title
    elif k.target == "device":
        if device_id is None or device_id not in {d.id for d in ticket_devices(session, ticket)}:
            raise CheckError("Link the device to this ticket first")
        target, user_id = session.get(Device, device_id), None
        name = target.name
    else:
        if user_id is None or user_id != ticket.requester_id:
            raise CheckError("Only the ticket's requester can be watched")
        target, device_id = session.get(User, user_id), None
        name = target.display_name
    if session.scalar(select(TicketCheck.id).where(TicketCheck.ticket_id == ticket.id, TicketCheck.kind == kind, TicketCheck.device_id.is_(device_id) if device_id is None else TicketCheck.device_id == device_id,
                                                   TicketCheck.user_id.is_(user_id) if user_id is None else TicketCheck.user_id == user_id, TicketCheck.state.in_(("waiting", "cleared")))):
        raise CheckError("Already watching that")
    check = TicketCheck(ticket_id=ticket.id, kind=kind, device_id=device_id, user_id=user_id, label=k.label(name)[:300], created_by_id=actor.id if actor else None)
    session.add(check)
    session.flush()
    services._event(session, ticket, EventType.check_added, actor, check=check.id, label=check.label)
    evaluate(session, [check])
    return check


def evaluate(session, checks: list[TicketCheck] | None = None) -> int:
    """Check waiting conditions; record the ones that cleared. Returns how many cleared."""
    from app import services

    if checks is None:
        checks = list(session.scalars(select(TicketCheck).join(Ticket, Ticket.id == TicketCheck.ticket_id)
                                      .where(TicketCheck.state == "waiting", Ticket.status.not_in(sla.CLOSED_STATUSES))))
    now = sla.utcnow()
    bot = None
    cleared = 0
    for check in checks:
        if check.state != "waiting":
            continue
        ok, seen = measure(session, check)
        check.checked_at = now
        check.detail = seen[:500]
        if ok:
            check.state, check.cleared_at = "cleared", now
            bot = bot or services.automation_user(session)
            ticket = session.get(Ticket, check.ticket_id)
            services._event(session, ticket, EventType.check_cleared, bot, check=check.id, label=check.label, seen=check.detail)
            cleared += 1
    session.flush()
    return cleared


def confirm(session, check: TicketCheck, actor: User) -> None:
    from app import services

    if check.state != "cleared":
        raise CheckError("Only a cleared condition can be verified")
    check.state, check.confirmed_by_id, check.confirmed_at = "confirmed", actor.id, sla.utcnow()
    services._event(session, session.get(Ticket, check.ticket_id), EventType.check_confirmed, actor, check=check.id, label=check.label)


def cancel(session, check: TicketCheck, actor: User) -> None:
    from app import services

    if check.state not in ("waiting", "cleared"):
        raise CheckError("That check is already finished")
    check.state = "cancelled"
    services._event(session, session.get(Ticket, check.ticket_id), EventType.check_cancelled, actor, check=check.id, label=check.label)


def watch_for_patching(session, ticket: Ticket, row: DevicePatch, actor: User | None) -> None:
    """A "Patching overdue" ticket from Action1 waits on what it was opened for."""
    if row.device_id is None:
        return
    if row.reboot_required:
        add(session, ticket, actor, "reboot_done", device_id=row.device_id)
    if row.missing_critical:
        add(session, ticket, actor, "updates_installed", device_id=row.device_id)
