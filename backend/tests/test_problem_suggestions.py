# SPDX-License-Identifier: AGPL-3.0-or-later
"""Suggest problems from patterns: evidence, visibility, one click, dismissible."""
from app.config import settings
from app.models import Device, Priority, Queue, Ticket, TicketSource, TicketStatus
from tests.conftest import as_user


def make(db, requester, queue, subject, device=None):
    t = Ticket(subject=subject, source=TicketSource.portal, requester_id=requester.id, queue_id=queue.id, status=TicketStatus.open,
               priority=Priority.medium, computer_id=device.id if device else None)
    db.add(t)
    db.flush()
    return t


def test_shared_model_is_suggested_then_created_or_dismissed(client, db, agent, requester):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    laptops = [Device(name=f"LT-{i}", model="Latitude 5440") for i in range(3)]
    db.add_all(laptops)
    db.flush()
    ids = [make(db, requester, q, s, d).id for s, d in zip(["Dock not charging", "No display on dock", "USB-C dock dead"], laptops)]
    make(db, requester, q, "Password reset")
    db.commit()
    got = client.get("/api/problems/suggestions", headers=as_user(agent)).json()
    assert len(got) == 1
    s = got[0]
    assert s["kind"] == "model" and s["evidence"] == "3 tickets in 48 hours share Device model Latitude 5440"
    assert sorted(t["id"] for t in s["tickets"]) == sorted(ids)
    # Dismissed: gone until the set of tickets changes.
    assert client.post("/api/problems/suggestions/dismiss", json={"key": s["key"]}, headers=as_user(agent)).status_code == 204
    assert client.get("/api/problems/suggestions", headers=as_user(agent)).json() == []
    make(db, requester, q, "Dock flickers", laptops[0])
    db.commit()
    back = client.get("/api/problems/suggestions", headers=as_user(agent)).json()
    assert len(back) == 1 and len(back[0]["tickets"]) == 4
    # One click: a problem with them linked; they're no longer suggested.
    created = client.post("/api/problems", json={"title": back[0]["suggested_title"], "ticket_ids": [t["id"] for t in back[0]["tickets"]]}, headers=as_user(agent))
    assert created.status_code == 201, created.text
    assert client.get("/api/problems/suggestions", headers=as_user(agent)).json() == []


def test_shared_words_and_off_switch(client, db, agent, requester, monkeypatch):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    for s in ["Outlook keeps crashing", "outlook won't open", "Can't send from Outlook"]:
        make(db, requester, q, s)
    db.commit()
    got = client.get("/api/problems/suggestions", headers=as_user(agent)).json()
    assert [g["kind"] for g in got] == ["words"] and "outlook" in got[0]["evidence"]
    monkeypatch.setattr(settings, "problem_suggest_tickets", 0)
    assert client.get("/api/problems/suggestions", headers=as_user(agent)).json() == []
    assert client.get("/api/problems/suggestions", headers=as_user(requester)).status_code == 403
