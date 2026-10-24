# SPDX-License-Identifier: AGPL-3.0-or-later
"""Satisfaction surveys: when a ticket is first solved, a random share of them (Settings > Surveys)
ask the requester how it went, 1 to 5 with an optional comment.

The email has a link per rating (one click answers); the link's token is the key, so the page
needs no sign-in. Only the token's hash is kept. The requester can also answer on the ticket in
the portal. Nobody is asked more than once in survey_cooldown_days.
"""

import hashlib
import logging
import random
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import numbering, services, sla
from app.config import settings
from app.models import EventType, Ticket, TicketSource, TicketSurvey

log = logging.getLogger(__name__)

RATINGS = {1: "Very unhappy", 2: "Unhappy", 3: "OK", 4: "Happy", 5: "Very happy"}


class SurveyError(ValueError):
    """The message is safe to show."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def link(token: str, rating: int | None = None) -> str | None:
    if not settings.portal_url:
        return None
    return f"{settings.portal_url.rstrip('/')}/survey/{token}" + (f"?r={rating}" if rating else "")


def _eligible(session: Session, ticket: Ticket) -> bool:
    requester = ticket.requester
    if ticket.merged_into_id is not None or ticket.parent_id is not None:
        return False  # merged away, or a task inside a bigger ticket
    if requester is None or not requester.is_active or requester.email.lower() == services.AUTOMATION_EMAIL:
        return False
    if session.scalar(select(TicketSurvey.id).where(TicketSurvey.ticket_id == ticket.id)) is not None:
        return False
    if settings.survey_cooldown_days:
        since = sla.utcnow() - timedelta(days=settings.survey_cooldown_days)
        if session.scalar(select(TicketSurvey.id).where(TicketSurvey.requester_id == requester.id, TicketSurvey.sent_at >= since).limit(1)) is not None:
            return False
    return True


def maybe_send(session: Session, ticket: Ticket, roll=random.random) -> TicketSurvey | None:
    """Called when a ticket is first solved: survey it if it's picked (survey_percent of them)."""
    if settings.survey_percent <= 0 or roll() * 100 >= settings.survey_percent:
        return None
    if not _eligible(session, ticket):
        return None
    return send(session, ticket)


def send(session: Session, ticket: Ticket) -> TicketSurvey:
    from app.mail import outbox

    token = secrets.token_urlsafe(24)
    now = sla.utcnow()
    survey = TicketSurvey(
        ticket_id=ticket.id, requester_id=ticket.requester_id, assignee_id=ticket.assignee_id, queue_id=ticket.queue_id,
        token_hash=hash_token(token), sent_at=now, expires_at=now + timedelta(days=settings.survey_expire_days),
    )
    session.add(survey)
    session.flush()
    first = ticket.requester.display_name.split()[0] if ticket.requester.display_name else ""
    lines = [f"Hello {first}," if first else "Hello,", "", f"Your request \"{ticket.subject}\" is done. {settings.survey_question}", ""]
    if link(token):
        lines += [f"  {n} {label}: {link(token, n)}" for n, label in sorted(RATINGS.items(), reverse=True)]
        lines += ["", "One click is enough; you can add a comment on the page it opens.", f"The links work for {settings.survey_expire_days} days."]
    else:
        lines += ["Open the request in the service desk portal to answer."]
    emailed = outbox._queue(session, ticket, ticket.requester, "\n".join(lines))
    services._event(session, ticket, EventType.survey_sent, None, emailed=emailed)
    log.info("Survey sent for ticket %s to %s", ticket.id, ticket.requester.email, extra={"source": "surveys"})
    return survey


def answer(session: Session, survey: TicketSurvey, rating: int, comment: str = "") -> None:
    """Record (or change, while the link works) the answer. Doesn't commit."""
    if rating not in RATINGS:
        raise SurveyError("Pick 1 to 5")
    if survey.expires_at < sla.utcnow():
        raise SurveyError("This survey has closed. Thank you anyway!")
    first = survey.rating is None
    was_low = _low(survey.rating)
    survey.rating, survey.comment, survey.answered_at = rating, comment.strip()[:4000], sla.utcnow()
    services._event(
        session, survey.ticket, EventType.survey_answered, survey.requester,
        rating=rating, label=RATINGS[rating], comment=survey.comment, changed=not first,
    )
    if _low(rating) and not was_low:
        _follow_up(session, survey)


def _low(rating: int | None) -> bool:
    return rating is not None and settings.survey_alert_at > 0 and rating <= settings.survey_alert_at


def _follow_up(session: Session, survey: TicketSurvey) -> None:
    """A low rating: a note on the ticket, and an email to whoever handled it and the queue's managers."""
    from app.mail import outbox

    ticket, who = survey.ticket, survey.requester.display_name if survey.requester else "The requester"
    text = f"{who} rated this {survey.rating}/5 ({RATINGS[survey.rating]})" + (f": \"{survey.comment}\"" if survey.comment else ".") + " Worth a call to put it right."
    services.add_comment(session, ticket, services.automation_user(session), body=f"Satisfaction survey: {text}", is_internal=True, source=TicketSource.agent, as_staff=True)
    people = {u.id: u for u in ([survey.assignee] if survey.assignee else []) + (list(ticket.queue.managers) if ticket.queue else [])}
    for person in people.values():
        outbox._queue_staff(session, ticket, person, f"Low satisfaction rating on {numbering.label(ticket.id, ticket.created_at)}: {ticket.subject}", text)
    log.info("Survey for ticket %s rated %s: followed up with %s", ticket.id, survey.rating, ", ".join(p.email for p in people.values()) or "nobody", extra={"source": "surveys"})


def by_token(session: Session, token: str) -> TicketSurvey:
    survey = session.scalar(select(TicketSurvey).where(TicketSurvey.token_hash == hash_token(token)))
    if survey is None:
        raise SurveyError("That survey link isn't valid")
    return survey
