"""Settings > Log: the database log handler, the worker heartbeat and the status summary."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import configstore, sla, syslog
from app.config import settings
from app.models import DirectorySyncRun, SystemEvent, WorkerJob
from tests.conftest import as_user


@pytest.fixture
def handler(engine):
    h = syslog.install(engine, "api")
    yield h
    logging.getLogger("app").removeHandler(h)


def events(db):
    db.expire_all()
    return db.scalars(select(SystemEvent).order_by(SystemEvent.id)).all()


def test_app_log_records_land_in_the_log_with_a_source(db, handler):
    logging.getLogger("app.directory.sync").warning("Couldn't reach %s", "dc1.corp.example")
    logging.getLogger("app.worker").info("Inbox: 2 processed", extra={"source": "email"})
    logging.getLogger("app.auth").debug("not kept")  # below info
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("app.somewhere").exception("Something broke")
    rows = events(db)
    assert [(e.level, e.source, e.message) for e in rows] == [
        ("warning", "directory", "Couldn't reach dc1.corp.example"),
        ("info", "email", "Inbox: 2 processed"),
        ("error", "server", "Something broke"),
    ]
    assert "RuntimeError: boom" in rows[2].details and rows[0].process == "api"


def test_settings_changes_are_logged_without_values(client, db, admin, handler):
    client.put("/api/settings/directory", json={"values": {"ldap_bind_password": "Top-secret-1", "ldap_sync_minutes": 60}}, headers=as_user(admin))
    (event,) = [e for e in events(db) if e.source == "settings"]
    assert event.message == f"Directory settings changed by {admin.display_name}: Service account password, Sync every (minutes)"
    assert "Top-secret-1" not in event.message


def test_log_api_filters_and_pages(client, db, admin, agent, handler):
    for i in range(5):
        logging.getLogger("app.directory").info("sync %s", i)
    logging.getLogger("app.mail.graph").error("Graph down")
    r = client.get("/api/system/log", params={"limit": 3}, headers=as_user(admin))
    assert [e["message"] for e in r.json()] == ["Graph down", "sync 4", "sync 3"]
    older = client.get("/api/system/log", params={"before_id": r.json()[-1]["id"], "source": "directory"}, headers=as_user(admin)).json()
    assert [e["message"] for e in older] == ["sync 2", "sync 1", "sync 0"]
    assert [e["message"] for e in client.get("/api/system/log", params={"level": "warning"}, headers=as_user(admin)).json()] == ["Graph down"]
    assert {s["value"] for s in client.get("/api/system/log/sources", headers=as_user(admin)).json()} >= {"directory", "email", "windows_sign_in"}
    assert client.get("/api/system/log", headers=as_user(agent)).status_code == 403
    assert client.get("/api/system/status", headers=as_user(agent)).status_code == 403


def status(client, admin):
    r = client.get("/api/system/status", headers=as_user(admin))
    assert r.status_code == 200, r.text
    return {i["key"]: i for i in r.json()}


def test_status_summary(client, db, admin, monkeypatch):
    s = status(client, admin)
    assert s["database"]["state"] == "ok" and s["database"]["summary"].startswith("PostgreSQL")
    assert s["worker"]["state"] == "error" and "never run" in s["worker"]["summary"]
    assert (s["directory"]["state"], s["email"]["state"], s["windows_sign_in"]["state"]) == ("off", "off", "off")
    assert s["disk"]["state"] in ("ok", "warning", "error") and "GB free" in s["disk"]["summary"]

    now = sla.utcnow()
    syslog.beat(db, "worker", now, host="helpdesk")
    db.add(WorkerJob(name="email", last_finished_at=now, last_ok=False, last_error="Graph sign-in failed: AADSTS7000215"))
    monkeypatch.setattr(settings, "ldap_url", "ldaps://dc1.corp.example")
    configstore.update(db, "email", {"mailbox": "helpdesk@example.com", "graph_tenant_id": "11111111-2222-3333-4444-555555555555",
                                     "graph_client_id": "11111111-2222-3333-4444-555555555555", "graph_client_secret": "x"}, admin)
    db.add(DirectorySyncRun(started_at=now, details=[], error="Can't contact LDAP server", dry_run=False))
    db.commit()
    s = status(client, admin)
    assert s["worker"]["state"] == "ok"
    assert s["directory"]["state"] == "error" and "Can't contact LDAP server" in s["directory"]["summary"]
    assert s["email"]["state"] == "error" and "AADSTS7000215" in s["email"]["summary"]

    # A worker that stopped checking in.
    syslog.beat(db, "worker", now - timedelta(minutes=30))
    db.commit()
    assert status(client, admin)["worker"]["state"] == "error"


def test_housekeeping_prunes_old_entries(db, session_factory):
    from app import worker

    db.add(SystemEvent(level="info", source="server", message="ancient", process="api", at=sla.utcnow() - timedelta(days=syslog.LOG_DAYS + 1)))
    db.add(SystemEvent(level="info", source="server", message="recent", process="api"))
    db.commit()
    worker.make_worker(session_factory).tick(run_all=True)
    assert [e.message for e in events(db)] == ["recent"]
