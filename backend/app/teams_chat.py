"""Delegated Teams access. Tokens and pending OAuth flows never leave secret files."""
import hashlib
import json
import re
import secrets
import time
from contextlib import contextmanager
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
import msal

from app import configstore
from app.config import settings

SCOPES = ["User.Read", "Chat.ReadBasic", "ChatMessage.Send"]
# Microsoft 365 Copilot Chat API (beta): all seven are required; asked for only when Copilot is on.
COPILOT_SCOPES = ["Sites.Read.All", "Mail.Read", "People.Read.All", "OnlineMeetingTranscript.Read.All", "Chat.Read", "ChannelMessage.Read.All", "ExternalItem.Read.All"]


def scopes():
    return SCOPES + (COPILOT_SCOPES if settings.copilot_enabled else [])
GRAPH = "https://graph.microsoft.com/v1.0"


class ConnectionError(ValueError):
    """Only static, safe messages are exposed to callers."""


def configuration():
    try:
        tenant, client = str(UUID(settings.teams_tenant_id)), str(UUID(settings.teams_client_id))
        base = urlsplit(settings.portal_url)
        valid = (base.scheme == "https" and base.hostname and not base.username
                 and not base.password and not base.query and not base.fragment
                 and base.path in ("", "/") and not re.search(r"[\s\\]", settings.portal_url))
        if not settings.teams_chat_enabled or not settings.teams_client_secret or not valid:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ConnectionError("An administrator must configure Teams sign-in and the HTTPS portal address first") from None
    return {"tenant": tenant, "client": client, "redirect": settings.portal_url.rstrip("/") + "/teams/callback"}


def path(user_id):
    return settings.secrets_dir / f"teams-user-{int(user_id)}.json"


@contextmanager
def oauth_client(cache):
    config = configuration()
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as http:
        yield msal.ConfidentialClientApplication(
            config["client"], authority=f"https://login.microsoftonline.com/{config['tenant']}",
            client_credential=settings.teams_client_secret, token_cache=cache,
            http_client=http, instance_discovery=False,
        )


def write(path_, value):
    configstore.write_secret_file(path_, json.dumps(value).encode())


def read(user_id):
    try:
        record = json.loads(path(user_id).read_text(encoding="utf-8"))
        if record["config"] != configuration():
            raise ConnectionError("Teams registration changed; reconnect your Microsoft account")
        return record
    except (OSError, ValueError, KeyError):
        raise ConnectionError("Connect your Microsoft account in My preferences first") from None


def status(user_id):
    try:
        config = configuration()
    except ConnectionError:
        return {"configured": False, "connected": False, "account": None, "copilot": False}
    try:
        record = read(user_id)
    except ConnectionError:
        return {"configured": True, "connected": False, "account": None, "copilot": False}
    return {"configured": bool(config), "connected": True, "account": record["account"], "copilot": bool(record.get("copilot"))}


def binding(cookie):
    if not cookie:
        raise ConnectionError("Sign in to Layer 8 in this browser before connecting Microsoft Teams")
    return hashlib.sha256(cookie.encode()).hexdigest()


def begin(user, cookie):
    config = configuration()
    session_hash = binding(cookie)
    state = secrets.token_urlsafe(32)
    cache = msal.SerializableTokenCache()
    try:
        with oauth_client(cache) as client:
            flow = client.initiate_auth_code_flow(
                scopes(), redirect_uri=config["redirect"], state=state,
                response_mode="fragment", login_hint=user.upn or user.email,
                prompt="select_account",
            )
    except (httpx.HTTPError, ValueError):
        raise ConnectionError("Microsoft sign-in could not be started; try again later") from None
    # Keep at most one pending connection per technician; user-row locking serializes writes.
    write(settings.secrets_dir / f"teams-pending-{user.id}.json", {
        "flow": flow, "state": state, "session": session_hash, "created": time.time(), "config": config,
    })
    return flow["auth_uri"]


def graph(method, route, token, **kwargs):
    try:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, GRAPH + route, headers={"Authorization": "Bearer " + token}, **kwargs)
        if response.status_code not in (200, 201):
            raise ConnectionError("Microsoft Teams did not accept the request; check your connection and permissions")
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise ConnectionError("Microsoft Teams could not confirm the request; check Teams before retrying a message") from None


def finish(user, cookie, response):
    pending_path = settings.secrets_dir / f"teams-pending-{user.id}.json"
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        valid = (secrets.compare_digest(pending["state"], response.get("state", ""))
                 and secrets.compare_digest(pending["session"], binding(cookie))
                 and 0 <= time.time() - pending["created"] <= 600
                 and pending["config"] == configuration())
        if not valid:
            raise ValueError
        pending_path.unlink()  # consume once, before exchanging the code
    except (OSError, ValueError, KeyError):
        raise ConnectionError("Microsoft sign-in expired or belongs to another browser session; connect again") from None
    cache = msal.SerializableTokenCache()
    try:
        with oauth_client(cache) as client:
            result = client.acquire_token_by_auth_code_flow(pending["flow"], response)
        token = result.get("access_token")
        if not token:
            raise ValueError
        identity = graph("GET", "/me?$select=id,userPrincipalName,mail", token)
        claims = result.get("id_token_claims", {})
        if claims.get("tid", "").lower() != configuration()["tenant"] or claims.get("oid") != identity.get("id"):
            raise ValueError
        permitted = {v.casefold() for v in (user.email, user.upn) if v}
        addresses = {v.casefold() for v in (identity.get("mail"), identity.get("userPrincipalName")) if v}
        if not permitted.intersection(addresses):
            raise ConnectionError("Choose the Microsoft work account matching your Layer 8 email or directory UPN")
    except (httpx.HTTPError, ValueError, KeyError):
        raise ConnectionError("Microsoft account verification failed; use the work account matching your Layer 8 email or UPN") from None
    granted = {s.casefold() for s in str(result.get("scope", "")).split()}
    write(path(user.id), {"config": configuration(), "oid": identity["id"],
                         "account": identity["userPrincipalName"], "cache": cache.serialize(),
                         "copilot": all(s.casefold() in granted for s in COPILOT_SCOPES)})


def disconnect(user_id):
    path(user_id).unlink(missing_ok=True)
    (settings.secrets_dir / f"teams-pending-{user_id}.json").unlink(missing_ok=True)


def access(user_id, wanted=None):
    """A delegated token for `wanted` scopes (default: the Teams ones), with the stored record."""
    record = read(user_id)
    cache = msal.SerializableTokenCache()
    try:
        cache.deserialize(record["cache"])
        with oauth_client(cache) as client:
            accounts = client.get_accounts()
            result = client.acquire_token_silent(wanted or SCOPES, account=accounts[0]) if len(accounts) == 1 else None
        # Persist rotated refresh tokens before any downstream request, including failures.
        if cache.has_state_changed:
            record["cache"] = cache.serialize()
            write(path(user_id), record)
        if not result or not result.get("access_token"):
            raise ValueError
        return result["access_token"], record
    except (httpx.HTTPError, ValueError, KeyError):
        raise ConnectionError("Your Microsoft session needs attention; reconnect Teams in My preferences") from None


def matches(chat, requester, oid):
    addresses = {v.casefold() for v in (requester.email, requester.upn) if v}
    members = chat.get("members", [])
    return (chat.get("chatType") == "oneOnOne" and len(members) == 2
            and not chat.get("members@odata.nextLink")
            and sum(m.get("userId") == oid for m in members) == 1
            and any(m.get("userId") != oid and (m.get("email") or "").casefold() in addresses for m in members))


def chats(token, requester, oid):
    route = "/me/chats?$filter=chatType%20eq%20'oneOnOne'&$expand=members&$top=50"
    found = []
    for _ in range(20):
        page = graph("GET", route, token)
        found.extend({"id": c["id"], "recipient": requester.upn or requester.email} for c in page.get("value", []) if matches(c, requester, oid))
        next_url = page.get("@odata.nextLink")
        if not next_url:
            return found
        url = urlsplit(next_url)
        if url.scheme != "https" or url.netloc != "graph.microsoft.com" or url.path != "/v1.0/me/chats" or url.fragment:
            raise ConnectionError("Microsoft returned an unexpected chat listing")
        route = url.path[len('/v1.0'):] + "?" + url.query
    raise ConnectionError("Too many Teams chats to search; no partial result was used")


def send(token, chat_id, body):
    return graph("POST", f"/chats/{quote(chat_id, safe='')}/messages", token,
                 json={"body": {"contentType": "text", "content": body}})
