# SPDX-License-Identifier: AGPL-3.0-or-later
"""Email intake and outbox end to end, against a fake mailbox."""

from datetime import timedelta
from itertools import count

import pytest
from sqlalchemy import func, select

from app import storage
from app.config import settings
from app.mail import outbox
from app.mail.intake import IntakeConfig, run_intake
from app.mail.text import REPLY_MARKER
from app.mail.types import Folder, InboundAttachment, InboundMessage, OutgoingMessage
from app.models import (
    Attachment,
    Comment,
    InboundEmail,
    InboundStatus,
    OutboundEmail,
    OutboundStatus,
    Queue,
    RoutingRule,
    Ticket,
    TicketSource,
    TicketStatus,
    User,
    UserRole,
)
from app.sla import utcnow
from tests.conftest import ANSWERS, as_user, make_user

MAILBOX = "helpdesk@corp.local"
CFG = IntakeConfig.create(MAILBOX, ["corp.local"])
_ids = count(1)


class FakeMailbox:
    address = MAILBOX

    def __init__(self):
        self.inbox: list[InboundMessage] = []
        self.folders: dict[Folder, list[InboundMessage]] = {f: [] for f in Folder}
        self.sent: list[OutgoingMessage] = []
        self.fail_sends = 0
        self.fail_moves = False

    def deliver(self, **kw) -> InboundMessage:
        n = next(_ids)
        kw.setdefault("internet_message_id", f"<msg{n}@corp.local>")
        kw.setdefault("from_name", "")
        kw.setdefault("body_text", "")
        # As Exchange Online stamps mail from inside the organization.
        kw.setdefault("headers", {"x-ms-exchange-organization-authas": "Internal"})
        msg = InboundMessage(mailbox_id=f"graph-{n}", **kw)
        self.inbox.append(msg)
        return msg

    def fetch_inbox(self, limit):
        return list(self.inbox[:limit])

    def move(self, message, folder):
        if self.fail_moves:
            raise RuntimeError("Graph unavailable")
        self.inbox.remove(message)
        self.folders[folder].append(message)

    def send(self, message):
        if self.fail_sends:
            self.fail_sends -= 1
            raise RuntimeError("503 from Graph")
        self.sent.append(message)
        return f"<out{len(self.sent)}@corp.local>"


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(settings, "mailbox", MAILBOX)
    return FakeMailbox()


@pytest.fixture
def run(session_factory, mailbox):
    return lambda: run_intake(session_factory, mailbox, CFG)


@pytest.fixture
def setup(db, service_desk, slas):
    return db


def tickets(db):
    db.expire_all()
    return db.scalars(select(Ticket).order_by(Ticket.id)).all()


def outbound(db):
    db.expire_all()
    return db.scalars(select(OutboundEmail).order_by(OutboundEmail.id)).all()


def test_new_email_from_allowed_domain_creates_requester_ticket_and_ack(setup, db, mailbox, run):
    mailbox.deliver(
        from_address="RAlvarez@corp.local",
        from_name="R. Alvarez",
        subject="FW: Switch port flapping, 2nd floor",
        body_text="Desks keep losing connection.",
        raw_mime=b"From: ralvarez@corp.local\r\n\r\nraw",
        attachments=[
            InboundAttachment("error-screenshot.png", "image/png", b"\x89PNG fake", is_inline=True),
            InboundAttachment("ping-log.txt", "text/plain", b"timeout\ntimeout\n"),
            InboundAttachment("empty.txt", "text/plain", b""),
        ],
    )
    [outcome] = run()
    assert outcome.status == InboundStatus.processed

    [t] = tickets(db)
    assert t.source == TicketSource.email
    assert t.subject == "Switch port flapping, 2nd floor"
    assert t.description == "Desks keep losing connection."
    assert t.queue.name == "Service desk"
    assert t.resolution_due_at is not None
    assert t.requester.display_name == "R. Alvarez"
    assert t.requester.role == UserRole.requester

    files = db.scalars(select(Attachment).order_by(Attachment.filename)).all()
    assert [(a.filename, a.size_bytes) for a in files] == [("error-screenshot.png", 9), ("ping-log.txt", 16)]
    assert storage.read(files[1].storage_key) == b"timeout\ntimeout\n"
    assert all(a.uploaded_by_id == t.requester_id and a.comment_id is None for a in files)

    record = db.scalar(select(InboundEmail))
    assert record.ticket_id == t.id
    assert storage.read(record.raw_storage_key).endswith(b"raw")

    [ack] = outbound(db)
    assert ack.to_address == "RAlvarez@corp.local"
    assert ack.subject == f"[#{t.id}] Switch port flapping, 2nd floor"
    assert ack.body.startswith(REPLY_MARKER)
    assert mailbox.folders[Folder.processed] and not mailbox.inbox


def test_email_is_routed_like_portal(setup, db, mailbox, run):
    network = Queue(name="Network")
    db.add(network)
    db.flush()
    db.add(RoutingRule(name="switch", match_keywords=["switch"], set_queue_id=network.id))
    db.add(RoutingRule(name="email only", position=200, match_source=TicketSource.email, set_priority="low"))
    db.commit()
    mailbox.deliver(from_address="a@corp.local", subject="Switch down")
    mailbox.deliver(from_address="b@corp.local", subject="Printer")
    run()
    first, second = tickets(db)
    assert (first.queue.name, first.matched_rule_name) == ("Network", "switch")
    assert (second.priority, second.matched_rule_name) == ("low", "email only")


@pytest.mark.parametrize(
    "kw, reason",
    [
        ({"from_address": "someone@gmail.com"}, "not allowed"),
        ({"from_address": "corp.local@evil.com"}, "not allowed"),
        ({"from_address": MAILBOX.upper()}, "intake mailbox"),
        ({"from_address": "a@corp.local", "headers": {"auto-submitted": "auto-replied"}}, "Auto-Submitted"),
        ({"from_address": "a@corp.local", "headers": {"precedence": "bulk"}}, "Precedence"),
        ({"from_address": "MAILER-DAEMON@corp.local"}, "system sender"),
        ({"from_address": "a@corp.local", "headers": {"content-type": "multipart/report; report-type=delivery-status"}}, "delivery report"),
    ],
)
def test_rejected_messages_make_no_ticket_user_or_mail(setup, db, mailbox, run, kw, reason):
    mailbox.deliver(subject="hello", **kw)
    [outcome] = run()
    assert outcome.status == InboundStatus.rejected
    assert reason in outcome.reason
    assert tickets(db) == [] and outbound(db) == []
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert mailbox.folders[Folder.rejected]
    assert db.scalar(select(InboundEmail)).status == InboundStatus.rejected


def test_auto_submitted_no_is_a_person(setup, db, mailbox, run):
    mailbox.deliver(from_address="a@corp.local", subject="hi", headers={"auto-submitted": "no"})
    assert run()[0].status == InboundStatus.processed


def test_inactive_user_is_rejected_even_from_allowed_domain(setup, db, mailbox, run):
    leaver = make_user(db, "leaver@corp.local")
    leaver.is_active = False
    db.commit()
    mailbox.deliver(from_address="leaver@corp.local", subject="hi")
    assert "inactive" in run()[0].reason


def test_known_user_outside_allowed_domains_is_accepted(setup, db, mailbox, run):
    make_user(db, "contractor@vendor.com")
    mailbox.deliver(from_address="contractor@vendor.com", subject="VPN access")
    assert run()[0].status == InboundStatus.processed


def test_reply_by_tag_adds_comment_strips_quote_and_reopens(setup, db, client, agent, mailbox, run):
    mailbox.deliver(from_address="ralvarez@corp.local", subject="Printer jam")
    run()
    [t] = tickets(db)
    client.patch(f"/api/tickets/{t.id}", json={"status": "waiting_on_user"}, headers=as_user(agent))

    mailbox.deliver(
        from_address="ralvarez@corp.local",
        subject=f"RE: [#{t.id}] Printer jam",
        body_text=f"Still jammed.\n\n{REPLY_MARKER}\nWe've received your request",
        attachments=[InboundAttachment("photo.jpg", "image/jpeg", b"jpg")],
    )
    [outcome] = run()
    assert outcome.ticket_id == t.id
    assert len(tickets(db)) == 1

    [comment] = db.scalars(select(Comment)).all()
    assert comment.body == "Still jammed."
    assert comment.source == TicketSource.email and not comment.is_internal
    assert db.get(Ticket, t.id).status == TicketStatus.open
    [photo] = db.scalars(select(Attachment)).all()
    assert photo.comment_id == comment.id
    # Requester replies don't email the requester.
    assert len(outbound(db)) == 1


def test_reply_threads_by_in_reply_to_without_tag(setup, db, mailbox, run):
    mailbox.deliver(from_address="ralvarez@corp.local", subject="Printer jam")
    run()
    with_session_send = outbox.send_due(db, mailbox, utcnow())
    assert with_session_send == 1
    [ack] = outbound(db)
    assert ack.status == OutboundStatus.sent and ack.internet_message_id == "<out1@corp.local>"

    mailbox.deliver(
        from_address="ralvarez@corp.local",
        subject="Re: Printer jam",  # client dropped the tag
        headers={"in-reply-to": "<out1@corp.local>", "references": "<msg-old@x> <out1@corp.local>"},
        body_text="Any news?",
    )
    assert run()[0].ticket_id == ack.ticket_id
    assert len(tickets(db)) == 1


def test_unknown_tag_creates_new_ticket(setup, db, mailbox, run):
    mailbox.deliver(from_address="a@corp.local", subject="RE: [#99999] old thing")
    run()
    [t] = tickets(db)
    assert t.subject == "old thing"


def test_reply_to_closed_ticket_opens_follow_up(setup, db, mailbox, run):
    mailbox.deliver(from_address="a@corp.local", subject="VPN broken")
    run()
    [old] = tickets(db)
    old.status = TicketStatus.closed
    db.commit()
    mailbox.deliver(from_address="a@corp.local", subject=f"Re: [#{old.id}] VPN broken", body_text="It's back.")
    run()
    old_again, new = tickets(db)
    assert old_again.status == TicketStatus.closed
    assert new.description == f"Follow-up to closed ticket #{old.id}.\n\nIt's back."


def test_same_message_twice_makes_one_ticket(setup, db, mailbox, run):
    msg = mailbox.deliver(from_address="a@corp.local", subject="dup")
    mailbox.fail_moves = True  # processed, but left in the inbox
    run()
    assert mailbox.inbox == [msg]
    mailbox.fail_moves = False
    [outcome] = run()
    assert outcome.reason == "duplicate"
    assert len(tickets(db)) == 1
    assert mailbox.folders[Folder.processed] == [msg]


def test_failure_goes_to_failed_folder_and_retry_succeeds(db, slas, mailbox, run):
    # No default queue yet, so ticket creation fails.
    msg = mailbox.deliver(from_address="a@corp.local", subject="help", attachments=[InboundAttachment("a.txt", "text/plain", b"x")])
    [outcome] = run()
    assert outcome.folder == Folder.failed
    assert "default queue" in outcome.reason
    record = db.scalar(select(InboundEmail))
    assert record.status == InboundStatus.failed
    assert tickets(db) == []
    # The rolled-back attempt left no rows behind.
    assert db.scalar(select(func.count()).select_from(User)) == 0

    db.add(Queue(name="Service desk", is_default=True))
    db.commit()
    mailbox.folders[Folder.failed].remove(msg)
    mailbox.inbox.append(msg)  # an admin moves it back to the inbox
    assert run()[0].status == InboundStatus.processed
    assert len(tickets(db)) == 1
    db.expire_all()
    assert db.scalar(select(InboundEmail)).status == InboundStatus.processed


def test_unloadable_message_is_failed_not_blocking(setup, db, mailbox, run):
    mailbox.deliver(from_address="a@corp.local", subject="broken", load_error="GraphError: 500")
    mailbox.deliver(from_address="b@corp.local", subject="fine")
    first, second = run()
    assert first.folder == Folder.failed and second.status == InboundStatus.processed


def test_agent_reply_is_emailed_but_notes_and_email_replies_are_not(setup, db, client, agent, mailbox, run):
    mailbox.deliver(from_address="ralvarez@corp.local", subject="Printer jam")
    run()
    [t] = tickets(db)
    client.post(f"/api/tickets/{t.id}/comments", json={"body": "Checked logs", "is_internal": True}, headers=as_user(agent))
    client.post(f"/api/tickets/{t.id}/comments", json={"body": "Please power-cycle it."}, headers=as_user(agent))
    # The agent answers from Outlook instead; they already wrote to the requester.
    mailbox.deliver(from_address=agent.email, subject=f"RE: [#{t.id}] Printer jam", body_text="Sent a tech.")
    run()

    ack, reply = outbound(db)
    assert reply.to_address == "ralvarez@corp.local"
    assert "J. Park wrote:\n\nPlease power-cycle it." in reply.body
    assert reply.comment_id is not None
    bodies = [c.body for c in db.scalars(select(Comment).order_by(Comment.id))]
    assert bodies == ["Checked logs", "Please power-cycle it.", "Sent a tech."]


def test_portal_ticket_gets_ack_agent_self_ticket_does_not(setup, db, client, agent, requester, mailbox):
    client.post("/api/portal/tickets", json={"subject": "portal", **ANSWERS}, headers=as_user(requester))
    client.post("/api/tickets", json={"subject": "agent note to self"}, headers=as_user(agent))
    client.post("/api/tickets", json={"subject": "for someone", "requester_email": requester.email}, headers=as_user(agent))
    assert [o.subject.split("] ")[1] for o in outbound(db)] == ["portal", "for someone"]


def test_no_mail_queued_when_mailbox_not_configured(setup, db, client, requester):
    client.post("/api/portal/tickets", json={"subject": "portal", **ANSWERS}, headers=as_user(requester))
    assert outbound(db) == []


def test_outbox_retries_with_backoff_then_gives_up(setup, db, mailbox, run):
    mailbox.deliver(from_address="a@corp.local", subject="x")
    run()
    mailbox.fail_sends = 1
    now = utcnow()
    assert outbox.send_due(db, mailbox, now) == 0
    [row] = outbound(db)
    assert (row.status, row.attempts) == (OutboundStatus.queued, 1)
    assert "503" in row.last_error
    # Not due again until the backoff has passed.
    assert outbox.send_due(db, mailbox, now) == 0
    assert outbox.send_due(db, mailbox, now + timedelta(minutes=3)) == 1
    assert outbound(db)[0].status == OutboundStatus.sent
    assert mailbox.sent[0].headers == {"X-Helpdesk-Ticket": str(row.ticket_id)}

    mailbox.deliver(from_address="a@corp.local", subject="y")
    run()
    mailbox.fail_sends = 100
    later = now
    for _ in range(outbox.MAX_ATTEMPTS):
        later += timedelta(hours=2)
        outbox.send_due(db, mailbox, later)
    assert outbound(db)[1].status == OutboundStatus.failed


# Mailbox aliases per queue


@pytest.fixture
def hr_alias(client, db, admin, service_desk):
    r = client.post("/api/queues", json={"name": "Human Resources", "email_addresses": ["HR@corp.com", "hr@corp.com", " people@corp.com "]}, headers=as_user(admin))
    assert r.status_code == 201, r.text
    assert r.json()["email_addresses"] == ["hr@corp.com", "people@corp.com"]
    return r.json()


def test_email_to_a_queue_alias_files_into_that_queue(setup, db, mailbox, run, hr_alias):
    mailbox.deliver(from_address="a@corp.local", subject="Payslip", to_addresses=["helpdesk@corp.local", "hr@corp.com"])
    mailbox.deliver(from_address="c@corp.local", subject="Printer", to_addresses=["helpdesk@corp.local"])
    run()
    hr, desk = tickets(db)
    assert (hr.queue.name, hr.requested_queue_id) == ("Human Resources", hr_alias["id"])
    assert desk.queue.name == "Service desk" and desk.requested_queue_id is None


def test_cc_to_an_alias_counts(setup, db, mailbox, run, hr_alias):
    mailbox.deliver(from_address="a@corp.local", subject="Leave", to_addresses=["manager@corp.local", "people@corp.com"])
    run()
    [t] = tickets(db)
    assert t.queue.name == "Human Resources"


def test_alias_choice_follows_routing_rules_like_the_portal(setup, db, mailbox, run, hr_alias):
    tier2 = Queue(name="Tier 2")
    db.add(tier2)
    db.flush()
    db.add(RoutingRule(name="keyword", match_keywords=["payslip"], set_queue_id=tier2.id, set_priority="high"))
    db.commit()
    mailbox.deliver(from_address="a@corp.local", subject="Payslip wrong", to_addresses=["hr@corp.com"])
    run()
    [t] = tickets(db)
    # Ordinary rules can't move a chosen queue, but may set priority.
    assert (t.queue.name, t.priority) == ("Human Resources", "high")


def test_an_alias_belongs_to_one_queue(client, admin, hr_alias):
    r = client.post("/api/queues", json={"name": "People", "email_addresses": ["HR@corp.com"]}, headers=as_user(admin))
    assert r.status_code == 422 and "already files into Human Resources" in r.json()["detail"]
    ok = client.patch(f"/api/queues/{hr_alias['id']}", json={"email_addresses": ["hr@corp.com"]}, headers=as_user(admin))
    assert ok.status_code == 200 and ok.json()["email_addresses"] == ["hr@corp.com"]


def test_a_ticket_number_alone_doesnt_let_strangers_post(setup, db, mailbox, run):
    mailbox.deliver(from_address="a@corp.local", subject="Payroll question")
    run()
    [mine] = tickets(db)
    # Someone else at the company quotes the number: a ticket of their own, nothing on a@'s.
    mailbox.deliver(from_address="mallory@corp.local", subject=f"Re: [#{mine.id}] Payroll question", body_text="Click here")
    run()
    mine_again, theirs = tickets(db)
    assert theirs.requester.email == "mallory@corp.local" and "Click here" in theirs.description
    db.expire_all()
    assert all("Click here" not in c.body for c in db.scalars(select(Comment).where(Comment.ticket_id == mine.id)))
    # Someone the desk emailed about it (a CC) may reply.
    db.add(OutboundEmail(to_address="boss@corp.local", ticket_id=mine.id, subject="x", body="x"))
    db.commit()
    mailbox.deliver(from_address="boss@corp.local", subject=f"Re: [#{mine.id}] Payroll question", body_text="Approved")
    run()
    db.expire_all()
    assert len(tickets(db)) == 2 and any(c.body == "Approved" for c in db.scalars(select(Comment).where(Comment.ticket_id == mine.id)))


def test_commands_dont_run_on_a_new_ticket_in_a_queue_the_technician_cant_see(setup, db, mailbox, run, hr_alias, agent, monkeypatch):
    monkeypatch.setattr(settings, "email_commands", True)
    from app.models import Queue

    db.get(Queue, hr_alias["id"]).restricted = True
    db.commit()
    mailbox.deliver(from_address=agent.email, subject="About someone", to_addresses=["hr@corp.com"], body_text="#priority critical\nPlease look")
    run()
    [t] = tickets(db)
    assert t.queue.name == "Human Resources" and t.priority.value != "critical"
    notice = [m for m in outbound(db) if m.to_address == agent.email and m.subject.startswith("Not done")]
    assert notice and notice[0].ticket_id is None
