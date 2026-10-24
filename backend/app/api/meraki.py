# SPDX-License-Identifier: AGPL-3.0-or-later
"""Meraki credentials remain admin-only; technicians can read cached network observations."""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import meraki_oauth, meraki_diagnostics
from app.auth import COOKIE, _user_from_cookie, current_user, require_admin
from sqlalchemy import func, select

from app import meraki, meraki_webhooks
from app.auth import Admin, Agent, SessionDep
from app.config import settings
from app.models import MerakiClient, WorkerJob, MerakiWebhookReceipt
from app.schemas import MerakiClientOut, MerakiStatusOut, MerakiTestOut, MerakiDiagnosticOut

router = APIRouter(prefix="/api/meraki", tags=["meraki"])


@router.post("/webhooks", status_code=204, response_class=Response, openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "required": ["sharedSecret", "organizationId", "networkId", "alertTypeId"],
        "properties": {
            "sharedSecret": {"type": "string", "writeOnly": True},
            "version": {"type": "string", "description": "0.1 for availability alerts"},
            "organizationId": {"type": "string"}, "networkId": {"type": "string"},
            "alertTypeId": {"type": "string", "description": "stopped_reporting / started_reporting; other types are ignored"},
            "alertId": {"type": "string"}, "deviceSerial": {"type": "string"},
            "occurredAt": {"type": "string", "format": "date-time"},
            "sentAt": {"type": "string", "format": "date-time"},
        }, "additionalProperties": True,
    }}}}
})
async def webhook(request: Request, session: SessionDep):
    """Default Meraki JSON payload; authenticates with its dedicated sharedSecret, not a user cookie."""
    raw = await meraki_webhooks.read_body(request)
    await run_in_threadpool(meraki_webhooks.receive, session, raw)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


class WebhookDeliveryOut(BaseModel):
    network_id: str
    serial: str
    alert_type: str
    occurred_at: datetime
    received_at: datetime
    outcome: str
    ticket_id: int | None


class WebhookStatusOut(BaseModel):
    enabled: bool
    configured: bool
    endpoint: str | None
    deliveries: list[WebhookDeliveryOut]


@router.get("/webhooks/status", response_model=WebhookStatusOut)
def webhook_status(_: Admin, session: SessionDep):
    rows = session.scalars(select(MerakiWebhookReceipt).order_by(MerakiWebhookReceipt.received_at.desc(), MerakiWebhookReceipt.alert_id).limit(20))
    return dict(enabled=settings.meraki_webhook_enabled, configured=meraki_webhooks.configured(),
                endpoint=meraki_webhooks.endpoint(), deliveries=[{k: getattr(row, k) for k in WebhookDeliveryOut.model_fields} for row in rows])


@router.get("/status", response_model=MerakiStatusOut)
def status(_: Admin, session: SessionDep):
    job = session.get(WorkerJob, "meraki")
    count = session.scalar(select(func.count()).select_from(meraki.visible(session).subquery())) if meraki.on() else 0
    return dict(enabled=settings.meraki_enabled, configured=bool(meraki.configured() and meraki.network_ids()),
                auth_mode=settings.meraki_auth_mode, oauth=meraki_oauth.status(),
                clients=count, networks=meraki.network_ids(), last_sync_at=job.last_finished_at if job else None,
                last_sync_ok=job.last_ok if job else None, last_sync_error=job.last_error if job else None,
                last_sync_summary=job.last_summary if job else None)


@router.post("/test", response_model=MerakiTestOut)
def test(_: Admin):
    client = None
    try:
        client = meraki.from_settings()
        return {"networks": [client.network(n)["name"] for n in meraki.network_ids()]}
    except meraki.MerakiError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        if client:
            client.close()


@router.get("/clients", response_model=list[MerakiClientOut])
def clients(_: Agent, session: SessionDep, q: str = Query("", max_length=100), limit: int = Query(200, ge=1, le=1000)):
    if not meraki.on():
        return []
    stmt = meraki.visible(session)
    if q.strip():
        stmt = stmt.where(MerakiClient.description.icontains(q.strip(), autoescape=True) |
                          MerakiClient.address.icontains(q.strip(), autoescape=True) |
                          MerakiClient.mac.icontains(q.strip(), autoescape=True) |
                          MerakiClient.network_name.icontains(q.strip(), autoescape=True))
    return [meraki.as_dict(item) for item in session.scalars(stmt.order_by(MerakiClient.network_id, MerakiClient.mac).limit(limit))]


class MerakiStartOut(BaseModel):
    url: str


class MerakiDisconnectOut(BaseModel):
    revoked: bool


@router.post("/oauth/connect", response_model=MerakiStartOut)
def connect(user: Admin, request: Request, session: SessionDep):
    browser_user = _user_from_cookie(session, request)
    if browser_user is None or browser_user.id != user.id:
        raise HTTPException(409, "Sign in with a browser session before connecting Meraki")
    meraki_oauth.lock(session)
    try:
        return {"url": meraki_oauth.begin(user.id, request.cookies.get(COOKIE))}
    except meraki.MerakiError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/oauth/callback", include_in_schema=False)
def callback(request: Request, session: SessionDep):
    # Always leave the authorization URL, including if the Layer 8 session expired.
    result = "failed"
    try:
        user = require_admin(current_user(request, session))
        meraki_oauth.lock(session)
        meraki_oauth.finish(user.id, request.cookies.get(COOKIE), request.state.meraki_callback)
        result = "connected"
    except (meraki.MerakiError, HTTPException):
        pass
    return RedirectResponse("/settings/meraki?meraki_oauth=" + result, status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.delete("/oauth/connection", response_model=MerakiDisconnectOut)
def disconnect(_: Admin, session: SessionDep):
    meraki_oauth.lock(session)
    try:
        return {"revoked": meraki_oauth.disconnect()}
    except meraki.MerakiError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/clients/{network_id}/{mac}/diagnostics", response_model=MerakiDiagnosticOut | None)
def diagnostic_cache(network_id: str, mac: str, _: Agent, session: SessionDep):
    item = meraki_diagnostics.selected(session, network_id, mac)
    return meraki_diagnostics.cached(session, item)


@router.post("/clients/{network_id}/{mac}/diagnostics", response_model=MerakiDiagnosticOut)
def read_diagnostics(network_id: str, mac: str, _: Agent, session: SessionDep):
    return meraki_diagnostics.read(session, network_id, mac)
