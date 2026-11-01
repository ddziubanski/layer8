# SPDX-License-Identifier: AGPL-3.0-or-later
"""Problem management: investigations, linked tickets and an internal update timeline."""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import broadcast, outage_problems, permissions, services, sla
from app.api.notices import _out as notice_out
from app.api.tickets import staff_ticket, visible
from app.auth import Agent, SessionDep
from app.models import EventType, Problem, ProblemStatus, ProblemUpdate, ServiceNotice, Ticket, User, UserRole, problem_tickets
from app.schemas import (
    BroadcastIn,
    BroadcastOut,
    OutageClusterOut,
    OutageProblemIn,
    ProblemIn,
    ProblemNoteIn,
    ProblemOut,
    ProblemSummaryOut,
    ProblemUpdateOut,
    TicketProblemLinkIn,
    TicketProblemOut,
    TicketProblemSuggestionOut,
    TicketProblemsOut,
)

router = APIRouter(tags=["problems"])
log = logging.getLogger(__name__)
ProblemDeleter = permissions.require("problem_delete")


def _label(problem: Problem) -> str:
    return f"PRB-{problem.id:05d}"


def _summary(problem: Problem) -> ProblemSummaryOut:
    return ProblemSummaryOut.model_validate({
        "id": problem.id,
        "label": _label(problem),
        "title": problem.title,
        "status": problem.status,
        "outage_scope": problem.outage_scope or "",
        "owner": problem.owner,
        "ticket_count": len(problem.tickets),
        "updated_at": problem.updated_at,
    })


def _can_see(session, problem: Problem, user: User) -> bool:
    if user.role == UserRole.admin or not problem.tickets:
        return True
    ticket_ids = {ticket.id for ticket in problem.tickets}
    visible_ids = set(session.scalars(visible(select(Ticket.id).where(Ticket.id.in_(ticket_ids)), user)))
    return visible_ids == ticket_ids


def _load(session, problem_id: int, user: User) -> Problem:
    problem = session.scalar(
        select(Problem)
        .options(
            selectinload(Problem.owner),
            selectinload(Problem.created_by),
            selectinload(Problem.notice).selectinload(ServiceNotice.created_by),
            selectinload(Problem.tickets).selectinload(Ticket.queue),
            selectinload(Problem.tickets).selectinload(Ticket.assignee),
            selectinload(Problem.updates).selectinload(ProblemUpdate.author),
        )
        .where(Problem.id == problem_id)
        .execution_options(populate_existing=True)
    )
    if problem is None or not _can_see(session, problem, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Problem not found")
    return problem


def _out(session, problem: Problem, user: User) -> ProblemOut:
    details = outage_problems.problem_outage_details(session, problem, user)
    return ProblemOut.model_validate({
        "id": problem.id,
        "label": _label(problem),
        "title": problem.title,
        "status": problem.status,
        "outage_scope": problem.outage_scope or "",
        "owner": problem.owner,
        "ticket_count": len(problem.tickets),
        "updated_at": problem.updated_at,
        "description": problem.description,
        "root_cause": problem.root_cause,
        "workaround": problem.workaround,
        "resolution": problem.resolution,
        "created_by": problem.created_by,
        "created_at": problem.created_at,
        "tickets": problem.tickets,
        "updates": problem.updates,
        "notice": notice_out(problem.notice, sla.utcnow()) if problem.notice is not None else None,
        **details,
    })


# The timeline outlives the links (an unlinked or deleted ticket), and visibility only checks the
# tickets linked now: it names tickets by number only, never a restricted ticket's subject.
def _update(session, problem: Problem, user: User, kind: str, body: str) -> None:
    session.add(ProblemUpdate(problem_id=problem.id, author_id=user.id, type=kind, body=body))
    problem.updated_at = func.now()


def _ticket_event(session, ticket: Ticket, problem: Problem, user: User, linked: bool, **extra) -> None:
    """Every link change is in the ticket's own history too. By number only: someone who can see the
    ticket may not be allowed to see the problem (it can also link tickets they can't see)."""
    services._event(session, ticket, EventType.problem_linked if linked else EventType.problem_unlinked, user,
                    problem_id=problem.id, problem=_label(problem), **extra)


def _tickets(session, ticket_ids: list[int], user: User) -> list[Ticket]:
    return [staff_ticket(session, ticket_id, user) for ticket_id in dict.fromkeys(ticket_ids)]


def _owner(session, owner_id: int | None) -> User | None:
    if owner_id is None:
        return None
    owner = session.get(User, owner_id)
    if owner is None or not owner.is_active or owner.role == UserRole.requester:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The owner must be an active staff member")
    return owner


def _validate_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a problem title")
    return title


@router.get("/api/problems/outage-clusters", response_model=list[OutageClusterOut])
def list_outage_clusters(user: Agent, session: SessionDep):
    return outage_problems.outage_clusters(session, user)


@router.post("/api/problems/from-outage", response_model=ProblemOut, status_code=status.HTTP_201_CREATED)
def create_problem_from_outage(body: OutageProblemIn, user: Agent, session: SessionDep):
    title = _validate_title(body.title)
    _owner(session, body.owner_id)
    tickets = _tickets(session, body.ticket_ids, user)
    scope = body.outage_scope.strip()
    notice: ServiceNotice | None = None
    if body.create_draft_notice:
        if not permissions.allowed(session, user, "notices_manage"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Drafting a service notice needs the notices permission")
        notice_title = (body.notice_title.strip() or f"Service outage: {scope or title}")[:200]
        notice_msg = body.notice_message.strip() or f"We are investigating an outage affecting {scope or title}."
        notice = ServiceNotice(
            title=notice_title,
            message=notice_msg,
            severity="outage",
            audience="everyone",
            starts_at=sla.utcnow(),
            ends_at=None,
            enabled=False,
            created_by_id=user.id,
        )
        session.add(notice)
        session.flush()

    problem = Problem(
        title=title,
        description=body.description,
        status=ProblemStatus.investigating,
        workaround=body.workaround,
        outage_scope=scope,
        notice_id=notice.id if notice else None,
        owner_id=body.owner_id,
        created_by_id=user.id,
        tickets=tickets,
    )
    session.add(problem)
    session.flush()
    _update(session, problem, user, "created", f"Problem opened from outage ({scope})" if scope else "Problem created")
    if notice is not None:
        _update(session, problem, user, "notice_drafted", f"Drafted service notice \"{notice.title}\"")
    for ticket in problem.tickets:
        _update(session, problem, user, "ticket_linked", f"Linked ticket #{ticket.id}")
        _ticket_event(session, ticket, problem, user, True)
    session.commit()
    return _out(session, _load(session, problem.id, user), user)


@router.get("/api/problems", response_model=list[ProblemSummaryOut])
def list_problems(
    user: Agent,
    session: SessionDep,
    q: str = "",
    status_: ProblemStatus | None = Query(None, alias="status"),
):
    stmt = select(Problem).options(selectinload(Problem.owner), selectinload(Problem.tickets)).order_by(Problem.updated_at.desc(), Problem.id.desc())
    if status_ is not None:
        stmt = stmt.where(Problem.status == status_)
    for word in q.split():
        like = f"%{word}%"
        stmt = stmt.where(or_(Problem.title.ilike(like), Problem.description.ilike(like), Problem.root_cause.ilike(like), Problem.workaround.ilike(like)))
    return [_summary(problem) for problem in session.scalars(stmt.limit(500)) if _can_see(session, problem, user)]


@router.post("/api/problems", response_model=ProblemOut, status_code=status.HTTP_201_CREATED)
def create_problem(body: ProblemIn, user: Agent, session: SessionDep):
    _owner(session, body.owner_id)
    problem = Problem(
        title=_validate_title(body.title),
        description=body.description,
        status=body.status,
        root_cause=body.root_cause,
        workaround=body.workaround,
        resolution=body.resolution,
        outage_scope=(body.outage_scope or "").strip(),
        owner_id=body.owner_id,
        created_by_id=user.id,
        tickets=_tickets(session, body.ticket_ids, user),
    )
    session.add(problem)
    session.flush()
    _update(session, problem, user, "created", "Problem created")
    for ticket in problem.tickets:
        _update(session, problem, user, "ticket_linked", f"Linked ticket #{ticket.id}")
        _ticket_event(session, ticket, problem, user, True)
    session.commit()
    return _out(session, _load(session, problem.id, user), user)


@router.get("/api/problems/{problem_id}", response_model=ProblemOut)
def get_problem(problem_id: int, user: Agent, session: SessionDep):
    return _out(session, _load(session, problem_id, user), user)


@router.put("/api/problems/{problem_id}", response_model=ProblemOut)
def update_problem(problem_id: int, body: ProblemIn, user: Agent, session: SessionDep):
    problem = _load(session, problem_id, user)
    new_tickets = _tickets(session, body.ticket_ids, user)
    _owner(session, body.owner_id)
    events: list[tuple[str, str]] = []
    if problem.status != body.status:
        events.append(("status_changed", f"Status changed from {problem.status.value} to {body.status.value}"))
    details_changed = any((
        problem.title != body.title.strip(),
        problem.description != body.description,
        problem.root_cause != body.root_cause,
        problem.workaround != body.workaround,
        problem.resolution != body.resolution,
        problem.owner_id != body.owner_id,
    ))
    if details_changed:
        events.append(("edited", "Problem details updated"))
    old_ticket_ids = {ticket.id for ticket in problem.tickets}
    new_ticket_ids = {ticket.id for ticket in new_tickets}
    linked = [t for t in new_tickets if t.id not in old_ticket_ids]
    unlinked = [t for t in problem.tickets if t.id not in new_ticket_ids]
    for ticket in sorted(linked, key=lambda t: t.id):
        events.append(("ticket_linked", f"Linked ticket #{ticket.id}"))
    for ticket in sorted(unlinked, key=lambda t: t.id):
        events.append(("ticket_unlinked", f"Unlinked ticket #{ticket.id}"))

    problem.title = _validate_title(body.title)
    problem.description = body.description
    problem.status = body.status
    problem.root_cause = body.root_cause
    problem.workaround = body.workaround
    problem.resolution = body.resolution
    if body.outage_scope is not None:
        problem.outage_scope = body.outage_scope.strip()
    problem.owner_id = body.owner_id
    problem.tickets = new_tickets
    for kind, note in events:
        _update(session, problem, user, kind, note)
    for ticket in linked:
        _ticket_event(session, ticket, problem, user, True)
    for ticket in unlinked:
        _ticket_event(session, ticket, problem, user, False)
    session.commit()
    return _out(session, _load(session, problem.id, user), user)


@router.post("/api/problems/{problem_id}/broadcast/preview", response_model=BroadcastOut)
def preview_problem_broadcast(problem_id: int, body: BroadcastIn, user: Agent, session: SessionDep):
    problem = _load(session, problem_id, user)
    try:
        r = broadcast.run_for_problem(
            session,
            problem,
            user,
            body=body.body,
            resolve=body.resolve,
            resolution_type_id=body.resolution_type_id,
            closure_reason_id=body.closure_reason_id,
        )
    except broadcast.BroadcastError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return BroadcastOut(applied=False, **r)


@router.post("/api/problems/{problem_id}/broadcast", response_model=BroadcastOut)
def send_problem_broadcast(problem_id: int, body: BroadcastIn, user: Agent, session: SessionDep):
    if not body.confirm or not body.fingerprint:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Preview the update and confirm before sending")
    problem = _load(session, problem_id, user)
    try:
        r = broadcast.run_for_problem(
            session,
            problem,
            user,
            body=body.body,
            resolve=body.resolve,
            resolution_type_id=body.resolution_type_id,
            closure_reason_id=body.closure_reason_id,
            apply=True,
            expected=body.fingerprint,
        )
    except broadcast.BroadcastError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return BroadcastOut(applied=True, **r)


@router.post("/api/problems/{problem_id}/updates", response_model=ProblemUpdateOut, status_code=status.HTTP_201_CREATED)
def add_problem_update(problem_id: int, body: ProblemNoteIn, user: Agent, session: SessionDep):
    problem = _load(session, problem_id, user)
    note = body.body.strip()
    if not note:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter an update")
    update = ProblemUpdate(problem_id=problem.id, author_id=user.id, type="note", body=note)
    session.add(update)
    session.commit()
    session.refresh(update)
    return update


@router.delete("/api/problems/{problem_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_problem(problem_id: int, user: ProblemDeleter, session: SessionDep):
    """For a problem opened by mistake or a duplicate. Linked tickets stay, and say so in their history."""
    problem = _load(session, problem_id, user)
    for ticket in problem.tickets:
        _ticket_event(session, ticket, problem, user, False, deleted=True)
    label, title, count = _label(problem), problem.title, len(problem.tickets)
    session.delete(problem)
    session.commit()
    log.warning("%s deleted problem %s %r (%s linked ticket(s) kept)", user.display_name, label, title, count, extra={"source": "problems"})


# From the ticket: which problems it belongs to, and linking or unlinking one there.

def _ticket_problem_out(p: Problem) -> TicketProblemOut:
    return TicketProblemOut.model_validate({**_summary(p).model_dump(), "workaround": p.workaround, "root_cause": p.root_cause})


def _ticket_problems(session, ticket: Ticket, user: User) -> TicketProblemsOut:
    linked = session.scalars(
        select(Problem).join(problem_tickets, problem_tickets.c.problem_id == Problem.id)
        .where(problem_tickets.c.ticket_id == ticket.id)
        .options(selectinload(Problem.owner), selectinload(Problem.tickets))
        .order_by(Problem.updated_at.desc(), Problem.id.desc())
    ).all()
    shown = [p for p in linked if _can_see(session, p, user)]
    suggestions = outage_problems.suggested_problems_for_ticket(session, ticket, user, _can_see)
    return TicketProblemsOut(
        problems=[_ticket_problem_out(p) for p in shown],
        hidden=len(linked) - len(shown),
        suggested=[TicketProblemSuggestionOut(problem=_ticket_problem_out(p), scope=scope) for p, scope in suggestions],
    )


@router.get("/api/tickets/{ticket_id}/problems", response_model=TicketProblemsOut)
def ticket_problems(ticket_id: int, user: Agent, session: SessionDep):
    return _ticket_problems(session, staff_ticket(session, ticket_id, user), user)


@router.post("/api/tickets/{ticket_id}/problems", response_model=TicketProblemsOut)
def link_ticket_problem(ticket_id: int, body: TicketProblemLinkIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    problem = _load(session, body.problem_id, user)
    if ticket not in problem.tickets:
        if len(problem.tickets) >= 100:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A problem links at most 100 tickets")
        problem.tickets.append(ticket)
        _update(session, problem, user, "ticket_linked", f"Linked ticket #{ticket.id}")
        _ticket_event(session, ticket, problem, user, True)
        session.commit()
    return _ticket_problems(session, ticket, user)


@router.delete("/api/tickets/{ticket_id}/problems/{problem_id}", response_model=TicketProblemsOut)
def unlink_ticket_problem(ticket_id: int, problem_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    problem = _load(session, problem_id, user)
    if ticket in problem.tickets:
        problem.tickets.remove(ticket)
        _update(session, problem, user, "ticket_unlinked", f"Unlinked ticket #{ticket.id}")
        _ticket_event(session, ticket, problem, user, False)
        session.commit()
    return _ticket_problems(session, ticket, user)
