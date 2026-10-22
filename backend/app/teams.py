"""Optional Teams Workflows digest. Only aggregate, non-restricted ticket counts leave the app."""

import re
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import sla
from app.config import settings
from app.models import Queue, Ticket, TicketStatus


class TeamsError(RuntimeError):
    """Messages are safe for the system log: never include the signed webhook URL."""


def validate_url(value: str) -> None:
    if not value:
        return
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        valid = (
            url.scheme == "https" and url.port in (None, 443)
            and not url.username and not url.password and not url.fragment
            and not re.search(r"[\s\\]", value)
            and (
                (host.endswith(".environment.api.powerplatform.com") and url.path.startswith("/powerautomate/automations/direct/workflows/"))
                or (host.endswith(".logic.azure.com") and url.path.startswith("/workflows/"))
            )
        )
    except ValueError:
        valid = False
    if not valid:
        raise TeamsError("Use an HTTPS Microsoft Teams Workflows URL on powerplatform.com or logic.azure.com (port 443).")


def counts(session: Session) -> dict[str, int]:
    base = select(Ticket.id).join(Queue).where(Queue.restricted.is_(False))
    opened = Ticket.status.not_in(sla.CLOSED_STATUSES)
    filters = {
        "Open tickets": opened,
        "Unassigned": opened & Ticket.assignee_id.is_(None),
        "SLA at risk (including breached)": opened & (Ticket.sla_at_risk_at <= sla.utcnow()),
        "Waiting on user": Ticket.status == TicketStatus.waiting_on_user,
    }
    return {label: session.scalar(select(func.count()).select_from(base.where(condition).subquery())) or 0 for label, condition in filters.items()}


def card(values: dict[str, int]) -> dict:
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "contentUrl": None,
        "content": {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard", "version": "1.2",
            "body": [
                {"type": "TextBlock", "text": "Layer 8 · Queue summary", "weight": "Bolder", "wrap": True},
                {"type": "FactSet", "facts": [{"title": label, "value": str(value)} for label, value in values.items()]},
                {"type": "TextBlock", "text": "Non-restricted queues only. Counts overlap; no ticket details are included.", "wrap": True, "isSubtle": True},
            ],
        },
    }]}


def send(payload: dict) -> None:
    validate_url(settings.teams_webhook_url)
    if not settings.teams_webhook_url:
        raise TeamsError("Set the webhook in Settings > Teams.")
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.post(settings.teams_webhook_url, json=payload)
        if not 200 <= response.status_code < 300:
            raise TeamsError(f"Teams delivery returned HTTP {response.status_code}; check the workflow and try the next scheduled run.")
    except httpx.HTTPError:
        # HTTP exception strings contain the URL and its signing secret.
        raise TeamsError("Teams delivery could not connect; check the workflow and network.") from None


def run(session: Session) -> tuple[str, bool]:
    if not settings.teams_enabled:
        return "Off: Settings > Teams", False
    if not settings.teams_webhook_url:
        return "Waiting for webhook: Settings > Teams", False
    send(card(counts(session)))
    return "Sent non-restricted queue counts to Teams", True
