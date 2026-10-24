# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with Microsoft Entra ID (OpenID Connect authorization code flow with PKCE, through MSAL).

The browser goes to /api/auth/entra/start, which keeps the pending flow (with its PKCE verifier)
as a file in the secrets folder, named by a hash of a random state and bound to this browser by a
short-lived cookie, and sends it to Microsoft. Microsoft
returns to /api/auth/entra/callback; MSAL checks state and nonce and exchanges the code directly
with Microsoft over TLS. Only existing, active people sign in, matched by their Entra object ID (oid)
or by UPN/email; accounts with a local password never do (the break-glass admin stays local)."""

import hashlib
import json
import secrets
import time
from contextlib import contextmanager
from uuid import UUID

import httpx
import msal
from sqlalchemy import func, select

from app.config import settings
from app.configstore import write_secret_file
from app.models import AuthSource, User

MAX_AGE = 600
COOKIE = "l8_entra"


class EntraError(RuntimeError):
    """Safe to show on the sign-in page."""


def configuration() -> dict:
    try:
        tenant, client = str(UUID(settings.entra_tenant_id)), str(UUID(settings.entra_client_id))
    except (ValueError, TypeError, AttributeError):
        raise EntraError("Microsoft sign-in isn't set up") from None
    if not (settings.entra_sign_in_enabled and settings.entra_client_secret and settings.portal_url.startswith("https://")):
        raise EntraError("Microsoft sign-in isn't set up")
    return {"tenant": tenant, "client": client, "redirect": settings.portal_url.rstrip("/") + "/api/auth/entra/callback"}


def available() -> bool:
    try:
        configuration()
        return True
    except EntraError:
        return False


@contextmanager
def _app(config: dict):
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as http:
        yield msal.ConfidentialClientApplication(
            config["client"], authority=f"https://login.microsoftonline.com/{config['tenant']}",
            client_credential=settings.entra_client_secret, http_client=http, instance_discovery=False,
        )


def _pending_path(state: str):
    return settings.secrets_dir / f"entra-pending-{hashlib.sha256(state.encode()).hexdigest()}.json"


def _sweep() -> None:
    """Remove abandoned attempts."""
    for old in settings.secrets_dir.glob("entra-pending-*.json"):
        try:
            if time.time() - old.stat().st_mtime > MAX_AGE:
                old.unlink(missing_ok=True)
        except OSError:
            pass


def begin() -> tuple[str, str]:
    """(Microsoft's sign-in URL, the browser cookie that binds this attempt)."""
    config = configuration()
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    try:
        with _app(config) as app:
            flow = app.initiate_auth_code_flow(["User.Read"], redirect_uri=config["redirect"], state=state, prompt="select_account")
    except (httpx.HTTPError, ValueError):
        raise EntraError("Microsoft sign-in could not be started; try again later") from None
    _sweep()
    pending = {"flow": flow, "browser": hashlib.sha256(browser.encode()).hexdigest(), "created": time.time(), "config": config}
    write_secret_file(_pending_path(state), json.dumps(pending).encode())
    return flow["auth_uri"], browser


def _exchange(config: dict, flow: dict, params: dict) -> dict:
    with _app(config) as app:
        result = app.acquire_token_by_auth_code_flow(flow, params)
    if "error" in result or "id_token_claims" not in result:
        raise EntraError("Microsoft didn't confirm the sign-in")
    return result["id_token_claims"]


def finish(session, params: dict, browser: str | None) -> User:
    state = params.get("state") or ""
    path = _pending_path(state) if state else None
    try:
        pending = json.loads(path.read_text(encoding="utf-8")) if path else None
        path.unlink()  # once only, before the code is exchanged
    except (OSError, ValueError, AttributeError):
        pending = None
    if not isinstance(pending, dict):
        raise EntraError("This sign-in has expired; start again")
    if (not browser or not secrets.compare_digest(pending["browser"], hashlib.sha256(browser.encode()).hexdigest())
            or not 0 <= time.time() - pending["created"] <= MAX_AGE or pending["config"] != configuration()):
        raise EntraError("This sign-in has expired; start again")
    if params.get("error"):
        raise EntraError("Microsoft sign-in was cancelled or refused")
    try:
        claims = _exchange(pending["config"], pending["flow"], params)
    except (httpx.HTTPError, ValueError):
        raise EntraError("Microsoft didn't confirm the sign-in") from None
    if claims.get("tid") != pending["config"]["tenant"] or not isinstance(claims.get("oid"), str):
        raise EntraError("That Microsoft account isn't from this organization")
    return _match(session, claims)


def _match(session, claims: dict) -> User:
    from app.auth import _find_login

    oid = claims["oid"].lower()
    user = session.scalar(select(User).where(func.lower(User.entra_id) == oid))
    if user is None:
        for name in (claims.get("preferred_username"), claims.get("upn"), claims.get("email")):
            if isinstance(name, str) and name:
                user = _find_login(session, name)
                if user is not None:
                    break
    if user is None or not user.is_active:
        raise EntraError("There's no account here for that Microsoft account; ask an administrator")
    if user.auth_source == AuthSource.local and user.password_hash is not None:
        raise EntraError("This account signs in with its password")
    if user.entra_id and user.entra_id.lower() != oid:
        raise EntraError("This account is linked to a different Microsoft account")
    user.entra_id = oid
    return user
