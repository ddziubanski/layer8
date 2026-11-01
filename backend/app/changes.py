# SPDX-License-Identifier: AGPL-3.0-or-later
"""Change management, light: risk-scored changes with plans (the rollback plan is required),
approval when the risk calls for it, and a record of what happened.

A change moves draft -> awaiting approval -> approved (or rejected) -> in progress -> completed,
failed or rolled back; it can be cancelled before it ends. A standard change (from a pre-approved
template) skips approval; an emergency change may start at once and is approved afterwards.
Linked tickets get a note when a change starts and when it ends.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import ipam, netdisco, numbering, services, sla
from app.config import settings
from app.models import (
    Change,
    ChangeEvent,
    ChangeKind,
    ChangeStatus,
    Device,
    Group,
    IpAddress,
    MerakiClient,
    NetworkDevice,
    NetworkInventory,
    NetworkObservation,
    Subnet,
    System,
    Ticket,
    TicketDevice,
    TicketSource,
    TicketSystem,
    User,
    UserRole,
    change_tickets,
    group_members,
    system_devices,
)

LEVELS = ("low", "medium", "high")
OPEN = (ChangeStatus.draft, ChangeStatus.awaiting_approval, ChangeStatus.approved, ChangeStatus.in_progress)
EDITABLE = (ChangeStatus.draft, ChangeStatus.awaiting_approval, ChangeStatus.rejected)
ENDED = (ChangeStatus.completed, ChangeStatus.failed, ChangeStatus.rolled_back)
CORRELATABLE = (
    ChangeStatus.in_progress,
    ChangeStatus.completed,
    ChangeStatus.failed,
    ChangeStatus.rolled_back,
    ChangeStatus.approved,
)


class ChangeError(ValueError):
    """The message is safe to show."""


def label(c: Change) -> str:
    return numbering.label(c.id, c.created_at, settings.change_label_format)


def risk(c: Change) -> int:
    return c.impact * c.likelihood


def risk_level(c: Change) -> str:
    r = risk(c)
    return "low" if r <= 2 else "medium" if r <= 4 else "high"


def needs_approval(c: Change) -> bool:
    if c.kind == ChangeKind.standard:
        return False
    level = settings.change_approval_level if settings.change_approval_level in LEVELS else "medium"
    return LEVELS.index(risk_level(c)) >= LEVELS.index(level)


def is_approver(session: Session, user: User) -> bool:
    name = settings.change_approvers_group.strip().lower()
    if not name:
        return user.role == UserRole.admin
    return session.scalar(
        select(Group.id).join(group_members, group_members.c.group_id == Group.id)
        .where(func.lower(Group.name) == name, Group.is_active, group_members.c.user_id == user.id).limit(1)
    ) is not None


def can_approve(session: Session, c: Change, user: User) -> bool:
    awaiting = c.status == ChangeStatus.awaiting_approval or (c.kind == ChangeKind.emergency and c.approved_at is None and c.status in (ChangeStatus.in_progress, ChangeStatus.completed, ChangeStatus.failed, ChangeStatus.rolled_back))
    return awaiting and is_approver(session, user) and user.id not in (c.owner_id, c.requested_by_id)


def conflicts(session: Session, c: Change) -> list[tuple[Change, list[str]]]:
    """Other changes not ended or cancelled whose windows overlap this one's, each with what they share
    (devices, systems). A window without an end counts as an hour."""
    if c.planned_start is None:
        return []
    from datetime import timedelta

    end = c.planned_end or c.planned_start + timedelta(hours=1)
    live = (ChangeStatus.draft, ChangeStatus.awaiting_approval, ChangeStatus.approved, ChangeStatus.in_progress)
    out = []
    for other in session.scalars(select(Change).where(Change.id != c.id, Change.status.in_(live), Change.planned_start.is_not(None), Change.planned_start < end).order_by(Change.planned_start)):
        other_end = other.planned_end or other.planned_start + timedelta(hours=1)
        if other_end <= c.planned_start:
            continue
        shared = [d.name for d in other.devices if d in c.devices] + [s.name for s in other.systems if s in c.systems]
        out.append((other, shared))
    return out


def event(c: Change, actor: User | None, type_: str, note: str = "") -> None:
    c.events.append(ChangeEvent(actor_id=actor.id if actor else None, type=type_, note=note))


def _ready(c: Change) -> None:
    missing = [what for what, v in (("an implementation plan", c.implementation_plan), ("a rollback plan", c.rollback_plan)) if not v.strip()]
    if c.planned_start is None:
        missing.append("a planned start")
    if missing:
        raise ChangeError("It needs " + ", ".join(missing) + " first")


def _tell_tickets(session: Session, c: Change, actor: User, text: str) -> None:
    for t in c.tickets:
        services.add_comment(session, t, actor, body=f"Change {label(c)} \"{c.title}\" {text}", is_internal=True, source=TicketSource.agent, as_staff=True)


def act(session: Session, c: Change, actor: User, action: str, note: str = "") -> None:
    """submit, approve, reject, start, complete, fail, roll_back, cancel, reopen. Doesn't commit."""
    note = note.strip()
    now = sla.utcnow()
    s = c.status
    if action == "submit":
        if s != ChangeStatus.draft:
            raise ChangeError("Only a draft can be submitted")
        _ready(c)
        if needs_approval(c):
            c.status = ChangeStatus.awaiting_approval
        else:
            c.status = ChangeStatus.approved
            note = note or ("Pre-approved (a standard change)" if c.kind == ChangeKind.standard else f"No approval needed at {risk_level(c)} risk")
    elif action in ("approve", "reject"):
        if not can_approve(session, c, actor):
            raise ChangeError("You can't approve this change (approvers: Settings > Changes; nobody approves their own)")
        if action == "reject":
            if c.status != ChangeStatus.awaiting_approval:
                raise ChangeError("An emergency change that has started can't be rejected; record what happened instead")
            if not note:
                raise ChangeError("Say why it's rejected")
            c.status = ChangeStatus.rejected
        else:
            if c.status == ChangeStatus.awaiting_approval:
                c.status = ChangeStatus.approved
            c.approved_by_id, c.approved_at = actor.id, now
    elif action == "start":
        if s == ChangeStatus.approved or (c.kind == ChangeKind.emergency and s in (ChangeStatus.draft, ChangeStatus.awaiting_approval)):
            if c.kind == ChangeKind.emergency and s != ChangeStatus.approved:
                if not c.rollback_plan.strip():
                    raise ChangeError("Even an emergency change needs a rollback plan")
                note = note or "Emergency: started before approval"
            c.status, c.started_at = ChangeStatus.in_progress, now
            _tell_tickets(session, c, actor, "has started.")
        else:
            raise ChangeError("It must be approved before it starts" if s in (ChangeStatus.draft, ChangeStatus.awaiting_approval) else "It can't start from here")
    elif action in ("complete", "fail", "roll_back"):
        if s != ChangeStatus.in_progress:
            raise ChangeError("Only a change in progress can end")
        if action != "complete" and not note:
            raise ChangeError("Say what happened")
        c.status = {"complete": ChangeStatus.completed, "fail": ChangeStatus.failed, "roll_back": ChangeStatus.rolled_back}[action]
        c.finished_at, c.outcome = now, note or c.outcome
        word = {"complete": "is complete", "fail": "failed", "roll_back": "was rolled back"}[action]
        _tell_tickets(session, c, actor, word + (f": {note}" if note else "."))
    elif action == "cancel":
        if s not in OPEN:
            raise ChangeError("It has already ended")
        c.status = ChangeStatus.cancelled
        if s == ChangeStatus.in_progress:
            _tell_tickets(session, c, actor, "was cancelled" + (f": {note}" if note else "."))
    elif action == "reopen":
        if s not in (ChangeStatus.rejected, ChangeStatus.cancelled):
            raise ChangeError("Only a rejected or cancelled change can be reopened")
        c.status, c.approved_by_id, c.approved_at = ChangeStatus.draft, None, None
    else:
        raise ChangeError(f"Unknown action {action!r}")
    event(c, actor, {"roll_back": "rolled_back", "fail": "failed", "complete": "completed", "start": "started", "approve": "approved",
                     "reject": "rejected", "submit": "submitted", "cancel": "cancelled", "reopen": "reopened"}[action], note)


# Change -> incident correlation and post-change review


def effective_start(c: Change) -> datetime | None:
    starts = [t for t in (c.started_at, c.planned_start) if t is not None]
    if starts:
        return min(starts)
    if c.status in (ChangeStatus.in_progress, *ENDED):
        return c.finished_at or c.created_at
    return None


def _windows(c: Change) -> list[tuple[datetime, datetime]]:
    hours = settings.change_correlation_hours
    if hours <= 0 or c.status not in CORRELATABLE:
        return []
    delta = timedelta(hours=hours)
    wins: list[tuple[datetime, datetime]] = []
    if c.started_at is not None:
        wins.append((c.started_at, max(c.started_at, c.finished_at or c.started_at) + delta))
    if c.planned_start is not None:
        wins.append((c.planned_start, max(c.planned_start, c.planned_end or c.planned_start) + delta))
    if not wins and c.status in (ChangeStatus.in_progress, *ENDED):
        start = c.finished_at or c.created_at
        if start is not None:
            wins.append((start, start + delta))
    return wins


def in_correlation_window(c: Change, at: datetime | None) -> bool:
    if at is None:
        return False
    return any(lo <= at <= hi for lo, hi in _windows(c))


def is_follow_up_ticket(c: Change, t: Ticket) -> bool:
    """True when a linked ticket followed the change (arrived in its window or was confirmed after it started),
    as opposed to the originating ticket that requested the change beforehand."""
    tlabel = numbering.label(t.id, t.created_at, settings.ticket_label_format)
    for e in c.events:
        if e.type == "ticket_linked" and e.note.split(":", 1)[0].strip() in (f"#{t.id}", tlabel):
            return True
    if t.created_at is None:
        return False
    if c.started_at is not None and t.created_at >= c.started_at:
        return True
    if c.planned_start is not None and t.created_at >= c.planned_start:
        return True
    return False


def link_ticket(session: Session, c: Change, ticket: Ticket, actor: User) -> None:
    if c.status in (ChangeStatus.cancelled, ChangeStatus.rejected):
        raise ChangeError("Tickets can't be linked to a cancelled or rejected change")
    if all(t.id != ticket.id for t in c.tickets):
        c.tickets.append(ticket)
        tlabel = numbering.label(ticket.id, ticket.created_at, settings.ticket_label_format)
        event(c, actor, "ticket_linked", f"{tlabel}: {ticket.subject}")
        services.add_comment(
            session,
            ticket,
            actor,
            body=f"Linked to change {label(c)} \"{c.title}\".",
            is_internal=True,
            source=TicketSource.agent,
            as_staff=True,
        )


def unlink_ticket(session: Session, c: Change, ticket: Ticket, actor: User) -> None:
    if c.status in (ChangeStatus.cancelled, ChangeStatus.rejected):
        raise ChangeError("Tickets can't be unlinked from a cancelled or rejected change")
    if any(t.id == ticket.id for t in c.tickets):
        c.tickets = [t for t in c.tickets if t.id != ticket.id]
        tlabel = numbering.label(ticket.id, ticket.created_at, settings.ticket_label_format)
        event(c, actor, "ticket_unlinked", f"{tlabel}: {ticket.subject}")


@dataclass
class _Footprint:
    device_names: dict[int, str] = field(default_factory=dict)
    device_by_lower: dict[str, str] = field(default_factory=dict)
    system_names: dict[int, str] = field(default_factory=dict)
    switches: dict[str, str] = field(default_factory=dict)
    subnets: dict[int, str] = field(default_factory=dict)
    sites: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.device_names or self.system_names or self.switches or self.subnets or self.sites)


def _device_ips(session: Session, d: Device) -> list[IpAddress]:
    names = {n.lower() for n in (d.name, d.dns_hostname) if n}
    short = d.name.lower()
    match = or_(func.lower(IpAddress.hostname).in_(names), func.split_part(func.lower(IpAddress.hostname), ".", 1) == short)
    return list(
        session.scalars(
            select(IpAddress).where(
                or_(IpAddress.device_id == d.id, IpAddress.device_id.is_(None) & (IpAddress.hostname != "") & match)
            )
        )
    )


def _subnet_site(s: Subnet | None) -> str:
    if s is None:
        return ""
    if s.site:
        return s.site
    return s.vlan.site if s.vlan and s.vlan.site else ""


def _subnet_label(s: Subnet) -> str:
    return f"{s.network} ({s.name})" if s.name else str(s.network)


def _build_footprint(session: Session, devices: list[Device], systems: list[System]) -> _Footprint:
    fp = _Footprint()
    dev_map: dict[int, Device] = {d.id: d for d in devices}
    sys_map: dict[int, System] = {s.id: s for s in systems}

    if sys_map:
        for d in session.scalars(select(Device).join(system_devices, system_devices.c.device_id == Device.id).where(system_devices.c.system_id.in_(list(sys_map)))):
            dev_map.setdefault(d.id, d)
        for d in session.scalars(select(Device).join(IpAddress, IpAddress.device_id == Device.id).where(IpAddress.system_id.in_(list(sys_map)))):
            dev_map.setdefault(d.id, d)
    if dev_map:
        for s in session.scalars(select(System).join(system_devices, system_devices.c.system_id == System.id).where(system_devices.c.device_id.in_(list(dev_map)))):
            sys_map.setdefault(s.id, s)

    for d in dev_map.values():
        fp.device_names[d.id] = d.name
        fp.device_by_lower[d.name.lower()] = d.name
    for s in sys_map.values():
        fp.system_names[s.id] = s.name
        for sa in s.addresses:
            sub = ipam.subnet_of(session, sa.address)
            if sub is not None:
                fp.subnets[sub.id] = _subnet_label(sub)
                site = _subnet_site(sub)
                if site:
                    fp.sites.setdefault(site.lower(), site)

    macs: set[str] = set()
    for d in dev_map.values():
        for ip in _device_ips(session, d):
            if ip.mac:
                macs.add(ip.mac)
            if ip.system_id and ip.system is not None:
                fp.system_names.setdefault(ip.system_id, ip.system.name)
            sub = ipam.subnet_of(session, ip.address)
            if sub is not None:
                fp.subnets[sub.id] = _subnet_label(sub)
                site = _subnet_site(sub)
                if site:
                    fp.sites.setdefault(site.lower(), site)

    if dev_map:
        for nd in session.scalars(select(NetworkDevice).where(or_(NetworkDevice.device_id.in_(list(dev_map)), func.lower(NetworkDevice.sys_name).in_(list(fp.device_by_lower))))):
            if nd.sys_name:
                fp.switches.setdefault(nd.sys_name.lower(), nd.sys_name)

    if macs:
        for o in session.scalars(select(NetworkObservation).where(NetworkObservation.mac.in_(macs))):
            if o.uplink_name:
                fp.switches.setdefault(o.uplink_name.lower(), o.uplink_name)
            if o.source_name:
                fp.sites.setdefault(o.source_name.lower(), o.source_name)
        for m in session.scalars(select(MerakiClient).where(MerakiClient.mac.in_(macs))):
            if m.switch_name:
                fp.switches.setdefault(m.switch_name.lower(), m.switch_name)
            if m.network_name:
                fp.sites.setdefault(m.network_name.lower(), m.network_name)
        for inv in session.scalars(select(NetworkInventory).where(NetworkInventory.mac.in_(macs))):
            if inv.source_name:
                fp.sites.setdefault(inv.source_name.lower(), inv.source_name)
        for port in netdisco.ports_for(session, macs).values():
            if port.get("switch"):
                fp.switches.setdefault(port["switch"].lower(), port["switch"])

    return fp


def _change_footprint(session: Session, c: Change) -> _Footprint:
    return _build_footprint(session, list(c.devices), list(c.systems))


def _ticket_footprint(session: Session, ticket: Ticket) -> _Footprint:
    devices: list[Device] = []
    seen: set[int] = set()
    if ticket.computer is not None:
        devices.append(ticket.computer)
        seen.add(ticket.computer.id)
    for d in session.scalars(select(Device).join(TicketDevice, TicketDevice.device_id == Device.id).where(TicketDevice.ticket_id == ticket.id).order_by(Device.name)):
        if d.id not in seen:
            devices.append(d)
            seen.add(d.id)
    systems = list(session.scalars(select(System).join(TicketSystem, TicketSystem.system_id == System.id).where(TicketSystem.ticket_id == ticket.id)))
    if not devices and not systems:
        for d in session.scalars(
            select(Device).where(Device.assigned_user_id == ticket.requester_id, Device.is_active.is_(True))
            .order_by(Device.last_logon_at.desc().nulls_last(), Device.name).limit(3)
        ):
            if d.id not in seen:
                devices.append(d)
                seen.add(d.id)
    return _build_footprint(session, devices, systems)


def _match_footprints(cf: _Footprint, tf: _Footprint) -> str:
    for did, name in cf.device_names.items():
        if did in tf.device_names:
            return f"Device {name}"
    for sid, name in cf.system_names.items():
        if sid in tf.system_names:
            return f"System {name}"
    for sw_key, sw_name in tf.switches.items():
        if sw_key in cf.device_by_lower or sw_key in cf.switches:
            return f"Switch {cf.device_by_lower.get(sw_key) or cf.switches.get(sw_key) or sw_name}"
    for sw_key, sw_name in cf.switches.items():
        if sw_key in tf.device_by_lower:
            return f"Switch {sw_name}"
    for sub_id, sub_label in cf.subnets.items():
        if sub_id in tf.subnets:
            return f"Subnet {sub_label}"
    for site_key, site_label in cf.sites.items():
        if site_key in tf.sites:
            return f"Site {site_label}"
    return ""


def suggested_changes(session: Session, ticket: Ticket) -> list[tuple[Change, str]]:
    """Unlinked changes whose correlation window covers this ticket's arrival and whose device,
    system, switch, subnet or site matches the ticket."""
    if settings.change_correlation_hours <= 0 or ticket.created_at is None:
        return []
    tf = _ticket_footprint(session, ticket)
    if tf.is_empty():
        return []
    linked = set(session.scalars(select(change_tickets.c.change_id).where(change_tickets.c.ticket_id == ticket.id)))
    candidates = session.scalars(
        select(Change).where(Change.status.in_(CORRELATABLE))
        .options(selectinload(Change.devices), selectinload(Change.systems))
        .order_by(Change.started_at.desc().nulls_last(), Change.planned_start.desc().nulls_last(), Change.id.desc())
        .limit(100)
    ).all()
    out: list[tuple[Change, str]] = []
    for c in candidates:
        if c.id in linked or not in_correlation_window(c, ticket.created_at):
            continue
        scope = _match_footprints(_change_footprint(session, c), tf)
        if scope:
            out.append((c, scope))
    return out[:10]


def suggested_tickets(session: Session, c: Change, user: User) -> list[tuple[Ticket, str]]:
    """Unlinked tickets visible to `user` that arrived within the correlation window after `c`
    on the same device, system, switch, subnet or site."""
    wins = _windows(c)
    if not wins:
        return []
    cf = _change_footprint(session, c)
    if cf.is_empty():
        return []
    lo = min(w[0] for w in wins)
    hi = max(w[1] for w in wins)
    linked = {t.id for t in c.tickets}
    from app.api.tickets import visible

    stmt = visible(
        select(Ticket).where(Ticket.created_at >= lo, Ticket.created_at <= hi)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(200),
        user,
    )
    out: list[tuple[Ticket, str]] = []
    for t in session.scalars(stmt):
        if t.id in linked or not in_correlation_window(c, t.created_at):
            continue
        scope = _match_footprints(cf, _ticket_footprint(session, t))
        if scope:
            out.append((t, scope))
    return out[:20]


def stats(session: Session, user: User) -> dict:
    """Change success rate and post-change incident summary."""
    from app.api.tickets import visible_queue_ids

    qids = visible_queue_ids(user)
    allowed = None if qids is None else set(session.scalars(qids))
    rows = session.scalars(
        select(Change).where(Change.status.in_(CORRELATABLE))
        .options(selectinload(Change.tickets), selectinload(Change.events), selectinload(Change.devices), selectinload(Change.systems))
        .order_by(Change.id.desc()).limit(500)
    ).all()
    ended_count = 0
    successful_count = 0
    completed_with_incidents_count = 0
    failed_or_rolled_back_count = 0
    follow_up_ids: set[int] = set()
    unreviewed_count = 0

    for idx, c in enumerate(rows):
        fups = [t for t in c.tickets if is_follow_up_ticket(c, t) and (allowed is None or t.queue_id in allowed)]
        for t in fups:
            follow_up_ids.add(t.id)
        if c.status in ENDED:
            ended_count += 1
            if c.status == ChangeStatus.completed:
                if fups:
                    completed_with_incidents_count += 1
                else:
                    successful_count += 1
            else:
                failed_or_rolled_back_count += 1
        if idx < 25 and (c.devices or c.systems):
            unreviewed_count += len(suggested_tickets(session, c, user))

    return {
        "ended_count": ended_count,
        "successful_count": successful_count,
        "completed_with_incidents_count": completed_with_incidents_count,
        "failed_or_rolled_back_count": failed_or_rolled_back_count,
        "follow_up_tickets_count": len(follow_up_ids),
        "unreviewed_suggestions_count": unreviewed_count,
        "success_rate_pct": round(100.0 * successful_count / ended_count, 1) if ended_count else None,
        "correlation_hours": settings.change_correlation_hours,
    }
