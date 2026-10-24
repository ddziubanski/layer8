# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin-only, local evidence of integration health. Never contacts an external service.

Worker passes are evidence of scheduled work, not proof that credentials are still valid.
No provider errors, signed URLs, secrets or per-technician OAuth tokens are returned.
"""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sla, syslog
from app.config import settings
from app.models import DirectorySyncRun, NetworkRead, Runner, ServiceHeartbeat, WorkerJob
from app.schemas import IntegrationHealthItemOut, IntegrationHealthOut, RelayHealthOut

RELAY_LATE = timedelta(minutes=3)  # Same check-in window as script previews.
DUE_GRACE = timedelta(minutes=5)


def job_health(*, key: str, label: str, enabled: bool, missing: list[str], path: str,
               job: WorkerJob | None, worker_up: bool, now: datetime, interval: timedelta) -> IntegrationHealthItemOut:
    item = IntegrationHealthItemOut(
        key=key, label=label, state="unknown", enabled=enabled, configured=not missing,
        summary="No completed pass has been recorded.", next_step="Review configuration and worker status.",
        settings_path=path, job_name=key,
        last_attempt_at=(job.last_started_at or job.last_finished_at) if job else None,
        last_success_at=job.last_success_at if job else None, next_due_at=job.next_due_at if job else None,
    )
    if not enabled:
        item.state, item.summary, item.next_step = "off", "Not in use.", "Enable only if your service desk uses this integration."
    elif missing:
        item.state, item.summary, item.next_step = "warning", "Setup incomplete: " + ", ".join(missing) + ".", "Complete configuration, then use its connection test where available."
    elif job and job.paused:
        item.state, item.summary, item.next_step = "warning", "Scheduled work is paused.", "Review the pause under Settings > Status before resuming."
    elif job and job.last_ok is False:
        item.state, item.summary, item.next_step = "error", "The last worker pass failed.", "Review Settings > Status and the system log; test the connection before changing credentials."
    elif not worker_up:
        item.state, item.summary, item.next_step = "error", "The background worker is not checking in.", "Check the worker service under Settings > Status."
    elif job and job.last_started_at and (not job.last_finished_at or job.last_started_at > job.last_finished_at):
        item.state, item.summary, item.next_step = "warning", "A worker pass is running.", "Wait for completion; review Status if it does not finish."
    elif job and job.last_ok and job.last_finished_at:
        if (job.next_due_at and job.next_due_at + DUE_GRACE < now) or job.last_finished_at + max(interval * 2, DUE_GRACE) < now:
            item.state, item.summary, item.next_step = "warning", "Scheduled work is overdue.", "Review the worker and this job under Settings > Status."
        elif (job.last_summary or "").lower().startswith(("off:", "waiting", "skipped:")):
            item.state, item.summary = "warning", "The last pass did not confirm completed integration work."
        else:
            item.state, item.summary, item.next_step = "ok", "The latest worker pass completed successfully.", "No action indicated by saved job data."
    return item


def snapshot(session: Session) -> IntegrationHealthOut:
    from app import meraki
    from app.mail import graph

    now = sla.utcnow()
    beat = session.get(ServiceHeartbeat, "worker")
    worker_state = "never_started" if beat is None else "not_responding" if syslog.worker_late(beat, now) else "running"
    rows = {row.name: row for row in session.scalars(select(WorkerJob))}
    # Only fixed field labels leave this function, never configuration values.
    def missing(*fields):
        return [label for name, label in fields if not getattr(settings, name)]

    meraki_missing = []
    if not meraki.configured():
        meraki_missing.append("API key or connected OAuth account")
    if not meraki.network_ids():
        meraki_missing.append("network IDs")
    definitions = [
        ("email", "Microsoft 365 mailbox", bool(settings.mailbox), graph.missing(settings), "email", timedelta(seconds=settings.mail_poll_seconds)),
        ("directory", "Directory sync", bool(settings.ldap_url), missing(("ldap_base_dn", "search base")), "directory", timedelta(minutes=settings.ldap_sync_minutes)),
        ("action1", "Action1 patch visibility", settings.action1_enabled, missing(("action1_client_id", "client ID"), ("action1_client_secret", "client secret")), "action1", timedelta(minutes=settings.action1_sync_minutes)),
        ("meraki", "Meraki observations", settings.meraki_enabled, meraki_missing, "meraki", timedelta(minutes=settings.meraki_sync_minutes)),
        ("unifi", "UniFi discovery", settings.unifi_enabled, missing(("unifi_api_key", "API key"), ("unifi_console_ids" if settings.unifi_mode == "cloud" else "unifi_console_url", "console selection")), "unifi", timedelta(minutes=max(5, settings.unifi_sync_minutes))),
        ("nutanix", "Nutanix inventory", settings.nutanix_enabled, missing(("nutanix_url", "Prism Central URL"), ("nutanix_username", "username"), ("nutanix_password", "password")), "nutanix", timedelta(minutes=max(5, settings.nutanix_sync_minutes))),
        ("junos", "Juniper switch reads", settings.junos_enabled, missing(("junos_switches", "switches"), ("junos_username", "username"), ("junos_password", "password")), "juniper", timedelta(minutes=max(5, settings.junos_sync_minutes))),
        ("teams", "Teams queue summaries", settings.teams_enabled, missing(("teams_webhook_url", "Workflows webhook")), "teams", timedelta(minutes=max(15, settings.teams_summary_minutes))),
    ]
    items = [job_health(key=key, label=label, enabled=enabled, missing=needed, path=f"/settings/{path}",
                        job=rows.get(key), worker_up=worker_state == "running", now=now, interval=interval)
             for key, label, enabled, needed, path, interval in definitions]

    # Directory checks may run every minute without doing a sync; use actual sync history.
    directory = next(item for item in items if item.key == "directory")
    directory.evidence = "Directory sync"
    run = session.scalar(select(DirectorySyncRun).where(DirectorySyncRun.dry_run.is_(False)).order_by(DirectorySyncRun.id.desc()).limit(1))
    success = session.scalar(select(DirectorySyncRun.finished_at).where(DirectorySyncRun.dry_run.is_(False), DirectorySyncRun.error.is_(None), DirectorySyncRun.finished_at.is_not(None)).order_by(DirectorySyncRun.finished_at.desc()).limit(1))
    directory.last_attempt_at, directory.last_success_at = run.started_at if run else None, success
    _actual_read(directory, run is not None, bool(run and run.error), run.finished_at if run else None, now, timedelta(minutes=max(5, settings.ldap_sync_minutes) * 2))

    # Queueing a relay read is not evidence that the switch was contacted successfully.
    junos = next(item for item in items if item.key == "junos")
    junos.evidence = "Relay read"
    read = session.scalar(select(NetworkRead).where(NetworkRead.provider == "junos").order_by(NetworkRead.id.desc()).limit(1))
    success = session.scalar(select(NetworkRead.processed_at).where(NetworkRead.provider == "junos", NetworkRead.processed_at.is_not(None), NetworkRead.error == "").order_by(NetworkRead.processed_at.desc()).limit(1))
    junos.last_attempt_at, junos.last_success_at = read.requested_at if read else None, success
    _actual_read(junos, read is not None, bool(read and read.error), read.processed_at if read else None, now, timedelta(minutes=max(5, settings.junos_sync_minutes) * 2))

    relays = [RelayHealthOut(id=row.id, name=row.name, last_seen_at=row.last_seen_at,
                            state="warning" if row.last_seen_at is None else "error" if now - row.last_seen_at >= RELAY_LATE else "ok")
              for row in session.scalars(select(Runner).where(Runner.is_active).order_by(Runner.name))]
    return IntegrationHealthOut(checked_at=now, worker_state=worker_state, items=items, relays=relays)


def _actual_read(item, exists, failed, finished_at, now, grace):
    if not item.enabled or not item.configured:
        return
    if failed:
        item.state, item.summary, item.next_step = "error", "The latest recorded sync/read failed.", "Review its integration settings and the system log."
    elif item.state in ("ok", "unknown"):
        if not exists:
            item.state, item.summary = "unknown", "No actual sync/read has been recorded."
        elif finished_at is None:
            item.state, item.summary = "warning", "A sync/read is waiting or running."
        elif finished_at + grace + DUE_GRACE < now:
            item.state, item.summary = "warning", "The last actual sync/read is overdue."
        else:
            item.state, item.summary = "ok", "The latest recorded sync/read completed successfully."
        if item.state != "ok":
            item.next_step = "Review integration settings and worker/relay status."
