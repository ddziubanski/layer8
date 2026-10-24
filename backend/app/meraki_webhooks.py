# SPDX-License-Identifier: AGPL-3.0-or-later
"""Default Meraki device availability alerts. No Dashboard API calls or raw payload storage."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app import services, sla
from app.config import settings
from app.models import MerakiWebhookIncident as Incident, MerakiWebhookReceipt as Receipt
from app.models import Priority, Queue, Ticket, TicketSource, TicketStatus

MAX_BODY = 65536
LOCK = 82638117
MAX_RECEIPTS = 100000
MAX_INCIDENTS = 10000
MAX_PER_MINUTE = 60
TYPES = {"stopped_reporting", "started_reporting"}
OPEN = (TicketStatus.new, TicketStatus.open, TicketStatus.waiting_on_user)


def endpoint() -> str | None:
    value = settings.portal_url.rstrip("/")
    try:
        url = urlsplit(value)
        if url.scheme == "https" and url.hostname and not url.username and not url.password and not url.query and not url.fragment:
            return value + "/api/meraki/webhooks"
    except ValueError:
        pass
    return None


def configured() -> bool:
    return bool(endpoint() and 32 <= len(settings.meraki_webhook_secret) <= 256
                and settings.meraki_webhook_organization_ids and settings.meraki_network_ids)


async def read_body(request: Request) -> bytes:
    # Content-Length alone does not bound chunked requests. Never echo validation input.
    if request.headers.get("content-encoding", "identity") != "identity":
        raise HTTPException(415, "Use uncompressed JSON")
    size = request.headers.get("content-length")
    if size is not None and (len(size) > 10 or not size.isdecimal() or int(size) > MAX_BODY):
        raise HTTPException(413, "Webhook body is too large")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Use application/json")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "Webhook body is too large")
        body.extend(chunk)
    return bytes(body)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse(raw: bytes, now: datetime) -> dict | None:
    if not settings.meraki_webhook_enabled or not configured():
        raise HTTPException(503, "Meraki webhook intake is not configured")
    try:
        if len(raw) > MAX_BODY:
            raise ValueError()
        data = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(data, dict):
            raise ValueError()
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, "Invalid webhook JSON") from None
    supplied = data.get("sharedSecret")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode("utf-8", errors="surrogatepass"), settings.meraki_webhook_secret.encode("utf-8", errors="surrogatepass")):
        raise HTTPException(401, "Invalid webhook credentials")
    # Only explicit scope and exact documented type IDs are accepted; never infer recovery from names.
    if data.get("organizationId") not in settings.meraki_webhook_organization_ids or data.get("networkId") not in settings.meraki_network_ids:
        raise HTTPException(403, "Webhook source is not allowed")
    if not isinstance(data.get("alertTypeId"), str):
        raise HTTPException(422, "Invalid Meraki availability alert")
    if data["alertTypeId"] not in TYPES:
        return None
    try:
        if str(data.get("version")) != "0.1":
            raise ValueError()
        fields = {"organization_id": data["organizationId"], "network_id": data["networkId"],
                  "alert_id": data["alertId"], "serial": data["deviceSerial"], "alert_type": data["alertTypeId"]}
        for key in ("organization_id", "network_id", "alert_id", "serial"):
            limit = 32 if key == "serial" else 100
            if not isinstance(fields[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1," + str(limit) + "}", fields[key]):
                raise ValueError()
        for name in ("occurredAt", "sentAt"):
            value = data[name]
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError()
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if when.tzinfo is None or when > now + timedelta(minutes=5):
                raise ValueError()
            if when < now - timedelta(days=7):
                return None  # Old retries cannot recreate tickets after receipt retention expires.
            if name == "occurredAt":
                fields["occurred_at"] = when
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "Invalid Meraki availability alert") from None
    return fields


def fingerprint(alert: dict) -> str:
    values = {**alert, "occurred_at": alert["occurred_at"].astimezone(timezone.utc).isoformat()}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def receive(session: Session, raw: bytes) -> None:
    now = sla.utcnow()
    alert = parse(raw, now)
    if alert is None:
        return
    # One global transaction lock also serializes automation-user creation, replay checks and limits.
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK}):
        raise HTTPException(503, "Webhook intake is busy; retry shortly", headers={"Retry-After": "5"})
    old = session.get(Receipt, (alert["organization_id"], alert["alert_id"]))
    digest = fingerprint(alert)
    if old:
        if old.fingerprint != digest:
            raise HTTPException(409, "Alert ID conflicts with a previous delivery")
        return
    recent = session.scalar(select(func.count()).select_from(Receipt).where(Receipt.received_at >= now - timedelta(minutes=1)))
    if recent >= MAX_PER_MINUTE:
        raise HTTPException(429, "Webhook intake rate exceeded", headers={"Retry-After": "60"})
    session.execute(delete(Receipt).where(Receipt.received_at < now - timedelta(days=8)))
    session.execute(delete(Incident).where(Incident.received_at < now - timedelta(days=90)))
    if session.scalar(select(func.count()).select_from(Receipt)) >= MAX_RECEIPTS:
        raise HTTPException(503, "Webhook history capacity reached")
    key = (alert["organization_id"], alert["network_id"], alert["serial"])
    incident = session.get(Incident, key)
    if incident is None:
        if session.scalar(select(func.count()).select_from(Incident)) >= MAX_INCIDENTS:
            raise HTTPException(503, "Webhook device capacity reached")
        incident = Incident(organization_id=key[0], network_id=key[1], serial=key[2],
                            occurred_at=alert["occurred_at"] - timedelta(microseconds=1), received_at=now, is_down=False)
        session.add(incident)
    outcome = "stale"
    if alert["occurred_at"] > incident.occurred_at:
        ticket = session.get(Ticket, incident.ticket_id) if incident.ticket_id else None
        active = ticket is not None and ticket.status in OPEN
        down = alert["alert_type"] == "stopped_reporting"
        if down and not active:
            queue = None
            if settings.meraki_webhook_queue_id:
                queue = session.get(Queue, settings.meraki_webhook_queue_id)
                if queue is None or not queue.is_active:
                    raise HTTPException(503, "Webhook fallback queue is unavailable")
            bot = services.automation_user(session)
            ticket = services.create_ticket(session, subject=f"Meraki device stopped reporting: {key[2]}",
                description=f"Meraki reports device {key[2]} stopped reporting.\nNetwork: {key[1]}\nOrganization: {key[0]}\nOccurred: {alert['occurred_at'].isoformat()}\nAlert: {alert['alert_id']}\n\nCheck the device and network. Recovery adds an internal note and requires technician review.",
                category=None, requester=bot, actor=bot, source=TicketSource.api, priority=Priority.high, preferred_queue=queue)
            incident.ticket_id = ticket.id
            outcome = "created"
        elif active and down != incident.is_down:
            bot = services.automation_user(session)
            services.add_comment(session, ticket, bot, body=f"Meraki device {key[2]} {'stopped reporting again' if down else 'started reporting (recovery)'}.\nOccurred: {alert['occurred_at'].isoformat()}\nAlert: {alert['alert_id']}\nTechnician review is required; ticket status was not changed.",
                                 is_internal=True, source=TicketSource.api, email_requester=False)
            outcome = "ongoing" if down else "recovery"
        else:
            outcome = "ongoing" if down else "unmatched_recovery"
        incident.is_down, incident.occurred_at, incident.received_at = down, alert["occurred_at"], now
    session.add(Receipt(**alert, fingerprint=digest, received_at=now, outcome=outcome, ticket_id=incident.ticket_id))
    session.commit()  # Receipt, state and normal audited ticket operations commit atomically.
