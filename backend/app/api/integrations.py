# SPDX-License-Identifier: AGPL-3.0-or-later
"""The integration API, for other applications (e.g. MER, a server-room sign-in app): look up and
search tickets, and add internal notes. They sign in with an API key (Settings > API keys) sent as
"Authorization: Bearer l8k_...". A key acts as its own technician account (inactive: it can't sign
in or be assigned work), so it sees what a technician sees (not restricted queues it isn't given)
and its notes carry its name. Keys can use these endpoints only; technicians signed in to the app
can use them too.
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import numbering, services, sla
from app.api.tickets import visible
from app.auth import Admin, SessionDep, current_user, require_agent
from app.config import settings
from app.models import ApiKey, AuthSource, Ticket, TicketSource, TicketStatus, User, UserRole

router = APIRouter(tags=["integrations"])
log = logging.getLogger(__name__)
PREFIX = "l8k_"
# last_used_at is written at most this often per key.
TOUCH_EVERY = timedelta(minutes=5)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def integration_user(request: Request, session: SessionDep, authorization: Annotated[str | None, Header()] = None) -> User:
    """An API key's account, or a signed-in technician."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if token.startswith(PREFIX):
        key = session.scalar(select(ApiKey).where(ApiKey.token_hash == _hash(token), ApiKey.is_active))
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or revoked API key", headers={"WWW-Authenticate": "Bearer"})
        now = sla.utcnow()
        if key.last_used_at is None or now - key.last_used_at > TOUCH_EVERY:
            key.last_used_at = now
            session.commit()
        return key.user
    return require_agent(current_user(request, session, request.headers.get("x-dev-user")))


Caller = Annotated[User, Depends(integration_user)]


# Tickets


class IntegrationTicketOut(BaseModel):
    id: int
    label: str
    subject: str
    status: str
    priority: str
    category: str | None
    queue: str
    assignee: str | None
    requester: str
    created_at: datetime
    url: str


class IntegrationNoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class IntegrationNoteOut(BaseModel):
    id: int
    ticket_id: int
    created_at: datetime


def _out(t: Ticket) -> IntegrationTicketOut:
    return IntegrationTicketOut(
        id=t.id, label=numbering.label(t.id, t.created_at), subject=t.subject, status=t.status.value, priority=t.priority.value,
        category=t.category, queue=t.queue.name, assignee=t.assignee.display_name if t.assignee else None,
        requester=t.requester.display_name, created_at=t.created_at, url=f"{(settings.portal_url or '').rstrip('/')}/tickets/{t.id}",
    )


def _number(ref: str) -> int | None:
    """1042, #1042 or the ticket's label (HD-1042, 2026-1042...)."""
    ref = ref.strip()
    if m := re.fullmatch(r"#?(\d+)", ref):
        return int(m[1])
    if m := numbering.pattern().fullmatch(ref):
        return int(m[1])
    return None


def _ticket(session, user: User, ref: str) -> Ticket:
    number = _number(ref)
    t = session.scalar(visible(select(Ticket), user).where(Ticket.id == number)) if number is not None else None
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No ticket {ref}")
    return t


@router.get("/api/integrations/tickets/{ref}", response_model=IntegrationTicketOut)
def get_ticket(ref: str, user: Caller, session: SessionDep):
    """A ticket by its number or label (1042, #1042, HD-1042)."""
    return _out(_ticket(session, user, ref))


@router.get("/api/integrations/tickets", response_model=list[IntegrationTicketOut])
def search_tickets(user: Caller, session: SessionDep, q: str = "", open_only: bool = True, limit: int = 20):
    """Tickets whose subject contains q (newest first), open ones only unless open_only=false."""
    stmt = visible(select(Ticket), user).where(Ticket.merged_into_id.is_(None))
    for word in q.split():
        stmt = stmt.where(Ticket.subject.ilike(f"%{word}%"))
    if open_only:
        stmt = stmt.where(Ticket.status.not_in([TicketStatus.resolved, TicketStatus.closed]))
    return [_out(t) for t in session.scalars(stmt.order_by(Ticket.id.desc()).limit(min(max(limit, 1), 100)))]


@router.post("/api/integrations/tickets/{ref}/notes", response_model=IntegrationNoteOut, status_code=status.HTTP_201_CREATED)
def add_note(ref: str, body: IntegrationNoteIn, user: Caller, session: SessionDep):
    """An internal note on the ticket (technicians see it; the requester doesn't)."""
    t = _ticket(session, user, ref)
    try:
        c = services.add_comment(session, t, user, body=body.text, is_internal=True, source=TicketSource.api, as_staff=True)
    except services.ServiceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    session.commit()
    return IntegrationNoteOut(id=c.id, ticket_id=t.id, created_at=c.created_at)


# Keys (Settings > API keys)


class ApiKeyOut(BaseModel):
    id: int
    name: str
    prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    created_by: str | None


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyMadeOut(ApiKeyOut):
    # Shown once, never stored.
    token: str


def _key_out(k: ApiKey, model=ApiKeyOut, **extra):
    return model(
        id=k.id, name=k.name, prefix=k.prefix, is_active=k.is_active, created_at=k.created_at, last_used_at=k.last_used_at,
        created_by=k.created_by.display_name if k.created_by else None, **extra,
    )


@router.get("/api/api-keys", response_model=list[ApiKeyOut])
def list_keys(_: Admin, session: SessionDep):
    return [_key_out(k) for k in session.scalars(select(ApiKey).order_by(ApiKey.is_active.desc(), ApiKey.name))]


@router.post("/api/api-keys", response_model=ApiKeyMadeOut, status_code=status.HTTP_201_CREATED)
def create_key(body: ApiKeyIn, admin: Admin, session: SessionDep):
    """A new key and its account. The key is shown once."""
    name = body.name.strip()
    token = PREFIX + secrets.token_urlsafe(32)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "app"
    n = session.scalar(select(func.count()).select_from(ApiKey)) or 0
    user = User(
        email=f"apikey-{n + 1}-{slug}@integrations.invalid", display_name=f"{name} (API key)"[:200], role=UserRole.agent,
        is_active=False, auth_source=AuthSource.local, role_source="api key",
    )
    session.add(user)
    session.flush()
    key = ApiKey(name=name, token_hash=_hash(token), prefix=token[: len(PREFIX) + 4], user_id=user.id, created_by_id=admin.id)
    session.add(key)
    session.commit()
    log.info("API key %r made by %s", name, admin.display_name, extra={"source": "settings"})
    return _key_out(key, ApiKeyMadeOut, token=token)


@router.delete("/api/api-keys/{key_id}", response_model=ApiKeyOut)
def revoke_key(key_id: int, admin: Admin, session: SessionDep):
    """Revoke: the key stops working at once. Notes it added stay, with its name."""
    key = session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    key.is_active = False
    session.commit()
    log.info("API key %r revoked by %s", key.name, admin.display_name, extra={"source": "settings"})
    return _key_out(key)
