# SPDX-License-Identifier: AGPL-3.0-or-later
"""Problems from the ticket's side: see them, link and unlink there, and keep it in the ticket's history."""
from sqlalchemy import select

from app.models import Priority, Problem, Queue, Ticket, TicketEvent, TicketSource, TicketStatus
from tests.conftest import as_user


def ticket_in(db, requester, queue, subject):
    t = Ticket(subject=subject, source=TicketSource.agent, requester_id=requester.id, queue_id=queue.id, status=TicketStatus.open, priority=Priority.medium)
    db.add(t)
    db.commit()
    return t


def events(db, ticket):
    return [(e.type.value, e.data.get("problem")) for e in db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.id))
            if e.type.value.startswith("problem_")]


def test_link_from_the_ticket_shows_the_workaround_and_is_in_its_history(client, db, agent, requester):
    desk = Queue(name="Desk", is_default=True)
    db.add(desk)
    db.commit()
    first, second = ticket_in(db, requester, desk, "VPN drops"), ticket_in(db, requester, desk, "VPN drops again")
    # Made from the first ticket: that ticket's history says so.
    made = client.post("/api/problems", json={"title": "VPN drops after sleep", "workaround": "Reconnect the client.", "ticket_ids": [first.id]}, headers=as_user(agent)).json()
    label = made["label"]
    assert events(db, first) == [("problem_linked", label)]

    url = f"/api/tickets/{second.id}/problems"
    assert client.get(url, headers=as_user(agent)).json() == {"problems": [], "hidden": 0}
    linked = client.post(url, json={"problem_id": made["id"]}, headers=as_user(agent))
    assert linked.status_code == 200, linked.text
    [shown] = linked.json()["problems"]
    assert (shown["label"], shown["workaround"], shown["ticket_count"]) == (label, "Reconnect the client.", 2)
    # Linking twice changes nothing.
    client.post(url, json={"problem_id": made["id"]}, headers=as_user(agent))
    assert events(db, second) == [("problem_linked", label)]
    timeline = client.get(f"/api/problems/{made['id']}", headers=as_user(agent)).json()["updates"]
    assert [u["type"] for u in timeline].count("ticket_linked") == 2

    gone = client.delete(f"{url}/{made['id']}", headers=as_user(agent))
    assert gone.json()["problems"] == []
    assert events(db, second) == [("problem_linked", label), ("problem_unlinked", label)]


def test_hidden_problems_are_counted_not_shown(client, db, agent, admin, requester):
    desk, hr = Queue(name="Desk", is_default=True), Queue(name="HR", restricted=True)
    db.add_all([desk, hr])
    db.commit()
    mine, private = ticket_in(db, requester, desk, "Printer jam"), ticket_in(db, requester, hr, "HR printer jam")
    made = client.post("/api/problems", json={"title": "Printer jams", "ticket_ids": [mine.id, private.id]}, headers=as_user(admin)).json()
    # The agent can see the ticket but not the problem (it also links an HR ticket).
    assert client.get(f"/api/tickets/{mine.id}/problems", headers=as_user(agent)).json() == {"problems": [], "hidden": 1}
    assert client.post(f"/api/tickets/{mine.id}/problems", json={"problem_id": made["id"]}, headers=as_user(agent)).status_code == 404
    assert client.get(f"/api/tickets/{private.id}/problems", headers=as_user(agent)).status_code in (403, 404)
    # The ticket's history names the problem by number, never by its title.
    history = client.get(f"/api/tickets/{mine.id}/events", headers=as_user(agent)).text
    assert made["label"] in history and "Printer jams" not in history


def test_delete_needs_the_permission_and_keeps_the_tickets(client, db, agent, admin, requester):
    desk = Queue(name="Desk", is_default=True)
    db.add(desk)
    db.commit()
    t = ticket_in(db, requester, desk, "Duplicate report")
    made = client.post("/api/problems", json={"title": "Opened by mistake", "ticket_ids": [t.id]}, headers=as_user(agent)).json()
    assert client.delete(f"/api/problems/{made['id']}", headers=as_user(agent)).status_code == 403
    assert client.delete(f"/api/problems/{made['id']}", headers=as_user(admin)).status_code == 204
    db.expire_all()
    assert db.get(Problem, made["id"]) is None and db.get(Ticket, t.id) is not None
    last = db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == t.id).order_by(TicketEvent.id.desc())).first()
    assert last.type.value == "problem_unlinked" and last.data["deleted"] is True
