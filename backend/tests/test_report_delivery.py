# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reports as PDF, emailed now, and on a schedule."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import OutboundEmail, ReportSchedule, Ticket, TicketSource
from app.reports import delivery
from app import storage
from tests.conftest import as_user, make_user

SPEC = {"group_by": ["status"], "measures": ["count"]}


@pytest.fixture
def mail(monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@example.com")
    monkeypatch.setattr(settings, "mail_allowed_domains", ["example.com"])


@pytest.fixture
def some_tickets(db, service_desk, requester):
    for i in range(3):
        db.add(Ticket(subject=f"t{i}", source=TicketSource.portal, queue_id=service_desk.id, requester_id=requester.id))
    db.commit()


def test_pdf_download(client, agent, some_tickets):
    r = client.post("/api/reports/run.pdf", json={"spec": SPEC, "title": "Total tickets by status"}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert "total-tickets-by-status" in r.headers["content-disposition"]


def test_email_now_queues_mail_with_the_files(client, db, agent, mail, some_tickets):
    r = client.post(
        "/api/reports/email",
        json={"spec": SPEC, "title": "Total tickets", "recipients": ["boss@example.com", "BOSS@example.com", agent.email], "format": "both", "message": "For Monday's meeting"},
        headers=as_user(agent),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"queued": 2}  # de-duplicated
    rows = db.scalars(select(OutboundEmail).order_by(OutboundEmail.id)).all()
    assert [(m.to_address, m.ticket_id) for m in rows] == [("boss@example.com", None), (agent.email, None)]
    assert rows[0].subject == "Report: Total tickets" and rows[0].body.startswith("For Monday's meeting")
    names = [a["filename"] for a in rows[0].attachments]
    assert names[0].endswith(".pdf") and names[1].endswith(".csv")
    assert storage.path(rows[0].attachments[0]["storage_key"]).read_bytes().startswith(b"%PDF")


def test_reports_only_go_to_known_people(client, agent, mail):
    for recipients, why in [(["someone@gmail.com"], "isn't a user here"), (["not-an-address"], "isn't an email address"), ([" "], "at least one")]:
        r = client.post("/api/reports/email", json={"spec": SPEC, "title": "x", "recipients": recipients}, headers=as_user(agent))
        assert r.status_code == 422 and why in r.json()["detail"], recipients


def test_email_needs_a_mailbox(client, agent, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "")
    r = client.post("/api/reports/email", json={"spec": SPEC, "title": "x", "recipients": [agent.email]}, headers=as_user(agent))
    assert r.status_code == 422 and "Email isn't set up" in r.json()["detail"]


def test_sent_with_attachments_through_the_mailbox(db, agent, mail, some_tickets):
    from app.mail import outbox
    from tests.test_mail_intake import FakeMailbox

    files = delivery.build_files(db, delivery.engine.ReportSpec(**SPEC), title="T", description="", user=agent, fmt="pdf", now=datetime.now(timezone.utc))
    delivery.queue_email(db, ["boss@example.com"], title="T", files=files, sender="J. Park", message="", period="All time")
    db.commit()
    box = FakeMailbox()
    assert outbox.send_due(db, box, datetime.now(timezone.utc)) == 1
    (sent,) = box.sent
    assert sent.headers == {} and sent.attachments[0].data.startswith(b"%PDF")


@pytest.mark.parametrize(
    "name, today, expected",
    [
        ("yesterday", date(2026, 10, 19), (date(2026, 10, 18), date(2026, 10, 18))),
        ("previous_week", date(2026, 10, 19), (date(2026, 10, 12), date(2026, 10, 18))),  # a Monday
        ("previous_week", date(2026, 10, 25), (date(2026, 10, 12), date(2026, 10, 18))),  # a Sunday
        ("previous_month", date(2026, 3, 5), (date(2026, 2, 1), date(2026, 2, 28))),
        ("last_7_days", date(2026, 10, 19), (date(2026, 10, 13), date(2026, 10, 19))),
        ("all", date(2026, 10, 19), (None, None)),
    ],
)
def test_periods(name, today, expected):
    assert delivery.period_range(name, today) == expected


def _local(y, mo, d, h=7, mi=0):
    return datetime(y, mo, d, h, mi).astimezone()


def test_next_run_times():
    s = ReportSchedule(frequency="weekly", weekdays=[0, 3], time_of_day="07:00", start_date=date(2026, 10, 19))
    assert delivery.next_run(s, _local(2026, 10, 19, 6, 0)) == _local(2026, 10, 19)  # Monday 07:00
    assert delivery.next_run(s, _local(2026, 10, 19, 7, 0)) == _local(2026, 10, 22)  # then Thursday
    s = ReportSchedule(frequency="monthly", day_of_month=-1, time_of_day="18:30", start_date=date(2026, 1, 1))
    assert delivery.next_run(s, _local(2026, 2, 3)) == _local(2026, 2, 28, 18, 30)  # last day of February
    s = ReportSchedule(frequency="once", time_of_day="09:00", start_date=date(2026, 11, 2))
    assert delivery.next_run(s, _local(2026, 10, 19)) == _local(2026, 11, 2, 9)
    assert delivery.next_run(s, _local(2026, 11, 2, 9)) is None  # done
    s = ReportSchedule(frequency="daily", time_of_day="08:00", start_date=date(2026, 10, 1))
    assert delivery.next_run(s, _local(2026, 10, 19, 8, 0)) == _local(2026, 10, 20, 8)


def test_schedule_runs_from_the_worker(client, db, session_factory, agent, mail, some_tickets):
    body = {"name": "Weekly status", "preset_key": "total-by-status", "period": "previous_week", "recipients": ["boss@example.com"],
            "frequency": "daily", "time_of_day": "07:00", "start_date": date.today().isoformat()}
    r = client.post("/api/reports/schedules", json=body, headers=as_user(agent))
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["report_title"] == "Total tickets by status" and s["next_run_at"] is not None and s["can_edit"]

    # Not due yet: nothing happens.
    assert delivery.run_due(session_factory) == (0, 0)
    due = datetime.fromisoformat(s["next_run_at"]) + timedelta(seconds=1)
    assert delivery.run_due(session_factory, due) == (1, 0)
    db.expire_all()
    (mail_row,) = db.scalars(select(OutboundEmail)).all()
    assert mail_row.to_address == "boss@example.com" and mail_row.attachments[0]["filename"].endswith(".pdf")
    sched = db.get(ReportSchedule, s["id"])
    assert sched.last_run_at is not None and sched.next_run_at > due and sched.last_error is None

    # Send now, from the list.
    assert client.post(f"/api/reports/schedules/{s['id']}/send", headers=as_user(agent)).json() == {"queued": 1}


def test_schedule_failures_are_recorded(client, db, session_factory, agent, mail, some_tickets):
    body = {"name": "Once", "preset_key": "by-queue", "period": "all", "recipients": ["boss@example.com"],
            "frequency": "once", "time_of_day": "07:00", "start_date": (date.today() + timedelta(days=1)).isoformat()}
    s = client.post("/api/reports/schedules", json=body, headers=as_user(agent)).json()
    agent.is_active = False
    db.commit()
    due = datetime.fromisoformat(s["next_run_at"]) + timedelta(seconds=1)
    assert delivery.run_due(session_factory, due) == (0, 1)
    db.expire_all()
    sched = db.get(ReportSchedule, s["id"])
    assert "no longer an active user" in sched.last_error and sched.is_active is False  # a one-off is done either way


def test_schedules_are_checked_and_private(client, db, agent, mail):
    base = {"name": "x", "period": "previous_week", "recipients": ["boss@example.com"], "frequency": "weekly", "start_date": date.today().isoformat()}
    for extra, why in [
        ({}, "Pick one report"),
        ({"preset_key": "total-by-status", "saved_report_id": 1}, "Pick one report"),
        ({"preset_key": "total-by-status", "weekdays": []}, "at least one day"),
        ({"preset_key": "total-by-status", "period": "fortnight"}, "Unknown period"),
        ({"preset_key": "total-by-status", "recipients": ["x@gmail.com"]}, "isn't a user here"),
        ({"preset_key": "total-by-status", "frequency": "once", "start_date": "2020-01-01"}, "never runs"),
    ]:
        r = client.post("/api/reports/schedules", json={**base, **extra}, headers=as_user(agent))
        assert r.status_code == 422 and why in r.json()["detail"], extra
    s = client.post("/api/reports/schedules", json={**base, "preset_key": "total-by-status"}, headers=as_user(agent)).json()
    other = make_user(db, "dcole@example.com", agent.role, "D. Cole")
    assert client.get("/api/reports/schedules", headers=as_user(other)).json() == []
    assert client.delete(f"/api/reports/schedules/{s['id']}", headers=as_user(other)).status_code == 404
    assert client.delete(f"/api/reports/schedules/{s['id']}", headers=as_user(agent)).status_code == 204
