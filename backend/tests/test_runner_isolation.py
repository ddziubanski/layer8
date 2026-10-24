# SPDX-License-Identifier: AGPL-3.0-or-later
"""Privileged integration runners never consume general or unpinned work."""
from app import adchanges
from app.models import ScriptRun
from tests.conftest import as_user
from tests.test_runs import bearer
from tests.test_version_execution import make_script, queue


def dedicated(client, admin, **over):
    response = client.post("/api/runners", json={"name": "AD integration", "languages": ["powershell", "bash"], "builtins_only": True, **over}, headers=as_user(admin))
    assert response.status_code == 201, response.text
    return response.json()


def test_dedicated_runner_only_claims_explicit_builtin_work(client, db, admin, agent):
    made = dedicated(client, admin)
    runner_id = made["runner"]["id"]
    sid = make_script(client, admin)
    queue(client, sid, agent)
    built = adchanges.ensure_script(db)
    # Construct dispatch fixtures directly; feature-level parameter checks have their own tests.
    unpinned = ScriptRun(script_id=built.id, version_id=built.versions[0].id, values={}, requested_by_id=admin.id, status="queued")
    db.add(unpinned)
    pinned = ScriptRun(script_id=built.id, version_id=built.versions[0].id, values={}, requested_by_id=admin.id, status="queued", only_runner_id=runner_id)
    db.add(pinned)
    db.commit()
    job = client.post("/api/runner/claim", json={}, headers=bearer(made["token"]))
    assert job.status_code == 200, job.text
    assert job.json()["run_id"] == pinned.id
    assert client.post("/api/runner/claim", json={}, headers=bearer(made["token"])).status_code == 204
    db.expire_all()
    assert db.get(ScriptRun, unpinned.id).status == "queued"
    preview = client.post(f"/api/scripts/{sid}/preview", json={"on_runner": True}, headers=as_user(agent)).json()
    assert preview["runners"] == []


def test_removing_selected_runner_cancels_pinned_work(client, db, admin):
    made = dedicated(client, admin)
    sid = make_script(client, admin)
    rid = queue(client, sid, admin)
    stored = db.get(ScriptRun, rid)
    stored.only_runner_id, stored.sealed = made["runner"]["id"], b"sealed test data"
    db.commit()
    response = client.delete(f"/api/runners/{made['runner']['id']}", headers=as_user(admin))
    assert response.status_code == 204, response.text
    db.expire_all()
    stored = db.get(ScriptRun, rid)
    assert stored.status == "cancelled" and stored.sealed is None
    assert stored.only_runner_id is None


def test_runner_scope_is_admin_only_and_round_trips(client, admin, agent):
    body = {"name": "Privileged", "languages": ["powershell"], "builtins_only": True}
    assert client.post("/api/runners", json=body, headers=as_user(agent)).status_code == 403
    made = dedicated(client, admin)
    assert made["runner"]["builtins_only"] is True
    assert client.get("/api/runners", headers=as_user(admin)).json()[0]["builtins_only"] is True
    # An older API client omitting the new field must not broaden the runner's scope.
    updated = client.put(f"/api/runners/{made['runner']['id']}", json={"name": "Renamed", "languages": ["powershell"]}, headers=as_user(admin))
    assert updated.status_code == 200 and updated.json()["builtins_only"] is True
    saved = client.put("/api/settings/directory", json={"values": {"ad_runner_name": "Renamed"}}, headers=as_user(admin))
    assert saved.status_code == 200, saved.text
    fields = client.get("/api/settings/directory", headers=as_user(admin)).json()["fields"]
    assert any(field["name"] == "ad_runner_name" and field["value"] == "Renamed" for field in fields)


def test_naming_the_ad_runner_moves_pending_ad_jobs_to_it(client, db, admin):
    from tests.test_version_execution import dated_disable
    run = dated_disable(db, admin)
    assert run.only_runner_id is None
    made = dedicated(client, admin)
    saved = client.put("/api/settings/directory", json={"values": {"ad_runner_name": "AD integration"}}, headers=as_user(admin))
    assert saved.status_code == 200, saved.text
    db.expire_all()
    assert db.get(ScriptRun, run.id).only_runner_id == made["runner"]["id"]
    job = client.post("/api/runner/claim", json={}, headers=bearer(made["token"]))
    assert job.status_code == 200 and job.json()["run_id"] == run.id
    missing = client.put("/api/settings/directory", json={"values": {"ad_runner_name": "Nowhere"}}, headers=as_user(admin))
    assert missing.status_code == 422 and "No active PowerShell runner" in missing.json()["detail"]


def test_removing_a_runner_keeps_the_original_approver(client, db, admin):
    from app.models import User, UserRole
    from tests.conftest import make_user
    made = dedicated(client, admin)
    sid = make_script(client, admin)
    rid = queue(client, sid, admin)
    approver = make_user(db, "approver@example.com", UserRole.admin)
    stored = db.get(ScriptRun, rid)
    stored.only_runner_id, stored.decided_by_id = made["runner"]["id"], approver.id
    db.commit()
    assert client.delete(f"/api/runners/{made['runner']['id']}", headers=as_user(admin)).status_code == 204
    db.expire_all()
    stored = db.get(ScriptRun, rid)
    assert stored.status == "cancelled" and stored.decided_by_id == approver.id
