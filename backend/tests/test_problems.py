# SPDX-License-Identifier: AGPL-3.0-or-later
"""Problem records group visible incidents and keep their investigation notes internal."""

from app.models import Priority, Queue, Ticket, TicketSource, TicketStatus
from tests.conftest import as_user


def make_ticket(db, requester, queue, subject):
    ticket = Ticket(subject=subject, source=TicketSource.agent, requester_id=requester.id, queue_id=queue.id, status=TicketStatus.open, priority=Priority.medium)
    db.add(ticket)
    db.commit()
    return ticket


def test_create_problem_link_tickets_and_record_updates(client, db, agent, requester):
    queue = Queue(name="Desk", is_default=True)
    db.add(queue)
    db.commit()
    ticket = make_ticket(db, requester, queue, "VPN disconnects after sleep")

    created = client.post("/api/problems", json={
        "title": "VPN clients disconnect after sleep",
        "description": "Several users reported disconnects after waking laptops.",
        "status": "investigating",
        "root_cause": "",
        "workaround": "Reconnect the client.",
        "resolution": "",
        "ticket_ids": [ticket.id],
    }, headers=as_user(agent))
    assert created.status_code == 201, created.text
    problem = created.json()
    assert problem["label"] == f"PRB-{problem['id']:05d}"
    assert problem["status"] == "investigating"
    assert [linked["id"] for linked in problem["tickets"]] == [ticket.id]
    assert [event["type"] for event in problem["updates"]] == ["created", "ticket_linked"]

    note = client.post(f"/api/problems/{problem['id']}/updates", json={"body": "Confirmed with two affected users."}, headers=as_user(agent))
    assert note.status_code == 201, note.text
    assert note.json()["type"] == "note" and note.json()["body"] == "Confirmed with two affected users."

    updated = client.put(f"/api/problems/{problem['id']}", json={
        "title": problem["title"],
        "description": problem["description"],
        "status": "known_error",
        "root_cause": "The VPN adapter driver resets after resume.",
        "workaround": "Reconnect the client.",
        "resolution": "",
        "owner_id": agent.id,
        "ticket_ids": [ticket.id],
    }, headers=as_user(agent))
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "known_error"
    assert updated.json()["root_cause"].startswith("The VPN adapter")
    assert [event["type"] for event in updated.json()["updates"][-2:]] == ["status_changed", "edited"]
    assert client.get("/api/problems", params={"status": "known_error"}, headers=as_user(agent)).json()[0]["id"] == problem["id"]


def test_problem_visibility_requires_access_to_every_linked_ticket(client, db, admin, agent, requester):
    public_queue = Queue(name="Desk", is_default=True)
    restricted_queue = Queue(name="People", restricted=True)
    db.add_all([public_queue, restricted_queue])
    db.commit()
    public_ticket = make_ticket(db, requester, public_queue, "Laptop Wi-Fi drops")
    restricted_ticket = make_ticket(db, requester, restricted_queue, "Confidential account issue")

    hidden_problem = client.post("/api/problems", json={
        "title": "Network driver regression",
        "ticket_ids": [public_ticket.id, restricted_ticket.id],
    }, headers=as_user(admin)).json()
    visible_problem = client.post("/api/problems", json={
        "title": "Wi-Fi driver reports",
        "ticket_ids": [public_ticket.id],
    }, headers=as_user(admin)).json()

    listing = client.get("/api/problems", headers=as_user(agent))
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [visible_problem["id"]]
    assert client.get(f"/api/problems/{hidden_problem['id']}", headers=as_user(agent)).status_code == 404
    assert client.post("/api/problems", json={"title": "Leak check", "ticket_ids": [restricted_ticket.id]}, headers=as_user(agent)).status_code == 404


def test_requesters_cannot_access_problem_management(client, requester):
    assert client.get("/api/problems", headers=as_user(requester)).status_code == 403
    assert client.post("/api/problems", json={"title": "Requester-created"}, headers=as_user(requester)).status_code == 403


def test_timeline_never_reveals_a_ticket_subject(client, db, admin, agent, requester):
    # Unlinking a restricted ticket makes the problem visible to everyone; its history must not
    # carry that ticket's subject along.
    public_queue = Queue(name="Desk", is_default=True)
    restricted_queue = Queue(name="People", restricted=True)
    db.add_all([public_queue, restricted_queue])
    db.commit()
    public_ticket = make_ticket(db, requester, public_queue, "Laptop Wi-Fi drops")
    restricted_ticket = make_ticket(db, requester, restricted_queue, "Confidential account issue")
    problem = client.post("/api/problems", json={"title": "Driver regression", "ticket_ids": [public_ticket.id, restricted_ticket.id]}, headers=as_user(admin)).json()
    r = client.put(f"/api/problems/{problem['id']}", json={"title": "Driver regression", "ticket_ids": [public_ticket.id]}, headers=as_user(admin))
    assert r.status_code == 200, r.text

    seen = client.get(f"/api/problems/{problem['id']}", headers=as_user(agent))
    assert seen.status_code == 200
    assert "Confidential" not in seen.text
    assert [u["body"] for u in seen.json()["updates"] if u["type"].startswith("ticket_")] == [
        f"Linked ticket #{public_ticket.id}", f"Linked ticket #{restricted_ticket.id}", f"Unlinked ticket #{restricted_ticket.id}"]
