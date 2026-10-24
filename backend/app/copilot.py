# SPDX-License-Identifier: AGPL-3.0-or-later
"""Microsoft 365 Copilot for technicians, from a ticket (Settings > Teams > Copilot on tickets).

Uses Microsoft Graph's Copilot Chat API (beta: Microsoft says it isn't supported for production
use, and it may change). It only takes delegated tokens, so each technician asks as themselves
through their own Microsoft connection (My preferences), and needs a Microsoft 365 Copilot licence.
Copilot answers from what that technician can already see in Microsoft 365, plus, when they choose,
the ticket's text, sent as additional context.

    POST /beta/copilot/conversations                       -> {"id": ...}
    POST /beta/copilot/conversations/{id}/chat             {"message": {"text"}, "locationHint": {"timeZone"}, "additionalContext": [{"text"}]}
"""

import os
import hashlib
import json
from urllib.parse import quote

import httpx

GRAPH_BETA = "https://graph.microsoft.com/beta"
# Ticket text sent as context: enough for the gist, not a whole mailbox thread.
MAX_CONTEXT = 12000
LAST_COMMENTS = 10


class CopilotError(RuntimeError):
    """Messages are safe to show to the technician."""


def time_zone() -> str:
    """The server's IANA time zone (e.g. America/Los_Angeles), which Copilot needs for "today"."""
    if tz := os.environ.get("TZ"):
        return tz.lstrip(":")
    real = os.path.realpath("/etc/localtime")
    return real.split("zoneinfo/", 1)[1] if "zoneinfo/" in real else "UTC"


def ticket_context(ticket, comments, *, include_internal: bool = False) -> list[str]:
    """The ticket's subject, description and latest comments as plain text, capped."""
    parts = [f"Help desk ticket {ticket.id}: {ticket.subject}\nStatus: {ticket.status.value}. Priority: {ticket.priority.value}. "
             f"Requester: {ticket.requester.display_name}.\n\n{(ticket.description or '').strip()}"]
    visible = [c for c in comments if include_internal or not c.is_internal]
    for c in visible[-LAST_COMMENTS:]:
        kind = "Internal note" if c.is_internal else "Reply"
        parts.append(f"{kind} by {c.author.display_name}, {c.created_at:%Y-%m-%d %H:%M}:\n{c.body.strip()}")
    out, size = [], 0
    for part in parts:
        part = part[: MAX_CONTEXT - size]
        if not part:
            break
        out.append(part)
        size += len(part)
    return out


def context_digest(context: list[str]) -> str:
    return hashlib.sha256(json.dumps(context, ensure_ascii=False).encode()).hexdigest()


def account_key(record: dict) -> str:
    """Bind a conversation to the delegated account and app registration, not its email label."""
    try:
        parts = [record["config"]["tenant"], record["config"]["client"], record["oid"]]
        if not all(isinstance(part, str) and part for part in parts):
            raise KeyError
    except (KeyError, TypeError):
        raise CopilotError("Reconnect your Microsoft account before starting a Copilot conversation") from None
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _post(http: httpx.Client, token: str, path: str, body: dict) -> dict:
    try:
        r = http.post(GRAPH_BETA + path, json=body, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise CopilotError(f"Microsoft 365 Copilot could not be reached ({type(exc).__name__}); try again") from None
    if r.status_code in (401, 403):
        raise CopilotError("Copilot refused: your account needs a Microsoft 365 Copilot licence, and an admin must consent to the Copilot permissions (Settings > Teams)")
    if r.status_code == 429:
        raise CopilotError("Copilot is busy; try again in a minute")
    if r.status_code not in (200, 201):
        raise CopilotError(f"Copilot returned HTTP {r.status_code}")
    try:
        result = r.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except ValueError:
        raise CopilotError("Copilot sent an answer that couldn't be read") from None


def ask(token: str, question: str, context: list[str], conversation_id: str | None = None, *, http: httpx.Client | None = None) -> dict:
    """One question, in a new conversation or a running one. Returns {conversation_id, answer, citations}."""
    own = http is None
    http = http or httpx.Client(timeout=90, follow_redirects=False, trust_env=False)
    try:
        if not conversation_id:
            conversation_id = _post(http, token, "/copilot/conversations", {}).get("id")
            if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 200:
                raise CopilotError("Copilot didn't start a conversation")
        body = {"message": {"text": question}, "locationHint": {"timeZone": time_zone()}}
        if context:
            body["additionalContext"] = [{"text": c} for c in context]
        reply = _post(http, token, f"/copilot/conversations/{quote(conversation_id, safe='')}/chat", body)
    finally:
        if own:
            http.close()
    messages = reply.get("messages") or []
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        raise CopilotError("Copilot sent no answer")
    last = messages[-1]
    if not isinstance(last.get("text"), str) or not last["text"].strip():
        raise CopilotError("Copilot sent no answer")
    citations, seen = [], set()
    for a in last.get("attributions") or []:
        if not isinstance(a, dict):
            continue
        url = a.get("seeMoreWebUrl") or ""
        if a.get("attributionType") == "citation" and url.startswith("https://") and url not in seen:
            seen.add(url)
            citations.append({"title": a.get("providerDisplayName") or url, "url": url})
    return {"conversation_id": conversation_id, "answer": last.get("text") or "", "citations": citations}
