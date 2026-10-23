"""The background worker's jobs, and the controls admins use on Settings > Status.

The worker ticks every TICK_SECONDS: it runs each job that is due (or that an admin asked
to run now), skips paused ones, records how each run went in worker_jobs, beats its
heartbeat, and restarts when asked (exits with RESTART_EXIT; systemd or dev/worker.sh
starts it again).
"""

import logging
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sla, syslog
from app.config import settings
from app.models import ServiceRequest, WorkerJob

log = logging.getLogger("app.worker")

TICK_SECONDS = 2
HEARTBEAT_SECONDS = 15
# Exit status meaning "start me again": dev/worker.sh and the systemd unit restart on it.
RESTART_EXIT = 75


LABELS = {
    "teams": "Teams: queue summary",
    "presence": "Teams: technicians' status for the portal",
    "email": "Email: check the mailbox and send",
    "directory": "Directory sync",
    "rules": "Scheduled ticket rules",
    "reports": "Scheduled reports",
    "script_schedules": "Scheduled scripts",
    "housekeeping": "Housekeeping",
    "backup": "Backup",
    "ipam": "IP address scans (scheduled)",
    "meraki": "Meraki: read client observations",
    "action1": "Action1: read patch status",
    "sweeps": "Device sweep (weekly ticket)",
    "warranty": "Warranty lookups (Dell, Lenovo)",
}


@dataclass(frozen=True)
class Job:
    name: str
    source: str  # log source
    every: Callable[[], timedelta]  # how often it's due
    # forced -> (summary of what it did or None if nothing to report, worth logging?)
    run: Callable[[bool], tuple[str | None, bool]]

    @property
    def label(self) -> str:
        return LABELS[self.name]


def _every(seconds: int) -> Callable[[], timedelta]:
    return lambda: timedelta(seconds=seconds)


def until_backup(now: datetime | None = None) -> timedelta:
    """Time from now to the next daily backup (Settings > Backup), server local time."""
    now = (now or datetime.now()).astimezone()
    h, m = (int(x) for x in settings.backup_time.split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at - now


def build_jobs(session_factory, mail) -> list[Job]:
    """mail: worker.MailSetup (rebuilt when Settings > Email changes)."""
    from app.directory import sync as directory
    from app.mail import runner
    from app.rules import engine as rules

    def email(forced: bool):
        mailbox, cfg = mail.update()
        if mailbox is None:
            return ("Off: no mailbox set" if not settings.mailbox else "Waiting for the rest of Settings > Email"), False
        result = runner.run_pass(session_factory, mailbox, cfg)
        if result is None:
            return "Skipped: a check was already running", False
        counts: dict[str, int] = {}
        for o in result.outcomes:
            counts[o.status] = counts.get(o.status, 0) + 1
        inbox = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing new"
        return f"Inbox: {inbox}; {result.sent} sent", bool(result.outcomes or result.sent)

    def directory_sync(forced: bool):
        run = directory.run_if_due(session_factory, sla.utcnow(), force=forced)
        if run is None:
            return (None if settings.ldap_url else "Off: no directory"), False
        if run.error:
            raise RuntimeError(run.error)
        return f"{run.users_seen} users, {run.created} created, {run.linked} linked, {run.deactivated} deactivated, {run.role_changes} role changes, {run.devices_seen} computers", True

    def scheduled_rules(forced: bool):
        ran = rules.run_due(session_factory, sla.utcnow())
        return (f"Ran {ran} rule(s)" if ran else "No rules due"), bool(ran)

    def scheduled_reports(forced: bool):
        from app.reports import delivery

        ran, failed = delivery.run_due(session_factory)
        if not ran and not failed:
            return "No reports due", False
        return f"Sent {ran} report(s)" + (f", {failed} failed" if failed else ""), True

    def scheduled_scripts(forced: bool):
        from app import scriptschedules

        ran, failed = scriptschedules.run_due(session_factory)
        if not ran and not failed:
            return "No scripts due", False
        return f"Ran {ran} schedule(s)" + (f", {failed} failed" if failed else ""), True

    def housekeeping(forced: bool):
        from app import runs

        with session_factory() as session:
            pruned = syslog.prune(session, sla.utcnow())
            stale = runs.fail_stale(session)
            session.commit()
        summary = f"Removed {pruned} log entries older than {syslog.LOG_DAYS} days"
        if stale:
            summary += f"; {stale} script run(s) whose runner went quiet marked failed"
        return summary, bool(pruned or stale)

    def backup(forced: bool):
        from app.backup import archive

        if not settings.backup_dir:
            return "Off: no backup folder", False
        with session_factory() as session:
            made = archive.create(session)
        removed = archive.prune(archive.target_dir(), settings.backup_keep)
        size = f"{made.size / 1024 / 1024:.1f} MB"
        return f"{made.name} ({size}) written and verified" + (f"; removed {len(removed)} old backup(s)" if removed else ""), True

    def ipam_scans(forced: bool):
        from app import ipamscan

        with session_factory() as session:
            made = ipamscan.run_due(session)
            session.commit()
        return (f"Queued {made} subnet scan(s)" if made else "No scans due"), bool(made)

    def meraki_sync(forced: bool):
        from app import meraki

        if not meraki.on():
            return "Off: enable and configure Settings > Meraki", False
        client = meraki.from_settings()
        try:
            with session_factory() as session:
                count = meraki.sync(session, client)
                session.commit()
            return f"Read {count} Meraki client observations", True
        finally:
            client.close()

    def action1_sync(forced: bool):
        from app import action1

        if not settings.action1_enabled:
            return "Off: Action1 isn't in use (Settings > General)", False
        if not action1.enabled():
            return "Off: Action1 isn't set up", False
        client = action1.from_settings()
        with session_factory() as session:
            c = action1.sync(session, client)
            session.commit()
        summary = f"{c['machines']} machines ({c['matched']} matched to devices); {c['critical']} missing critical updates, {c['reboot']} waiting for a reboot"
        summary += f"; {c['tickets']} ticket(s) opened" if c["tickets"] else ""
        # The patch state is saved even when the automations can't be read (the credentials' role may lack view_automations).
        with session_factory() as session:
            try:
                ch = action1.sync_changes(session, client)
                session.commit()
            except action1.Action1Error as exc:
                return summary + f"; patch automations not read: {exc}", True
        words = [f"{n} change(s) {w}" for n, w in ((ch["made"], "made"), (ch["moved"], "moved"), (ch["finished"], "finished"), (ch["cancelled"], "cancelled")) if n]
        return summary + ("; " + ", ".join(words) if words else ""), True

    def warranty_lookups(forced: bool):
        from app import warranty

        if not (warranty.enabled("dell") or warranty.enabled("lenovo")):
            return "Off: no Dell or Lenovo credentials (Settings > Warranty lookups)", False
        with session_factory() as session:
            c = warranty.run(session)
            session.commit()
        if not c["looked_up"] and not c["problems"]:
            return "Nothing due", False
        summary = f"Looked up {c['looked_up']} device(s): {c['found']} found, {c['errors']} not found"
        return summary + ("; " + "; ".join(c["problems"]) if c["problems"] else ""), True

    def device_sweep(forced: bool):
        from app import sweeps

        if not settings.sweep_weekly_ticket:
            return "Off: no weekly ticket (Settings > Device sweeps)", False
        with session_factory() as session:
            ticket = sweeps.summary_ticket(session)
            session.commit()
        return (f"Opened ticket #{ticket.id}: {ticket.subject}" if ticket else "Nothing stale or unknown"), ticket is not None

    def teams_summary(forced: bool):
        from app import teams

        with session_factory() as session:
            return teams.run(session)

    def presence_refresh(forced: bool):
        from app import presence

        with session_factory() as session:
            result = presence.refresh(session)
            session.commit()
            return result

    return [
        Job("teams", "worker", lambda: timedelta(minutes=max(15, settings.teams_summary_minutes)), teams_summary),
        Job("presence", "worker", _every(60), presence_refresh),
        Job("email", "email", lambda: timedelta(seconds=settings.mail_poll_seconds), email),
        # It decides itself whether a sync is due (every ldap_sync_minutes); checked each minute.
        Job("directory", "directory", _every(60), directory_sync),
        Job("rules", "rules", _every(30), scheduled_rules),
        Job("reports", "reports", _every(60), scheduled_reports),
        Job("script_schedules", "scripts", _every(60), scheduled_scripts),
        Job("housekeeping", "worker", _every(600), housekeeping),
        # Daily at backup_time: the next due time is worked out after each run.
        Job("backup", "backup", until_backup, backup),
        Job("ipam", "ipam", _every(300), ipam_scans),
        Job("meraki", "meraki", lambda: timedelta(minutes=settings.meraki_sync_minutes), meraki_sync),
        Job("action1", "action1", lambda: timedelta(minutes=settings.action1_sync_minutes), action1_sync),
        Job("sweeps", "worker", _every(7 * 86400), device_sweep),
        Job("warranty", "devices", _every(6 * 3600), warranty_lookups),
    ]


class Worker:
    def __init__(self, session_factory, jobs: list[Job]):
        self.session_factory = session_factory
        self.jobs = jobs
        self.started_at = sla.utcnow()
        self.last_beat: datetime | None = None
        self.restart_requested_by: str | None = None

    def _row(self, session: Session, name: str) -> WorkerJob:
        row = session.get(WorkerJob, name)
        if row is None:
            row = WorkerJob(name=name)
            session.add(row)
        return row

    def run_job(self, job: Job, forced: bool) -> None:
        with self.session_factory() as session:
            row = self._row(session, job.name)
            row.last_started_at = sla.utcnow()
            row.run_requested_at = None
            session.commit()
        ok, summary, error = True, None, None
        try:
            summary, notable = job.run(forced)
            if summary and (forced or notable):
                log.info("%s: %s%s", job.label, summary, " (run now)" if forced else "", extra={"source": job.source})
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"[:1000]
            log.exception("%s failed", job.label, extra={"source": job.source})
        with self.session_factory() as session:
            row = self._row(session, job.name)
            now = sla.utcnow()
            row.last_finished_at, row.last_ok, row.last_error = now, ok, error
            if summary is not None or not ok:
                row.last_summary = summary
            row.next_due_at = now + job.every()
            session.commit()

    def tick(self, *, run_all: bool = False) -> bool:
        """Run what's due. False when a restart was asked for (the caller exits)."""
        from app import configstore

        with self.session_factory() as session:
            configstore.refresh(session, force=True)
            rows = {r.name: r for r in session.scalars(select(WorkerJob))}
            restart = session.scalar(
                select(ServiceRequest).where(ServiceRequest.service == "worker", ServiceRequest.action == "restart", ServiceRequest.handled_at.is_(None))
            )
            if restart is not None:
                restart.handled_at = sla.utcnow()
                self.restart_requested_by = restart.requested_by
                session.commit()
                log.warning("Restarting, as asked by %s", restart.requested_by or "an admin", extra={"source": "worker"})
                return False
        now = sla.utcnow()
        for job in self.jobs:
            row = rows.get(job.name)
            requested = row is not None and row.run_requested_at is not None
            due = row is None or row.next_due_at is None or row.next_due_at <= now
            if run_all or requested or (due and not (row is not None and row.paused)):
                self.run_job(job, forced=run_all or requested)
        if self.last_beat is None or (sla.utcnow() - self.last_beat).total_seconds() >= HEARTBEAT_SECONDS:
            self.beat()
        return True

    def beat(self) -> None:
        with self.session_factory() as session:
            now = sla.utcnow()
            syslog.beat(session, "worker", now, pid=os.getpid(), host=socket.gethostname(), started_at=self.started_at.isoformat(), tick_seconds=TICK_SECONDS)
            session.commit()
        self.last_beat = now


# Controls, used by the API


def request_run(session: Session, name: str) -> WorkerJob:
    row = session.get(WorkerJob, name) or WorkerJob(name=name)
    session.add(row)
    row.run_requested_at = sla.utcnow()
    return row


def set_paused(session: Session, name: str, paused: bool) -> WorkerJob:
    row = session.get(WorkerJob, name) or WorkerJob(name=name)
    session.add(row)
    row.paused = paused
    return row


def request_restart(session: Session, by: str) -> ServiceRequest:
    req = ServiceRequest(service="worker", action="restart", requested_by=by)
    session.add(req)
    return req


def pending_restart(session: Session) -> ServiceRequest | None:
    return session.scalar(
        select(ServiceRequest)
        .where(ServiceRequest.service == "worker", ServiceRequest.action == "restart", ServiceRequest.handled_at.is_(None))
        .order_by(ServiceRequest.id.desc())
        .limit(1)
    )
