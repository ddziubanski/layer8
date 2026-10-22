"""Checklists: templates (Settings > Checklists, admins) and starting one on a ticket."""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app import checklists, permissions
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.models import ChecklistTemplate, Queue, TicketChecklist, User, UserRole
from app.schemas import ChecklistDateIn, ChecklistTemplateIn, ChecklistTemplateOut, StartChecklistIn, TicketChecklistOut

router = APIRouter(tags=["checklists"])
TemplatesManager = permissions.require("templates_manage")
log = logging.getLogger(__name__)


def _template(session, template_id: int) -> ChecklistTemplate:
    t = session.get(ChecklistTemplate, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checklist not found")
    return t


@router.get("/api/checklist-templates", response_model=list[ChecklistTemplateOut])
def list_templates(user: Agent, session: SessionDep, include_inactive: bool = False):
    stmt = select(ChecklistTemplate).order_by(ChecklistTemplate.kind, func.lower(ChecklistTemplate.name))
    if not (include_inactive and user.role == UserRole.admin):
        stmt = stmt.where(ChecklistTemplate.is_active)
    return session.scalars(stmt).all()


def _apply(session, t: ChecklistTemplate, body: ChecklistTemplateIn) -> None:
    other = session.scalar(select(ChecklistTemplate).where(func.lower(ChecklistTemplate.name) == body.name.strip().lower()))
    if other is not None and other.id != t.id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a checklist called {other.name!r}")
    for i, step in enumerate(body.items, 1):
        if step.queue_id is not None and session.get(Queue, step.queue_id) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {i}: no such queue")
        if step.assignee_id is not None:
            u = session.get(User, step.assignee_id)
            if u is None or u.role == UserRole.requester:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {i}: the technician must be staff")
    t.name, t.kind, t.description, t.is_active = body.name.strip(), body.kind, body.description, body.is_active
    t.items = [s.model_dump() for s in body.items]


@router.post("/api/checklist-templates", response_model=ChecklistTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(body: ChecklistTemplateIn, admin: TemplatesManager, session: SessionDep):
    t = ChecklistTemplate()
    _apply(session, t, body)
    session.add(t)
    session.commit()
    log.info("Checklist %r added by %s", t.name, admin.display_name, extra={"source": "settings"})
    return t


@router.put("/api/checklist-templates/{template_id}", response_model=ChecklistTemplateOut)
def update_template(template_id: int, body: ChecklistTemplateIn, admin: TemplatesManager, session: SessionDep):
    t = _template(session, template_id)
    _apply(session, t, body)
    session.commit()
    return t


@router.delete("/api/checklist-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, admin: TemplatesManager, session: SessionDep):
    """Checklists already started keep their steps."""
    t = _template(session, template_id)
    session.delete(t)
    session.commit()
    log.info("Checklist %r deleted by %s", t.name, admin.display_name, extra={"source": "settings"})


@router.get("/api/tickets/{ticket_id}/checklists", response_model=list[TicketChecklistOut])
def ticket_checklists(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    return session.scalars(select(TicketChecklist).where(TicketChecklist.ticket_id == ticket.id).order_by(TicketChecklist.id)).all()


@router.post("/api/tickets/{ticket_id}/checklists", response_model=TicketChecklistOut, status_code=status.HTTP_201_CREATED)
def start_checklist(ticket_id: int, body: StartChecklistIn, user: Agent, session: SessionDep):
    """Each step becomes a child ticket, due from the date; an offboarding step can disable the AD account on its day."""
    ticket = staff_ticket(session, ticket_id, user)
    for_user = session.get(User, body.for_user_id) if body.for_user_id is not None else None
    if body.for_user_id is not None and for_user is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such person")
    try:
        c = checklists.start(session, ticket, user, _template(session, body.template_id), key_date=body.key_date, for_user=for_user, for_name=body.for_name)
    except checklists.ChecklistError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    session.commit()
    return c


@router.put("/api/tickets/{ticket_id}/checklists/{checklist_id}/date", response_model=TicketChecklistOut)
def set_checklist_date(ticket_id: int, checklist_id: int, body: ChecklistDateIn, user: Agent, session: SessionDep):
    """Set or move the start date / last day: steps' due dates follow, and a waiting AD disable moves too."""
    ticket = staff_ticket(session, ticket_id, user)
    c = session.get(TicketChecklist, checklist_id)
    if c is None or c.ticket_id != ticket.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checklist not found")
    checklists.set_date(session, c, user, body.key_date)
    session.commit()
    return c
