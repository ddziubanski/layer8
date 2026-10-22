"""Merging tickets: what moves, the reason, the closed duplicate, email threading, and the rules."""

import pytest

from app.mail.intake import _thread_ticket
from app.mail.types import InboundMessage
from app.models import Comment, Device, FieldOption, Queue, Ticket, TicketDevice, TicketEvent
from tests.conftest import as_user, make_user


@pytest.fixture
def desk(db):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.commit()
    return q


def reason(db, label="Duplicate"):
    return db.query(FieldOption).filter_by(list="merge_reason", label=label).one()


def create(client, who, **body):
    r = client.post("/api/tickets", json={"subject": "x", **body}, headers=as_user(who))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def reasons(db):
    # The test database is emptied between tests; put back what the migration seeds.
    for i, label in enumerate(["Duplicate", "Same issue reported by someone else"]):
        db.add(FieldOption(list="merge_reason", label=label, position=i))
    db.add(FieldOption(list="closure_reason", label="Merged into another ticket", position=1000, key="merged"))
    db.commit()


def test_merge_moves_replies_files_devices_and_closes_the_duplicate(client, db, agent, requester, desk, reasons):
    other = make_user(db, "mosei@corp.local", name="M. Osei")
    db.commit()
    keep = create(client, agent, subject="Printer down on 2nd floor", requester_id=requester.id)
    dup = create(client, agent, subject="2nd floor printer not working", requester_id=other.id)
    client.post(f"/api/tickets/{dup['id']}/comments", json={"body": "It shows error 50.1", "is_internal": False}, headers=as_user(agent))
    pc = Device(name="PRN-2F")
    db.add(pc)
    db.commit()
    client.post(f"/api/tickets/{dup['id']}/devices", json={"device_id": pc.id}, headers=as_user(agent))

    r = client.post(f"/api/tickets/{dup['id']}/merge", json={"into_id": keep["id"], "reason_id": reason(db, "Same issue reported by someone else").id, "note": "Same printer"}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == keep["id"]
    bodies = [c.body for c in db.query(Comment).filter_by(ticket_id=keep["id"]).order_by(Comment.id)]
    assert "It shows error 50.1" in bodies
    note = bodies[-1]
    assert f"Merged #{dup['id']}" in note and "Same issue reported by someone else" in note and "Same printer" in note and "M. Osei" in note
    assert db.query(TicketDevice).filter_by(ticket_id=keep["id"], device_id=pc.id).count() == 1

    gone = client.get(f"/api/tickets/{dup['id']}", headers=as_user(agent)).json()
    assert gone["status"] == "closed" and gone["merged_into"]["id"] == keep["id"] and gone["closure_reason"]["label"] == "Merged into another ticket"
    assert {e.data["direction"] for e in db.query(TicketEvent).filter_by(type="merged")} == {"in", "out"}

    # A reply to the merged ticket's email lands on the one it was merged into.
    msg = InboundMessage(mailbox_id="m1", internet_message_id="<x@y>", from_address="mosei@corp.local", from_name="M. Osei", subject=f"Re: [#{dup['id']}] printer", body_text="Still broken")
    assert _thread_ticket(db, msg)[0].id == keep["id"]


def test_merge_rules(client, db, agent, requester, desk, reasons):
    a = create(client, agent, requester_id=requester.id)
    b = create(client, agent, requester_id=requester.id)
    child = create(client, agent, parent_id=a["id"])
    dup = reason(db).id
    merge = lambda s, t, rid=dup: client.post(f"/api/tickets/{s}/merge", json={"into_id": t, "reason_id": rid}, headers=as_user(agent))  # noqa: E731
    assert "into itself" in merge(a["id"], a["id"]).json()["detail"]
    assert "parent instead" in merge(a["id"], child["id"]).json()["detail"]
    closure = db.query(FieldOption).filter_by(list="closure_reason").first().id
    assert "Pick why" in merge(b["id"], a["id"], closure).json()["detail"]
    # Merging a parent moves its child tickets over.
    assert merge(a["id"], b["id"]).status_code == 200
    assert db.get(Ticket, child["id"]).parent_id == b["id"]
    assert "already merged" in merge(a["id"], b["id"]).json()["detail"]
    assert "merge into that one" in merge(create(client, agent, requester_id=requester.id)["id"], a["id"]).json()["detail"]
    assert merge(b["id"], 999999).status_code == 404
