"""Child tickets: create, list, re-parent without loops, block closing the parent, notes."""

import pytest

from app.config import settings
from app.models import Comment, Queue, TicketEvent
from tests.conftest import as_user, make_user


@pytest.fixture
def desk(db):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.commit()
    return q


def create(client, who, **body):
    r = client.post("/api/tickets", json={"subject": "x", **body}, headers=as_user(who))
    assert r.status_code == 201, r.text
    return r.json()


def test_child_for_another_technician(client, db, agent, requester, desk):
    parent = create(client, agent, subject="New starter: Ana", requester_id=requester.id)
    other = make_user(db, "dcole@corp.local", agent.role, "D. Cole")
    db.commit()
    child = create(client, agent, subject="Set up laptop", parent_id=parent["id"])
    assert {k: child["parent"][k] for k in ("id", "subject", "status", "label")} == {"id": parent["id"], "subject": "New starter: Ana", "status": "new", "label": f"#{parent['id']}"}
    # It takes the parent's requester.
    assert child["requester"]["id"] == requester.id
    client.post(f"/api/tickets/{child['id']}/assign", json={"assignee_id": other.id}, headers=as_user(agent))
    kids = client.get(f"/api/tickets/{parent['id']}/children", headers=as_user(agent)).json()
    assert [(k["subject"], k["assignee"]["display_name"]) for k in kids] == [("Set up laptop", "D. Cole")]
    p = client.get(f"/api/tickets/{parent['id']}", headers=as_user(agent)).json()
    assert (p["child_count"], p["open_child_count"]) == (1, 1)
    assert db.query(TicketEvent).filter_by(ticket_id=parent["id"], type="child_changed").one().data["action"] == "added"


def test_parent_stays_open_until_children_are_done(client, db, agent, requester, desk, monkeypatch):
    monkeypatch.setattr(settings, "require_resolution_type", False)
    monkeypatch.setattr(settings, "require_closure_reason", False)
    parent = create(client, agent, requester_id=requester.id)
    child = create(client, agent, subject="Order monitor", parent_id=parent["id"])
    r = client.patch(f"/api/tickets/{parent['id']}", json={"status": "resolved"}, headers=as_user(agent))
    assert r.status_code in (409, 422) and f"#{child['id']}" in r.json()["detail"]
    assert client.patch(f"/api/tickets/{child['id']}", json={"status": "resolved"}, headers=as_user(agent)).status_code == 200
    note = db.query(Comment).filter_by(ticket_id=parent["id"]).one()
    assert note.is_internal and f"Child ticket #{child['id']}" in note.body and "All child tickets are done" in note.body
    assert client.patch(f"/api/tickets/{parent['id']}", json={"status": "resolved"}, headers=as_user(agent)).status_code == 200
    # The setting can turn the block off.
    monkeypatch.setattr(settings, "block_parent_close", False)
    p2 = create(client, agent, requester_id=requester.id)
    create(client, agent, parent_id=p2["id"])
    assert client.patch(f"/api/tickets/{p2['id']}", json={"status": "resolved"}, headers=as_user(agent)).status_code == 200


def test_reparent_without_loops(client, db, agent, requester, desk):
    a = create(client, agent, subject="A", requester_id=requester.id)
    b = create(client, agent, subject="B", parent_id=a["id"])
    c = create(client, agent, subject="C", parent_id=b["id"])
    r = client.patch(f"/api/tickets/{a['id']}", json={"parent_id": c["id"]}, headers=as_user(agent))
    assert r.status_code in (409, 422) and "own child" in r.json()["detail"]
    assert client.patch(f"/api/tickets/{a['id']}", json={"parent_id": a["id"]}, headers=as_user(agent)).status_code in (409, 422)
    # Move C up to A, then detach it.
    assert client.patch(f"/api/tickets/{c['id']}", json={"parent_id": a["id"]}, headers=as_user(agent)).json()["parent"]["id"] == a["id"]
    assert client.get(f"/api/tickets/{b['id']}/children", headers=as_user(agent)).json() == []
    assert client.patch(f"/api/tickets/{c['id']}", json={"parent_id": None}, headers=as_user(agent)).json()["parent"] is None
    actions = [e.data["action"] for e in db.query(TicketEvent).filter_by(ticket_id=b["id"], type="child_changed").order_by(TicketEvent.id)]
    assert actions == ["added", "removed"]
