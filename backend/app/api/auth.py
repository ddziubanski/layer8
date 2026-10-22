import base64
import logging
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app import auth, kerberos, permissions, sla
from app.auth import CurrentUser, SessionDep
from app.config import settings
from app.schemas import LoginIn, MeOut, PasswordChangeIn, PreferencesIn, SignInOptionsOut


def me_out(session, user) -> MeOut:
    out = MeOut.model_validate(user)
    out.permissions = permissions.granted(session, user)
    out.features = ["action1"] if settings.action1_enabled else []
    return out

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger(__name__)


@router.get("/options", response_model=SignInOptionsOut)
def sign_in_options(session: SessionDep):
    config = kerberos.active(session)
    return SignInOptionsOut(windows_sign_in=config is not None, windows_sign_in_silent=config is not None and config.silent)


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
    try:
        user = auth.authenticate(session, body.email, body.password, now)
    except auth.AuthError as exc:
        recent.append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None
    auth.start_session(session, user, response, now)
    session.commit()
    return me_out(session, user)


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
    """My colour in the queue and my own colour rules."""
    user.ticket_color = body.ticket_color.lower() if body.ticket_color else None
    user.color_rules = [r.model_dump() for r in body.color_rules]
    session.commit()
    return me_out(session, user)
