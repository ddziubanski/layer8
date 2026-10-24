# SPDX-License-Identifier: AGPL-3.0-or-later
"""Changes: risk-scored, planned, approved when the risk calls for it; and change templates."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app import changes, permissions
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.models import Change, ChangeKind, ChangeStatus, ChangeTemplate, Device, System, Ticket, User, UserRole, change_tickets
from app.schemas import ChangeActionIn, ChangeConflictOut, ChangeIn, ChangeOut, ChangeSummaryOut, ChangeTemplateIn, ChangeTemplateOut

router = APIRouter(tags=["changes"])
TemplatesManager = permissions.require("templates_manage")
log = logging.getLogger(__name__)


def _change(session, change_id: int) -> Change:
    c = session.get(Change, change_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Change not found")
    return c


def _summary(c: Change, cls=ChangeSummaryOut, **extra):
    return cls.model_validate({**{k: getattr(c, k) for k in ChangeSummaryOut.model_fields if hasattr(c, k) and k not in ("label", "risk", "risk_level")},
                               "label": changes.label(c), "risk": changes.risk(c), "risk_level": changes.risk_level(c), **extra})


def _out(session, c: Change, user: User) -> ChangeOut:
    fields = {k: getattr(c, k) for k in ChangeOut.model_fields if hasattr(c, k) and k not in ChangeSummaryOut.model_fields}
    return _summary(c, ChangeOut, **fields, needs_approval=changes.needs_approval(c), can_approve=changes.can_approve(session, c, user),
                    can_edit=c.status in changes.EDITABLE, conflicts=[ChangeConflictOut(change=_summary(o), shared=sh) for o, sh in changes.conflicts(session, c)])


@router.get("/api/changes", response_model=list[ChangeSummaryOut])
def list_changes(
    _: Agent, session: SessionDep, q: str = "", status_: ChangeStatus | None = Query(None, alias="status"), open_only: bool = False, ticket_id: int | None = None,
    starts_from: datetime | None = None, starts_before: datetime | None = None,
):
    """Newest planned first. open_only: not ended (draft to in progress). starts_from / starts_before: a window
    starting in that range (a calendar's month), oldest first."""
    return _list(session, q, status_, open_only, ticket_id, starts_from, starts_before)


def _list(session, q: str = "", status_: ChangeStatus | None = None, open_only: bool = False, ticket_id: int | None = None, starts_from=None, starts_before=None):
    stmt = select(Change).order_by(Change.planned_start.desc().nulls_first(), Change.id.desc())
    if starts_from is not None or starts_before is not None:
        stmt = select(Change).where(Change.planned_start.is_not(None)).order_by(Change.planned_start, Change.id)
        if starts_from is not None:
            stmt = stmt.where(Change.planned_start >= starts_from)
        if starts_before is not None:
            stmt = stmt.where(Change.planned_start < starts_before)
    if open_only:
        stmt = stmt.where(Change.status.in_(changes.OPEN))
    if status_:
        stmt = stmt.where(Change.status == status_)
    if ticket_id is not None:
        stmt = stmt.where(Change.id.in_(select(change_tickets.c.change_id).where(change_tickets.c.ticket_id == ticket_id)))
    for word in q.split():
        like = f"%{word}%"
        conds = [Change.title.ilike(like), Change.description.ilike(like)]
        digits = "".join(ch for ch in word if ch.isdigit())
        if digits:
            conds.append(Change.id == int(digits))
        stmt = stmt.where(or_(*conds))
    return [_summary(c) for c in session.scalars(stmt.limit(500))]


@router.get("/api/changes/{change_id}", response_model=ChangeOut)
def get_change(change_id: int, user: Agent, session: SessionDep):
    return _out(session, _change(session, change_id), user)


def _apply(session, c: Change, body: ChangeIn, user: User) -> None:
    template = session.get(ChangeTemplate, body.template_id) if body.template_id is not None else None
    if body.template_id is not None and template is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such template")
    if body.kind == "standard" and not (template and template.pre_approved):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A standard change comes from a pre-approved template")
    if body.planned_start and body.planned_end and body.planned_end < body.planned_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The window ends before it starts")
    owner = session.get(User, body.owner_id) if body.owner_id is not None else None
    if body.owner_id is not None and (owner is None or owner.role == UserRole.requester):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The owner must be staff")

    def load(model, ids, what):
        out = []
        for i in dict.fromkeys(ids):
            obj = session.get(model, i)
            if obj is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No {what} {i}")
            out.append(obj)
        return out

    tickets = [staff_ticket(session, t.id, user) for t in load(Ticket, body.ticket_ids, "ticket")]
    for f in ("title", "description", "implementation_plan", "rollback_plan", "test_plan", "impact", "likelihood", "planned_start", "planned_end"):
        setattr(c, f, getattr(body, f).strip() if f == "title" else getattr(body, f))
    c.kind, c.template_id, c.owner_id = ChangeKind(body.kind), body.template_id, body.owner_id
    c.tickets, c.devices, c.systems = tickets, load(Device, body.device_ids, "device"), load(System, body.system_ids, "system")


@router.post("/api/changes", response_model=ChangeOut, status_code=status.HTTP_201_CREATED)
def create_change(body: ChangeIn, user: Agent, session: SessionDep):
    c = Change(requested_by_id=user.id, owner_id=user.id)
    _apply(session, c, body, user)
    c.owner_id = body.owner_id or user.id
    changes.event(c, user, "created")
    session.add(c)
    session.commit()
    log.info("Change %s %r raised by %s", changes.label(c), c.title, user.display_name, extra={"source": "changes"})
    return _out(session, c, user)


@router.put("/api/changes/{change_id}", response_model=ChangeOut)
def update_change(change_id: int, body: ChangeIn, user: Agent, session: SessionDep):
    """While it's a draft, awaiting approval or rejected. Editing one awaiting approval sends it back to draft."""
    c = _change(session, change_id)
    if c.status not in changes.EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft, a change awaiting approval or a rejected one can be edited")
    _apply(session, c, body, user)
    note = ""
    if c.status == ChangeStatus.awaiting_approval:
        c.status, note = ChangeStatus.draft, "Back to draft: changed while awaiting approval"
    changes.event(c, user, "edited", note)
    session.commit()
    return _out(session, c, user)


@router.post("/api/changes/{change_id}/actions", response_model=ChangeOut)
def change_action(change_id: int, body: ChangeActionIn, user: Agent, session: SessionDep):
    c = _change(session, change_id)
    try:
        changes.act(session, c, user, body.action, body.note)
    except changes.ChangeError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    session.commit()
    log.info("Change %s: %s by %s", changes.label(c), body.action, user.display_name, extra={"source": "changes"})
    return _out(session, c, user)


@router.get("/api/tickets/{ticket_id}/changes", response_model=list[ChangeSummaryOut])
def ticket_changes(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    return _list(session, ticket_id=ticket.id)


# Templates (Settings > Change templates)


@router.get("/api/change-templates", response_model=list[ChangeTemplateOut])
def list_templates(user: Agent, session: SessionDep, include_inactive: bool = False):
    stmt = select(ChangeTemplate).order_by(func.lower(ChangeTemplate.name))
    if not (include_inactive and user.role == UserRole.admin):
        stmt = stmt.where(ChangeTemplate.is_active)
    return session.scalars(stmt).all()


def _apply_template(session, t: ChangeTemplate, body: ChangeTemplateIn) -> None:
    other = session.scalar(select(ChangeTemplate).where(func.lower(ChangeTemplate.name) == body.name.strip().lower()))
    if other is not None and other.id != t.id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a template called {other.name!r}")
    if body.pre_approved and not body.rollback_plan.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A pre-approved template needs a rollback plan")
    for f, v in body.model_dump().items():
        setattr(t, f, v.strip() if f == "name" else v)


@router.post("/api/change-templates", response_model=ChangeTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(body: ChangeTemplateIn, admin: TemplatesManager, session: SessionDep):
    t = ChangeTemplate()
    _apply_template(session, t, body)
    session.add(t)
    session.commit()
    return t


@router.put("/api/change-templates/{template_id}", response_model=ChangeTemplateOut)
def update_template(template_id: int, body: ChangeTemplateIn, admin: TemplatesManager, session: SessionDep):
    t = session.get(ChangeTemplate, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    _apply_template(session, t, body)
    session.commit()
    return t


@router.delete("/api/change-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, admin: TemplatesManager, session: SessionDep):
    t = session.get(ChangeTemplate, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    session.delete(t)
    session.commit()
