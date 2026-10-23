"""Delegated authors must never inherit the execution policy of reviewed code."""
import pytest

from app import permissions
from app.models import Group
from tests.conftest import as_user


@pytest.fixture
def writer(db, agent):
    group = Group(name="Script authors", members=[db.merge(agent)])
    db.add(group)
    db.commit()
    permissions.save(db, {"scripts_manage": {"who": "groups", "group_ids": [group.id]}})
    db.commit()
    return agent


BODY = {"name": "Example action", "kind": "action", "language": "bash", "body": "echo example",
        "parameters": [], "agents_can_run": True, "high_risk": False}


@pytest.mark.parametrize("change", [
    {"body": "echo changed"},
    {"parameters": [{"name": "label", "type": "text", "default": "example"}]},
    {"language": "python"},
])
def test_delegated_executable_edits_revoke_access(client, admin, writer, change):
    made = client.post("/api/scripts", json=BODY, headers=as_user(admin)).json()
    updated = client.put(f"/api/scripts/{made['id']}", json={**BODY, **change}, headers=as_user(writer))
    assert updated.status_code == 200, updated.text
    assert updated.json()["current_version"] == 2
    assert updated.json()["high_risk"] is True
    assert updated.json()["agents_can_run"] is False
    assert updated.json()["can_run"] is False
    assert client.post(f"/api/scripts/{made['id']}/runs", json={"on_runner": True}, headers=as_user(writer)).status_code == 422
    # An admin can explicitly review and re-enable the new version through the existing API.
    reviewed = client.put(f"/api/scripts/{made['id']}", json={**BODY, **change}, headers=as_user(admin))
    assert reviewed.status_code == 200
    assert reviewed.json()["agents_can_run"] is True
    assert reviewed.json()["current_version"] == 2


def test_delegated_restore_revokes_access(client, admin, writer):
    made = client.post("/api/scripts", json=BODY, headers=as_user(admin)).json()
    client.put(f"/api/scripts/{made['id']}", json={**BODY, "body": "echo second"}, headers=as_user(admin))
    restored = client.post(f"/api/scripts/{made['id']}/versions/1/restore", headers=as_user(writer))
    assert restored.status_code == 200
    assert restored.json()["current_version"] == 3
    assert restored.json()["high_risk"] is True
    assert restored.json()["agents_can_run"] is False


def test_metadata_only_edit_preserves_reviewed_policy(client, admin, writer):
    made = client.post("/api/scripts", json=BODY, headers=as_user(admin)).json()
    updated = client.put(f"/api/scripts/{made['id']}", json={**BODY, "description": "Clearer description"}, headers=as_user(writer))
    assert updated.status_code == 200
    assert updated.json()["current_version"] == 1
    assert updated.json()["agents_can_run"] is True
    assert updated.json()["high_risk"] is False
