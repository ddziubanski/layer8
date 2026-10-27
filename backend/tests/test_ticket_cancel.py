# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cancellation is a retained, audited closure, never deletion or a satisfaction survey."""
import pytest
from sqlalchemy import select

from app import sla, surveys
from app.config import settings
from app.models import Comment, FieldList, FieldOption, Ticket, TicketEvent, TicketSource, TicketSurvey
from tests.conftest import as_user


@pytest.fixture
def setup(db, requester, service_desk):
    resolution = FieldOption(list=FieldList.resolution_type, label="Not applicable", position=0)
    closure = FieldOption(list=FieldList.closure_reason, label="Canceled", position=0)
    ticket = Ticket(subject="Example canceled request", requester_id=requester.id, queue_id=service_desk.id, source=TicketSource.portal)
    db.add_all([resolution, closure, ticket]); db.commit()
    return ticket, {"reason": "Request is no longer needed", "resolution_type_id": resolution.id, "closure_reason_id": closure.id}


def test_cancel_retains_record_notes_and_audits_reason_without_survey(client, db, agent, setup, monkeypatch):
    ticket, body = setup
    survey_calls = []
    monkeypatch.setattr(surveys, "maybe_send", lambda *args: survey_calls.append(True))
    monkeypatch.setattr(settings, "survey_percent", 100)
    db.add(Comment(ticket_id=ticket.id, author_id=agent.id, body="Existing note", is_internal=True, source=TicketSource.agent)); db.commit()
    response = client.post(f"/api/tickets/{ticket.id}/cancel", json=body, headers=as_user(agent))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
    db.expire_all()
    saved = db.get(Ticket, ticket.id)
    assert saved is not None and saved.closed_at is not None and saved.status in sla.CLOSED_STATUSES
    notes = db.scalars(select(Comment).where(Comment.ticket_id == ticket.id)).all()
    assert len(notes) == 2 and all(note.is_internal and not note.emailed for note in notes)
    assert any(note.body == f"Canceled ticket: {body['reason']}" for note in notes)
    assert not db.scalars(select(TicketSurvey).where(TicketSurvey.ticket_id == ticket.id)).all()
    assert not survey_calls
    events = db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id)).all()
    assert any(event.data.get("canceled") and event.data["cancellation_reason"] == body["reason"] for event in events)
    assert client.post(f"/api/tickets/{ticket.id}/cancel", json=body, headers=as_user(agent)).status_code == 409
    db.expire_all()
    assert len(db.scalars(select(Comment).where(Comment.ticket_id == ticket.id)).all()) == 2


def test_cancel_preserves_closing_requirements_and_open_child_guard(client, db, agent, setup, requester, service_desk):
    ticket, body = setup
    path = f"/api/tickets/{ticket.id}/cancel"
    assert client.post(path, json={"reason": "Not needed"}, headers=as_user(agent)).status_code == 409
    child = Ticket(subject="Example open child", requester_id=requester.id, queue_id=service_desk.id, parent_id=ticket.id, source=TicketSource.portal)
    db.add(child); db.commit()
    assert client.post(path, json=body, headers=as_user(agent)).status_code == 409
    db.expire_all()
    assert db.get(Ticket, ticket.id).status not in sla.CLOSED_STATUSES
    assert not db.scalars(select(Comment).where(Comment.ticket_id == ticket.id)).all()


def test_requesters_and_staff_outside_the_queue_cannot_cancel(client, db, agent, requester, admin, setup):
    ticket, body = setup
    path = f"/api/tickets/{ticket.id}/cancel"
    assert client.post(path, json=body, headers=as_user(requester)).status_code == 403
    ticket.queue.restricted = True; db.commit()
    assert client.post(path, json=body, headers=as_user(agent)).status_code == 404
    assert client.post(path, json=body, headers=as_user(admin)).status_code == 200


@pytest.mark.parametrize("reason", ["", "   ", "x" * 2001])
def test_cancel_reason_is_required(client, agent, setup, reason):
    ticket, body = setup
    assert client.post(f"/api/tickets/{ticket.id}/cancel", json={**body, "reason": reason}, headers=as_user(agent)).status_code == 422
