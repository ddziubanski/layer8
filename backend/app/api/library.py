# SPDX-License-Identifier: AGPL-3.0-or-later
"""Library source status, explicit search and reviewed internal ticket references."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app import library, services, teams_chat
from app.api.teams_chat import lock
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.config import settings
from app.models import Comment, TicketSource

router = APIRouter(tags=['library'])


class LibraryStatus(BaseModel):
    enabled: bool
    connected: bool
    site_url: str | None = None


class LibraryReference(BaseModel):
    drive_id: str
    item_id: str
    title: str
    url: str
    summary: str = ''


class LibrarySearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=200)


class LibraryLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    drive_id: str = Field(min_length=1, max_length=512, pattern=r'^[A-Za-z0-9!_.-]+$')
    item_id: str = Field(min_length=1, max_length=512, pattern=r'^[A-Za-z0-9!_.-]+$')
    confirm: bool = False


class LibraryLinked(BaseModel):
    comment_id: int


def enabled():
    if not settings.library_enabled or not settings.library_site_url:
        raise HTTPException(409, 'An administrator can enable Library in Settings > Library.')
    try:
        library.site_url(settings.library_site_url)
    except library.LibraryError as exc:
        raise HTTPException(409, str(exc)) from None


def access(session, user):
    enabled()
    lock(session, user)
    token, record = teams_chat.access(user.id, library.SCOPES)
    if not (record.get('library') or record.get('copilot')):
        raise teams_chat.ConnectionError('Reconnect your Microsoft account in My preferences to grant Library access.')
    return token


@router.get('/api/library/status', response_model=LibraryStatus)
def status(user: Agent):
    if not settings.library_enabled or not settings.library_site_url:
        return {'enabled': False, 'connected': False}
    try:
        site = library.site_url(settings.library_site_url)
        record = teams_chat.read(user.id)
        connected = bool(record.get('library') or record.get('copilot'))
    except (library.LibraryError, teams_chat.ConnectionError):
        return {'enabled': True, 'connected': False}
    return {'enabled': True, 'connected': connected, 'site_url': site}


@router.post('/api/tickets/{ticket_id}/library/search', response_model=list[LibraryReference])
def search(ticket_id: int, body: LibrarySearch, user: Agent, session: SessionDep):
    staff_ticket(session, ticket_id, user)
    try:
        result = library.search(access(session, user), body.query)
        session.rollback()  # Release the connection-cache lock; search stores no content.
        return result
    except (library.LibraryError, teams_chat.ConnectionError) as exc:
        raise HTTPException(409, str(exc)) from None


@router.post('/api/tickets/{ticket_id}/library/link', response_model=LibraryLinked)
def link(ticket_id: int, body: LibraryLink, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    if not body.confirm:
        raise HTTPException(422, 'Review and confirm the internal reference before linking.')
    try:
        ref = library.resolve(access(session, user), body.drive_id, body.item_id)
    except (library.LibraryError, teams_chat.ConnectionError) as exc:
        raise HTTPException(409, str(exc)) from None
    note = f"Library reference: {ref['title']}\n{ref['url']}"
    previous = session.scalar(select(Comment).where(Comment.ticket_id == ticket.id, Comment.author_id == user.id, Comment.is_internal, Comment.body == note).limit(1))
    if previous:
        session.rollback()
        return {'comment_id': previous.id}
    comment = services.add_comment(session, ticket, user,
        body=note, is_internal=True,
        source=TicketSource.agent, as_staff=True, email_requester=False)
    session.commit()
    return {'comment_id': comment.id}
