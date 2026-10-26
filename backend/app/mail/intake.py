# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turns mailbox messages into tickets and comments.

Mail goes through the same services as the portal and API, so routing, SLA and
history are identical. Each message is processed in its own transaction and
recorded by Message-ID, so retries and crashes never create duplicates.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import services, sla, storage
from app.directory.sync import find_by_address
from app.config import settings
from app.mail import commands
from app.mail.text import clean_subject, find_ticket_id, strip_quoted, ticket_tag
from app.mail.types import Folder, InboundMessage, Mailbox
from app.models import (
    InboundEmail,
    InboundStatus,
    OutboundEmail,
    Queue,
    Ticket,
    TicketSource,
    TicketStatus,
    User,
    UserRole,
)

log = logging.getLogger(__name__)

SYSTEM_SENDERS = {"mailer-daemon", "postmaster"}


@dataclass(frozen=True)
class IntakeConfig:
    mailbox_address: str
    allowed_domains: frozenset[str]

    @classmethod
    def create(cls, mailbox_address: str, allowed_domains: list[str]) -> "IntakeConfig":
        return cls(mailbox_address.lower(), frozenset(d.lower().lstrip("@") for d in allowed_domains))


@dataclass(frozen=True)
class Outcome:
    folder: Folder
    status: InboundStatus
    ticket_id: int | None = None
    reason: str | None = None


def automated_reason(msg: InboundMessage) -> str | None:
    """Why a message looks machine-generated (auto-reply, bounce, bulk), or None.

    Answering these is how two systems end up emailing each other forever.
    """
    h = msg.headers
    auto = h.get("auto-submitted", "").strip().lower()
    if auto and auto != "no":
        return f"Auto-Submitted: {auto}"
    precedence = h.get("precedence", "").strip().lower()
    if precedence in {"bulk", "junk", "list", "auto_reply"}:
        return f"Precedence: {precedence}"
    if "x-autoreply" in h or "x-autorespond" in h:
        return "auto-reply header"
    if h.get("content-type", "").lower().startswith("multipart/report"):
        return "delivery report"
    if msg.from_address.split("@")[0].lower() in SYSTEM_SENDERS:
        return "system sender"
    return None


def _find_user(session: Session, email: str) -> User | None:
    # Primary address or any alias (AD proxyAddresses), so mail from an alias reaches the right person.
    return find_by_address(session, [email])


def _resolve_sender(session: Session, msg: InboundMessage, cfg: IntakeConfig) -> User | str:
    """The sending user, created if their domain is allowed, or the reason they're rejected."""
    email = msg.from_address.strip()
    user = _find_user(session, email)
    if user is not None:
        return user if user.is_active else "sender is an inactive user"
    domain = email.rsplit("@", 1)[-1].lower()
    if domain not in cfg.allowed_domains:
        return f"sender domain {domain!r} is not allowed"
    user = User(email=email, display_name=msg.from_name.strip() or email.split("@")[0], role=UserRole.requester)
    session.add(user)
    session.flush()
    return user


def _thread_ticket(session: Session, msg: InboundMessage) -> tuple[Ticket | None, bool]:
    """The ticket a message belongs to, following merges (a reply to a merged ticket lands on
    the one it was merged into), and whether it answers a staff notice."""
    ticket, notice = _tagged_or_referenced(session, msg)
    for _ in range(20):
        if ticket is None or ticket.merged_into_id is None:
            break
        ticket = session.get(Ticket, ticket.merged_into_id)
    return ticket, notice


def _tagged_or_referenced(session: Session, msg: InboundMessage) -> tuple[Ticket | None, bool]:
    """Subject tag first, then In-Reply-To/References (preserving staff-notice threading)."""
    refs = msg.in_reply_to
    sent = (
        session.execute(
            select(OutboundEmail.ticket_id, OutboundEmail.is_staff_notice)
            .where(OutboundEmail.internet_message_id.in_(refs))
            .limit(1)
        ).first()
        if refs
        else None
    )
    tagged = find_ticket_id(msg.subject)
    if tagged is not None:
        ticket = session.get(Ticket, tagged)
        if ticket is not None:
            notice = bool(sent and sent.ticket_id == ticket.id and sent.is_staff_notice)
            return ticket, notice
    if not refs:
        return None, False
    if sent and sent.ticket_id is not None:
        return session.get(Ticket, sent.ticket_id), sent.is_staff_notice
    if sent:
        # A reply to mail that isn't about a ticket (e.g. an emailed report): a new message.
        return None, False
    ticket_id = session.scalar(
        select(InboundEmail.ticket_id)
        .where(InboundEmail.internet_message_id.in_(refs), InboundEmail.ticket_id.is_not(None))
        .limit(1)
    )
    return (session.get(Ticket, ticket_id) if ticket_id else None), False


def _commands_allowed(msg: InboundMessage) -> bool:
    """Email commands are on and, unless that check is off, Exchange vouches the sender is internal."""
    if not settings.email_commands:
        return False
    return not settings.email_commands_internal_only or msg.headers.get("x-ms-exchange-organization-authas", "").strip().lower() == "internal"


def _may_reply(session: Session, ticket: Ticket, sender: User, *, unspoofed: bool = True) -> bool:
    """Whether a message from sender may join this ticket: its requester, staff who can see its
    queue, or someone the desk has emailed about it (a CC, a manager's notice). Anyone else
    quoting the number starts a ticket of their own."""
    if sender.id == ticket.requester_id or (unspoofed and sender.role in services.AGENT_ROLES and ticket.queue.can_view(sender)):
        return True
    return session.scalar(
        select(OutboundEmail.id).where(OutboundEmail.ticket_id == ticket.id, func.lower(OutboundEmail.to_address) == sender.email.lower()).limit(1)
    ) is not None


def _report_commands(session: Session, ticket: Ticket, sender: User, msg: InboundMessage, result: "commands.Result") -> None:
    """Note what the email did on the ticket; tell the technician what couldn't be done."""
    if result.done:
        services.add_comment(
            session, ticket, sender, body=f"By email: {'; '.join(result.done)}.", is_internal=True, source=TicketSource.email, as_staff=True,
        )
    if result.errors and sender.email and "@" in sender.email:
        lines = ["Some of the commands in your email weren't done:", ""] + [f"  {e}" for e in result.errors]
        lines += ["", commands.HELP]
        session.add(OutboundEmail(to_address=sender.email, ticket_id=ticket.id, is_staff_notice=True, subject=f"Not done: {ticket_tag(ticket.id, ticket.created_at)} {msg.subject[:200]}", body="\n".join(lines)))


def _save_attachments(session: Session, msg: InboundMessage, ticket: Ticket, sender: User, comment) -> None:
    for att in msg.attachments:
        if att.data:
            services.add_attachment(
                session, ticket, sender, filename=att.filename, content_type=att.content_type, data=att.data, comment=comment
            )


def _record(session: Session, msg: InboundMessage, outcome: Outcome, **fields) -> None:
    row = session.scalar(select(InboundEmail).where(InboundEmail.internet_message_id == msg.internet_message_id))
    if row is None:
        row = InboundEmail(internet_message_id=msg.internet_message_id)
        session.add(row)
    row.from_address = msg.from_address[:320]
    row.subject = msg.subject[:998]
    row.received_at = msg.received_at
    row.status = outcome.status
    row.reason = outcome.reason
    row.ticket_id = outcome.ticket_id
    row.processed_at = sla.utcnow()
    for name, value in fields.items():
        setattr(row, name, value)


def _queue_for_addresses(session: Session, addresses: list[str]) -> Queue | None:
    """The first active queue claiming one of these addresses, in the order they appear."""
    for address in addresses:
        queue = session.scalar(select(Queue).where(Queue.is_active, Queue.email_addresses.any(address.lower())))
        if queue is not None:
            return queue
    return None


def process_message(session: Session, msg: InboundMessage, cfg: IntakeConfig) -> Outcome:
    """Apply one message to the database. The caller commits, then files the message."""
    if msg.load_error:
        raise RuntimeError(f"could not load message: {msg.load_error}")
    earlier = session.scalar(select(InboundEmail).where(InboundEmail.internet_message_id == msg.internet_message_id))
    if earlier is not None and earlier.status != InboundStatus.failed:
        # Already handled; we probably crashed before moving it out of the inbox.
        folder = Folder.processed if earlier.status == InboundStatus.processed else Folder.rejected
        return Outcome(folder, earlier.status, earlier.ticket_id, "duplicate")

    def reject(reason: str) -> Outcome:
        outcome = Outcome(Folder.rejected, InboundStatus.rejected, reason=reason)
        _record(session, msg, outcome)
        return outcome

    if msg.from_address.lower() == cfg.mailbox_address:
        return reject("sent by the intake mailbox itself")
    if reason := automated_reason(msg):
        return reject(f"automated message ({reason})")
    sender = _resolve_sender(session, msg, cfg)
    if isinstance(sender, str):
        return reject(sender)

    unspoofed = not settings.email_commands_internal_only or msg.headers.get("x-ms-exchange-organization-authas", "").strip().lower() != "anonymous"
    ticket, staff_reply = _thread_ticket(session, msg)
    if ticket is not None and not _may_reply(session, ticket, sender, unspoofed=unspoofed):
        ticket, staff_reply = None, False
    comment = None
    is_staff = sender.role in services.AGENT_ROLES
    if ticket is not None and ticket.status != TicketStatus.closed:
        body = strip_quoted(msg.body_text) or "(no message text)"
        # Staff only if they may see this queue (and Exchange didn't flag the mail as external/anonymous).
        staff = is_staff and unspoofed and ticket.queue.can_view(sender)
        # Anyone authorized to answer a staff/escalation notice is talking to staff, not the requester.
        # Never for mail Exchange flagged as external/anonymous: a forged reply to a notice isn't staff talk.
        internal = bool(staff_reply and unspoofed and (staff or sender.id != ticket.requester_id))
        parsed = commands.parse(body) if staff and _commands_allowed(msg) else commands.Parsed(body)
        result = commands.apply(session, ticket, sender, parsed.commands) if parsed.commands else None
        if result is not None and result.internal:
            internal = True
        if parsed.text or not parsed.commands:
            comment = services.add_comment(session, ticket, sender, body=parsed.text or "(no message text)", is_internal=internal, source=TicketSource.email, as_staff=staff or internal)
        if result is not None:
            _report_commands(session, ticket, sender, msg, result)
    else:
        description = msg.body_text.strip()
        if ticket is not None:
            # Closed tickets stay closed; a reply opens a new one that points back.
            description = f"Follow-up to closed ticket #{ticket.id}.\n\n{strip_quoted(msg.body_text)}"
        parsed = commands.parse(description) if is_staff and _commands_allowed(msg) else commands.Parsed(description)
        requester = sender
        for name, arg in parsed.commands:
            if name == "requester":
                found = _find_user(session, arg) if arg else None
                requester = found or requester
        ticket = services.create_ticket(
            session,
            subject=clean_subject(msg.subject)[:300],
            description=parsed.text,
            category=None,
            requester=requester,
            source=TicketSource.email,
            actor=sender,
            # Sent to a queue's alias (hr@...): like choosing that queue in the portal.
            requested_queue=_queue_for_addresses(session, msg.to_addresses),
        )
        if parsed.commands and not ticket.queue.can_view(sender):
            # Routing put it in a queue this technician can't see: no commands there.
            # Not tied to the ticket, so it doesn't make the sender someone who may reply to it.
            session.add(OutboundEmail(
                to_address=sender.email, ticket_id=None, is_staff_notice=True, subject=f"Not done: {msg.subject[:200]}",
                body="The commands in your email weren't run: the request went to a queue you can't see. It was logged as a ticket there.",
            ))
        elif parsed.commands:
            result = commands.apply(session, ticket, sender, parsed.commands)
            if requester is sender and any(n == "requester" for n, _ in parsed.commands):
                result.errors.append("#requester: no one with that address here; you're the requester")
            elif requester is not sender:
                result.done.insert(0, f"logged for {requester.display_name}")
            _report_commands(session, ticket, sender, msg, result)

    _save_attachments(session, msg, ticket, sender, comment)
    raw_key = storage.save(msg.raw_mime).key if msg.raw_mime else None
    outcome = Outcome(Folder.processed, InboundStatus.processed, ticket.id)
    _record(session, msg, outcome, comment_id=comment.id if comment else None, raw_storage_key=raw_key)
    return outcome


def run_intake(session_factory: Callable[[], Session], mailbox: Mailbox, cfg: IntakeConfig, limit: int = 25) -> list[Outcome]:
    """Process a batch from the inbox and file each message. Returns what happened to each."""
    outcomes = []
    for msg in mailbox.fetch_inbox(limit):
        with session_factory() as session:
            try:
                outcome = process_message(session, msg, cfg)
                session.commit()
            except Exception as exc:
                session.rollback()
                log.exception("Failed to process message %s from %s", msg.internet_message_id, msg.from_address)
                outcome = Outcome(Folder.failed, InboundStatus.failed, reason=f"{type(exc).__name__}: {exc}"[:2000])
                _record(session, msg, outcome)
                session.commit()
        try:
            mailbox.move(msg, outcome.folder)
        except Exception:
            # It stays in the inbox and is picked up again; the Message-ID check stops a second ticket.
            log.exception("Could not move message %s to %s", msg.internet_message_id, outcome.folder)
        outcomes.append(outcome)
    return outcomes
