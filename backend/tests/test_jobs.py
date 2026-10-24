"""The background worker's jobs and Settings > Status: run now, pause, restart."""

import shlex
import sys
from datetime import timedelta

import pytest

from app import jobs, sla
from app.config import settings
from app.models import ServiceHeartbeat, ServiceRequest, WorkerJob
from tests.conftest import as_user


class Recorder:
    """Stand-in jobs that note when they ran."""

    def __init__(self):
        self.ran: list[tuple[str, bool]] = []
        self.fail = set()

    def job(self, name, every=60):
        def run(forced):
            self.ran.append((name, forced))
            if name in self.fail:
                raise RuntimeError(f"{name} broke")
            return f"{name} done", False

        return jobs.Job(name, "worker", lambda: timedelta(seconds=every), run)


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def worker(session_factory, rec):
    return jobs.Worker(session_factory, [rec.job("email", 30), rec.job("directory"), rec.job("rules")])


def row(db, name):
    db.expire_all()
    return db.get(WorkerJob, name)


def test_jobs_run_when_due_and_record_how_it_went(db, worker, rec):
    rec.fail.add("directory")
    assert worker.tick() is True
    assert rec.ran == [("email", False), ("directory", False), ("rules", False)]
    email, directory = row(db, "email"), row(db, "directory")
    assert (email.last_ok, email.last_summary, email.last_error) == (True, "email done", None)
    assert (directory.last_ok, directory.last_error) == (False, "RuntimeError: directory broke")
    assert email.next_due_at - email.last_finished_at == timedelta(seconds=30)
    # Not due again yet.
    worker.tick()
    assert len(rec.ran) == 3
    beat = db.get(ServiceHeartbeat, "worker")
    assert beat.info["pid"] > 0 and beat.info["host"]


def test_run_now_and_pause(client, db, admin, worker, rec):
    worker.tick()
    rec.ran.clear()
    r = client.post("/api/system/jobs/email/run", headers=as_user(admin))
    assert r.status_code == 200
    assert next(j for j in r.json()["jobs"] if j["name"] == "email")["run_requested"] is True
    worker.tick()
    assert rec.ran == [("email", True)]
    assert row(db, "email").run_requested_at is None

    client.put("/api/system/jobs/rules", json={"paused": True}, headers=as_user(admin))
    db.query(WorkerJob).update({WorkerJob.next_due_at: sla.utcnow() - timedelta(seconds=1)})
    db.commit()
    rec.ran.clear()
    worker.tick()
    assert ("rules", False) not in rec.ran and ("email", False) in rec.ran
    # Paused jobs still run when asked to.
    client.post("/api/system/jobs/rules/run", headers=as_user(admin))
    worker.tick()
    assert ("rules", True) in rec.ran
    assert client.post("/api/system/jobs/nope/run", headers=as_user(admin)).status_code == 404


def test_restart_is_done_by_the_worker(client, db, admin, worker, rec):
    r = client.post("/api/system/worker/restart", json={}, headers=as_user(admin))
    assert r.status_code == 200
    assert r.json()["worker"]["restart_requested_by"] == admin.display_name
    assert worker.tick() is False  # the worker exits (status 75) and its service manager starts it again
    assert rec.ran == []
    db.expire_all()
    assert db.query(ServiceRequest).one().handled_at is not None
    assert client.get("/api/system/services", headers=as_user(admin)).json()["worker"]["restart_requested_at"] is None


def test_force_restart_runs_the_server_command(client, admin, monkeypatch):
    r = client.post("/api/system/worker/restart", json={"force": True}, headers=as_user(admin))
    assert r.status_code == 409 and "HELPDESK_WORKER_RESTART_COMMAND" in r.json()["detail"]

    monkeypatch.setattr(settings, "worker_restart_command", f"{sys.executable} -c pass")
    services = client.get("/api/system/services", headers=as_user(admin)).json()
    assert services["worker"]["can_force_restart"] is True
    assert client.post("/api/system/worker/restart", json={"force": True}, headers=as_user(admin)).status_code == 200

    failing = [sys.executable, "-c", "import sys; sys.exit('Unit helpdesk-worker.service not found.')"]
    monkeypatch.setattr(settings, "worker_restart_command", shlex.join(failing))
    r = client.post("/api/system/worker/restart", json={"force": True}, headers=as_user(admin))
    assert r.status_code == 502 and "Unit helpdesk-worker.service not found." in r.json()["detail"]


def test_services_overview(client, db, admin, agent):
    s = client.get("/api/system/services", headers=as_user(admin)).json()
    assert s["worker"]["state"] == "never_started"
    assert [j["name"] for j in s["jobs"]] == ["software_deployments", "teams", "presence", "email", "directory", "rules", "reports", "script_schedules", "housekeeping", "backup", "ipam", "meraki", "action1", "sweeps", "warranty"]
    assert s["api"]["pid"] > 0
    db.add(ServiceHeartbeat(name="worker", last_seen=sla.utcnow() - timedelta(minutes=10), info={}))
    db.add(WorkerJob(name="directory", last_started_at=sla.utcnow(), last_finished_at=sla.utcnow() - timedelta(minutes=1)))
    db.commit()
    s = client.get("/api/system/services", headers=as_user(admin)).json()
    assert s["worker"]["state"] == "not_responding"
    assert next(j for j in s["jobs"] if j["name"] == "directory")["running"] is True
    assert client.get("/api/system/services", headers=as_user(agent)).status_code == 403
    assert client.post("/api/system/worker/restart", json={}, headers=as_user(agent)).status_code == 403


def test_real_jobs_run_once(db, session_factory):
    from app import worker

    w = worker.make_worker(session_factory)
    assert w.tick(run_all=True) is True
    summaries = {name: row(db, name).last_summary for name in jobs.LABELS}
    assert summaries["email"] == "Off: no mailbox set" and summaries["directory"] == "Off: no directory"
    assert summaries["rules"] == "No rules due" and summaries["reports"] == "No reports due"
    assert all(row(db, n).last_ok for n in jobs.LABELS)
