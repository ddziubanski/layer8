"""Checklists: templates, starting one on a ticket (child tickets, due dates, queues, technicians), and the offboarding disable."""

from datetime import date, timedelta

import pytest

from app import adchanges, cli
from app.models import ChecklistTemplate, Comment, Queue, ScriptRun, Ticket, TicketSource, UserRole
from tests.conftest import as_user, make_user

LAST_DAY = date.today() + timedelta(days=10)


def _setup(db, requester):
    desk = Queue(name="Desk", is_default=True)
    facilities = Queue(name="Facilities")
    db.add_all([desk, facilities])
    db.flush()
    t = Ticket(subject="Leaver: Pat Lee", source=TicketSource.portal, queue_id=desk.id, requester_id=requester.id)
    db.add(t)
    db.commit()
    return t, desk, facilities


def test_templates_are_admin_only(client, db, agent, admin):
    body = {"name": "Leaver", "kind": "offboarding", "items": [{"title": "Collect laptop", "offset_days": 0}]}
    assert client.post("/api/checklist-templates", json=body, headers=as_user(agent)).status_code == 403
    r = client.post("/api/checklist-templates", json=body, headers=as_user(admin))
    assert r.status_code == 201, r.text
    assert client.post("/api/checklist-templates", json={**body, "name": "leaver"}, headers=as_user(admin)).status_code == 409
    bad = {**body, "name": "X", "items": [{"title": "A", "queue_id": 999999}]}
    assert client.post("/api/checklist-templates", json=bad, headers=as_user(admin)).status_code == 422
    # Technicians see the ones in use, to start them.
    assert [t["name"] for t in client.get("/api/checklist-templates", headers=as_user(agent)).json()] == ["Leaver"]


def test_start_offboarding(client, db, agent, admin, requester):
    t, desk, facilities = _setup(db, requester)
    tech = make_user(db, "tech@corp.local", UserRole.agent, "Tech One")
    leaver = make_user(db, "plee@corp.local", UserRole.requester, "Pat Lee")
    leaver.directory_id, leaver.directory_dn = "guid-plee", "CN=Pat Lee,OU=Staff,DC=corp,DC=local"
    db.commit()
    body = {
        "name": "Leaver", "kind": "offboarding",
        "items": [
            {"title": "Collect laptop and badge", "details": "From reception", "queue_id": facilities.id, "offset_days": 0},
            {"title": "Back up mailbox", "assignee_id": tech.id, "offset_days": -2},
            {"title": "Disable account", "offset_days": 1, "action": "disable_ad_account"},
        ],
    }
    tpl = client.post("/api/checklist-templates", json=body, headers=as_user(admin)).json()
    start = {"template_id": tpl["id"], "key_date": str(LAST_DAY), "for_user_id": leaver.id}
    # The disable step needs an AD person and a date.
    assert client.post(f"/api/tickets/{t.id}/checklists", json={**start, "for_user_id": None, "for_name": "Pat"}, headers=as_user(agent)).status_code == 422
    assert client.post(f"/api/tickets/{t.id}/checklists", json={**start, "key_date": None}, headers=as_user(agent)).status_code == 422
    r = client.post(f"/api/tickets/{t.id}/checklists", json=start, headers=as_user(agent))
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["for_name"] == "Pat Lee" and [i["due_on"] for i in c["items"]] == [str(LAST_DAY), str(LAST_DAY - timedelta(days=2)), str(LAST_DAY + timedelta(days=1))]
    laptop, mailbox, disable = c["items"]
    assert laptop["child_ticket"]["subject"] == "Collect laptop and badge: Pat Lee" and laptop["child_ticket"]["queue"]["name"] == "Facilities"
    assert mailbox["child_ticket"]["assignee"]["display_name"] == "Tech One" and mailbox["child_ticket"]["queue"]["name"] == "Desk"
    child = db.get(Ticket, laptop["child_ticket"]["id"])
    assert child.parent_id == t.id and "From reception" in child.description and child.resolution_due_at.astimezone().date() == LAST_DAY
    # The disable waits for the day after the last day, on the child ticket.
    run = db.get(ScriptRun, disable["run_id"])
    assert run.script.name == adchanges.DISABLE_SCRIPT_NAME and run.values == {"user_guid": "guid-plee"}
    assert run.not_before.astimezone().date() == LAST_DAY + timedelta(days=1) and run.ticket_id == disable["child_ticket"]["id"]
    note = next(x.body for x in db.query(Comment).filter_by(ticket_id=t.id) if x.body.startswith("Started checklist"))
    assert "Leaver" in note and "last day" in note and "Collect laptop and badge: Pat Lee" in note
    got = client.get(f"/api/tickets/{t.id}/checklists", headers=as_user(agent)).json()
    assert len(got) == 1 and len(got[0]["items"]) == 3


def test_onboarding_for_a_name_and_inactive_template(client, db, agent, admin, requester):
    t, *_ = _setup(db, requester)
    tpl = client.post("/api/checklist-templates", json={"name": "Starter", "kind": "onboarding", "items": [{"title": "Order laptop", "offset_days": -7}]}, headers=as_user(admin)).json()
    r = client.post(f"/api/tickets/{t.id}/checklists", json={"template_id": tpl["id"], "for_name": "John Smith"}, headers=as_user(agent))
    assert r.status_code == 201 and r.json()["items"][0]["due_on"] is None
    assert client.post(f"/api/tickets/{t.id}/checklists", json={"template_id": tpl["id"]}, headers=as_user(agent)).status_code == 422
    client.put(f"/api/checklist-templates/{tpl['id']}", json={"name": "Starter", "kind": "onboarding", "items": [{"title": "x"}], "is_active": False}, headers=as_user(admin))
    assert client.post(f"/api/tickets/{t.id}/checklists", json={"template_id": tpl["id"], "for_name": "J"}, headers=as_user(agent)).status_code == 422


def test_checklists_from_the_answers_file(db, admin):
    db.add(Queue(name="Facilities"))
    db.commit()
    answers = {"checklist_templates": [{"name": "Leaver", "kind": "offboarding", "items": [
        {"title": "Collect laptop", "queue": "facilities", "offset_days": 0},
        {"title": "Back up mailbox", "assignee": admin.email},
    ]}]}
    cli.import_settings(db, answers)
    db.commit()
    tpl = db.query(ChecklistTemplate).one()
    assert tpl.kind == "offboarding" and tpl.items[0]["queue_id"] is not None and tpl.items[1]["assignee_id"] == admin.id
    with pytest.raises(Exception, match="no queue"):
        cli.import_settings(db, {"checklist_templates": [{"name": "X", "items": [{"title": "a", "queue": "Nope"}]}]})


def test_steps_stay_in_the_tickets_queue_despite_keyword_rules(client, db, agent, admin, requester):
    from app.models import RoutingRule

    t, desk, facilities = _setup(db, requester)
    db.add(RoutingRule(name="password keyword", position=1, match_keywords=["password"], set_queue_id=facilities.id))
    db.commit()
    tpl = client.post("/api/checklist-templates", json={"name": "Starter", "kind": "onboarding", "items": [{"title": "Set first password"}]}, headers=as_user(admin)).json()
    c = client.post(f"/api/tickets/{t.id}/checklists", json={"template_id": tpl["id"], "for_name": "J"}, headers=as_user(agent)).json()
    assert c["items"][0]["child_ticket"]["queue"]["name"] == "Desk"
