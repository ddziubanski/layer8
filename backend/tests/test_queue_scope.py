"""Queue: scope (mine / my groups / all), bulk move and assign, technicians' colours."""

import pytest

from app.models import Group, Queue, Ticket, TicketEvent, TicketSource
from tests.conftest import as_user, make_user


@pytest.fixture
def setup(db, agent, requester):
    desk = Queue(name="Desk", is_default=True)
    net = Queue(name="Network")
    db.add_all([desk, net])
    db.flush()
    team = Group(name="Network team", members=[agent])
    db.add(team)
    db.flush()
    net.owner_groups.append(team)
    other = make_user(db, "dcole@corp.local", name="D. Cole", role=agent.role)

    def t(subject, queue, assignee=None):
        ticket = Ticket(subject=subject, source=TicketSource.portal, queue_id=queue.id, requester_id=requester.id, assignee_id=assignee.id if assignee else None)
        db.add(ticket)
        return ticket

    t("mine on desk", desk, agent)
    t("net unassigned", net)
    t("net other", net, other)
    t("desk unassigned", desk)
    db.commit()
    return {"desk": desk, "net": net, "other": other}


def subjects(client, user, **params):
    r = client.get("/api/tickets", params=params, headers=as_user(user))
    assert r.status_code == 200, r.text
    return sorted(t["subject"] for t in r.json()["items"])


def test_scopes_and_views(client, agent, setup):
    assert subjects(client, agent, scope="mine") == ["mine on desk"]
    assert subjects(client, agent, scope="groups") == ["net other", "net unassigned"]
    assert len(subjects(client, agent, scope="all")) == 4
    assert subjects(client, agent, scope="groups", view="unassigned") == ["net unassigned"]
    counts = client.get("/api/tickets/views", params={"scope": "groups"}, headers=as_user(agent)).json()
    assert counts["open"] == 2 and counts["unassigned"] == 1


def test_bulk_move_and_assign(client, db, agent, setup):
    ids = [t["id"] for t in client.get("/api/tickets", params={"scope": "all"}, headers=as_user(agent)).json()["items"]]
    r = client.post("/api/tickets/bulk/assign", json={"ticket_ids": ids, "queue_id": setup["net"].id, "note": "Network issue"}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert sorted(r.json()["done"]) == sorted(ids) and r.json()["failed"] == {}
    assert {t.queue_id for t in db.query(Ticket)} == {setup["net"].id}
    assert db.query(TicketEvent).filter_by(type="assigned").count() >= 2
    # The other technician isn't an owner of Network: reported per ticket, nothing fatal.
    r = client.post("/api/tickets/bulk/assign", json={"ticket_ids": ids[:2] + [999999], "assignee_id": setup["other"].id}, headers=as_user(agent))
    assert r.json()["done"] == [] and set(r.json()["failed"]) == {str(i) for i in ids[:2]} | {"999999"}


def test_colours(client, db, agent, requester, setup):
    r = client.put(
        "/api/auth/me/preferences",
        json={"ticket_color": "#2563EB", "color_rules": [{"field": "priority", "value": "critical", "color": "#dc2626"}, {"field": "assignee", "value": "unassigned", "color": "#9ca3af"}]},
        headers=as_user(agent),
    )
    assert r.status_code == 200, r.text
    me = client.get("/api/auth/me", headers=as_user(agent)).json()
    assert me["ticket_color"] == "#2563eb" and me["color_rules"][0] == {"field": "priority", "value": "critical", "color": "#dc2626"}
    # Everyone sees whose ticket it is by colour.
    mine = client.get("/api/tickets", params={"scope": "mine"}, headers=as_user(agent)).json()["items"][0]
    assert mine["assignee"]["ticket_color"] == "#2563eb"
    assert client.put("/api/auth/me/preferences", json={"ticket_color": "blue"}, headers=as_user(agent)).status_code == 422
    assert client.put("/api/auth/me/preferences", json={"color_rules": [{"field": "mood", "value": "x", "color": "#000000"}]}, headers=as_user(agent)).status_code == 422
