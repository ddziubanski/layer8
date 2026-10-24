# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-service portal: queue choice, categories, my tickets, resolve; and requester linking."""

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import OutboundEmail, Queue, RoutingRule
from tests.conftest import ANSWERS, as_user, make_user


@pytest.fixture
def queues(db, service_desk):
    printing = Queue(name="Printing", show_in_portal=True, portal_name="Printers & scanners", portal_description="Jams, toner, scan-to-email", portal_position=20, categories=["Jam", "Toner"])
    network = Queue(name="Network", show_in_portal=True, portal_position=10)
    hidden = Queue(name="Tier 2")
    retired = Queue(name="Old", show_in_portal=True, is_active=False)
    db.add_all([printing, network, hidden, retired])
    db.commit()
    return {"printing": printing, "network": network, "hidden": hidden}


def file(client, user, **body):
    body.setdefault("subject", "Help")
    body = {**ANSWERS, **body}
    return client.post("/api/portal/tickets", json=body, headers=as_user(user))


def agent_view(client, agent, tid):
    return client.get(f"/api/tickets/{tid}", headers=as_user(agent)).json()


def test_portal_lists_only_visible_active_queues_in_order(client, requester, queues):
    r = client.get("/api/portal/queues", headers=as_user(requester)).json()
    # Hours and Teams status are covered in test_hours.py.
    assert all("open_now" in q.pop("hours") and q.pop("technicians") is None for q in r)
    assert r == [
        {"id": queues["network"].id, "name": "Network", "description": None, "categories": []},
        {"id": queues["printing"].id, "name": "Printers & scanners", "description": "Jams, toner, scan-to-email", "categories": ["Jam", "Toner"]},
    ]


def test_requester_view_hides_internals(client, requester, queues):
    t = file(client, requester, queue_id=queues["printing"].id, category="Jam", subject="Front desk MFP").json()
    assert t["queue_name"] == "Printers & scanners"
    assert t["status"] == "new"
    for internal in ("matched_rule_name", "sla_state", "resolution_due_at", "requester", "priority"):
        assert internal not in t


def test_chosen_queue_is_kept_and_recorded(client, agent, requester, queues):
    t = file(client, requester, queue_id=queues["network"].id, subject="Wi-Fi drops").json()
    full = agent_view(client, agent, t["id"])
    assert full["queue"]["name"] == "Network"
    assert full["requested_queue_id"] == queues["network"].id
    assert full["source"] == "portal"


def test_only_rules_that_say_so_replace_the_chosen_queue(client, db, agent, requester, queues):
    db.add_all(
        [
            # Ordinary keyword rule: may set priority, may not move a chosen queue.
            RoutingRule(name="toner urgent", position=10, match_keywords=["toner"], set_queue_id=queues["hidden"].id, set_priority="high"),
            # Matches on the chosen queue: allowed to move it.
            RoutingRule(name="badge printers", position=20, match_keywords=["badge"], match_requested_queue_id=queues["printing"].id, set_queue_id=queues["hidden"].id),
            # Explicit override flag: allowed too.
            RoutingRule(name="outage", position=30, match_keywords=["outage"], override_requested_queue=True, set_queue_id=queues["hidden"].id),
        ]
    )
    db.commit()
    printing = queues["printing"].id

    kept = agent_view(client, agent, file(client, requester, queue_id=printing, category="Toner", subject="Out of toner").json()["id"])
    assert (kept["queue"]["name"], kept["priority"], kept["matched_rule_name"]) == ("Printing", "high", "toner urgent")

    moved = agent_view(client, agent, file(client, requester, queue_id=printing, category="Jam", subject="Badge printer jammed").json()["id"])
    assert (moved["queue"]["name"], moved["requested_queue_id"]) == ("Tier 2", printing)

    flagged = agent_view(client, agent, file(client, requester, queue_id=queues["network"].id, subject="Site outage").json()["id"])
    assert flagged["queue"]["name"] == "Tier 2"

    # Without a chosen queue, ordinary rules route as they always did.
    free = agent_view(client, agent, file(client, requester, subject="toner").json()["id"])
    assert free["queue"]["name"] == "Tier 2"


def test_queue_and_category_validation(client, requester, queues):
    assert file(client, requester, queue_id=queues["hidden"].id).status_code == 422
    # Free text when none of the options fit.
    assert file(client, requester, queue_id=queues["printing"].id, category="Label printer").status_code == 201
    # The queue has options, so the question must be answered.
    r = file(client, requester, queue_id=queues["printing"].id)
    assert r.status_code == 422 and "pick an option or describe it" in r.json()["detail"]
    assert file(client, requester, queue_id=queues["printing"].id, category="   ").status_code == 422
    # A queue without a list takes any category, or none.
    assert file(client, requester, queue_id=queues["network"].id, category="VPN").status_code == 201


def test_no_portal_queues_still_works(client, requester, service_desk):
    assert client.get("/api/portal/queues", headers=as_user(requester)).json() == []
    assert file(client, requester).status_code == 201


def test_my_tickets_resolve_and_reopen_by_reply(client, db, agent, requester, queues):
    other = make_user(db, "tnguyen@corp.local")
    a = file(client, requester, subject="A").json()["id"]
    b = file(client, requester, subject="B").json()["id"]
    file(client, other, subject="not mine")

    def ids(closed):
        return [t["id"] for t in client.get("/api/portal/tickets", params={"closed": closed}, headers=as_user(requester)).json()["items"]]

    assert set(ids(False)) == {a, b} and ids(True) == []
    r = client.post(f"/api/portal/tickets/{a}/resolve", headers=as_user(requester))
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert ids(False) == [b] and ids(True) == [a]
    assert client.post(f"/api/portal/tickets/{a}/resolve", headers=as_user(requester)).status_code == 409
    # Can't resolve someone else's.
    assert client.post(f"/api/portal/tickets/{a}/resolve", headers=as_user(other)).status_code == 404

    events = client.get(f"/api/tickets/{a}/events", headers=as_user(agent)).json()
    assert events[-1]["type"] == "status_changed" and events[-1]["actor"]["id"] == requester.id

    client.post(f"/api/tickets/{a}/comments", json={"body": "It's back"}, headers=as_user(requester))
    assert client.get(f"/api/portal/tickets/{a}", headers=as_user(requester)).json()["status"] == "open"


def test_agent_can_file_straight_into_a_queue(client, agent, queues):
    r = client.post("/api/tickets", json={"subject": "Patch panel", "queue_id": queues["hidden"].id}, headers=as_user(agent))
    assert r.json()["queue"]["name"] == "Tier 2"


def test_change_requester_to_existing_or_new_account(client, db, agent, requester, service_desk, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@corp.local")
    # Came in from a shared mailbox address.
    shared = make_user(db, "frontdesk@corp.local", name="Front desk")
    t = client.post("/api/tickets", json={"subject": "MFP", "requester_email": shared.email}, headers=as_user(agent)).json()

    r = client.post(f"/api/tickets/{t['id']}/requester", json={"email": "RAlvarez@corp.local"}, headers=as_user(agent))
    assert r.status_code == 200 and r.json()["requester"]["id"] == requester.id
    events = client.get(f"/api/tickets/{t['id']}/events", headers=as_user(agent)).json()
    assert events[-1]["type"] == "requester_changed"
    assert events[-1]["data"] == {"before": shared.id, "after": requester.id}

    # From now on replies go to the linked person.
    client.post(f"/api/tickets/{t['id']}/comments", json={"body": "On my way"}, headers=as_user(agent))
    last = db.scalars(select(OutboundEmail).order_by(OutboundEmail.id.desc())).first()
    assert last.to_address == requester.email

    new = client.post(f"/api/tickets/{t['id']}/requester", json={"email": "newstarter@corp.local", "display_name": "New Starter"}, headers=as_user(agent)).json()
    assert new["requester"]["display_name"] == "New Starter"


def test_change_requester_rules(client, db, agent, requester, service_desk):
    t = client.post("/api/tickets", json={"subject": "x"}, headers=as_user(agent)).json()
    leaver = make_user(db, "leaver@corp.local")
    leaver.is_active = False
    db.commit()
    assert client.post(f"/api/tickets/{t['id']}/requester", json={"email": "leaver@corp.local"}, headers=as_user(agent)).status_code == 409
    assert client.post(f"/api/tickets/{t['id']}/requester", json={"email": requester.email}, headers=as_user(requester)).status_code == 403


def test_queue_portal_settings_and_category_tidying(client, admin, service_desk):
    r = client.patch(
        f"/api/queues/{service_desk.id}",
        json={"show_in_portal": True, "portal_name": "IT help", "categories": [" Wi-Fi ", "", "VPN", "wi-fi"]},
        headers=as_user(admin),
    )
    assert r.status_code == 200
    q = r.json()
    assert (q["show_in_portal"], q["portal_name"], q["categories"]) == (True, "IT help", ["Wi-Fi", "VPN"])


def test_option_spelling_is_normalised(client, agent, requester, queues):
    t = file(client, requester, queue_id=queues["printing"].id, category="toner").json()
    assert agent_view(client, agent, t["id"])["category"] == "Toner"


def test_problem_options_send_to_specific_queues(client, db, admin, agent, requester, service_desk):
    network, accounts = Queue(name="Network"), Queue(name="Accounts")
    db.add_all([network, accounts])
    db.commit()
    it = client.post(
        "/api/queues",
        json={
            "name": "Information Technology",
            "show_in_portal": True,
            "categories": ["Wi-Fi or network", "Password or account", "Other IT"],
            "category_routes": {"wi-fi or network": network.id, "Password or account": accounts.id, "Not an option": network.id},
        },
        headers=as_user(admin),
    ).json()
    # Stored against the option's own spelling; routes for unknown options are dropped.
    assert it["category_routes"] == {"Wi-Fi or network": network.id, "Password or account": accounts.id}
    # Requesters see the options, not where they go.
    portal_it = [q for q in client.get("/api/portal/queues", headers=as_user(requester)).json() if q["id"] == it["id"]][0]
    assert "category_routes" not in portal_it

    wifi = agent_view(client, agent, file(client, requester, queue_id=it["id"], category="Wi-Fi or network").json()["id"])
    assert (wifi["queue"]["name"], wifi["requested_queue_id"], wifi["category"]) == ("Network", it["id"], "Wi-Fi or network")
    other = agent_view(client, agent, file(client, requester, queue_id=it["id"], category="Other IT").json()["id"])
    assert other["queue"]["name"] == "Information Technology"
    free = agent_view(client, agent, file(client, requester, queue_id=it["id"], category="Badge reader").json()["id"])
    assert free["queue"]["name"] == "Information Technology"

    # Renaming an option drops its route; routes to inactive or missing queues are refused.
    r = client.patch(f"/api/queues/{it['id']}", json={"categories": ["Wi-Fi", "Password or account"]}, headers=as_user(admin)).json()
    assert r["category_routes"] == {"Password or account": accounts.id}
    bad = client.patch(f"/api/queues/{it['id']}", json={"categories": ["Wi-Fi"], "category_routes": {"Wi-Fi": 99999}}, headers=as_user(admin))
    assert bad.status_code == 422
    selfish = client.patch(f"/api/queues/{it['id']}", json={"categories": ["Wi-Fi"], "category_routes": {"Wi-Fi": it["id"]}}, headers=as_user(admin))
    assert selfish.status_code == 422


def test_rules_matching_the_department_can_still_redirect(client, db, admin, agent, requester, service_desk):
    network, tier2 = Queue(name="Network"), Queue(name="Tier 2")
    db.add_all([network, tier2])
    db.commit()
    it = client.post("/api/queues", json={"name": "IT", "show_in_portal": True, "categories": ["Wi-Fi"], "category_routes": {"Wi-Fi": network.id}}, headers=as_user(admin)).json()
    db.add(RoutingRule(name="outage", match_keywords=["outage"], override_requested_queue=True, set_queue_id=tier2.id))
    db.commit()
    t = agent_view(client, agent, file(client, requester, queue_id=it["id"], category="Wi-Fi", subject="Site outage").json()["id"])
    assert t["queue"]["name"] == "Tier 2"


# Business impact and urgency


@pytest.mark.parametrize(
    "impact, urgency, priority",
    [("single", "low", "low"), ("single", "high", "medium"), ("many", "high", "high"), ("business", "high", "critical"), ("business", "low", "medium")],
)
def test_impact_and_urgency_set_priority(client, agent, requester, service_desk, impact, urgency, priority):
    t = client.post("/api/portal/tickets", json={"subject": "x", "impact": impact, "urgency": urgency}, headers=as_user(requester)).json()
    assert (t["impact"], t["urgency"]) == (impact, urgency)
    assert agent_view(client, agent, t["id"])["priority"] == priority


def test_portal_requires_both_answers(client, requester, service_desk):
    assert client.post("/api/portal/tickets", json={"subject": "x", "impact": "single"}, headers=as_user(requester)).status_code == 422
    assert client.post("/api/portal/tickets", json={"subject": "x", "urgency": "low"}, headers=as_user(requester)).status_code == 422


def test_admins_edit_the_matrix_and_missing_cells_use_the_default(client, db, admin, agent, requester, service_desk):
    from app.models import PriorityMatrixCell

    # The test database starts empty: every cell comes from the built-in default.
    cells = client.get("/api/priority-matrix", headers=as_user(agent)).json()
    assert len(cells) == 9 and {"impact": "business", "urgency": "high", "priority": "critical"} in cells
    assert client.put("/api/priority-matrix", json=[{"impact": "single", "urgency": "high", "priority": "high"}], headers=as_user(agent)).status_code == 403
    r = client.put("/api/priority-matrix", json=[{"impact": "single", "urgency": "high", "priority": "high"}], headers=as_user(admin))
    assert {"impact": "single", "urgency": "high", "priority": "high"} in r.json()
    assert db.scalar(select(func.count()).select_from(PriorityMatrixCell)) == 1
    t = client.post("/api/portal/tickets", json={"subject": "x", "impact": "single", "urgency": "high"}, headers=as_user(requester)).json()
    assert agent_view(client, agent, t["id"])["priority"] == "high"


def test_rules_can_still_change_a_matrix_priority(client, db, agent, requester, service_desk):
    db.add(RoutingRule(name="vip", match_requester="ralvarez@corp.local", set_priority="critical"))
    db.commit()
    t = client.post("/api/portal/tickets", json={"subject": "x", "impact": "single", "urgency": "low"}, headers=as_user(requester)).json()
    assert agent_view(client, agent, t["id"])["priority"] == "critical"


def test_agent_reassessment_reprioritises_and_explicit_priority_wins(client, agent, requester, service_desk, slas):
    t = client.post("/api/portal/tickets", json={"subject": "x", "impact": "single", "urgency": "low"}, headers=as_user(requester)).json()
    r = client.patch(f"/api/tickets/{t['id']}", json={"impact": "business"}, headers=as_user(agent)).json()
    assert (r["impact"], r["urgency"], r["priority"]) == ("business", "low", "medium")
    r = client.patch(f"/api/tickets/{t['id']}", json={"urgency": "high", "priority": "low"}, headers=as_user(agent)).json()
    assert (r["urgency"], r["priority"]) == ("high", "low")
    events = client.get(f"/api/tickets/{t['id']}/events", headers=as_user(agent)).json()
    assert any(e["type"] == "updated" and "impact" in e["data"]["fields"] for e in events)
    assert events[0]["data"]["impact"] == "single"


def test_agents_may_give_a_priority_or_an_assessment(client, agent, service_desk):
    a = client.post("/api/tickets", json={"subject": "x", "impact": "business", "urgency": "high"}, headers=as_user(agent)).json()
    assert a["priority"] == "critical"
    b = client.post("/api/tickets", json={"subject": "x", "priority": "low", "impact": "business", "urgency": "high"}, headers=as_user(agent)).json()
    assert (b["priority"], b["impact"]) == ("low", "business")
    c = client.post("/api/tickets", json={"subject": "x"}, headers=as_user(agent)).json()
    assert (c["priority"], c["impact"]) == ("medium", None)
