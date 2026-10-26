# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from app.models import AppSetting
from tests.conftest import as_user

DEFAULT = {"palette": None, "mode": "organization"}


def test_personal_appearance_isolated_preserved_and_reset(client, db, agent, admin):
    headers = as_user(agent)
    before = client.get("/api/auth/me", headers=headers).json()
    assert before["console_appearance"] == DEFAULT
    chosen = {"palette": "graphite-lime", "mode": "dark"}
    response = client.put("/api/auth/me/preferences", headers=headers, json={"console_appearance": chosen})
    assert response.status_code == 200
    assert response.json()["console_appearance"] == chosen
    assert response.json()["permissions"] == before["permissions"]
    assert client.get("/api/auth/me", headers=as_user(admin)).json()["console_appearance"] == DEFAULT
    assert client.put("/api/auth/me/preferences", headers=headers, json={"ticket_color": "#112233"}).json()["console_appearance"] == chosen
    assert db.get(AppSetting, f"personal_console_appearance:{agent.id}").updated_by_id == agent.id
    assert client.put("/api/auth/me/preferences", headers=headers, json={"console_appearance": None}).json()["console_appearance"] == DEFAULT


@pytest.mark.parametrize("value", [{"palette": "midnight"}, {"mode": "nope"}, {"css": "url(bad)"}, []])
def test_appearance_validated(client, agent, value):
    assert client.put("/api/auth/me/preferences", headers=as_user(agent), json={"console_appearance": value}).status_code == 422


def test_requester_cannot_change_console_appearance(client, requester):
    response = client.put("/api/auth/me/preferences", headers=as_user(requester), json={"ticket_color": "#112233", "console_appearance": DEFAULT})
    assert response.status_code == 403
    assert client.get("/api/auth/me", headers=as_user(requester)).json()["ticket_color"] is None


def test_damaged_preference_falls_back(client, db, agent):
    db.add(AppSetting(key=f"personal_console_appearance:{agent.id}", value={"palette": "bogus"}))
    db.commit()
    assert client.get("/api/auth/me", headers=as_user(agent)).json()["console_appearance"] == DEFAULT


def test_organization_theme_api_and_permissions(client, admin, agent):
    assert client.get("/api/branding").json()["console_theme"] == "tidepool"
    assert client.put("/api/settings/theme", headers=as_user(agent), json={"values": {"console_theme": "amethyst"}}).status_code == 403
    response = client.put("/api/settings/theme", headers=as_user(admin), json={"values": {"console_theme": "amethyst"}})
    assert response.status_code == 200
    assert client.get("/api/branding").json()["console_theme"] == "amethyst"
    assert client.put("/api/settings/theme", headers=as_user(admin), json={"values": {"console_theme": "midnight"}}).status_code == 422
