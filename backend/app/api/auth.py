# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import logging
import time
from collections import defaultdict, deque
from typing import Annotated, get_args

from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from pydantic import ValidationError

from app import auth, entra, kerberos, permissions, sla, totp, workspace
from app.auth import CurrentUser, SessionDep
from app.config import settings
from app.schemas import (
    LoginIn, MeOut, PasswordChangeIn, PreferencesIn, QueueListPreferences, SidebarModule, SignInOptionsOut, TicketWorkspacePreferences,
    TwoFactorConfirmIn, TwoFactorOffIn, TwoFactorRecoveryOut, TwoFactorSetupOut, TwoFactorStatusOut,
)
from app.models import AppSetting, User, UserRole


def me_out(session, user) -> MeOut:
    out = MeOut.model_validate(user)
    out.permissions = permissions.granted(session, user)
    out.disabled_modules = workspace.disabled()
    out.must_set_up_two_factor = auth.must_set_up_two_factor(user)
    out.features = [name for name, on in (("meraki", settings.meraki_enabled), ("unifi", settings.unifi_enabled), ("nutanix", settings.nutanix_enabled), ("nutanix_power", settings.nutanix_enabled and settings.nutanix_power_enabled), ("junos", settings.junos_enabled), ("unifi_vouchers", settings.unifi_enabled and settings.unifi_vouchers_enabled), ("action1", settings.action1_enabled), ("copilot", settings.copilot_enabled and settings.teams_chat_enabled)) if on]
    navigation = session.get(AppSetting, f"personal_navigation:{user.id}")
    hidden = navigation.value.get("hidden_modules", []) if navigation else []
    out.hidden_modules = sorted({m for m in hidden if isinstance(m, str) and m in get_args(SidebarModule)}) if isinstance(hidden, list) else []
    presentation = session.get(AppSetting, f"personal_ticket_workspace:{user.id}")
    if presentation:
        try:
            out.ticket_workspace = TicketWorkspacePreferences.model_validate(presentation.value)
        except ValidationError:
            pass  # A damaged preference never prevents signing in or working a ticket.
    queue_list = session.get(AppSetting, f"personal_queue_list:{user.id}")
    if queue_list:
        try:
            out.queue_list = QueueListPreferences.model_validate(queue_list.value)
        except ValidationError:
            pass
    return out

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger(__name__)


@router.get("/options", response_model=SignInOptionsOut)
def sign_in_options(session: SessionDep):
    config = kerberos.active(session)
    return SignInOptionsOut(windows_sign_in=config is not None, windows_sign_in_silent=config is not None and config.silent, entra_sign_in=entra.available())


# Failed sign-ins per client address: password spraying across accounts is slowed down too, not
# only guesses at one account (which locks it).
MAX_FAILURES = 30
WINDOW = 15 * 60
_failures: dict[str, deque] = defaultdict(deque)


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, session: SessionDep):
    now = sla.utcnow()
    recent = _failures[request.client.host if request.client else "?"]
    while recent and recent[0] < time.monotonic() - WINDOW:
        recent.popleft()
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed sign-ins from here. Wait a few minutes and try again")
    # A right password alone mustn't clear the failed-attempt count of an account with two-factor
    # sign-in, or wrong codes would never add up to a lockout.
    known = auth._find_login(session, body.email)
    before = (known.failed_logins, known.locked_until) if known is not None and known.totp_enabled_at is not None else None
    try:
        user = auth.authenticate(session, body.email, body.password, now)
    except auth.AuthError as exc:
        recent.append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None
    if user.totp_enabled_at is not None:
        if before is not None and known is user:
            user.failed_logins, user.locked_until = before
        if not body.code:
            # The password was right; the sign-in page now asks for the code (nothing is counted yet).
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, TWO_FACTOR_NEEDED)
        if not _second_factor(user, body.code):
            recent.append(time.monotonic())
            auth._failed(session, user, now)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code didn't work. Use the newest one in your authenticator app, or a recovery code")
        user.failed_logins, user.locked_until = 0, None
    auth.start_session(session, user, response, now)
    session.commit()
    return me_out(session, user)


TWO_FACTOR_NEEDED = "Enter the code from your authenticator app"


def _second_factor(user: User, code: str) -> bool:
    """An authenticator code (once only) or one of the recovery codes (used up)."""
    secret = totp.unseal(user.totp_secret) if user.totp_secret else None
    if secret is not None and (step := totp.check(secret, code, user.totp_last_step)) is not None:
        user.totp_last_step = step
        return True
    left = totp.use_recovery_code(user.totp_recovery or [], code)
    if left is not None:
        user.totp_recovery = left
        log.warning("%s signed in with a recovery code (%d left)", user.email, len(left), extra={"source": "auth"})
        return True
    return False


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, session: SessionDep):
    auth.end_session(session, request, response)
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/sso", response_model=MeOut)
def single_sign_on(response: Response, session: SessionDep, authorization: Annotated[str | None, Header()] = None):
    """Portal sign-in as the logged-on Windows user. With no ticket yet, answers 401 with
    "WWW-Authenticate: Negotiate" so a browser that trusts this site sends one. A failed
    check answers 401 without it, so the browser stops and the portal shows the sign-in form.
    The session is portal-only: the console still needs a password."""
    config = kerberos.active(session)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Single sign-on isn't set up")
    try:
        token = kerberos.negotiate_token(authorization)
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This computer didn't offer a Windows sign-in", headers={"WWW-Authenticate": "Negotiate"})
        principal, reply = kerberos.accept(token)
    except kerberos.KerberosError as exc:
        log.info("Single sign-on refused: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Windows sign-in didn't work on this computer") from None
    now = sla.utcnow()
    try:
        user = auth.sso_sign_in(session, principal, config.realm, now)
    except auth.AuthError as exc:
        log.info("Single sign-on refused for %s: %s", principal, exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None
    auth.start_session(session, user, response, now, portal_only=True)
    session.commit()
    if reply:
        response.headers["WWW-Authenticate"] = "Negotiate " + base64.b64encode(reply).decode()
    return me_out(session, user)


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser, session: SessionDep):
    return me_out(session, user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChangeIn, user: CurrentUser, request: Request, response: Response, session: SessionDep):
    if user.portal_only:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in with your password first")
    try:
        auth.authenticate(session, user.email, body.current_password, sla.utcnow())
        auth.set_password(session, user, body.new_password)
    except auth.AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    # set_password signed out every session; keep this browser signed in.
    auth.start_session(session, user, response, sla.utcnow())
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.put("/me/preferences", response_model=MeOut)
def save_preferences(body: PreferencesIn, user: CurrentUser, session: SessionDep):
    """My colours and sidebar; omitted fields preserve independently saved preferences."""
    if {"ticket_workspace", "queue_list"} & body.model_fields_set and user.role == UserRole.requester:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ticket workspace and queue preferences are for technicians")
    session.scalar(select(User).where(User.id == user.id).with_for_update())
    if "ticket_color" in body.model_fields_set:
        user.ticket_color = body.ticket_color.lower() if body.ticket_color else None
    if "color_rules" in body.model_fields_set:
        user.color_rules = [r.model_dump() for r in body.color_rules]
    if "hidden_modules" in body.model_fields_set:
        key = f"personal_navigation:{user.id}"
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key)
            session.add(row)
        row.value = {"hidden_modules": body.hidden_modules or []}
        row.updated_by_id, row.updated_at = user.id, sla.utcnow()
    if "ticket_workspace" in body.model_fields_set:
        key = f"personal_ticket_workspace:{user.id}"
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key)
            session.add(row)
        row.value = (body.ticket_workspace or TicketWorkspacePreferences()).model_dump()
        row.updated_by_id, row.updated_at = user.id, sla.utcnow()
    if "queue_list" in body.model_fields_set:
        key = f"personal_queue_list:{user.id}"
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key)
            session.add(row)
        row.value = (body.queue_list or QueueListPreferences()).model_dump()
        row.updated_by_id, row.updated_at = user.id, sla.utcnow()
    session.commit()
    return me_out(session, user)


# Two-factor sign-in (authenticator app)


@router.get("/two-factor", response_model=TwoFactorStatusOut)
def two_factor_status(user: CurrentUser):
    return {"enabled": user.totp_enabled_at is not None, "recovery_codes_left": len(user.totp_recovery or []),
            "required": auth.must_set_up_two_factor(user)}


@router.post("/two-factor/setup", response_model=TwoFactorSetupOut)
def two_factor_setup(user: CurrentUser, session: SessionDep):
    """A new secret to add to an authenticator app; it's on once a code from it is confirmed."""
    if user.portal_only:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in with your password first")
    if user.totp_enabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Two-factor sign-in is already on; turn it off first to move it to another app")
    secret = totp.new_secret()
    user.totp_pending = totp.seal(secret)
    session.commit()
    return {"secret": secret, "uri": totp.uri(secret, user.email, settings.product_name or "Layer 8")}


@router.post("/two-factor/confirm", response_model=TwoFactorRecoveryOut)
def two_factor_confirm(body: TwoFactorConfirmIn, user: CurrentUser, session: SessionDep):
    secret = totp.unseal(user.totp_pending) if user.totp_pending else None
    step = totp.check(secret, body.code, None) if secret else None
    if step is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That code didn't match; check the app's clock and try the newest code")
    codes, hashes = totp.recovery_codes()
    user.totp_secret, user.totp_pending = user.totp_pending, None
    user.totp_enabled_at, user.totp_last_step, user.totp_recovery = sla.utcnow(), step, hashes
    log.info("%s turned on two-factor sign-in", user.email, extra={"source": "auth"})
    session.commit()
    return {"recovery_codes": codes}


@router.post("/two-factor/off", status_code=status.HTTP_204_NO_CONTENT)
def two_factor_off(body: TwoFactorOffIn, user: CurrentUser, request: Request, session: SessionDep):
    if user.portal_only:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in with your password first")
    if auth.must_set_up_two_factor(user) or (settings.totp_required_admins and user.role == UserRole.admin and user.password_hash is not None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Two-factor sign-in is required for admins here (Settings > Sign-in)")
    now = sla.utcnow()
    before = (user.failed_logins, user.locked_until)
    try:
        auth.authenticate(session, user.email, body.password, now)
    except auth.AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    user.failed_logins, user.locked_until = before
    if user.totp_enabled_at is None or not _second_factor(user, body.code):
        auth._failed(session, user, now)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That code didn't work")
    user.failed_logins, user.locked_until = 0, None
    clear_two_factor(user)
    log.warning("%s turned off two-factor sign-in", user.email, extra={"source": "auth"})
    session.commit()


def clear_two_factor(user: User) -> None:
    user.totp_secret = user.totp_pending = None
    user.totp_enabled_at = user.totp_last_step = None
    user.totp_recovery = []


# Sign in with Microsoft (Entra ID)


@router.get("/entra/start")
def entra_start():
    try:
        url, browser = entra.begin()
    except entra.EntraError as exc:
        return RedirectResponse("/?signin_error=" + quote(str(exc)), status_code=303)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(entra.COOKIE, browser, max_age=entra.MAX_AGE, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/api/auth/entra")
    return response


@router.get("/entra/callback")
def entra_callback(request: Request, session: SessionDep):
    params = dict(request.query_params)
    try:
        user = entra.finish(session, params, request.cookies.get(entra.COOKIE))
    except entra.EntraError as exc:
        log.info("Microsoft sign-in refused: %s", exc, extra={"source": "auth"})
        response = RedirectResponse("/?signin_error=" + quote(str(exc)), status_code=303)
    else:
        response = RedirectResponse("/", status_code=303)
        auth.start_session(session, user, response, sla.utcnow())
        log.info("%s signed in with Microsoft", user.email, extra={"source": "auth"})
    session.commit()
    response.delete_cookie(entra.COOKIE, path="/api/auth/entra")
    return response

