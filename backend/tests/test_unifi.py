# SPDX-License-Identifier: AGPL-3.0-or-later
"""UniFi against a mocked API (no live console): paging, snapshots, outages, vouchers, settings."""
import json
from datetime import timedelta

import httpx
import pytest

from app import configstore, network, sla, unifi
from app.config import settings
from app.models import Comment, IpAddress, NetworkInventory, NetworkObservation, NetworkOutage, Subnet, Ticket, TicketStatus
from tests.conftest import as_user

BASE = "/v1/connector/consoles/console-1/network/integration/v1"


def page(items, offset, limit, total=None):
    total = len(items) if total is None else total
    return {"offset": offset, "limit": limit, "count": len(items[offset:offset + limit]), "totalCount": total, "data": items[offset:offset + limit]}


class FakeUnifi:
    def __init__(self):
        self.devices = [
            {"id": "dev-gw", "name": "Gateway", "model": "UDM-Pro", "macAddress": "AA-AA-AA-00-00-01", "ipAddress": "10.0.0.1", "state": "ONLINE", "features": ["gateway"]},
            {"id": "dev-ap", "name": "Lobby AP", "model": "U6-Pro", "macAddress": "aa:aa:aa:00:00:02", "ipAddress": "10.0.0.2", "state": "ONLINE", "features": ["accessPoint"]},
        ]
        self.clients = [
            {"type": "WIRELESS", "id": "c1", "name": "reception-laptop", "macAddress": "BB:BB:BB:00:00:01", "ipAddress": "10.0.1.20", "uplinkDeviceId": "dev-ap", "wirelessNetworkName": "Staff"},
            {"type": "VPN", "id": "c2", "name": "remote", "ipAddress": "10.8.0.2"},  # no MAC: skipped
        ] + [{"type": "WIRED", "id": f"w{i}", "name": f"pc{i}", "macAddress": f"cc:cc:cc:00:01:{i:02x}", "uplinkDeviceId": "dev-gw"} for i in range(250)]
        self.fail = False
        self.calls = []

    def __call__(self, request: httpx.Request):
        self.calls.append((request.method, request.url.path))
        assert request.headers["X-API-KEY"] == "test-key"
        if self.fail:
            return httpx.Response(503)
        path, params = request.url.path, request.url.params
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 25))
        if path == f"{BASE}/sites":
            return httpx.Response(200, json=page([{"id": "site-1", "internalReference": "default", "name": "Head office"}], offset, limit))
        if path == f"{BASE}/sites/site-1/devices":
            return httpx.Response(200, json=page(self.devices, offset, limit))
        if path == f"{BASE}/sites/site-1/clients":
            return httpx.Response(200, json=page(self.clients, offset, limit))
        if path == f"{BASE}/sites/site-1/hotspot/vouchers" and request.method == "POST":
            body = json.loads(request.content)
            assert body["count"] == 1 and body["authorizedGuestLimit"] == 2
            return httpx.Response(201, json={"vouchers": [{"id": "v1", "code": "12345-67890", "timeLimitMinutes": body["timeLimitMinutes"]}]})
        if path == "/v1/hosts":
            return httpx.Response(200, json={"data": [{"id": "console-1", "reportedState": {"name": "HQ console"}}], "httpStatusCode": 200})
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    f = FakeUnifi()
    monkeypatch.setattr(settings, "unifi_enabled", True)
    monkeypatch.setattr(settings, "unifi_mode", "cloud")
    monkeypatch.setattr(settings, "unifi_api_key", "test-key")
    monkeypatch.setattr(settings, "unifi_console_ids", ["console-1"])
    monkeypatch.setattr(settings, "unifi_site_ids", [])
    monkeypatch.setattr(unifi, "from_settings", lambda: unifi.Client("test-key", http=httpx.Client(transport=httpx.MockTransport(f)), sleep=lambda _: None))
    return f


def test_sync_reads_every_page_and_stores_one_snapshot(db, fake):
    summary = unifi.sync(db, unifi.from_settings())
    db.commit()
    assert summary.startswith("251 clients, 2 UniFi devices (0 offline)")
    obs = db.get(NetworkObservation, ("unifi", "console-1:site-1", "bb:bb:bb:00:00:01"))
    assert (obs.hostname, obs.connection, obs.ssid, obs.uplink_name, obs.address, obs.source_name) == ("reception-laptop", "wireless", "Staff", "Lobby AP", "10.0.1.20", "Head office")
    gw = db.get(NetworkInventory, ("unifi", "console-1:site-1", "dev-gw"))
    assert (gw.kind, gw.mac, gw.state) == ("gateway", "aa:aa:aa:00:00:01", "online")
    assert sum(1 for c in fake.calls if c[1].endswith("/clients")) == 2  # 252 clients in pages of 200


def test_failed_read_keeps_the_last_complete_snapshot(db, fake):
    unifi.sync(db, unifi.from_settings())
    db.commit()
    fake.fail = True
    with pytest.raises(unifi.UnifiError, match="last complete read was kept"):
        unifi.sync(db, unifi.from_settings())
    db.rollback()
    assert db.query(NetworkObservation).filter_by(provider="unifi").count() == 251


def test_offline_device_opens_one_ticket_after_the_grace_period_and_notes_recovery(db, fake, monkeypatch, service_desk):
    monkeypatch.setattr(settings, "unifi_outage_tickets", True)
    monkeypatch.setattr(settings, "unifi_outage_minutes", 10)
    fake.devices[1]["state"] = "OFFLINE"
    unifi.sync(db, unifi.from_settings())
    db.commit()
    assert db.query(Ticket).count() == 0  # within the grace period
    outage = db.get(NetworkOutage, ("unifi", "console-1:site-1", "dev-ap"))
    outage.down_since = sla.utcnow() - timedelta(minutes=11)
    db.commit()
    unifi.sync(db, unifi.from_settings())
    unifi.sync(db, unifi.from_settings())
    db.commit()
    tickets = db.query(Ticket).all()
    assert len(tickets) == 1 and tickets[0].subject == "Offline: UniFi ap Lobby AP"
    fake.devices[1]["state"] = "ONLINE"
    unifi.sync(db, unifi.from_settings())
    db.commit()
    ticket = db.get(Ticket, tickets[0].id)
    assert ticket.status != TicketStatus.resolved
    notes = db.query(Comment).filter_by(ticket_id=ticket.id).all()
    assert any("back online" in c.body and c.is_internal for c in notes)


def test_observations_show_on_documented_ip_records(client, db, agent, fake):
    unifi.sync(db, unifi.from_settings())
    db.add(Subnet(network="10.0.1.0/24", name="Office"))
    db.add(IpAddress(address="10.0.1.20", mac="bb:bb:bb:00:00:01", hostname="reception"))
    db.commit()
    rows = client.get("/api/ipam/addresses?q=10.0.1.20", headers=as_user(agent)).json()
    seen = rows[0]["observations"]
    assert len(seen) == 1 and seen[0]["provider_label"] == "UniFi" and seen[0]["uplink_name"] == "Lobby AP" and seen[0]["stale"] is False
    # Off: nothing shown and the lists are closed.
    settings.unifi_enabled = False
    assert client.get("/api/ipam/addresses?q=10.0.1.20", headers=as_user(agent)).json()[0]["observations"] == []
    assert client.get("/api/unifi/clients", headers=as_user(agent)).status_code == 404


def test_lists_and_admin_test(client, db, admin, agent, fake):
    unifi.sync(db, unifi.from_settings())
    db.commit()
    assert client.get("/api/unifi/clients?q=reception", headers=as_user(agent)).json()[0]["hostname"] == "reception-laptop"
    assert [d["name"] for d in client.get("/api/unifi/devices", headers=as_user(agent)).json()] == ["Gateway", "Lobby AP"]
    assert client.get("/api/unifi/sites", headers=as_user(agent)).json() == [{"source_id": "console-1:site-1", "name": "Head office"}]
    assert client.post("/api/unifi/test", headers=as_user(agent)).status_code == 403
    tested = client.post("/api/unifi/test", headers=as_user(admin)).json()
    assert tested == {"consoles": ["HQ console (console-1)"], "sites": ["Head office (console-1:site-1)"]}
    status = client.get("/api/unifi/status", headers=as_user(admin)).json()
    assert status["clients"] == 251 and status["devices"] == 2 and status["configured"] is True


def test_voucher_needs_the_switch_and_permission_and_is_noted_on_the_ticket(client, db, admin, agent, requester, fake, monkeypatch, service_desk):
    from app import services
    from app.models import TicketSource
    unifi.sync(db, unifi.from_settings())
    ticket = services.create_ticket(db, subject="Visitor Wi-Fi", description="", category=None, requester=requester, source=TicketSource.portal, actor=requester)
    db.commit()
    body = {"source_id": "console-1:site-1", "minutes": 480, "guests": 2}
    assert client.post(f"/api/unifi/tickets/{ticket.id}/vouchers", json=body, headers=as_user(admin)).status_code == 409
    monkeypatch.setattr(settings, "unifi_vouchers_enabled", True)
    assert client.post(f"/api/unifi/tickets/{ticket.id}/vouchers", json=body, headers=as_user(agent)).status_code == 403
    assert client.post(f"/api/unifi/tickets/{ticket.id}/vouchers", json={**body, "source_id": "console-1:other"}, headers=as_user(admin)).status_code == 422
    made = client.post(f"/api/unifi/tickets/{ticket.id}/vouchers", json=body, headers=as_user(admin))
    assert made.status_code == 200, made.text
    assert made.json() == {"codes": ["12345-67890"], "minutes": 480, "site": "Head office"}
    db.expire_all()
    note = db.query(Comment).filter_by(ticket_id=ticket.id).order_by(Comment.id.desc()).first()
    assert note.is_internal and "12345-67890" in note.body and "8 h" in note.body


def test_local_mode_and_settings_checks(db, admin, monkeypatch):
    monkeypatch.setattr(settings, "unifi_mode", "local")
    monkeypatch.setattr(settings, "unifi_console_url", "https://unifi.corp.example/")
    assert unifi.bases() == [("unifi.corp.example", "https://unifi.corp.example/proxy/network/integration/v1")]
    for bad in ({"unifi_console_url": "http://unifi.corp.example"}, {"unifi_console_url": "https://unifi.corp.example/path"}, {"unifi_console_ids": ["bad id"]}, {"unifi_sync_minutes": 1}):
        with pytest.raises(configstore.SettingsError):
            configstore.update(db, "unifi", bad, admin)


def test_network_helpers_reject_garbage():
    assert network.mac("not-a-mac") == "" and network.mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert network.address("999.1.1.1") == "" and network.clip({"x": 1}, 10) == ""
