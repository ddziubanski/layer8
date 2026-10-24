# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from app.models import AppSetting
from tests.conftest import as_user

DEFAULT = {"mode": "drawer", "selected_tool": "details", "keep_open": False, "width": 440}


def test_workspace_personal_and_backward_compatible(client, db, agent, admin):
    h = as_user(agent)
    before = client.get("/api/auth/me", headers=h).json()
    assert before["ticket_workspace"] == DEFAULT
    chosen = {**DEFAULT, "selected_tool": "network", "width": 560, "keep_open": True}
    r = client.put("/api/auth/me/preferences", headers=h, json={"ticket_workspace": chosen})
    assert r.status_code == 200, r.text
    assert r.json()["ticket_workspace"] == chosen
    assert r.json()["permissions"] == before["permissions"]
    assert r.json()["features"] == before["features"]
    assert client.get("/api/auth/me", headers=as_user(admin)).json()["ticket_workspace"] == DEFAULT
    r = client.put("/api/auth/me/preferences", headers=h, json={"ticket_color": "#112233", "hidden_modules": ["reports"]})
    assert r.json()["ticket_workspace"] == chosen
    r = client.put("/api/auth/me/preferences", headers=h, json={"ticket_workspace": None})
    assert r.json()["ticket_workspace"] == DEFAULT
    assert r.json()["ticket_color"] == "#112233"
    assert r.json()["hidden_modules"] == ["reports"]
    assert db.get(AppSetting, f"personal_ticket_workspace:{agent.id}").updated_by_id == agent.id


@pytest.mark.parametrize("value", [{"width": 319}, {"width": 721}, {"mode": "unknown"}, {"selected_tool": "admin"}, {"secret": "never"}, []])
def test_workspace_validated(client, agent, value):
    assert client.put("/api/auth/me/preferences", headers=as_user(agent), json={"ticket_workspace": value}).status_code == 422


def test_malformed_workspace_does_not_block_sign_in(client, db, agent):
    db.add(AppSetting(key=f"personal_ticket_workspace:{agent.id}", value={"width": "broken"}))
    db.commit()
    assert client.get("/api/auth/me", headers=as_user(agent)).json()["ticket_workspace"] == DEFAULT


def test_requester_cannot_change_staff_workspace_atomically(client, requester):
    h = as_user(requester)
    r = client.put("/api/auth/me/preferences", headers=h, json={"ticket_color": "#112233", "ticket_workspace": DEFAULT})
    assert r.status_code == 403
    assert client.get("/api/auth/me", headers=h).json()["ticket_color"] is None
