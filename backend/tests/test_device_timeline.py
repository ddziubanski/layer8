# SPDX-License-Identifier: AGPL-3.0-or-later
"""A device's history, merged from every source, with restricted tickets counted and never named."""
from datetime import date, datetime, timedelta, timezone

from app.models import (
    Change, ChangeStatus, Device, DevicePatch, IpAddress, Queue, Ticket, TicketDevice, TicketSource, TicketStatus, change_devices,
)
from tests.conftest import as_user

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def setup(db, requester):
    desk, hr = Queue(name="Desk", is_default=True), Queue(name="HR", restricted=True)
    pc = Device(name="WS-0142", os="Windows 11", warranty_ends=date.today() + timedelta(days=90))
    db.add_all([desk, hr, pc])
    db.flush()
    seen = Ticket(subject="Laptop won't wake", source=TicketSource.portal, queue_id=desk.id, requester_id=requester.id,
                  status=TicketStatus.resolved, created_at=T0, resolved_at=T0 + timedelta(hours=3), computer_id=pc.id)
    secret = Ticket(subject="Confidential HR laptop matter", source=TicketSource.portal, queue_id=hr.id, requester_id=requester.id, created_at=T0)
    change = Change(title="Replace dock firmware", status=ChangeStatus.completed, finished_at=T0 + timedelta(days=2))
    db.add_all([seen, secret, change])
    db.flush()
    db.add(TicketDevice(ticket_id=secret.id, device_id=pc.id))
    db.execute(change_devices.insert().values(change_id=change.id, device_id=pc.id))
    db.add(IpAddress(address="10.20.0.42", device_id=pc.id, first_seen_at=T0 - timedelta(days=30), last_seen_at=T0 + timedelta(days=5)))
    db.add(DevicePatch(device_id=pc.id, action1_id="e-1", organization_id="o-1", name=pc.name, status="Connected",
                       reboot_required=True, missing_critical=2, last_seen_at=T0 + timedelta(days=5)))
    db.commit()
    return pc, seen, secret, change


def test_timeline_merges_sources_and_hides_what_the_viewer_cant_see(client, db, agent, requester):
    pc, seen, secret, change = setup(db, requester)
    r = client.get(f"/api/devices/{pc.id}/timeline", headers=as_user(agent))
    assert r.status_code == 200, r.text
    got = r.json()
    titles = [e["title"] for e in got["entries"]]
    # Newest first: the change, the ticket resolved, opened, then the IP first seen.
    assert titles.index("Change CHG-%05d completed" % change.id) < titles.index(next(t for t in titles if t.endswith("resolved")))
    assert any(t.startswith("First seen at 10.20.0.42") for t in titles)
    assert next(e for e in got["entries"] if e["kind"] == "ticket")["link"] == f"/tickets/{seen.id}"
    # The HR ticket is counted, never named.
    assert got["hidden"] == 1 and "Confidential" not in r.text
    now = {n["label"]: n for n in got["now"]}
    assert now["Reboot"]["tone"] == "warn" and now["Critical updates missing"]["value"] == "2" and now["IP address"]["value"] == "10.20.0.42"
    assert [u["title"] for u in got["upcoming"]] == ["Warranty ends"] and got["upcoming"][0]["tone"] == "warn"


def test_kinds_filter_and_admin_sees_everything(client, db, agent, admin, requester):
    pc, *_ = setup(db, requester)
    only = client.get(f"/api/devices/{pc.id}/timeline?kinds=change", headers=as_user(agent)).json()
    assert {e["kind"] for e in only["entries"]} == {"change"}
    everything = client.get(f"/api/devices/{pc.id}/timeline?kinds=ticket", headers=as_user(admin)).json()
    assert everything["hidden"] == 0 and sum(e["title"].endswith("opened") for e in everything["entries"]) == 2
    assert client.get("/api/devices/999999/timeline", headers=as_user(agent)).status_code == 404
    assert client.get(f"/api/devices/{pc.id}/timeline", headers=as_user(requester)).status_code == 403
