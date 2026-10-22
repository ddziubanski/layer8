"""Execution stays tied to the reviewed version and live authorization."""
import pytest

from app.models import ScriptRun, UserRole
from tests.conftest import as_user, make_user
from tests.test_script_hardening import BODY, writer
from tests.test_runs import bearer, runner


def make_script(client, admin, **over):
    result = client.post("/api/scripts", json={**BODY, **over}, headers=as_user(admin))
    assert result.status_code == 201, result.text
    return result.json()["id"]


def queue(client, script_id, user):
    result = client.post(f"/api/scripts/{script_id}/runs", json={"on_runner": True}, headers=as_user(user))
    assert result.status_code == 201, result.text
    return result.json()[0]["id"]


@pytest.mark.parametrize("change", [{"body": "echo changed"}, {"high_risk": True}, {"agents_can_run": False}, {"is_active": False}])
def test_changes_cancel_unclaimed_runs_and_clear_secrets(client, db, admin, agent, change):
    sid = make_script(client, admin)
    rid = queue(client, sid, agent)
    stored = db.get(ScriptRun, rid)
    stored.sealed = b"never disclosed"
    db.commit()
    response = client.put(f"/api/scripts/{sid}", json={**BODY, **change}, headers=as_user(admin))
    assert response.status_code == 200, response.text
    db.refresh(stored)
    assert stored.status == "cancelled" and stored.sealed is None and stored.finished_at is not None
    token = runner(client, admin)
    assert client.post("/api/runner/claim", json={}, headers=bearer(token)).status_code == 204


def test_interpreter_and_review_provenance_survive_restore(client, db, admin, writer):
    sid = make_script(client, admin)
    first = client.get(f"/api/scripts/{sid}/versions/1", headers=as_user(admin)).json()
    assert first["language"] == "bash" and first["reviewed_by"]["id"] == admin.id
    changed = {**BODY, "language": "python", "body": "print('example')"}
    assert client.put(f"/api/scripts/{sid}", json=changed, headers=as_user(writer)).status_code == 200
    unreviewed = client.get(f"/api/scripts/{sid}/versions/2", headers=as_user(admin)).json()
    assert unreviewed["reviewed_at"] is None
    assert client.put(f"/api/scripts/{sid}", json=changed, headers=as_user(admin)).status_code == 200
    reviewed = client.get(f"/api/scripts/{sid}/versions/2", headers=as_user(admin)).json()
    assert reviewed["reviewed_by"]["id"] == admin.id
    other = make_user(db, "reviewer@example.com", UserRole.admin)
    client.put(f"/api/scripts/{sid}", json={**changed, "description": "Metadata"}, headers=as_user(other))
    assert client.get(f"/api/scripts/{sid}/versions/2", headers=as_user(admin)).json()["reviewed_at"] == reviewed["reviewed_at"]
    restored = client.post(f"/api/scripts/{sid}/versions/1/restore", headers=as_user(admin))
    assert restored.json()["language"] == "bash"
    assert client.get(f"/api/scripts/{sid}/versions/1", headers=as_user(admin)).json()["reviewed_at"] == first["reviewed_at"]
    token = runner(client, admin, languages=("bash",))
    rid = queue(client, sid, admin)
    job = client.post("/api/runner/claim", json={}, headers=bearer(token)).json()
    assert job["run_id"] == rid and job["language"] == "bash" and job["body"] == BODY["body"]


def test_author_cannot_approve_someone_elses_run_of_their_code(client, db, admin, agent):
    sid = make_script(client, admin, high_risk=True)
    rid = queue(client, sid, agent)
    result = client.post(f"/api/runs/{rid}/approve", headers=as_user(admin))
    assert result.status_code == 422 and "code you authored" in result.json()["detail"]
    other = make_user(db, "reviewer@example.com", UserRole.admin)
    assert client.post(f"/api/runs/{rid}/approve", headers=as_user(other)).status_code == 200


def test_claim_rechecks_requester_and_skips_revoked_run(client, db, admin, agent):
    sid = make_script(client, admin)
    stale = queue(client, sid, agent)
    user = db.merge(agent)
    user.is_active = False
    db.commit()
    valid = queue(client, sid, admin)
    token = runner(client, admin)
    assert client.post("/api/runner/claim", json={}, headers=bearer(token)).json()["run_id"] == valid
    db.expire_all()
    assert db.get(ScriptRun, stale).status == "cancelled"


def test_scheduled_discovery_service_identity_can_still_run(client, db, admin):
    from app import ipamscan
    from app.models import Subnet
    subnet = Subnet(network="10.7.0.0/24", name="Scheduled discovery", scan_every_hours=12)
    db.add(subnet)
    db.commit()
    assert ipamscan.run_due(db) == 1
    db.commit()
    token = runner(client, admin, languages=("python",))
    job = client.post("/api/runner/claim", json={}, headers=bearer(token))
    assert job.status_code == 200, job.text
    assert job.json()["values"]["network"] == "10.7.0.0/24"
