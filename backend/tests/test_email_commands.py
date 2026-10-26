# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tickets Anywhere: technicians act on tickets with #commands in email."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.mail import commands
from app.models import Comment, FieldOption, OutboundEmail, Queue, Ticket, TicketStatus
from tests.conftest import make_user
from tests.test_mail_intake import FakeMailbox, outbound, tickets  # noqa: F401  (fixtures below reuse these)
from tests.test_mail_intake import mailbox, run, setup  # noqa: F401


def test_parse_keeps_text_and_pulls_commands():
    p = commands.parse("Swapped the toner.\n#close Fixed\n  #assign me\nIssue #1234 was similar.\n#priority high")
    assert p.text == "Swapped the toner.\nIssue #1234 was similar."
    assert p.commands == [("close", "Fixed"), ("assign", "me"), ("priority", "high")]


def first_ticket(db, mailbox, run, requester_email="ralvarez@corp.local"):
    mailbox.deliver(from_address=requester_email, subject="Printer jammed", body_text="Again.")
    run()
    return tickets(db)[0]


def test_reply_with_commands(setup, db, mailbox, run, agent, monkeypatch):
    monkeypatch.setattr(settings, "require_resolution_type", False)
    monkeypatch.setattr(settings, "require_closure_reason", False)
    net = Queue(name="Network")
    db.add(net)
    db.commit()
    t = first_ticket(db, mailbox, run)
    mailbox.deliver(from_address=agent.email, subject=f"RE: [#{t.id}] Printer jammed", body_text="#assign me\n#priority high\n#queue Network\n#internal\nChecked the fuser.")
    run()
    db.expire_all()
    t = db.get(Ticket, t.id)
    assert (t.assignee_id, t.priority, t.queue.name) == (agent.id, "high", "Network")
    notes = db.query(Comment).filter_by(ticket_id=t.id).order_by(Comment.id).all()
    reply = next(c for c in notes if c.body == "Checked the fuser.")
    assert reply.is_internal  # #internal: not sent to the requester
    summary = next(c.body for c in notes if c.body.startswith("By email:"))
    assert all(part in summary for part in ("assigned to", "priority high", "moved to Network", "kept as an internal note"))
    mailbox.deliver(from_address=agent.email, subject=f"RE: [#{t.id}] Printer jammed", body_text="#close")
    run()
    db.expire_all()
    assert db.get(Ticket, t.id).status == TicketStatus.closed


def test_mistakes_come_back_by_email_and_requesters_cant_use_commands(setup, db, mailbox, run, agent):
    t = first_ticket(db, mailbox, run)
    mailbox.deliver(from_address=agent.email, subject=f"RE: [#{t.id}] Printer jammed", body_text="#prioritty high\n#assign nobody-here")
    run()
    back = [m for m in outbound(db) if m.to_address == agent.email and m.subject.startswith("Not done:")]
    assert back and "#prioritty high: unknown command" in back[0].body and "no technician called 'nobody-here'" in back[0].body
    # A requester's #lines are just text.
    mailbox.deliver(from_address="ralvarez@corp.local", subject=f"RE: [#{t.id}] Printer jammed", body_text="#close\nPlease hurry")
    run()
    db.expire_all()
    assert db.get(Ticket, t.id).status != TicketStatus.closed
    assert db.query(Comment).filter(Comment.body == "#close\nPlease hurry").count() == 1


def test_technician_logs_a_ticket_for_someone(setup, db, mailbox, run, agent, monkeypatch):
    rosa = make_user(db, "rosa@corp.local", name="Rosa")
    db.commit()
    mailbox.deliver(from_address=agent.email, subject="Rosa's monitor flickers", body_text="#create\n#requester rosa@corp.local\n#assign me\nShe called it in.")
    run()
    t = tickets(db)[-1]
    assert (t.requester_id, t.assignee_id, t.description) == (rosa.id, agent.id, "She called it in.")
    assert db.query(Comment).filter(Comment.ticket_id == t.id, Comment.body.like("By email: logged for Rosa%")).count() == 1
    # Switched off: the lines stay as text.
    monkeypatch.setattr(settings, "email_commands", False)
    mailbox.deliver(from_address=agent.email, subject=f"RE: [#{t.id}] x", body_text="#unassign")
    run()
    db.expire_all()
    assert db.get(Ticket, t.id).assignee_id == agent.id


def test_forged_mail_cant_use_commands(setup, db, mailbox, run, agent, requester):
    mailbox.deliver(from_address=requester.email, subject="Printer jammed", body_text="Again.")
    run()
    [t] = tickets(db)
    # From: a technician, but Exchange didn't vouch for it (it came from outside, spoofed).
    mailbox.deliver(from_address=agent.email, subject=f"RE: [#{t.id}] Printer jammed", body_text="#close\nDone.", headers={"x-ms-exchange-organization-authas": "Anonymous"})
    run()
    db.expire_all()
    # Not staff, not the requester: it can't close the ticket or post on it; it's a ticket of its own.
    original, forged = tickets(db)
    assert original.id == t.id and original.status.value != "closed"
    assert not any("Done." in c.body for c in db.scalars(select(Comment).where(Comment.ticket_id == original.id)))
    assert forged.requester_id == agent.id
