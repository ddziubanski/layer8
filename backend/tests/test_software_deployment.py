"""Mocked Action1 transport plus real PostgreSQL intent, permissions and audit tests."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app import action1, permissions, sla, software_deployment as deploy
from app.config import settings
from app.models import Comment, Device, DevicePatch, Queue, SoftwareDeployment, Ticket, TicketSource
from tests.conftest import as_user

ORG = "00000000-0000-0000-0000-000000000001"
PACKAGE = "Example_Viewer_builtin"


class Provider:
    def __init__(self):
        self.posts = []
        self.reads = []
        self.failure = None
        self.version = {"id": "version-1", "version": "1.2.3", "additional_actions": [], "EULA_accepted": "yes"}
        self.endpoint = {"id": "endpoint-1", "name": "PC-0042", "platform": "Windows", "status": "Connected"}
        self.package_platform = "Windows"
        self.status = "Pending"
        self.recover = False

    def handler(self, request):
        path = request.url.path.removeprefix("/api/3.0")
        if path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        if request.method == "POST":
            self.posts.append(json.loads(request.content))
            if self.failure == "timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            return httpx.Response(200, json={"id": "instance-1"})
        self.reads.append(str(request.url))
        if path == f"/endpoints/managed/{ORG}/endpoint-1":
            return httpx.Response(200, json=self.endpoint)
        pkg = {"id": PACKAGE, "name": "Example Viewer", "vendor": "Example", "platform": self.package_platform, "versions": [self.version]}
        if path == f"/software-repository/{ORG}":
            return httpx.Response(200, json={"items": [pkg], "next_page": ""})
        if path == f"/software-repository/{ORG}/{PACKAGE}":
            return httpx.Response(200, json=pkg)
        if path == f"/software-repository/{ORG}/{PACKAGE}/versions/version-1":
            return httpx.Response(200, json=self.version)
        if path == f"/installed-software/{ORG}/data/endpoint-1":
            return httpx.Response(200, json={"items": [{"fields": {"Name": "Example Viewer", "Version": "1.0.0"}}]})
        if path == f"/automations/instances/{ORG}/instance-1":
            return httpx.Response(200, json={"id": "instance-1", "status": self.status, **(self.posts[0] if self.recover else {})})
        if path == f"/automations/instances/{ORG}":
            return httpx.Response(200, json={"items": [{"id": "instance-1", "name": self.posts[0]["name"]}] if self.recover else []})
        return httpx.Response(404)


@pytest.fixture
def provider(monkeypatch):
    p = Provider()
    for name, value in {"action1_enabled": True, "action1_deploy_enabled": True,
                        "action1_deploy_client_id": "deployment-client", "action1_deploy_client_secret": "synthetic-secret",
                        "action1_deploy_org_ids": [ORG], "action1_region": "NorthAmerica"}.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(deploy, "client", lambda: action1.Client(action1.HOSTS["NorthAmerica"], "deployment-client", "synthetic-secret",
                       httpx.Client(transport=httpx.MockTransport(p.handler))))
    return p


@pytest.fixture
def device(db):
    d = Device(name="PC-0042", dns_hostname="pc-0042.corp.example", os="Windows 11")
    db.add(d); db.flush()
    db.add(DevicePatch(device_id=d.id, action1_id="endpoint-1", organization_id=ORG, organization="Example Org", name=d.name))
    db.commit()
    return d


@pytest.fixture
def ticket(db, device, requester, service_desk):
    row = Ticket(subject="Install Example Viewer", requester_id=requester.id, queue_id=service_desk.id,
                 computer_id=device.id, source=TicketSource.portal)
    db.add(row); db.commit()
    return row


def review(client, admin, device, ticket=None):
    return client.post("/api/action1/deployments/preview", headers=as_user(admin), json={
        "device_id": device.id, "ticket_id": ticket.id if ticket else None,
        "package_id": PACKAGE, "version_id": "version-1"})


def test_catalog_filters_and_permission_boundary(client, db, admin, agent, requester, device, provider):
    path = f"/api/action1/devices/{device.id}/software?q=viewer"
    assert client.get(path, headers=as_user(agent)).status_code == 403
    assert client.get(path, headers=as_user(requester)).status_code == 403
    assert not provider.reads
    r = client.get(path, headers=as_user(admin))
    assert r.status_code == 200
    assert r.json()["items"][0]["versions"] == [{"id": "version-1", "version": "1.2.3"}]
    assert "filter=viewer" in provider.reads[-1] and "platform=Windows" in provider.reads[-1]
    permissions.save(db, {"action1_deploy": {"who": "agents"}}); db.commit()
    assert client.get(path, headers=as_user(agent)).status_code == 200


def test_preview_then_one_explicit_submission_and_private_ticket_audit(client, db, admin, device, ticket, provider):
    r = review(client, admin, device, ticket)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["state"] == "review" and not provider.posts
    assert not any(k.endswith("_digest") for k in row["details"])
    assert "synthetic-secret" not in r.text
    assert row["details"]["installed"][0]["version"] == "1.0.0"
    path = f"/api/action1/deployments/{row['id']}/submit"
    for _ in range(2):
        result = client.post(path, headers=as_user(admin))
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "pending"
    assert len(provider.posts) == 1
    payload = provider.posts[0]
    assert payload["endpoints"] == [{"id": "endpoint-1", "type": "Endpoint"}]
    assert payload["retry_minutes"] == "30"
    assert payload["actions"][0]["template_id"] == "deploy_package"
    assert payload["actions"][0]["params"]["packages"] == [{PACKAGE: "1.2.3"}]
    assert payload["actions"][0]["params"]["reboot_options"] == {"auto_reboot": "no"}
    notes = db.scalars(select(Comment).where(Comment.ticket_id == ticket.id)).all()
    assert len(notes) == 2 and all(n.is_internal and not n.emailed for n in notes)
    provider.status = "Success"
    done = client.post(f"/api/action1/deployments/{row['id']}/refresh", headers=as_user(admin))
    assert done.json()["state"] == "success"
    assert "already present" in done.json()["message"]


def test_unknown_acceptance_is_durable_and_never_retried(client, db, admin, device, provider):
    row = review(client, admin, device).json()
    provider.failure = "timeout"
    path = f"/api/action1/deployments/{row['id']}/submit"
    assert client.post(path, headers=as_user(admin)).json()["state"] == "unknown"
    assert client.post(path, headers=as_user(admin)).json()["state"] == "unknown"
    assert len(provider.posts) == 1
    other = review(client, admin, device).json()
    assert client.post(f"/api/action1/deployments/{other['id']}/submit", headers=as_user(admin)).status_code == 409
    assert len(provider.posts) == 1
    assert db.get(SoftwareDeployment, row["id"]).state == "unknown"


@pytest.mark.parametrize("change", ["org", "mapping", "package", "actions", "licence", "platform", "expiry", "config"])
def test_changes_and_unsupported_packages_fail_before_external_write(change, client, db, admin, device, provider, monkeypatch):
    row = review(client, admin, device).json()
    if change == "org": monkeypatch.setattr(settings, "action1_deploy_org_ids", [])
    if change == "mapping": provider.endpoint["name"] = "DIFFERENT-PC"
    if change == "package": provider.version["silent_install_switches"] = "/changed"
    if change == "actions": provider.version["additional_actions"] = [{"template_id": "run_script"}]
    if change == "licence": provider.version["EULA_accepted"] = "no"
    if change == "platform": provider.package_platform = "Mac"
    if change == "expiry":
        db.get(SoftwareDeployment, row["id"]).created_at = sla.utcnow() - timedelta(minutes=11); db.commit()
    if change == "config": monkeypatch.setattr(settings, "action1_deploy_client_secret", "rotated")
    r = client.post(f"/api/action1/deployments/{row['id']}/submit", headers=as_user(admin))
    assert r.status_code == 409, r.text
    assert not provider.posts


def test_queue_access_link_and_review_ownership(client, db, admin, agent, device, ticket, provider):
    permissions.save(db, {"action1_deploy": {"who": "agents"}})
    ticket.queue.restricted = True
    db.commit()
    assert review(client, agent, device, ticket).status_code == 404
    row = review(client, admin, device, ticket).json()
    assert client.get(f"/api/action1/deployments/{row['id']}", headers=as_user(agent)).status_code == 404
    assert client.post(f"/api/action1/deployments/{row['id']}/submit", headers=as_user(agent)).status_code == 404
    ticket.computer_id = None; db.commit()
    assert client.post(f"/api/action1/deployments/{row['id']}/submit", headers=as_user(admin)).status_code == 409
    assert not provider.posts


def test_upgrade_requires_fresh_org_mapping_and_feature_opt_in(client, db, admin, device, provider, monkeypatch):
    patch = db.scalar(select(DevicePatch).where(DevicePatch.device_id == device.id))
    patch.organization_id = ""; db.commit()
    assert review(client, admin, device).status_code == 409
    assert not provider.reads
    monkeypatch.setattr(settings, "action1_deploy_enabled", False)
    assert review(client, admin, device).status_code == 409


def test_worker_refresh_is_throttled_and_final_notes_are_not_duplicated(client, db, admin, device, ticket, provider):
    row = review(client, admin, device, ticket).json()
    client.post(f"/api/action1/deployments/{row['id']}/submit", headers=as_user(admin))
    provider.status = "Warning"
    deploy.poll(db)
    deploy.poll(db)
    db.expire_all()
    assert db.get(SoftwareDeployment, row["id"]).state == "warning"
    notes = db.scalars(select(Comment).where(Comment.ticket_id == ticket.id)).all()
    assert sum("warning" in n.body for n in notes) == 1
    assert sum("/automations/instances/" in r for r in provider.reads) == 1


def test_identifiers_cannot_change_provider_paths():
    for value in ("../elsewhere", "all", "..", "a/b", "a\\b", ""):
        with pytest.raises(action1.Action1Error): deploy.segment(value)


def test_recover_lost_post_response_by_reading_exact_instance(client, admin, device, provider):
    row = review(client, admin, device).json()
    provider.failure = "timeout"
    client.post(f"/api/action1/deployments/{row['id']}/submit", headers=as_user(admin))
    provider.recover, provider.status = True, "Running"
    r = client.post(f"/api/action1/deployments/{row['id']}/refresh", headers=as_user(admin))
    assert r.status_code == 200, r.text
    assert r.json()["instance_id"] == "instance-1" and r.json()["state"] == "running"
    assert len(provider.posts) == 1


def test_concurrent_duplicate_submit_sends_one_instance(client, admin, device, provider):
    row = review(client, admin, device).json()
    def send():
        return client.post(f"/api/action1/deployments/{row['id']}/submit", headers=as_user(admin))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert len(provider.posts) == 1
