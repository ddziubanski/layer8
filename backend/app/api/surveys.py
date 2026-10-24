# SPDX-License-Identifier: AGPL-3.0-or-later
"""Satisfaction surveys: answering by the emailed link (no sign-in; the link is the key) or on the
ticket in the portal, and the results for staff."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app import numbering, sla, surveys
from app.api.tickets import load_ticket, visible_queue_ids
from app.auth import Agent, CurrentUser, SessionDep
from app.config import settings
from app.models import Ticket, TicketSurvey
from app.schemas import SurveyAnswerIn, SurveyOut, SurveyResultOut, SurveySummaryOut

router = APIRouter(tags=["surveys"])


def _out(s: TicketSurvey) -> SurveyOut:
    t = s.ticket
    return SurveyOut(
        ticket_label=numbering.label(t.id, t.created_at), subject=t.subject, question=settings.survey_question, ratings=surveys.RATINGS,
        rating=s.rating, comment=s.comment, expired=s.expires_at < sla.utcnow(),
    )


def _by_token(session, token: str) -> TicketSurvey:
    try:
        return surveys.by_token(session, token)
    except surveys.SurveyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


def _answer(session, s: TicketSurvey, body: SurveyAnswerIn) -> SurveyOut:
    try:
        surveys.answer(session, s, body.rating, body.comment)
    except surveys.SurveyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    session.commit()
    return _out(s)


@router.get("/api/surveys/answer/{token}", response_model=SurveyOut)
def survey_by_link(token: str, session: SessionDep):
    return _out(_by_token(session, token))


@router.post("/api/surveys/answer/{token}", response_model=SurveyOut)
def answer_by_link(token: str, body: SurveyAnswerIn, session: SessionDep):
    return _answer(session, _by_token(session, token), body)


def _mine(session, ticket_id: int, user) -> TicketSurvey:
    ticket = load_ticket(session, ticket_id, user)
    s = session.scalar(select(TicketSurvey).where(TicketSurvey.ticket_id == ticket.id))
    if s is None or s.requester_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No survey for this ticket")
    return s


@router.get("/api/portal/tickets/{ticket_id}/survey", response_model=SurveyOut)
def portal_survey(ticket_id: int, user: CurrentUser, session: SessionDep):
    return _out(_mine(session, ticket_id, user))


@router.post("/api/portal/tickets/{ticket_id}/survey", response_model=SurveyOut)
def portal_answer(ticket_id: int, body: SurveyAnswerIn, user: CurrentUser, session: SessionDep):
    return _answer(session, _mine(session, ticket_id, user), body)


@router.get("/api/surveys", response_model=SurveySummaryOut)
def survey_results(user: Agent, session: SessionDep, days: int = Query(90, ge=1, le=3650), limit: int = Query(50, ge=1, le=500)):
    """Surveys sent in the last `days`, in queues the user can see: counts, average and the latest answers."""
    stmt = select(TicketSurvey).where(TicketSurvey.sent_at >= sla.utcnow() - timedelta(days=days))
    queues = visible_queue_ids(user)
    if queues is not None:
        stmt = stmt.join(Ticket, Ticket.id == TicketSurvey.ticket_id).where(Ticket.queue_id.in_(queues))
    rows = list(session.scalars(stmt.order_by(TicketSurvey.answered_at.desc().nulls_last(), TicketSurvey.sent_at.desc())))
    answered = [s for s in rows if s.rating is not None]
    by_rating = {n: sum(1 for s in answered if s.rating == n) for n in surveys.RATINGS}
    recent = [
        SurveyResultOut(
            ticket_id=s.ticket_id, ticket_label=numbering.label(s.ticket_id, s.ticket.created_at), subject=s.ticket.subject,
            requester=s.requester.display_name if s.requester else None, assignee=s.assignee.display_name if s.assignee else None,
            queue=s.ticket.queue.name if s.ticket.queue else None, rating=s.rating, comment=s.comment, sent_at=s.sent_at, answered_at=s.answered_at,
        )
        for s in rows[:limit]
    ]
    average = round(sum(s.rating for s in answered) / len(answered), 2) if answered else None
    return SurveySummaryOut(days=days, sent=len(rows), answered=len(answered), average=average, by_rating=by_rating, recent=recent)
