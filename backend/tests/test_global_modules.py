# SPDX-License-Identifier: AGPL-3.0-or-later
"""Global workspace policy stays distinct from personal preferences and authorization."""
from typing import get_args

import pytest

from app import cli, configstore, workspace
from app.config import Settings, settings
from app.models import AppSetting
from app.schemas import SidebarModule
from tests.conftest import as_user


def test_pure_catalog_matches_navigation_contract_and_all_modules_default_on():
    assert {m for m, _, _, _ in workspace.MODULES} == set(get_args(SidebarModule))
    assert all(Settings.model_fields[name].default is True for _, name, _, _ in workspace.MODULES)
    assert all(name in configstore.FIELDS for _, name, _, _ in workspace.MODULES)


def test_pure_workspace_flags_do_not_change_integration_settings(monkeypatch):
    before = (settings.action1_enabled, settings.teams_chat_enabled)
    monkeypatch.setattr(settings, "workspace_ipam_enabled", False)
    monkeypatch.setattr(settings, "workspace_scripts_enabled", False)
    assert "ipam" in workspace.disabled() and "scripts" in workspace.disabled()
    assert before == (settings.action1_enabled, settings.teams_chat_enabled)


def test_admin_controls_apply_to_all_profiles_without_overwriting_personal_choices(client, db, admin, agent, requester):
    assert client.get("/api/auth/me", headers=as_user(agent)).json()["disabled_modules"] == []
    client.put("/api/auth/me/preferences", headers=as_user(agent), json={"hidden_modules": ["vendors"]})
    before = client.get("/api/auth/me", headers=as_user(agent)).json()
    response = client.put("/api/settings/modules", headers=as_user(admin), json={"values": {
        "workspace_ipam_enabled": False, "workspace_reports_enabled": False, "workspace_settings_enabled": False}})
    assert response.status_code == 200, response.text
    for user in (admin, agent, requester):
        profile = client.get("/api/auth/me", headers=as_user(user)).json()
        assert set(profile["disabled_modules"]) == {"ipam", "reports", "settings"}
    after = client.get("/api/auth/me", headers=as_user(agent)).json()
    assert after["hidden_modules"] == ["vendors"]
    assert after["permissions"] == before["permissions"] and after["features"] == before["features"]
    assert db.get(AppSetting, "modules").updated_by_id == admin.id
    # Settings remains an admin recovery path, and global flags are not API permissions.
    assert client.get("/api/settings/modules", headers=as_user(admin)).status_code == 200
    assert client.get("/api/ipam/subnets", headers=as_user(agent)).status_code == 200


@pytest.mark.parametrize("role", ["agent", "requester"])
def test_nonadmins_cannot_change_global_modules(client, admin, agent, requester, role):
    user = agent if role == "agent" else requester
    for method in ("get", "put"):
        kwargs = {"json": {"values": {"workspace_scripts_enabled": False}}} if method == "put" else {}
        assert getattr(client, method)("/api/settings/modules", headers=as_user(user), **kwargs).status_code == 403
    assert settings.workspace_scripts_enabled


def test_personal_show_all_cannot_reenable_global_off_and_global_reenable_preserves_preferences(client, admin, agent):
    client.put("/api/settings/modules", headers=as_user(admin), json={"values": {"workspace_scripts_enabled": False}})
    profile = client.put("/api/auth/me/preferences", headers=as_user(agent), json={"hidden_modules": [], "disabled_modules": []}).json()
    assert profile["hidden_modules"] == [] and profile["disabled_modules"] == ["scripts"]
    client.put("/api/auth/me/preferences", headers=as_user(agent), json={"hidden_modules": ["scripts"]})
    client.put("/api/settings/modules", headers=as_user(admin), json={"values": {"workspace_scripts_enabled": True}})
    profile = client.get("/api/auth/me", headers=as_user(agent)).json()
    assert profile["disabled_modules"] == [] and profile["hidden_modules"] == ["scripts"]


def test_cli_import_and_server_environment_lock(client, db, admin, monkeypatch):
    cli.import_settings(db, {"modules": {"workspace_devices_enabled": False, "workspace_portal_shortcut_enabled": False}})
    db.commit()
    assert set(client.get("/api/auth/me", headers=as_user(admin)).json()["disabled_modules"]) == {"devices", "portal"}
    monkeypatch.setattr(configstore, "FROM_ENVIRONMENT", configstore.FROM_ENVIRONMENT | {"workspace_devices_enabled"})
    fields = client.get("/api/settings/modules", headers=as_user(admin)).json()["fields"]
    assert next(f for f in fields if f["name"] == "workspace_devices_enabled")["locked"]
    assert client.put("/api/settings/modules", headers=as_user(admin), json={"values": {"workspace_devices_enabled": True}}).status_code == 422


def test_unknown_core_switches_are_refused_atomically(client, admin):
    response = client.put("/api/settings/modules", headers=as_user(admin), json={"values": {
        "workspace_devices_enabled": False, "workspace_tickets_enabled": False}})
    assert response.status_code == 422 and settings.workspace_devices_enabled


def test_core_helpdesk_and_source_offer_survive_all_workspace_modules_off(client, admin, agent, service_desk):
    before = client.get("/api/auth/me", headers=as_user(agent)).json()["features"]
    values = {name: False for _, name, _, _ in workspace.MODULES}
    assert client.put("/api/settings/modules", headers=as_user(admin), json={"values": values}).status_code == 200
    assert client.get("/api/settings/modules", headers=as_user(admin)).status_code == 200
    assert client.get("/api/tickets", headers=as_user(agent)).status_code == 200
    assert client.get("/api/portal/queues", headers=as_user(agent)).status_code == 200
    profile = client.get("/api/auth/me", headers=as_user(agent)).json()
    assert profile["features"] == before and len(profile["disabled_modules"]) == len(workspace.MODULES)
    about = client.get("/api/about")
    assert about.status_code == 200 and about.json()["license"] == "AGPL-3.0-or-later"
    assert about.json()["source_download"] or about.json()["source_repository"]
