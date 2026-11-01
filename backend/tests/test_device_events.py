# SPDX-License-Identifier: AGPL-3.0-or-later
"""Device timeline part 2: the syncs record what changed (never a first sighting), and it shows up."""
from datetime import timedelta

from sqlalchemy import select

from app import action1, network, sla
from app.directory import sync as dsync
from app.directory.types import DirectoryComputer
from app.models import Device, DeviceEvent, DevicePatch, IpAddress, NetworkInventory, NetworkObservation
from tests.conftest import as_user

MAC = "aa:bb:cc:00:11:22"


def events(db, device):
    return [(e.kind, e.title, e.tone) for e in db.scalars(select(DeviceEvent).where(DeviceEvent.device_id == device.id).order_by(DeviceEvent.id))]


def test_directory_changes_are_recorded_but_not_the_first_sighting(db):
    pc = DirectoryComputer(guid="g-1", dn="CN=WS-0142,OU=Laptops,OU=Staff,DC=corp,DC=local", name="WS-0142", os="Windows 11 Pro", os_version="10.0 (22631)")
    dsync.upsert_device(db, pc, {})
    db.flush()
    device = db.scalar(select(Device).where(Device.directory_id == "g-1"))
    assert events(db, device) == []
    pc.os_version, pc.enabled, pc.dn = "10.0 (26100)", False, "CN=WS-0142,OU=Retired,DC=corp,DC=local"
    dsync.upsert_device(db, pc, {})
    db.flush()
    got = events(db, device)
    assert ("directory", "Operating system now Windows 11 Pro 10.0 (26100)", "info") in got
    assert ("directory", "Disabled in Active Directory", "warn") in got
    assert ("directory", "Moved to another OU", "info") in got
    # Last sign-in alone is noise, not an event.
    pc.last_logon_at = sla.utcnow()
    before = len(events(db, device))
    dsync.upsert_device(db, pc, {})
    db.flush()
    assert len(events(db, device)) == before


def test_action1_restarts_updates_and_agent_state(db):
    device = Device(name="WS-0142")
    db.add(device)
    db.flush()
    booted = sla.utcnow() - timedelta(days=3)
    row = DevicePatch(action1_id="e-1", organization_id="o", name="WS-0142", device_id=device.id, status="Connected",
                      last_boot_at=booted, reboot_required=False, missing_critical=0)
    db.add(row)
    db.flush()
    before = (row.device_id, row.status, row.last_boot_at, row.reboot_required, row.missing_critical)
    row.status, row.last_boot_at, row.reboot_required, row.missing_critical = "Disconnected", booted + timedelta(days=2), True, 3
    action1._note_patch_changes(db, row, before)
    db.flush()
    assert [t for _, t, _ in events(db, device)] == ["Action1 agent disconnected", "Restarted", "Restart needed to finish updates", "3 critical updates missing"]


def test_switch_port_moves_and_outages_of_known_devices(db):
    device = Device(name="AP-LOBBY")
    db.add(device)
    db.flush()
    db.add(IpAddress(address="10.20.0.50", mac=MAC, device_id=device.id))
    db.flush()
    now = sla.utcnow()

    def obs(port):
        return NetworkObservation(provider="unifi", source_id="site", mac=MAC, uplink_name="SW-CORE", port=port, network="Staff", observed_at=now)

    network.replace_snapshot(db, "unifi", [obs("4")], [])
    assert events(db, device) == []
    network.replace_snapshot(db, "unifi", [obs("7")], [])
    assert events(db, device) == [("network", "Moved to SW-CORE port 7 · Staff", "info")]

    def gear(state):
        return NetworkInventory(provider="unifi", source_id="site", device_key="ap-1", name="AP-LOBBY", mac=MAC, state=state, observed_at=now)

    device_id = device.id
    for state in ("offline", "unknown", "online"):
        # Each sync runs in its own session.
        db.commit()
        db.expunge_all()
        network.replace_snapshot(db, "unifi", [obs("7")], [gear(state)])
        network.track_outages(db, "unifi", enabled=False, after_minutes=10, queue_id=0)
    titles = [e.title for e in db.scalars(select(DeviceEvent).where(DeviceEvent.device_id == device_id).order_by(DeviceEvent.id))]
    # Same port every time: no more moves. "unknown" doesn't end the outage; only "online" does.
    assert titles == ["Moved to SW-CORE port 7 · Staff", "Went offline (UniFi)", "Back online (UniFi)"]


def test_the_timeline_shows_recorded_events(client, db, agent):
    device = Device(name="WS-0142")
    db.add(device)
    db.flush()
    db.add(DeviceEvent(device_id=device.id, at=sla.utcnow() - timedelta(hours=1), kind="patch", title="Restarted", tone="info", source="action1"))
    db.add(DeviceEvent(device_id=device.id, at=sla.utcnow() - timedelta(hours=2), kind="network", title="Went offline (UniFi)", tone="bad", source="unifi"))
    db.commit()
    got = client.get(f"/api/devices/{device.id}/timeline", headers=as_user(agent)).json()
    assert [(e["kind"], e["title"]) for e in got["entries"] if e["kind"] != "lifecycle"] == [("patch", "Restarted"), ("network", "Went offline (UniFi)")]
    assert next(e for e in got["entries"] if e["kind"] == "network")["tone"] == "bad"
    only = client.get(f"/api/devices/{device.id}/timeline?kinds=patch", headers=as_user(agent)).json()
    assert {e["kind"] for e in only["entries"]} == {"patch"}
