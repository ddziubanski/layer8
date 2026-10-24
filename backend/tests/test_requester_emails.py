# SPDX-License-Identifier: AGPL-3.0-or-later
"""Emails to requesters: editable wording, and the optional resolved and assigned emails."""
from app import configstore, services
from app.config import settings
from app.models import OutboundEmail, TicketSource, TicketStatus
from tests.conftest import as_user


def _ticket(db, requester):
    t = services.create_ticket(db, subject="Printer jam", description="", category=None, requester=requester, source=TicketSource.portal, actor=requester)
    db.commit()
    return t


def _bodies(db, ticket):
    return [m.body for m in db.query(OutboundEmail).filter_by(ticket_id=ticket.id).order_by(OutboundEmail.id)]


def test_acknowledgement_uses_the_template(db, requester, service_desk, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@example.com")
    monkeypatch.setattr(settings, "email_ack_template", "Thanks {first_name}! {ticket} is in {queue}.")
    t = _ticket(db, requester)
    first = requester.display_name.split()[0]
    assert f"Thanks {first}! #{t.id} is in {service_desk.name}." in _bodies(db, t)[0]


def test_resolved_and_assigned_emails_are_optional(client, db, admin, agent, requester, service_desk, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@example.com")
    t = _ticket(db, requester)
    h = as_user(agent)
    client.post(f"/api/tickets/{t.id}/assign", json={"assignee_id": agent.id}, headers=h)
    client.patch(f"/api/tickets/{t.id}", json={"status": "resolved"}, headers=h)
    assert len(_bodies(db, t)) == 1  # only the acknowledgement
    configstore.update(db, "requester_emails", {"email_resolved_enabled": True, "email_assigned_enabled": True}, admin)
    db.commit()
    t2 = _ticket(db, requester)
    assert client.post(f"/api/tickets/{t2.id}/assign", json={"assignee_id": agent.id}, headers=h).status_code == 200
    assert client.patch(f"/api/tickets/{t2.id}", json={"status": "resolved"}, headers=h).status_code == 200
    db.expire_all()
    bodies = _bodies(db, t2)
    assert len(bodies) == 3
    assert f"{agent.display_name} is now looking after ticket #{t2.id}" in bodies[1]
    assert f"We've marked ticket #{t2.id} as resolved" in bodies[2] and "reply to this email" in bodies[2]


def test_preview_fills_in_sample_values(client, admin, agent):
    body = {"template": "Hi {first_name}, {ticket} ({subject}) - {unknown}"}
    assert client.post("/api/settings/requester-emails/preview", json=body, headers=as_user(agent)).status_code == 403
    assert client.post("/api/settings/requester-emails/preview", json=body, headers=as_user(admin)).json() == {"body": "Hi Ana, #1042 (Laptop won't join Staff Wi-Fi) - {unknown}"}
