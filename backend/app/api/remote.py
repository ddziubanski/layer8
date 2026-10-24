# SPDX-License-Identifier: AGPL-3.0-or-later
"""Remote sessions from a ticket: Remote Assistance offered to the requester's computer.

The browser can't start msra.exe, so technicians' PCs have a small launcher registered for
helpdesk-remote: links (docs/remote-assistance.md). Starting a session here logs it on the ticket
and returns such a link; the launcher runs msra /offerra <computer>, and when Remote Assistance
closes it reports the end with the link's one-time token, so the ticket shows how long it took.
"""

import hashlib
import logging
import re
import secrets
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app import services, sla
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.config import settings
from app.models import Device, EventType, RemoteSession, Ticket, TicketSource, User, UserRole
from app.schemas import RemoteLaunchOut, RemoteSessionIn, RemoteSessionOut, UserRef

router = APIRouter(tags=["remote"])
log = logging.getLogger(__name__)

HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})")
TOOLS = {"msra": "Remote Assistance"}


def _out(rs: RemoteSession) -> RemoteSessionOut:
    return RemoteSessionOut(
        id=rs.id, ticket_id=rs.ticket_id, device_id=rs.device_id, target=rs.target, tool=rs.tool,
        started_by=UserRef.model_validate(rs.started_by) if rs.started_by else None, started_at=rs.started_at, ended_at=rs.ended_at,
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _minutes(rs: RemoteSession) -> str:
    seconds = int((rs.ended_at - rs.started_at).total_seconds())
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


@router.post("/api/tickets/{ticket_id}/remote-sessions", response_model=RemoteLaunchOut, status_code=status.HTTP_201_CREATED)
def start_remote_session(ticket_id: int, body: RemoteSessionIn, request: Request, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    device = session.get(Device, body.device_id) if body.device_id is not None else ticket.computer
    if device is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Set the requester's computer first")
    target = device.dns_hostname or device.name
    if not HOST.fullmatch(target):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{target!r} isn't a host name Remote Assistance can use")
    token = secrets.token_urlsafe(24)
    rs = RemoteSession(ticket_id=ticket.id, device_id=device.id, target=target, tool="msra", started_by_id=user.id, started_at=sla.utcnow(), end_token_hash=_hash(token))
    session.add(rs)
    session.flush()
    services._event(session, ticket, EventType.remote_session, user, session_id=rs.id, tool="msra", target=target, action="started")
    services.add_comment(
        session, ticket, user, body=f"Offered Remote Assistance to {target} (remote session #{rs.id}).", is_internal=True, source=TicketSource.agent, as_staff=True
    )
    session.commit()
    base = (settings.portal_url or str(request.base_url)).rstrip("/")
    uri = "helpdesk-remote:offerra?" + urlencode({"host": target, "end": f"{base}/api/remote-sessions/{rs.id}/end", "token": token}, quote_via=quote)
    log.info("%s offered Remote Assistance to %s from ticket #%s", user.display_name, target, ticket.id, extra={"source": "scripts"})
    return RemoteLaunchOut(session=_out(rs), launch_uri=uri)


@router.get("/api/tickets/{ticket_id}/remote-sessions", response_model=list[RemoteSessionOut])
def list_remote_sessions(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    return [_out(rs) for rs in session.scalars(select(RemoteSession).where(RemoteSession.ticket_id == ticket.id).order_by(RemoteSession.id.desc()))]


def _end(session, rs: RemoteSession, by: User | None) -> None:
    rs.ended_at, rs.end_token_hash = sla.utcnow(), None
    ticket = session.get(Ticket, rs.ticket_id)
    author = by or rs.started_by or services.automation_user(session)
    services._event(session, ticket, EventType.remote_session, by, session_id=rs.id, tool=rs.tool, target=rs.target, action="ended", duration=_minutes(rs))
    services.add_comment(
        session, ticket, author, body=f"{TOOLS.get(rs.tool, rs.tool)} with {rs.target} ended after {_minutes(rs)} (remote session #{rs.id}).",
        is_internal=True, source=TicketSource.agent, as_staff=True,
    )


@router.post("/api/remote-sessions/{session_id}/end", status_code=status.HTTP_204_NO_CONTENT)
def end_remote_session(session_id: int, session: SessionDep, token: str = Query(...)):
    """Called by the launcher when Remote Assistance closes, with the link's one-time token (no sign-in)."""
    rs = session.get(RemoteSession, session_id)
    if rs is None or rs.end_token_hash is None or not secrets.compare_digest(rs.end_token_hash, _hash(token)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such open session")
    _end(session, rs, None)
    session.commit()


@router.post("/api/remote-sessions/{session_id}/close", response_model=RemoteSessionOut)
def close_remote_session(session_id: int, user: Agent, session: SessionDep):
    """A technician marks it ended (e.g. the launcher couldn't report back)."""
    rs = session.get(RemoteSession, session_id)
    if rs is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session")
    staff_ticket(session, rs.ticket_id, user)
    if rs.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already ended")
    if not (user.role == UserRole.admin or rs.started_by_id == user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only whoever started it, or an admin, can end it")
    _end(session, rs, user)
    session.commit()
    return _out(rs)
