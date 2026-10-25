# SPDX-License-Identifier: AGPL-3.0-or-later
"""Directory sync: users (with aliases), groups (nested membership), group-based roles, and
computers (as devices).

AD is the source of truth for directory users: their role comes only from role
mappings (highest wins; no mapped group means requester). Local accounts with a
password are never taken over, so a local break-glass admin always works.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from app import sla
from app.config import settings
from app.directory.ldap import from_settings
from app.directory.types import Directory, DirectoryComputer, DirectoryError, DirectoryUser
from app.models import AuthSource, Device, DeviceSource, DirectorySyncRun, Group, RoleMapping, User, UserEmail, UserRole, UserSession

RANK = {UserRole.requester: 0, UserRole.agent: 1, UserRole.admin: 2}
MAX_DETAILS = 200


class SyncAborted(RuntimeError):
    pass


def get_directory() -> Directory | None:
    """The configured directory, or None for a local-only install. Tests replace this."""
    return from_settings(settings)


def _note(run: DirectorySyncRun, kind: str, who: str, text: str) -> None:
    if len(run.details) < MAX_DETAILS:
        run.details = [*run.details, {"kind": kind, "user": who, "text": text}]


def find_by_address(session: Session, addresses: list[str]) -> User | None:
    lowered = [a.strip().lower() for a in addresses if a and a.strip()]
    if not lowered:
        return None
    return session.scalar(
        select(User)
        .where(or_(func.lower(User.email).in_(lowered), User.id.in_(select(UserEmail.user_id).where(func.lower(UserEmail.email).in_(lowered)))))
        .limit(1)
    )


def _address_owner(session: Session, address: str) -> int | None:
    a = address.lower()
    return session.scalar(select(User.id).where(func.lower(User.email) == a)) or session.scalar(
        select(UserEmail.user_id).where(func.lower(UserEmail.email) == a)
    )


def _end_sessions(session: Session, user: User) -> None:
    session.execute(delete(UserSession).where(UserSession.user_id == user.id))


def upsert_user(session: Session, du: DirectoryUser, now: datetime, run: DirectorySyncRun) -> User | None:
    """Create, link or update the account for a directory user. None if it conflicts with a local account."""
    user = session.scalar(select(User).where(User.directory_id == du.guid))
    created = linked = False
    if user is None:
        candidate = find_by_address(session, [du.email, *du.aliases])
        if candidate is not None:
            if candidate.directory_id and candidate.directory_id != du.guid:
                _note(run, "conflict", du.email, f"address already belongs to another directory account ({candidate.email})")
                return None
            if candidate.auth_source == AuthSource.local and candidate.password_hash is not None:
                _note(run, "conflict", du.email, "a local account with a password has this address; left as a local account")
                return None
            user, linked = candidate, True
            run.linked += 1
            _note(run, "linked", du.email, f"existing account linked to {du.dn}")
        else:
            if not du.email:
                _note(run, "skipped", du.dn, "no mail, proxyAddresses or UPN")
                return None
            user = User(email=du.email, display_name=du.display_name, role=UserRole.requester, auth_source=AuthSource.directory, role_source="default")
            session.add(user)
            created = True
            run.created += 1
            _note(run, "created", du.email, du.display_name)

    before = (user.email, user.display_name, user.upn, user.sam_account_name, user.department, user.title, user.phone, user.mobile, user.office, user.company, user.directory_dn, user.is_active)
    if du.email and du.email.lower() != (user.email or "").lower():
        owner = _address_owner(session, du.email)
        if owner and owner != user.id:
            _note(run, "conflict", du.email, f"can't change {user.email}'s address: another account uses it")
        else:
            user.email = du.email
    user.display_name = du.display_name or user.display_name
    user.upn, user.sam_account_name = du.upn, du.sam
    user.department, user.title, user.phone = du.department, du.title, du.phone
    user.mobile, user.office, user.company = du.mobile, du.office, du.company
    user.directory_id, user.directory_dn = du.guid, du.dn
    user.auth_source = AuthSource.directory
    # AD holds the password now; a stale local hash must never work again,
    # unless it's this technician's deliberate AD-down fallback.
    if not user.local_fallback:
        user.password_hash = None
    if user.is_active and not du.enabled:
        _note(run, "disabled", user.email, "disabled in the directory; signed out")
        if user.id:
            _end_sessions(session, user)
    user.is_active = du.enabled
    user.last_synced_at = now
    session.flush()

    wanted = {a.lower(): a for a in du.aliases if a.lower() != user.email.lower()}
    current = {e.email.lower(): e for e in session.scalars(select(UserEmail).where(UserEmail.user_id == user.id))}
    for key, row in current.items():
        if key not in wanted:
            session.delete(row)
    for key, address in wanted.items():
        if key in current:
            continue
        owner = _address_owner(session, address)
        if owner and owner != user.id:
            _note(run, "conflict", user.email, f"alias {address} already belongs to another account")
            continue
        session.add(UserEmail(user_id=user.id, email=address))
    session.flush()

    after = (user.email, user.display_name, user.upn, user.sam_account_name, user.department, user.title, user.phone, user.mobile, user.office, user.company, user.directory_dn, user.is_active)
    if not created and not linked and before != after:
        run.updated += 1
    return user


def link_managers(session: Session, pairs: list[tuple[User, str | None]]) -> None:
    """Point each user at their manager's account, matched by DN (a manager outside the sync scope: none)."""
    wanted = {dn.lower() for _, dn in pairs if dn}
    by_dn = {dn.lower(): uid for uid, dn in session.execute(select(User.id, User.directory_dn).where(func.lower(User.directory_dn).in_(wanted)))} if wanted else {}
    for user, dn in pairs:
        manager = by_dn.get(dn.lower()) if dn else None
        user.manager_id = manager if manager != user.id else None
    session.flush()


def sync_group(session: Session, directory: Directory, group: Group, now: datetime, run: DirectorySyncRun) -> None:
    dg = directory.group_by_guid(group.directory_id)
    if dg is None:
        # Deleted in AD (or out of scope): nobody keeps rights through it.
        if group.members:
            _note(run, "group", group.name, f"not found in the directory; {len(group.members)} member(s) removed")
        group.members = []
        group.sync_error = "Not found in the directory"
        group.last_synced_at = now
        return
    guids = directory.group_member_guids(dg)
    members = list(session.scalars(select(User).where(User.directory_id.in_(guids)))) if guids else []
    old = {u.id for u in group.members}
    new = {u.id for u in members}
    if old != new:
        _note(run, "group", group.name, f"+{len(new - old)} / -{len(old - new)} member(s)")
    group.members = members
    group.directory_dn = dg.dn
    group.sync_error = None
    group.last_synced_at = now
    run.groups_synced += 1


def role_for(user: User, mappings: list[RoleMapping]) -> tuple[UserRole, str]:
    best, source = UserRole.requester, "default"
    for m in mappings:
        if m.group.is_active and user in m.group.members and RANK[m.role] > RANK[best]:
            best, source = m.role, f"group:{m.group.name}"
    return best, source


def apply_roles(session: Session, run: DirectorySyncRun, users: list[User] | None = None) -> None:
    """Set directory users' roles from the mappings. Refuses changes that would leave no admin."""
    mappings = list(session.scalars(select(RoleMapping)))
    targets = users if users is not None else list(session.scalars(select(User).where(User.auth_source == AuthSource.directory)))
    plan = []
    for user in targets:
        role, source = role_for(user, mappings)
        if role != user.role or source != user.role_source:
            plan.append((user, role, source))

    planned = {u.id: r for u, r, _ in plan}
    admins = session.scalars(select(User).where(User.is_active, User.role == UserRole.admin)).all()
    remaining = [a for a in admins if planned.get(a.id, a.role) == UserRole.admin and (a.auth_source == AuthSource.directory or a.password_hash)]
    gained = [u for u, r, _ in plan if r == UserRole.admin and u.is_active]
    demotes_admin = any(u.role == UserRole.admin and r != UserRole.admin for u, r, _ in plan)
    if demotes_admin and not remaining and not gained:
        raise SyncAborted("These role mappings would leave no active admin; roles were not changed. Map an admin group, or keep a local admin.")

    for user, role, source in plan:
        if role != user.role:
            run.role_changes += 1
            _note(run, "role", user.email, f"{user.role.value} → {role.value} ({source.replace('group:', 'via ') if source != 'default' else 'no mapped group'})")
            if RANK[role] < RANK[user.role]:
                # Lost rights take effect now, not at their next sign-in.
                _end_sessions(session, user)
            if role == UserRole.requester and user.local_fallback:
                # Fallback sign-in is for technicians only.
                user.local_fallback = False
                user.password_hash = None
                _note(run, "fallback", user.email, "local fallback password removed (no longer an agent or admin)")
        user.role, user.role_source = role, source
    session.flush()


def tracked_groups(session: Session) -> list[Group]:
    return list(session.scalars(select(Group).where(Group.source == AuthSource.directory, Group.is_active, Group.directory_id.is_not(None))))


def upsert_device(session: Session, dc: DirectoryComputer, users_by_dn: dict[str, int]) -> bool:
    """Create or update the device for an AD computer. True if anything changed."""
    device = session.scalar(select(Device).where(Device.directory_id == dc.guid))
    if device is None:
        device = Device(source=DeviceSource.directory, directory_id=dc.guid)
        session.add(device)
    values = {
        "name": dc.name,
        "dns_hostname": dc.dns_hostname,
        "directory_dn": dc.dn,
        "os": dc.os,
        "os_version": dc.os_version,
        "description": dc.description,
        "enabled": dc.enabled,
        "is_active": True,
        "last_logon_at": dc.last_logon_at,
        "assigned_user_id": users_by_dn.get(dc.managed_by.lower()) if dc.managed_by else None,
    }
    changed = device.id is None
    for k, v in values.items():
        if getattr(device, k) != v:
            setattr(device, k, v)
            changed = True
    return changed


def sync_devices(session: Session, directory: Directory, run: DirectorySyncRun) -> None:
    users_by_dn = {dn.lower(): uid for uid, dn in session.execute(select(User.id, User.directory_dn).where(User.directory_dn.is_not(None)))}
    seen: set[str] = set()
    for dc in directory.computers():
        seen.add(dc.guid)
        upsert_device(session, dc, users_by_dn)
    run.devices_seen = len(seen)
    known = list(session.scalars(select(Device).where(Device.source == DeviceSource.directory, Device.is_active)))
    if not seen and known:
        _note(run, "skipped", "computers", "The directory returned no computers; devices were left as they were. Check the computer search base and filter.")
        return
    for device in known:
        if device.directory_id not in seen:
            device.is_active = False
            _note(run, "removed", device.name, "computer no longer in the directory scope")


def full_sync(session: Session, directory: Directory, now: datetime | None = None, *, dry_run: bool = False) -> DirectorySyncRun:
    now = now or sla.utcnow()
    run = DirectorySyncRun(started_at=now, dry_run=dry_run, details=[], users_seen=0, created=0, updated=0, linked=0, deactivated=0, role_changes=0, groups_synced=0, devices_seen=0)
    savepoint = session.begin_nested() if dry_run else None
    try:
        seen: set[str] = set()
        managers: list[tuple[User, str | None]] = []
        for du in directory.users():
            seen.add(du.guid)
            user = upsert_user(session, du, now, run)
            if user is not None:
                managers.append((user, du.manager_dn))
        run.users_seen = len(seen)
        link_managers(session, managers)

        known = list(session.scalars(select(User).where(User.auth_source == AuthSource.directory, User.directory_id.is_not(None))))
        if not seen and known:
            raise SyncAborted("The directory returned no users; refusing to deactivate everyone. Check ldap_base_dn and ldap_user_filter.")
        for user in known:
            if user.directory_id not in seen and user.is_active:
                if user.role == UserRole.admin and not session.scalar(
                    select(func.count()).select_from(User).where(User.role == UserRole.admin, User.is_active, User.id != user.id)
                ):
                    # Never leave the service desk without an admin (a narrowed search base, the last
                    # admin offboarded): kept, and said so, until someone else is an admin.
                    _note(run, "kept", user.email, "no longer in the directory scope, but the only active admin; kept active")
                    continue
                user.is_active = False
                _end_sessions(session, user)
                run.deactivated += 1
                _note(run, "removed", user.email, "no longer in the directory scope; deactivated")
        session.flush()

        for group in tracked_groups(session):
            sync_group(session, directory, group, now, run)
        session.flush()
        apply_roles(session, run)
        sync_devices(session, directory, run)
        session.flush()
    except (DirectoryError, SyncAborted) as exc:
        run.error = str(exc)
    finally:
        if savepoint is not None:
            savepoint.rollback()
    run.finished_at = sla.utcnow()
    return run


def sign_in_sync(session: Session, directory: Directory, du: DirectoryUser, now: datetime) -> User | None:
    """At sign-in: bring this one user up to date, including group memberships and role,
    so someone added to a group in AD gets access without waiting for the next sync."""
    run = DirectorySyncRun(started_at=now, details=[], created=0, updated=0, linked=0, role_changes=0)
    user = upsert_user(session, du, now, run)
    if user is None or not user.is_active:
        return None
    link_managers(session, [(user, du.manager_dn)])
    guids = directory.user_group_guids(du)
    for group in tracked_groups(session):
        inside = group.directory_id in guids
        if inside and user not in group.members:
            group.members.append(user)
        elif not inside and user in group.members:
            group.members.remove(user)
    session.flush()
    try:
        apply_roles(session, run, [user])
    except SyncAborted:
        pass  # keep their current role; the full sync reports the problem
    return user


# Any constant works; it just has to be the same in every worker.
SYNC_LOCK = 724_001


def run_if_due(session_factory, now: datetime, *, force: bool = False) -> DirectorySyncRun | None:
    """Worker hook: a full sync every ldap_sync_minutes (or now, if forced). Only one worker syncs at a time."""
    directory = get_directory()
    if directory is None:
        return None
    with session_factory() as session:
        last = session.scalar(select(func.max(DirectorySyncRun.started_at)).where(DirectorySyncRun.dry_run.is_(False)))
        if not force and last is not None and now - last < timedelta(minutes=settings.ldap_sync_minutes):
            return None
        if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": SYNC_LOCK}):
            return None
        run = full_sync(session, directory, now)
        session.add(run)
        session.commit()
        return run
