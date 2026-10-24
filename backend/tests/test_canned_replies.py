# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canned replies: shared and personal, per queue, with the ticket's details filled in."""
from app import numbering, services
from app.models import Queue, TicketSource
from tests.conftest import as_user


def _ticket(db, requester, queue=None):
    t = services.create_ticket(db, subject="Outlook password prompt", description="", category=None, requester=requester,
                               source=TicketSource.portal, actor=requester, preferred_queue=queue)
    db.commit()
    return t


def test_shared_needs_the_permission_personal_does_not(client, db, admin, agent):
    body = {"title": "Restart", "body": "Hi {first_name}, please restart.", "personal": False}
    assert client.post("/api/canned-replies", json=body, headers=as_user(agent)).status_code == 403
    mine = client.post("/api/canned-replies", json={**body, "personal": True}, headers=as_user(agent))
    assert mine.status_code == 201 and mine.json()["personal"] and mine.json()["may_edit"]
    shared = client.post("/api/canned-replies", json={**body, "title": "Shared restart"}, headers=as_user(admin)).json()
    listed = client.get("/api/canned-replies", headers=as_user(agent)).json()
    assert [r["title"] for r in listed] == ["Restart", "Shared restart"]  # own first, then shared
    assert next(r for r in listed if r["id"] == shared["id"])["may_edit"] is False
    assert client.delete(f"/api/canned-replies/{shared['id']}", headers=as_user(agent)).status_code == 403
    # Someone else's personal reply doesn't exist for you.
    assert client.delete(f"/api/canned-replies/{mine.json()['id']}", headers=as_user(admin)).status_code == 404
    assert client.delete(f"/api/canned-replies/{mine.json()['id']}", headers=as_user(agent)).status_code == 204


def test_render_fills_in_the_ticket_and_queue_limits_apply(client, db, admin, agent, requester, service_desk):
    other = Queue(name="Network")
    db.add(other)
    db.commit()
    ticket = _ticket(db, requester)
    for_desk = client.post("/api/canned-replies", json={"title": "Done", "body": "Hi {first_name}, {ticket} ({subject}) is fixed. {technician}, {queue}. {unknown}", "personal": False, "queue_id": service_desk.id}, headers=as_user(admin)).json()
    for_network = client.post("/api/canned-replies", json={"title": "Port", "body": "x", "personal": False, "queue_id": other.id}, headers=as_user(admin)).json()
    titles = [r["title"] for r in client.get(f"/api/canned-replies?ticket_id={ticket.id}", headers=as_user(agent)).json()]
    assert titles == ["Done"]
    out = client.post(f"/api/canned-replies/{for_desk['id']}/render?ticket_id={ticket.id}", headers=as_user(agent)).json()
    first = requester.display_name.split()[0]
    label = numbering.label(ticket.id, ticket.created_at)
    assert out == {"body": f"Hi {first}, {label} (Outlook password prompt) is fixed. {agent.display_name}, {service_desk.name}. {{unknown}}", "to_requester": True}
    assert client.post(f"/api/canned-replies/{for_network['id']}/render?ticket_id={ticket.id}", headers=as_user(agent)).status_code == 422
