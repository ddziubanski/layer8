# SPDX-License-Identifier: AGPL-3.0-or-later
"""Devices: AD computers from directory sync, devices added by hand, and linking them to tickets."""

from datetime import UTC, datetime

import pytest

from app.directory import sync
from app.directory.ldap import filetime
from app.models import AuthSource, Device, Queue, Ticket, TicketEvent, TicketSource, User
from tests.conftest import as_user
from tests.fake_directory import FakeDirectory


@pytest.fixture
def directory():
    return FakeDirectory()


def test_filetime():
    assert filetime("133000000000000000") == datetime(2022, 6, 18, 4, 26, 40, tzinfo=UTC)
    assert filetime("0") is None and filetime(None) is None and filetime(str(0x7FFFFFFFFFFFFFFF)) is None


def test_sync_computers_as_devices(db, directory):
    owner = directory.add_user("ral@corp.local", "R. Alvarez")
    pc = directory.add_computer("WS-0142", os="Windows 11 Enterprise", os_version="10.0 (22631)", last_logon_at=datetime(2026, 10, 1, tzinfo=UTC), managed_by=owner.dn.upper())
    old = directory.add_computer("WS-0007", enabled=False)
    run = sync.full_sync(db, directory)
    db.commit()
    assert run.error is None and run.devices_seen == 2
    device = db.query(Device).filter_by(name="WS-0142").one()
    assert device.dns_hostname == "ws-0142.corp.local" and device.os == "Windows 11 Enterprise" and device.source == "directory"
    assert device.assigned_user.email == "ral@corp.local"
    assert db.query(Device).filter_by(name="WS-0007").one().enabled is False

    # Renamed in AD: same device. Gone from AD: kept, but inactive.
    directory.computers_by_guid[pc.guid].name = "WS-0142-RENAMED"
    del directory.computers_by_guid[old.guid]
    sync.full_sync(db, directory)
    db.commit()
    assert db.query(Device).filter_by(directory_id=pc.guid).one().name == "WS-0142-RENAMED"
    assert db.query(Device).filter_by(directory_id=old.guid).one().is_active is False

    # A directory that suddenly returns no computers doesn't retire them all.
    directory.computers_by_guid.clear()
    run = sync.full_sync(db, directory)
    db.commit()
    assert db.query(Device).filter_by(directory_id=pc.guid).one().is_active
    assert any("no computers" in d["text"] for d in run.details)


def test_devices_api_and_ticket_links(client, db, admin, agent, requester):
    r = client.post("/api/devices", json={"name": " SW-CORE-1 ", "dns_hostname": "sw-core-1.corp.example", "os": "Cisco Meraki MS"}, headers=as_user(admin))
    assert r.status_code == 201, r.text
    sw = r.json()
    assert sw["name"] == "SW-CORE-1" and sw["source"] == "manual"
    assert client.post("/api/devices", json={"name": "x"}, headers=as_user(agent)).status_code == 403
    ad = Device(name="WS-0142", source="directory", directory_id="g-1", assigned_user_id=requester.id)
    db.add(ad)
    db.commit()

    page = client.get("/api/devices", params={"q": "meraki"}, headers=as_user(agent)).json()
    assert page["total"] == 1 and page["items"][0]["name"] == "SW-CORE-1"
    assert client.get("/api/devices", params={"q": requester.display_name.split()[0]}, headers=as_user(agent)).json()["items"][0]["name"] == "WS-0142"

    # AD owns most fields of AD computers; agents can only change notes.
    assert client.patch(f"/api/devices/{ad.id}", json={"os": "Linux"}, headers=as_user(admin)).status_code == 422
    assert client.patch(f"/api/devices/{ad.id}", json={"notes": "Docking station flaky"}, headers=as_user(agent)).json()["notes"] == "Docking station flaky"
    assert client.patch(f"/api/devices/{sw['id']}", json={"os": "MS 17.1"}, headers=as_user(agent)).status_code == 403
    assert client.patch(f"/api/devices/{sw['id']}", json={"os": "MS 17.1"}, headers=as_user(admin)).json()["os"] == "MS 17.1"
    assert client.delete(f"/api/devices/{ad.id}", headers=as_user(admin)).status_code == 422

    queue = Queue(name="Desk", is_default=True)
    db.add(queue)
    db.flush()
    ticket = Ticket(subject="Port flapping", source=TicketSource.portal, queue_id=queue.id, requester_id=requester.id)
    db.add(ticket)
    db.commit()
    r = client.post(f"/api/tickets/{ticket.id}/devices", json={"device_id": sw["id"]}, headers=as_user(agent))
    assert r.status_code == 201 and [d["device"]["name"] for d in r.json()] == ["SW-CORE-1"]
    # Linking twice is harmless.
    assert len(client.post(f"/api/tickets/{ticket.id}/devices", json={"device_id": sw["id"]}, headers=as_user(agent)).json()) == 1
    detail = client.get(f"/api/devices/{sw['id']}", headers=as_user(agent)).json()
    assert [t["id"] for t in detail["tickets"]] == [ticket.id]
    # Requesters can't see or change device links.
    assert client.get(f"/api/tickets/{ticket.id}/devices", headers=as_user(requester)).status_code == 403
    assert client.delete(f"/api/tickets/{ticket.id}/devices/{sw['id']}", headers=as_user(agent)).json() == []
    events = [e.type for e in db.query(TicketEvent).filter_by(ticket_id=ticket.id)]
    assert events.count("device_linked") == 1 and events.count("device_unlinked") == 1
