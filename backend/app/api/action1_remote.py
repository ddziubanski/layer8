# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket-bound, single-click Action1 handoff. No remote URL is stored or logged."""
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app import action1_remote as remote, services, sla
from app.api.tickets import staff_ticket
from app.auth import SessionDep
from app.models import AppSetting, EventType, RemoteSession, Ticket, TicketDevice, TicketSource
from app.permissions import require

router = APIRouter(tags=["action1"])
Operator = require("action1_remote")


class Action1RemoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: int


class Action1RemoteOut(BaseModel):
    session_id: int
    status: Literal["starting", "pending", "ready", "failed", "unknown", "expired"]
    message: str
    launch_url: str | None = None


def private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def linked(session, ticket, device_id):
    if ticket.computer_id != device_id and session.scalar(select(TicketDevice).where(TicketDevice.ticket_id == ticket.id, TicketDevice.device_id == device_id)) is None:
        raise HTTPException(404, "Device is not linked to this ticket")
    return remote.target(session, device_id)


def state_key(session_id):
    return f"action1_remote:{session_id}"


def result(rs, value, url=None):
    return Action1RemoteOut(session_id=rs.id, status=value["status"], message=value.get("message", "Waiting for Action1 and endpoint consent…"), launch_url=url)


@router.post("/api/tickets/{ticket_id}/action1-remote", response_model=Action1RemoteOut)
def start(ticket_id: int, body: Action1RemoteIn, user: Operator, session: SessionDep, response: Response):
    private(response)
    ticket = staff_ticket(session, ticket_id, user)
    try:
        # Serialize repeated clicks across tabs. An uncertain external write is never retried.
        session.scalar(select(Ticket).where(Ticket.id == ticket.id).with_for_update().execution_options(populate_existing=True))
        session.expire(ticket, ["queue"])
        staff_ticket(session, ticket.id, user)
        device, mapping = linked(session, ticket, body.device_id)
        fingerprint = remote.configuration()
        existing = session.scalar(select(RemoteSession).where(RemoteSession.ticket_id == ticket.id,
            RemoteSession.device_id == device.id, RemoteSession.started_by_id == user.id,
            RemoteSession.tool == "action1", RemoteSession.ended_at.is_(None)).order_by(RemoteSession.id.desc()))
        if existing:
            row = session.get(AppSetting, state_key(existing.id))
            if row:
                return result(existing, row.value)
            raise HTTPException(409, "Close the existing remote-session tracking entry before launching again")
        with remote.client() as api:
            org, endpoint_id = mapping.organization_id, mapping.action1_id
            endpoint = api.endpoint(org, endpoint_id)
            names = {name.lower().strip() for name in (device.name, device.dns_hostname) if name}
            if endpoint.get("id") != mapping.action1_id or str(endpoint.get("name") or "").lower().strip() not in names:
                raise remote.RemoteError("Action1 endpoint identity changed; refresh and verify the device mapping")
            if str(endpoint.get("status") or "").lower() != "connected":
                raise remote.RemoteError("The Action1 endpoint is offline; no session was requested")
            rs = RemoteSession(ticket_id=ticket.id, device_id=device.id, target=device.dns_hostname or device.name,
                tool="action1", started_by_id=user.id, started_at=sla.utcnow())
            session.add(rs)
            session.flush()
            state = AppSetting(key=state_key(rs.id), value={"status": "starting", "org": mapping.organization_id,
                "endpoint": mapping.action1_id, "configuration": fingerprint}, updated_by_id=user.id)
            session.add(state)
            services._event(session, ticket, EventType.remote_session, user, session_id=rs.id, tool="action1", target=rs.target, action="requested")
            services.add_comment(session, ticket, user, body=f"Requested Action1 remote assistance for {rs.target} (tracking #{rs.id}). Connection and duration are not yet confirmed.", is_internal=True, source=TicketSource.agent, as_staff=True)
            session.commit()  # Durable intent before the external write; crash/timeout cannot silently retry it.
            try:
                upstream = api.start(org, endpoint_id)
                provider_id = remote.identifier(str(upstream.get("id") or ""))
                state.value = {**state.value, "provider_id": provider_id, "status": "pending"}
            except remote.RemoteError as error:
                # A malformed successful response can also mean the request was accepted.
                uncertain = error.uncertain or "upstream" in locals()
                message = str(error) + (" Check Action1 before closing tracking or requesting another session." if uncertain else "")
                state.value = {**state.value, "status": "unknown" if uncertain else "failed", "message": message}
                if not uncertain:
                    rs.ended_at = sla.utcnow()
                services._event(session, ticket, EventType.remote_session, user, session_id=rs.id, tool="action1", action=state.value["status"])
            session.commit()
            return result(rs, state.value)
    except remote.RemoteError as error:
        raise HTTPException(409, str(error)) from None


@router.get("/api/tickets/{ticket_id}/action1-remote/{session_id}", response_model=Action1RemoteOut)
def poll(ticket_id: int, session_id: int, user: Operator, session: SessionDep, response: Response):
    private(response)
    ticket = staff_ticket(session, ticket_id, user)
    rs = session.get(RemoteSession, session_id)
    if rs is None or rs.ticket_id != ticket.id or rs.started_by_id != user.id or rs.tool != "action1":
        raise HTTPException(404, "No remote launch for this technician and ticket")
    state = session.scalar(select(AppSetting).where(AppSetting.key == state_key(rs.id)).with_for_update())
    if state is None:
        raise HTTPException(404, "Remote launch not found")
    data = state.value
    if rs.ended_at is not None or sla.utcnow() - rs.started_at > timedelta(minutes=5):
        return Action1RemoteOut(session_id=rs.id, status="expired", message="Launch tracking expired or was closed. Check Action1, then mark tracking ended before requesting another session.")
    try:
        _, mapping = linked(session, ticket, rs.device_id)
        if data["configuration"] != remote.configuration() or (data["org"], data["endpoint"]) != (mapping.organization_id, mapping.action1_id):
            raise remote.RemoteError("Remote configuration or device mapping changed; launch stopped")
        if data["status"] in ("failed", "unknown"):
            return result(rs, data)
        if not data.get("provider_id"):
            return result(rs, {**data, "status": "starting"})
        now = sla.utcnow().timestamp()
        if now < data.get("next_poll", 0):
            return result(rs, {**data, "status": "pending"})
        state.value = {**data, "next_poll": now + 5}
        with remote.client() as api:
            upstream = api.poll(data["org"], data["endpoint"], data["provider_id"])
        if str(upstream.get("id") or "") != data["provider_id"]:
            raise remote.RemoteError("Action1 returned a different remote session; launch stopped")
        url = remote.launch_url(upstream.get("remote_session")) if upstream.get("connected") == "yes" else None
        value = {**state.value, "status": "ready" if url else "pending"}
        if url and not data.get("link_returned"):
            value["link_returned"] = True
            services._event(session, ticket, EventType.remote_session, user, session_id=rs.id, tool="action1", action="launch_link_returned")
        state.value = value
        session.commit()
        return result(rs, value, url)
    except remote.RemoteError as error:
        session.commit()  # Retain the poll throttle even when the provider fails.
        raise HTTPException(502, str(error)) from None
