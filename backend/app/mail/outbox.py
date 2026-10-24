# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outgoing ticket email: queued by the services layer, sent by the worker."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app import placeholders
from app.mail.text import REPLY_MARKER, ticket_tag
from app import storage
from app.mail.types import Mailbox, OutgoingAttachment, OutgoingMessage
from app.models import Comment, OutboundEmail, OutboundStatus, Ticket, User

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
TICKET_HEADER = "X-Helpdesk-Ticket"


def _footer(ticket: Ticket) -> str:
    lines = [
        "",
        "--",
        f"Reply to this email to add to your ticket. Please keep {ticket_tag(ticket.id, ticket.created_at)} in the subject.",
    ]
    if link := _ticket_link(ticket):
        lines.append(f"Track your tickets: {link}")
    if settings.email_footer:
        lines += ["", settings.email_footer]
    return "\n".join(lines)


def _ticket_link(ticket: Ticket) -> str | None:
    return f"{settings.portal_url.rstrip('/')}/tickets/{ticket.id}" if settings.portal_url else None


def _queue(session: Session, ticket: Ticket, to: User, body: str, comment: Comment | None = None) -> bool:
    """Queue mail to a requester. False when it can't go (no mailbox set up, inactive user)."""
    if not settings.mailbox or not to.is_active:
        return False
    session.add(
        OutboundEmail(
            ticket_id=ticket.id,
            comment_id=comment.id if comment else None,
            to_address=to.email,
            subject=f"{ticket_tag(ticket.id, ticket.created_at)} {ticket.subject}",
            body=f"{REPLY_MARKER}\n\n{body}\n{_footer(ticket)}",
        )
    )
    return True


def _queue_staff(session: Session, ticket: Ticket, to: User | str, subject: str, body: str) -> None:
    """A notice to staff (a user, or a bare address). No [#id] tag, so a reply can't land on the
    ticket as a public comment; replies thread by In-Reply-To and are filed as internal notes."""
    if not settings.mailbox or (isinstance(to, User) and not to.is_active):
        return
    address = to.email if isinstance(to, User) else to
    lines = [REPLY_MARKER, "", body, "", "--", "Replies to this email are added to the ticket as internal notes."]
    if link := _ticket_link(ticket):
        lines.append(f"Open the ticket: {link}")
    session.add(
        OutboundEmail(
            ticket_id=ticket.id,
            to_address=address,
            subject=subject,
            body="\n".join(lines),
            is_staff_notice=True,
        )
    )


def ticket_created(session: Session, ticket: Ticket) -> None:
    _queue(session, ticket, ticket.requester, placeholders.fill(settings.email_ack_template, placeholders.values(ticket)))


def ticket_resolved(session: Session, ticket: Ticket, actor: User | None) -> bool:
    """Settings > Requester emails > Resolved, when it's on (not when requesters resolve their own)."""
    if not settings.email_resolved_enabled or (actor is not None and actor.id == ticket.requester_id):
        return False
    return _queue(session, ticket, ticket.requester, placeholders.fill(settings.email_resolved_template, placeholders.values(ticket, actor)))


def ticket_assigned(session: Session, ticket: Ticket) -> bool:
    """Settings > Requester emails > Assigned, when it's on and someone other than the requester has it."""
    if not settings.email_assigned_enabled or ticket.assignee is None or ticket.assignee_id == ticket.requester_id:
        return False
    return _queue(session, ticket, ticket.requester, placeholders.fill(settings.email_assigned_template, placeholders.values(ticket)))


def agent_replied(session: Session, ticket: Ticket, comment: Comment) -> bool:
    """Email a staff reply to the requester. Returns whether it was queued."""
    return _queue(session, ticket, ticket.requester, f"{comment.author.display_name} wrote:\n\n{comment.body}", comment)


def escalated(session: Session, ticket: Ticket, managers: list[User], *, actor: User, reason: str) -> None:
    for manager in managers:
        if manager.id == actor.id:
            continue
        _queue_staff(
            session,
            ticket,
            manager,
            f"Escalated to {ticket.queue.name}: ticket {ticket.id}, {ticket.subject}",
            f"{actor.display_name} escalated ticket {ticket.id} to {ticket.queue.name} "
            f"(priority now {ticket.priority}).\n\nReason: {reason}",
        )


def rule_email(session: Session, ticket: Ticket, to: User | str, subject: str, body: str, *, to_requester: bool) -> bool:
    """An email from a ticket rule. Returns whether one was queued."""
    if not settings.mailbox:
        return False
    if to_requester:
        if not isinstance(to, User) or not to.is_active:
            return False
        session.add(
            OutboundEmail(
                ticket_id=ticket.id,
                to_address=to.email,
                subject=f"{ticket_tag(ticket.id, ticket.created_at)} {subject}",
                body=f"{REPLY_MARKER}\n\n{body}\n{_footer(ticket)}",
            )
        )
        return True
    if isinstance(to, User) and not to.is_active:
        return False
    _queue_staff(session, ticket, to, subject, body)
    return True


def _backoff(attempts: int) -> timedelta:
    return timedelta(minutes=min(2**attempts, 60))


def send_due(session: Session, mailbox: Mailbox, now: datetime, batch: int = 20) -> int:
    """Send queued email that is due. Returns how many were sent. Safe to run in several workers."""
    sent = 0
    for _ in range(batch):
        # One row per transaction: the lock is held only while that row is sent.
        row = session.scalar(
            select(OutboundEmail)
            .where(OutboundEmail.status == OutboundStatus.queued, OutboundEmail.next_attempt_at <= now)
            .order_by(OutboundEmail.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            break
        try:
            message = OutgoingMessage(
                to_address=row.to_address,
                subject=row.subject,
                body=row.body,
                headers={TICKET_HEADER: str(row.ticket_id)} if row.ticket_id is not None else {},
                attachments=[
                    OutgoingAttachment(a["filename"], a["content_type"], storage.path(a["storage_key"]).read_bytes()) for a in row.attachments or []
                ],
            )
            row.internet_message_id = mailbox.send(message)
        except Exception as exc:  # any failure is retried; the row records why
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = OutboundStatus.failed
                log.error("Giving up on outbound email %s to %s: %s", row.id, row.to_address, row.last_error)
            else:
                row.next_attempt_at = now + _backoff(row.attempts)
                log.warning("Outbound email %s failed (attempt %s): %s", row.id, row.attempts, row.last_error)
        else:
            row.status = OutboundStatus.sent
            row.sent_at = now
            row.attempts += 1
            sent += 1
        # Commit straight away so a crash doesn't re-send mail that already went.
        session.commit()
    return sent
