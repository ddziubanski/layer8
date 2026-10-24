"""Synthetic diagnostics contracts, permission gates and cross-process request limits."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import func, select, text

from app import auth, meraki, meraki_diagnostics as diag, meraki_oauth as oauth, sla
from app.config import settings
from app.models import IpAddress, MerakiClient, MerakiDiagnostic
from tests.conftest import as_user

MAC = "00:11:22:33:44:55"
NETWORK = "N_123"
SERIAL = "QXXX-EXAM-PLE1"
PATH = f"/api/meraki/clients/{NETWORK}/{MAC}/diagnostics"


def observation(**over):
    return dict(network_id=NETWORK, mac=MAC, network_name="Example office", client_id="client1", address="10.20.1.42",
                description="WS-0142", status="Online", vlan="20", switch_name="SW-01", switch_serial=SERIAL,
                port="12", connection="Wired", ssid="", last_seen_at=sla.utcnow(), observed_at=sla.utcnow(), **over)


def item(**over):
    data = observation(); data.update(over)
    return SimpleNamespace(**data)


def current(o):
    return dict(id=o.client_id, mac=o.mac, recentDeviceSerial=o.switch_serial,
                recentDeviceConnection=o.connection, switchport=o.port)


def port(**over):
    return dict(portId="12", enabled=True, status="Connected", speed="1 Gbps", duplex="full", isUplink=False,
                clientCount=2, errors=[], warnings=["High proportion of Ethernet collisions"], **over)


def event(**over):
    return dict(occurredAt=(sla.utcnow() - timedelta(seconds=30)).isoformat(), type="dhcp", subtype="timeout",
                severity="bad", deviceSerial=SERIAL, band="5", channel=36, rssi=-67, durationMs=4000, **over)


def transport(o=None, *, fresh=None, device=None, ports=None, events=None, link=None, failure=None, gate=None):
    o = o or item()
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.host == "api.meraki.com"
        assert request.headers["X-Cisco-Meraki-API-Key"] == "synthetic-key"
        if failure:
            return httpx.Response(failure, text="synthetic-key", headers={"Location": "https://example.com"})
        if gate:
            gate[0].set(); assert gate[1].wait(10)
        path = request.url.path
        if path.endswith("/clients/" + o.client_id):
            return httpx.Response(200, json=fresh if fresh is not None else current(o))
        if path == "/api/v1/devices/" + o.switch_serial:
            return httpx.Response(200, json=device if device is not None else dict(serial=o.switch_serial, networkId=o.network_id))
        if path.endswith("/switch/ports/statuses"):
            assert request.url.params["timespan"] == "900"
            return httpx.Response(200, json=ports if ports is not None else [port()])
        assert path.endswith("/connectivityEvents"), path
        assert request.url.params["perPage"] == "50" and request.url.params["sortOrder"] == "descending"
        assert "t0" in request.url.params and "t1" in request.url.params
        return httpx.Response(200, json=events if events is not None else [event()],
                              headers={"Link": f'<{link}>; rel="next"'} if link else {})
    return meraki.Client("synthetic-key", httpx.Client(transport=httpx.MockTransport(handler))), calls


@pytest.fixture
def configured(monkeypatch):
    for key, value in dict(meraki_enabled=True, meraki_diagnostics_enabled=True, meraki_auth_mode="api_key",
                           meraki_api_key="synthetic-key", meraki_network_ids=[NETWORK]).items():
        monkeypatch.setattr(settings, key, value)


@pytest.fixture
def observed(db):
    o = MerakiClient(**observation()); db.add(o); db.commit()
    return o


def test_wired_checks_current_identity_network_and_port_without_returning_other_ports():
    client, calls = transport(ports=[port(), {"portId": "13", "status": "other-port-private-data"}])
    result = diag.fetch(client, item(), sla.utcnow())
    assert result.port.port_id == "12" and result.port.client_count == 2
    assert "other-port-private-data" not in result.model_dump_json()
    assert [r.url.path for r in calls] == ["/api/v1/networks/N_123/clients/client1", "/api/v1/devices/" + SERIAL, "/api/v1/devices/" + SERIAL + "/switch/ports/statuses"]
    assert result.read_at.tzinfo and result.window_end - result.window_start == timedelta(minutes=15)


@pytest.mark.parametrize("change", [dict(mac="00:11:22:33:44:66"), dict(id="different"), dict(recentDeviceSerial="OTHER-SERIAL"), dict(switchport="13"), dict(recentDeviceConnection="Wireless")])
def test_moved_or_reassigned_client_never_reaches_port_endpoint(change):
    fresh = {**current(item()), **change}
    client, calls = transport(fresh=fresh)
    with pytest.raises(meraki.MerakiError, match="identity or location"):
        diag.fetch(client, item(), sla.utcnow())
    assert len(calls) == 1


@pytest.mark.parametrize("change", [dict(networkId="N_OTHER", serial=SERIAL), dict(networkId=NETWORK, serial="OTHER")])
def test_moved_switch_never_reaches_port_endpoint(change):
    client, calls = transport(device=change)
    with pytest.raises(meraki.MerakiError, match="switch"):
        diag.fetch(client, item(), sla.utcnow())
    assert len(calls) == 2


@pytest.mark.parametrize("ports", [[], [port(), port()], ["invalid"], [{"portId": "12", "clientCount": True}], [{"portId": "12", "errors": ["x" * 257]}], [{"portId": "12", "warnings": ["x"] * 33}]])
def test_invalid_or_ambiguous_port_response_is_refused(ports):
    client, _ = transport(ports=ports)
    with pytest.raises(meraki.MerakiError): diag.fetch(client, item(), sla.utcnow())


def test_wireless_bounds_events_omits_payloads_and_never_follows_link():
    o = item(connection="Wireless", port="")
    payload = {**event(), "eventData": {"url": "https://example.com/private"}, "captureId": "private-capture"}
    client, calls = transport(o, events=[payload], link="https://example.com/steal-credentials")
    result = diag.fetch(client, o, sla.utcnow())
    assert result.kind == "wireless" and result.events_truncated and len(result.events) == 1
    assert result.events[0].rssi == -67 and result.events[0].occurred_at.tzinfo
    assert "private" not in result.model_dump_json() and len(calls) == 2


@pytest.mark.parametrize("payload", [[{**event(), "occurredAt": "2026-01-01"}], [{**event(), "occurredAt": (sla.utcnow() + timedelta(days=1)).isoformat()}], [{**event(), "rssi": "-67"}], [{**event(), "channel": True}], [event()] * 51, {}])
def test_invalid_wireless_events_fail_closed(payload):
    o = item(connection="Wireless", port="")
    client, _ = transport(o, events=payload)
    with pytest.raises(meraki.MerakiError): diag.fetch(client, o, sla.utcnow())


@pytest.mark.parametrize("code", [302, 401, 403, 500])
def test_redirects_and_provider_errors_are_fixed_messages(code):
    client, calls = transport(failure=code)
    with pytest.raises(meraki.MerakiError) as exc: diag.fetch(client, item(), sla.utcnow())
    assert "synthetic-key" not in str(exc.value) and len(calls) == 1


def test_oauth_scope_expansion_is_explicit_and_invalidates_old_registration(monkeypatch):
    monkeypatch.setattr(settings, "meraki_auth_mode", "oauth")
    monkeypatch.setattr(settings, "meraki_client_id", "example-client")
    monkeypatch.setattr(settings, "meraki_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "portal_url", "https://helpdesk.corp.example")
    monkeypatch.setattr(settings, "meraki_diagnostics_enabled", False)
    _, before = oauth.configuration()
    assert oauth.scopes() == oauth.SCOPES
    monkeypatch.setattr(settings, "meraki_diagnostics_enabled", True)
    _, after = oauth.configuration()
    assert before != after
    assert set(oauth.scopes().split()) == set(oauth.SCOPES.split()) | {"switch:telemetry:read", "wireless:telemetry:read"}
    assert all(s.endswith(":read") for s in oauth.scopes().split())


def test_cache_get_never_contacts_meraki_and_post_is_explicit(client, agent, configured, observed, monkeypatch, db):
    c, calls = transport()
    monkeypatch.setattr(meraki, "from_settings", lambda: c)
    assert client.get(PATH, headers=as_user(agent)).json() is None and not calls
    first = client.post(PATH, headers=as_user(agent))
    assert first.status_code == 200, first.text
    body = first.json()
    assert not body["cached"] and body["data"]["port"]["client_count"] == 2
    assert len(calls) == 3
    # A second request reuses the exact successful read and does not call the provider.
    second = client.post(PATH, headers=as_user(agent)).json()
    assert second["cached"] and second["data"] == body["data"] and len(calls) == 3
    assert client.get(PATH, headers=as_user(agent)).json()["data"] == body["data"]
    assert db.scalar(select(func.count()).select_from(MerakiDiagnostic)) == 1


@pytest.mark.parametrize("setting,value", [("meraki_enabled", False), ("meraki_diagnostics_enabled", False), ("meraki_network_ids", [])])
def test_disabled_or_removed_network_cannot_read_even_cached_data(client, agent, configured, observed, monkeypatch, setting, value):
    monkeypatch.setattr(settings, setting, value)
    monkeypatch.setattr(meraki, "from_settings", lambda: pytest.fail("must not contact provider"))
    assert client.get(PATH, headers=as_user(agent)).status_code == 404
    assert client.post(PATH, headers=as_user(agent)).status_code == 404


def test_requester_and_portal_session_cannot_read(client, requester, admin, configured, observed, db):
    assert client.get(PATH, headers=as_user(requester)).status_code == 403
    assert client.post(PATH, headers=as_user(requester)).status_code == 403
    response = Response()
    auth.start_session(db, admin, response, sla.utcnow(), portal_only=True); db.commit()
    token = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    client.cookies.set(auth.COOKIE, token)
    assert client.get(PATH).status_code == 403
    assert client.post(PATH, headers={auth.CSRF_HEADER: "1"}).status_code == 403


def test_real_browser_post_requires_csrf(client, agent, configured, observed, db, monkeypatch):
    response = Response(); auth.start_session(db, agent, response, sla.utcnow()); db.commit()
    client.cookies.set(auth.COOKIE, response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1])
    monkeypatch.setattr(meraki, "from_settings", lambda: transport()[0])
    assert client.post(PATH).status_code == 403
    assert client.post(PATH, headers={auth.CSRF_HEADER: "1"}).status_code == 200


def test_failed_refresh_retains_previous_success_and_consumes_cooldown(client, agent, configured, observed, db, monkeypatch):
    monkeypatch.setattr(meraki, "from_settings", lambda: transport()[0])
    old = client.post(PATH, headers=as_user(agent)).json()["data"]
    row = db.get(MerakiDiagnostic, (NETWORK, MAC)); row.attempted_at -= timedelta(minutes=2); db.commit()
    c, calls = transport(failure=500)
    monkeypatch.setattr(meraki, "from_settings", lambda: c)
    failed = client.post(PATH, headers=as_user(agent)).json()
    assert failed["error"] and failed["data"] == old and not failed["cached"]
    again = client.post(PATH, headers=as_user(agent)).json()
    assert again["cached"] and again["error"] == failed["error"] and len(calls) == 1


def test_changed_cached_attachment_hides_old_diagnostics(client, agent, configured, observed, db, monkeypatch):
    monkeypatch.setattr(meraki, "from_settings", lambda: transport()[0])
    assert client.post(PATH, headers=as_user(agent)).status_code == 200
    observed.port = "13"; db.commit()
    assert client.get(PATH, headers=as_user(agent)).json() is None
    assert client.post(PATH, headers=as_user(agent)).status_code == 429


def test_global_cooldown_and_busy_lock_prevent_provider_calls(client, agent, configured, observed, db, monkeypatch):
    c, calls = transport()
    monkeypatch.setattr(meraki, "from_settings", lambda: c)
    assert client.post(PATH, headers=as_user(agent)).status_code == 200
    other = MerakiClient(**{**observation(), "mac": "00:11:22:33:44:66"}); db.add(other); db.commit()
    result = client.post(PATH.replace(MAC, other.mac), headers=as_user(agent))
    assert result.status_code == 429 and int(result.headers["retry-after"]) <= 6 and len(calls) == 3
    db.execute(text("SELECT pg_advisory_xact_lock(82638116)"))
    result = client.post(PATH, headers=as_user(agent))
    assert result.status_code == 409 and len(calls) == 3
    db.rollback()


def test_concurrent_sessions_perform_only_one_provider_read(session_factory, configured, observed, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    c, calls = transport(gate=(entered, release))
    monkeypatch.setattr(meraki, "from_settings", lambda: c)
    def run():
        with session_factory() as session: return diag.read(session, NETWORK, MAC)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        assert entered.wait(5)
        try:
            with session_factory() as second:
                with pytest.raises(HTTPException) as exc: diag.read(second, NETWORK, MAC)
                assert exc.value.status_code == 409
        finally:
            release.set()
        assert future.result(timeout=10)["data"].port.port_id == "12"
    assert len(calls) == 3


def test_diagnostics_do_not_modify_documented_ipam_or_discovery(client, agent, configured, observed, db, monkeypatch):
    address = IpAddress(address="10.20.1.43", mac=MAC, hostname="ws-0142.corp.example")
    db.add(address); db.commit()
    before = meraki.as_dict(observed)
    monkeypatch.setattr(meraki, "from_settings", lambda: transport()[0])
    assert client.post(PATH, headers=as_user(agent)).status_code == 200
    db.refresh(address); db.refresh(observed)
    assert str(address.address) == "10.20.1.43" and address.hostname == "ws-0142.corp.example" and address.last_seen_at is None
    assert meraki.as_dict(observed) == before


def test_cache_retention_is_bounded_by_age_and_count(client, agent, configured, observed, db, monkeypatch):
    monkeypatch.setattr(diag, "MAX_CACHE", 3)
    for n in range(6):
        db.add(MerakiDiagnostic(network_id=NETWORK, mac=f"00:11:22:33:44:{n:02x}", identity_hash="x" * 64,
                               attempted_at=sla.utcnow() - timedelta(days=2) if n == 0 else sla.utcnow() - timedelta(minutes=n + 2), data={}))
    db.commit()
    monkeypatch.setattr(meraki, "from_settings", lambda: transport()[0])
    assert client.post(PATH, headers=as_user(agent)).status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(MerakiDiagnostic)) == 3
    assert db.get(MerakiDiagnostic, (NETWORK, MAC)) is not None
    assert db.get(MerakiDiagnostic, (NETWORK, "00:11:22:33:44:00")) is None



def test_enabled_diagnostics_can_authorize_exactly_the_four_read_scopes(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    for key, value in dict(meraki_auth_mode="oauth", meraki_client_id="example-client", meraki_client_secret="synthetic-secret",
                           portal_url="https://helpdesk.corp.example", meraki_network_ids=[NETWORK], meraki_diagnostics_enabled=False).items():
        monkeypatch.setattr(settings, key, value)
    _, fingerprint = oauth.configuration()
    oauth._write("tokens", dict(config=fingerprint, access_token="synthetic-old", refresh_token="synthetic-refresh", expires_at=oauth.time.time()+3600))
    assert oauth.status()["connected"]
    monkeypatch.setattr(settings, "meraki_diagnostics_enabled", True)
    assert not oauth.status()["connected"]
    with pytest.raises(meraki.MerakiError): oauth.access()
    real_client = httpx.Client
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url == oauth.TOKEN_URL and request.method == "POST"
        form = parse_qs(request.content.decode())
        assert form["scope"] == [oauth.scopes()]
        return httpx.Response(200, json=dict(access_token="synthetic-new", refresh_token="synthetic-new-refresh", token_type="Bearer", expires_in=3600, scope=oauth.scopes()))
    monkeypatch.setattr(oauth.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    params = parse_qs(urlsplit(oauth.begin(7, "browser-session")).query)
    assert params["scope"] == [oauth.scopes()]
    oauth.finish(7, "browser-session", dict(state=params["state"][0], code="synthetic-code"))
    assert oauth.access() == "synthetic-new" and len(calls) == 1
    assert oauth.status()["connected"]



def test_positive_provider_rssi_is_retained_without_inventing_units():
    o = item(connection="Wireless", port="")
    client, _ = transport(o, events=[{**event(), "rssi": 80}])
    result = diag.fetch(client, o, sla.utcnow())
    assert result.events[0].rssi == 80


def test_diagnostics_setting_is_admin_only_and_importable(client, admin, agent):
    body = {"values": {"meraki_diagnostics_enabled": True}}
    assert client.put("/api/settings/meraki", json=body, headers=as_user(agent)).status_code == 403
    assert client.put("/api/settings/meraki", json=body, headers=as_user(admin)).status_code == 200
    assert settings.meraki_diagnostics_enabled



def test_wireless_event_times_are_returned_newest_first():
    o = item(connection="Wireless", port="")
    old = {**event(), "occurredAt": (sla.utcnow() - timedelta(minutes=5)).isoformat(), "type": "dhcp"}
    recent = {**event(), "occurredAt": (sla.utcnow() - timedelta(minutes=1)).isoformat(), "type": "roam"}
    client, _ = transport(o, events=[old, recent])
    result = diag.fetch(client, o, sla.utcnow())
    assert [e.type for e in result.events] == ["roam", "dhcp"]


def test_oauth_diagnostic_reads_send_only_bearer_credentials(client, agent, configured, observed, session_factory, monkeypatch):
    from app import db as database
    for key, value in dict(meraki_auth_mode="oauth", meraki_client_id="example-client", meraki_client_secret="synthetic-secret",
                           portal_url="https://helpdesk.corp.example").items():
        monkeypatch.setattr(settings, key, value)
    _, fingerprint = oauth.configuration()
    oauth._write("tokens", dict(config=fingerprint, access_token="synthetic-oauth-diagnostics", refresh_token="synthetic-refresh", expires_at=oauth.time.time()+3600))
    monkeypatch.setattr(database, "get_session_factory", lambda: session_factory)
    real_client, calls = httpx.Client, []
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.host == "api.meraki.com"
        assert request.headers["authorization"] == "Bearer synthetic-oauth-diagnostics"
        assert "x-cisco-meraki-api-key" not in request.headers
        if request.url.path.endswith("/clients/client1"): return httpx.Response(200, json=current(observed))
        if request.url.path.endswith("/devices/" + SERIAL): return httpx.Response(200, json=dict(serial=SERIAL, networkId=NETWORK))
        assert request.url.path.endswith("/switch/ports/statuses")
        return httpx.Response(200, json=[port()])
    monkeypatch.setattr(meraki.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    result = client.post(PATH, headers=as_user(agent))
    assert result.status_code == 200, result.text
    assert result.json()["data"]["port"]["status"] == "Connected" and len(calls) == 3
