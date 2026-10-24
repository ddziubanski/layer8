# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tickets nobody has taken: one alert to everyone who could take them, a digest for a backlog."""
from datetime import timedelta

from app import services, sla, unaccepted
from app.config import settings
from app.models import OutboundEmail, Queue, Ticket, TicketSource, User, UserRole
from tests.conftest import as_user


def _ticket(db, requester, subject="VPN down", minutes_ago=31, queue=None):
    t = services.create_ticket(db, subject=subject, description="", category=None, requester=requester, source=TicketSource.portal,
                               actor=requester, preferred_queue=queue)
    t.unassigned_since = sla.utcnow() - timedelta(minutes=minutes_ago)
    db.commit()
    return t


def _on(monkeypatch):
    monkeypatch.setattr(settings, "unaccepted_alert_enabled", True)
    monkeypatch.setattr(settings, "unaccepted_alert_minutes", 30)
    monkeypatch.setattr(settings, "mailbox", "helpdesk@example.com")
    monkeypatch.setattr(settings, "email_commands", True)


def _alerts(db):
    return db.query(OutboundEmail).filter(OutboundEmail.subject.like("%not taken%") | OutboundEmail.subject.like("Not taken%")).all()


def test_off_by_default_then_one_alert_per_technician_once(db, admin, agent, requester, service_desk, monkeypatch):
    t = _ticket(db, requester)
    assert unaccepted.run(db) == "Off: Settings > Desk hours"
    _on(monkeypatch)
    fresh = _ticket(db, requester, "Too new", minutes_ago=5)
    assert unaccepted.run(db).startswith("1 ticket(s) not taken; 2 email(s)")
    db.commit()
    mails = _alerts(db)
    assert sorted(m.to_address for m in mails) == sorted([admin.email, agent.email])
    assert all(m.ticket_id == t.id and m.is_staff_notice and "#assign me" in m.body and "VPN down" in m.body for m in mails)
    assert db.get(Ticket, fresh.id).unaccepted_alerted_at is None
    # Once only.
    assert unaccepted.run(db) == "Nothing waiting"


def test_only_people_who_could_take_it_and_unassigning_starts_over(db, admin, agent, requester, service_desk, monkeypatch):
    _on(monkeypatch)
    hr = Queue(name="HR", restricted=True)
    hr.owners.append(admin)
    db.add(hr)
    db.commit()
    t = _ticket(db, requester, queue=hr)
    unaccepted.run(db)
    db.commit()
    assert [m.to_address for m in _alerts(db)] == [admin.email]
    services.assign(db, t, admin, assignee_id=admin.id)
    assert t.unassigned_since is None
    services.assign(db, t, admin, unassign=True)
    assert t.unaccepted_alerted_at is None and t.unassigned_since is not None
    db.commit()


def test_a_backlog_is_one_digest_per_person(db, admin, agent, requester, service_desk, monkeypatch):
    _on(monkeypatch)
    for i in range(4):
        _ticket(db, requester, f"Old ticket {i}", minutes_ago=600)
    assert unaccepted.run(db).startswith("4 ticket(s) not taken; 2 email(s)")
    db.commit()
    digests = db.query(OutboundEmail).filter(OutboundEmail.subject == "4 tickets not taken").all()
    assert len(digests) == 2 and all("Old ticket 3" in d.body and d.ticket_id is None for d in digests)


def test_console_counter(client, db, agent, requester, service_desk, monkeypatch):
    assert client.get("/api/tickets/unaccepted", headers=as_user(agent)).json() == {"enabled": False, "count": 0, "minutes": 30}
    _on(monkeypatch)
    _ticket(db, requester)
    unaccepted.run(db)
    db.commit()
    assert client.get("/api/tickets/unaccepted", headers=as_user(agent)).json() == {"enabled": True, "count": 1, "minutes": 30}
    # A requester can't ask.
    requester_user = db.query(User).filter_by(role=UserRole.requester).first()
    assert client.get("/api/tickets/unaccepted", headers=as_user(requester_user)).status_code == 403


def test_teams_channel_post_leaves_restricted_queues_to_email(db, admin, agent, requester, service_desk, monkeypatch):
    from app import teams

    _on(monkeypatch)
    monkeypatch.setattr(settings, "unaccepted_alert_via", "teams")
    monkeypatch.setattr(settings, "portal_url", "https://helpdesk.example.com")
    posts = []
    monkeypatch.setattr(teams, "send", posts.append)
    hr = Queue(name="HR", restricted=True)
    hr.owners.append(admin)
    db.add(hr)
    db.commit()
    open_one = _ticket(db, requester, "Printer jam")
    _ticket(db, requester, "Salary question", queue=hr)
    assert unaccepted.run(db).endswith("1 email(s) to technicians; posted to Teams")
    db.commit()
    text = str(posts[0])
    assert f"https://helpdesk.example.com/tickets/{open_one.id}" in text and "Printer jam" in text and "Salary" not in text
    assert [m.to_address for m in _alerts(db)] == [admin.email]  # the HR one, by email only


def test_teams_card_keeps_requester_text_plain(db, requester, service_desk, monkeypatch):
    t = _ticket(db, requester, "Urgent [verify your account](https://evil.example) **now**")
    card = str(unaccepted.teams_card([(t, 40)]))
    assert "](https://evil.example)" not in card and "\\\\[verify your account\\\\]" in card
