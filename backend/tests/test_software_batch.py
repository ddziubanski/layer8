# SPDX-License-Identifier: AGPL-3.0-or-later
"""Action1 deployments to several machines or a group, bundles, and a set time. A mocked Action1
whose responses follow the shapes read from a live tenant (GET only); all data here is made up."""
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import action1, permissions, sla, software_batch as batch, software_deployment as deploy
from app.config import settings
from app.models import Comment, Device, DevicePatch, SoftwareBundle, SoftwareDeployment, Ticket, TicketSource
from tests.conftest import as_user

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-000000000002"
API = "https://app.action1.com/api/3.0"


def pkg(pid, name, platform="Windows"):
    return {"id": pid, "name": name, "vendor": "Example", "platform": platform,
            "versions": [{"id": f"{pid}-v1", "version": "1.0", "status": "Published", "additional_actions": []}]}


class Provider:
    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.failure = None
        self.status = "Running"
        self.endpoints = [{"id": f"endpoint-{i}", "name": f"PC-00{i}", "platform": "Windows", "status": "Connected"} for i in (1, 2, 3)]
        self.packages = {"Viewer": pkg("Viewer", "Example Viewer"), "Editor": pkg("Editor", "Example Editor"), "MacTool": pkg("MacTool", "Mac Tool", "Mac")}
        self.group_members = [{"id": "endpoint-1", "name": "PC-001", "platform": "Windows"}, {"id": "endpoint-2", "name": "PC-002", "platform": "Windows"}]
        self.recover = False

    def handler(self, request):
        path = request.url.path.removeprefix("/api/3.0")
        if path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        if request.method == "POST":
            self.posts.append((path, json.loads(request.content)))
            if self.failure == "timeout":
                raise httpx.ReadTimeout("synthetic", request=request)
            return httpx.Response(200, json={"id": "schedule-1" if "/schedules/" in path else "instance-1"})
        assert request.method == "GET"
        page = lambda items: httpx.Response(200, json={"items": items, "next_page": ""})  # noqa: E731
        if path == "/organizations":
            return page([{"id": ORG, "name": "Example Org"}, {"id": OTHER_ORG, "name": "Other Org"}])
        if path == f"/endpoints/managed/{ORG}":
            return page(self.endpoints)
        if path == f"/endpoints/groups/{ORG}":
            return page([{"id": "group-1", "name": "Front desk", "description": ""}])
        if path == f"/endpoints/groups/{ORG}/group-1":
            return httpx.Response(200, json={"id": "group-1", "name": "Front desk"})
        if path == f"/endpoints/groups/{ORG}/group-1/contents":
            return page(self.group_members)
        for pid, p in self.packages.items():
            if path == f"/software-repository/{ORG}/{pid}":
                return httpx.Response(200, json=p)
            if path == f"/software-repository/{ORG}/{pid}/versions/{pid}-v1":
                return httpx.Response(200, json=p["versions"][0])
        if path in (f"/automations/instances/{ORG}", f"/automations/schedules/{ORG}"):
            name = request.url.params.get("filter")
            kind = "schedules" if "schedules" in path else "instances"
            sent = [b for p, b in self.posts if kind in p]
            ran = kind == "instances" and (self.recover or any("schedules" in p for p, _ in self.posts) and self.status != "notyet")
            if kind == "instances" and any("schedules" in p for p, _ in self.posts):
                return page([{"id": "instance-9", "name": name}] if ran else [])
            return page([{"id": f"{'schedule' if kind == 'schedules' else 'instance'}-1", "name": name, "endpoints": sent[0]["endpoints"]}] if self.recover and sent else [])
        if path.startswith(f"/automations/instances/{ORG}/instance-") and not path.endswith("/endpoint_results"):
            iid = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": iid, "status": self.status, "endpoint_results": f"{API}/automations/instances/{ORG}/{iid}/endpoint_results"})
        if path.endswith("/endpoint_results"):
            return page([{"endpoint_name": "PC-001", "status": "Success", "time": "2026-11-06T10:00:00Z", "details": "Installed"},
                         {"endpoint_name": "PC-002", "status": "Pending", "time": "", "details": "Offline"}])
        return httpx.Response(404)


@pytest.fixture
def provider(monkeypatch):
    p = Provider()
    for name, value in {"action1_enabled": True, "action1_deploy_enabled": True, "action1_deploy_client_id": "deployment-client",
                        "action1_deploy_client_secret": "synthetic-secret", "action1_deploy_org_ids": [ORG], "action1_region": "NorthAmerica"}.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(deploy, "client", lambda: action1.Client(action1.HOSTS["NorthAmerica"], "deployment-client", "synthetic-secret",
                                                                 httpx.Client(transport=httpx.MockTransport(p.handler)), sleep=lambda s: None))
    return p


@pytest.fixture
def machines(db):
    out = []
    for i in (1, 2, 3):
        d = Device(name=f"PC-00{i}", dns_hostname=f"pc-00{i}.corp.example", os="Windows 11")
        db.add(d); db.flush()
        db.add(DevicePatch(device_id=d.id, action1_id=f"endpoint-{i}", organization_id=ORG, organization="Example Org", name=d.name))
        out.append(d)
    db.commit()
    return out


def preview(client, user, **body):
    return client.post("/api/action1/batches/preview", headers=as_user(user), json={"organization_id": ORG, **body})


def test_several_machines_two_packages_now(client, db, admin, machines, provider):
    r = preview(client, admin, device_ids=[m.id for m in machines[:2]], packages=[{"package_id": "Viewer", "version_id": "Viewer-v1"},
                                                                                     {"package_id": "Editor", "version_id": "Editor-v1"}])
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["state"] == "review" and [t["name"] for t in row["targets"]] == ["PC-001", "PC-002"] and len(row["packages"]) == 2
    assert provider.posts == []  # a review sends nothing
    s = client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(admin))
    assert s.status_code == 200, s.text
    assert s.json()["state"] == "pending"
    path, body = provider.posts[0]
    assert path == f"/automations/instances/{ORG}"
    assert body["endpoints"] == [{"id": "endpoint-1", "type": "Endpoint"}, {"id": "endpoint-2", "type": "Endpoint"}]
    action = body["actions"][0]
    assert action["template_id"] == "deploy_package" and action["params"]["packages"] == [{"Viewer": "1.0"}, {"Editor": "1.0"}]
    assert action["params"]["reboot_options"] == {"auto_reboot": "no"} and "settings" not in body
    # A repeated submit never sends again.
    assert client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(admin)).status_code == 200
    assert len(provider.posts) == 1
    # Per-machine results.
    provider.status = "Warning"
    done = client.post(f"/api/action1/batches/{row['id']}/refresh", headers=as_user(admin)).json()
    assert done["state"] == "warning" and {r["endpoint"]: r["status"] for r in done["results"]} == {"PC-001": "Success", "PC-002": "Pending"}
    assert "1 pending, 1 success" in done["message"]


def test_group_with_bundle_scheduled_once(client, db, admin, machines, provider):
    b = client.post("/api/action1/deploy/bundles", headers=as_user(admin), json={"organization_id": ORG, "name": "New starter",
        "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}, {"package_id": "Editor", "version_id": "Editor-v1"}]})
    assert b.status_code == 200, b.text
    at = (sla.utcnow() + timedelta(days=2)).replace(hour=7, minute=30, second=0, microsecond=0)
    r = preview(client, admin, group_id="group-1", bundle_id=b.json()["id"], run_at=at.isoformat(), retry_minutes="1440")
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["targets"] == [{"type": "EndpointGroup", "id": "group-1", "name": "Front desk", "members": 2}] and row["member_count"] == 2
    s = client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(admin)).json()
    assert s["state"] == "scheduled" and s["schedule_id"] == "schedule-1"
    path, body = provider.posts[0]
    assert path == f"/automations/schedules/{ORG}"
    assert body["endpoints"] == [{"id": "group-1", "type": "EndpointGroup"}] and body["retry_minutes"] == "1440"
    assert body["settings"] == f"ENABLED ONCE AT:07-30-00 DATE:{at:%Y-%m-%d}" and body["settings_timezone"] == "UTC"
    # Before it runs: still scheduled. After: its run is found by name and followed.
    provider.status = "notyet"
    db_row = db.get(SoftwareDeployment, row["id"]); db_row.checked_at = None; db.commit()
    assert client.post(f"/api/action1/batches/{row['id']}/refresh", headers=as_user(admin)).json()["state"] == "scheduled"
    provider.status = "Success"
    db.expire_all(); db_row = db.get(SoftwareDeployment, row["id"]); db_row.checked_at = None; db.commit()
    after = client.post(f"/api/action1/batches/{row['id']}/refresh", headers=as_user(admin)).json()
    assert after["state"] == "success" and after["instance_id"] == "instance-9"


@pytest.mark.parametrize("body, message", [
    ({"device_ids": [], "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}]}, "Choose machines or one Action1 group"),
    ({"group_id": "group-1", "device_ids": [1], "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}]}, "Choose machines or one Action1 group"),
    ({"group_id": "group-1", "packages": [{"package_id": "MacTool", "version_id": "MacTool-v1"}]}, "isn't a Windows package"),
    ({"group_id": "group-1", "packages": []}, "Choose 1 to 10 packages"),
    ({"group_id": "group-1", "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}], "retry_minutes": "5"}, "retry for 30 minutes"),
    ({"group_id": "group-1", "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}], "run_at": "2020-01-01T00:00:00+00:00"}, "at least 10 minutes ahead"),
    ({"group_id": "group-1", "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}], "run_at": "2030-01-01T00:00:00"}, "time zone"),
])
def test_bad_choices_are_refused_before_anything_is_sent(client, admin, machines, provider, body, message):
    r = preview(client, admin, **body)
    assert r.status_code == 409 and message in r.json()["detail"], r.text
    assert provider.posts == []


def test_scope_permissions_and_renamed_machines(client, db, admin, agent, machines, provider):
    assert preview(client, agent, group_id="group-1", packages=[{"package_id": "Viewer", "version_id": "Viewer-v1"}]).status_code == 403
    r = client.post("/api/action1/batches/preview", headers=as_user(admin), json={"organization_id": OTHER_ORG, "group_id": "group-1",
                                                                               "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}]})
    assert r.status_code == 404
    permissions.save(db, {"action1_deploy": {"who": "agents"}}); db.commit()
    # Agents may deploy, but only admins change bundles.
    assert client.post("/api/action1/deploy/bundles", headers=as_user(agent), json={"organization_id": ORG, "name": "X",
        "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}]}).status_code == 403
    row = preview(client, agent, device_ids=[machines[0].id], packages=[{"package_id": "Viewer", "version_id": "Viewer-v1"}]).json()
    # Someone else can't submit a review; a renamed machine needs a new review.
    assert client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(admin)).status_code == 403
    provider.endpoints[0]["name"] = "PC-RENAMED"
    r = client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(agent))
    assert r.status_code == 409 and "changed" in r.json()["detail"] and provider.posts == []
    # Single-device routes don't serve batch rows (and vice versa).
    assert client.get(f"/api/action1/deployments/{row['id']}", headers=as_user(admin)).status_code == 404


def test_overlapping_waiting_deployment_is_refused(client, admin, machines, provider):
    first = preview(client, admin, device_ids=[machines[0].id, machines[1].id], packages=[{"package_id": "Viewer", "version_id": "Viewer-v1"}]).json()
    assert client.post(f"/api/action1/batches/{first['id']}/submit", headers=as_user(admin)).json()["state"] == "pending"
    second = preview(client, admin, device_ids=[machines[1].id, machines[2].id], packages=[{"package_id": "Viewer", "version_id": "Viewer-v1"}]).json()
    r = client.post(f"/api/action1/batches/{second['id']}/submit", headers=as_user(admin))
    assert r.status_code == 409 and "already have one of these packages" in r.json()["detail"]
    other_pkg = preview(client, admin, device_ids=[machines[1].id], packages=[{"package_id": "Editor", "version_id": "Editor-v1"}]).json()
    assert client.post(f"/api/action1/batches/{other_pkg['id']}/submit", headers=as_user(admin)).status_code == 200
    assert len(provider.posts) == 2


def test_lost_answer_is_unknown_never_resent_and_recovered_by_name(client, db, admin, machines, provider):
    provider.failure = "timeout"
    row = preview(client, admin, device_ids=[machines[0].id], packages=[{"package_id": "Viewer", "version_id": "Viewer-v1"}]).json()
    s = client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(admin)).json()
    assert s["state"] == "unknown" and "Do not resend" in s["message"]
    assert client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(admin)).json()["state"] == "unknown"
    assert len(provider.posts) == 1
    provider.failure, provider.recover = None, True
    after = client.post(f"/api/action1/batches/{row['id']}/refresh", headers=as_user(admin)).json()
    assert after["instance_id"] == "instance-1" and after["state"] == "running" and len(provider.posts) == 1


def test_expired_review_and_ticket_notes(client, db, admin, machines, provider, requester, service_desk):
    t = Ticket(subject="Set up the front desk", requester_id=requester.id, queue_id=service_desk.id, source=TicketSource.portal)
    db.add(t); db.commit()
    row = preview(client, admin, group_id="group-1", packages=[{"package_id": "Viewer", "version_id": "Viewer-v1"}], ticket_id=t.id).json()
    old = db.get(SoftwareDeployment, row["id"]); old.created_at = sla.utcnow() - timedelta(minutes=11); db.commit()
    assert "expired" in client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(admin)).json()["detail"]
    row = preview(client, admin, group_id="group-1", packages=[{"package_id": "Viewer", "version_id": "Viewer-v1"}], ticket_id=t.id).json()
    client.post(f"/api/action1/batches/{row['id']}/submit", headers=as_user(admin))
    notes = db.scalars(select(Comment).where(Comment.ticket_id == t.id)).all()
    assert notes and all(n.is_internal for n in notes) and any("Action1 group Front desk (2 machines)" in n.body for n in notes)


def test_worker_poll_follows_batches(db, admin, machines, provider):
    row = SoftwareDeployment(actor_id=admin.id, snapshot={"kind": "batch", "organization_id": ORG, "organization": "Example Org", "platform": "Windows",
        "targets": [{"type": "EndpointGroup", "id": "group-1", "name": "Front desk"}], "packages": [], "run_at": None, "retry_minutes": "30",
        "configuration_digest": deploy.configuration()}, state="pending", instance_id="instance-1")
    db.add(row); db.commit()
    provider.status = "Success"
    assert deploy.poll(db) == 1
    db.refresh(row)
    assert row.state == "success" and len(row.results) == 2


def test_bundle_rules(client, db, admin, provider):
    ok = {"organization_id": ORG, "name": "New starter", "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}]}
    assert client.post("/api/action1/deploy/bundles", headers=as_user(admin), json=ok).status_code == 200
    dup = client.post("/api/action1/deploy/bundles", headers=as_user(admin), json={**ok, "name": "new STARTER"})
    assert dup.status_code == 409
    mixed = client.post("/api/action1/deploy/bundles", headers=as_user(admin), json={**ok, "name": "Mixed",
        "packages": [{"package_id": "Viewer", "version_id": "Viewer-v1"}, {"package_id": "MacTool", "version_id": "MacTool-v1"}]})
    assert mixed.status_code == 409 and "one platform" in mixed.json()["detail"]
    listed = client.get("/api/action1/deploy/bundles", headers=as_user(admin), params={"organization_id": ORG}).json()
    assert [b["name"] for b in listed] == ["New starter"] and listed[0]["packages"][0]["version"] == "1.0"
    assert client.delete(f"/api/action1/deploy/bundles/{listed[0]['id']}", headers=as_user(admin)).status_code == 204
    assert db.scalar(select(SoftwareBundle)) is None


def test_lists_for_the_page(client, admin, machines, provider):
    orgs = client.get("/api/action1/deploy/organizations", headers=as_user(admin)).json()
    assert orgs == [{"id": ORG, "name": "Example Org"}]  # only allowed organizations
    assert [g["name"] for g in client.get(f"/api/action1/deploy/organizations/{ORG}/groups", headers=as_user(admin)).json()] == ["Front desk"]
    assert [m["name"] for m in client.get(f"/api/action1/deploy/organizations/{ORG}/machines", headers=as_user(admin)).json()] == ["PC-001", "PC-002", "PC-003"]


def test_results_link_must_stay_on_automations(provider):
    api = deploy.client()
    with pytest.raises(batch.BatchError):
        batch._results(api, {"endpoint_results": f"{API}/organizations/../oauth2/token"})
