# SPDX-License-Identifier: AGPL-3.0-or-later
"""Satisfaction surveys: a random share of solved tickets, once per ticket and per person per cooldown, answered by link or in the portal."""

import re
from datetime import timedelta

import pytest

from app import sla, surveys
from app.config import settings
from app.models import OutboundEmail, Queue, Ticket, TicketEvent, TicketSource, TicketStatus, TicketSurvey
from tests.conftest import as_user, make_user


@pytest.fixture
def setup(db, monkeypatch, agent, requester):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@example.com")
    monkeypatch.setattr(settings, "portal_url", "https://helpdesk.corp.example")
    monkeypatch.setattr(settings, "survey_percent", 100)
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.commit()
    return q


def new_ticket(db, q, requester, **over):
    t = Ticket(subject="Printer jammed", source=TicketSource.portal, queue_id=q.id, requester_id=requester.id, status=TicketStatus.open, **over)
    db.add(t)
    db.commit()
    return t


def solve(client, agent, t, status="resolved"):
    r = client.patch(f"/api/tickets/{t.id}", json={"status": status}, headers=as_user(agent))
    assert r.status_code == 200, r.text


def token_from(db, t) -> str:
    mail = db.query(OutboundEmail).filter_by(ticket_id=t.id).order_by(OutboundEmail.id.desc()).first()
    return re.search(r"/survey/([\w-]+)\?r=5", mail.body).group(1)


def test_solved_ticket_gets_a_survey_answered_by_link(client, db, setup, agent, requester):
    t = new_ticket(db, setup, requester, assignee_id=agent.id)
    solve(client, agent, t)
    s = db.query(TicketSurvey).one()
    assert (s.ticket_id, s.requester_id, s.assignee_id, s.rating) == (t.id, requester.id, agent.id, None)
    mail = db.query(OutboundEmail).filter_by(ticket_id=t.id).one()
    assert mail.to_address == requester.email and settings.survey_question in mail.body and "5 Very happy: https://helpdesk.corp.example/survey/" in mail.body
    token = token_from(db, t)
    assert s.token_hash == surveys.hash_token(token) and token not in str(s.token_hash)

    # No sign-in needed: the link is the key.
    page = client.get(f"/api/surveys/answer/{token}").json()
    assert page["subject"] == "Printer jammed" and page["rating"] is None and page["ratings"]["5"] == "Very happy"
    assert client.post(f"/api/surveys/answer/{token}", json={"rating": 2, "comment": "Took three days"}).json()["rating"] == 2
    # Changing your mind while the link works is fine.
    assert client.post(f"/api/surveys/answer/{token}", json={"rating": 3}).json()["rating"] == 3
    events = db.query(TicketEvent).filter_by(ticket_id=t.id).filter(TicketEvent.type.in_(["survey_sent", "survey_answered"])).order_by(TicketEvent.id).all()
    assert [e.type for e in events] == ["survey_sent", "survey_answered", "survey_answered"] and events[1].data["comment"] == "Took three days"
    assert client.get("/api/surveys/answer/not-a-token").status_code == 404

    # Expired links don't take answers.
    s.expires_at = sla.utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.post(f"/api/surveys/answer/{token}", json={"rating": 5}).status_code == 409

    # Results for staff.
    res = client.get("/api/surveys", headers=as_user(agent)).json()
    assert (res["sent"], res["answered"], res["average"], res["by_rating"]["3"]) == (1, 1, 3.0, 1)
    assert res["recent"][0]["assignee"] == agent.display_name and res["recent"][0]["comment"] == ""
    assert client.get("/api/surveys", headers=as_user(requester)).status_code == 403


def test_who_gets_one(client, db, setup, agent, requester, monkeypatch):
    # Reopened and solved again: still one survey.
    t = new_ticket(db, setup, requester)
    solve(client, agent, t)
    solve(client, agent, t, "open")
    solve(client, agent, t, "closed")
    assert db.query(TicketSurvey).count() == 1
    # The same person isn't asked again within the cooldown...
    solve(client, agent, new_ticket(db, setup, requester))
    assert db.query(TicketSurvey).count() == 1
    # ...but someone else is; child tasks never are.
    other = make_user(db, "tnguyen@corp.local", name="Tam Nguyen")
    db.commit()
    parent = new_ticket(db, setup, other)
    solve(client, agent, new_ticket(db, setup, other, parent_id=parent.id))
    assert db.query(TicketSurvey).count() == 1
    solve(client, agent, parent)
    assert db.query(TicketSurvey).count() == 2
    # With no cooldown, the same person can be asked again.
    monkeypatch.setattr(settings, "survey_cooldown_days", 0)
    solve(client, agent, new_ticket(db, setup, requester))
    assert db.query(TicketSurvey).count() == 3


def test_the_share_is_random(db, setup, requester, monkeypatch):
    monkeypatch.setattr(settings, "survey_percent", 25)
    monkeypatch.setattr(settings, "survey_cooldown_days", 0)
    rolls = iter([0.10, 0.30, 0.249, 0.99])
    made = [surveys.maybe_send(db, new_ticket(db, setup, requester), roll=lambda: next(rolls)) is not None for _ in range(4)]
    assert made == [True, False, True, False]
    monkeypatch.setattr(settings, "survey_percent", 0)
    assert surveys.maybe_send(db, new_ticket(db, setup, requester), roll=lambda: 0.0) is None


def test_answer_in_the_portal(client, db, setup, agent, requester):
    t = new_ticket(db, setup, requester)
    solve(client, agent, t)
    assert client.get(f"/api/portal/tickets/{t.id}/survey", headers=as_user(requester)).json()["rating"] is None
    assert client.post(f"/api/portal/tickets/{t.id}/survey", json={"rating": 5, "comment": "Quick!"}, headers=as_user(requester)).json()["rating"] == 5
    # Not someone else's.
    nosy = make_user(db, "nosy@corp.local")
    db.commit()
    assert client.get(f"/api/portal/tickets/{t.id}/survey", headers=as_user(nosy)).status_code == 404


def test_off_by_default(client, db, agent, requester, monkeypatch):
    monkeypatch.setattr(settings, "survey_percent", 0)
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.commit()
    solve(client, agent, new_ticket(db, q, requester))
    assert db.query(TicketSurvey).count() == 0


def test_low_ratings_are_followed_up(client, db, setup, agent, requester):
    manager = make_user(db, "lead@corp.local", name="Desk Lead")
    from app.models import Comment, UserRole

    manager.role = UserRole.agent
    setup.manager_users = [manager]
    db.commit()
    t = new_ticket(db, setup, requester, assignee_id=agent.id)
    solve(client, agent, t)
    token = token_from(db, t)
    before = db.query(OutboundEmail).count()
    client.post(f"/api/surveys/answer/{token}", json={"rating": 4})
    assert db.query(OutboundEmail).count() == before and db.query(Comment).filter_by(ticket_id=t.id).count() == 0
    client.post(f"/api/surveys/answer/{token}", json={"rating": 2, "comment": "Nobody called back"})
    note = db.query(Comment).filter_by(ticket_id=t.id).one()
    assert note.is_internal and "rated this 2/5 (Unhappy): \"Nobody called back\"" in note.body
    alerts = db.query(OutboundEmail).filter(OutboundEmail.is_staff_notice).all()
    assert sorted(m.to_address for m in alerts) == sorted([agent.email, manager.email]) and alerts[0].subject.startswith("Low satisfaction rating on")
    # Already low: lowering it further doesn't alert again.
    client.post(f"/api/surveys/answer/{token}", json={"rating": 1})
    assert db.query(OutboundEmail).filter(OutboundEmail.is_staff_notice).count() == 2
