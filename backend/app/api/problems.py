# SPDX-License-Identifier: AGPL-3.0-or-later
"""Problem management: investigations, linked tickets and an internal update timeline."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.tickets import staff_ticket, visible
from app.auth import Agent, SessionDep
from app.models import Problem, ProblemStatus, ProblemUpdate, Ticket, User, UserRole
from app.schemas import ProblemIn, ProblemNoteIn, ProblemOut, ProblemSummaryOut, ProblemUpdateOut

router = APIRouter(tags=["problems"])


def _label(problem: Problem) -> str:
    return f"PRB-{problem.id:05d}"


def _summary(problem: Problem) -> ProblemSummaryOut:
    return ProblemSummaryOut.model_validate({
        "id": problem.id,
        "label": _label(problem),
        "title": problem.title,
        "status": problem.status,
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
            selectinload(Problem.tickets),
            selectinload(Problem.updates).selectinload(ProblemUpdate.author),
        )
        .where(Problem.id == problem_id)
        .execution_options(populate_existing=True)
    )
    if problem is None or not _can_see(session, problem, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Problem not found")
    return problem


def _out(problem: Problem) -> ProblemOut:
    return ProblemOut.model_validate({
        "id": problem.id,
        "label": _label(problem),
        "title": problem.title,
        "status": problem.status,
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
    })


def _update(session, problem: Problem, user: User, kind: str, body: str) -> None:
    session.add(ProblemUpdate(problem_id=problem.id, author_id=user.id, type=kind, body=body))
    problem.updated_at = func.now()


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
        owner_id=body.owner_id,
        created_by_id=user.id,
        tickets=_tickets(session, body.ticket_ids, user),
    )
    session.add(problem)
    session.flush()
    _update(session, problem, user, "created", "Problem created")
    for ticket in problem.tickets:
        _update(session, problem, user, "ticket_linked", f"Linked ticket #{ticket.id}: {ticket.subject}")
    session.commit()
    return _out(_load(session, problem.id, user))


@router.get("/api/problems/{problem_id}", response_model=ProblemOut)
def get_problem(problem_id: int, user: Agent, session: SessionDep):
    return _out(_load(session, problem_id, user))


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
    for ticket_id in sorted(new_ticket_ids - old_ticket_ids):
        ticket = next(ticket for ticket in new_tickets if ticket.id == ticket_id)
        events.append(("ticket_linked", f"Linked ticket #{ticket.id}: {ticket.subject}"))
    for ticket_id in sorted(old_ticket_ids - new_ticket_ids):
        ticket = next(ticket for ticket in problem.tickets if ticket.id == ticket_id)
        events.append(("ticket_unlinked", f"Unlinked ticket #{ticket.id}: {ticket.subject}"))

    problem.title = _validate_title(body.title)
    problem.description = body.description
    problem.status = body.status
    problem.root_cause = body.root_cause
    problem.workaround = body.workaround
    problem.resolution = body.resolution
    problem.owner_id = body.owner_id
    problem.tickets = new_tickets
    for kind, note in events:
        _update(session, problem, user, kind, note)
    session.commit()
    return _out(_load(session, problem.id, user))


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
