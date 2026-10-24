"""Synthetic OAuth exchanges; no Dashboard tenant or credentials are used."""
import asyncio
import json
import os
import threading
from urllib.parse import parse_qs, urlsplit
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import Response

from app import auth, meraki, meraki_oauth as oauth, sla
from app.config import settings
from tests.conftest import as_user


@pytest.fixture
def configured(monkeypatch):
    for key, value in dict(meraki_auth_mode="oauth", meraki_client_id="example-client",
                           meraki_client_secret="synthetic-secret", meraki_network_ids=["N_123"],
                           portal_url="https://helpdesk.corp.example", meraki_enabled=True).items():
        monkeypatch.setattr(settings, key, value)


def pending(user=7, cookie="browser-session"):
    return parse_qs(urlsplit(oauth.begin(user, cookie)).query)["state"][0]


def connected(expiry=3600):
    _, fingerprint = oauth.configuration()
    oauth._write("tokens", dict(config=fingerprint, access_token="synthetic-access", refresh_token="synthetic-refresh", expires_at=oauth.time.time() + expiry))


@pytest.fixture
def provider(monkeypatch):
    calls = []
    real_client = httpx.Client
    def handler(request):
        calls.append(request)
        assert request.url.host == "as.meraki.com" and request.method == "POST"
        assert request.headers["authorization"].startswith("Basic ")
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        if request.url.path == "/oauth/revoke":
            return httpx.Response(200)
        return httpx.Response(200, json=dict(access_token="synthetic-next-access", refresh_token="synthetic-next-refresh", expires_in=3600, token_type="Bearer", scope=oauth.SCOPES))
    monkeypatch.setattr(oauth.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return calls


@pytest.mark.parametrize("url", ["", "http://helpdesk.corp.example", "https://name:password@helpdesk.corp.example", "https://helpdesk.corp.example/path", "https://helpdesk.corp.example?x=1", "https://helpdesk.corp.example#x", "https://[bad"])
def test_requires_https_base(configured, monkeypatch, url):
    monkeypatch.setattr(settings, "portal_url", url)
    assert not oauth.status()["configured"]
    with pytest.raises(meraki.MerakiError):
        pending()


def test_authorization_requests_only_verified_read_scopes(configured):
    url = urlsplit(oauth.begin(7, "browser-session"))
    params = parse_qs(url.query)
    assert url.netloc == "as.meraki.com" and url.path == "/oauth/authorize"
    assert params["scope"] == [oauth.SCOPES]
    assert all(scope.endswith(":read") for scope in oauth.SCOPES.split())
    assert params["redirect_uri"] == [settings.portal_url + oauth.CALLBACK]
    assert "synthetic-secret" not in url.query
    record = oauth._read("pending")
    assert record["session"] != "browser-session" and record["state"] != params["state"][0]
    if os.name != "nt":
        assert oauth._path("pending").stat().st_mode & 0o777 == 0o600


def test_cookie_and_explicit_networks_required(configured, monkeypatch):
    with pytest.raises(meraki.MerakiError, match="browser"):
        pending(cookie=None)
    monkeypatch.setattr(settings, "meraki_network_ids", [])
    with pytest.raises(meraki.MerakiError, match="network"):
        pending()


@pytest.mark.parametrize("user,cookie,state", [(8, "browser-session", None), (7, "different-session", None), (7, "browser-session", "incorrect")])
def test_wrong_session_user_or_state_cannot_consume_flow(configured, provider, user, cookie, state):
    original = pending()
    with pytest.raises(meraki.MerakiError, match="session"):
        oauth.finish(user, cookie, dict(state=state or original, code="synthetic-code"))
    assert oauth._read("pending") and not provider
    oauth.finish(7, "browser-session", dict(state=original, code="synthetic-code"))
    assert oauth.status()["connected"]


def test_success_consumes_once_and_tokens_never_escape_status(configured, provider):
    state = pending()
    oauth.finish(7, "browser-session", dict(state=state, code="synthetic-code"))
    assert oauth.access() == "synthetic-next-access"
    assert len(provider) == 1 and not oauth._read("pending")
    form = parse_qs(provider[0].content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["redirect_uri"] == [settings.portal_url + oauth.CALLBACK]
    assert "synthetic" not in json.dumps(oauth.status())
    with pytest.raises(meraki.MerakiError):
        oauth.finish(7, "browser-session", dict(state=state, code="synthetic-code"))
    assert len(provider) == 1


@pytest.mark.parametrize("change", ["expired", "future", "registration", "denied"])
def test_invalid_flow_consumed_without_exchange(configured, provider, monkeypatch, change):
    state = pending()
    if change in ("expired", "future"):
        record = oauth._read("pending")
        record["created"] += -601 if change == "expired" else 60
        oauth._write("pending", record)
    if change == "registration":
        monkeypatch.setattr(settings, "meraki_client_secret", "different-secret")
    with pytest.raises(meraki.MerakiError):
        oauth.finish(7, "browser-session", dict(state=state, error="provider-secret") if change == "denied" else dict(state=state, code="synthetic-code"))
    assert not provider and not oauth._read("pending")


def test_refresh_saved_before_read_and_reused(configured, provider):
    connected(-1)
    assert oauth.access() == "synthetic-next-access"
    assert oauth._read("tokens")["refresh_token"] == "synthetic-next-refresh"
    assert oauth.access() == "synthetic-next-access" and len(provider) == 1
    assert parse_qs(provider[0].content.decode()) == dict(grant_type=["refresh_token"], refresh_token=["synthetic-refresh"])


@pytest.mark.parametrize("failure", ["timeout", "redirect", "rejected", "json", "scope", "unchanged-refresh", "expiry"])
def test_uncertain_refresh_never_retries_old_token(configured, monkeypatch, failure):
    connected(-1)
    real_client, calls = httpx.Client, []
    def handler(request):
        calls.append(request)
        assert oauth._read("tokens")["refreshing"] is True
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-secret")
        if failure in ("redirect", "rejected"):
            return httpx.Response(302 if failure == "redirect" else 401, text="synthetic-secret", headers={"Location": "https://example.com"})
        if failure == "json":
            return httpx.Response(200, text="synthetic-secret")
        token = dict(access_token="synthetic-new", refresh_token="synthetic-rotated", expires_in=3600, token_type="Bearer")
        if failure == "scope": token["scope"] = oauth.SCOPES + " dashboard:general:config:write"
        if failure == "unchanged-refresh": token["refresh_token"] = "synthetic-refresh"
        if failure == "expiry": token["expires_in"] = True
        return httpx.Response(200, json=token)
    monkeypatch.setattr(oauth.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    for _ in range(2):
        with pytest.raises(meraki.MerakiError) as exc:
            oauth.access()
        assert "synthetic" not in str(exc.value)
    assert len(calls) == 1 and not oauth.status()["connected"]


def test_crash_marker_and_failed_save_prevent_duplicate_refresh(configured, provider, monkeypatch):
    connected(-1)
    write = oauth._write
    def fail_after_rotation(name, data):
        if data.get("access_token") == "synthetic-next-access":
            raise meraki.MerakiError("Cannot persist credentials")
        write(name, data)
    monkeypatch.setattr(oauth, "_write", fail_after_rotation)
    with pytest.raises(meraki.MerakiError): oauth.access()
    assert oauth._read("tokens")["refreshing"]
    with pytest.raises(meraki.MerakiError): oauth.access()
    assert len(provider) == 1


def test_registration_change_requires_reconnect(configured, provider, monkeypatch):
    connected()
    monkeypatch.setattr(settings, "meraki_client_id", "different-client")
    assert not oauth.status()["connected"]
    with pytest.raises(meraki.MerakiError): oauth.access()
    assert not provider


def test_disconnect_forgets_pending_and_tokens_and_revokes(configured, provider):
    connected(); pending()
    assert oauth.disconnect() is True
    assert not oauth._read("tokens") and not oauth._read("pending") and not oauth.status()["connected"]
    assert provider[0].url.path == "/oauth/revoke"
    assert parse_qs(provider[0].content.decode())["token_type_hint"] == ["refresh_token"]


def test_disconnect_keeps_local_removal_when_remote_fails(configured, monkeypatch):
    connected()
    real_client = httpx.Client
    monkeypatch.setattr(oauth.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="synthetic-secret")), **kw))
    assert oauth.disconnect() is False
    assert not oauth._read("tokens")
    oauth._path("tokens").write_text("invalid", encoding="utf-8")
    assert oauth.disconnect() is False and not oauth._read("tokens")


def test_bearer_reads_never_include_api_key(configured):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-access"
        assert "x-cisco-meraki-api-key" not in request.headers
        return httpx.Response(200, json={"id": "N_123", "name": "Example office"})
    client = meraki.Client("synthetic-access", httpx.Client(transport=httpx.MockTransport(handler)), bearer=True)
    client.network("N_123")
    assert len(calls) == 1 and calls[0].method == "GET"


def test_query_redaction_preserves_code_only_in_private_state():
    scope = dict(type="http", path=oauth.CALLBACK, query_string=b"state=example-state&code=synthetic-code")
    messages = []
    async def app(scope, receive, send):
        assert scope["query_string"] == b""
        assert scope["state"]["meraki_callback"] == dict(state="example-state", code="synthetic-code")
        await send(dict(type="http.response.start", status=303, headers=[]))
    async def send(message): messages.append(message)
    asyncio.run(oauth.RedactCallbackQuery(app)(scope, None, send))
    assert dict(messages[0]["headers"])[b"referrer-policy"] == b"no-referrer"
    assert b"synthetic" not in scope["query_string"]


def test_api_requires_admin_real_session_and_csrf(client, admin, agent, db, configured, provider):
    for path, method in [("/api/meraki/oauth/connect", "post"), ("/api/meraki/oauth/connection", "delete")]:
        assert getattr(client, method)(path, headers=as_user(agent)).status_code == 403
    assert client.post("/api/meraki/oauth/connect", headers=as_user(admin)).status_code == 409
    response = Response()
    auth.start_session(db, admin, response, sla.utcnow()); db.commit()
    cookie = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    client.cookies.set(auth.COOKIE, cookie)
    assert client.post("/api/meraki/oauth/connect").status_code == 403
    result = client.post("/api/meraki/oauth/connect", headers={auth.CSRF_HEADER: "1"})
    assert result.status_code == 200
    state = parse_qs(urlsplit(result.json()["url"]).query)["state"][0]
    callback = client.get(oauth.CALLBACK, params=dict(state=state, code="synthetic-code"), follow_redirects=False)
    assert callback.status_code == 303 and callback.headers["location"].endswith("meraki_oauth=connected")
    assert callback.headers["cache-control"] == "no-store" and callback.headers["referrer-policy"] == "no-referrer"
    status = client.get("/api/meraki/status").json()
    assert status["oauth"]["connected"] and status["configured"]
    assert "synthetic" not in json.dumps(status)
    replay = client.get(oauth.CALLBACK, params=dict(state=state, code="synthetic-code"), follow_redirects=False)
    assert replay.headers["location"].endswith("meraki_oauth=failed")


def test_callback_without_session_clears_url_and_keeps_state(client, configured):
    state = pending()
    result = client.get(oauth.CALLBACK, params=dict(state=state, code="synthetic-code"), follow_redirects=False)
    assert result.headers["location"].endswith("meraki_oauth=failed")
    assert oauth._read("pending")


def test_refresh_is_serialized_between_independent_db_sessions(db, session_factory, configured, provider):
    connected(-1)
    gate = threading.Barrier(2)
    def read():
        gate.wait(timeout=5)
        with session_factory() as session:
            oauth.lock(session)
            return oauth.access()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: read(), range(2)))
    assert results == ["synthetic-next-access", "synthetic-next-access"] and len(provider) == 1


def test_oauth_settings_are_importable_and_secrets_write_only(client, admin, agent):
    values = dict(meraki_auth_mode="oauth", meraki_client_id="example-client", meraki_client_secret="synthetic-secret", meraki_network_ids=["N_123"])
    assert client.put("/api/settings/meraki", json={"values": values}, headers=as_user(agent)).status_code == 403
    saved = client.put("/api/settings/meraki", json={"values": values}, headers=as_user(admin))
    assert saved.status_code == 200 and "synthetic-secret" not in saved.text
    assert "synthetic-secret" not in client.get("/api/settings/meraki", headers=as_user(admin)).text
    assert settings.meraki_client_secret == "synthetic-secret"


def test_worker_client_uses_refreshed_bearer_and_preserves_rotation_after_api_error(db, session_factory, configured, provider, monkeypatch):
    from app import db as database
    connected(-1)
    monkeypatch.setattr(database, "get_session_factory", lambda: session_factory)
    client = meraki.from_settings()
    assert client.bearer and client.key == "synthetic-next-access"
    assert oauth._read("tokens")["refresh_token"] == "synthetic-next-refresh"
    # A later API refusal must not roll back the already saved rotated token.
    monkeypatch.setattr(client.http, "get", lambda *args, **kwargs: httpx.Response(403))
    with pytest.raises(meraki.MerakiError): client.network("N_123")
    assert oauth._read("tokens")["refresh_token"] == "synthetic-next-refresh"
    client.close()


def test_uncertain_refresh_requires_dashboard_revocation_even_after_revoke_200(configured, provider):
    connected()
    oauth._write("tokens", {**oauth._read("tokens"), "refreshing": True})
    assert oauth.disconnect() is False and not oauth._read("tokens")
