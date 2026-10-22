"""Settings > Status and Settings > Log: how each part of the system is doing, the background
worker and its jobs (run now, pause, restart), and the system log. Admins only."""

import logging
import os
import shlex
import socket
import subprocess
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from app import jobs, sla, syslog
from app.auth import Admin, SessionDep
from app.config import settings
from app.models import ServiceHeartbeat, WorkerJob
from app.schemas import (
    ApiServiceOut,
    JobOut,
    JobUpdateIn,
    LogSourceOut,
    ServicesOut,
    StatusItemOut,
    SystemEventOut,
    WorkerRestartIn,
    WorkerServiceOut,
)

router = APIRouter(prefix="/api/system", tags=["system"])
log = logging.getLogger(__name__)
STARTED_AT = sla.utcnow()


@router.get("/status", response_model=list[StatusItemOut])
def system_status(_: Admin, session: SessionDep):
    return syslog.status(session)


def _services(session) -> ServicesOut:
    now = sla.utcnow()
    beat = session.get(ServiceHeartbeat, "worker")
    restart = jobs.pending_restart(session)
    info = beat.info if beat else {}
    worker = WorkerServiceOut(
        state="never_started" if beat is None else "not_responding" if syslog.worker_late(beat, now) else "running",
        last_seen=beat.last_seen if beat else None,
        host=info.get("host"),
        pid=info.get("pid"),
        started_at=datetime.fromisoformat(info["started_at"]) if info.get("started_at") else None,
        restart_requested_at=restart.requested_at if restart else None,
        restart_requested_by=restart.requested_by if restart else None,
        can_force_restart=bool(settings.worker_restart_command),
    )
    rows = {r.name: r for r in session.query(WorkerJob).all()}
    out = []
    for name, label in jobs.LABELS.items():
        r = rows.get(name) or WorkerJob(name=name, paused=False)
        running = r.last_started_at is not None and (r.last_finished_at is None or r.last_started_at > r.last_finished_at)
        out.append(
            JobOut(
                name=name, label=label, paused=bool(r.paused), running=running, run_requested=r.run_requested_at is not None,
                last_started_at=r.last_started_at, last_finished_at=r.last_finished_at, last_ok=r.last_ok,
                last_summary=r.last_summary, last_error=r.last_error, next_due_at=r.next_due_at,
            )
        )
    api = ApiServiceOut(host=socket.gethostname(), pid=os.getpid(), started_at=STARTED_AT, version="0.1.0")
    return ServicesOut(api=api, worker=worker, jobs=out)


@router.get("/services", response_model=ServicesOut)
def services(_: Admin, session: SessionDep):
    return _services(session)


def _job(name: str) -> str:
    if name not in jobs.LABELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such job")
    return name


@router.post("/jobs/{name}/run", response_model=ServicesOut)
def run_job_now(name: str, admin: Admin, session: SessionDep):
    jobs.request_run(session, _job(name))
    session.commit()
    log.info("%s: run now asked for by %s", jobs.LABELS[name], admin.display_name, extra={"source": "worker"})
    return _services(session)


@router.put("/jobs/{name}", response_model=ServicesOut)
def update_job(name: str, body: JobUpdateIn, admin: Admin, session: SessionDep):
    jobs.set_paused(session, _job(name), body.paused)
    session.commit()
    log.warning("%s %s by %s", jobs.LABELS[name], "paused" if body.paused else "resumed", admin.display_name, extra={"source": "worker"})
    return _services(session)


@router.post("/worker/restart", response_model=ServicesOut)
def restart_worker(body: WorkerRestartIn, admin: Admin, session: SessionDep):
    if not body.force:
        jobs.request_restart(session, admin.display_name)
        session.commit()
        log.warning("Worker restart asked for by %s", admin.display_name, extra={"source": "worker"})
        return _services(session)
    if not settings.worker_restart_command:
        raise HTTPException(status.HTTP_409_CONFLICT, "No force-restart command is set up on the server (HELPDESK_WORKER_RESTART_COMMAND)")
    log.warning("Worker force-restart by %s: %s", admin.display_name, settings.worker_restart_command, extra={"source": "worker"})
    try:
        done = subprocess.run(shlex.split(settings.worker_restart_command), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("Worker force-restart failed: %s", exc, extra={"source": "worker"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The restart command didn't run: {exc}") from None
    if done.returncode != 0:
        why = (done.stderr or done.stdout).strip()[:500] or f"exit status {done.returncode}"
        log.error("Worker force-restart failed: %s", why, extra={"source": "worker"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The restart command failed: {why}")
    # Any normal restart still waiting is done now.
    pending = jobs.pending_restart(session)
    if pending is not None:
        pending.handled_at = sla.utcnow()
        session.commit()
    return _services(session)


@router.get("/log/sources", response_model=list[LogSourceOut])
def log_sources(_: Admin):
    return [LogSourceOut(value=k, label=v) for k, v in syslog.SOURCES.items()]


@router.get("/log", response_model=list[SystemEventOut])
def system_log(
    _: Admin,
    session: SessionDep,
    source: str | None = None,
    # warning: warnings and errors; error: errors only.
    level: Literal["info", "warning", "error"] | None = None,
    # Older than this event (paging back).
    before_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    return syslog.events(session, source, level, before_id, limit)
