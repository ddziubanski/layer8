# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only Meraki transport, atomic snapshots and conservative IPAM identity matching."""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app import meraki, sla
from app.config import settings
from app.models import Device, IpAddress, MerakiClient
from tests.conftest import as_user

MAC = "00:11:22:33:44:55"
NETWORK = {"id": "N_123", "name": "Example office"}


def body(**over):
    return {"id": "client1", "mac": MAC, "ip": "10.20.1.42", "description": "WS-0142",
            "status": "Online", "lastSeen": sla.utcnow().isoformat(), "recentDeviceName": "SW-01",
            "recentDeviceSerial": "QXXX-EXAM-PLE1", "switchport": "12", "vlan": "20", **over}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "meraki_enabled", True)
    monkeypatch.setattr(settings, "meraki_api_key", "example-secret")
    monkeypatch.setattr(settings, "meraki_network_ids", [NETWORK["id"]])


def transport(handler, sleep=lambda _: None):
    return meraki.Client("example-secret", httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleep)


class Fake:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [body()]
        self.calls = []

    def network(self, id):
        self.calls.append(id)
        if id != NETWORK["id"]:
            raise meraki.MerakiError("Network unavailable")
        return NETWORK

    def clients(self, id):
        return self.rows

    def close(self):
        pass


def test_pages_preserve_bounds_and_only_get():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.host == "api.meraki.com"
        assert request.headers["X-Cisco-Meraki-API-Key"] == "example-secret"
        assert request.url.params["timespan"] == "86400"
        assert request.url.params["perPage"] == "1000"
        if "startingAfter" not in request.url.params:
            return httpx.Response(200, json=[body()], headers={"Link": '<https://api.meraki.com/api/v1/networks/N_123/clients?startingAfter=next>; rel="next"'})
        return httpx.Response(200, json=[body(mac="00:11:22:33:44:66")])
    assert len(list(transport(handler).clients("N_123"))) == 2
    assert len(calls) == 2


@pytest.mark.parametrize("link", ["https://example.com/api/v1/networks/N_123/clients?startingAfter=x",
    "http://api.meraki.com/api/v1/networks/N_123/clients?startingAfter=x",
    "https://api.meraki.com/api/v1/networks/N_OTHER/clients?startingAfter=x",
    "https://api.meraki.com/api/v1/networks/N_123/clients?startingAfter=x&startingAfter=y"])
def test_untrusted_pagination_is_never_followed(link):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[], headers={"Link": f'<{link}>; rel="next"'})
    with pytest.raises(meraki.MerakiError, match="pagination"):
        list(transport(handler).clients("N_123"))
    assert len(calls) == 1


def test_redirect_refused_and_errors_do_not_echo_secrets():
    for code in [301, 302, 401, 403, 500]:
        c = transport(lambda r: httpx.Response(code, headers={"Location": "https://example.com"}, text="example-secret"))
        with pytest.raises(meraki.MerakiError) as exc:
            c.network("N_123")
        assert "example-secret" not in str(exc.value)


def test_rate_limit_retries_are_bounded():
    calls, sleeps = [], []
    def handler(r):
        calls.append(r)
        return httpx.Response(429, headers={"Retry-After": "999"})
    with pytest.raises(meraki.MerakiError):
        transport(handler, sleep=sleeps.append).network("N_123")
    assert len(calls) == 3 and sleeps == [5, 5]


@pytest.mark.parametrize("over", [{"mac": "oops"}, {"ip": "not-an-ip"}, {"lastSeen": "yesterday"}, {"lastSeen": True}, {"description": "x" * 256}])
def test_bad_observations_fail_the_snapshot(over):
    with pytest.raises(meraki.MerakiError):
        meraki.observation(body(**over), NETWORK, sla.utcnow())


def test_snapshot_enriches_exact_mac_without_changing_documented_data(db, configured, client, agent):
    d = Device(name="WS-0142")
    db.add(d); db.flush()
    ip = IpAddress(address="10.20.1.43", hostname="documented.corp.example", mac=MAC, device_id=d.id)
    db.add(ip); db.commit()
    assert meraki.sync(db, Fake()) == 1
    db.commit()
    r = client.get(f"/api/ipam/devices/{d.id}/addresses", headers=as_user(agent))
    assert r.status_code == 200
    address = r.json()[0]["address"]
    assert address["address"] == "10.20.1.43" and address["hostname"] == "documented.corp.example"
    assert address["meraki"]["address"] == "10.20.1.42"
    assert address["meraki"]["port"] == "12" and not address["meraki"]["stale"]
    assert ip.last_seen_at is None
    assert client.get("/api/meraki/clients", headers=as_user(agent)).json()[0]["mac"] == MAC
    assert client.get("/api/meraki/clients?q=nothing", headers=as_user(agent)).json() == []


def test_partial_failed_or_duplicate_reads_keep_previous_snapshot(db, configured, monkeypatch):
    meraki.sync(db, Fake()); db.commit()
    old = db.query(MerakiClient).one().observed_at
    monkeypatch.setattr(settings, "meraki_network_ids", ["N_123", "N_OTHER"])
    with pytest.raises(meraki.MerakiError):
        meraki.sync(db, Fake([body(description="New value")]))
    db.rollback()
    assert db.query(MerakiClient).one().description == "WS-0142"
    monkeypatch.setattr(settings, "meraki_network_ids", ["N_123"])
    with pytest.raises(meraki.MerakiError, match="duplicate"):
        meraki.sync(db, Fake([body(), body()]))
    db.rollback()
    assert db.query(MerakiClient).one().observed_at == old
    assert meraki.sync(db, Fake([])) == 0
    db.commit()
    assert db.query(MerakiClient).count() == 0


def test_ambiguous_macs_never_enrich(db, configured):
    meraki.sync(db, Fake()); db.commit()
    assert MAC in meraki.context_for(db, [MAC])
    second = meraki.observation(body(), {"id": "N_456", "name": "Example branch"}, sla.utcnow())
    db.add(second); db.commit()
    settings.meraki_network_ids = ["N_123", "N_456"]
    assert meraki.context_for(db, [MAC]) == {}
    settings.meraki_network_ids = ["N_123"]
    db.add_all([IpAddress(address="10.20.1.42", mac=MAC), IpAddress(address="10.20.1.43", mac=MAC)])
    db.commit()
    assert meraki.context_for(db, [MAC]) == {}


def test_disabling_or_removing_networks_hides_cache(db, configured, client, agent, monkeypatch):
    meraki.sync(db, Fake()); db.commit()
    monkeypatch.setattr(settings, "meraki_network_ids", ["N_OTHER"])
    assert client.get("/api/meraki/clients", headers=as_user(agent)).json() == []
    monkeypatch.setattr(settings, "meraki_enabled", False)
    assert meraki.context_for(db, [MAC]) == {}
    assert meraki.sync(db, Fake()) == 0


def test_old_cache_and_unknown_last_seen_are_stale():
    item = meraki.observation(body(lastSeen=None), NETWORK, sla.utcnow())
    assert meraki.as_dict(item)["stale"]
    item = meraki.observation(body(lastSeen=(sla.utcnow() - timedelta(days=3)).isoformat()), NETWORK, sla.utcnow() - timedelta(days=3))
    assert meraki.as_dict(item)["stale"]


def test_admin_credentials_test_and_permissions(client, admin, agent, requester, monkeypatch, tmp_path):
    for user in (agent, requester):
        assert client.post("/api/meraki/test", headers=as_user(user)).status_code == 403
        assert client.get("/api/meraki/status", headers=as_user(user)).status_code == 403
    assert client.get("/api/meraki/clients", headers=as_user(requester)).status_code == 403
    assert client.get("/api/meraki/clients").status_code == 401
    values = {"meraki_enabled": True, "meraki_api_key": "example-secret", "meraki_network_ids": ["N_123"]}
    response = client.put("/api/settings/meraki", headers=as_user(admin), json={"values": values})
    assert response.status_code == 200 and "example-secret" not in response.text
    assert (tmp_path / "secrets" / "meraki_api_key").read_text() == "example-secret"
    assert client.put("/api/settings/meraki", headers=as_user(admin), json={"values": {"meraki_network_ids": ["https://example.com"]}}).status_code == 422
    monkeypatch.setattr(meraki, "from_settings", lambda: Fake())
    assert client.post("/api/meraki/test", headers=as_user(admin)).json() == {"networks": ["Example office"]}


def test_concurrent_discovery_is_refused(db, session_factory, configured):
    with session_factory() as other:
        other.execute(text("SELECT pg_advisory_xact_lock(82638114)"))
        with pytest.raises(meraki.MerakiError, match="already running"):
            meraki.sync(db, Fake())
        db.rollback()
