# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proactive tickets: open one ticket before something lapses, each with a lead time in Settings >
Proactive tickets. Each ticket watches its own condition (app/ticket_checks.py, "proactive_cleared"),
so it shows Verify once the condition no longer holds; nothing closes by itself.

Conditions, all from data Layer 8 already holds:
- a device warranty ending (Devices, warranty lookups);
- a vendor contract ending (Vendors);
- a service account expiring (directory users whose sAMAccountName starts with a configured prefix);
- a runner that stopped checking in;
- backups that haven't succeeded in N days (when backups are set up).
Certificates aren't covered yet: Layer 8 doesn't record their expiry dates.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from app import services, sla, ticket_checks
from app.config import settings
from app.models import Device, Priority, ProactiveAlert, Runner, Ticket, TicketDevice, TicketSource, User, Vendor, WorkerJob, EventType

log = logging.getLogger(__name__)


@dataclass
class Condition:
    key: str
    title: str
    description: str
    device: Device | None = None


def _date(d) -> str:
    return f"{d:%d %B %Y}"


def conditions(session) -> list[Condition]:
    today = sla.utcnow().date()
    out: list[Condition] = []
    if settings.proactive_warranty_days:
        until = today + timedelta(days=settings.proactive_warranty_days)
        for d in session.scalars(select(Device).where(Device.is_active.is_(True), Device.warranty_ends.is_not(None), Device.warranty_ends <= until,
                                                      Device.warranty_ends >= today - timedelta(days=1))):
            out.append(Condition(f"warranty:device:{d.id}:{d.warranty_ends}", f"Warranty ending: {d.name}",
                                 f"The warranty on {d.name} ends on {_date(d.warranty_ends)}. Renew it, or plan the replacement.", d))
    if settings.proactive_contract_days:
        until = today + timedelta(days=settings.proactive_contract_days)
        for v in session.scalars(select(Vendor).where(Vendor.is_active.is_(True), Vendor.contract_ends.is_not(None), Vendor.contract_ends <= until,
                                                      Vendor.contract_ends >= today - timedelta(days=1))):
            out.append(Condition(f"contract:vendor:{v.id}:{v.contract_ends}", f"Contract ending: {v.name}",
                                 f"The {v.name} contract{f' ({v.contract_level})' if v.contract_level else ''} ends on {_date(v.contract_ends)}. Renew it or plan what replaces it."))
    prefixes = [p.strip().lower() for p in settings.proactive_account_prefixes.split(",") if p.strip()]
    if settings.proactive_account_days and prefixes:
        until = sla.utcnow() + timedelta(days=settings.proactive_account_days)
        for u in session.scalars(select(User).where(User.directory_id.is_not(None), User.is_active.is_(True), User.account_expires_at.is_not(None),
                                                    User.account_expires_at <= until, User.account_expires_at >= sla.utcnow() - timedelta(days=1))):
            if (u.sam_account_name or "").lower().startswith(tuple(prefixes)):
                out.append(Condition(f"account:user:{u.id}:{u.account_expires_at.date()}", f"Service account expiring: {u.sam_account_name}",
                                     f"The Active Directory account {u.sam_account_name} ({u.display_name}) expires on {_date(u.account_expires_at.astimezone())}. "
                                     "Extend it, or move what uses it, before it stops signing in."))
    if settings.proactive_runner_hours:
        stale = sla.utcnow() - timedelta(hours=settings.proactive_runner_hours)
        for r in session.scalars(select(Runner).where(Runner.is_active.is_(True), Runner.last_seen_at.is_not(None), Runner.last_seen_at < stale)):
            out.append(Condition(f"runner:{r.id}", f"Runner not checking in: {r.name}",
                                 f"The runner {r.name}{f' on {r.last_host}' if r.last_host else ''} last checked in {r.last_seen_at.astimezone():%d %B %Y %H:%M}. "
                                 "Scripts and scans that use it wait until it's back. Check the service on that host."))
    if settings.proactive_backup_days and settings.backup_dir:
        job = session.get(WorkerJob, "backup")
        since = sla.utcnow() - timedelta(days=settings.proactive_backup_days)
        if job is not None and job.last_finished_at is not None and (job.last_success_at is None or job.last_success_at < since):
            last = f"{job.last_success_at.astimezone():%d %B %Y %H:%M}" if job.last_success_at else "never"
            out.append(Condition("backup", "Backups failing",
                                 f"No backup has succeeded for {settings.proactive_backup_days} day(s) (last success: {last}). "
                                 f"Last error: {job.last_error or 'none recorded'}. See Settings > Backup and Settings > Status."))
    return out


def _still_applies(session, key: str) -> bool:
    """A dated condition that's out of the lead window (its date passed) still applies until the date
    itself changes: a renewed warranty or contract, or an extended account."""
    kind, *rest = key.split(":")
    if kind == "warranty" and len(rest) == 3:
        d = session.get(Device, int(rest[1]))
        return d is not None and d.is_active and str(d.warranty_ends) == rest[2]
    if kind == "contract" and len(rest) == 3:
        v = session.get(Vendor, int(rest[1]))
        return v is not None and v.is_active and str(v.contract_ends) == rest[2]
    if kind == "account" and len(rest) == 3:
        u = session.get(User, int(rest[1]))
        return u is not None and u.is_active and u.account_expires_at is not None and str(u.account_expires_at.date()) == rest[2]
    return False


def run(session) -> dict:
    """Open tickets for new conditions; mark the ones that no longer hold (their tickets show Verify)."""
    if not settings.proactive_tickets:
        return {"opened": 0, "cleared": 0}
    now = sla.utcnow()
    current = {c.key: c for c in conditions(session)}
    opened = cleared = 0
    for alert in session.scalars(select(ProactiveAlert).where(ProactiveAlert.cleared_at.is_(None))):
        if alert.key not in current and not _still_applies(session, alert.key):
            alert.cleared_at, alert.cleared_detail = now, "Renewed or fixed" if alert.key.split(":")[0] in ("warranty", "contract", "account") else "Back to normal"
            cleared += 1
    bot = None
    for key, c in current.items():
        alert = session.get(ProactiveAlert, key)
        if alert is not None:
            ticket = session.get(Ticket, alert.ticket_id) if alert.ticket_id else None
            # One ticket per condition: not again while it's open, or once handled and still the same lapse.
            if alert.cleared_at is None:
                continue
            if ticket is not None and ticket.status not in sla.CLOSED_STATUSES:
                continue
        bot = bot or services.automation_user(session)
        ticket = services.create_ticket(session, subject=c.title, description=c.description + "\n\nOpened by Layer 8's proactive checks (Settings > Proactive tickets).",
                                        category=None, requester=bot, source=TicketSource.api, priority=Priority.medium, actor=bot)
        if c.device is not None:
            session.add(TicketDevice(ticket_id=ticket.id, device_id=c.device.id, linked_by_id=bot.id))
            services._event(session, ticket, EventType.device_linked, bot, device_id=c.device.id, device=c.device.name)
        if alert is None:
            alert = ProactiveAlert(key=key, title=c.title[:300])
            session.add(alert)
        alert.ticket_id, alert.opened_at, alert.cleared_at, alert.cleared_detail = ticket.id, now, None, ""
        session.flush()
        ticket_checks.add(session, ticket, bot, "proactive_cleared")
        log.info("Proactive: opened ticket #%s: %s", ticket.id, c.title, extra={"source": "worker"})
        opened += 1
    session.flush()
    if cleared:
        ticket_checks.evaluate(session)
    return {"opened": opened, "cleared": cleared}
