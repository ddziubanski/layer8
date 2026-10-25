# SPDX-License-Identifier: AGPL-3.0-or-later
from app.models import Device, DeviceSource, Queue, Ticket, TicketSource, TicketStatus
from tests.conftest import as_user


def _tickets(db, requester, desk, hr):
    for queue, status in ((desk, TicketStatus.open), (desk, TicketStatus.closed), (desk, TicketStatus.resolved), (hr, TicketStatus.open)):
        db.add(Ticket(subject="x", source=TicketSource.portal, queue_id=queue.id, requester_id=requester.id, status=status))


def test_person_card_counts_only_visible_tickets(client, db, admin, agent, requester):
    desk, hr = Queue(name="Service desk", is_default=True), Queue(name="HR", restricted=True)
    db.add_all([desk, hr])
    db.flush()
    _tickets(db, requester, desk, hr)
    requester.title, requester.department, requester.phone = "Accountant", "Finance", "+1 555 0100"
    requester.directory_dn = "CN=R. Alvarez,OU=Finance,OU=Staff,DC=corp,DC=local"
    db.add(Device(name="WS-0142", source=DeviceSource.manual, assigned_user_id=requester.id))
    db.commit()
    seen = client.get(f"/api/people/{requester.id}", headers=as_user(agent)).json()
    assert (seen["open_tickets"], seen["all_tickets"]) == (1, 3)  # HR is restricted
    assert (seen["title"], seen["department"], seen["phone"]) == ("Accountant", "Finance", "+1 555 0100")
    assert seen["in_directory"] and seen["ou"] == "OU=Finance,OU=Staff,DC=corp,DC=local"
    assert [d["name"] for d in seen["devices"]] == ["WS-0142"]
    everything = client.get(f"/api/people/{requester.id}", headers=as_user(admin)).json()
    assert (everything["open_tickets"], everything["all_tickets"]) == (2, 4)


def test_ticket_list_by_requester_respects_queues(client, db, agent, requester):
    desk, hr = Queue(name="Service desk", is_default=True), Queue(name="HR", restricted=True)
    db.add_all([desk, hr])
    db.flush()
    _tickets(db, requester, desk, hr)
    db.commit()
    page = client.get(f"/api/tickets?requester_id={requester.id}&sort=id", headers=as_user(agent)).json()
    assert page["total"] == 3 and all(t["requester"]["id"] == requester.id for t in page["items"])


def test_people_is_staff_only(client, requester, agent):
    assert client.get(f"/api/people/{agent.id}", headers=as_user(requester)).status_code == 403
    assert client.get("/api/people/999999", headers=as_user(agent)).status_code == 404
