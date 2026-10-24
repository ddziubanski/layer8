# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reports: the engine, ready-made reports, access by queue, and CSV export."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Priority, Queue, Ticket, TicketSource, TicketStatus, UserRole
from app.reports import presets
from tests.conftest import as_user, make_user

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def data(db, admin, agent):
    desk = Queue(name="Service desk", is_default=True)
    hr = Queue(name="HR", restricted=True)
    db.add_all([desk, hr])
    db.flush()
    dcole = make_user(db, "dcole@corp.local", UserRole.agent, "D. Cole")
    ral = make_user(db, "ralvarez@corp.local", name="R. Alvarez")
    ral.department = "Finance"
    mos = make_user(db, "mosei@corp.local", name="M. Osei")

    def t(queue, requester, assignee=None, status=TicketStatus.open, priority=Priority.medium, days=0, resolved_hours=None, due_hours=8, **kw):
        created = T0 + timedelta(days=days)
        ticket = Ticket(
            subject="x", source=TicketSource.portal, queue_id=queue.id, requester_id=requester.id,
            assignee_id=assignee.id if assignee else None, status=status, priority=priority, created_at=created,
            resolution_due_at=created + timedelta(hours=due_hours),
            resolved_at=created + timedelta(hours=resolved_hours) if resolved_hours is not None else None, **kw,
        )
        db.add(ticket)
        return ticket

    t(desk, ral, agent, TicketStatus.resolved, resolved_hours=4, category="Printer")          # met SLA
    t(desk, ral, agent, TicketStatus.closed, Priority.high, days=1, resolved_hours=10, category="Printer")  # missed
    t(desk, mos, dcole, TicketStatus.open, days=8, category="Wi-Fi")
    t(desk, mos, None, TicketStatus.new, Priority.critical, days=9)
    t(hr, ral, dcole, TicketStatus.open, days=2)  # restricted: hidden from agents
    db.commit()
    return {"desk": desk, "hr": hr, "dcole": dcole}


def run(client, user, **spec):
    r = client.post("/api/reports/run", json=spec, headers=as_user(user))
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {tuple(row[: len(spec.get("group_by", []))]): row[len(spec.get("group_by", [])):] for row in body["rows"]}


def test_tickets_per_technician(client, admin, data):
    body, rows = run(client, admin, group_by=["assignee"], measures=["count", "open", "resolved", "avg_hours_to_resolve", "resolution_breached"])
    assert [c["label"] for c in body["columns"]] == ["Technician", "Tickets", "Still open", "Resolved or closed", "Avg. hours to resolve", "Resolution SLA missed"]
    assert rows[("J. Park",)] == [2, 0, 2, 7.0, 1]
    assert rows[("D. Cole",)][:2] == [2, 2]
    assert rows[("Unassigned",)][:2] == [1, 1]
    # Biggest first, and totals over everything.
    # Every open ticket is past its due time by now; one resolved ticket was late.
    assert body["totals"] == {"count": 5, "open": 3, "resolved": 2, "resolution_breached": 4}


def test_agents_only_count_their_queues(client, admin, agent, data):
    assert run(client, admin, measures=["count"])[0]["rows"] == [[5]]
    body, rows = run(client, agent, group_by=["queue"], measures=["count"])
    assert rows == {("Service desk",): [4]}


def test_period_filters_and_time_groups(client, admin, data):
    body, rows = run(client, admin, group_by=["created_week"], measures=["count"], date_from="2026-09-01", date_to="2026-09-03")
    assert sum(r[1] for r in body["rows"]) == 3
    body, _ = run(client, admin, group_by=["created_day"], measures=["count"])
    assert [r[0] for r in body["rows"]] == sorted(r[0] for r in body["rows"])  # oldest first
    _, rows = run(client, admin, group_by=["status"], measures=["count"], filters={"assignee": ["none", str(data["dcole"].id)]})
    assert rows == {("Open",): [2], ("New",): [1]}
    _, rows = run(client, admin, group_by=["requester", "category"], measures=["count"], filters={"queue": [str(data["desk"].id)]})
    assert rows[("R. Alvarez", "Printer")] == [2] and rows[("M. Osei", "(none)")] == [1]


def test_sla_percent(client, admin, data):
    _, rows = run(client, admin, group_by=["priority"], measures=["resolution_met_pct"])
    assert rows[("High",)] == [0.0]
    assert rows[("Medium",)] == [pytest.approx(33.3)]


def test_every_preset_runs(client, admin, agent, data):
    r = client.get("/api/reports/presets", headers=as_user(admin))
    assert {p["key"] for p in r.json()} == set(presets.BY_KEY)
    for p in r.json():
        body, _ = run(client, admin, **p["spec"])
        assert body["columns"], p["key"]
    agents = {p["key"] for p in client.get("/api/reports/presets", headers=as_user(agent)).json()}
    assert agents == {k for k, p in presets.BY_KEY.items() if not p.spec.dataset.startswith("email")}


def test_bad_specs_are_refused(client, admin, requester):
    for spec, why in [
        ({"group_by": ["password_hash"]}, "Can't group"),
        ({"measures": ["sum_of_ids"]}, "Unknown measure"),
        ({"filters": {"nonsense": ["1"]}}, "Can't filter"),
        ({"filters": {"queue": ["abc"]}}, "expected IDs"),
        ({"dataset": "users_passwords"}, "Unknown dataset"),
    ]:
        r = client.post("/api/reports/run", json=spec, headers=as_user(admin))
        assert r.status_code == 422 and why in r.json()["detail"], spec
    assert client.post("/api/reports/run", json={}, headers=as_user(requester)).status_code == 403


def test_csv_export(client, admin, data, db):
    data_user = make_user(db, "evil@corp.local", name="=HYPERLINK(\"http://x\")")
    db.add(Ticket(subject="x", source=TicketSource.email, queue_id=data["desk"].id, requester_id=data_user.id))
    db.commit()
    r = client.post("/api/reports/run.csv", json={"group_by": ["requester"], "measures": ["count"]}, headers=as_user(admin))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0] == "Requester,Tickets"
    assert "'=HYPERLINK" in r.text  # can't run as a formula in Excel


def test_datasets_are_described(client, agent):
    d = client.get("/api/reports/datasets/tickets", headers=as_user(agent)).json()
    assert {"assignee", "requester", "created_month"} <= {x["key"] for x in d["dimensions"]}
    assert client.get("/api/reports/datasets/nope", headers=as_user(agent)).status_code == 404


def test_resolution_fields_in_reports(client, db, admin, data):
    from app.models import FieldList, FieldOption, Ticket

    fixed = FieldOption(list=FieldList.resolution_type, label="Fixed")
    db.add(fixed)
    db.flush()
    db.query(Ticket).filter(Ticket.resolved_at.is_not(None)).update({Ticket.resolution_type_id: fixed.id})
    db.commit()
    _, rows = run(client, admin, group_by=["resolution_type"], measures=["count"])
    assert rows == {("Fixed",): [2], ("Not set",): [3]}
    _, rows = run(client, admin, group_by=["assignee"], measures=["count"], filters={"resolution_type": [str(fixed.id)]})
    assert rows == {("J. Park",): [2]}


def test_saved_reports_private_or_shared(client, db, admin, agent):
    other = make_user(db, "dcole2@corp.local", UserRole.agent, "D. Cole")
    spec = {"group_by": ["assignee"], "measures": ["count", "open"], "filters": {"status": ["open"]}}
    r = client.post("/api/reports/saved", json={"name": "My backlog", "spec": spec}, headers=as_user(agent))
    assert r.status_code == 201, r.text
    mine = r.json()
    assert mine["spec"]["group_by"] == ["assignee"] and mine["can_edit"] is True and mine["shared"] is False
    # Private: others don't see it, admins do.
    assert client.get("/api/reports/saved", headers=as_user(other)).json() == []
    assert [x["name"] for x in client.get("/api/reports/saved", headers=as_user(admin)).json()] == ["My backlog"]
    # Shared: everyone sees it, only the owner (or an admin) changes it.
    client.put(f"/api/reports/saved/{mine['id']}", json={"name": "Team backlog", "spec": spec, "shared": True}, headers=as_user(agent))
    seen = client.get("/api/reports/saved", headers=as_user(other)).json()
    assert [(x["name"], x["can_edit"]) for x in seen] == [("Team backlog", False)]
    assert client.delete(f"/api/reports/saved/{mine['id']}", headers=as_user(other)).status_code == 403
    assert client.delete(f"/api/reports/saved/{mine['id']}", headers=as_user(admin)).status_code == 204
    # Specs that couldn't run aren't saved.
    r = client.post("/api/reports/saved", json={"name": "x", "spec": {"group_by": ["password_hash"]}}, headers=as_user(agent))
    assert r.status_code == 422


def test_activity_users_and_email_datasets(client, db, admin, agent, data):
    from app.models import Comment, InboundEmail, InboundStatus, Ticket, TicketSource

    t = db.query(Ticket).filter(Ticket.queue_id == data["desk"].id).first()
    hr_ticket = db.query(Ticket).filter(Ticket.queue_id == data["hr"].id).first()
    db.add_all([
        Comment(ticket_id=t.id, author_id=agent.id, body="checked", is_internal=True, source=TicketSource.agent),
        Comment(ticket_id=t.id, author_id=agent.id, body="try again", is_internal=False, emailed=True, source=TicketSource.agent),
        Comment(ticket_id=t.id, author_id=t.requester_id, body="still broken", is_internal=False, source=TicketSource.portal),
        Comment(ticket_id=hr_ticket.id, author_id=data["dcole"].id, body="hr", is_internal=True, source=TicketSource.agent),
        InboundEmail(internet_message_id="<1@x>", from_address="Spam@Gmail.com", status=InboundStatus.rejected, reason="not allowed"),
        InboundEmail(internet_message_id="<2@x>", from_address="r@corp.local", status=InboundStatus.processed, ticket_id=t.id),
    ])
    db.commit()

    _, rows = run(client, agent, dataset="activity", group_by=["kind"], measures=["count"])
    # The HR note is in a restricted queue: not counted for this agent.
    assert rows == {("Internal note",): [1], ("Reply to requester",): [1], ("Requester reply",): [1]}
    _, rows = run(client, admin, dataset="activity", group_by=["author"], measures=["count", "tickets", "notes", "emailed"])
    assert rows[("J. Park",)] == [2, 1, 1, 1] and rows[("D. Cole",)] == [1, 1, 1, 0]
    _, rows = run(client, admin, dataset="activity", group_by=["author"], measures=["count"], filters={"internal": ["true"]})
    assert rows == {("J. Park",): [1], ("D. Cole",): [1]}

    _, rows = run(client, admin, dataset="users", group_by=["role"], measures=["count"])
    assert rows[("Agent",)] == [2] and rows[("Admin",)] == [1]

    _, rows = run(client, admin, dataset="email_in", group_by=["domain"], measures=["count", "tickets"])
    assert rows == {("gmail.com",): [1, 0], ("corp.local",): [1, 1]}
    # Email reports are for admins: agents get nothing, and don't see them listed.
    assert run(client, agent, dataset="email_in", measures=["count"])[0]["rows"] == [[0]]
    assert "email_in" not in {d["name"] for d in client.get("/api/reports/datasets", headers=as_user(agent)).json()}
    assert not any(p["spec"]["dataset"] == "email_in" for p in client.get("/api/reports/presets", headers=as_user(agent)).json())
    roles = next(f for f in client.get("/api/reports/datasets/users", headers=as_user(admin)).json()["filters"] if f["key"] == "role")
    assert [v["label"] for v in roles["values"]] == ["Requester", "Agent", "Admin"]


# SQL reports


def sql_run(client, user, sql, **spec):
    return client.post("/api/reports/run", json={"dataset": "sql", "sql": sql, **spec}, headers=as_user(user))


def test_sql_report_columns_rows_and_period(client, admin, data):
    sql = "SELECT category, count(*) AS tickets, max(created_at) AS latest FROM tickets WHERE created_at >= :start AND created_at < :end GROUP BY 1 ORDER BY 2 DESC, 1"
    r = sql_run(client, admin, sql)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(c["label"], c["kind"]) for c in body["columns"]] == [("category", "text"), ("tickets", "number"), ("latest", "text")]
    assert sql_run(client, admin, "SELECT 1 AS device_id, 2 AS n").json()["columns"][0]["kind"] == "text"
    assert [row[:2] for row in body["rows"]] == [["Printer", 2], [None, 2], ["Wi-Fi", 1]]
    # The period applies through :start and :end (end inclusive of its day).
    r = sql_run(client, admin, sql, date_from="2026-09-01", date_to="2026-09-03")
    assert [row[:2] for row in r.json()["rows"]] == [["Printer", 2], [None, 1]]


def test_sql_report_is_read_only_one_select_and_limited(client, admin, data):
    for sql, message in [
        ("UPDATE tickets SET priority = 'low'", "Must start with SELECT or WITH"),
        ("WITH x AS (UPDATE tickets SET priority = 'low' RETURNING id) SELECT * FROM x", "SQL error"),
        ("SELECT 1; DELETE FROM tickets", "SQL error"),
        ("SELECT 1) AS report; COMMIT; UPDATE tickets SET priority = 'low'; SELECT * FROM (SELECT 1", "multiple commands"),
        ("SELECT * FROM sessions", "SQL error"),
        ("SELECT nextval('tickets_id_seq')", "SQL error"),
    ]:
        r = sql_run(client, admin, sql)
        assert r.status_code == 422 and message in r.json()["detail"], (sql, r.text)
    r = sql_run(client, admin, "SELECT id FROM tickets", limit=2)
    assert len(r.json()["rows"]) == 2 and r.json()["truncated"]


def test_sql_reports_are_for_admins(client, admin, agent, data):
    assert sql_run(client, agent, "SELECT 1").status_code == 422
    assert "sql" not in {d["name"] for d in client.get("/api/reports/datasets", headers=as_user(agent)).json()}
    spec = {"dataset": "sql", "sql": "SELECT 1 AS one"}
    r = client.post("/api/reports/saved", json={"name": "Raw", "spec": spec, "shared": True}, headers=as_user(admin))
    assert r.status_code == 201, r.text
    assert client.get("/api/reports/saved", headers=as_user(agent)).json() == []
    assert client.post("/api/reports/saved", json={"name": "Mine", "spec": spec}, headers=as_user(agent)).status_code == 403
    # CSV and PDF work like any report.
    assert client.post("/api/reports/run.csv", json=spec, headers=as_user(admin)).content.decode().splitlines()[1] == "1"
    r = client.post("/api/reports/run.pdf", json={"spec": spec, "title": "Raw", "description": ""}, headers=as_user(admin))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
