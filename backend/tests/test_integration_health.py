# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import timedelta

import pytest

from app import integration_health as health, jobs, sla
from app.config import settings
from app.models import DirectorySyncRun, Runner, ServiceHeartbeat, WorkerJob
from tests.conftest import as_user


def sample_job(now, **changes):
    values = dict(name="email", paused=False, last_started_at=now - timedelta(seconds=10),
                  last_finished_at=now - timedelta(seconds=5), last_success_at=now - timedelta(seconds=5),
                  last_ok=True, next_due_at=now + timedelta(seconds=25), last_summary="Mailbox checked")
    return WorkerJob(**(values | changes))


def item(now, job, **changes):
    args = dict(key="email", label="Email", enabled=True, missing=[], path="/settings/email", job=job,
                worker_up=True, now=now, interval=timedelta(seconds=30))
    return health.job_health(**(args | changes))


@pytest.mark.parametrize("over,expected", [
    ({}, "ok"), ({"enabled": False}, "off"), ({"missing": ["client secret"]}, "warning"),
    ({"worker_up": False}, "error"),
])
def test_health_uses_local_evidence(over, expected):
    now = sla.utcnow()
    assert item(now, sample_job(now), **over).state == expected


def test_job_health_distinguishes_paused_failed_overdue_running_and_unknown():
    now = sla.utcnow()
    assert item(now, None).state == "unknown"
    assert item(now, sample_job(now, paused=True)).state == "warning"
    assert item(now, sample_job(now, last_ok=False, last_error="secret-url-and-token")).state == "error"
    assert "secret-url-and-token" not in item(now, sample_job(now, last_ok=False, last_error="secret-url-and-token")).model_dump_json()
    assert item(now, sample_job(now, last_finished_at=now - timedelta(hours=1))).state == "warning"
    assert item(now, sample_job(now, last_started_at=now + timedelta(seconds=1))).state == "warning"
    assert item(now, sample_job(now, last_summary="Off: not configured")).state == "warning"


def test_actual_read_never_calls_a_queued_or_missing_read_healthy():
    now = sla.utcnow()
    result = item(now, sample_job(now))
    health._actual_read(result, False, False, None, now, timedelta(minutes=30))
    assert result.state == "unknown"
    result = item(now, sample_job(now))
    health._actual_read(result, True, False, None, now, timedelta(minutes=30))
    assert result.state == "warning"
    result = item(now, sample_job(now))
    health._actual_read(result, True, True, now, now, timedelta(minutes=30))
    assert result.state == "error"


def test_api_is_admin_only(client, admin, agent, requester):
    for user in (agent, requester):
        assert client.get("/api/system/integrations", headers=as_user(user)).status_code == 403
    assert client.get("/api/system/integrations", headers=as_user(admin)).status_code == 200


def test_snapshot_masks_secrets_tracks_relays_and_reads_actual_directory_history(client, db, admin, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    request = httpx.Client.request
    monkeypatch.setattr(httpx.Client, "request", lambda self, *a, **k: request(self, *a, **k) if isinstance(self, TestClient) else pytest.fail("Health must not contact providers"))
    monkeypatch.setattr(settings, "mailbox", "desk@corp.example")
    for name in ("graph_tenant_id", "graph_client_id", "graph_client_secret"):
        monkeypatch.setattr(settings, name, "private-value-should-not-escape")
    monkeypatch.setattr(settings, "ldap_url", "ldaps://dc.corp.example")
    monkeypatch.setattr(settings, "ldap_base_dn", "DC=corp,DC=example")
    now = sla.utcnow()
    db.add_all([
        ServiceHeartbeat(name="worker", last_seen=now, info={}),
        sample_job(now),
        WorkerJob(name="directory", last_ok=True, paused=False, last_finished_at=now, last_success_at=now),
        DirectorySyncRun(started_at=now-timedelta(hours=1), finished_at=now-timedelta(minutes=59), dry_run=False),
        DirectorySyncRun(started_at=now, finished_at=now, dry_run=False, error="provider error with private-value-should-not-escape"),
        Runner(name="Online relay", token_hash="one", is_active=True, last_seen_at=now),
        Runner(name="Late relay", token_hash="two", is_active=True, last_seen_at=now-timedelta(minutes=4)),
        Runner(name="New relay", token_hash="three", is_active=True),
        Runner(name="Disabled relay", token_hash="four", is_active=False),
    ])
    db.commit()
    response = client.get("/api/system/integrations", headers=as_user(admin))
    assert response.status_code == 200
    assert "private-value-should-not-escape" not in response.text
    body = response.json()
    assert body["worker_state"] == "running"
    assert {r["name"]: r["state"] for r in body["relays"]} == {"Online relay": "ok", "Late relay": "error", "New relay": "warning"}
    directory = next(i for i in body["items"] if i["key"] == "directory")
    assert directory["state"] == "error" and directory["last_success_at"] is not None
    assert next(i for i in body["items"] if i["key"] == "email")["state"] == "ok"
    assert next(i for i in body["items"] if i["key"] == "nutanix")["state"] == "off"


def test_worker_keeps_last_success_after_failure(db, session_factory):
    worker = jobs.Worker(session_factory, [])
    worker.run_job(jobs.Job("email", "email", lambda: timedelta(seconds=30), lambda forced: ("Read mail", False)), False)
    db.expire_all()
    before = db.get(WorkerJob, "email").last_success_at
    assert before is not None
    def fail(forced):
        raise RuntimeError("Example failure")
    worker.run_job(jobs.Job("email", "email", lambda: timedelta(seconds=30), fail), False)
    db.expire_all()
    row = db.get(WorkerJob, "email")
    assert row.last_ok is False and row.last_success_at == before
