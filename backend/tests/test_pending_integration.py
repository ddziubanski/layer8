"""Temporary integration branch: all pending schema and auth additions coexist."""
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.config import settings
from tests.conftest import alembic_config, as_user


def test_pending_schema_sequence_upgrades_each_approved_stage(engine):
    cfg = alembic_config()
    assert ScriptDirectory.from_config(cfg).get_heads() == ["a86d20f49c71"]
    try:
        command.downgrade(cfg, "c3d58f1b7a20")
        assert not inspect(engine).has_table("copilot_conversations")
        assert not inspect(engine).has_table("software_deployments")
        assert not inspect(engine).has_table("meraki_clients")
        command.upgrade(cfg, "e4a27b9f1062")
        assert inspect(engine).has_table("copilot_conversations")
        assert not inspect(engine).has_table("software_deployments")
        command.upgrade(cfg, "f52b6d10a930")
        assert inspect(engine).has_table("software_deployments")
        assert not inspect(engine).has_table("meraki_clients")
    finally:
        command.upgrade(cfg, "head")
    assert inspect(engine).has_table("meraki_clients")


def test_navigation_and_meraki_feature_flags_survive_combined_auth(client, agent, monkeypatch):
    monkeypatch.setattr(settings, "meraki_enabled", True)
    monkeypatch.setattr(settings, "action1_enabled", True)
    response = client.put("/api/auth/me/preferences", headers=as_user(agent), json={"hidden_modules": ["scripts"]})
    assert response.status_code == 200
    profile = client.get("/api/auth/me", headers=as_user(agent)).json()
    assert profile["hidden_modules"] == ["scripts"]
    assert {"meraki", "action1"} <= set(profile["features"])
