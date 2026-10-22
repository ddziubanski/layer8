from datetime import timedelta
from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import update

from app.models import Queue, Ticket, TicketSource, UserRole
from app.sla import utcnow
from tests.conftest import ANSWERS, alembic_config, as_user, make_user

# An agent used only to read back the full (agent) view of tickets requesters file.
VIEWER = {"X-Dev-User": "viewer@corp.local"}


@pytest.fixture(autouse=True)
def viewer(db):
    make_user(db, "viewer@corp.local", UserRole.agent, "Viewer")


def create(client, user, **body):
    """File a ticket as `user`: through the portal for requesters, the agent API otherwise.
    Returns the agent view of the ticket."""
    body.setdefault("subject", "Cannot print to Front Desk MFP")
    if user.role != UserRole.requester:
        return client.post("/api/tickets", json=body, headers=as_user(user))
    r = client.post("/api/portal/tickets", json={**ANSWERS, **body}, headers=as_user(user))
    if r.status_code != 201:
        return r
    full = client.get(f"/api/tickets/{r.json()['id']}", headers=VIEWER).json()
    return SimpleNamespace(status_code=201, json=lambda: full, text=str(full))


def test_migrations_match_models(engine):
    # Fails if a model changed without a migration.
    command.check(alembic_config())


def test_portal_ticket_lands_in_default_queue_with_sla(client, requester, service_desk, slas):
    r = create(client, requester, description="Paper jam light is on")
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["id"] == 1000
    assert t["source"] == "portal"
    assert t["status"] == "new"
    assert t["priority"] == "medium"
    assert t["queue"]["name"] == "Service desk"
    assert t["requester"]["email"] == requester.email
    assert t["matched_rule_name"] is None
    assert t["resolution_due_at"] is not None
    assert t["sla_state"] == "ok"


def test_email_and_portal_tickets_route_identically(client, db, admin, agent, requester, service_desk, slas):
    network = Queue(name="Network")
    db.add(network)
    db.commit()
    rule = client.post(
        "/api/routing-rules",
        json={"name": 'subject contains "switch"', "match_keywords": ["switch"], "set_queue_id": network.id, "set_priority": "high"},
        headers=as_user(admin),
    ).json()

    portal = create(client, requester, subject="Switch port flapping").json()
    # Agent filing on the requester's behalf, as the mailbox worker will.
    email = create(client, agent, subject="Switch port flapping", requester_email=requester.email, source="email").json()

    for t in (portal, email):
        assert t["queue"]["name"] == "Network"
        assert t["priority"] == "high"
        assert t["matched_rule_id"] == rule["id"]
        assert t["matched_rule_name"] == 'subject contains "switch"'
    assert {portal["source"], email["source"]} == {"portal", "email"}


def test_rule_order_and_requester_domain(client, db, admin, requester, service_desk):
    identity = Queue(name="Identity")
    db.add(identity)
    db.commit()
    hdr = as_user(admin)
    client.post("/api/routing-rules", json={"name": "late", "position": 50, "match_keywords": ["password"], "set_priority": "low"}, headers=hdr)
    client.post(
        "/api/routing-rules",
        json={"name": "corp passwords", "position": 10, "match_keywords": ["password"], "match_requester": "@corp.local", "set_queue_id": identity.id},
        headers=hdr,
    )
    t = create(client, requester, subject="Password reset request").json()
    assert t["matched_rule_name"] == "corp passwords"
    assert t["queue"]["name"] == "Identity"
    assert t["priority"] == "medium"  # the winning rule only set the queue


def test_rule_must_have_an_action(client, admin):
    r = client.post("/api/routing-rules", json={"name": "noop", "match_keywords": ["x"]}, headers=as_user(admin))
    assert r.status_code == 422


def test_deleting_a_rule_keeps_the_explanation_on_tickets(client, db, admin, requester, service_desk):
    rule = client.post("/api/routing-rules", json={"name": "printers", "match_keywords": ["print"], "set_priority": "low"}, headers=as_user(admin)).json()
    t = create(client, requester).json()
    assert client.delete(f"/api/routing-rules/{rule['id']}", headers=as_user(admin)).status_code == 204
    after = client.get(f"/api/tickets/{t['id']}", headers=VIEWER).json()
    assert after["matched_rule_id"] is None
    assert after["matched_rule_name"] == "printers"


def test_no_default_queue_is_a_clear_error(client, requester):
    r = create(client, requester)
    assert r.status_code == 409
    assert "default queue" in r.json()["detail"]


def test_requester_limits(client, db, agent, requester, service_desk):
    other = make_user(db, "tnguyen@corp.local")
    # The agent API is for agents; requesters use the portal.
    assert client.post("/api/tickets", json={"subject": "x"}, headers=as_user(requester)).status_code == 403
    assert client.get("/api/tickets", headers=as_user(requester)).status_code == 403

    theirs = create(client, other).json()
    mine = create(client, requester).json()
    assert client.get(f"/api/tickets/{mine['id']}", headers=as_user(requester)).status_code == 403
    assert client.get(f"/api/portal/tickets/{theirs['id']}", headers=as_user(requester)).status_code == 404
    listed = client.get("/api/portal/tickets", headers=as_user(requester)).json()
    assert [t["id"] for t in listed["items"]] == [mine["id"]]
    # Comments on someone else's ticket are hidden too.
    assert client.get(f"/api/tickets/{theirs['id']}/comments", headers=as_user(requester)).status_code == 404

    assert client.get(f"/api/tickets/{mine['id']}/events", headers=as_user(requester)).status_code == 403
    assert client.patch(f"/api/tickets/{mine['id']}", json={"status": "closed"}, headers=as_user(requester)).status_code == 403
    assert client.get("/api/tickets/views", headers=as_user(requester)).status_code == 403


def test_auth_is_required(client, requester):
    assert client.get("/api/tickets").status_code == 401
    assert client.get("/api/tickets", headers={"X-Dev-User": "nobody@corp.local"}).status_code == 401


def test_assign_opens_ticket_and_logs_history(client, db, agent, requester, service_desk):
    t = create(client, requester).json()
    r = client.post(f"/api/tickets/{t['id']}/assign", json={"assignee_id": agent.id}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert r.json()["assignee"]["id"] == agent.id
    assert r.json()["status"] == "open"

    other_agent = make_user(db, "dcole@corp.local", UserRole.agent)
    client.post(f"/api/tickets/{t['id']}/assign", json={"assignee_id": other_agent.id}, headers=as_user(agent))

    events = client.get(f"/api/tickets/{t['id']}/events", headers=as_user(agent)).json()
    types = [e["type"] for e in events]
    assert types == ["created", "routed", "assigned", "status_changed", "assigned"]
    assert events[-1]["data"] == {
        "before": {"queue_id": service_desk.id, "assignee_id": agent.id},
        "after": {"queue_id": service_desk.id, "assignee_id": other_agent.id},
    }
    assert events[-1]["actor"]["id"] == agent.id
    assert events[1]["actor"] is None  # routing is done by the system


def test_cannot_assign_to_a_requester(client, agent, requester, service_desk):
    t = create(client, requester).json()
    r = client.post(f"/api/tickets/{t['id']}/assign", json={"assignee_id": requester.id}, headers=as_user(agent))
    assert r.status_code == 409


def test_escalate_moves_queue_raises_priority_and_tightens_sla(client, db, agent, requester, service_desk, slas):
    tier2 = Queue(name="Tier 2")
    db.add(tier2)
    db.commit()
    db.execute(update(Queue).where(Queue.id == service_desk.id).values(escalation_queue_id=tier2.id))
    db.commit()

    t = create(client, requester).json()
    client.post(f"/api/tickets/{t['id']}/assign", json={"assignee_id": agent.id}, headers=as_user(agent))
    r = client.post(f"/api/tickets/{t['id']}/escalate", json={"reason": "Front desk cannot check in guests"}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["queue"]["name"] == "Tier 2"
    assert e["priority"] == "high"
    assert e["assignee"] is None
    assert e["resolution_due_at"] < t["resolution_due_at"]

    last = client.get(f"/api/tickets/{t['id']}/events", headers=as_user(agent)).json()[-1]
    assert last["type"] == "escalated"
    assert last["data"]["reason"] == "Front desk cannot check in guests"


def test_comments_internal_notes_and_status_effects(client, agent, requester, service_desk):
    t = create(client, requester).json()
    tid = t["id"]

    assert client.post(f"/api/tickets/{tid}/comments", json={"body": "secret", "is_internal": True}, headers=as_user(requester)).status_code == 409

    client.post(f"/api/tickets/{tid}/comments", json={"body": "Checked the MFP logs", "is_internal": True}, headers=as_user(agent))
    after_note = client.get(f"/api/tickets/{tid}", headers=as_user(agent)).json()
    assert after_note["first_responded_at"] is None  # notes don't count as a response
    assert after_note["status"] == "new"

    client.post(f"/api/tickets/{tid}/comments", json={"body": "Can you power-cycle it?"}, headers=as_user(agent))
    replied = client.get(f"/api/tickets/{tid}", headers=as_user(agent)).json()
    assert replied["first_responded_at"] is not None
    assert replied["status"] == "open"

    client.patch(f"/api/tickets/{tid}", json={"status": "waiting_on_user"}, headers=as_user(agent))
    client.post(f"/api/tickets/{tid}/comments", json={"body": "Done, still jammed"}, headers=as_user(requester))
    assert client.get(f"/api/tickets/{tid}", headers=as_user(agent)).json()["status"] == "open"

    seen_by_requester = client.get(f"/api/tickets/{tid}/comments", headers=as_user(requester)).json()
    assert [c["body"] for c in seen_by_requester] == ["Can you power-cycle it?", "Done, still jammed"]
    assert len(client.get(f"/api/tickets/{tid}/comments", headers=as_user(agent)).json()) == 3


def test_resolve_and_reopen(client, agent, requester, service_desk, slas):
    tid = create(client, requester).json()["id"]
    r = client.patch(f"/api/tickets/{tid}", json={"status": "resolved"}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    resolved = r.json()
    assert resolved["resolved_at"] is not None
    assert resolved["sla_state"] == "met"
    reopened = client.patch(f"/api/tickets/{tid}", json={"status": "open"}, headers=as_user(agent)).json()
    assert reopened["resolved_at"] is None
    assert reopened["sla_state"] == "ok"


def test_priority_change_recomputes_sla_and_is_logged(client, agent, requester, service_desk, slas):
    t = create(client, requester).json()
    r = client.patch(f"/api/tickets/{t['id']}", json={"priority": "critical", "category": "Printing"}, headers=as_user(agent)).json()
    assert r["resolution_due_at"] < t["resolution_due_at"]
    assert r["category"] == "Printing"
    types = [e["type"] for e in client.get(f"/api/tickets/{t['id']}/events", headers=as_user(agent)).json()]
    assert types[-2:] == ["priority_changed", "updated"]


def test_saved_views_and_counts(client, db, agent, requester, service_desk, slas):
    a = create(client, requester, subject="A").json()  # portal, unassigned
    b = create(client, agent, subject="B", requester_email=requester.email, source="email").json()
    c = create(client, requester, subject="C").json()
    client.post(f"/api/tickets/{b['id']}/assign", json={"assignee_id": agent.id}, headers=as_user(agent))
    client.patch(f"/api/tickets/{c['id']}", json={"status": "waiting_on_user"}, headers=as_user(agent))
    # Push A past its at-risk point.
    db.execute(update(Ticket).where(Ticket.id == a["id"]).values(sla_at_risk_at=utcnow() - timedelta(minutes=1)))
    db.commit()

    def ids(view):
        return {t["id"] for t in client.get("/api/tickets", params={"view": view}, headers=as_user(agent)).json()["items"]}

    assert ids("unassigned") == {a["id"], c["id"]}
    assert ids("mine") == {b["id"]}
    assert ids("sla_at_risk") == {a["id"]}
    assert ids("waiting_on_user") == {c["id"]}
    assert ids("from_email") == {b["id"]}
    assert ids("from_portal") == {a["id"], c["id"]}

    counts = client.get("/api/tickets/views", headers=as_user(agent)).json()
    assert counts == {"open": 3, "unassigned": 2, "mine": 1, "my_queues": 0, "sla_at_risk": 1, "waiting_on_user": 1, "from_email": 1, "from_portal": 2}

    at_risk = client.get(f"/api/tickets/{a['id']}", headers=as_user(agent)).json()
    assert at_risk["sla_state"] == "at_risk"


def test_admin_only_setup_and_single_default_queue(client, agent, admin, service_desk):
    assert client.post("/api/queues", json={"name": "Network"}, headers=as_user(agent)).status_code == 403
    r = client.post("/api/queues", json={"name": "Network", "is_default": True}, headers=as_user(admin))
    assert r.status_code == 201
    queues = {q["name"]: q for q in client.get("/api/queues", headers=as_user(agent)).json()}
    assert queues["Network"]["is_default"] is True
    assert queues["Service desk"]["is_default"] is False

    assert client.post("/api/queues", json={"name": "Network"}, headers=as_user(admin)).status_code == 409
    assert client.post("/api/users", json={"email": "jpark@corp.local", "display_name": "dup"}, headers=as_user(admin)).status_code == 409
    assert client.post("/api/users", json={"email": "JPark@corp.local", "display_name": "dup"}, headers=as_user(admin)).status_code == 409


def test_sla_policy_put(client, admin, agent):
    r = client.put("/api/sla-policies/high", json={"first_response_minutes": 30, "resolution_minutes": 240}, headers=as_user(admin))
    assert r.status_code == 200
    r = client.put("/api/sla-policies/high", json={"first_response_minutes": 300, "resolution_minutes": 240}, headers=as_user(admin))
    assert r.status_code == 422
    assert client.get("/api/sla-policies", headers=as_user(agent)).json()[0]["first_response_minutes"] == 30


def test_rule_with_missing_queue_is_rejected(client, admin):
    r = client.post("/api/routing-rules", json={"name": "x", "set_queue_id": 999}, headers=as_user(admin))
    assert r.status_code == 404


def test_queue_sorts_by_any_column(client, db, agent, service_desk):
    from datetime import timedelta

    from app import sla
    from app.models import Priority, Ticket, TicketSource, TicketStatus, UserRole
    from tests.conftest import make_user

    zoe = make_user(db, "zoe@corp.local", name="Zoe Adams")
    amy = make_user(db, "amy@corp.local", name="amy Brown")
    tech = make_user(db, "tech@corp.local", UserRole.agent, "Bo Tech")
    now = sla.utcnow()

    def t(subject, requester, priority, days_old, assignee=None, status=TicketStatus.open):
        ticket = Ticket(subject=subject, source=TicketSource.portal, queue_id=service_desk.id, requester_id=requester.id, priority=priority,
                        status=status, assignee_id=assignee.id if assignee else None, created_at=now - timedelta(days=days_old))
        db.add(ticket)
        db.flush()
        return ticket.id

    a = t("Printer", zoe, Priority.low, 1, tech)
    b = t("VPN down", amy, Priority.critical, 5)
    c = t("Account locked", zoe, Priority.high, 3, agent, TicketStatus.new)
    db.commit()

    def ids(**params):
        r = client.get("/api/tickets", params=params, headers=as_user(agent))
        assert r.status_code == 200, r.text
        return [x["id"] for x in r.json()["items"]]

    assert ids(sort="priority") == [b, c, a]  # by severity, most severe first
    assert ids(sort="priority", dir="asc") == [a, c, b]
    assert ids(sort="age") == [b, c, a]  # longest open first
    assert ids(sort="requester") == [b, c, a]  # amy before Zoe (case-insensitive); ties newest first
    assert ids(sort="assignee") == [a, c, b]  # Bo Tech, J. Park, then unassigned last
    assert ids(sort="subject") == [c, a, b]
    assert ids(sort="status") == [c, b, a]  # new before open
    assert ids(sort="id") == [c, b, a]
    assert client.get("/api/tickets", params={"sort": "password"}, headers=as_user(agent)).status_code == 422


def test_notes_can_go_to_the_portal_with_or_without_email(client, db, agent, requester, service_desk, slas, monkeypatch):
    from sqlalchemy import select

    from app.config import settings
    from app.models import OutboundEmail

    monkeypatch.setattr(settings, "mailbox", "helpdesk@corp.local")
    tid = create(client, requester).json()["id"]
    db.query(OutboundEmail).delete()  # the new-ticket acknowledgement
    db.commit()

    def post(**body):
        r = client.post(f"/api/tickets/{tid}/comments", json={"body": "x", **body}, headers=as_user(agent))
        assert r.status_code == 201, r.text
        return r.json()

    internal = post(is_internal=True)
    portal_only = post(email_requester=False)
    emailed = post()
    assert (internal["is_internal"], internal["emailed"]) == (True, False)
    assert (portal_only["is_internal"], portal_only["emailed"]) == (False, False)
    assert (emailed["is_internal"], emailed["emailed"]) == (False, True)
    db.expire_all()
    assert [m.comment_id for m in db.scalars(select(OutboundEmail))] == [emailed["id"]]
    # The requester sees the two that aren't internal.
    seen = client.get(f"/api/tickets/{tid}/comments", headers=as_user(requester)).json()
    assert [c["id"] for c in seen] == [portal_only["id"], emailed["id"]]


def test_view_counts_for_one_queue(client, db, agent, requester):
    hr, maint = Queue(name="HR", is_default=True), Queue(name="Maintenance")
    db.add_all([hr, maint])
    db.flush()
    db.add_all([Ticket(subject=f"t{i}", source=TicketSource.portal, queue_id=(hr if i < 2 else maint).id, requester_id=requester.id) for i in range(5)])
    db.commit()
    all_open = client.get("/api/tickets/views", headers=as_user(agent)).json()["open"]
    only = client.get("/api/tickets/views", params={"queue_id": maint.id}, headers=as_user(agent)).json()["open"]
    assert (all_open, only) == (5, 3)
    assert client.get("/api/tickets", params={"queue_id": maint.id}, headers=as_user(agent)).json()["total"] == 3
