"""Finer permissions: defaults as before, handing an action to groups, the reasons given, and deleting tickets."""

import pytest

from app import cli, configstore, permissions, storage
from app.models import Attachment, Comment, Group, KbArticle, Queue, Ticket, TicketSource, TicketStatus, UserRole
from tests.conftest import as_user, make_user


@pytest.fixture
def desk(db, requester):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.commit()
    return q


def ticket(db, q, requester, **over):
    t = Ticket(subject="Spam: win a prize", source=TicketSource.email, queue_id=q.id, requester_id=requester.id, status=TicketStatus.new, **over)
    db.add(t)
    db.commit()
    return t


def test_defaults_are_as_before(client, db, admin, agent, requester):
    me = client.get("/api/auth/me", headers=as_user(agent)).json()["permissions"]
    assert {"ticket_merge", "ticket_bulk", "ad_create_accounts", "kb_publish"} <= set(me)
    assert not {"ticket_delete", "rules_manage", "devices_delete", "scripts_manage"} & set(me)
    assert set(client.get("/api/auth/me", headers=as_user(admin)).json()["permissions"]) == {p.key for p in permissions.CATALOG}
    assert client.get("/api/auth/me", headers=as_user(requester)).json()["permissions"] == []
    # Today's admin-only areas stay admin-only, with the reason.
    r = client.get("/api/ticket-rules", headers=as_user(agent))
    assert r.status_code == 403 and r.json()["detail"] == "Ticket rules: create, change, test and run: only admins may (Settings > Permissions)"
    assert client.get("/api/ticket-rules", headers=as_user(admin)).status_code == 200


def test_hand_an_area_to_a_group(client, db, admin, agent):
    leads = Group(name="Service Desk Leads", members=[db.merge(agent)])
    db.add(leads)
    db.commit()
    r = client.put("/api/permissions", json={"rules_manage": {"who": "groups", "group_ids": [leads.id]}, "notices_manage": {"who": "agents"}}, headers=as_user(admin))
    assert r.status_code == 200, r.text
    rules = {p["key"]: p for p in r.json()}
    assert rules["rules_manage"]["who"] == "groups" and rules["rules_manage"]["group_ids"] == [leads.id] and rules["ticket_delete"]["who"] == "admins"
    assert client.get("/api/ticket-rules", headers=as_user(agent)).status_code == 200
    assert client.get("/api/notices", headers=as_user(agent)).status_code == 200
    # Someone outside the group is told who may.
    other = make_user(db, "dcole@corp.local", UserRole.agent, "D. Cole")
    db.commit()
    assert client.get("/api/ticket-rules", headers=as_user(other)).json()["detail"] == "Ticket rules: create, change, test and run: only admins and members of Service Desk Leads may (Settings > Permissions)"
    # An inactive group grants nothing.
    leads.is_active = False
    db.commit()
    assert client.get("/api/ticket-rules", headers=as_user(agent)).status_code == 403


def test_saving_is_checked_and_admins_only(client, db, admin, agent):
    assert client.put("/api/permissions", json={"nope": {"who": "agents"}}, headers=as_user(admin)).status_code == 422
    assert "pick at least one existing group" in client.put("/api/permissions", json={"ticket_delete": {"who": "groups", "group_ids": []}}, headers=as_user(admin)).json()["detail"]
    assert client.put("/api/permissions", json={"ticket_delete": {"who": "groups", "group_ids": [999]}}, headers=as_user(admin)).status_code == 422
    assert client.put("/api/permissions", json={"ticket_delete": {"who": "agents"}}, headers=as_user(agent)).status_code == 403
    assert client.get("/api/permissions", headers=as_user(agent)).status_code == 403


def test_delete_a_ticket(client, db, admin, agent, requester, desk):
    t = ticket(db, desk, requester)
    child = ticket(db, desk, requester, parent_id=t.id)
    tid, cid = t.id, child.id
    db.add(Comment(ticket_id=t.id, author_id=agent.id, body="spam", is_internal=True, source=TicketSource.agent))
    db.commit()
    up = client.post(f"/api/tickets/{t.id}/attachments", files={"file": ("prize.pdf", b"%PDF", "application/pdf")}, headers=as_user(agent))
    assert up.status_code == 201, up.text
    key = db.query(Attachment).one().storage_key
    assert storage.path(key).exists()

    r = client.delete(f"/api/tickets/{tid}", headers=as_user(agent))
    assert r.status_code == 403 and "Delete tickets: only admins may" in r.json()["detail"]
    assert client.delete(f"/api/tickets/{tid}", headers=as_user(admin)).status_code == 204
    db.expire_all()
    assert db.get(Ticket, tid) is None and db.query(Comment).count() == 0 and db.query(Attachment).count() == 0
    assert not storage.path(key).exists()
    # The child ticket stays, on its own.
    assert db.get(Ticket, cid).parent_id is None
    assert client.delete(f"/api/tickets/{tid}", headers=as_user(admin)).status_code == 404

    # Handed to every technician.
    permissions.save(db, {"ticket_delete": {"who": "agents"}})
    db.commit()
    assert client.delete(f"/api/tickets/{cid}", headers=as_user(agent)).status_code == 204


def test_kb_publishing(client, db, admin, agent):
    permissions.save(db, {"kb_publish": {"who": "admins"}})
    db.commit()
    body = {"title": "VPN", "body": "x", "audience": "everyone"}
    draft = client.post("/api/kb/articles", json={**body, "status": "draft"}, headers=as_user(agent))
    assert draft.status_code == 201
    assert client.post("/api/kb/articles", json={**body, "status": "published"}, headers=as_user(agent)).status_code == 403
    assert client.put(f"/api/kb/articles/{draft.json()['id']}", json={**body, "status": "published"}, headers=as_user(agent)).status_code == 403
    assert client.put(f"/api/kb/articles/{draft.json()['id']}", json={**body, "status": "published"}, headers=as_user(admin)).status_code == 200
    # Changing a published article needs it too; drafts don't.
    assert client.put(f"/api/kb/articles/{draft.json()['id']}", json={**body, "title": "VPN!", "status": "published"}, headers=as_user(agent)).status_code == 403
    assert db.query(KbArticle).count() == 1


def test_scripts_writers_see_and_edit(client, db, admin, agent):
    writers = Group(name="Script writers", members=[db.merge(agent)])
    db.add(writers)
    db.commit()
    body = {"name": "Restart spooler", "kind": "action", "language": "powershell", "body": "Restart-Service Spooler", "parameters": []}
    assert client.post("/api/scripts", json=body, headers=as_user(agent)).status_code == 403
    permissions.save(db, {"scripts_manage": {"who": "groups", "group_ids": [writers.id]}})
    db.commit()
    s = client.post("/api/scripts", json={**body, "is_active": False}, headers=as_user(agent))
    assert s.status_code == 201, s.text
    assert s.json()["can_edit"] is True
    # Inactive ones are visible to writers.
    assert [x["name"] for x in client.get("/api/scripts", headers=as_user(agent)).json()] == ["Restart spooler"]


def test_answers_file(db, agent):
    db.add(Group(name="IT Onboarding"))
    db.commit()
    done = cli.import_settings(db, {"permissions": {"ticket_delete": {"who": "groups", "groups": ["it onboarding"]}}, "directory": {"ad_new_user_allowed": "admins"}})
    assert any(d.startswith("permissions:") for d in done)
    assert permissions.rule(db, "ticket_delete")["who"] == "groups" and permissions.rule(db, "ad_create_accounts")["who"] == "admins"
    with pytest.raises(configstore.SettingsError, match="no group called 'Nobody'"):
        cli.import_settings(db, {"permissions": {"kb_delete": {"who": "groups", "groups": ["Nobody"]}}})


def test_queues_handed_to_a_group(client, db, admin, agent, desk):
    body = {"name": "Facilities"}
    r = client.post("/api/queues", json=body, headers=as_user(agent))
    assert r.status_code == 403 and "Queues: create them and change any queue: only admins may" in r.json()["detail"]
    assert client.patch(f"/api/queues/{desk.id}", json={"description": "x"}, headers=as_user(agent)).status_code == 403
    leads = Group(name="Desk leads", members=[db.merge(agent)])
    db.add(leads)
    db.commit()
    permissions.save(db, {"queues_manage": {"who": "groups", "group_ids": [leads.id]}})
    db.commit()
    q = client.post("/api/queues", json=body, headers=as_user(agent))
    assert q.status_code == 201, q.text
    assert client.patch(f"/api/queues/{desk.id}", json={"restricted": True}, headers=as_user(agent)).status_code == 200
    assert client.put(f"/api/queues/{q.json()['id']}/managers", json={"user_ids": [agent.id], "group_ids": []}, headers=as_user(agent)).status_code == 200
    # A plain queue manager edits their queue, but not whether it's restricted.
    permissions.save(db, {"queues_manage": {"who": "admins"}})
    db.commit()
    assert client.patch(f"/api/queues/{q.json()['id']}", json={"description": "Buildings"}, headers=as_user(agent)).status_code == 200
    r = client.patch(f"/api/queues/{q.json()['id']}", json={"restricted": True}, headers=as_user(agent))
    assert r.status_code == 403 and r.json()["detail"].startswith("Changing is_default, is_active or restricted: Queues")


def test_builtin_scripts_only_run_from_their_feature(client, db, admin, agent):
    from app import adchanges, ipamscan

    ad = adchanges.ensure_script(db)
    scan = ipamscan.ensure_script(db)
    db.commit()
    for s in (ad, scan):
        r = client.post(f"/api/scripts/{s.id}/runs", json={"on_runner": True, "values": {}}, headers=as_user(admin))
        assert r.status_code == 422 and "Built-in scripts run from their own place" in r.json()["detail"]
    sched = {"name": "x", "script_id": ad.id, "on_runner": True, "frequency": "daily", "time": "02:00", "start_date": "2026-10-22"}
    assert "Built-in scripts" in client.post("/api/script-schedules", json=sched, headers=as_user(admin)).json()["detail"]
    # Nobody can make a lookalike, and writers who aren't admins can't change built-ins.
    body = {"name": "Layer 8: change an AD account", "kind": "action", "language": "powershell", "body": "evil", "parameters": []}
    assert client.post("/api/scripts", json=body, headers=as_user(admin)).status_code == 422
    assert client.post("/api/scripts", json={**body, "name": " layer 8: IP scan"}, headers=as_user(admin)).status_code == 422
    writers = Group(name="Writers", members=[db.merge(agent)])
    db.add(writers)
    db.commit()
    permissions.save(db, {"scripts_manage": {"who": "groups", "group_ids": [writers.id]}})
    db.commit()
    upd = {"name": ad.name, "kind": "action", "language": "powershell", "body": "evil", "parameters": [], "agents_can_run": True, "high_risk": False}
    assert client.put(f"/api/scripts/{ad.id}", json=upd, headers=as_user(agent)).status_code == 403
    assert client.delete(f"/api/scripts/{ad.id}", headers=as_user(agent)).status_code == 403


def test_only_admins_decide_who_runs_a_script(client, db, admin, agent):
    writers = Group(name="Writers", members=[db.merge(agent)])
    db.add(writers)
    db.commit()
    permissions.save(db, {"scripts_manage": {"who": "groups", "group_ids": [writers.id]}})
    db.commit()
    body = {"name": "Reboot", "kind": "action", "language": "powershell", "body": "Restart-Computer", "parameters": [], "agents_can_run": True, "high_risk": False}
    made = client.post("/api/scripts", json=body, headers=as_user(agent)).json()
    assert (made["agents_can_run"], made["high_risk"]) == (False, True)
    r = client.put(f"/api/scripts/{made['id']}", json={**body, "agents_can_run": True, "high_risk": True}, headers=as_user(agent))
    assert r.status_code == 403 and "Only admins" in r.json()["detail"]
    assert client.put(f"/api/scripts/{made['id']}", json={**body, "body": "Restart-Computer -Force", "agents_can_run": False, "high_risk": True}, headers=as_user(agent)).status_code == 200
    assert client.put(f"/api/scripts/{made['id']}", json=body, headers=as_user(admin)).json()["agents_can_run"] is True


def test_the_last_active_admin_stays(client, db, admin):
    other = make_user(db, "it@corp.local", UserRole.admin)
    db.commit()
    assert client.patch(f"/api/users/{other.id}", json={"is_active": False}, headers=as_user(admin)).status_code == 200
    r = client.patch(f"/api/users/{admin.id}", json={"role": "agent"}, headers=as_user(admin))
    assert r.status_code == 409 and "at least one active admin" in r.json()["detail"]
