from app.models import AppSetting
from tests.conftest import as_user


def test_sidebar_preferences_are_personal_and_do_not_change_permissions(client, db, admin, agent):
    before = client.get("/api/auth/me", headers=as_user(agent)).json()
    assert before["hidden_modules"] == []
    r = client.put("/api/auth/me/preferences", headers=as_user(agent), json={"hidden_modules": ["scripts", "reports", "scripts", "settings"]})
    assert r.status_code == 200, r.text
    assert r.json()["hidden_modules"] == ["reports", "scripts", "settings"]
    assert r.json()["permissions"] == before["permissions"]
    assert r.json()["features"] == before["features"]
    assert client.get("/api/auth/me", headers=as_user(agent)).json()["hidden_modules"] == ["reports", "scripts", "settings"]
    assert client.get("/api/auth/me", headers=as_user(admin)).json()["hidden_modules"] == []
    assert client.get("/api/scripts", headers=as_user(agent)).status_code == 200
    assert client.get("/api/permissions", headers=as_user(agent)).status_code == 403
    row = db.get(AppSetting, f"personal_navigation:{agent.id}")
    assert row.updated_by_id == agent.id


def test_older_colour_saves_preserve_navigation_and_sidebar_saves_preserve_colours(client, agent):
    h = as_user(agent)
    rule = {"field": "priority", "value": "high", "color": "#112233"}
    assert client.put("/api/auth/me/preferences", headers=h, json={"ticket_color": "#112233", "color_rules": [rule]}).status_code == 200
    r = client.put("/api/auth/me/preferences", headers=h, json={"hidden_modules": ["vendors"]})
    assert r.json()["ticket_color"] == "#112233" and r.json()["color_rules"] == [rule]
    r = client.put("/api/auth/me/preferences", headers=h, json={"ticket_color": "#445566", "color_rules": []})
    assert r.json()["hidden_modules"] == ["vendors"]
    assert client.put("/api/auth/me/preferences", headers=h, json={"hidden_modules": None}).json()["hidden_modules"] == []


def test_core_links_and_unknown_keys_cannot_be_hidden(client, agent):
    for hidden in (["tickets"], ["preferences"], ["sign-out"], ["unknown"], "scripts"):
        r = client.put("/api/auth/me/preferences", headers=as_user(agent), json={"hidden_modules": hidden})
        assert r.status_code == 422
    assert client.get("/api/auth/me", headers=as_user(agent)).json()["hidden_modules"] == []
