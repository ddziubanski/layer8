"""Changes: risk, required plans, approval by someone else when the risk calls for it, standard and emergency changes, ticket notes."""

from app.config import settings
from app.models import Comment, Group, Queue, Ticket, TicketSource, UserRole
from tests.conftest import as_user, make_user

PLANS = {"implementation_plan": "Upgrade the stack one member at a time", "rollback_plan": "Boot the previous image", "planned_start": "2026-11-01T22:00:00Z", "planned_end": "2026-11-01T23:30:00Z"}


def act(client, user, cid, action, note=""):
    return client.post(f"/api/changes/{cid}/actions", json={"action": action, "note": note}, headers=as_user(user))


def test_normal_change_needs_approval_by_someone_else(client, db, agent, admin, requester):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    t = Ticket(subject="Core switch firmware", source=TicketSource.agent, queue_id=q.id, requester_id=requester.id)
    db.add(t)
    db.commit()
    body = {"title": "Core switch firmware 17.1", "impact": 3, "likelihood": 2, "ticket_ids": [t.id]}
    r = client.post("/api/changes", json=body, headers=as_user(agent))
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["label"], c["risk"], c["risk_level"], c["needs_approval"], c["status"], c["owner"]["id"]) == (f"CHG-{c['id']:05d}", 6, "high", True, "draft", agent.id)
    # Not without plans and a start.
    assert "rollback plan" in act(client, agent, c["id"], "submit").json()["detail"]
    client.put(f"/api/changes/{c['id']}", json={**body, **PLANS}, headers=as_user(agent))
    assert act(client, agent, c["id"], "submit").json()["status"] == "awaiting_approval"
    assert act(client, agent, c["id"], "start").status_code == 409
    # Agents aren't approvers by default; admins are, but not for their own change.
    assert act(client, agent, c["id"], "approve").status_code == 409
    assert act(client, admin, c["id"], "reject").json()["detail"] == "Say why it's rejected"
    got = act(client, admin, c["id"], "approve", "Go ahead in the window")
    assert got.json()["status"] == "approved" and got.json()["approved_by"]["id"] == admin.id
    assert act(client, agent, c["id"], "start").json()["status"] == "in_progress"
    assert act(client, agent, c["id"], "roll_back").json()["detail"] == "Say what happened"
    done = act(client, agent, c["id"], "roll_back", "Stack member 2 didn't rejoin; previous image booted").json()
    assert done["status"] == "rolled_back" and done["outcome"].startswith("Stack member 2")
    assert [e["type"] for e in done["events"]] == ["created", "edited", "submitted", "approved", "started", "rolled_back"]
    notes = [x.body for x in db.query(Comment).filter_by(ticket_id=t.id)]
    assert any("has started" in n for n in notes) and any("was rolled back: Stack member 2" in n for n in notes)
    assert [x["id"] for x in client.get(f"/api/tickets/{t.id}/changes", headers=as_user(agent)).json()] == [c["id"]]
    # Ended: no edits.
    assert client.put(f"/api/changes/{c['id']}", json=body, headers=as_user(agent)).status_code == 409


def test_low_risk_needs_no_approval_and_editing_resets(client, db, agent, admin, monkeypatch):
    c = client.post("/api/changes", json={"title": "New printer queue", **PLANS}, headers=as_user(agent)).json()
    assert c["risk_level"] == "low" and not c["needs_approval"]
    assert act(client, agent, c["id"], "submit").json()["status"] == "approved"
    # At "every change" it would have needed approval; editing one awaiting approval sends it back to draft.
    monkeypatch.setattr(settings, "change_approval_level", "low")
    c2 = client.post("/api/changes", json={"title": "Firewall rule", **PLANS}, headers=as_user(agent)).json()
    assert act(client, agent, c2["id"], "submit").json()["status"] == "awaiting_approval"
    edited = client.put(f"/api/changes/{c2['id']}", json={"title": "Firewall rule (port 443 only)", **PLANS}, headers=as_user(agent)).json()
    assert edited["status"] == "draft" and "Back to draft" in edited["events"][-1]["note"]


def test_approvers_group(client, db, agent, admin, monkeypatch):
    board = make_user(db, "board@corp.local", UserRole.agent, "Board Member")
    db.add(Group(name="Change Board", members=[db.merge(board)]))
    db.commit()
    monkeypatch.setattr(settings, "change_approvers_group", "change board")
    c = client.post("/api/changes", json={"title": "Risky", "impact": 3, "likelihood": 3, **PLANS}, headers=as_user(agent)).json()
    act(client, agent, c["id"], "submit")
    assert act(client, admin, c["id"], "approve").status_code == 409  # not on the board
    assert client.get(f"/api/changes/{c['id']}", headers=as_user(board)).json()["can_approve"]
    assert act(client, board, c["id"], "approve").json()["status"] == "approved"


def test_standard_and_emergency(client, db, agent, admin):
    assert client.post("/api/change-templates", json={"name": "Patch ring 1", "pre_approved": True}, headers=as_user(admin)).status_code == 422
    tpl = client.post("/api/change-templates", json={"name": "Patch ring 1", "pre_approved": True, "rollback_plan": "Uninstall the update", "impact": 2, "likelihood": 2}, headers=as_user(admin)).json()
    assert client.post("/api/change-templates", json={"name": "x"}, headers=as_user(agent)).status_code == 403
    # Standard: from a pre-approved template only, and approved on submit whatever the risk.
    assert client.post("/api/changes", json={"title": "Patching", "kind": "standard"}, headers=as_user(agent)).status_code == 422
    std = client.post("/api/changes", json={"title": "October patches, ring 1", "kind": "standard", "template_id": tpl["id"], "impact": 3, "likelihood": 3, **PLANS}, headers=as_user(agent)).json()
    assert act(client, agent, std["id"], "submit").json()["status"] == "approved"
    # Emergency: starts at once (it still needs a rollback plan), approved afterwards.
    em = client.post("/api/changes", json={"title": "Block attacker IP", "kind": "emergency", "impact": 2, "likelihood": 3}, headers=as_user(agent)).json()
    assert act(client, agent, em["id"], "start").json()["detail"] == "Even an emergency change needs a rollback plan"
    client.put(f"/api/changes/{em['id']}", json={"title": "Block attacker IP", "kind": "emergency", "impact": 2, "likelihood": 3, "rollback_plan": "Remove the rule"}, headers=as_user(agent))
    started = act(client, agent, em["id"], "start").json()
    assert started["status"] == "in_progress" and started["events"][-1]["note"] == "Emergency: started before approval"
    act(client, agent, em["id"], "complete")
    late = act(client, admin, em["id"], "approve").json()
    assert late["status"] == "completed" and late["approved_at"]
    assert [x["id"] for x in client.get("/api/changes", params={"status": "completed"}, headers=as_user(agent)).json()] == [em["id"]]
    assert {x["id"] for x in client.get("/api/changes", params={"open_only": True}, headers=as_user(agent)).json()} == {std["id"]}


def test_change_templates_from_the_answers_file(db):
    from app import cli
    from app.models import ChangeTemplate

    cli.import_settings(db, {"change_templates": [{"name": "Patch ring 1", "pre_approved": True, "rollback_plan": "Uninstall", "impact": 2}]})
    db.commit()
    t = db.query(ChangeTemplate).one()
    assert t.pre_approved and t.impact == 2 and t.rollback_plan == "Uninstall"


def test_overlapping_windows_and_calendar_range(client, db, agent):
    from app.models import Device

    sw = Device(name="SW-CORE-1")
    db.add(sw)
    db.commit()
    a = client.post("/api/changes", json={"title": "Firmware", "planned_start": "2026-11-01T22:00:00Z", "planned_end": "2026-11-01T23:30:00Z", "device_ids": [sw.id]}, headers=as_user(agent)).json()
    b = client.post("/api/changes", json={"title": "Cabling", "planned_start": "2026-11-01T23:00:00Z", "device_ids": [sw.id]}, headers=as_user(agent)).json()
    client.post("/api/changes", json={"title": "Later", "planned_start": "2026-11-02T01:00:00Z"}, headers=as_user(agent))
    got = client.get(f"/api/changes/{a['id']}", headers=as_user(agent)).json()["conflicts"]
    assert [(x["change"]["id"], x["shared"]) for x in got] == [(b["id"], ["SW-CORE-1"])]
    # A cancelled one doesn't count.
    act(client, agent, b["id"], "cancel")
    assert client.get(f"/api/changes/{a['id']}", headers=as_user(agent)).json()["conflicts"] == []
    month = client.get("/api/changes", params={"starts_from": "2026-11-01T00:00:00Z", "starts_before": "2026-11-02T00:00:00Z"}, headers=as_user(agent)).json()
    assert [x["title"] for x in month] == ["Firmware", "Cabling"]
