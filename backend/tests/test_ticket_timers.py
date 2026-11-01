# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket timers: start and stop log time; the console lists mine; closing a ticket stops them all."""
from datetime import timedelta

from sqlalchemy import select

from app import sla
from app.models import Queue, Ticket, TicketSource, TicketStatus, TicketTimer, TimeEntry
from tests.conftest import as_user, make_user
from app.models import UserRole


def setup(db, requester):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    t = Ticket(subject="Printer offline", source=TicketSource.portal, queue_id=q.id, requester_id=requester.id, status=TicketStatus.open)
    db.add(t)
    db.commit()
    return t


def backdate(db, ticket, user, minutes):
    timer = db.scalar(select(TicketTimer).where(TicketTimer.ticket_id == ticket.id, TicketTimer.user_id == user.id))
    timer.started_at = sla.utcnow() - timedelta(minutes=minutes, seconds=10)
    db.commit()


def test_start_stop_logs_rounded_minutes_and_lists_my_timers(client, db, agent, requester):
    t = setup(db, requester)
    started = client.post(f"/api/tickets/{t.id}/timer", headers=as_user(agent))
    assert started.status_code == 201, started.text
    # Starting again changes nothing.
    assert client.post(f"/api/tickets/{t.id}/timer", headers=as_user(agent)).json()["started_at"] == started.json()["started_at"]
    mine = client.get("/api/me/timers", headers=as_user(agent)).json()
    assert [(m["ticket_id"], m["subject"]) for m in mine] == [(t.id, "Printer offline")]
    backdate(db, t, agent, 12)
    stopped = client.post(f"/api/tickets/{t.id}/timer/stop", json={"note": "Reinstalled driver"}, headers=as_user(agent))
    assert stopped.status_code == 200, stopped.text
    assert (stopped.json()["minutes"], stopped.json()["note"]) == (13, "Reinstalled driver")
    assert client.get("/api/me/timers", headers=as_user(agent)).json() == []
    assert client.get(f"/api/tickets/{t.id}/time", headers=as_user(agent)).json()["total_minutes"] == 13
    assert client.post(f"/api/tickets/{t.id}/timer/stop", json={}, headers=as_user(agent)).status_code == 404


def test_resolving_stops_everyones_timers_and_logs_them(client, db, agent, requester):
    t = setup(db, requester)
    other = make_user(db, "dcole@corp.local", UserRole.agent, "D. Cole")
    client.post(f"/api/tickets/{t.id}/timer", headers=as_user(agent))
    client.post(f"/api/tickets/{t.id}/timer", headers=as_user(other))
    assert [x["user_name"] for x in client.get(f"/api/tickets/{t.id}/time", headers=as_user(agent)).json()["timers"]] == ["J. Park", "D. Cole"]
    backdate(db, t, other, 5)
    r = client.patch(f"/api/tickets/{t.id}", json={"status": "resolved"}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalars(select(TicketTimer)).all() == []
    entries = {e.user_id: e for e in db.scalars(select(TimeEntry).where(TimeEntry.ticket_id == t.id))}
    assert entries[other.id].minutes == 6 and "stopped when the ticket was resolved" in entries[other.id].note
    assert entries[agent.id].minutes == 1
    # A closed ticket can't be timed.
    assert client.post(f"/api/tickets/{t.id}/timer", headers=as_user(agent)).status_code == 409


def test_a_forgotten_timer_is_capped_at_a_day(client, db, agent, requester):
    t = setup(db, requester)
    client.post(f"/api/tickets/{t.id}/timer", headers=as_user(agent))
    backdate(db, t, agent, 30 * 60)
    entry = client.post(f"/api/tickets/{t.id}/timer/stop", json={}, headers=as_user(agent)).json()
    assert entry["minutes"] == 24 * 60 and "capped at 24 h" in entry["note"]


def test_requesters_cannot_time(client, db, requester):
    t = setup(db, requester)
    assert client.post(f"/api/tickets/{t.id}/timer", headers=as_user(requester)).status_code == 403
    assert client.get("/api/me/timers", headers=as_user(requester)).status_code == 403
