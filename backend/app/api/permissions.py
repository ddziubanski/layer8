"""Settings > Permissions: who may take each action in app.permissions.CATALOG."""

import logging

from fastapi import APIRouter, HTTPException, status

from app import permissions as perms
from app.auth import Admin, SessionDep
from app.schemas import PermissionOut, PermissionRuleIn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/permissions", tags=["permissions"])


def _list(session) -> list[PermissionOut]:
    saved = perms.stored(session)
    out = []
    for p in perms.CATALOG:
        r = perms.rule(session, p.key, saved)
        out.append(PermissionOut(key=p.key, area=p.area, label=p.label, help=p.help, default=p.default, who=r["who"], group_ids=r["group_ids"]))
    return out


@router.get("", response_model=list[PermissionOut])
def list_permissions(_: Admin, session: SessionDep):
    return _list(session)


@router.put("", response_model=list[PermissionOut])
def save_permissions(body: dict[str, PermissionRuleIn], admin: Admin, session: SessionDep):
    """Change some permissions (the ones not sent keep theirs)."""
    try:
        perms.save(session, {k: v.model_dump() for k, v in body.items()})
    except perms.PermissionError_ as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    session.commit()
    log.info("%s changed permissions: %s", admin.display_name, ", ".join(sorted(body)), extra={"source": "settings"})
    return _list(session)
