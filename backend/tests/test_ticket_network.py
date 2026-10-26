# SPDX-License-Identifier: AGPL-3.0-or-later
"""A single ticket Devices workflow for assets, cached UniFi entries and scanned IPs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app import services, sla
from app.config import settings
from app.models import Device, FieldList, FieldOption, IpAddress, IpSource, NetworkInventory, NetworkObservation, Ticket, TicketEvent, TicketNetworkLink, TicketSource
from tests.conftest import as_user


@pytest.fixture
def inventory(db, monkeypatch):
    monkeypatch.setattr(settings, "unifi_enabled", True)
    monkeypatch.setattr(settings, "unifi_api_key", "synthetic-key")
    monkeypatch.setattr(settings, "unifi_mode", "local")
    monkeypatch.setattr(settings, "unifi_console_url", "https://console.example.com")
    db.add_all([
        NetworkInventory(provider="unifi", source_id="console:site-a", device_key="ap-1", name="Lobby AP", model="Example AP", kind="ap",
            source_name="Example office", address="192.0.2.10", mac="aa:bb:cc:00:00:01", state="online", observed_at=sla.utcnow()),
        NetworkInventory(provider="unifi", source_id="console:site-b", device_key="ap-1", name="Lobby AP", model="Example AP", kind="ap",
            source_name="Example branch", address="192.0.2.10", mac="aa:bb:cc:00:00:01", state="offline", observed_at=sla.utcnow()),
        NetworkObservation(provider="unifi", source_id="console:site-a", source_name="Example office", mac="aa:bb:cc:00:00:02",
            address="192.0.2.20", hostname="laptop.example.com", ssid="Example staff", uplink_name="Lobby AP", observed_at=sla.utcnow() - timedelta(days=2)),
        IpAddress(address="192.0.2.30", hostname="printer.example.com", source=IpSource.scan, mac="aa:bb:cc:00:00:03"),
        IpAddress(address="2001:db8::40", hostname="ipv6.example.com", source=IpSource.scan),
    ])
    db.commit()


@pytest.fixture
def ticket(db, requester, service_desk):
    row = Ticket(subject="Network trouble", requester_id=requester.id, queue_id=service_desk.id, source=TicketSource.portal)
    db.add(row); db.commit()
    return row


def api_path(ticket, suffix):
    return f"/api/tickets/{ticket.id}/{suffix}"


def ref(source="console:site-a"):
    return {"kind": "unifi_device", "source_id": source, "key": "ap-1"}


def test_search_finds_equipment_clients_and_scanned_ips(client, agent, ticket, inventory):
    for query, expected in [("192.0.2.10", "unifi_device"), ("aa:bb:cc:00:00:02", "unifi_client"),
                            ("printer", "ip_address"), ("2001:db8::40", "ip_address"), ("Example branch", "unifi_device")]:
        response = client.get(api_path(ticket, "device-candidates"), params={"q": query}, headers=as_user(agent))
        assert response.status_code == 200, response.text
        assert response.json() and all(row["kind"] == expected for row in response.json())
    rows = client.get(api_path(ticket, "device-candidates"), params={"q": "192.0.2.10"}, headers=as_user(agent)).json()
    assert len(rows) == 2 and {r["source_name"] for r in rows} == {"Example office", "Example branch"}
    assert all(r["source_label"] == "UniFi" for r in rows)
    stale = client.get(api_path(ticket, "device-candidates"), params={"q": "laptop"}, headers=as_user(agent)).json()[0]
    assert stale["stale"]
    scan = client.get(api_path(ticket, "device-candidates"), params={"q": "printer"}, headers=as_user(agent)).json()[0]
    assert scan["source_label"] == "IPAM scan"
    assert client.get(api_path(ticket, "device-candidates"), params={"q": "%%"}, headers=as_user(agent)).json() == []


def test_link_is_idempotent_audited_and_scoped_by_site(client, db, agent, ticket, inventory):
    path = api_path(ticket, "network-links")
    for _ in range(2):
        response = client.post(path, json=ref(), headers=as_user(agent))
        assert response.status_code == 201, response.text
        assert len(response.json()) == 1
    assert len(client.post(path, json=ref("console:site-b"), headers=as_user(agent)).json()) == 2
    rows = db.scalars(select(TicketNetworkLink)).all()
    assert len(rows) == 2
    events = db.scalars(select(TicketEvent).where(TicketEvent.type == "device_linked")).all()
    assert len(events) == 2 and all(e.data["network_target"]["source_label"] == "UniFi" for e in events)
    # A link doesn't invent an asset or overwrite the documented IP database.
    assert not db.scalars(select(Device)).all()
    assert len(db.scalars(select(IpAddress)).all()) == 2


def test_snapshot_survives_refresh_and_address_changes(client, db, agent, ticket, inventory):
    path = api_path(ticket, "network-links")
    linked = client.post(path, json=ref(), headers=as_user(agent)).json()[0]
    row = db.get(NetworkInventory, ("unifi", "console:site-a", "ap-1"))
    row.address = "192.0.2.99"; db.commit()
    current = client.get(path, headers=as_user(agent)).json()[0]
    assert current["target"]["address"] == "192.0.2.10" and current["current"]["address"] == "192.0.2.99"
    db.execute(delete(NetworkInventory)); db.commit()
    old = client.get(path, headers=as_user(agent)).json()[0]
    assert old["target"]["name"] == "Lobby AP" and old["current"] is None
    assert client.delete(f"{path}/{linked['id']}", headers=as_user(agent)).json() == []
    assert db.scalar(select(TicketEvent).where(TicketEvent.type == "device_unlinked")) is not None


def test_ip_link_retains_identity_when_address_is_reused(client, db, agent, ticket, inventory):
    row = db.scalar(select(IpAddress).where(IpAddress.hostname == "printer.example.com"))
    path = api_path(ticket, "network-links")
    response = client.post(path, json={"kind": "ip_address", "key": str(row.id)}, headers=as_user(agent))
    assert response.status_code == 201, response.text
    db.delete(row); db.commit()
    db.add(IpAddress(address="192.0.2.30", hostname="different.example.com")); db.commit()
    linked = client.get(path, headers=as_user(agent)).json()[0]
    assert linked["current"] is None and linked["target"]["name"] == "printer.example.com"


def test_ticket_access_and_cross_ticket_unlink(client, db, agent, requester, admin, ticket, inventory):
    path = api_path(ticket, "network-links")
    for suffix in ("network-links", "device-candidates?q=192.0.2"):
        assert client.get(api_path(ticket, suffix), headers=as_user(requester)).status_code == 403
    assert client.post(path, json=ref(), headers=as_user(requester)).status_code == 403
    linked = client.post(path, json=ref(), headers=as_user(agent)).json()[0]
    other = Ticket(subject="Other", requester_id=requester.id, queue_id=ticket.queue_id, source=TicketSource.portal)
    db.add(other); db.commit()
    assert client.delete(api_path(other, f"network-links/{linked['id']}"), headers=as_user(agent)).status_code == 404
    ticket.queue.restricted = True; db.commit()
    assert client.get(path, headers=as_user(agent)).status_code == 404
    assert client.post(path, json=ref(), headers=as_user(agent)).status_code == 404
    assert client.delete(f"{path}/{linked['id']}", headers=as_user(agent)).status_code == 404
    assert client.get(path, headers=as_user(admin)).status_code == 200


def test_disabled_source_and_forged_fields_are_rejected(client, agent, ticket, inventory, monkeypatch):
    path = api_path(ticket, "network-links")
    assert client.post(path, json={**ref(), "name": "Forged"}, headers=as_user(agent)).status_code == 422
    assert client.post(path, json={"kind": "unifi_device", "source_id": "not-a-site", "key": "ap-1"}, headers=as_user(agent)).status_code == 404
    assert client.post(path, json=ref(), headers=as_user(agent)).status_code == 201
    monkeypatch.setattr(settings, "unifi_enabled", False)
    assert client.get(api_path(ticket, "device-candidates"), params={"q": "Lobby"}, headers=as_user(agent)).json() == []
    assert client.post(path, json=ref(), headers=as_user(agent)).status_code == 404
    assert client.get(path, headers=as_user(agent)).json()[0]["current"] is None


def test_simultaneous_link_clicks_do_not_duplicate(client, agent, ticket, inventory):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(api_path(ticket, "network-links"), json=ref(), headers=as_user(agent)), range(2)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert all(len(r.json()) == 1 for r in results)


def test_existing_asset_can_be_found_by_linked_ip_or_mac(client, db, agent):
    device = Device(name="PC-0042")
    db.add(device); db.flush()
    db.add(IpAddress(address="192.0.2.42", hostname="pc-0042.example.com", mac="aa:bb:cc:00:00:42", device_id=device.id)); db.commit()
    for query in ("192.0.2.42", "aa:bb:cc:00:00:42"):
        response = client.get("/api/devices", params={"q": query}, headers=as_user(agent))
        assert response.status_code == 200, response.text
        assert [d["id"] for d in response.json()["items"]] == [device.id]


def test_merge_keeps_device_links_without_duplicates(client, db, agent, requester, service_desk, ticket, inventory):
    target = Ticket(subject="Keep", requester_id=requester.id, queue_id=service_desk.id, source=TicketSource.portal)
    why = FieldOption(list=FieldList.merge_reason, label="Duplicate", position=0)
    db.add_all([target, why]); db.commit()
    for t in (ticket, target):
        client.post(api_path(t, "network-links"), json=ref(), headers=as_user(agent))
    client.post(api_path(ticket, "network-links"), json=ref("console:site-b"), headers=as_user(agent))
    services.merge(db, ticket, target, agent, why); db.commit()
    assert len(client.get(api_path(target, "network-links"), headers=as_user(agent)).json()) == 2
