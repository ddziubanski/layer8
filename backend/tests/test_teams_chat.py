import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app import configstore, teams_chat as teams
from app.config import settings
from app.models import Comment, Queue, Ticket, TicketEvent, TicketSource
from tests.conftest import as_user

TENANT = "00000000-0000-0000-0000-000000000001"
CLIENT = "00000000-0000-0000-0000-000000000002"
OID = "00000000-0000-0000-0000-000000000003"
PERSON = SimpleNamespace(id=7, email="agent@example.com", upn="agent@example.com")


@pytest.fixture
def configured(monkeypatch):
    for key, value in {"teams_chat_enabled": True, "teams_tenant_id": TENANT,
                       "teams_client_id": CLIENT, "teams_client_secret": "synthetic-secret",
                       "portal_url": "https://helpdesk.corp.example"}.items():
        monkeypatch.setattr(settings, key, value)


@pytest.fixture
def oauth(monkeypatch):
    @contextmanager
    def client(cache):
        class FakeClient:
            def initiate_auth_code_flow(self, scopes, **kwargs):
                assert scopes == ["User.Read", "Chat.ReadBasic", "ChatMessage.Send"]
                assert kwargs["response_mode"] == "fragment"
                assert kwargs["redirect_uri"] == "https://helpdesk.corp.example/teams/callback"
                return {"state": kwargs["state"], "auth_uri": "https://login.microsoftonline.com/authorize", "code_verifier": "synthetic-verifier"}

            def acquire_token_by_auth_code_flow(self, flow, response):
                assert response["state"] == flow["state"]
                return {"access_token": "synthetic-access", "id_token_claims": {"tid": TENANT, "oid": OID}}

            def get_accounts(self):
                return [{"username": PERSON.email}]

            def acquire_token_silent(self, scopes, account):
                cache._cache["test_rotation"] = "synthetic-rotated-refresh"
                cache.has_state_changed = True
                return {"access_token": "synthetic-refreshed-access"}
        yield FakeClient()
    monkeypatch.setattr(teams, "oauth_client", client)
    monkeypatch.setattr(teams, "graph", lambda *args, **kwargs: {"id": OID, "userPrincipalName": PERSON.email, "mail": PERSON.email})


def pending(user=PERSON, cookie="browser-session"):
    teams.begin(user, cookie)
    filename = settings.secrets_dir / f"teams-pending-{user.id}.json"
    return filename, json.loads(filename.read_text())


def test_configuration_is_opt_in_and_uses_fixed_tenant(configured, monkeypatch):
    assert teams.configuration()["tenant"] == TENANT
    monkeypatch.setattr(settings, "teams_chat_enabled", False)
    with pytest.raises(teams.ConnectionError):
        teams.configuration()
    assert teams.status(7) == {"configured": False, "connected": False, "account": None}


@pytest.mark.parametrize("portal", ["http://helpdesk.corp.example", "https://helpdesk.corp.example/path", "https://user:pass@helpdesk.corp.example", "https://helpdesk.corp.example#x"])
def test_redirect_configuration_rejects_unsafe_origins(configured, monkeypatch, portal):
    monkeypatch.setattr(settings, "portal_url", portal)
    with pytest.raises(teams.ConnectionError):
        teams.configuration()


def test_connection_binds_session_consumes_state_and_hides_tokens(configured, oauth):
    filename, flow = pending()
    assert flow["session"] != "browser-session"
    teams.finish(PERSON, "browser-session", {"state": flow["state"], "code": "synthetic-code"})
    assert not filename.exists()
    assert teams.status(PERSON.id) == {"configured": True, "connected": True, "account": PERSON.email}
    assert "synthetic-access" not in json.dumps(teams.status(PERSON.id))
    with pytest.raises(teams.ConnectionError):
        teams.finish(PERSON, "browser-session", {"state": flow["state"], "code": "synthetic-code"})
    token, _ = teams.access(PERSON.id)
    assert token == "synthetic-refreshed-access"
    assert "synthetic-rotated-refresh" in teams.path(PERSON.id).read_text()
    teams.disconnect(PERSON.id)
    assert not teams.status(PERSON.id)["connected"]


@pytest.mark.parametrize("change", ["session", "state", "expired", "registration"])
def test_invalid_oauth_returns_no_connection(configured, oauth, monkeypatch, change):
    filename, flow = pending()
    cookie, state = "browser-session", flow["state"]
    if change == "session": cookie = "another-session"
    if change == "state": state = "another-state"
    if change == "expired":
        flow["created"] -= 601
        filename.write_text(json.dumps(flow))
    if change == "registration": monkeypatch.setattr(settings, "teams_client_id", TENANT)
    with pytest.raises(teams.ConnectionError):
        teams.finish(PERSON, cookie, {"state": state, "code": "synthetic-code"})
    assert not teams.path(PERSON.id).exists()


def test_wrong_microsoft_account_is_never_saved(configured, oauth, monkeypatch):
    _, flow = pending()
    monkeypatch.setattr(teams, "graph", lambda *a, **k: {"id": OID, "userPrincipalName": "someone-else@example.com"})
    with pytest.raises(teams.ConnectionError):
        teams.finish(PERSON, "browser-session", {"state": flow["state"], "code": "synthetic-code"})
    assert not teams.path(PERSON.id).exists()


def test_chat_matching_excludes_groups_and_other_people():
    requester = SimpleNamespace(email="requester@example.com", upn=None)
    chat = {"chatType": "oneOnOne", "members": [{"userId": OID}, {"userId": "other", "email": requester.email}]}
    assert teams.matches(chat, requester, OID)
    assert not teams.matches({**chat, "chatType": "group"}, requester, OID)
    assert not teams.matches(chat, SimpleNamespace(email="private@example.com", upn=None), OID)
    assert not teams.matches(chat, requester, "wrong-sender")
    assert not teams.matches({**chat, "members": [{"userId": OID}, {"userId": "other", "email": None}]}, requester, OID)


def test_chat_pagination_rejects_external_urls(monkeypatch):
    monkeypatch.setattr(teams, "graph", lambda *a, **k: {"value": [], "@odata.nextLink": "https://evil.example/steal"})
    with pytest.raises(teams.ConnectionError):
        teams.chats("synthetic-access", PERSON, OID)


def test_graph_send_is_plain_text_no_redirects_and_errors_are_safe(monkeypatch):
    original, seen = httpx.Client, []
    def receive(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/synthetic-secret"})
    def client(**kwargs):
        assert kwargs == {"timeout": 15, "follow_redirects": False, "trust_env": False}
        return original(transport=httpx.MockTransport(receive), **kwargs)
    monkeypatch.setattr(httpx, "Client", client)
    with pytest.raises(teams.ConnectionError) as exc:
        teams.send("synthetic-access", "chat/id", "<b>plain text</b>")
    assert "synthetic" not in str(exc.value)
    assert len(seen) == 1
    assert seen[0].url.raw_path == b"/v1.0/chats/chat%2Fid/messages"
    assert json.loads(seen[0].content)["body"] == {"contentType": "text", "content": "<b>plain text</b>"}


def test_real_msal_flow_has_pkce_and_uses_fragment_callback(configured, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    original = httpx.Client
    def metadata(request):
        assert request.url.host == "login.microsoftonline.com"
        return httpx.Response(200, json={
            "authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
            "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
            "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        })
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(metadata), **kwargs))
    url = teams.begin(PERSON, "browser-session")
    params = parse_qs(urlsplit(url).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["response_mode"] == ["fragment"]
    assert params["response_type"] == ["code"]
    assert "offline_access" in params["scope"][0]
    saved = json.loads((settings.secrets_dir / "teams-pending-7.json").read_text())
    assert saved["flow"]["code_verifier"] not in url


@pytest.fixture
def ticket(db, requester, service_desk):
    row = Ticket(subject="Synthetic support request", requester_id=requester.id, queue_id=service_desk.id, source=TicketSource.portal)
    db.add(row); db.commit()
    return row


def mock_send(monkeypatch, ticket):
    monkeypatch.setattr(teams, "access", lambda _: ("synthetic-access", {"oid": OID, "account": "agent@example.com"}))
    monkeypatch.setattr(teams, "chats", lambda *a: [{"id": "one-to-one", "recipient": ticket.requester.email}])
    return {"delivery_id": str(uuid4()), "chat_id": "one-to-one", "body": "Try reconnecting your VPN."}


def test_ticket_reply_is_logged_and_duplicate_send_never_calls_graph_twice(client, db, agent, ticket, monkeypatch):
    body = mock_send(monkeypatch, ticket)
    calls = []
    monkeypatch.setattr(teams, "send", lambda *a: calls.append(a) or {"id": "message-1"})
    url = f"/api/tickets/{ticket.id}/teams/messages"
    first = client.post(url, json=body, headers=as_user(agent))
    assert first.status_code == 200, first.text
    duplicate = client.post(url, json=body, headers=as_user(agent))
    assert duplicate.json() == first.json() and len(calls) == 1
    comment = db.get(Comment, first.json()["comment_id"])
    assert comment.body == body["body"] and not comment.is_internal and not comment.emailed
    event = db.scalar(select(TicketEvent).where(TicketEvent.data["teams_status"].as_string() == "sent"))
    assert event.data["teams_message_id"] == "message-1"
    changed = client.post(url, json={**body, "body": "Different text"}, headers=as_user(agent))
    assert changed.status_code == 409 and len(calls) == 1


def test_uncertain_delivery_cannot_be_retried_automatically(client, db, agent, ticket, monkeypatch):
    body = mock_send(monkeypatch, ticket)
    calls = []
    def fail(*args):
        calls.append(args)
        raise teams.ConnectionError("Check Teams before retrying")
    monkeypatch.setattr(teams, "send", fail)
    url = f"/api/tickets/{ticket.id}/teams/messages"
    assert client.post(url, json=body, headers=as_user(agent)).status_code == 409
    assert client.post(url, json=body, headers=as_user(agent)).status_code == 409
    assert len(calls) == 1
    assert db.scalar(select(TicketEvent).where(TicketEvent.data["teams_status"].as_string() == "unknown"))
    assert not list(db.scalars(select(Comment)))


def test_requesters_and_hidden_queue_cannot_send_or_find_chats(client, db, agent, requester, ticket, monkeypatch):
    body = mock_send(monkeypatch, ticket)
    monkeypatch.setattr(teams, "send", lambda *a: pytest.fail("must not send"))
    assert client.post(f"/api/tickets/{ticket.id}/teams/messages", json=body, headers=as_user(requester)).status_code == 403
    private = Queue(name="Restricted", restricted=True)
    db.add(private); db.flush(); ticket.queue = private; db.commit()
    assert client.post(f"/api/tickets/{ticket.id}/teams/messages", json=body, headers=as_user(agent)).status_code == 404
    assert client.get(f"/api/tickets/{ticket.id}/teams/chats", headers=as_user(agent)).status_code == 404


def test_unmatched_chat_never_sends(client, agent, ticket, monkeypatch):
    body = mock_send(monkeypatch, ticket)
    monkeypatch.setattr(teams, "send", lambda *a: pytest.fail("must not send"))
    assert client.post(f"/api/tickets/{ticket.id}/teams/messages", json={**body, "chat_id": "group"}, headers=as_user(agent)).status_code == 409


def test_settings_are_admin_only_and_secret_is_write_only(client, admin, agent):
    body = {"values": {"teams_tenant_id": TENANT, "teams_client_id": CLIENT, "teams_client_secret": "synthetic-client-secret"}}
    assert client.put("/api/settings/teams", json=body, headers=as_user(agent)).status_code == 403
    saved = client.put("/api/settings/teams", json=body, headers=as_user(admin))
    assert saved.status_code == 200, saved.text
    assert "synthetic-client-secret" not in saved.text
    assert "synthetic-client-secret" not in client.get("/api/settings/teams", headers=as_user(admin)).text


def test_real_session_requires_csrf_and_callback_stays_bound(client, db, agent, configured, oauth, monkeypatch):
    from app import auth
    monkeypatch.setattr(settings, "dev_auth", False)
    auth.set_password(db, agent, "Synthetic-password-123"); db.commit()
    assert client.post("/api/auth/login", json={"email": agent.email, "password": "Synthetic-password-123"}).status_code == 200
    assert client.post("/api/teams/connect").status_code == 403
    assert client.post("/api/teams/connect", headers={auth.CSRF_HEADER: "1"}).status_code == 200
    flow = json.loads((settings.secrets_dir / f"teams-pending-{agent.id}.json").read_text())
    # A new login to the same Layer 8 user replaces the browser session binding.
    client.post("/api/auth/login", json={"email": agent.email, "password": "Synthetic-password-123"})
    result = client.post("/api/teams/callback", json={"state": flow["state"], "code": "synthetic-code"}, headers={auth.CSRF_HEADER: "1"})
    assert result.status_code == 409
    assert not teams.path(agent.id).exists()


def test_portal_only_session_cannot_connect(client, db, agent, configured):
    from fastapi import Response
    from app import auth, sla
    response = Response()
    auth.start_session(db, agent, response, sla.utcnow(), portal_only=True); db.commit()
    token = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    client.cookies.set(auth.COOKIE, token)
    assert client.get("/api/teams/connection").status_code == 403
    assert client.post("/api/teams/connect", headers={auth.CSRF_HEADER: "1"}).status_code == 403
