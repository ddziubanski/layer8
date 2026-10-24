# SPDX-License-Identifier: AGPL-3.0-or-later
"""Screen layouts: everyone's default (admins), each technician's own, and new panels never lost."""

import pytest

from app import cli, layouts
from tests.conftest import as_user


def test_default_mine_and_reset(client, admin, agent):
    r = client.get("/api/layouts/ticket", headers=as_user(agent)).json()
    assert r["effective"] == layouts.usual("ticket") and r["mine"] is None and r["required"] == ["conversation"]
    # An agent can't change everyone's.
    body = {"main": ["conversation", "scripts"], "side": ["status"], "hidden": ["expenses"]}
    assert client.put("/api/layouts/ticket/default", json=body, headers=as_user(agent)).status_code == 403
    d = client.put("/api/layouts/ticket/default", json=body, headers=as_user(admin)).json()["default"]
    # Everything else comes back in its usual column, after the ones placed.
    assert d["main"][:2] == ["conversation", "scripts"] and d["side"][0] == "status" and d["hidden"] == ["expenses"]
    assert set(d["main"] + d["side"] + d["hidden"]) == set(layouts.usual("ticket")["main"] + layouts.usual("ticket")["side"])
    assert client.get("/api/layouts/ticket", headers=as_user(agent)).json()["effective"] == d
    # The agent's own wins, until they reset it.
    own = client.put("/api/layouts/ticket/mine", json={"main": ["history", "conversation"], "side": [], "hidden": []}, headers=as_user(agent)).json()
    assert own["effective"]["main"][:2] == ["history", "conversation"] and own["mine"] is not None
    assert client.delete("/api/layouts/ticket/mine", headers=as_user(agent)).json()["effective"] == d
    assert client.get("/api/layouts/nope", headers=as_user(agent)).status_code == 404


def test_conversation_cant_be_hidden_and_junk_is_dropped():
    out = layouts.normalise("ticket", {"main": ["bogus", "history", "history"], "hidden": ["conversation", "actions"]})
    assert "conversation" in out["main"] and out["hidden"] == ["actions"] and "bogus" not in sum((out[z] for z in layouts.ZONES), [])
    assert out["main"].count("history") == 1


def test_layouts_from_the_answers_file(db):
    cli.import_settings(db, {"layouts": {"ticket": {"main": ["conversation"], "side": ["actions"], "hidden": ["systems"]}}})
    assert layouts.default(db, "ticket")["hidden"] == ["systems"]
    with pytest.raises(Exception, match="No page"):
        cli.import_settings(db, {"layouts": {"nope": {}}})


def test_full_width_zones():
    out = layouts.normalise("ticket", {"top": ["history"], "bottom": ["attachments", "devices"], "main": ["conversation"]})
    assert out["top"] == ["history"] and out["bottom"] == ["attachments", "devices"]
    assert "history" not in out["main"] and "devices" not in out["side"]
    # A layout saved before the zones existed reads the same as before.
    assert layouts.normalise("ticket", {"main": ["conversation"], "side": [], "hidden": []})["top"] == []


def test_purchase_order_page(client, agent):
    r = client.get("/api/layouts/po", headers=as_user(agent)).json()
    assert r["effective"]["main"] == ["order"] and r["required"] == ["order"]
    own = client.put("/api/layouts/po/mine", json={"top": ["history"], "main": ["order", "files"], "hidden": ["order", "heads_up"]}, headers=as_user(agent)).json()["effective"]
    assert own["top"] == ["history"] and own["main"] == ["order", "files"] and own["hidden"] == ["heads_up"] and own["side"] == ["receive"]


def test_field_groups(client, agent):
    usual = client.get("/api/layouts/po", headers=as_user(agent)).json()["effective"]["fields"]
    assert usual["order_top"]["order"][0] == "vendor" and usual["order_bottom"]["wide"] == ["ship_to", "terms", "notes"]
    body = {"main": ["order"], "fields": {"order_top": {"order": ["currency", "bogus", "vendor"], "hidden": ["budget_code"], "wide": ["vendor"]}}}
    got = client.put("/api/layouts/po/mine", json=body, headers=as_user(agent)).json()["effective"]["fields"]
    top = got["order_top"]
    assert top["order"][:2] == ["currency", "vendor"] and "bogus" not in top["order"] and len(top["order"]) == 9
    assert top["hidden"] == ["budget_code"] and top["wide"] == ["vendor"]
    # Groups not given keep their usual arrangement.
    assert got["order_bottom"] == usual["order_bottom"]
    assert client.get("/api/layouts/ticket", headers=as_user(agent)).json()["effective"]["fields"] == {}


def test_widget_mode_makes_missing_optional_panels_opt_in():
    explicit = layouts.normalise("ticket", {"widget_mode": True, "main": ["conversation", "history"]})
    assert explicit["widget_mode"] is True
    assert explicit["main"] == ["conversation", "history"] and explicit["side"] == []
    assert "scripts" in explicit["hidden"] and "copilot" in explicit["hidden"]
    # Legacy arrangements keep their existing normalization and new-panel behavior.
    legacy = layouts.normalise("ticket", {"main": ["conversation", "history"]})
    assert legacy["widget_mode"] is False and "scripts" in legacy["main"]
    assert "conversation" not in layouts.normalise("ticket", {"widget_mode": True, "hidden": ["conversation"]})["hidden"]


def test_widget_mode_survives_api_save_reset_and_import(client, db, admin, agent):
    body = {"widget_mode": True, "main": ["conversation"], "side": ["status"]}
    mine = client.put("/api/layouts/ticket/mine", headers=as_user(agent), json=body)
    assert mine.status_code == 200 and mine.json()["effective"]["widget_mode"] is True
    assert mine.json()["effective"]["main"] == ["conversation"]
    assert client.put("/api/layouts/ticket/default", headers=as_user(agent), json=body).status_code == 403
    client.put("/api/layouts/ticket/default", headers=as_user(admin), json=body)
    assert client.delete("/api/layouts/ticket/mine", headers=as_user(agent)).json()["effective"]["widget_mode"] is True
    cli.import_settings(db, {"layouts": {"po": {"widget_mode": True, "main": ["order"]}}})
    saved = layouts.default(db, "po")
    assert saved["widget_mode"] is True and saved["side"] == [] and "files" in saved["hidden"]
