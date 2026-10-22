"""The script library: definitions, versions and diffs, running queries, who may do what."""

from app.models import Device, Queue, Ticket, TicketSource
from tests.conftest import as_user

QUERY = {
    "name": "Stale devices",
    "description": "Not seen lately",
    "kind": "query",
    "language": "sql",
    "body": "SELECT id AS device_id, name FROM devices WHERE last_logon_at IS NULL OR last_logon_at < now() - make_interval(days => :days) ORDER BY name",
    "parameters": [{"name": "days", "label": "Days", "type": "int", "required": True, "default": 30}],
}


def create(client, admin, **over):
    r = client.post("/api/scripts", json={**QUERY, **over}, headers=as_user(admin))
    assert r.status_code == 201, r.text
    return r.json()


def test_versions_and_diff(client, admin):
    s = create(client, admin)
    assert s["current_version"] == 1 and s["parameters"][0]["default"] == 30
    # Only description changed: no new version.
    r = client.put(f"/api/scripts/{s['id']}", json={**QUERY, "description": "Old kit"}, headers=as_user(admin))
    assert r.json()["current_version"] == 1 and r.json()["description"] == "Old kit"
    body2 = QUERY["body"].replace("ORDER BY name", "ORDER BY last_logon_at NULLS FIRST")
    r = client.put(f"/api/scripts/{s['id']}", json={**QUERY, "body": body2, "note": "Oldest first"}, headers=as_user(admin))
    assert r.json()["current_version"] == 2
    versions = client.get(f"/api/scripts/{s['id']}/versions", headers=as_user(admin)).json()
    assert [(v["number"], v["note"], v["is_current"]) for v in versions] == [(2, "Oldest first", True), (1, "First version", False)]
    v2 = client.get(f"/api/scripts/{s['id']}/versions/2", headers=as_user(admin)).json()
    assert "-SELECT id AS device_id" in v2["diff"] and "+SELECT id AS device_id" in v2["diff"] and "version 1" in v2["diff"]
    r = client.post(f"/api/scripts/{s['id']}/versions/1/restore", headers=as_user(admin))
    assert r.json()["current_version"] == 3 and r.json()["body"] == QUERY["body"]


def test_definition_checks(client, admin):
    for over, why in [
        ({"language": "powershell"}, "A query script is written in sql"),
        ({"kind": "action", "language": "sql"}, "An action"),
        ({"parameters": [{"name": "Bad Name"}]}, "lowercase"),
        ({"parameters": [{"name": "start"}]}, "reserved"),
        ({"parameters": [{"name": "x", "type": "choice"}]}, "list the choices"),
        ({"parameters": [{"name": "x", "type": "int", "default": "many"}]}, "whole number"),
    ]:
        r = client.post("/api/scripts", json={**QUERY, **over}, headers=as_user(admin))
        assert r.status_code == 422 and why.split()[0] in r.json()["detail"], (over, r.text)
    create(client, admin)
    assert client.post("/api/scripts", json=QUERY, headers=as_user(admin)).status_code == 409


def test_run_query_with_parameters_and_access(client, db, admin, agent):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    db.add_all([Device(name="WS-OLD", last_logon_at=now - timedelta(days=60)), Device(name="WS-NEW", last_logon_at=now)])
    db.commit()
    s = create(client, admin)
    run = lambda who, **values: client.post(f"/api/scripts/{s['id']}/run", json={"values": values}, headers=as_user(who))  # noqa: E731
    r = run(admin)
    assert r.status_code == 200, r.text
    assert [row[1] for row in r.json()["rows"]] == ["WS-OLD"]
    assert [row[1] for row in run(admin, days=0).json()["rows"]] == ["WS-NEW", "WS-OLD"]
    assert "whole number" in run(admin, days="lots").json()["detail"]
    assert "Unknown parameter" in run(admin, weeks=2).json()["detail"]
    # Agents only when the script allows it; never edit.
    assert run(agent).status_code == 403
    assert client.get("/api/scripts", headers=as_user(agent)).json()[0]["can_run"] is False
    client.put(f"/api/scripts/{s['id']}", json={**QUERY, "agents_can_run": True}, headers=as_user(admin))
    assert run(agent).status_code == 200
    assert client.put(f"/api/scripts/{s['id']}", json=QUERY, headers=as_user(agent)).status_code == 403
    csv = client.post(f"/api/scripts/{s['id']}/run.csv", json={"values": {}}, headers=as_user(agent))
    assert csv.status_code == 200 and "WS-OLD" in csv.text
    # Still read-only.
    bad = create(client, admin, name="Sneaky", body="WITH x AS (DELETE FROM devices RETURNING id) SELECT * FROM x", parameters=[])
    assert client.post(f"/api/scripts/{bad['id']}/run", json={}, headers=as_user(admin)).status_code == 422
    assert db.query(Device).count() == 2


def test_examples_run(client, db, admin, requester):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    db.add(Ticket(subject="x", source=TicketSource.portal, queue_id=q.id, requester_id=requester.id))
    db.commit()
    added = client.post("/api/scripts/examples", headers=as_user(admin)).json()
    assert len(added) >= 5
    assert client.post("/api/scripts/examples", headers=as_user(admin)).json() == []
    for s in client.get("/api/scripts", headers=as_user(admin)).json():
        r = client.post(f"/api/scripts/{s['id']}/run", json={}, headers=as_user(admin))
        assert r.status_code == 200, (s["name"], r.text)
