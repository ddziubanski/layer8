# SPDX-License-Identifier: AGPL-3.0-or-later
"""Script schedules: actions queued on a timetable (devices, a dynamic group, the runner), queries emailed as CSV."""

from datetime import date, datetime, timedelta

import pytest

from app import scriptschedules
from app.config import settings
from app.models import Device, OutboundEmail, RunStatus, ScriptRun, ScriptSchedule, UserRole
from tests.conftest import as_user, make_user
from tests.test_runs import script


@pytest.fixture
def devices(db):
    pcs = [Device(name="WS-01"), Device(name="WS-02"), Device(name="SRV-01")]
    db.add_all(pcs)
    db.commit()
    return pcs


def schedule(client, user, **over):
    body = {"name": "Nightly", "frequency": "daily", "time_of_day": "02:00", "start_date": date.today().isoformat(), **over}
    return client.post("/api/script-schedules", json=body, headers=as_user(user))


def test_action_schedule_queues_runs_as_its_owner(client, db, session_factory, admin, agent, devices):
    s = script(client, admin)
    group = client.post(
        "/api/scripts", json={"name": "Workstations", "kind": "query", "language": "sql", "body": "SELECT id AS device_id FROM devices WHERE name LIKE 'WS-%'", "agents_can_run": True},
        headers=as_user(admin),
    ).json()
    r = schedule(client, agent, script_id=s["id"], group_script_id=group["id"], values={"who": "night"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["script_name"] == "Echo" and out["group_name"] == "Workstations" and out["owner"]["id"] == agent.id and out["next_run_at"]

    # Due: the group is worked out at the run, so a device added since counts.
    db.add(Device(name="WS-03"))
    db.commit()
    when = datetime.fromisoformat(out["next_run_at"]) + timedelta(seconds=1)
    assert scriptschedules.run_due(session_factory, when) == (1, 0)
    made = db.query(ScriptRun).filter_by(schedule_id=out["id"]).all()
    assert sorted(r.target for r in made) == ["WS-01", "WS-02", "WS-03"]
    assert {r.requested_by_id for r in made} == {agent.id} and {r.status for r in made} == {RunStatus.queued} and made[0].values == {"who": "night"}
    after = client.get("/api/script-schedules", headers=as_user(agent)).json()[0]
    assert after["last_result"] == "Queued on 3 device(s)" and len(after["recent_runs"]) == 3
    assert datetime.fromisoformat(after["next_run_at"]) - datetime.fromisoformat(out["next_run_at"]) == timedelta(days=1)
    # Not again until then.
    assert scriptschedules.run_due(session_factory, when + timedelta(minutes=5)) == (0, 0)

    # Run now leaves the timetable alone.
    r = client.post(f"/api/script-schedules/{out['id']}/run", headers=as_user(agent))
    assert r.status_code == 200 and r.json()["next_run_at"] == after["next_run_at"]
    assert db.query(ScriptRun).filter_by(schedule_id=out["id"]).count() == 6


def test_schedule_rules(client, db, admin, agent, devices):
    s = script(client, admin)
    hidden = script(client, admin, name="Admins only", agents_can_run=False)
    assert "Pick the devices" in schedule(client, agent, script_id=s["id"]).json()["detail"]
    assert "Pick the devices" in schedule(client, agent, script_id=s["id"], on_runner=True, device_ids=[devices[0].id]).json()["detail"]
    assert "can't run" in schedule(client, agent, script_id=hidden["id"], on_runner=True).json()["detail"]
    assert "day of the week" in schedule(client, agent, script_id=s["id"], on_runner=True, frequency="weekly", weekdays=[]).json()["detail"]
    assert "never runs" in schedule(client, agent, script_id=s["id"], on_runner=True, frequency="once", start_date="2020-01-01").json()["detail"]
    mine = schedule(client, agent, script_id=s["id"], on_runner=True).json()
    # Only its owner (or an admin) sees and changes it.
    other = make_user(db, "agent2@corp.local", UserRole.agent, "Other Agent")
    db.commit()
    assert client.get("/api/script-schedules", headers=as_user(other)).json() == []
    assert client.delete(f"/api/script-schedules/{mine['id']}", headers=as_user(other)).status_code == 404
    assert [x["id"] for x in client.get("/api/script-schedules", headers=as_user(admin)).json()] == [mine["id"]]
    assert client.delete(f"/api/script-schedules/{mine['id']}", headers=as_user(agent)).status_code == 204


def test_failures_are_kept_and_high_risk_waits(client, db, session_factory, admin, agent, devices):
    risky = script(client, admin, name="Reboot", high_risk=True)
    out = schedule(client, admin, script_id=risky["id"], device_ids=[devices[2].id]).json()
    when = datetime.fromisoformat(out["next_run_at"]) + timedelta(seconds=1)
    assert scriptschedules.run_due(session_factory, when) == (1, 0)
    run = db.query(ScriptRun).filter_by(schedule_id=out["id"]).one()
    assert run.status == RunStatus.awaiting_approval
    assert "waiting for an admin's approval" in db.get(ScriptSchedule, out["id"]).last_result

    # The owner leaves: the next run fails, says why, and the timetable carries on.
    db.get(type(admin), admin.id).is_active = False
    db.commit()
    assert scriptschedules.run_due(session_factory, when + timedelta(days=1)) == (0, 1)
    db.expire_all()
    s = db.get(ScriptSchedule, out["id"])
    assert s.last_error == "Its owner is no longer an active user" and s.next_run_at > when + timedelta(days=1)


def test_query_schedule_emails_csv(client, db, session_factory, admin, devices, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@example.com")
    q = client.post(
        "/api/scripts", json={"name": "Servers", "kind": "query", "language": "sql", "body": "SELECT name FROM devices WHERE name LIKE 'SRV-%'", "agents_can_run": True},
        headers=as_user(admin),
    ).json()
    assert "recipient" in schedule(client, admin, script_id=q["id"]).json()["detail"]
    out = schedule(client, admin, script_id=q["id"], name="Server list", recipients=[admin.email], frequency="weekly", weekdays=[0], message="Weekly list").json()
    when = datetime.fromisoformat(out["next_run_at"]) + timedelta(seconds=1)
    assert scriptschedules.run_due(session_factory, when) == (1, 0)
    mail = db.query(OutboundEmail).one()
    assert mail.to_address == admin.email and mail.subject == "Report: Server list" and mail.body.startswith("Weekly list")
    assert mail.attachments[0]["filename"].startswith("server-list-") and mail.attachments[0]["content_type"] == "text/csv"
    assert db.get(ScriptSchedule, out["id"]).last_result == "Emailed 1 row(s) to 1 recipient(s)"
    assert datetime.fromisoformat(out["next_run_at"]).astimezone().weekday() == 0
