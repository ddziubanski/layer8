# SPDX-License-Identifier: AGPL-3.0-or-later
"""AD/LDAP: sync, nested groups, group-based roles, sign-in, and the admin API."""

import pytest
from sqlalchemy import func, select

from app import auth
from app.directory import sync
from app.mail.intake import IntakeConfig, run_intake
from app.models import AuthSource, Group, Ticket, User, UserEmail, UserRole, UserSession
from tests.conftest import as_user, make_user
from tests.fake_directory import FakeDirectory
from tests.test_mail_intake import FakeMailbox

PW = "Correct-horse-1"
CSRF = {auth.CSRF_HEADER: "1"}


@pytest.fixture
def ad(monkeypatch):
    d = FakeDirectory()
    monkeypatch.setattr(sync, "get_directory", lambda: d)
    return d


@pytest.fixture
def org(ad):
    """Helpdesk admins contain a nested 'IT leads' group; agents group; a plain user."""
    people = {
        "park": ad.add_user("jpark@corp.local", "J. Park", sam="jpark", aliases=["j.park@corp.com"], dept="IT"),
        "cole": ad.add_user("dcole@corp.local", "D. Cole", sam="dcole"),
        "osei": ad.add_user("mosei@corp.local", "M. Osei", sam="mosei"),
        "user": ad.add_user("ralvarez@corp.local", "R. Alvarez", sam="ralvarez", aliases=["frontdesk@corp.local"]),
    }
    leads = ad.add_group("SG-IT-Leads", people["osei"])
    ad.add_group("SG-Helpdesk-Admins", leads)
    ad.add_group("SG-Helpdesk-Agents", people["park"], people["cole"])
    ad.add_group("SG-Network-Team", people["cole"])
    return people


def run_sync(db, ad, **kw):
    # Like the worker: start from what's committed, not this test session's cached objects.
    db.expire_all()
    run = sync.full_sync(db, ad, **kw)
    db.add(run)
    db.commit()
    db.expire_all()
    return run


def user(db, email):
    db.expire_all()
    return db.scalar(select(User).where(func.lower(User.email) == email.lower()))


def import_group(client, admin, ad, name):
    g = next(x for x in ad.groups.values() if x.name == name)
    r = client.post("/api/directory/groups/import", json={"guid": g.guid}, headers=as_user(admin))
    assert r.status_code == 201, r.text
    return r.json()


def map_roles(client, admin, mappings):
    return client.put("/api/directory/role-mappings", json=mappings, headers=as_user(admin))


@pytest.fixture
def mapped(client, db, admin, ad, org):
    """The usual setup: sync, import the two role groups, map them."""
    run_sync(db, ad)
    admins = import_group(client, admin, ad, "SG-Helpdesk-Admins")
    agents = import_group(client, admin, ad, "SG-Helpdesk-Agents")
    r = map_roles(client, admin, [{"group_id": admins["id"], "role": "admin"}, {"group_id": agents["id"], "role": "agent"}])
    assert r.status_code == 200, r.text
    return {"admins": admins, "agents": agents}


# Sync


def test_sync_creates_users_with_details_and_aliases(db, ad, org):
    run = run_sync(db, ad)
    assert (run.users_seen, run.created, run.error) == (4, 4, None)
    park = user(db, "jpark@corp.local")
    assert (park.auth_source, park.role, park.role_source) == (AuthSource.directory, UserRole.requester, "default")
    assert (park.sam_account_name, park.department, park.directory_id) == ("jpark", "IT", org["park"].guid)
    assert [e.email for e in db.scalars(select(UserEmail).where(UserEmail.user_id == park.id))] == ["j.park@corp.com"]
    assert park.password_hash is None


def test_sync_brings_contact_details_and_links_managers(client, db, admin, ad, org):
    # The manager comes later in the listing than the person who reports to them.
    ad.put(org["user"], mobile="+1 555 0199", office="Building 2, Room 14", company="Example Corp", manager_dn=org["osei"].dn.upper())
    ad.put(org["osei"], manager_dn="CN=Someone,OU=Elsewhere,DC=corp,DC=local")  # outside the sync scope
    run = run_sync(db, ad)
    assert run.error is None
    ral, osei = user(db, "ralvarez@corp.local"), user(db, "mosei@corp.local")
    assert (ral.mobile, ral.office, ral.company, ral.manager_id) == ("+1 555 0199", "Building 2, Room 14", "Example Corp", osei.id)
    assert osei.manager_id is None
    card = client.get(f"/api/people/{ral.id}", headers=as_user(admin)).json()
    assert card["manager"] == {"id": osei.id, "display_name": "M. Osei", "title": None}
    assert [r["display_name"] for r in client.get(f"/api/people/{osei.id}", headers=as_user(admin)).json()["direct_reports"]] == ["R. Alvarez"]
    # A manager removed in AD is unlinked on the next sync.
    ad.put(org["user"], manager_dn=None)
    run_sync(db, ad)
    assert user(db, "ralvarez@corp.local").manager_id is None


def test_resync_updates_renames_and_aliases(db, ad, org):
    run_sync(db, ad)
    ad.put(org["park"], display_name="Jae Park", dn="CN=Jae Park,OU=Moved,DC=corp,DC=local", aliases=["jae@corp.com"], department="Infra")
    run = run_sync(db, ad)
    assert run.updated == 1 and run.created == 0
    park = user(db, "jpark@corp.local")
    assert (park.display_name, park.department, park.directory_dn) == ("Jae Park", "Infra", "CN=Jae Park,OU=Moved,DC=corp,DC=local")
    assert [e.email for e in db.scalars(select(UserEmail).where(UserEmail.user_id == park.id))] == ["jae@corp.com"]


def test_disabled_and_removed_accounts_are_deactivated_and_signed_out(db, ad, org):
    run_sync(db, ad)
    park, cole = user(db, "jpark@corp.local"), user(db, "dcole@corp.local")
    db.add_all([UserSession(token_hash="a" * 64, user_id=park.id, expires_at=auth.sla.utcnow()), UserSession(token_hash="b" * 64, user_id=cole.id, expires_at=auth.sla.utcnow())])
    db.commit()
    ad.put(org["park"], enabled=False)
    del ad.users_by_guid[org["cole"].guid]
    run = run_sync(db, ad)
    assert run.deactivated == 1
    assert not user(db, "jpark@corp.local").is_active and not user(db, "dcole@corp.local").is_active
    assert db.scalar(select(func.count()).select_from(UserSession)) == 0
    assert {d["kind"] for d in run.details} >= {"disabled", "removed"}


def test_empty_directory_result_deactivates_nobody(db, ad, org):
    run_sync(db, ad)
    ad.users_by_guid.clear()
    run = run_sync(db, ad)
    assert "refusing to deactivate everyone" in run.error
    assert user(db, "jpark@corp.local").is_active


def test_the_last_admin_is_never_deactivated_by_sync(db, ad, org):
    run_sync(db, ad)
    park, cole = user(db, "jpark@corp.local"), user(db, "dcole@corp.local")
    park.role, cole.role = UserRole.admin, UserRole.admin
    db.commit()
    del ad.users_by_guid[org["cole"].guid]
    del ad.users_by_guid[org["park"].guid]
    run = run_sync(db, ad)
    # One goes; the other is kept as the only admin left, and the run says so.
    active = [u.email for u in (user(db, "jpark@corp.local"), user(db, "dcole@corp.local")) if u.is_active]
    assert len(active) == 1 and any(d["kind"] == "kept" for d in run.details)
    # Disabling the remaining admin in AD also refuses to deactivate the last active admin.
    kept_guid = org["park"].guid if active[0] == "jpark@corp.local" else org["cole"].guid
    kept_user = org["park"] if active[0] == "jpark@corp.local" else org["cole"]
    ad.users_by_guid[kept_guid] = kept_user
    ad.put(kept_user, enabled=False)
    run2 = run_sync(db, ad)
    assert user(db, active[0]).is_active and any(d["kind"] == "kept" for d in run2.details)


def test_links_email_intake_requesters_keeping_their_tickets(client, db, ad, org, agent, service_desk):
    # Created by email intake from an alias before AD sync existed: no password.
    early = make_user(db, "frontdesk@corp.local", name="frontdesk")
    t = client.post("/api/tickets", json={"subject": "MFP", "requester_email": early.email}, headers=as_user(agent)).json()
    run = run_sync(db, ad)
    # The passwordless dev "agent" fixture (jpark@) is linked too.
    assert sorted(d["user"] for d in run.details if d["kind"] == "linked") == ["jpark@corp.local", "ralvarez@corp.local"]
    linked = db.get(User, early.id)
    db.refresh(linked)
    assert (linked.email, linked.directory_id, linked.auth_source) == ("ralvarez@corp.local", org["user"].guid, AuthSource.directory)
    assert db.get(Ticket, t["id"]).requester_id == early.id


def test_local_account_with_password_is_never_taken_over(db, ad, org):
    local = make_user(db, "jpark@corp.local", UserRole.admin)
    auth.set_password(db, local, "break-glass-password")
    db.commit()
    run = run_sync(db, ad)
    db.refresh(local)
    assert (local.auth_source, local.role) == (AuthSource.local, UserRole.admin)
    assert any(d["kind"] == "conflict" and "local account" in d["text"] for d in run.details)


def test_dry_run_changes_nothing(db, ad, org):
    run = sync.full_sync(db, ad, dry_run=True)
    db.commit()
    assert run.created == 4 and run.dry_run
    assert db.scalar(select(func.count()).select_from(User).where(User.auth_source == AuthSource.directory)) == 0


def test_directory_outage_is_reported(db, ad, org):
    ad.down = True
    run = run_sync(db, ad)
    assert "unavailable" in run.error


# Groups and roles


def test_nested_group_membership_and_highest_role_wins(client, db, admin, ad, org, mapped):
    osei, park, cole, ral = (user(db, e) for e in ("mosei@corp.local", "jpark@corp.local", "dcole@corp.local", "ralvarez@corp.local"))
    # Osei is admin through SG-IT-Leads nested inside SG-Helpdesk-Admins.
    assert (osei.role, osei.role_source) == (UserRole.admin, "group:SG-Helpdesk-Admins")
    assert (park.role, park.role_source) == (UserRole.agent, "group:SG-Helpdesk-Agents")
    assert ral.role == UserRole.requester
    # In both groups: admin wins.
    ad.members[mapped_guid(ad, "SG-Helpdesk-Admins")].add(org["cole"].guid)
    run_sync(db, ad)
    assert user(db, "dcole@corp.local").role == UserRole.admin
    assert cole.id  # still the same account


def mapped_guid(ad, name):
    return next(g for g, x in ad.groups.items() if x.name == name)


def test_removed_from_group_loses_role_and_is_signed_out(client, db, admin, ad, org, mapped):
    park = user(db, "jpark@corp.local")
    db.add(UserSession(token_hash="c" * 64, user_id=park.id, expires_at=auth.sla.utcnow()))
    db.commit()
    ad.members[mapped_guid(ad, "SG-Helpdesk-Agents")].discard(org["park"].guid)
    run = run_sync(db, ad)
    assert user(db, "jpark@corp.local").role == UserRole.requester
    assert db.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == park.id)) == 0
    assert any(d["kind"] == "role" and "agent → requester" in d["text"] for d in run.details)


def test_group_deleted_in_ad_removes_rights(client, db, admin, ad, org, mapped):
    del ad.groups[mapped_guid(ad, "SG-Helpdesk-Agents")]
    run_sync(db, ad)
    g = db.get(Group, mapped["agents"]["id"])
    assert g.sync_error == "Not found in the directory" and g.members == []
    assert user(db, "jpark@corp.local").role == UserRole.requester


def test_role_changes_that_leave_no_admin_are_refused(client, db, admin, ad, org, mapped):
    # Only admin is the dev-header admin with no password, so it doesn't count as a usable admin.
    r = map_roles(client, admin, [{"group_id": mapped["agents"]["id"], "role": "agent"}])
    assert r.status_code == 409 and "no active admin" in r.json()["detail"]
    assert user(db, "mosei@corp.local").role == UserRole.admin


def test_preview_shows_changes_without_saving(client, db, admin, ad, org, mapped):
    r = client.post(
        "/api/directory/role-mappings/preview",
        json=[{"group_id": mapped["admins"]["id"], "role": "admin"}, {"group_id": mapped["agents"]["id"], "role": "admin"}],
        headers=as_user(admin),
    ).json()
    assert r["error"] is None
    assert sorted(c["user"] for c in r["changes"]) == ["dcole@corp.local", "jpark@corp.local"]
    assert r["admins"] >= 3
    assert user(db, "jpark@corp.local").role == UserRole.agent
    assert {m["role"] for m in client.get("/api/directory/role-mappings", headers=as_user(admin)).json()} == {"admin", "agent"}


def test_directory_roles_cant_be_edited_by_hand(client, db, admin, ad, org, mapped):
    park = user(db, "jpark@corp.local")
    r = client.patch(f"/api/users/{park.id}", json={"role": "admin"}, headers=as_user(admin))
    assert r.status_code == 409 and "role mappings" in r.json()["detail"]
    assert client.get("/api/users", headers=as_user(admin)).json()[0]["role_source"]


def test_ad_group_as_queue_owners(client, db, admin, ad, org, mapped, service_desk):
    team = import_group(client, admin, ad, "SG-Network-Team")
    client.put(f"/api/queues/{service_desk.id}/owners", json={"group_ids": [team["id"]]}, headers=as_user(admin))
    names = [u["display_name"] for u in client.get(f"/api/queues/{service_desk.id}/assignable", headers=as_user(admin)).json()]
    assert names == ["D. Cole"]


def test_directory_groups_need_existing_ad_group(client, admin, ad):
    assert client.post("/api/directory/groups/import", json={"guid": "6f1c0000-0000-4000-8000-000000000000"}, headers=as_user(admin)).status_code == 404


def test_search_and_lookup(client, db, admin, ad, org, mapped):
    found = client.get("/api/directory/groups/search", params={"q": "helpdesk"}, headers=as_user(admin)).json()
    assert [(g["name"], g["group_id"] is not None) for g in found] == [("SG-Helpdesk-Admins", True), ("SG-Helpdesk-Agents", True)]
    r = client.post("/api/directory/lookup", json={"login": "CORP\\mosei"}, headers=as_user(admin)).json()
    assert r["found"] and r["role"] == "admin" and r["role_source"] == "group:SG-Helpdesk-Admins"
    assert r["groups"] == ["SG-Helpdesk-Admins"]
    assert client.post("/api/directory/lookup", json={"login": "nobody"}, headers=as_user(admin)).json() == {**r, **{k: None for k in r}, "found": False, "aliases": [], "groups": []}


def test_admin_only(client, agent, ad):
    for method, path in [("get", "/api/directory/status"), ("post", "/api/directory/sync"), ("get", "/api/directory/role-mappings")]:
        assert getattr(client, method)(path, headers=as_user(agent)).status_code == 403


def test_sync_endpoint_and_status(client, db, admin, ad, org):
    run = client.post("/api/directory/sync", headers=as_user(admin)).json()
    assert run["created"] == 4
    status = client.get("/api/directory/status", headers=as_user(admin)).json()
    assert status["directory_users"] == 4 and status["last_run"]["id"] == run["id"]


def test_worker_sync_runs_when_due(db, session_factory, ad, org):
    assert sync.run_if_due(session_factory, auth.sla.utcnow()) is not None
    assert sync.run_if_due(session_factory, auth.sla.utcnow()) is None  # not due yet


# Sign-in


def login(client, who, password=PW):
    return client.post("/api/auth/login", json={"email": who, "password": password})


@pytest.fixture
def real_login(monkeypatch):
    monkeypatch.setattr(auth.settings, "dev_auth", False)


def test_ad_sign_in_creates_user_just_in_time_with_group_role(client, db, admin, ad, org, mapped, real_login):
    newbie = ad.add_user("newhire@corp.local", "New Hire", sam="nhire")
    ad.members[mapped_guid(ad, "SG-Helpdesk-Agents")].add(newbie.guid)
    r = login(client, "CORP\\nhire")
    assert r.status_code == 200, r.text
    assert (r.json()["email"], r.json()["role"], r.json()["role_source"]) == ("newhire@corp.local", "agent", "group:SG-Helpdesk-Agents")
    assert client.get("/api/auth/me").json()["auth_source"] == "directory"


@pytest.mark.parametrize("who", ["jpark@corp.local", "JPark", "CORP\\jpark", "j.park@corp.com"])
def test_sign_in_with_any_identifier(client, db, ad, org, real_login, who):
    assert login(client, who).status_code == 200


def test_wrong_password_disabled_and_empty_password(client, db, ad, org, real_login):
    assert login(client, "jpark", "nope").status_code == 401
    ad.put(org["cole"], enabled=False)
    assert login(client, "dcole").status_code == 401
    ad.binds.clear()
    # An empty password would be an anonymous bind that "succeeds"; it never reaches the directory.
    assert login(client, "jpark", "").status_code == 422
    assert ad.binds == []


def test_failed_ad_sign_ins_are_throttled(client, db, ad, org, real_login):
    run_sync(db, ad)
    for _ in range(auth.MAX_FAILED_LOGINS):
        login(client, "jpark", "wrong-password")
    r = login(client, "jpark")
    assert r.status_code == 401 and r.json()["detail"] == "Wrong email or password"


def test_directory_down_says_so_without_leaking(client, db, ad, org, real_login):
    ad.down = True
    r = login(client, "jpark")
    assert r.status_code == 401 and r.json()["detail"] == "Sign-in is unavailable right now. Try again shortly."


def test_local_break_glass_admin_still_works_with_ad(client, db, ad, org, real_login):
    local = make_user(db, "admin@corp.local", UserRole.admin)
    auth.set_password(db, local, "break-glass-password")
    db.commit()
    assert login(client, "admin@corp.local", "break-glass-password").status_code == 200


def test_email_from_an_ad_alias_links_to_the_person(db, session_factory, ad, org, service_desk, monkeypatch):
    run_sync(db, ad)
    mailbox = FakeMailbox()
    mailbox.deliver(from_address="J.Park@corp.com", subject="Laptop dock")
    run_intake(session_factory, mailbox, IntakeConfig.create("helpdesk@corp.local", ["corp.local"]))
    db.expire_all()
    [t] = db.scalars(select(Ticket)).all()
    assert t.requester.email == "jpark@corp.local"


# Sign-in mode: AD only, AD with local fallback, local only

FALLBACK = "fallback-pass-1234"


def sign_in_mode(client, admin, uid, mode, password=None):
    body = {"mode": mode, **({"password": password} if password else {})}
    return client.put(f"/api/users/{uid}/sign-in", json=body, headers=as_user(admin))


def test_fallback_password_works_only_when_ad_is_down(client, db, admin, ad, org, mapped, monkeypatch):
    park = user(db, "jpark@corp.local")
    r = sign_in_mode(client, admin, park.id, "directory_with_fallback", FALLBACK)
    assert r.status_code == 200 and r.json()["local_fallback"] is True
    monkeypatch.setattr(auth.settings, "dev_auth", False)

    # AD up: the fallback password is not a second way in.
    assert login(client, "jpark", FALLBACK).status_code == 401
    assert login(client, "jpark").status_code == 200
    client.cookies.clear()

    # AD down: it is.
    ad.down = True
    assert login(client, "jpark", FALLBACK).status_code == 200
    client.cookies.clear()
    assert login(client, "jpark", "wrong-wrong-wrong").status_code == 401
    # Without a fallback, AD down means no sign-in.
    r = login(client, "dcole")
    assert r.status_code == 401 and "unavailable" in r.json()["detail"]


def test_sync_keeps_the_fallback_and_removes_it_when_no_longer_staff(client, db, admin, ad, org, mapped):
    park = user(db, "jpark@corp.local")
    sign_in_mode(client, admin, park.id, "directory_with_fallback", FALLBACK)
    run_sync(db, ad)
    assert user(db, "jpark@corp.local").password_hash is not None
    ad.members[mapped_guid(ad, "SG-Helpdesk-Agents")].discard(org["park"].guid)
    run = run_sync(db, ad)
    park = user(db, "jpark@corp.local")
    assert (park.role, park.local_fallback, park.password_hash) == (UserRole.requester, False, None)
    assert any(d["kind"] == "fallback" for d in run.details)


def test_fallback_is_for_directory_staff_only(client, db, admin, ad, org, mapped):
    requester = user(db, "ralvarez@corp.local")
    r = sign_in_mode(client, admin, requester.id, "directory_with_fallback", FALLBACK)
    assert r.status_code == 422 and "agents and admins" in r.json()["detail"]
    park = user(db, "jpark@corp.local")
    assert sign_in_mode(client, admin, park.id, "directory_with_fallback").status_code == 422  # needs a password
    assert sign_in_mode(client, admin, park.id, "directory_with_fallback", "short").status_code == 422


def test_switch_to_local_account_detaches_from_ad(client, db, admin, ad, org, mapped, monkeypatch):
    cole = user(db, "dcole@corp.local")
    r = sign_in_mode(client, admin, cole.id, "local", "a-local-password-1")
    assert r.status_code == 200 and r.json()["auth_source"] == "local"
    run = run_sync(db, ad)
    cole = user(db, "dcole@corp.local")
    assert (cole.auth_source, cole.directory_id, cole.role_source) == (AuthSource.local, None, "manual")
    assert any(d["kind"] == "conflict" and d["user"] == "dcole@corp.local" for d in run.details), run.details
    monkeypatch.setattr(auth.settings, "dev_auth", False)
    ad.down = True
    assert login(client, "dcole@corp.local", "a-local-password-1").status_code == 200


def test_back_to_ad_only_clears_the_fallback(client, db, admin, ad, org, mapped):
    park = user(db, "jpark@corp.local")
    sign_in_mode(client, admin, park.id, "directory_with_fallback", FALLBACK)
    r = sign_in_mode(client, admin, park.id, "directory")
    assert r.status_code == 200 and r.json()["local_fallback"] is False
    assert user(db, "jpark@corp.local").password_hash is None
