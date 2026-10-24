# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canned replies: saved text technicians insert into a ticket's composer. Shared ones (for every
queue, or one) are managed with the canned_replies_manage permission; anyone keeps their own."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app import permissions, placeholders
from app.api.tickets import staff_ticket, visible_queue_ids
from app.auth import Agent, SessionDep
from app.models import CannedReply, Queue, User

router = APIRouter(prefix="/api/canned-replies", tags=["canned replies"])


class CannedReplyIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=20000)
    to_requester: bool = True
    queue_id: int | None = None
    # Yours only, or shared with every technician (needs the permission).
    personal: bool = True


class CannedReplyOut(BaseModel):
    id: int
    title: str
    body: str
    to_requester: bool
    queue_id: int | None
    queue_name: str | None
    personal: bool
    may_edit: bool
    updated_at: datetime


class RenderedOut(BaseModel):
    body: str
    to_requester: bool


def _out(session, r: CannedReply, user: User) -> dict:
    return {"id": r.id, "title": r.title, "body": r.body, "to_requester": r.to_requester, "queue_id": r.queue_id,
            "queue_name": r.queue.name if r.queue else None, "personal": r.owner_id is not None,
            "may_edit": _may_edit(session, r, user), "updated_at": r.updated_at}


def _may_edit(session, r: CannedReply, user: User) -> bool:
    return r.owner_id == user.id if r.owner_id is not None else permissions.allowed(session, user, "canned_replies_manage")


def _visible(session, user: User, queue_id: int | None = None):
    stmt = select(CannedReply).where(or_(CannedReply.owner_id.is_(None), CannedReply.owner_id == user.id))
    visible = visible_queue_ids(user)
    if queue_id is not None:
        stmt = stmt.where(or_(CannedReply.queue_id.is_(None), CannedReply.queue_id == queue_id))
    elif visible is not None:
        stmt = stmt.where(or_(CannedReply.queue_id.is_(None), CannedReply.queue_id.in_(visible)))
    return stmt.order_by(CannedReply.owner_id.is_(None), CannedReply.title)


@router.get("", response_model=list[CannedReplyOut])
def list_replies(user: Agent, session: SessionDep, ticket_id: int | None = None):
    """Your own and the shared ones; with ticket_id, only those for that ticket's queue (or every queue)."""
    queue_id = staff_ticket(session, ticket_id, user).queue_id if ticket_id is not None else None
    return [_out(session, r, user) for r in session.scalars(_visible(session, user, queue_id))]


def _get(session, reply_id: int, user: User) -> CannedReply:
    r = session.get(CannedReply, reply_id)
    if r is None or (r.owner_id is not None and r.owner_id != user.id):
        raise HTTPException(404, "Canned reply not found")
    return r


def _check(session, body: CannedReplyIn, user: User) -> None:
    if not body.personal and not permissions.allowed(session, user, "canned_replies_manage"):
        raise HTTPException(403, "Shared canned replies need the permission (Settings > Permissions); keep it as yours instead")
    if body.queue_id is not None:
        queue = session.get(Queue, body.queue_id)
        if queue is None or not queue.can_view(user):
            raise HTTPException(422, "Pick a queue you can see")


@router.post("", response_model=CannedReplyOut, status_code=201)
def create_reply(body: CannedReplyIn, user: Agent, session: SessionDep):
    _check(session, body, user)
    r = CannedReply(title=body.title.strip(), body=body.body, to_requester=body.to_requester, queue_id=body.queue_id,
                    owner_id=user.id if body.personal else None, updated_by_id=user.id)
    session.add(r)
    session.commit()
    session.refresh(r)
    return _out(session, r, user)


@router.put("/{reply_id}", response_model=CannedReplyOut)
def update_reply(reply_id: int, body: CannedReplyIn, user: Agent, session: SessionDep):
    r = _get(session, reply_id, user)
    if not _may_edit(session, r, user):
        raise HTTPException(403, "Shared canned replies need the permission (Settings > Permissions)")
    _check(session, body, user)
    r.title, r.body, r.to_requester, r.queue_id = body.title.strip(), body.body, body.to_requester, body.queue_id
    r.owner_id, r.updated_by_id = (user.id if body.personal else None), user.id
    session.commit()
    session.refresh(r)
    return _out(session, r, user)


@router.delete("/{reply_id}", status_code=204)
def delete_reply(reply_id: int, user: Agent, session: SessionDep):
    r = _get(session, reply_id, user)
    if not _may_edit(session, r, user):
        raise HTTPException(403, "Shared canned replies need the permission (Settings > Permissions)")
    session.delete(r)
    session.commit()


@router.post("/{reply_id}/render", response_model=RenderedOut)
def render(reply_id: int, ticket_id: int, user: Agent, session: SessionDep):
    """The reply with this ticket's details filled in, ready for the composer."""
    ticket = staff_ticket(session, ticket_id, user)
    r = _get(session, reply_id, user)
    if r.queue_id is not None and r.queue_id != ticket.queue_id:
        raise HTTPException(422, "That canned reply is for another queue")
    return {"body": placeholders.fill(r.body, placeholders.values(ticket, user)), "to_requester": r.to_requester}
