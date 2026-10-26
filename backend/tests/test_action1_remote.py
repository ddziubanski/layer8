# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic Action1 transport; PostgreSQL tests cover authorization and durable intent."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import action1, action1_remote as remote, permissions, sla
from app.config import settings
from app.models import AppSetting, Comment, Device, DevicePatch, RemoteSession, Ticket, TicketEvent, TicketSource
from tests.conftest import as_user

ORG = "00000000-0000-0000-0000-000000000001"
VIEWER = "https://us.remote.app.action1.com/remote/?id=synthetic-sensitive-link"


class Provider:
    def __init__(self):
        self.posts = []
        self.reads = []
        self.failure = None
        self.connected = "yes"
        self.url = VIEWER
        self.endpoint = {"id": "endpoint-1", "name": "PC-0042", "status": "Connected"}

    def handler(self, request):
        path = request.url.path.removeprefix("/api/3.0")
        if path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        if request.method == "POST":
            self.posts.append(json.loads(request.content))
            if self.failure == "timeout":
                raise httpx.ReadTimeout("SENSITIVE PROVIDER DATA", request=request)
            if self.failure == "malformed": return httpx.Response(200, json=[])
            if isinstance(self.failure, int): return httpx.Response(self.failure, text="SENSITIVE PROVIDER DATA")
            return httpx.Response(200, json={"id": "1"})
        self.reads.append(path)
        if path == f"/endpoints/managed/{ORG}/endpoint-1": return httpx.Response(200, json=self.endpoint)
        if path == f"/endpoints/managed/{ORG}/endpoint-1/remote-sessions/1":
            return httpx.Response(200, json={"id": "1", "connected": self.connected, "remote_session": self.url})
        return httpx.Response(404)


@pytest.fixture
def provider(monkeypatch):
    p = Provider()
    for name, value in {"action1_enabled": True, "action1_remote_enabled": True,
                        "action1_remote_client_id": "remote-client", "action1_remote_client_secret": "synthetic-secret",
                        "action1_remote_org_ids": [ORG], "action1_region": "NorthAmerica"}.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(remote, "client", lambda: remote.Client(action1.HOSTS["NorthAmerica"], "remote-client", "synthetic-secret",
                       httpx.Client(transport=httpx.MockTransport(p.handler))))
    return p


@pytest.fixture
def device(db):
    d = Device(name="PC-0042", dns_hostname="pc-0042.corp.example", os="Windows 11")
    db.add(d); db.flush()
    db.add(DevicePatch(device_id=d.id, action1_id="endpoint-1", organization_id=ORG, name=d.name))
    db.commit()
    return d


@pytest.fixture
def ticket(db, device, requester, service_desk):
    row = Ticket(subject="Remote help", requester_id=requester.id, queue_id=service_desk.id,
                 computer_id=device.id, source=TicketSource.portal)
    db.add(row); db.commit()
    return row


def start(client, user, ticket, device):
    return client.post(f"/api/tickets/{ticket.id}/action1-remote", headers=as_user(user), json={"device_id": device.id})


def poll(client, user, ticket, row):
    return client.get(f"/api/tickets/{ticket.id}/action1-remote/{row['session_id']}", headers=as_user(user))


@pytest.mark.parametrize("url", ["http://remote.action1.com/remote?id=x", "https://remote.action1.com.evil.example/remote?id=x",
    "https://evil.example/remote?id=x", "https://user@remote.action1.com/remote?id=x", "https://remote.action1.com:8443/remote?id=x",
    "https://remote.action1.com/other?id=x", "https://remote.action1.com/remote?id=x#x", "javascript:alert(1)",
    "https://remote.action1.com/remote", "https://remote.action1.com/remote?id=", "https://remote.action1.com/remote?id=x\n", None])
def test_untrusted_viewer_urls_are_rejected_without_echoing(url):
    with pytest.raises(remote.RemoteError) as error: remote.launch_url(url)
    assert "unsupported" in str(error.value)
    assert "evil.example" not in str(error.value)


@pytest.mark.parametrize("host", ["remote.action1.com", "remote.app.action1.com", "us.remote.app.action1.com", "eu.remote.app.action1.com"])
def test_official_viewer_hosts(host):
    value = f"https://{host}/remote/?id=synthetic"
    assert remote.launch_url(value) == value


@pytest.mark.parametrize("failure,uncertain", [(401, False), (403, False), (429, False), (500, True), ("timeout", True), ("malformed", True)])
def test_external_posts_are_never_retried_or_echo_provider_data(failure, uncertain):
    p = Provider(); p.failure = failure
    with remote.Client(action1.HOSTS["NorthAmerica"], "id", "secret", httpx.Client(transport=httpx.MockTransport(p.handler))) as api:
        with pytest.raises(remote.RemoteError) as error: api.start(ORG, "endpoint-1")
    assert error.value.uncertain is uncertain
    assert "SENSITIVE" not in str(error.value)
    assert p.posts == [{"connection_type": "assistance"}]


def test_permission_and_queue_boundaries(client, db, admin, agent, requester, ticket, device, provider):
    assert start(client, requester, ticket, device).status_code == 403
    assert start(client, agent, ticket, device).status_code == 403
    permissions.save(db, {"action1_remote": {"who": "agents"}})
    ticket.queue.restricted = True; db.commit()
    assert start(client, agent, ticket, device).status_code == 404
    assert not provider.posts and not provider.reads
    assert start(client, admin, ticket, device).status_code == 200


@pytest.mark.parametrize("change", ["disabled", "global_off", "no_orgs", "foreign_org", "unlinked", "inactive", "renamed", "offline"])
def test_configuration_and_device_changes_prevent_external_write(change, client, db, admin, ticket, device, provider, monkeypatch):
    if change == "disabled": monkeypatch.setattr(settings, "action1_remote_enabled", False)
    if change == "global_off": monkeypatch.setattr(settings, "action1_enabled", False)
    if change == "no_orgs": monkeypatch.setattr(settings, "action1_remote_org_ids", [])
    if change == "foreign_org": monkeypatch.setattr(settings, "action1_remote_org_ids", ["other"])
    if change == "unlinked": ticket.computer_id = None
    if change == "inactive": device.is_active = False
    if change == "renamed": provider.endpoint["name"] = "OTHER-PC"
    if change == "offline": provider.endpoint["status"] = "Disconnected"
    db.commit()
    response = start(client, admin, ticket, device)
    assert response.status_code in (404, 409), response.text
    assert not provider.posts


def test_duplicate_click_private_handoff_and_accurate_tracking(client, db, admin, agent, ticket, device, provider):
    response = start(client, admin, ticket, device)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["status"] == "pending" and row["launch_url"] is None
    assert start(client, admin, ticket, device).json()["session_id"] == row["session_id"]
    assert len(provider.posts) == 1
    permissions.save(db, {"action1_remote": {"who": "agents"}}); db.commit()
    assert poll(client, agent, ticket, row).status_code == 404
    ready = poll(client, admin, ticket, row)
    assert ready.status_code == 200, ready.text
    assert ready.json()["launch_url"] == VIEWER
    assert ready.headers["cache-control"] == "private, no-store"
    assert ready.headers["referrer-policy"] == "no-referrer"
    assert poll(client, admin, ticket, row).json()["launch_url"] is None  # throttled
    db.expire_all()
    stored = db.get(AppSetting, f"action1_remote:{row['session_id']}").value
    notes = db.scalars(select(Comment).where(Comment.ticket_id == ticket.id)).all()
    events = db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id)).all()
    content = json.dumps(stored) + " ".join(n.body for n in notes) + str([e.data for e in events])
    assert "synthetic-sensitive-link" not in content and "synthetic-secret" not in content
    assert notes and all(n.is_internal and not n.emailed for n in notes)
    close = client.post(f"/api/remote-sessions/{row['session_id']}/close", headers=as_user(admin))
    assert close.status_code == 200
    db.expire_all()
    notes = db.scalars(select(Comment).where(Comment.ticket_id == ticket.id)).all()
    assert any("does not disconnect Action1" in n.body for n in notes)
    assert poll(client, admin, ticket, row).json()["status"] == "expired"


@pytest.mark.parametrize("failure", ["timeout", "malformed", 500])
def test_uncertain_intent_is_durable_and_never_resent(failure, client, db, admin, ticket, device, provider):
    provider.failure = failure
    first = start(client, admin, ticket, device)
    assert first.status_code == 200, first.text
    row = first.json()
    assert row["status"] == "unknown"
    assert start(client, admin, ticket, device).json()["session_id"] == row["session_id"]
    assert poll(client, admin, ticket, row).json()["status"] == "unknown"
    assert len(provider.posts) == 1


@pytest.mark.parametrize("change", ["config", "mapping", "unlinked", "expired"])
def test_changed_context_cannot_retrieve_a_viewer(change, client, db, admin, ticket, device, provider, monkeypatch):
    row = start(client, admin, ticket, device).json()
    if change == "config": monkeypatch.setattr(settings, "action1_remote_client_secret", "rotated")
    if change == "mapping": db.scalar(select(DevicePatch).where(DevicePatch.device_id == device.id)).action1_id = "endpoint-2"
    if change == "unlinked": ticket.computer_id = None
    if change == "expired": db.get(RemoteSession, row["session_id"]).started_at = sla.utcnow() - timedelta(minutes=6)
    db.commit()
    response = poll(client, admin, ticket, row)
    assert response.status_code in (200, 404, 502)
    assert VIEWER not in response.text
    assert len(provider.reads) == 1  # validation read before the initial POST only


def test_concurrent_clicks_request_one_provider_session(client, admin, ticket, device, provider):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: start(client, admin, ticket, device), range(2)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert len({r.json()["session_id"] for r in responses}) == 1
    assert provider.posts == [{"connection_type": "assistance"}]


def test_remote_settings_secret_is_write_only_and_feature_switch_takes_effect(client, admin, agent):
    path = "/api/settings/action1"
    assert client.put(path, headers=as_user(agent), json={"values": {"action1_remote_enabled": True}}).status_code == 403
    saved = client.put(path, headers=as_user(admin), json={"values": {
        "action1_remote_enabled": True, "action1_remote_client_id": "synthetic-client",
        "action1_remote_client_secret": "synthetic-private-secret", "action1_remote_org_ids": [ORG]}})
    assert saved.status_code == 200, saved.text
    response = client.get(path, headers=as_user(admin))
    assert "synthetic-private-secret" not in response.text
    assert settings.action1_remote_client_secret == "synthetic-private-secret"
    assert client.put("/api/settings/general", headers=as_user(admin), json={"values": {"action1_enabled": True}}).status_code == 200
    assert "action1_remote" in client.get("/api/auth/me", headers=as_user(admin)).json()["features"]
    assert client.put(path, headers=as_user(admin), json={"values": {"action1_remote_enabled": False}}).status_code == 200
    assert "action1_remote" not in client.get("/api/auth/me", headers=as_user(admin)).json()["features"]
