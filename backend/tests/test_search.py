# SPDX-License-Identifier: AGPL-3.0-or-later
"""One search for everything: grouped by type, identifiers by prefix, visibility per type."""
from app.config import settings
from app.models import Change, Device, IpAddress, KbArticle, Problem, PurchaseOrder, Queue, System, Ticket, TicketSource, Vendor
from tests.conftest import as_user


def groups(client, user, q):
    res = client.get("/api/search", params={"q": q}, headers=as_user(user))
    assert res.status_code == 200, res.text
    return {g["kind"]: [(h["label"], h["to"]) for h in g["hits"]] for g in res.json()["groups"]}


def test_devices_by_name_serial_ip_and_mac(client, db, agent):
    pc = Device(name="WS-0142", serial="5CG1234XYZ", os="Windows 11 Pro")
    db.add(pc)
    db.flush()
    db.add_all([IpAddress(address="10.20.0.55", mac="aa:bb:cc:00:09:55", device_id=pc.id), IpAddress(address="10.20.0.99", hostname="printer-lobby")])
    db.commit()
    assert groups(client, agent, "ws-01")["device"] == [("WS-0142", f"/devices/{pc.id}")]
    assert groups(client, agent, "5cg12")["device"][0][0] == "WS-0142"
    assert groups(client, agent, "AA-BB-CC-00-09-55")["device"] == [("WS-0142", f"/devices/{pc.id}")]
    by_ip = groups(client, agent, "10.20.0.")["device"]
    # An address without a device opens in IPAM.
    assert ("WS-0142", f"/devices/{pc.id}") in by_ip and ("printer-lobby", "/ipam/addresses?q=10.20.0.99") in by_ip


def test_people_problems_changes_articles_pos_vendors_systems(client, db, agent, requester):
    vendor = Vendor(name="Contoso Networks")
    db.add(vendor)
    db.flush()
    db.add_all([
        Problem(title="VPN drops after sleep"),
        Change(title="Replace VPN concentrator"),
        KbArticle(title="Reconnect the VPN client", body="Open the client and press Connect."),
        PurchaseOrder(title="VPN licences", vendor_id=vendor.id),
        System(name="VPN gateway", vendor_id=vendor.id),
    ])
    db.commit()
    got = groups(client, agent, "vpn")
    assert got["problem"][0][0].endswith("VPN drops after sleep")
    assert got["change"][0][0].endswith("Replace VPN concentrator")
    assert got["article"][0][0] == "Reconnect the VPN client"
    assert got["purchase_order"][0][0].endswith("VPN licences")
    assert got["system"] == [("VPN gateway", "/vendors/systems?q=VPN%20gateway")]
    assert groups(client, agent, "contoso")["vendor"] == [("Contoso Networks", "/vendors?q=Contoso%20Networks")]
    assert groups(client, agent, requester.display_name[:5])["person"][0][1] == f"/people/{requester.id}"
    # A problem number finds that problem.
    p = db.query(Problem).one()
    assert groups(client, agent, f"PRB-{p.id:05d}")["problem"][0][1] == f"/problems/{p.id}"


def test_staff_only_hidden_problems_and_switched_off_modules(client, db, agent, requester, monkeypatch):
    assert client.get("/api/search", params={"q": "vpn"}, headers=as_user(requester)).status_code == 403
    private = Queue(name="HR", restricted=True)
    db.add(private)
    db.flush()
    t = Ticket(subject="Payroll VPN", source=TicketSource.agent, requester_id=requester.id, queue_id=private.id)
    db.add(t)
    db.flush()
    db.add(Problem(title="VPN payroll export", tickets=[t]))
    db.add(Device(name="VPN-TEST-01"))
    db.commit()
    got = groups(client, agent, "vpn")
    assert "problem" not in got  # it links a ticket this agent can't see
    monkeypatch.setattr(settings, "workspace_devices_enabled", False)
    assert "device" not in groups(client, agent, "vpn")
