# SPDX-License-Identifier: AGPL-3.0-or-later
"""Time spent on tickets: logging, removing, and the Time spent report."""
from datetime import date, timedelta

from app import services
from app.models import TicketSource
from tests.conftest import as_user


def _ticket(db, requester):
    t = services.create_ticket(db, subject="Laptop rebuild", description="", category="Hardware", requester=requester,
                               source=TicketSource.portal, actor=requester)
    db.commit()
    return t


def test_log_list_and_remove_time(client, db, admin, agent, requester, service_desk):
    t = _ticket(db, requester)
    url = f"/api/tickets/{t.id}/time"
    assert client.post(url, json={"minutes": 0}, headers=as_user(agent)).status_code == 422
    assert client.post(url, json={"minutes": 30, "worked_on": (date.today() + timedelta(days=2)).isoformat()}, headers=as_user(agent)).status_code == 422
    note = client.post(f"/api/tickets/{t.id}/comments", json={"body": "Reimaged", "is_internal": True}, headers=as_user(agent)).json()
    mine = client.post(url, json={"minutes": 45, "note": "Reimage", "comment_id": note["id"]}, headers=as_user(agent)).json()
    theirs = client.post(url, json={"minutes": 15}, headers=as_user(admin)).json()
    listed = client.get(url, headers=as_user(agent)).json()
    assert listed["total_minutes"] == 60 and {e["user_name"] for e in listed["entries"]} == {agent.display_name, admin.display_name}
    assert client.delete(f"{url}/{theirs['id']}", headers=as_user(agent)).status_code == 403
    assert client.delete(f"{url}/{mine['id']}", headers=as_user(agent)).status_code == 204
    assert client.delete(f"{url}/{theirs['id']}", headers=as_user(admin)).status_code == 204
    assert client.get(url, headers=as_user(agent)).json() == {"total_minutes": 0, "entries": []}
    assert client.get(url, headers=as_user(requester)).status_code == 403


def test_time_spent_report(client, db, admin, agent, requester, service_desk):
    t = _ticket(db, requester)
    for who, minutes in ((agent, 90), (agent, 30), (admin, 60)):
        client.post(f"/api/tickets/{t.id}/time", json={"minutes": minutes}, headers=as_user(who))
    r = client.post("/api/reports/run", json={"dataset": "time", "group_by": ["who"], "measures": ["hours", "tickets"]}, headers=as_user(admin))
    assert r.status_code == 200, r.text
    rows = {row[0]: row[1:] for row in r.json()["rows"]}
    assert rows[agent.display_name] == [2.0, 1] and rows[admin.display_name] == [1.0, 1]
