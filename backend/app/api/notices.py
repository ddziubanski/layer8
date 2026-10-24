# SPDX-License-Identifier: AGPL-3.0-or-later
"""Service notices: outages and known problems admins post (Settings > Notices).

Everyone signed in reads the current ones meant for them; admins manage them."""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select

from app import permissions, sla
from app.auth import CurrentUser, SessionDep
from app.models import ServiceNotice, UserRole
from app.schemas import NoticeIn, NoticeOut

router = APIRouter(prefix="/api/notices", tags=["notices"])
NoticesManager = permissions.require("notices_manage")
log = logging.getLogger("app.api.settings")
ORDER = {"outage": 0, "warning": 1, "info": 2}


def _out(n: ServiceNotice, now) -> NoticeOut:
    out = NoticeOut.model_validate(n)
    out.state = "upcoming" if n.starts_at > now else "ended" if n.ends_at is not None and n.ends_at <= now else "current"
    return out


@router.get("/current", response_model=list[NoticeOut])
def current_notices(user: CurrentUser, session: SessionDep):
    """What this person should see now: outages first. Staff see staff notices; requesters
    (and anyone using the portal through Windows sign-in) see requester notices."""
    now = sla.utcnow()
    staff = user.role in (UserRole.agent, UserRole.admin) and not user.portal_only
    audiences = ["everyone", "staff" if staff else "requesters"]
    rows = session.scalars(
        select(ServiceNotice).where(
            ServiceNotice.starts_at <= now,
            or_(ServiceNotice.ends_at.is_(None), ServiceNotice.ends_at > now),
            ServiceNotice.audience.in_(audiences),
        )
    ).all()
    return sorted((_out(n, now) for n in rows), key=lambda n: (ORDER.get(n.severity, 3), -n.starts_at.timestamp()))


@router.get("", response_model=list[NoticeOut])
def list_notices(_: NoticesManager, session: SessionDep):
    now = sla.utcnow()
    rows = session.scalars(select(ServiceNotice).order_by(ServiceNotice.starts_at.desc()).limit(200)).all()
    return [_out(n, now) for n in rows]


def _apply(n: ServiceNotice, body: NoticeIn) -> None:
    starts = body.starts_at or n.starts_at or sla.utcnow()
    if body.ends_at is not None and body.ends_at <= starts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The end must be after the start")
    n.title, n.message, n.severity, n.audience = body.title.strip(), body.message.strip(), body.severity, body.audience
    n.starts_at, n.ends_at = starts, body.ends_at


@router.post("", response_model=NoticeOut, status_code=status.HTTP_201_CREATED)
def post_notice(body: NoticeIn, admin: NoticesManager, session: SessionDep):
    n = ServiceNotice(created_by_id=admin.id)
    _apply(n, body)
    session.add(n)
    session.commit()
    log.warning("Notice posted by %s (%s, %s): %s", admin.display_name, n.severity, n.audience, n.title, extra={"source": "settings"})
    return _out(n, sla.utcnow())


def _notice(session, notice_id: int) -> ServiceNotice:
    n = session.get(ServiceNotice, notice_id)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notice not found")
    return n


@router.put("/{notice_id}", response_model=NoticeOut)
def update_notice(notice_id: int, body: NoticeIn, admin: NoticesManager, session: SessionDep):
    n = _notice(session, notice_id)
    _apply(n, body)
    session.commit()
    return _out(n, sla.utcnow())


@router.post("/{notice_id}/end", response_model=NoticeOut)
def end_notice(notice_id: int, admin: NoticesManager, session: SessionDep):
    """Take it down now (it stays in the list as ended)."""
    n = _notice(session, notice_id)
    now = sla.utcnow()
    n.ends_at = max(now, n.starts_at)
    session.commit()
    log.info("Notice ended by %s: %s", admin.display_name, n.title, extra={"source": "settings"})
    return _out(n, now)


@router.delete("/{notice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notice(notice_id: int, _: NoticesManager, session: SessionDep):
    session.delete(_notice(session, notice_id))
    session.commit()
