# SPDX-License-Identifier: AGPL-3.0-or-later
"""Service notices: posted by admins, shown to the people they're meant for while current."""

from datetime import timedelta

from app import sla
from tests.conftest import as_user


def post(client, admin, **body):
    r = client.post("/api/notices", json={"title": "Email is down", **body}, headers=as_user(admin))
    assert r.status_code == 201, r.text
    return r.json()


def titles(client, user):
    return [n["title"] for n in client.get("/api/notices/current", headers=as_user(user)).json()]


def test_current_notices_by_audience_and_severity(client, admin, agent, requester):
    post(client, admin, title="Printer queue slow", severity="info")
    post(client, admin, title="Email is down", severity="outage")
    post(client, admin, title="Maintenance tonight", severity="warning", audience="staff")
    post(client, admin, title="Use the portal, not phone", audience="requesters")
    assert titles(client, agent) == ["Email is down", "Maintenance tonight", "Printer queue slow"]  # outages first
    assert titles(client, requester) == ["Email is down", "Use the portal, not phone", "Printer queue slow"]


def test_scheduled_and_ended_notices(client, admin, agent):
    now = sla.utcnow()
    later = post(client, admin, title="Upgrade Saturday", starts_at=(now + timedelta(days=2)).isoformat())
    assert later["state"] == "upcoming" and titles(client, agent) == []
    n = post(client, admin, title="VPN flaky")
    assert titles(client, agent) == ["VPN flaky"]
    r = client.post(f"/api/notices/{n['id']}/end", headers=as_user(admin))
    assert r.json()["state"] == "ended" and titles(client, agent) == []
    # Still listed for admins, newest start first.
    assert [x["title"] for x in client.get("/api/notices", headers=as_user(admin)).json()] == ["Upgrade Saturday", "VPN flaky"]
    r = client.post("/api/notices", json={"title": "x", "starts_at": now.isoformat(), "ends_at": (now - timedelta(hours=1)).isoformat()}, headers=as_user(admin))
    assert r.status_code == 422


def test_only_admins_manage_notices(client, admin, agent):
    assert client.post("/api/notices", json={"title": "x"}, headers=as_user(agent)).status_code == 403
    n = post(client, admin)
    r = client.put(f"/api/notices/{n['id']}", json={"title": "Email is back, catching up", "severity": "info"}, headers=as_user(admin))
    assert r.json()["title"] == "Email is back, catching up"
    assert client.delete(f"/api/notices/{n['id']}", headers=as_user(admin)).status_code == 204
    assert titles(client, agent) == []
