"""KACE-style custom ticket rules: builder and SQL, triggers, safety and test runs."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.config import settings
from app.models import Comment, OutboundEmail, Queue, RuleFire, RuleRun, Ticket, TicketEvent, TicketStatus, UserRole
from app.rules import engine
from app.sla import utcnow
from tests.conftest import ANSWERS, as_user


@pytest.fixture
def desk(db, service_desk, slas):
    return service_desk


def rule(client, admin, **body):
    body.setdefault("name", "rule")
    return client.post("/api/ticket-rules", json=body, headers=as_user(admin))


def new_ticket(client, user, **body):
    body.setdefault("subject", "Printer jam")
    path = "/api/portal/tickets" if user.role == UserRole.requester else "/api/tickets"
    if user.role == UserRole.requester:
        body = {**ANSWERS, **body}
    return client.post(path, json=body, headers=as_user(user)).json()["id"]


def events(client, agent, tid):
    return client.get(f"/api/tickets/{tid}/events", headers=as_user(agent)).json()


def comments(db, tid):
    db.expire_all()
    return db.scalars(select(Comment).where(Comment.ticket_id == tid).order_by(Comment.id)).all()


def ticket(db, tid):
    db.expire_all()
    return db.get(Ticket, tid)


# Builder rules


def test_on_create_rule_acts_after_routing_and_says_which_rule(client, db, admin, agent, requester, desk):
    r = rule(
        client,
        admin,
        name="VIP printers",
        trigger="on_create",
        conditions=[{"field": "text", "op": "contains_any", "value": ["printer"]}],
        actions=[{"type": "set_priority", "value": "high"}, {"type": "add_note", "body": "Auto: ticket ${ticket_id} in ${queue}"}],
    )
    assert r.status_code == 201, r.text
    tid = new_ticket(client, requester)
    t = ticket(db, tid)
    assert t.priority == "high"
    [note] = comments(db, tid)
    assert note.is_internal and note.body == f"Auto: ticket {tid} in Service desk"
    assert note.author.display_name == "Service Desk"
    pri = [e for e in events(client, agent, tid) if e["type"] == "priority_changed"][0]
    assert pri["data"]["rule"] == "VIP printers"
    # Tightened SLA, as for a person changing priority.
    assert t.resolution_due_at - t.created_at == timedelta(minutes=480)
    other = new_ticket(client, requester, subject="VPN")
    assert ticket(db, other).priority == "medium"


def test_on_update_requester_reply_emails_assignee(client, db, admin, agent, requester, desk, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@corp.local")
    rule(
        client,
        admin,
        name="tell owner",
        trigger="on_update",
        conditions=[{"field": "change", "op": "in", "value": ["requester_reply"]}, {"field": "assignee", "op": "is_set"}],
        actions=[{"type": "email", "to": "assignee", "subject": "${requester} replied on ${ticket_id}", "body": "See ${url}"}],
    )
    tid = new_ticket(client, requester)
    client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": agent.id}, headers=as_user(agent))
    client.post(f"/api/tickets/{tid}/comments", json={"body": "agent"}, headers=as_user(agent))
    client.post(f"/api/tickets/{tid}/comments", json={"body": "still broken"}, headers=as_user(requester))

    notices = db.scalars(select(OutboundEmail).where(OutboundEmail.is_staff_notice)).all()
    assert [(n.to_address, n.subject) for n in notices] == [(agent.email, f"R. Alvarez replied on {tid}")]


def test_rule_changes_never_trigger_on_update_rules(client, db, admin, agent, requester, desk):
    rule(client, admin, name="a", trigger="on_update", conditions=[{"field": "change", "op": "in", "value": ["status"]}], actions=[{"type": "set_priority", "value": "high"}])
    rule(client, admin, name="b", trigger="on_update", conditions=[{"field": "change", "op": "in", "value": ["priority"]}], actions=[{"type": "add_note", "body": "b fired"}])
    tid = new_ticket(client, requester)
    client.patch(f"/api/tickets/{tid}", json={"status": "open"}, headers=as_user(agent))
    assert ticket(db, tid).priority == "high"  # rule a ran
    assert comments(db, tid) == []  # rule b did not run on a's change
    # A person changing priority does trigger b.
    client.patch(f"/api/tickets/{tid}", json={"priority": "low"}, headers=as_user(agent))
    assert [c.body for c in comments(db, tid)] == ["b fired"]


def test_scheduled_rule_fires_once_per_ticket_until_it_stops_matching(client, db, session_factory, admin, agent, requester, desk):
    r = rule(
        client,
        admin,
        name="nudge unassigned",
        trigger="schedule",
        schedule_minutes=15,
        conditions=[{"field": "assignee", "op": "is_empty"}, {"field": "status", "op": "not_in", "value": ["resolved", "closed"]}, {"field": "age_minutes", "op": "gt", "value": 120}],
        actions=[{"type": "add_note", "body": "Unassigned for 2h"}],
    ).json()
    old, fresh = new_ticket(client, requester), new_ticket(client, requester)
    db.execute(update(Ticket).where(Ticket.id == old).values(created_at=utcnow() - timedelta(hours=3)))
    db.commit()

    now = utcnow()
    assert engine.run_due(session_factory, now) == 1
    assert [c.body for c in comments(db, old)] == ["Unassigned for 2h"] and comments(db, fresh) == []
    # Not due again yet; and when it is, the same ticket isn't nudged twice.
    assert engine.run_due(session_factory, now + timedelta(minutes=5)) == 0
    engine.run_due(session_factory, now + timedelta(minutes=16))
    assert len(comments(db, old)) == 1

    # Assigning clears it; unassigning later lets it fire again.
    client.post(f"/api/tickets/{old}/assign", json={"assignee_id": agent.id}, headers=as_user(agent))
    engine.run_due(session_factory, now + timedelta(minutes=32))
    assert db.scalar(select(func.count()).select_from(RuleFire)) == 0
    client.post(f"/api/tickets/{old}/assign", json={"unassign": True}, headers=as_user(agent))
    engine.run_due(session_factory, now + timedelta(minutes=48))
    assert [c.body for c in comments(db, old)].count("Unassigned for 2h") == 2

    runs = client.get(f"/api/ticket-rules/{r['id']}/runs", headers=as_user(admin)).json()
    assert runs[0]["trigger"] == "schedule" and runs[0]["matched"] == 1


def test_auto_close_resolved_after_days(client, db, session_factory, admin, agent, requester, desk):
    rule(
        client,
        admin,
        name="close after 7 days",
        trigger="schedule",
        schedule_minutes=60,
        conditions=[{"field": "status", "op": "in", "value": ["resolved"]}, {"field": "status_minutes", "op": "gt", "value": 7 * 24 * 60}],
        actions=[{"type": "set_status", "value": "closed"}],
    )
    tid = new_ticket(client, requester)
    client.patch(f"/api/tickets/{tid}", json={"status": "resolved"}, headers=as_user(agent))
    engine.run_due(session_factory, utcnow())
    assert ticket(db, tid).status == TicketStatus.resolved
    db.execute(update(Ticket).where(Ticket.id == tid).values(status_changed_at=utcnow() - timedelta(days=8)))
    db.commit()
    engine.run_due(session_factory, utcnow() + timedelta(hours=2))
    t = ticket(db, tid)
    assert t.status == TicketStatus.closed and t.closed_at is not None


def test_test_run_shows_changes_and_changes_nothing(client, db, admin, agent, requester, desk, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@corp.local")
    r = rule(
        client,
        admin,
        name="escalate stale",
        trigger="schedule",
        schedule_minutes=60,
        conditions=[{"field": "status", "op": "in", "value": ["new"]}],
        actions=[{"type": "set_priority", "value": "critical"}, {"type": "add_reply", "body": "We're on it"}],
    ).json()
    tid = new_ticket(client, requester)
    before_events = db.scalar(select(func.count()).select_from(TicketEvent))
    before_mail = db.scalar(select(func.count()).select_from(OutboundEmail))

    run = client.post(f"/api/ticket-rules/{r['id']}/test", headers=as_user(admin)).json()
    assert run["dry_run"] and run["matched"] == 1 and run["changed"] == 1 and run["error"] is None
    [detail] = run["details"]
    assert detail["ticket_id"] == tid
    assert {"field": "priority", "before": "medium", "after": "critical"} in detail["changes"]

    db.expire_all()
    assert ticket(db, tid).priority == "medium"
    assert db.scalar(select(func.count()).select_from(TicketEvent)) == before_events
    assert db.scalar(select(func.count()).select_from(OutboundEmail)) == before_mail
    assert comments(db, tid) == []
    assert db.scalar(select(func.count()).select_from(RuleFire)) == 0


def test_row_cap_stops_the_whole_run(client, db, admin, requester, desk):
    r = rule(client, admin, name="capped", trigger="schedule", schedule_minutes=5, max_rows=2, conditions=[{"field": "status", "op": "in", "value": ["new"]}], actions=[{"type": "set_priority", "value": "low"}]).json()
    ids = [new_ticket(client, requester) for _ in range(3)]
    run = client.post(f"/api/ticket-rules/{r['id']}/run", headers=as_user(admin)).json()
    assert "limit of 2" in run["error"]
    assert all(ticket(db, i).priority == "medium" for i in ids)


def test_a_failing_rule_never_breaks_the_save(client, db, admin, requester, desk, monkeypatch):
    rule(client, admin, name="boom", trigger="on_create", actions=[{"type": "add_note", "body": "x"}])

    def explode(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr("app.rules.actions.apply", explode)
    tid = new_ticket(client, requester)
    assert ticket(db, tid) is not None


def test_automated_reply_does_not_count_as_first_response(client, db, admin, requester, desk):
    rule(client, admin, name="ack", trigger="on_create", actions=[{"type": "add_reply", "body": "Thanks, we got it"}])
    tid = new_ticket(client, requester)
    t = ticket(db, tid)
    assert [c.body for c in comments(db, tid)] == ["Thanks, we got it"]
    assert t.first_responded_at is None


def test_queue_scope(client, db, admin, agent, requester, desk):
    network = Queue(name="Network")
    db.add(network)
    db.commit()
    rule(client, admin, name="net only", trigger="on_create", queue_ids=[network.id], actions=[{"type": "set_priority", "value": "high"}])
    a = new_ticket(client, agent, queue_id=network.id)
    b = new_ticket(client, agent)
    assert ticket(db, a).priority == "high" and ticket(db, b).priority == "medium"


@pytest.mark.parametrize(
    "body, message",
    [
        ({"trigger": "schedule", "conditions": [{"field": "status", "op": "in", "value": ["new"]}], "actions": [{"type": "set_priority", "value": "low"}]}, "schedule_minutes"),
        ({"trigger": "schedule", "schedule_minutes": 5, "actions": [{"type": "set_priority", "value": "low"}]}, "at least one condition"),
        ({"trigger": "schedule", "schedule_minutes": 5, "conditions": [{"field": "change", "op": "in", "value": ["status"]}], "actions": [{"type": "set_priority", "value": "low"}]}, "on_update"),
        ({"trigger": "on_create", "conditions": [{"field": "colour", "op": "in", "value": ["red"]}], "actions": [{"type": "set_priority", "value": "low"}]}, "unknown field"),
        ({"trigger": "on_create", "conditions": [{"field": "status", "op": "in", "value": ["sleeping"]}], "actions": [{"type": "set_priority", "value": "low"}]}, "values must be"),
        ({"trigger": "on_create", "actions": []}, "at least one action"),
        ({"trigger": "on_create", "actions": [{"type": "email", "to": "address", "address": "nope", "subject": "s", "body": "b"}]}, "address"),
        ({"trigger": "on_create", "actions": [{"type": "set_priority", "value": "low"}], "update_sql": "UPDATE tickets SET priority='low'"}, "Builder rules can't have SQL"),
    ],
)
def test_validation(client, admin, body, message):
    r = rule(client, admin, **body)
    assert r.status_code == 422 and message in r.json()["detail"]


def test_admins_only(client, agent):
    assert client.get("/api/ticket-rules", headers=as_user(agent)).status_code == 403
    assert rule(client, agent, trigger="on_create", actions=[{"type": "add_note", "body": "x"}]).status_code == 403


# SQL rules


def test_sql_select_with_builder_actions(client, db, session_factory, admin, requester, desk):
    rule(
        client,
        admin,
        name="sql low",
        trigger="schedule",
        schedule_minutes=10,
        mode="sql",
        select_sql="SELECT t.id FROM tickets t WHERE t.priority = 'low' AND t.status <> 'closed'",
        actions=[{"type": "add_note", "body": "low priority ticket"}],
    )
    low = new_ticket(client, requester, subject="low one")
    db.execute(update(Ticket).where(Ticket.id == low).values(priority="low"))
    db.commit()
    other = new_ticket(client, requester)
    engine.run_due(session_factory, utcnow())
    assert [c.body for c in comments(db, low)] == ["low priority ticket"] and comments(db, other) == []


def test_sql_update_is_applied_by_the_app_with_history_sla_and_move_note(client, db, admin, agent, requester, desk):
    tier2 = Queue(name="Tier 2")
    db.add(tier2)
    db.commit()
    r = rule(
        client,
        admin,
        name="sql bump",
        trigger="schedule",
        schedule_minutes=10,
        mode="sql",
        select_sql="SELECT id FROM tickets WHERE subject ILIKE '%outage%'",
        update_sql=f"UPDATE tickets SET priority = 'critical', queue_id = {tier2.id} WHERE id = ANY(:ticket_ids)",
    ).json()
    assert r.get("id"), r
    tid = new_ticket(client, requester, subject="Site outage")
    calm = new_ticket(client, requester, subject="Mouse")

    preview = client.post(f"/api/ticket-rules/{r['id']}/test", headers=as_user(admin)).json()
    assert preview["error"] is None and preview["matched"] == 1
    assert ticket(db, tid).priority == "medium"  # test changed nothing

    run = client.post(f"/api/ticket-rules/{r['id']}/run", headers=as_user(admin)).json()
    assert run["error"] is None and run["changed"] == 1
    t = ticket(db, tid)
    assert (t.priority, t.queue_id) == ("critical", tier2.id)
    assert t.resolution_due_at - t.created_at == timedelta(minutes=240)
    evs = events(client, agent, tid)
    assert {e["type"] for e in evs if e["data"].get("rule") == "sql bump"} >= {"priority_changed", "assigned", "comment_added"}
    assert comments(db, tid)[0].body.startswith("Moved from Service desk to Tier 2 by Service Desk.")
    assert ticket(db, calm).priority == "medium"


def test_sql_update_cap_changes_nothing(client, db, admin, requester, desk):
    r = rule(
        client, admin, name="sql all", trigger="schedule", schedule_minutes=10, mode="sql", max_rows=1,
        select_sql="SELECT id FROM tickets", update_sql="UPDATE tickets SET priority = 'low'",
    ).json()
    a, b = new_ticket(client, requester), new_ticket(client, requester)
    run = client.post(f"/api/ticket-rules/{r['id']}/run", headers=as_user(admin)).json()
    assert "limit of 1" in run["error"]
    assert ticket(db, a).priority == ticket(db, b).priority == "medium"


@pytest.mark.parametrize(
    "select_sql, update_sql, message",
    [
        ("SELECT user_id AS id FROM user_sessions", None, "permission denied"),
        ("SELECT id, password_hash FROM users", None, "permission denied"),
        ("SELECT 1 AS id; DELETE FROM tickets", None, "SQL error"),
        # Closing the wrapper's bracket to slip in more statements: refused (one prepared statement only).
        ("SELECT 1 AS id) AS rule_select; COMMIT; UPDATE tickets SET status = 'closed'; SELECT * FROM (SELECT 1 AS id", None, "multiple commands"),
        ("SELECT id FROM tickets", "UPDATE tickets SET priority = 'low') SELECT 1; COMMIT; UPDATE tickets SET status = 'closed'; WITH x AS (SELECT 1", "SQL error"),
        ("DELETE FROM tickets", None, "Must start with SELECT"),
        ("SELECT set_config('role', 'helpdesk', false) AS id", None, "SQL error"),
        ("SELECT id FROM tickets", "UPDATE users SET role = 'admin'", "permission denied"),
        ("SELECT id FROM tickets", "UPDATE tickets SET resolution_due_at = now()", "permission denied"),
        ("SELECT id FROM tickets", "UPDATE tickets SET priority = 'low' RETURNING id", "RETURNING"),
        ("SELECT subject FROM tickets", None, "SQL error"),  # no id column
    ],
)
def test_sql_rules_cannot_escape_their_login(client, db, admin, requester, desk, select_sql, update_sql, message):
    new_ticket(client, requester)
    r = rule(client, admin, name="evil", trigger="schedule", schedule_minutes=5, mode="sql", select_sql=select_sql, update_sql=update_sql, actions=[] if update_sql else [{"type": "add_note", "body": "x"}])
    assert r.status_code == 422, r.text
    assert message in r.json()["detail"]
    # Nothing leaked into the app's data.
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1
    assert db.scalar(select(func.count()).select_from(Ticket).where(Ticket.status == "closed")) == 0


def test_sql_rules_disabled_without_their_login(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "rules_database_url", "")
    r = rule(client, admin, trigger="schedule", schedule_minutes=5, mode="sql", select_sql="SELECT id FROM tickets", actions=[{"type": "add_note", "body": "x"}])
    assert r.status_code == 422 and "HELPDESK_RULES_DATABASE_URL" in r.json()["detail"]
    assert client.get("/api/ticket-rules/catalog", headers=as_user(admin)).json()["sql_enabled"] is False


def test_run_log_is_kept_for_event_rules_only_when_something_happens(client, db, admin, requester, desk):
    r = rule(client, admin, name="printers", trigger="on_create", conditions=[{"field": "text", "op": "contains_any", "value": ["printer"]}], actions=[{"type": "set_priority", "value": "high"}]).json()
    new_ticket(client, requester, subject="VPN")
    new_ticket(client, requester, subject="printer")
    runs = db.scalars(select(RuleRun).where(RuleRun.rule_id == r["id"])).all()
    assert [(x.trigger, x.matched, x.changed) for x in runs] == [("on_create", 1, 1)]


def test_rules_can_match_impact(client, db, admin, requester, desk):
    rule(client, admin, name="business down", trigger="on_create", conditions=[{"field": "impact", "op": "in", "value": ["business"]}], actions=[{"type": "add_note", "body": "Major incident?"}])
    a = new_ticket(client, requester, impact="business", urgency="low")
    b = new_ticket(client, requester)
    assert [c.body for c in comments(db, a)] == ["Major incident?"] and comments(db, b) == []


def test_rule_starts_a_checklist_once_and_never_from_its_own_steps(client, db, admin, agent, requester, desk):
    from app.models import ChecklistItem, TicketChecklist

    starter = client.post("/api/checklist-templates", json={"name": "New starter", "kind": "onboarding", "items": [
        {"title": "Order laptop for the starter", "offset_days": -7}, {"title": "Day one", "offset_days": 0}]}, headers=as_user(admin)).json()
    leaver = client.post("/api/checklist-templates", json={"name": "Leaver", "kind": "offboarding", "items": [
        {"title": "Disable", "offset_days": 1, "action": "disable_ad_account"}]}, headers=as_user(admin)).json()
    # "starter" is in the steps' subjects too: they mustn't start checklists of their own.
    r = rule(client, admin, name="starters", trigger="on_create", conditions=[{"field": "text", "op": "contains_any", "value": ["starter"]}],
             actions=[{"type": "start_checklist", "value": starter["id"], "for_name": "the starter in ${subject}"}, {"type": "start_checklist", "value": leaver["id"]}])
    assert r.status_code == 201, r.text
    assert rule(client, admin, name="bad", trigger="on_create", actions=[{"type": "start_checklist"}]).status_code == 422
    tid = new_ticket(client, requester, subject="New starter Monday")
    db.expire_all()
    lists = db.scalars(select(TicketChecklist)).all()
    assert [(c.ticket_id, c.name, c.for_name, c.key_date) for c in lists] == [(tid, "New starter", "the starter in New starter Monday", None)]
    steps = [i.child_ticket_id for i in db.scalars(select(ChecklistItem))]
    assert len(steps) == 2 and all(ticket(db, s).parent_id == tid for s in steps)
    # The leaver checklist needs a date and an AD person: skipped, and the run says why.
    runs = db.scalars(select(RuleRun).order_by(RuleRun.id)).all()
    assert any("start it by hand" in note for r in runs for d in r.details for note in d.get("skipped", []))

    # Set the date afterwards: the steps' due dates follow.
    from datetime import date

    c = lists[0]
    got = client.put(f"/api/tickets/{tid}/checklists/{c.id}/date", json={"key_date": "2026-11-09"}, headers=as_user(agent)).json()
    assert [i["due_on"] for i in got["items"]] == ["2026-11-02", "2026-11-09"]
    assert ticket(db, steps[1]).resolution_due_at.astimezone().date() == date(2026, 11, 9)
    assert any("Start date for \"New starter\"" in x.body for x in comments(db, tid))


def test_sql_is_refused_while_turned_off(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "sql_enabled", False)
    r = rule(client, admin, trigger="schedule", schedule_minutes=5, mode="sql", select_sql="SELECT id FROM tickets", actions=[{"type": "add_note", "body": "x"}])
    assert r.status_code == 422 and "Settings > General > Allow SQL" in r.json()["detail"]
