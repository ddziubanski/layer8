# SPDX-License-Identifier: AGPL-3.0-or-later
"""Failed and successful tries at the doors without a session: sign-in and the receiving page.

Kept in the database, so the API's worker processes share one count and a restart doesn't
forget it. Addresses are kept for 30 days: long enough to know where someone usually signs in.
"""

import ipaddress
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import SignInAttempt

SIGN_IN = "sign-in"            # one account, from one address
SIGN_IN_ADDRESS = "address"    # any failed sign-in from one address
RECEIVING = "receiving"        # wrong PO number or code on the receiving page
KEEP = timedelta(days=30)


def key(address: str | None) -> str:
    """One client: an IPv4 address, or an IPv6 /64 (a single machine has a whole /64 to draw from)."""
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return (address or "?")[:64]
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address) + "/64"
    return str(ip)


def record(session: Session, kind: str, address: str | None, now: datetime, *, user_id: int | None = None, ok: bool = False) -> None:
    session.add(SignInAttempt(kind=kind, address=key(address), user_id=user_id, ok=ok, at=now))
    session.flush()


def failures(session: Session, kind: str, address: str | None, since: datetime) -> int:
    """Failed tries of this kind from this address."""
    return session.scalar(select(func.count()).select_from(SignInAttempt).where(
        SignInAttempt.kind == kind, SignInAttempt.address == key(address), SignInAttempt.ok.is_(False), SignInAttempt.at > since)) or 0


def pair_failures(session: Session, user_id: int, address: str | None, since: datetime) -> int:
    """This account's failed sign-ins from this address, since its last successful one there."""
    mine = (SignInAttempt.kind == SIGN_IN, SignInAttempt.user_id == user_id, SignInAttempt.address == key(address))
    last_ok = session.scalar(select(func.max(SignInAttempt.at)).where(*mine, SignInAttempt.ok.is_(True)))
    if last_ok is not None and last_ok > since:
        since = last_ok
    return session.scalar(select(func.count()).select_from(SignInAttempt).where(*mine, SignInAttempt.ok.is_(False), SignInAttempt.at > since)) or 0


def account_failures(session: Session, user_id: int, since: datetime) -> int:
    """This account's failed sign-ins from anywhere."""
    return session.scalar(select(func.count()).select_from(SignInAttempt).where(
        SignInAttempt.kind == SIGN_IN, SignInAttempt.user_id == user_id, SignInAttempt.ok.is_(False), SignInAttempt.at > since)) or 0


def known(session: Session, user_id: int, address: str | None, now: datetime) -> bool:
    """Has this account signed in from this address lately?"""
    return session.scalar(select(SignInAttempt.id).where(
        SignInAttempt.kind == SIGN_IN, SignInAttempt.user_id == user_id, SignInAttempt.address == key(address),
        SignInAttempt.ok.is_(True), SignInAttempt.at > now - KEEP).limit(1)) is not None


def forget(session: Session, user_id: int) -> None:
    """A new password: the account's failed tries no longer count."""
    session.execute(delete(SignInAttempt).where(SignInAttempt.user_id == user_id, SignInAttempt.ok.is_(False)))


def prune(session: Session, now: datetime) -> None:
    session.execute(delete(SignInAttempt).where(SignInAttempt.at < now - KEEP))
