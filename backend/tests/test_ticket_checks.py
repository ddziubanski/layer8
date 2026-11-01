# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-checking tickets: watch a condition, record when it clears, Verify until confirmed; never close."""
from sqlalchemy import select

from app import ticket_checks
from app.models import Device, DevicePatch, Priority, Queue, Ticket, TicketCheck, TicketEvent, TicketSource, TicketStatus
from tests.conftest import as_user


def setup(db, requester):
    q = Queue(name="Desk", is_default=True)
    pc = Device(name="WS-0142")
    db.add_all([q, pc])
    db.flush()
    t = Ticket(subject="Updates stuck", source=TicketSource.agent, requester_id=requester.id, queue_id=q.id, status=TicketStatus.open, priority=Priority.medium, computer_id=pc.id)
    db.add(t)
    db.flush()
    patch = DevicePatch(action1_id="e-1", name="WS-0142", device_id=pc.id, status="Connected", reboot_required=True, missing_critical=2)
    db.add(patch)
    db.commit()
    return t, pc, patch


def events(db, t):
    return [e.type for e in db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == t.id).order_by(TicketEvent.id))]


def test_watch_clear_verify(client, db, agent, requester):
    t, pc, patch = setup(db, requester)
    url = f"/api/tickets/{t.id}/checks"
    got = client.get(url, headers=as_user(agent)).json()
    offered = {o["kind"]: o for o in got["available"]}
    # Only what isn't true yet is offered: the agent is connected, so not "agent back online".
    assert set(offered) == {"reboot_done", "updates_installed"}
    assert offered["reboot_done"]["label"] == "Restart pending clears on WS-0142"
    added = client.post(url, json={"kind": "reboot_done", "device_id": pc.id}, headers=as_user(agent))
    assert added.status_code == 201, added.text
    check = added.json()["checks"][0]
    assert check["state"] == "waiting" and check["detail"] == "Restart still pending"
    assert client.post(url, json={"kind": "reboot_done", "device_id": pc.id}, headers=as_user(agent)).status_code == 422
    assert client.get(f"/api/tickets/{t.id}", headers=as_user(agent)).json()["check_review"] is False

    patch.reboot_required = False
    db.commit()
    assert ticket_checks.evaluate(db) == 1
    db.commit()
    ticket = client.get(f"/api/tickets/{t.id}", headers=as_user(agent)).json()
    assert ticket["check_review"] is True and ticket["status"] == "open"  # never closes by itself
    confirmed = client.post(f"{url}/{check['id']}/confirm", headers=as_user(agent)).json()
    assert confirmed["checks"][0]["state"] == "confirmed"
    assert client.get(f"/api/tickets/{t.id}", headers=as_user(agent)).json()["check_review"] is False
    assert events(db, t)[-3:] == ["check_added", "check_cleared", "check_confirmed"]


def test_only_linked_devices_and_the_requester_and_staff_only(client, db, agent, requester):
    t, pc, _ = setup(db, requester)
    other = Device(name="OTHER-PC")
    db.add(other)
    db.commit()
    url = f"/api/tickets/{t.id}/checks"
    assert client.post(url, json={"kind": "reboot_done", "device_id": other.id}, headers=as_user(agent)).status_code == 422
    assert client.post(url, json={"kind": "account_unlocked", "user_id": agent.id}, headers=as_user(agent)).status_code == 422
    assert client.get(url, headers=as_user(requester)).status_code in (403, 404)
    # Stop watching.
    cid = client.post(url, json={"kind": "updates_installed", "device_id": pc.id}, headers=as_user(agent)).json()["checks"][0]["id"]
    assert client.delete(f"{url}/{cid}", headers=as_user(agent)).json()["checks"] == []
    assert db.get(TicketCheck, cid).state == "cancelled"


def test_closed_tickets_are_not_checked(db, requester):
    t, pc, patch = setup(db, requester)
    ticket_checks.add(db, t, None, "updates_installed", device_id=pc.id)
    t.status = TicketStatus.closed
    patch.missing_critical = 0
    db.commit()
    assert ticket_checks.evaluate(db) == 0
