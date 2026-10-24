# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional, admin-owned Meraki authorization. Call mutations/access under lock().

Only the documented read scopes are requested. A durable refresh marker prevents
reusing a rotating refresh token after a crash, timeout or failed credential write.
"""
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import text

from app.config import settings
from app.meraki import MerakiError, network_ids

SCOPES = "dashboard:general:config:read dashboard:general:telemetry:read"
CALLBACK = "/api/meraki/oauth/callback"
TOKEN_URL = "https://as.meraki.com/oauth/token"
RECONNECT = "Meraki OAuth needs reconnecting in Settings > Meraki"


def scopes():
    return SCOPES + (" switch:telemetry:read wireless:telemetry:read" if settings.meraki_diagnostics_enabled else "")


def lock(session):
    # A different lock from discovery: API workers and the scheduler share it.
    session.execute(text("SELECT pg_advisory_xact_lock(82638115)"))


def configuration():
    try:
        url = urlsplit(settings.portal_url)
    except ValueError:
        raise MerakiError("Save a valid HTTPS portal base address first") from None
    if (settings.meraki_auth_mode != "oauth" or not settings.meraki_client_id.strip()
            or not settings.meraki_client_secret or url.scheme != "https" or not url.hostname
            or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
        raise MerakiError("Save OAuth client credentials and an HTTPS portal base address first")
    redirect = settings.portal_url.rstrip("/") + CALLBACK
    fingerprint = hashlib.sha256(json.dumps([settings.meraki_client_id, settings.meraki_client_secret, redirect, scopes()]).encode()).hexdigest()
    return redirect, fingerprint


def _path(name):
    return settings.secrets_dir / ("meraki-oauth-" + name + ".json")


def _read(name):
    try:
        data = json.loads(_path(name).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise MerakiError(RECONNECT) from None


def _write(name, data):
    """Atomic replacement, fsync before sending a rotating token to the provider."""
    path = _path(name)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = path.with_suffix(".new")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except OSError:
        raise MerakiError("Cannot save the Meraki connection securely; reconnect after checking the secrets directory") from None


def status():
    try:
        redirect, fingerprint = configuration()
        record = _read("tokens")
        connected = bool(record.get("config") == fingerprint and record.get("refresh_token") and not record.get("refreshing"))
        return dict(configured=True, connected=connected, redirect_uri=redirect)
    except MerakiError:
        return dict(configured=False, connected=False, redirect_uri=None)


def _binding(cookie):
    if not cookie:
        raise MerakiError("Sign in with a browser session before connecting Meraki")
    return hashlib.sha256(cookie.encode()).hexdigest()


def begin(user_id, cookie):
    redirect, fingerprint = configuration()
    if not network_ids():
        raise MerakiError("Save explicit Meraki network IDs before connecting")
    binding = _binding(cookie)
    state = secrets.token_urlsafe(32)
    _write("pending", dict(state=hashlib.sha256(state.encode()).hexdigest(), user=user_id,
                          session=binding, created=time.time(), config=fingerprint))
    # Meraki documents query callbacks, not fragment response_mode or PKCE.
    return "https://as.meraki.com/oauth/authorize?" + urlencode(dict(
        response_type="code", client_id=settings.meraki_client_id, redirect_uri=redirect,
        scope=scopes(), state=state))


def _exchange(data):
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.post(TOKEN_URL, data=data, auth=(settings.meraki_client_id, settings.meraki_client_secret))
        if response.status_code != 200:
            raise MerakiError("Meraki refused authorization; reconnect and check the application registration")
        token = response.json()
        if not isinstance(token, dict):
            raise ValueError()
        for field in ("access_token", "refresh_token"):
            value = token.get(field)
            if not isinstance(value, str) or not 1 <= len(value) <= 16384 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError()
        expiry = token.get("expires_in")
        if type(expiry) is not int or not 1 <= expiry <= 3600 or str(token.get("token_type", "")).lower() != "bearer":
            raise ValueError()
        if set(str(token.get("scope", scopes())).split()) != set(scopes().split()):
            raise ValueError()
        if data.get("refresh_token") == token["refresh_token"]:
            raise ValueError()
        return dict(access_token=token["access_token"], refresh_token=token["refresh_token"], expires_at=time.time() + expiry)
    except (httpx.HTTPError, ValueError):
        raise MerakiError("Meraki authorization could not be verified; reconnect to continue") from None


def finish(user_id, cookie, params):
    redirect, fingerprint = configuration()
    pending = _read("pending")
    state = params.get("state", "")
    if (not isinstance(state, str) or len(state) > 256 or not state
            or not secrets.compare_digest(pending.get("state", ""), hashlib.sha256(state.encode()).hexdigest())
            or pending.get("user") != user_id or pending.get("session") != _binding(cookie)):
        raise MerakiError("Meraki authorization does not match this browser session; connect again")
    # Consume before any provider request, including denial and malformed responses.
    _write("pending", {})
    if pending.get("config") != fingerprint or not 0 <= time.time() - pending.get("created", 0) <= 600:
        raise MerakiError("Meraki authorization expired or configuration changed; connect again")
    code = params.get("code")
    if params.get("error") or not isinstance(code, str) or not 1 <= len(code) <= 8192:
        raise MerakiError("Meraki authorization was not completed; connect again")
    token = _exchange(dict(grant_type="authorization_code", code=code, redirect_uri=redirect, scope=scopes()))
    _write("tokens", dict(config=fingerprint, **token))


def access():
    _, fingerprint = configuration()
    record = _read("tokens")
    if record.get("config") != fingerprint or not record.get("refresh_token") or record.get("refreshing"):
        raise MerakiError(RECONNECT)
    if record.get("expires_at", 0) > time.time() + 60:
        return record["access_token"]
    # Never retry this refresh token after an uncertain exchange, even after restart.
    _write("tokens", {**record, "refreshing": True})
    token = _exchange(dict(grant_type="refresh_token", refresh_token=record["refresh_token"]))
    _write("tokens", dict(config=fingerprint, **token))
    return token["access_token"]


def disconnect():
    # Forget locally first, even if Meraki is unavailable. Never retain a usable
    # local connection just because remote revocation failed.
    try:
        record = _read("tokens")
    except MerakiError:
        record = {"unreadable": True}
    _write("tokens", {})
    _write("pending", {})
    if not record.get("refresh_token"):
        return not record.get("unreadable", False)
    try:
        _, fingerprint = configuration()
        if record.get("config") != fingerprint:
            return False
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.post("https://as.meraki.com/oauth/revoke", auth=(settings.meraki_client_id, settings.meraki_client_secret),
                                   data=dict(token=record["refresh_token"], token_type_hint="refresh_token"))
        return response.status_code == 200 and not record.get("refreshing", False)
    except (MerakiError, httpx.HTTPError):
        return False


class RedactCallbackQuery:
    """Keep the provider's code out of Uvicorn access logs and error validation."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == CALLBACK:
            from urllib.parse import parse_qs
            raw = scope.get("query_string", b"")
            scope["query_string"] = b""
            # Bounded parsing; duplicate parameters are refused by the receiver.
            try:
                params = parse_qs(raw.decode("ascii"), max_num_fields=8) if len(raw) <= 12288 else {}
                params = {k: v[0] for k, v in params.items() if len(v) == 1}
            except (ValueError, UnicodeError):
                params = {}
            scope.setdefault("state", {})["meraki_callback"] = params
            async def secure_send(message):
                if message["type"] == "http.response.start":
                    message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() not in (b"cache-control", b"referrer-policy")] + [(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")]
                await send(message)
            await self.app(scope, receive, secure_send)
        else:
            await self.app(scope, receive, send)
