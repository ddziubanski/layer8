# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket templates (Settings > Ticket templates): ready-made new tickets. Technicians list and
apply them in the New ticket form; the templates_manage permission edits them. A template holds
no requester, assignee or credentials, and applying one creates nothing: the ticket is still
reviewed and created through the usual path (routing, SLA and emails included)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app import permissions
from app.api.tickets import visible_queue_ids
from app.auth import Agent, SessionDep
from app.models import ChecklistTemplate, Priority, Queue, TicketTemplate

router = APIRouter(prefix="/api/ticket-templates", tags=["ticket templates"])
Manager = permissions.require("templates_manage")


class TicketTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    subject: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=20000)
    category: str | None = Field(default=None, max_length=100)
    priority: Priority | None = None
    queue_id: int | None = None
    checklist_id: int | None = None
    is_active: bool = True


class TicketTemplateOut(BaseModel):
    id: int
    name: str
    subject: str
    description: str
    category: str | None
    priority: Priority | None
    queue_id: int | None
    queue_name: str | None
    checklist_id: int | None
    checklist_name: str | None
    is_active: bool
    updated_at: datetime


def _out(session, t: TicketTemplate) -> dict:
    checklist = session.get(ChecklistTemplate, t.checklist_id) if t.checklist_id else None
    return {"id": t.id, "name": t.name, "subject": t.subject, "description": t.description, "category": t.category, "priority": t.priority,
            "queue_id": t.queue_id, "queue_name": t.queue.name if t.queue else None, "checklist_id": t.checklist_id,
            "checklist_name": checklist.name if checklist else None, "is_active": t.is_active, "updated_at": t.updated_at}


def _check(session, body: TicketTemplateIn, template_id: int | None = None) -> None:
    clash = session.scalar(select(TicketTemplate.id).where(func.lower(TicketTemplate.name) == body.name.strip().lower()))
    if clash and clash != template_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Another template has that name")
    if body.queue_id is not None:
        queue = session.get(Queue, body.queue_id)
        if queue is None or not queue.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose an active queue, or leave it to routing")
    if body.checklist_id is not None and session.get(ChecklistTemplate, body.checklist_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such checklist")


@router.get("", response_model=list[TicketTemplateOut])
def list_templates(user: Agent, session: SessionDep, all: bool = Query(default=False)):
    """Active templates for the New ticket form; with all=true (managers), inactive ones too.
    A template for a queue the technician can't see isn't offered to them."""
    stmt = select(TicketTemplate).order_by(func.lower(TicketTemplate.name))
    if not (all and permissions.allowed(session, user, "templates_manage")):
        stmt = stmt.where(TicketTemplate.is_active)
        ids = visible_queue_ids(user)
        if ids is not None:
            stmt = stmt.where(or_(TicketTemplate.queue_id.is_(None), TicketTemplate.queue_id.in_(ids)))
    return [_out(session, t) for t in session.scalars(stmt)]


@router.post("", response_model=TicketTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(body: TicketTemplateIn, _: Manager, session: SessionDep):
    _check(session, body)
    t = TicketTemplate(**{**body.model_dump(), "name": body.name.strip()})
    session.add(t)
    session.commit()
    return _out(session, t)


@router.put("/{template_id}", response_model=TicketTemplateOut)
def update_template(template_id: int, body: TicketTemplateIn, _: Manager, session: SessionDep):
    t = session.get(TicketTemplate, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such template")
    _check(session, body, template_id)
    for k, v in {**body.model_dump(), "name": body.name.strip()}.items():
        setattr(t, k, v)
    session.commit()
    return _out(session, t)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, _: Manager, session: SessionDep):
    t = session.get(TicketTemplate, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such template")
    session.delete(t)
    session.commit()
