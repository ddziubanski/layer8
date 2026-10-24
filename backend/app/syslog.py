# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Log tab's data: app log records kept in the database, the worker's heartbeat, and
a status summary (database, worker, directory, email, Windows sign-in, disk).

install() adds a handler to the "app" logger in the API and the worker, so existing
log.info/warning/exception calls land in system_events. A record's source comes from
extra={"source": ...} or from the logger's module.
"""

import logging
import shutil
import traceback
from datetime import datetime, timedelta

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import sla
from app.config import settings
from app.models import DirectorySyncRun, ServiceHeartbeat, SystemEvent, WorkerJob

LOG_DAYS = 90
SOURCES = {
    "updates": "Release updates",
    "directory": "Directory",
    "email": "Email",
    "sign_in": "Sign-in",
    "windows_sign_in": "Windows sign-in",
    "settings": "Settings",
    "rules": "Ticket rules",
    "reports": "Reports",
    "backup": "Backup",
    "scripts": "Scripts",
    "ipam": "IP addresses",
    "action1": "Action1",
    "meraki": "Meraki",
    "devices": "Devices",
    "changes": "Changes",
    "tickets": "Tickets",
    "kb": "Knowledge base",
    "surveys": "Surveys",
    "worker": "Worker",
    "server": "Server",
}
# Logger name prefix -> source, most specific first.
_BY_LOGGER = [
    ("app.directory", "directory"),
    ("app.api.directory", "directory"),
    ("app.mail", "email"),
    ("app.kerberos", "windows_sign_in"),
    ("app.api.auth", "windows_sign_in"),
    ("app.auth", "sign_in"),
    ("app.configstore", "settings"),
    ("app.api.settings", "settings"),
    ("app.rules", "rules"),
    ("app.reports", "reports"),
    ("app.api.reports", "reports"),
    ("app.backup", "backup"),
    ("app.runs", "scripts"),
    ("app.api.runs", "scripts"),
    ("app.worker", "worker"),
]


def source_for(record: logging.LogRecord) -> str:
    explicit = getattr(record, "source", None)
    if explicit in SOURCES:
        return explicit
    return next((src for prefix, src in _BY_LOGGER if record.name.startswith(prefix)), "server")


class DatabaseHandler(logging.Handler):
    """Writes each record in its own short transaction. Never raises into the caller."""

    def __init__(self, engine: Engine, process: str, level: int = logging.INFO):
        super().__init__(level)
        self.engine = engine
        self.process = process

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = "error" if record.levelno >= logging.ERROR else "warning" if record.levelno >= logging.WARNING else "info"
            details = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None
            with self.engine.begin() as conn:
                conn.execute(
                    insert(SystemEvent).values(
                        level=level, source=source_for(record), message=record.getMessage()[:4000], details=details, process=self.process
                    )
                )
        except Exception:
            # The database may be the thing that's down; stderr still has it.
            pass


def install(engine: Engine, process: str) -> DatabaseHandler:
    app_log = logging.getLogger("app")
    app_log.setLevel(logging.INFO)
    for h in list(app_log.handlers):
        if isinstance(h, DatabaseHandler):
            app_log.removeHandler(h)
    handler = DatabaseHandler(engine, process)
    app_log.addHandler(handler)
    return handler


def prune(session: Session, now: datetime) -> int:
    return session.execute(delete(SystemEvent).where(SystemEvent.at < now - timedelta(days=LOG_DAYS))).rowcount


def beat(session: Session, name: str, now: datetime, **info) -> None:
    row = session.get(ServiceHeartbeat, name)
    if row is None:
        row = ServiceHeartbeat(name=name, last_seen=now, info={})
        session.add(row)
    row.last_seen = now
    row.info = {**row.info, **info}


# Status

# A worker that hasn't beaten for this long is taken to be down. Generous, as a long job
# (a big directory sync) holds the heartbeat back.
WORKER_LATE = timedelta(minutes=3)


def worker_late(beat: ServiceHeartbeat, now: datetime) -> bool:
    return now - beat.last_seen > WORKER_LATE


def _item(key: str, label: str, state: str, summary: str, at: datetime | None = None) -> dict:
    return {"key": key, "label": label, "state": state, "summary": summary, "at": at}


def status(session: Session) -> list[dict]:
    """ok, warning, error or off for each part, with a one-line summary."""
    from app import kerberos

    now = sla.utcnow()
    items = []
    version = session.scalar(text("SHOW server_version"))
    items.append(_item("database", "Database", "ok", f"PostgreSQL {version}"))

    worker = session.get(ServiceHeartbeat, "worker")
    if worker is None:
        items.append(_item("worker", "Background worker", "error", "Has never run: start the worker service (python -m app.worker)"))
    elif worker_late(worker, now):
        items.append(_item("worker", "Background worker", "error", "Not checking in: is the worker service running?", worker.last_seen))
    else:
        items.append(_item("worker", "Background worker", "ok", f"Running on {worker.info.get('host', '?')}", worker.last_seen))

    if not settings.ldap_url:
        items.append(_item("directory", "Directory sync", "off", "No directory: local accounts only"))
    else:
        run = session.scalar(select(DirectorySyncRun).where(DirectorySyncRun.dry_run.is_(False)).order_by(DirectorySyncRun.id.desc()).limit(1))
        if run is None:
            items.append(_item("directory", "Directory sync", "warning", "Hasn't synced yet"))
        elif run.error:
            items.append(_item("directory", "Directory sync", "error", f"Last sync failed: {run.error}", run.started_at))
        else:
            items.append(_item("directory", "Directory sync", "ok", f"{run.users_seen} users seen, {run.created} new, {run.deactivated} deactivated", run.started_at))

    from app.mail import graph

    if not settings.mailbox:
        items.append(_item("email", "Email", "off", "No mailbox set"))
    elif graph.missing(settings):
        items.append(_item("email", "Email", "warning", "Still needed: " + ", ".join(graph.missing(settings))))
    else:
        job = session.get(WorkerJob, "email")
        if job is not None and job.last_ok is False:
            items.append(_item("email", "Email", "error", f"Last check failed: {job.last_error}", job.last_finished_at))
        elif job is None or job.last_finished_at is None:
            items.append(_item("email", "Email", "warning", f"{settings.mailbox}: not checked yet"))
        elif job.paused:
            items.append(_item("email", "Email", "warning", f"{settings.mailbox}: paused on Settings > Status", job.last_finished_at))
        else:
            items.append(_item("email", "Email", "ok", f"{settings.mailbox}: {job.last_summary or 'checked'}", job.last_finished_at))

    config = kerberos.load(session)
    if not config.enabled:
        items.append(_item("windows_sign_in", "Windows sign-in", "off", "Off"))
    else:
        problem = kerberos.keytab_problem() if kerberos.keytab_path().exists() else "The keytab file is missing"
        items.append(
            _item("windows_sign_in", "Windows sign-in", "error" if problem else "ok", problem or f"On for {config.realm}" + (", automatic" if config.silent else ""))
        )

    path = settings.storage_dir.resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    usage = shutil.disk_usage(path)
    free = usage.free / usage.total if usage.total else 1
    gb = usage.free / 1024**3
    items.append(
        _item("disk", "Attachment storage", "error" if free < 0.05 else "warning" if free < 0.15 else "ok", f"{gb:.1f} GB free ({free:.0%})")
    )

    counts = counts_by_level(session, now - timedelta(hours=24))
    errors, warnings = counts.get("error", 0), counts.get("warning", 0)
    items.append(
        _item("log", "Last 24 hours", "error" if errors else "warning" if warnings else "ok", f"{errors} error(s), {warnings} warning(s) in the log below")
    )
    return items


def events(session: Session, source: str | None, level: str | None, before_id: int | None, limit: int) -> list[SystemEvent]:
    stmt = select(SystemEvent).order_by(SystemEvent.id.desc()).limit(limit)
    if source:
        stmt = stmt.where(SystemEvent.source == source)
    if level == "warning":
        stmt = stmt.where(SystemEvent.level.in_(["warning", "error"]))
    elif level == "error":
        stmt = stmt.where(SystemEvent.level == "error")
    if before_id:
        stmt = stmt.where(SystemEvent.id < before_id)
    return list(session.scalars(stmt))


def counts_by_level(session: Session, since: datetime) -> dict[str, int]:
    rows = session.execute(select(SystemEvent.level, func.count()).where(SystemEvent.at >= since).group_by(SystemEvent.level))
    return {level: n for level, n in rows}
