# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in and the current user.

Local accounts: Argon2 password hashes, server-side sessions, a random token
in an HttpOnly cookie. Works with no directory at all. Directory users sign in
with their AD password, or to the portal only by single sign-on (Kerberos);
everything else only depends on `current_user`.
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import sla
from app.config import settings
from app.db import get_session
from app.models import AuthSource, User, UserRole, UserSession

SessionDep = Annotated[Session, Depends(get_session)]
log = logging.getLogger(__name__)

COOKIE = "helpdesk_session"
# Cookie-authenticated requests that change data must carry this header. Another
# site can't add custom headers to a request without CORS, which we don't allow.
CSRF_HEADER = "X-Helpdesk-CSRF"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MIN_PASSWORD = 12
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
# Only write last_seen_at this often, not on every request.
TOUCH_EVERY = timedelta(minutes=5)
# All a portal-only (single sign-on) session can reach: the portal and the files on its tickets.
PORTAL_ONLY_PATHS = re.compile(r"^/api/(auth/|portal/|attachments/|tickets/\d+/(attachments|comments)$|notices/current$|branding)")

_hasher = PasswordHasher()
# Verified against when the email is unknown, so timing doesn't reveal which emails exist.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


class AuthError(Exception):
    pass


# First run: before any admin has ever existed, "admin" / "admin" signs in as a local admin who
# must change the password before doing anything else. Gone for good once a real admin exists,
# even if every admin is later disabled (then: python -m app.cli set-password, or the recovery
# console), so losing the admins never reopens the door.
FIRST_RUN_LOGIN = FIRST_RUN_PASSWORD = "admin"
FIRST_RUN_EMAIL = "admin@localhost"


def _first_run_admin(session: Session) -> User | None:
    """The first-run admin, created on the spot, if no real admin has ever existed (the first-run
    admin itself counts only once its password has been changed)."""
    real = select(func.count()).select_from(User).where(
        User.role == UserRole.admin, ~((func.lower(User.email) == FIRST_RUN_EMAIL) & User.must_change_password.is_(True))
    )
    if session.scalar(real):
        return None
    user = session.scalar(select(User).where(func.lower(User.email) == FIRST_RUN_EMAIL))
    if user is None:
        user = User(email=FIRST_RUN_EMAIL, display_name="Administrator", sam_account_name=FIRST_RUN_LOGIN)
        session.add(user)
    user.role, user.is_active, user.auth_source = UserRole.admin, True, AuthSource.local
    user.password_hash = _hasher.hash(FIRST_RUN_PASSWORD)
    user.must_change_password = True
    session.flush()
    log.warning("First-run sign-in as admin/admin: the password must be changed now")
    return user


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Passwords must be at least {MIN_PASSWORD} characters")


def set_password(session: Session, user: User, password: str) -> None:
    """Set a local password and sign the user out everywhere."""
    check_password_rules(password)
    if user.auth_source != AuthSource.local:
        raise AuthError("This user signs in through the directory")
    user.password_hash = _hasher.hash(password)
    user.must_change_password = False
    user.failed_logins = 0
    user.locked_until = None
    end_sessions(session, user)


def end_sessions(session: Session, user: User) -> None:
    session.execute(delete(UserSession).where(UserSession.user_id == user.id))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _find_login(session: Session, login: str) -> User | None:
    """A local record for what someone typed at sign-in: email, alias, UPN or sAMAccountName."""
    from app.directory.sync import find_by_address

    v = login.strip().split("\\", 1)[-1].lower()
    return find_by_address(session, [v]) or session.scalar(
        select(User).where((func.lower(User.upn) == v) | (func.lower(User.sam_account_name) == v)).limit(1)
    )


def _failed(session: Session, user: User | None, now: datetime) -> None:
    if user is None:
        return
    user.failed_logins += 1
    if user.failed_logins >= MAX_FAILED_LOGINS:
        user.locked_until = now + LOCKOUT
        user.failed_logins = 0
    session.commit()


def authenticate(session: Session, login: str, password: str, now: datetime) -> User:
    """The user for these credentials, or AuthError with a message safe to show.

    Local accounts check their Argon2 hash. Everyone else signs in against the
    directory (AD/LDAP) when one is configured, and is synced at that moment.
    """
    generic = AuthError("Wrong email or password")
    if login.strip().lower() == FIRST_RUN_LOGIN and password == FIRST_RUN_PASSWORD:
        first = _first_run_admin(session)
        if first is not None:
            return first
    user = _find_login(session, login)
    if user is not None and user.locked_until and user.locked_until > now:
        # The same answer as a wrong password: a lockout mustn't show which accounts exist.
        raise generic

    if user is not None and user.auth_source == AuthSource.local and user.password_hash is not None:
        try:
            _hasher.verify(user.password_hash, password)
        except (VerificationError, InvalidHashError):
            _failed(session, user, now)
            raise generic from None
        if not user.is_active:
            raise generic
        if _hasher.check_needs_rehash(user.password_hash):
            user.password_hash = _hasher.hash(password)
        user.failed_logins = 0
        user.locked_until = None
        return user

    return _directory_sign_in(session, user, login, password, now, generic)


def _directory_sign_in(session: Session, user: User | None, login: str, password: str, now: datetime, generic: AuthError) -> User:
    from app.directory import sync
    from app.directory.types import DirectoryError

    directory = sync.get_directory() if password else None
    if directory is None and password and user is not None and user.local_fallback:
        # Directory settings removed: same as the directory being down.
        return _fallback(session, user, password, now, generic)
    if directory is None:
        # Same work as a real check, so timing doesn't reveal which logins exist.
        try:
            _hasher.verify(_DUMMY_HASH, password or "x")
        except VerificationError:
            pass
        raise generic
    try:
        found = directory.find_user(login)
        ok = found is not None and found.enabled and directory.check_password(found, password)
        if ok:
            user = sync.sign_in_sync(session, directory, found, now)
    except DirectoryError as exc:
        log.warning("Directory unavailable for %s's sign-in: %s", login, exc)
        if user is not None and user.local_fallback:
            return _fallback(session, user, password, now, generic)
        raise AuthError("Sign-in is unavailable right now. Try again shortly.") from None
    if not ok or user is None:
        session.rollback()
        _failed(session, _find_login(session, login), now)
        raise generic
    user.failed_logins = 0
    user.locked_until = None
    return user


def _fallback(session: Session, user: User, password: str, now: datetime, generic: AuthError) -> User:
    """The directory can't be reached: a technician with a fallback password may still sign in."""
    if not (user.is_active and user.password_hash and user.role in (UserRole.agent, UserRole.admin)):
        raise AuthError("Sign-in is unavailable right now. Try again shortly.")
    try:
        _hasher.verify(user.password_hash, password)
    except (VerificationError, InvalidHashError):
        _failed(session, user, now)
        raise generic from None
    log.warning("Directory unavailable: %s signed in with their local fallback password", user.email)
    user.failed_logins = 0
    user.locked_until = None
    return user


def sso_sign_in(session: Session, principal: str, realm: str, now: datetime) -> User:
    """Portal single sign-on: the directory user for a Kerberos principal the browser has
    proved: sAMAccountName@REALM, or UPN@REALM for an email-style Windows sign-in. Synced at that moment, like a password sign-in. Falls back to the
    synced record when the directory can't be reached. AuthError if there's no such user."""
    from app.directory import sync
    from app.directory.types import DirectoryError

    unknown = AuthError("Your Windows account isn't set up for the service desk")
    name, _, principal_realm = principal.rpartition("@")
    # Signing in to Windows with an email-style name (UPN) gives jpark\@corp.com@CORP.LOCAL.
    name = name.replace("\\@", "@").lower()
    # user@REALM only: not service or machine principals (HTTP/host, PC01$).
    if principal_realm.upper() != realm.upper() or not name or "/" in name or name.endswith("$"):
        raise unknown
    directory = sync.get_directory()
    user = None
    try:
        if directory is not None:
            found = directory.find_user(name)
            if found is None or name not in ((found.sam or "").lower(), (found.upn or "").lower()) or not found.enabled:
                raise unknown
            user = sync.sign_in_sync(session, directory, found, now)
    except DirectoryError:
        session.rollback()
        log.warning("Directory unavailable: single sign-on for %s uses the last synced record", principal)
        directory = None
    if directory is None:
        user = session.scalar(
            select(User)
            .where(User.auth_source == AuthSource.directory, (func.lower(User.sam_account_name) == name) | (func.lower(User.upn) == name))
            .limit(1)
        )
    if user is None or not user.is_active or user.auth_source != AuthSource.directory:
        raise unknown
    return user


def set_sign_in(session: Session, user: User, mode: str, password: str | None) -> None:
    """How a technician signs in: "directory", "directory_with_fallback" or "local"."""
    if mode == "directory_with_fallback":
        if user.auth_source != AuthSource.directory or user.role not in (UserRole.agent, UserRole.admin):
            raise AuthError("A fallback password is for directory agents and admins")
        if password is None and not (user.local_fallback and user.password_hash):
            raise AuthError("Set the fallback password")
        if password is not None:
            check_password_rules(password)
            user.password_hash = _hasher.hash(password)
        user.local_fallback = True
    elif mode == "directory":
        if user.auth_source != AuthSource.directory:
            raise AuthError("This is a local account; it isn't linked to the directory")
        user.local_fallback = False
        user.password_hash = None
    elif mode == "local":
        if password is None and not (user.auth_source == AuthSource.local and user.password_hash):
            raise AuthError("Set a password for the local account")
        if password is not None:
            check_password_rules(password)
            user.password_hash = _hasher.hash(password)
        # Detach from the directory so sync leaves it alone (it reports the address as local).
        user.auth_source = AuthSource.local
        user.directory_id = None
        user.directory_dn = None
        user.local_fallback = False
        user.role_source = "manual"
    else:
        raise AuthError(f"Unknown sign-in mode {mode!r}")
    user.failed_logins = 0
    user.locked_until = None
    end_sessions(session, user)


def start_session(session: Session, user: User, response: Response, now: datetime, *, portal_only: bool = False) -> None:
    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            token_hash=_hash_token(token), user_id=user.id, expires_at=now + timedelta(hours=settings.session_hours), portal_only=portal_only
        )
    )
    user.portal_only = portal_only
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def end_session(session: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        session.execute(delete(UserSession).where(UserSession.token_hash == _hash_token(token)))
    response.delete_cookie(COOKIE, path="/")


def _user_from_cookie(session: Session, request: Request) -> User | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = session.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    now = sla.utcnow()
    if row is None or row.expires_at <= now:
        return None
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing {CSRF_HEADER} header")
    if now - row.last_seen_at > TOUCH_EVERY:
        # Sliding expiry: active sessions stay signed in.
        row.last_seen_at = now
        row.expires_at = now + timedelta(hours=settings.session_hours)
        session.commit()
    if row.user.must_change_password and not request.url.path.startswith("/api/auth/"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Change your password first")
    if must_set_up_two_factor(row.user) and not request.url.path.startswith("/api/auth/"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Set up two-factor sign-in first")
    if row.portal_only:
        if not PORTAL_ONLY_PATHS.match(request.url.path):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in with your password to use the service desk console")
        row.user.portal_only = True
    return row.user


def must_set_up_two_factor(user: User) -> bool:
    """Settings > Sign-in requires it of admins with a local password, and it isn't on yet."""
    return (settings.totp_required_admins and user.role == UserRole.admin and user.auth_source == AuthSource.local
            and user.password_hash is not None and user.totp_enabled_at is None)


def current_user(
    request: Request, session: SessionDep, x_dev_user: Annotated[str | None, Header()] = None
) -> User:
    user = _user_from_cookie(session, request)
    if user is None and x_dev_user and settings.dev_auth:
        user = session.scalar(select(User).where(func.lower(User.email) == x_dev_user.lower()))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_agent(user: CurrentUser) -> User:
    if user.role not in (UserRole.agent, UserRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Agents only")
    return user


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user


Agent = Annotated[User, Depends(require_agent)]
Admin = Annotated[User, Depends(require_admin)]
