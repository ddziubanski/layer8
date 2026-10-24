# SPDX-License-Identifier: AGPL-3.0-or-later
"""AD / LDAP: connection, group import, group-based roles, sync. Admins only."""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app import sla
from app.api.admin import load_groups
from app.auth import Admin, SessionDep
from app.config import settings
from app.directory import sync
from app.directory.types import DirectoryError
from app.models import AuthSource, DirectorySyncRun, Group, RoleMapping, User, UserRole, group_members
from app.schemas import (
    DirectoryGroupOut,
    DirectoryLookupIn,
    DirectoryLookupOut,
    DirectoryStatusOut,
    DirectorySyncRunOut,
    GroupOut,
    ImportGroupIn,
    RoleMappingIn,
    RoleMappingOut,
    RolePreviewOut,
)

router = APIRouter(prefix="/api/directory", tags=["directory"])
log = logging.getLogger(__name__)


def directory():
    try:
        d = sync.get_directory()
    except DirectoryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if d is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No directory configured: fill in Settings > Directory > Connection")
    return d


def unavailable(exc: DirectoryError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.get("/status", response_model=DirectoryStatusOut)
def directory_status(_: Admin, session: SessionDep):
    last = session.scalar(select(DirectorySyncRun).where(DirectorySyncRun.dry_run.is_(False)).order_by(DirectorySyncRun.id.desc()).limit(1))
    return DirectoryStatusOut(
        configured=bool(settings.ldap_url),
        kind=settings.ldap_kind,
        servers=[u.strip() for u in settings.ldap_url.split(",") if u.strip()],
        base_dn=settings.ldap_base_dn,
        sync_minutes=settings.ldap_sync_minutes,
        last_run=last,
        tracked_groups=len(sync.tracked_groups(session)),
        directory_users=session.scalar(select(func.count()).select_from(User).where(User.auth_source == AuthSource.directory, User.is_active)),
    )


@router.post("/test")
def test_connection(admin: Admin):
    try:
        result = directory().describe()
    except DirectoryError as exc:
        log.warning("Connection test by %s failed: %s", admin.display_name, exc)
        raise unavailable(exc) from None
    log.info("Connection test by %s: connected to %s as %s", admin.display_name, result.get("server"), result.get("bound_as"))
    return result


@router.post("/lookup", response_model=DirectoryLookupOut)
def lookup_user(body: DirectoryLookupIn, _: Admin, session: SessionDep):
    """Look someone up in AD the way sign-in does, and show which role the mappings would give them."""
    d = directory()
    try:
        du = d.find_user(body.login)
        if du is None:
            return DirectoryLookupOut(found=False)
        guids = d.user_group_guids(du)
    except DirectoryError as exc:
        raise unavailable(exc) from None
    groups = [g for g in sync.tracked_groups(session) if g.directory_id in guids]
    best, source = UserRole.requester, "default"
    for m in session.scalars(select(RoleMapping)):
        if m.group in groups and m.group.is_active and sync.RANK[m.role] > sync.RANK[best]:
            best, source = m.role, f"group:{m.group.name}"
    return DirectoryLookupOut(
        found=True, guid=du.guid, dn=du.dn, email=du.email, display_name=du.display_name, enabled=du.enabled,
        upn=du.upn, sam=du.sam, aliases=du.aliases, groups=sorted(g.name for g in groups), role=best, role_source=source,
    )


@router.get("/groups/search", response_model=list[DirectoryGroupOut])
def search_groups(_: Admin, session: SessionDep, q: str = Query(min_length=2, max_length=100)):
    try:
        found = directory().search_groups(q)
    except DirectoryError as exc:
        raise unavailable(exc) from None
    imported = dict(session.execute(select(Group.directory_id, Group.id).where(Group.directory_id.in_([g.guid for g in found]))).all()) if found else {}
    return [DirectoryGroupOut(guid=g.guid, dn=g.dn, name=g.name, description=g.description, group_id=imported.get(g.guid)) for g in found]


@router.post("/groups/import", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def import_group(body: ImportGroupIn, _: Admin, session: SessionDep):
    """Bring an AD group in (for role mappings, queue owners or managers) and sync its members now."""
    d = directory()
    existing = session.scalar(select(Group).where(Group.directory_id == body.guid))
    try:
        dg = d.group_by_guid(body.guid)
        if dg is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such group in the directory")
        group = existing or Group(name=dg.name, source=AuthSource.directory, directory_id=dg.guid)
        if existing is None:
            if session.scalar(select(Group).where(func.lower(Group.name) == dg.name.lower())):
                group.name = f"{dg.name} (AD)"
            session.add(group)
        group.description = dg.description
        group.is_active = True
        session.flush()
        run = DirectorySyncRun(started_at=sla.utcnow(), details=[], groups_synced=0)
        sync.sync_group(session, d, group, sla.utcnow(), run)
    except DirectoryError as exc:
        raise unavailable(exc) from None
    session.commit()
    return group


def _mappings_out(session) -> list[RoleMappingOut]:
    # Highest role first (admins, then agents), then by group name.
    rows = sorted(session.scalars(select(RoleMapping).join(Group)).all(), key=lambda m: (-sync.RANK[m.role], m.group.name.lower()))
    counts = dict(session.execute(select(group_members.c.group_id, func.count()).group_by(group_members.c.group_id)).all())
    return [
        RoleMappingOut(group=m.group, role=m.role, member_count=counts.get(m.group_id, 0), last_synced_at=m.group.last_synced_at, sync_error=m.group.sync_error)
        for m in rows
    ]


@router.get("/role-mappings", response_model=list[RoleMappingOut])
def list_mappings(_: Admin, session: SessionDep):
    return _mappings_out(session)


def _set_mappings(session, body: list[RoleMappingIn]) -> None:
    groups = load_groups(session, [m.group_id for m in body])
    if any(g.source != AuthSource.directory for g in groups):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Role mappings use directory (AD/LDAP) groups")
    for m in session.scalars(select(RoleMapping)):
        session.delete(m)
    session.flush()
    for m in body:
        session.add(RoleMapping(group_id=m.group_id, role=m.role))
    session.flush()


@router.post("/role-mappings/preview", response_model=RolePreviewOut)
def preview_mappings(body: list[RoleMappingIn], _: Admin, session: SessionDep):
    """Who would gain or lose admin/agent with these mappings. Nothing is saved."""
    savepoint = session.begin_nested()
    run = DirectorySyncRun(started_at=sla.utcnow(), details=[], role_changes=0)
    error = None
    try:
        _set_mappings(session, body)
        sync.apply_roles(session, run)
    except sync.SyncAborted as exc:
        error = str(exc)
    admins = session.scalar(select(func.count()).select_from(User).where(User.is_active, User.role == UserRole.admin))
    agents = session.scalar(select(func.count()).select_from(User).where(User.is_active, User.role == UserRole.agent))
    savepoint.rollback()
    return RolePreviewOut(error=error, changes=[d for d in run.details if d["kind"] == "role"], admins=admins, agents=agents)


@router.put("/role-mappings", response_model=list[RoleMappingOut])
def save_mappings(body: list[RoleMappingIn], admin: Admin, session: SessionDep):
    """Save and apply now (memberships are already synced, so no directory call is needed)."""
    _set_mappings(session, body)
    run = DirectorySyncRun(started_at=sla.utcnow(), details=[], role_changes=0)
    try:
        sync.apply_roles(session, run)
    except sync.SyncAborted as exc:
        session.rollback()
        log.warning("Role mappings not saved (%s): %s", admin.display_name, exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    session.commit()
    log.info("Role mappings saved by %s: %s role change(s)", admin.display_name, run.role_changes)
    return _mappings_out(session)


@router.post("/sync", response_model=DirectorySyncRunOut)
def sync_now(admin: Admin, session: SessionDep, dry_run: bool = False):
    run = sync.full_sync(session, directory(), dry_run=dry_run)
    session.add(run)
    session.commit()
    what = "Dry run" if dry_run else "Sync"
    if run.error:
        log.error("%s by %s failed: %s", what, admin.display_name, run.error)
    else:
        log.info("%s by %s: %s users, %s created, %s deactivated, %s role changes", what, admin.display_name, run.users_seen, run.created, run.deactivated, run.role_changes)
    return run


@router.get("/runs", response_model=list[DirectorySyncRunOut])
def sync_runs(_: Admin, session: SessionDep, limit: int = Query(default=20, ge=1, le=200)):
    return session.scalars(select(DirectorySyncRun).order_by(DirectorySyncRun.id.desc()).limit(limit)).all()
