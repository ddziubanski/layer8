"""Device sweeps: stale devices and unknown ones on the network or in Action1."""

import ipaddress
from datetime import timedelta

from app import sla, sweeps
from app.config import settings
from app.models import Device, DevicePatch, FindingKind, IpAddress, IpFinding, IpKind, Queue, Subnet, Ticket
from tests.conftest import as_user


def test_stale_and_unknown(client, db, agent, monkeypatch):
    monkeypatch.setattr(settings, "action1_enabled", True)
    now = sla.utcnow()
    old, recent = now - timedelta(days=200), now - timedelta(days=2)
    quiet = Device(name="WS-OLD", last_logon_at=old)
    # AD says long ago, but Action1 saw it yesterday: not stale.
    a1_alive = Device(name="WS-A1", last_logon_at=old)
    # Only the network sees it.
    net_alive = Device(name="PRN-01", created_at=old)
    never = Device(name="NEVER-SEEN", created_at=old)
    brand_new = Device(name="NEW-PC")
    gone = Device(name="GONE", last_logon_at=old, is_active=False)
    db.add_all([quiet, a1_alive, net_alive, never, brand_new, gone])
    db.flush()
    db.add(DevicePatch(action1_id="a1", name="WS-A1", device_id=a1_alive.id, last_seen_at=recent))
    db.add(DevicePatch(action1_id="a2", name="KIOSK-9", last_seen_at=recent, os="Windows 10"))
    db.add(DevicePatch(action1_id="a3", name="LONG-GONE", last_seen_at=old))
    db.add(Subnet(network=ipaddress.ip_network("10.9.0.0/24")))
    db.flush()
    add = lambda a, **kw: db.add(IpAddress(address=ipaddress.ip_address(a), **kw))  # noqa: E731
    add("10.9.0.5", device_id=net_alive.id, last_seen_at=recent)
    add("10.9.0.6", hostname="mystery-cam", mac="aa:bb:cc:00:00:06", kind=IpKind.static, last_seen_at=recent)
    add("10.9.0.7", hostname="ws-old.corp.example", last_seen_at=recent)  # a device's name: known
    add("10.9.0.8", hostname="stale-printer", last_seen_at=old)  # not seen lately
    sub = db.query(Subnet).one()
    db.add(IpFinding(subnet_id=sub.id, address=ipaddress.ip_address("10.9.0.9"), kind=FindingKind.new_host, details={"hostname": "", "mac": "de:ad:be:ef:00:09"}, last_found_at=recent))
    db.commit()

    r = client.get("/api/devices/sweep", headers=as_user(agent)).json()
    assert r["stale_days"] == 90 and r["recent_days"] == 7
    # Never seen first, then the longest quiet.
    assert [s["device"]["name"] for s in r["stale"]] == ["NEVER-SEEN", "WS-OLD"]
    assert r["stale"][1]["ad_logon_at"] and r["stale"][1]["action1_at"] is None
    assert [(u["kind"], u["name"], u["address"]) for u in r["unknown"]] == [
        ("address", "mystery-cam", "10.9.0.6"), ("undocumented", "(no name)", "10.9.0.9"), ("action1", "KIOSK-9", ""),
    ]
    # Shorter windows from the query.
    assert len(client.get("/api/devices/sweep", params={"stale_days": 1}, headers=as_user(agent)).json()["stale"]) == 4


def test_weekly_ticket(db, monkeypatch):
    db.add(Queue(name="Desk", is_default=True))
    db.add(Device(name="WS-OLD", last_logon_at=sla.utcnow() - timedelta(days=200)))
    db.commit()
    t = sweeps.summary_ticket(db)
    db.commit()
    assert t.subject == "Device sweep: 1 stale, 0 unknown" and "WS-OLD: last seen" in t.description and t.priority == "low"
    db.query(Device).delete()
    db.commit()
    assert sweeps.summary_ticket(db) is None and db.query(Ticket).count() == 1


def test_weekly_ticket_says_where_unknowns_were_seen(db, monkeypatch):
    monkeypatch.setattr(settings, "action1_enabled", True)
    import ipaddress as ip

    db.add(Queue(name="Desk", is_default=True))
    db.add(DevicePatch(action1_id="k", name="KIOSK-9", last_seen_at=sla.utcnow(), os="Windows 10"))
    db.add(IpAddress(address=ip.ip_address("10.9.0.6"), hostname="mystery-cam", last_seen_at=sla.utcnow()))
    db.commit()
    t = sweeps.summary_ticket(db)
    assert "- mystery-cam 10.9.0.6 (seen by scans; documented as static)" in t.description
    assert "- KIOSK-9 (in Action1, no device here; Windows 10)" in t.description
