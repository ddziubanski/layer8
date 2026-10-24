# SPDX-License-Identifier: AGPL-3.0-or-later
"""The requester's computer on a ticket, and remote sessions to it."""

from urllib.parse import parse_qs, urlparse

import pytest

from app.models import Comment, Device, Queue, RemoteSession, Ticket, TicketEvent
from tests.conftest import as_user, make_user


@pytest.fixture
def desk(db):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.commit()
    return q


def new_ticket(client, who, **body):
    r = client.post("/api/tickets", json={"subject": "Slow PC", **body}, headers=as_user(who))
    assert r.status_code == 201, r.text
    return r.json()


def test_filled_in_when_certain_and_editable(client, db, agent, requester, desk):
    pc = Device(name="WS-0142", dns_hostname="ws-0142.corp.local", assigned_user_id=requester.id)
    db.add(pc)
    db.commit()
    t = new_ticket(client, agent, requester_id=requester.id)
    assert t["computer"]["name"] == "WS-0142"
    # Two computers: no guessing.
    db.add(Device(name="LT-0099", assigned_user_id=requester.id))
    db.commit()
    assert new_ticket(client, agent, requester_id=requester.id)["computer"] is None

    other = Device(name="WS-0200")
    db.add(other)
    db.commit()
    r = client.patch(f"/api/tickets/{t['id']}", json={"computer_id": other.id}, headers=as_user(agent))
    assert r.json()["computer"]["name"] == "WS-0200"
    ev = db.query(TicketEvent).filter_by(ticket_id=t["id"], type="updated").all()[-1]
    assert ev.data["fields"] == {"computer": {"before": "WS-0142", "after": "WS-0200"}}
    assert client.patch(f"/api/tickets/{t['id']}", json={"computer_id": 99999}, headers=as_user(agent)).status_code in (409, 422)
    assert client.patch(f"/api/tickets/{t['id']}", json={"computer_id": None}, headers=as_user(agent)).json()["computer"] is None


def test_follows_a_requester_change(client, db, agent, requester, desk):
    other = make_user(db, "mosei@corp.local", name="M. Osei")
    db.add_all([Device(name="WS-OLD", assigned_user_id=requester.id), Device(name="WS-NEW", assigned_user_id=other.id)])
    db.commit()
    t = new_ticket(client, agent, requester_id=requester.id)
    assert t["computer"]["name"] == "WS-OLD"
    r = client.post(f"/api/tickets/{t['id']}/requester", json={"user_id": other.id}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert r.json()["computer"]["name"] == "WS-NEW"


def test_portal_which_computer(client, db, requester, desk):
    mine = Device(name="WS-0142", assigned_user_id=requester.id)
    laptop = Device(name="LT-0099", assigned_user_id=requester.id)
    theirs = Device(name="WS-9999")
    db.add_all([mine, laptop, theirs])
    db.commit()
    assert [d["name"] for d in client.get("/api/portal/devices", headers=as_user(requester)).json()] == ["LT-0099", "WS-0142"]
    body = {"subject": "Printer", "urgency": "medium", "impact": "single"}
    assert client.post("/api/portal/tickets", json={**body, "computer_id": theirs.id}, headers=as_user(requester)).status_code == 422
    r = client.post("/api/portal/tickets", json={**body, "computer_id": laptop.id}, headers=as_user(requester))
    assert r.status_code == 201, r.text
    assert db.get(Ticket, r.json()["id"]).computer.name == "LT-0099"


def test_remote_assistance_session_is_logged_with_its_length(client, db, agent, requester, desk):
    pc = Device(name="WS-0142", dns_hostname="ws-0142.corp.local", assigned_user_id=requester.id)
    db.add(pc)
    db.commit()
    t = new_ticket(client, agent, requester_id=requester.id)
    r = client.post(f"/api/tickets/{t['id']}/remote-sessions", json={}, headers=as_user(agent))
    assert r.status_code == 201, r.text
    uri = urlparse(r.json()["launch_uri"])
    q = {k: v[0] for k, v in parse_qs(uri.query).items()}
    assert uri.scheme == "helpdesk-remote" and uri.path == "offerra" and q["host"] == "ws-0142.corp.local"
    sid = r.json()["session"]["id"]
    assert q["end"].endswith(f"/api/remote-sessions/{sid}/end")
    assert client.post(f"/api/remote-sessions/{sid}/end", params={"token": "nope"}).status_code == 404
    assert client.post(f"/api/remote-sessions/{sid}/end", params={"token": q["token"]}).status_code == 204
    # One time only.
    assert client.post(f"/api/remote-sessions/{sid}/end", params={"token": q["token"]}).status_code == 404
    assert db.get(RemoteSession, sid).ended_at is not None
    notes = [c.body for c in db.query(Comment).filter_by(ticket_id=t["id"]).order_by(Comment.id)]
    assert notes[0].startswith("Offered Remote Assistance to ws-0142.corp.local") and "ended after" in notes[1]
    assert [e.data["action"] for e in db.query(TicketEvent).filter_by(ticket_id=t["id"], type="remote_session").order_by(TicketEvent.id)] == ["started", "ended"]
    # Ended by hand when the launcher can't report.
    sid2 = client.post(f"/api/tickets/{t['id']}/remote-sessions", json={}, headers=as_user(agent)).json()["session"]["id"]
    assert client.post(f"/api/remote-sessions/{sid2}/close", headers=as_user(agent)).json()["ended_at"]
    assert len(client.get(f"/api/tickets/{t['id']}/remote-sessions", headers=as_user(agent)).json()) == 2


def test_remote_session_needs_a_computer_with_a_sane_name(client, db, agent, requester, desk):
    t = new_ticket(client, agent, requester_id=requester.id)
    assert "computer first" in client.post(f"/api/tickets/{t['id']}/remote-sessions", json={}, headers=as_user(agent)).json()["detail"]
    bad = Device(name='ws"; calc')
    db.add(bad)
    db.commit()
    assert client.post(f"/api/tickets/{t['id']}/remote-sessions", json={"device_id": bad.id}, headers=as_user(agent)).status_code == 422
    assert client.post(f"/api/tickets/{t['id']}/remote-sessions", json={}, headers=as_user(requester)).status_code == 403
