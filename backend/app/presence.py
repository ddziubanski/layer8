"""Technicians' Teams status for the portal (status only: no chat or call links anywhere).

Read through Microsoft Graph with the Email tab's app registration (client credentials). It needs
the application permissions Presence.Read.All (POST /communications/getPresencesByUserId) and
User.ReadBasic.All (each technician's Entra object ID, looked up once by UPN or email), with admin
consent. The worker job "presence" refreshes it every minute while Settings > Desk hours >
Show technicians' Teams status is on; the portal shows only what it stored.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sla
from app.config import settings
from app.mail.graph import GRAPH, missing, msal_token_provider
from app.models import Queue, User, UserRole

log = logging.getLogger(__name__)

# Graph's availability values, folded into what the portal shows.
FOLD = {
    "Available": "available", "AvailableIdle": "available",
    "Busy": "busy", "BusyIdle": "busy", "DoNotDisturb": "busy",
    "BeRightBack": "away", "Away": "away",
    "Offline": "offline",
}
# Older than this, a stored status is shown as unknown (the job stopped, or Graph refused).
FRESH_FOR = timedelta(minutes=10)
CHUNK = 650  # getPresencesByUserId takes up to 650 IDs per call


class PresenceError(RuntimeError):
    pass


def technicians(session: Session) -> list[User]:
    """Owners of the queues the portal shows: the people whose status it shows."""
    queues = session.scalars(select(Queue).where(Queue.show_in_portal, Queue.is_active))
    people = {u.id: u for q in queues for u in q.owners if u.is_active and u.role in (UserRole.agent, UserRole.admin)}
    return list(people.values())


def shown(user: User, now: datetime) -> str:
    """What the portal shows for someone: their folded status, or "unknown" when it's stale."""
    if user.presence and user.presence_at and now - user.presence_at <= FRESH_FOR:
        return user.presence
    return "unknown"


def _graph(http: httpx.Client, token: str, method: str, path: str, **kw) -> httpx.Response:
    try:
        return http.request(method, GRAPH + path, headers={"Authorization": f"Bearer {token}"}, **kw)
    except httpx.HTTPError as exc:
        raise PresenceError(f"Microsoft Graph could not be reached: {type(exc).__name__}") from None


def refresh(session: Session, *, http: httpx.Client | None = None, token: str | None = None) -> tuple[str, bool]:
    """One pass: look up missing Entra IDs, then read everyone's status. Returns (what it did, did anything)."""
    if not settings.presence_on_portal:
        return "Off: Settings > Desk hours", False
    if token is None:
        if gaps := [g for g in missing(settings) if g != "mailbox"]:
            return f"Waiting for the app registration in Settings > Email ({', '.join(gaps)})", False
        try:
            token = msal_token_provider(settings.graph_tenant_id, settings.graph_client_id, settings.graph_client_secret)()
        except RuntimeError as exc:
            raise PresenceError(str(exc)) from None
    people = technicians(session)
    if not people:
        return "No technicians: give the portal's queues owners in Settings > Queues", False
    own = http is None
    http = http or httpx.Client(timeout=15, follow_redirects=False)
    try:
        for user in people:
            if user.entra_id:
                continue
            name = user.upn or user.email
            r = _graph(http, token, "GET", f"/users/{quote(name, safe='@')}?$select=id")
            if r.status_code == 404:
                log.info("Teams status: no Microsoft 365 account for %s", name)
                continue
            if r.status_code in (401, 403):
                raise PresenceError("Graph refused the user lookup: grant the app registration User.ReadBasic.All (application) with admin consent")
            if r.status_code != 200:
                raise PresenceError(f"Graph user lookup returned HTTP {r.status_code}")
            user.entra_id = r.json().get("id")
        known = {u.entra_id: u for u in people if u.entra_id}
        now = sla.utcnow()
        ids = list(known)
        for i in range(0, len(ids), CHUNK):
            r = _graph(http, token, "POST", "/communications/getPresencesByUserId", json={"ids": ids[i : i + CHUNK]})
            if r.status_code in (401, 403):
                raise PresenceError("Graph refused to read presence: grant the app registration Presence.Read.All (application) with admin consent")
            if r.status_code != 200:
                raise PresenceError(f"Graph presence returned HTTP {r.status_code}")
            for p in r.json().get("value", []):
                if (user := known.get(p.get("id"))) is not None:
                    user.presence, user.presence_at = FOLD.get(p.get("availability", ""), None), now
    finally:
        if own:
            http.close()
    return f"Teams status read for {len(known)} of {len(people)} technician(s)", bool(known)
