# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ticket's AD account card: the requester's groups and OU, changed through a PowerShell relay;
onboarding templates with a preview and the nested-group warning for hybrid (Entra) sites."""

import json

import pytest

from app import adchanges, permissions
from app.config import settings
from app.directory import sync
from app.models import Comment, Group, Queue, ScriptRun, Ticket, TicketSource, UserRole
from tests.conftest import as_user, make_user
from tests.fake_directory import FakeDirectory

BASE = "OU=Access,DC=corp,DC=local"


@pytest.fixture
def ad(db, monkeypatch, requester, admin):
    d = FakeDirectory()
    monkeypatch.setattr(sync, "get_directory", lambda: d)
    monkeypatch.setattr(adchanges, "get_directory", lambda: d)
    monkeypatch.setattr(settings, "ldap_url", "ldaps://dc1.corp.local")
    monkeypatch.setattr(settings, "ad_change_group_bases", [BASE])
    monkeypatch.setattr(settings, "ad_change_ou_bases", ["OU=Staff,DC=corp,DC=local"])
    du = d.add_user("ralvarez@corp.local", "R. Alvarez")
    requester.email, requester.directory_id, requester.directory_dn = du.email, du.guid, du.dn
    in_ou = lambda g, ou: setattr(g, "dn", f"CN={g.name},{ou}") or g  # noqa: E731
    finance = in_ou(d.add_group("Finance-Share", du), BASE)
    vpn = in_ou(d.add_group("VPN-Users"), BASE)
    lic = in_ou(d.add_group("M365-E3"), BASE)
    lic_inner = in_ou(d.add_group("M365-E3-Finance"), BASE)
    d.members[lic.guid].add(lic_inner.guid)  # nested: Entra won't follow it
    admins = d.add_group("Domain Admins", du)  # outside the allowed OU
    ent = d.add_group("Enterprise Admins")  # outside too, and not a member
    d.ous = ["OU=Finance,OU=Staff,DC=corp,DC=local", "OU=Sales,OU=Staff,DC=corp,DC=local", "OU=Servers,DC=corp,DC=local"]
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    t = Ticket(subject="New starter: access", source=TicketSource.portal, queue_id=q.id, requester_id=requester.id)
    db.add(t)
    db.commit()
    return {"d": d, "ticket": t, "finance": finance, "vpn": vpn, "lic": lic, "admins": admins, "ent": ent}


def test_card_shows_groups_and_what_may_change(client, agent, ad):
    r = client.get(f"/api/tickets/{ad['ticket'].id}/requester-ad", headers=as_user(agent)).json()
    assert r["enabled"] and r["ou"] == "OU=Staff,DC=corp,DC=local"
    assert {(g["name"], g["changeable"]) for g in r["groups"]} == {("Finance-Share", True), ("Domain Admins", False)}
    assert [g["name"] for g in client.get("/api/ad/groups", params={"q": "vpn"}, headers=as_user(agent)).json()] == ["VPN-Users"]
    assert client.get("/api/ad/groups", params={"q": "admins"}, headers=as_user(agent)).json() == []
    assert [o["name"] for o in client.get("/api/ad/ous", params={"q": ""}, headers=as_user(agent)).json()] == ["Finance", "Sales"]
    nest = client.get("/api/ad/groups/nesting", params={"dn": ad["lic"].dn}, headers=as_user(agent)).json()
    assert nest["nested"] and "Entra ID" in nest["warning"]


def test_change_goes_to_a_runner_and_is_noted(client, db, agent, ad):
    t = ad["ticket"]
    body = {"add": [ad["vpn"].dn], "remove": [ad["finance"].dn], "move_to_ou": "OU=Sales,OU=Staff,DC=corp,DC=local", "title": "Account Manager"}
    pre = client.post(f"/api/tickets/{t.id}/ad-changes/preview", json=body, headers=as_user(agent)).json()
    assert [g["name"] for g in pre["add"]] == ["VPN-Users"] and [g["name"] for g in pre["remove"]] == ["Finance-Share"] and pre["move_to_ou"].startswith("OU=Sales")
    r = client.post(f"/api/tickets/{t.id}/ad-changes", json=body, headers=as_user(agent))
    assert r.status_code == 201, r.text
    run = db.get(ScriptRun, r.json()[0]["id"])
    assert run.script.name == adchanges.SCRIPT_NAME and run.target is None and run.ticket_id == t.id
    assert json.loads(run.values["add_groups"]) == [ad["vpn"].dn] and run.values["user_guid"] and run.values["title"] == "Account Manager"
    note = next(c.body for c in db.query(Comment).filter_by(ticket_id=t.id) if c.body.startswith("AD change for"))
    assert "add to VPN-Users" in note and "remove from Finance-Share" in note and "move to OU=Sales" in note


def test_ad_changes_pin_the_selected_dedicated_runner(client, db, admin, agent, ad, monkeypatch):
    made = client.post("/api/runners", json={"name": "AD worker", "languages": ["powershell"], "builtins_only": True}, headers=as_user(admin)).json()
    monkeypatch.setattr(settings, "ad_runner_name", "AD worker")
    response = client.post(f"/api/tickets/{ad['ticket'].id}/ad-changes", json={"add": [ad["vpn"].dn]}, headers=as_user(agent))
    assert response.status_code == 201, response.text
    run = db.get(ScriptRun, response.json()[0]["id"])
    assert run.only_runner_id == made["runner"]["id"]
    job = client.post("/api/runner/claim", json={}, headers={"Authorization": f"Bearer {made['token']}"})
    assert job.status_code == 200 and job.json()["run_id"] == run.id
    monkeypatch.setattr(settings, "ad_runner_name", "Missing relay")
    failed = client.post(f"/api/tickets/{ad['ticket'].id}/ad-changes", json={"add": [ad["vpn"].dn]}, headers=as_user(agent))
    assert failed.status_code == 422 and "No active PowerShell relay" in failed.json()["detail"]


def test_allow_lists_are_enforced(client, agent, ad):
    t = ad["ticket"]
    for body, why in [
        ({"add": [ad["ent"].dn]}, "isn't under the OUs"),
        ({"remove": [ad["admins"].dn]}, "isn't under the OUs"),
        ({"move_to_ou": "OU=Servers,DC=corp,DC=local"}, "isn't one of the OUs"),
        ({"remove": [ad["vpn"].dn]}, "isn't directly in"),
        ({}, "Nothing to change"),
    ]:
        r = client.post(f"/api/tickets/{t.id}/ad-changes", json=body, headers=as_user(agent))
        assert r.status_code == 422 and why in r.json()["detail"], (body, r.text)


def test_onboarding_template_preview_and_apply(client, db, admin, agent, ad):
    body = {"name": "Finance – AP clerk", "groups": [{"dn": ad["finance"].dn}, {"dn": ad["vpn"].dn}, {"dn": ad["lic"].dn}], "ou_dn": "OU=Finance,OU=Staff,DC=corp,DC=local", "department": "Finance", "title": "AP Clerk"}
    assert client.post("/api/onboarding-templates", json=body, headers=as_user(agent)).status_code == 403
    tpl = client.post("/api/onboarding-templates", json=body, headers=as_user(admin)).json()
    assert [g["name"] for g in tpl["groups"]] == ["Finance-Share", "VPN-Users", "M365-E3"]
    bad = {**body, "name": "Bad", "groups": [{"dn": ad["admins"].dn}]}
    assert client.post("/api/onboarding-templates", json=bad, headers=as_user(admin)).status_code == 422
    pre = client.post(f"/api/tickets/{ad['ticket'].id}/ad-changes/preview", json={"template_id": tpl["id"]}, headers=as_user(agent)).json()
    assert [g["name"] for g in pre["already"]] == ["Finance-Share"]
    assert [(g["name"], g["nested"]) for g in pre["add"]] == [("VPN-Users", False), ("M365-E3", True)]
    assert pre["move_to_ou"] == "OU=Finance,OU=Staff,DC=corp,DC=local" and (pre["department"], pre["title"]) == ("Finance", "AP Clerk")
    r = client.post(f"/api/tickets/{ad['ticket'].id}/ad-changes", json={"template_id": tpl["id"]}, headers=as_user(agent))
    assert r.status_code == 201
    assert any('template "Finance – AP clerk"' in c.body for c in db.query(Comment).filter_by(ticket_id=ad["ticket"].id))


def test_templates_from_the_answers_file(db):
    from app.cli import import_settings
    from app.models import OnboardingTemplate

    import_settings(db, {"onboarding_templates": [{"name": "Sales rep", "groups": ["CN=Sales-Share,OU=Access,DC=corp,DC=local"], "ou": "OU=Sales,OU=Staff,DC=corp,DC=local", "title": "Sales Rep"}]})
    t = db.query(OnboardingTemplate).one()
    assert t.groups == [{"dn": "CN=Sales-Share,OU=Access,DC=corp,DC=local", "name": "Sales-Share"}] and t.title == "Sales Rep"


# New accounts (onboarding)


@pytest.fixture
def onboarding(ad, monkeypatch):
    monkeypatch.setattr(settings, "ad_new_user_ou", "OU=Staff,DC=corp,DC=local")
    monkeypatch.setattr(settings, "ad_new_user_domain", "example.com")
    monkeypatch.setattr(settings, "ad_new_user_name_format", "{f}{last}")
    d = ad["d"]
    d.add_user("jsmith@example.com", "Jane Smith", sam="jsmith", upn="jsmith@example.com")
    in_ou = lambda g: setattr(g, "dn", f"CN={g.name},{BASE}") or g  # noqa: E731
    ad["dl_sales"] = in_ou(d.add_group("[EMAIL] Sales"))
    ad["dl_all"] = in_ou(d.add_group("[EMAIL] All Staff"))
    d.add_group("[EMAIL] Board")  # outside the allowed OUs
    return ad


def test_new_account_name_is_made_and_checked(client, agent, onboarding):
    t = onboarding["ticket"]
    # jsmith is taken, so jsmith2.
    pre = client.post(f"/api/tickets/{t.id}/ad-account/new/preview", json={"given_name": "John", "surname": "Smith"}, headers=as_user(agent)).json()
    assert (pre["sam"], pre["upn"], pre["display_name"], pre["ou"]) == ("jsmith2", "jsmith2@example.com", "John Smith", "OU=Staff,DC=corp,DC=local")
    assert pre["problems"] == []
    # The technician's own choices are checked, not changed.
    pre = client.post(
        f"/api/tickets/{t.id}/ad-account/new/preview", json={"given_name": "John", "surname": "Smith", "sam": "jsmith", "mail": "jsmith@example.com", "ou": "OU=Servers,DC=corp,DC=local"},
        headers=as_user(agent),
    ).json()
    assert any("account name is already used" in p for p in pre["problems"]) and any("email address is already used" in p for p in pre["problems"])
    assert any("isn't an OU new accounts may go in" in p for p in pre["problems"])
    assert adchanges.account_name("{first}.{last}", "Zoë", "O'Brien-Smith") == "zoe.obriensmith"


def test_group_lists_offer_distribution_lists(client, agent, onboarding, monkeypatch):
    monkeypatch.setattr(settings, "ad_group_lists", ["Distribution lists: [EMAIL]"])
    lists = client.get("/api/ad/group-lists", headers=as_user(agent)).json()
    assert [(l["label"], [g["name"] for g in l["groups"]]) for l in lists] == [("Distribution lists", ["[EMAIL] All Staff", "[EMAIL] Sales"])]


def test_group_list_from_an_ou(client, agent, onboarding, monkeypatch):
    d = onboarding["d"]
    ou = "OU=Distribution Lists,DC=corp,DC=local"
    for name in ("Everyone", "Finance team"):
        d.add_group(name).dn = f"CN={name},{ou}"
    monkeypatch.setattr(settings, "ad_group_lists", [f"Distribution lists: {ou}"])
    lists = client.get("/api/ad/group-lists", headers=as_user(agent)).json()
    # Every group in the OU, whatever it's called; the OU needn't be among the changeable ones.
    assert [(l["label"], [g["name"] for g in l["groups"]]) for l in lists] == [("Distribution lists", ["Everyone", "Finance team"])]
    assert adchanges.under(f"CN=Everyone,{ou}", adchanges.group_bases())
    t = onboarding["ticket"]
    r = client.post(f"/api/tickets/{t.id}/ad-changes/preview", json={"add": [f"CN=Everyone,{ou}"], "remove": []}, headers=as_user(agent))
    assert r.status_code == 200 and [g["name"] for g in r.json()["add"]] == ["Everyone"], r.text


def test_group_list_setting_is_checked(client, admin):
    r = client.put("/api/settings/directory", json={"values": {"ad_group_lists": ["Lists: OU=Distribution Lists"]}}, headers=as_user(admin))
    assert r.status_code == 422 and "full DN" in r.json()["detail"]


def test_new_account_runs_with_a_sealed_password(client, db, agent, admin, onboarding):
    t = onboarding["ticket"]
    from tests.test_runs import bearer, runner

    token = runner(client, admin, languages=("powershell",))
    body = {
        "given_name": "John", "surname": "Smith", "mail": "john.smith@example.com", "phone": "+1 555 0100", "extension": "4321",
        "manager_id": t.requester_id, "groups": [onboarding["dl_sales"].dn, onboarding["lic"].dn], "department": "Sales",
    }
    r = client.post(f"/api/tickets/{t.id}/ad-account/new", json=body, headers=as_user(agent))
    assert r.status_code == 201, r.text
    out = r.json()
    password = out["password"]
    assert out["sam"] == "jsmith2" and len(password) >= 16
    run = db.get(ScriptRun, out["runs"][0]["id"])
    assert run.script.name == adchanges.CREATE_SCRIPT_NAME and run.values["extension"] == "4321" and run.values["manager_dn"] == onboarding["ticket"].requester.directory_dn
    assert json.loads(run.values["groups"]) == [onboarding["dl_sales"].dn, onboarding["lic"].dn]
    # Not in the clear anywhere: values, the note, the run's API view.
    assert password not in json.dumps(run.values) and run.sealed and password.encode() not in run.sealed
    note = next(c.body for c in db.query(Comment).filter_by(ticket_id=t.id) if c.body.startswith("New AD account"))
    assert "jsmith2@example.com" in note and "ext. 4321" in note and "not stored" in note and password not in note
    assert password not in client.get(f"/api/runs/{run.id}", headers=as_user(agent)).text
    # The runner gets it once.
    job = client.post("/api/runner/claim", json={"host": "r1", "version": "1.1"}, headers=bearer(token)).json()
    assert job["secret_values"] == {"password": password} and "password" not in job["values"]
    db.refresh(run)
    assert run.sealed is None


def test_new_account_needs_setting_up(client, agent, ad):
    r = client.post(f"/api/tickets/{ad['ticket'].id}/ad-account/new/preview", json={"given_name": "A", "surname": "B"}, headers=as_user(agent))
    assert r.status_code == 422 and "isn't set up" in r.json()["detail"]


def test_who_may_create_accounts(client, db, agent, admin, onboarding, monkeypatch):
    t = onboarding["ticket"]
    new = lambda who: client.post(f"/api/tickets/{t.id}/ad-account/new/preview", json={"given_name": "John", "surname": "Smith"}, headers=as_user(who))
    card = lambda who: client.get(f"/api/tickets/{t.id}/requester-ad", headers=as_user(who)).json()["can_create"]
    assert new(agent).status_code == 200 and card(agent)
    permissions.save(db, {"ad_create_accounts": {"who": "admins"}})
    db.commit()
    r = new(agent)
    assert r.status_code == 403 and r.json()["detail"] == "Create AD accounts: only admins may (Settings > Permissions)" and not card(agent)
    assert client.post(f"/api/tickets/{t.id}/ad-account/new", json={"given_name": "John", "surname": "Smith"}, headers=as_user(agent)).status_code == 403
    assert new(admin).status_code == 200 and card(admin)
    group = Group(name="IT Onboarding")
    db.add(group)
    db.flush()
    permissions.save(db, {"ad_create_accounts": {"who": "groups", "group_ids": [group.id]}})
    db.commit()
    assert new(agent).json()["detail"] == "Create AD accounts: only admins and members of IT Onboarding may (Settings > Permissions)"
    group.members = [db.merge(agent)]
    db.commit()
    assert new(agent).status_code == 200 and card(agent)


def test_copy_groups_from_another_person(client, db, agent, ad, admin):
    d = ad["d"]
    colleague = d.add_user("mosei@corp.local", "M. Osei")
    d.members[ad["vpn"].guid].add(colleague.guid)
    d.members[ad["lic"].guid].add(colleague.guid)
    d.members[ad["admins"].guid].add(colleague.guid)
    admin.directory_id, admin.directory_dn = colleague.guid, colleague.dn
    db.commit()
    got = {g["name"]: g for g in client.get(f"/api/ad/users/{admin.id}/groups", headers=as_user(agent)).json()}
    assert set(got) == {"VPN-Users", "M365-E3", "Domain Admins"}
    assert got["VPN-Users"]["changeable"] and not got["Domain Admins"]["changeable"]
    assert got["M365-E3"]["nested"] and "Entra ID" in got["M365-E3"]["warning"]
    local = make_user(db, "local@corp.local", UserRole.requester)
    r = client.get(f"/api/ad/users/{local.id}/groups", headers=as_user(agent))
    assert r.status_code == 422 and "isn't an AD account" in r.json()["detail"]


def test_new_account_with_a_start_date_and_the_manager_emailed(client, db, agent, admin, onboarding):
    from datetime import date, timedelta as td

    from app import sla
    from app.models import OutboundEmail
    from tests.test_runs import bearer, runner

    t = onboarding["ticket"]
    token = runner(client, admin, languages=("powershell",))
    later = date.today() + td(days=7)
    base = {"given_name": "John", "surname": "Smith", "mail": "john.smith@example.com", "manager_id": t.requester_id}
    pre = lambda **b: client.post(f"/api/tickets/{t.id}/ad-account/new/preview", json={**base, **b}, headers=as_user(agent)).json()
    assert pre(start_date=str(later))["enabled"] is False
    assert any("start date has passed" in p for p in pre(start_date=str(date.today() - td(days=1)))["problems"])
    assert any("Pick the manager" in p for p in pre(email_manager=True, manager_id=None)["problems"])

    r = client.post(f"/api/tickets/{t.id}/ad-account/new", json={**base, "start_date": str(later), "email_manager": True}, headers=as_user(agent))
    assert r.status_code == 201, r.text
    out = r.json()
    create, enable = (db.get(ScriptRun, x["id"]) for x in out["runs"])
    assert create.values["enabled"] is False and enable.script.name == adchanges.ENABLE_SCRIPT_NAME and enable.values == {"sam": "jsmith2"}
    assert enable.not_before.date() == later and out["runs"][1]["not_before"]
    note = next(c.body for c in db.query(Comment).filter_by(ticket_id=t.id) if c.body.startswith("New AD account"))
    assert "enabled on" in note and "emailed once it's created" in note

    # The runner gets the account now, and the enable run only from the start date.
    job = client.post("/api/runner/claim", json={"host": "r1", "version": "1.1"}, headers=bearer(token)).json()
    assert job["run_id"] == create.id
    assert client.post("/api/runner/claim", json={"host": "r1", "version": "1.1"}, headers=bearer(token)).status_code == 204
    # Created: the manager hears, without the password.
    client.post(f"/api/runner/runs/{create.id}/finish", json={"exit_code": 0, "error": None}, headers=bearer(token))
    mail = db.query(OutboundEmail).filter_by(to_address=t.requester.email).one()
    assert "jsmith2@example.com" in mail.body and "enabled on" in mail.body and out["password"] not in mail.body and mail.ticket_id == t.id
    enable.not_before = sla.utcnow() - td(minutes=1)
    db.commit()
    assert client.post("/api/runner/claim", json={"host": "r1", "version": "1.1"}, headers=bearer(token)).json()["run_id"] == enable.id


def test_failed_account_doesnt_email_the_manager(client, db, agent, admin, onboarding):
    from app.models import OutboundEmail
    from tests.test_runs import bearer, runner

    t = onboarding["ticket"]
    token = runner(client, admin, languages=("powershell",))
    body = {"given_name": "John", "surname": "Smith", "manager_id": t.requester_id, "email_manager": True}
    run_id = client.post(f"/api/tickets/{t.id}/ad-account/new", json=body, headers=as_user(agent)).json()["runs"][0]["id"]
    client.post("/api/runner/claim", json={"host": "r1", "version": "1.1"}, headers=bearer(token))
    client.post(f"/api/runner/runs/{run_id}/finish", json={"exit_code": 1, "error": "Access denied"}, headers=bearer(token))
    assert db.query(OutboundEmail).filter_by(to_address=t.requester.email).count() == 0
