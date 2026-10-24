# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tickets nobody has taken. After Settings > Desk hours > Not taken for (minutes) of waiting (in
desk hours, like the SLA), everyone who could take the ticket gets one email: the queue's owners,
or every technician who can see the queue. A ticket alerts once; unassigning it starts over.

Several new alerts for one person in the same run go out as one digest, so turning this on with a
backlog sends each technician one email, not one per ticket."""

from datetime import datetime

from sqlalchemy import select

from app import numbering, services, sla, teams
from app.config import settings
from app.mail import outbox
from app.models import EventType, OutboundEmail, Ticket, TicketStatus, User, UserRole
from app.mail.text import REPLY_MARKER

WAITING = (TicketStatus.new, TicketStatus.open)
# More than this many for one person in one run: a digest instead.
DIGEST_FROM = 3


def waiting(session, now: datetime) -> list[tuple[Ticket, int]]:
    """Unassigned tickets past the threshold that haven't alerted yet, with their business minutes."""
    out = []
    candidates = session.scalars(
        select(Ticket).where(Ticket.assignee_id.is_(None), Ticket.status.in_(WAITING), Ticket.unassigned_since.is_not(None),
                             Ticket.unaccepted_alerted_at.is_(None), Ticket.merged_into_id.is_(None))
        .order_by(Ticket.unassigned_since).limit(500))
    for t in candidates:
        minutes = sla.business_minutes(t.unassigned_since, now, sla.schedule(t.queue.hours))
        if minutes >= settings.unaccepted_alert_minutes:
            out.append((t, round(minutes)))
    return out


def recipients(session, ticket: Ticket, staff: list[User]) -> list[User]:
    return [u for u in staff if ticket.queue.can_be_assigned(u)]


def _label(t: Ticket) -> str:
    return numbering.label(t.id, t.created_at)


def _waited(minutes: int) -> str:
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def run(session, now: datetime | None = None) -> str:
    if not settings.unaccepted_alert_enabled:
        return "Off: Settings > Desk hours"
    now = now or sla.utcnow()
    due = waiting(session, now)
    if not due:
        return "Nothing waiting"
    staff = list(session.scalars(select(User).where(User.is_active, User.role.in_((UserRole.agent, UserRole.admin)))))
    per_person: dict[int, list[tuple[Ticket, int]]] = {}
    people = {u.id: u for u in staff}
    bot = services.automation_user(session)
    by_email = settings.unaccepted_alert_via in ("email", "both")
    by_teams = settings.unaccepted_alert_via in ("teams", "both")
    for ticket, minutes in due:
        told = recipients(session, ticket, staff)
        # Restricted queues never go to the shared channel: they're always emailed.
        if by_email or ticket.queue.restricted:
            for u in told:
                per_person.setdefault(u.id, []).append((ticket, minutes))
        ticket.unaccepted_alerted_at = now
        services._event(session, ticket, EventType.updated, bot, unaccepted_alert=len(told), waited_minutes=minutes)
    emails = 0
    for user_id, items in per_person.items():
        user = people[user_id]
        if len(items) <= DIGEST_FROM:
            for ticket, minutes in items:
                outbox._queue_staff(
                    session, ticket, user, f"Not taken: {_label(ticket)} {ticket.subject}"[:300],
                    f"{_label(ticket)} in {ticket.queue.name} has waited {_waited(minutes)} with nobody assigned.\n\n"
                    f"  {ticket.subject}\n  From {ticket.requester.display_name} · priority {ticket.priority.value}"
                    + ("\n\nReply to this email with #assign me to take it." if settings.email_commands else ""))
                emails += 1
        elif settings.mailbox and user.is_active:
            lines = [f"{len(items)} tickets have waited at least {_waited(settings.unaccepted_alert_minutes)} with nobody assigned:", ""]
            lines += [f"  {_label(t)} {t.subject} ({t.queue.name}, {_waited(m)})" for t, m in items]
            if settings.portal_url:
                lines += ["", f"Open the queue: {settings.portal_url.rstrip('/')}/?view=unassigned"]
            session.add(OutboundEmail(ticket_id=None, to_address=user.email, subject=f"{len(items)} tickets not taken",
                                      body=REPLY_MARKER + "\n\n" + "\n".join(lines), is_staff_notice=True))
            emails += 1
    out = f"{len(due)} ticket(s) not taken; {emails} email(s) to technicians"
    if by_teams:
        posted = [(t, m) for t, m in due if not t.queue.restricted]
        if posted:
            try:
                teams.send(teams_card(posted))
                out += "; posted to Teams"
            except teams.TeamsError as exc:
                out += f"; Teams post failed: {exc}"
    return out


def _plain(text: str) -> str:
    """Requester-written text, so it can't become a link or formatting in the Teams card."""
    return "".join("\\" + ch if ch in "\\`*_{}[]()#+-.!<>|~" else ch for ch in text.replace("\n", " "))


def teams_card(items: list[tuple[Ticket, int]]) -> dict:
    """One Adaptive Card for the channel: the tickets with their queue, wait and a link."""
    base = settings.portal_url.rstrip("/") if settings.portal_url else ""
    body = [{"type": "TextBlock", "text": f"Layer 8 · {len(items)} ticket{'s' if len(items) != 1 else ''} not taken", "weight": "Bolder", "wrap": True}]
    for t, m in items[:10]:
        line = f"**{_label(t)}** {_plain(t.subject)}" if not base else f"[{_label(t)}]({base}/tickets/{t.id}) {_plain(t.subject)}"
        body.append({"type": "TextBlock", "text": f"{line} · {_plain(t.queue.name)} · {_waited(m)} · {t.priority.value}", "wrap": True})
    if len(items) > 10:
        body.append({"type": "TextBlock", "text": f"and {len(items) - 10} more", "wrap": True, "isSubtle": True})
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.2", "body": body},
    }]}


def count_for(session, user: User, visible_queue_ids) -> dict:
    """The console's counter: tickets this person can see that have waited past the threshold."""
    if not settings.unaccepted_alert_enabled:
        return {"enabled": False, "count": 0, "minutes": settings.unaccepted_alert_minutes}
    stmt = select(Ticket.id).where(Ticket.assignee_id.is_(None), Ticket.status.in_(WAITING), Ticket.unaccepted_alerted_at.is_not(None))
    if visible_queue_ids is not None:
        stmt = stmt.where(Ticket.queue_id.in_(visible_queue_ids))
    return {"enabled": True, "count": len(session.scalars(stmt.limit(1000)).all()), "minutes": settings.unaccepted_alert_minutes}
