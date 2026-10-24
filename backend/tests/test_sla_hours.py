# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SLA clock in business time: desk hours, queue hours, closed days, and pausing while waiting."""
import time
from datetime import UTC, datetime, timedelta

import pytest

from app import services, sla
from app.config import settings
from app.models import Priority, Queue, SlaPolicy, Ticket, TicketSource, TicketStatus
from tests.conftest import as_user

FRI_16 = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)  # a Friday


@pytest.fixture(autouse=True)
def utc_desk(monkeypatch):
    """Server time is UTC; Mon-Fri 08:00-17:00; the desk-hours clock."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    monkeypatch.setattr(settings, "sla_clock", "desk")
    monkeypatch.setattr(settings, "desk_hours", "Mon-Fri 08:00-17:00")
    monkeypatch.setattr(settings, "desk_closed_days", "")
    yield
    monkeypatch.undo()
    time.tzset()


def test_business_time_skips_nights_weekends_and_closed_days(monkeypatch):
    sched = sla.schedule(None)
    assert sla.add_business(FRI_16, 4 * 60, sched) == datetime(2026, 10, 26, 11, 0, tzinfo=UTC)  # Monday 11:00
    assert sla.business_minutes(FRI_16, datetime(2026, 10, 26, 11, 0, tzinfo=UTC), sched) == 240
    assert sla.business_minutes(datetime(2026, 10, 26, 11, 0, tzinfo=UTC), FRI_16, sched) == -240
    # Saturday: the clock starts on Monday at 08:00.
    assert sla.add_business(datetime(2026, 10, 24, 10, 0, tzinfo=UTC), 60, sched) == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(settings, "desk_closed_days", "2026-10-26 Holiday")
    assert sla.add_business(FRI_16, 4 * 60, sla.schedule(None)) == datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
    # A queue's own hours win; no hours at all, or "around the clock", is wall time.
    assert sla.add_business(FRI_16, 4 * 60, sla.schedule("Mon-Sun 00:00-24:00")) == FRI_16 + timedelta(hours=4)
    monkeypatch.setattr(settings, "desk_hours", "")
    assert sla.schedule(None) is None
    monkeypatch.setattr(settings, "desk_hours", "Mon-Fri 08:00-17:00")
    monkeypatch.setattr(settings, "sla_clock", "always")
    assert sla.schedule(None) is None


def _ticket(db, queue_hours=None, minutes=240):
    """A ticket in memory (never saved), so each test can make several."""
    policy = SlaPolicy(priority=Priority.high, first_response_minutes=60, resolution_minutes=minutes, at_risk_percent=75)
    t = Ticket(subject="x", source=TicketSource.portal, status=TicketStatus.open, priority=Priority.high,
               queue=Queue(name="Desk", hours=queue_hours), created_at=FRI_16)
    sla.apply_policy(t, policy, FRI_16)
    return t


def test_policy_deadlines_in_business_time(db):
    t = _ticket(db)
    assert t.first_response_due_at == datetime(2026, 10, 23, 17, 0, tzinfo=UTC)
    assert t.sla_at_risk_at == datetime(2026, 10, 26, 10, 0, tzinfo=UTC)  # 75% of 4 h = 3 h
    assert t.resolution_due_at == datetime(2026, 10, 26, 11, 0, tzinfo=UTC)
    assert t.sla_resolution_minutes == 240
    # A queue open around the clock is due 4 hours later.
    assert _ticket(db, "Mon-Sun 00:00-24:00").resolution_due_at == FRI_16 + timedelta(hours=4)


def test_waiting_on_user_stops_the_clock_and_gives_the_time_back(db):
    t = _ticket(db)
    sla.pause(t, FRI_16 + timedelta(minutes=30))  # 30 business minutes used
    assert t.resolution_due_at is None and t.sla_at_risk_at is None and t.first_response_due_at is None
    assert t.sla_left["resolution"] == 210 and sla.sla_state(t, FRI_16) == sla.SlaState.paused
    assert t.sla_minutes_left == 210
    # Back on Tuesday at 09:00: 210 business minutes from then.
    tue_9 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
    sla.resume(t, tue_9)
    assert t.resolution_due_at == tue_9 + timedelta(minutes=210)
    assert t.sla_paused_at is None and t.sla_left is None
    assert t.sla_paused_minutes == 30 + 9 * 60 + 60  # Fri 16:30-17:00, all Monday, Tue 08:00-09:00


def test_priority_change_while_paused_keeps_it_paused(db):
    t = _ticket(db)
    paused_at = FRI_16 + timedelta(minutes=30)
    sla.pause(t, paused_at)
    sla.apply_policy(t, SlaPolicy(priority=Priority.critical, first_response_minutes=15, resolution_minutes=120, at_risk_percent=75), FRI_16)
    assert t.resolution_due_at is None and t.sla_left["resolution"] == 90


def test_status_changes_pause_and_resume_and_paused_tickets_are_never_breached(client, db, agent, requester, service_desk, monkeypatch):
    db.add(SlaPolicy(priority=Priority.medium, first_response_minutes=1, resolution_minutes=1, at_risk_percent=50))
    db.commit()
    monkeypatch.setattr(settings, "sla_clock", "always")
    ticket = services.create_ticket(db, subject="Printer", description="", category=None, requester=requester, source=TicketSource.portal, actor=requester)
    db.commit()
    h = as_user(agent)
    assert client.patch(f"/api/tickets/{ticket.id}", json={"status": "waiting_on_user"}, headers=h).status_code == 200
    db.expire_all()
    t = db.get(Ticket, ticket.id)
    assert t.sla_paused_at is not None and t.resolution_due_at is None
    shown = client.get(f"/api/tickets/{ticket.id}", headers=h).json()
    assert shown["sla_state"] == "paused" and shown["sla_minutes_left"] == 1
    # Nothing lists it as at risk or breached while it waits.
    assert all(r["id"] != ticket.id for r in client.get("/api/tickets?view=sla_at_risk", headers=h).json()["items"])
    assert client.patch(f"/api/tickets/{ticket.id}", json={"status": "open"}, headers=h).status_code == 200
    db.expire_all()
    t = db.get(Ticket, ticket.id)
    assert t.sla_paused_at is None and t.resolution_due_at is not None
    # The setting off: waiting doesn't stop the clock.
    monkeypatch.setattr(settings, "sla_pause_waiting", False)
    client.patch(f"/api/tickets/{ticket.id}", json={"status": "waiting_on_user"}, headers=h)
    db.expire_all()
    assert db.get(Ticket, ticket.id).sla_paused_at is None
