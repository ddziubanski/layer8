# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finer permissions (Settings > Permissions): for each action below, who may do it beyond the
agent/admin roles: every technician, admins only, or admins plus technicians in chosen groups
(local or synced from AD). Admins may always; requesters never.

Stored as one app_settings document {key: {"who": "agents"|"admins"|"groups", "group_ids": [...]}};
an action not in it has its default, which is how the app behaved before the action had a permission.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import SessionDep, require_agent
from app.models import AppSetting, Group, User, UserRole, group_members

KEY = "permissions"
WHO = ("agents", "admins", "groups")


@dataclass(frozen=True)
class Permission:
    key: str
    area: str
    label: str
    default: str  # agents or admins
    help: str = ""


CATALOG = [
    Permission("action1_deploy", "Assets", "Deploy software through Action1", "admins", "Search packages, review a single device deployment and submit it. Separate opt-in credentials are required; existing patch status stays read-only."),
    Permission("nutanix_power", "Assets", "Restart or power on Nutanix VMs", "admins", "From a ticket the VM is linked to, when Settings > Nutanix > Power actions is on. A restart is graceful: through Nutanix Guest Tools when the VM has them, otherwise ACPI. Noted on the ticket."),
    Permission("nutanix_power_cycle", "Assets", "Power-cycle Nutanix VMs (hard restart)", "admins", "Like pulling the plug and starting it again: unsaved work in the VM is lost. Separate from a graceful restart. Noted on the ticket."),
    Permission("unifi_vouchers", "Assets", "Make UniFi guest Wi-Fi vouchers", "admins", "From a ticket, when Settings > UniFi > Guest Wi-Fi vouchers is on. Each voucher is noted on the ticket."),
    Permission("ticket_delete", "Tickets", "Delete tickets", "admins", "For the odd ticket that should never have existed (spam, a test). Gone for good, with its replies and files; the system log keeps who and what."),
    Permission("ticket_merge", "Tickets", "Merge tickets", "agents"),
    Permission("ticket_bulk", "Tickets", "Change many tickets at once from the queue", "agents"),
    Permission("ad_create_accounts", "Accounts", "Create AD accounts", "agents", "New account on a ticket's AD account card."),
    Permission("kb_publish", "Knowledge base", "Publish articles (and change published ones)", "agents", "Others can still write drafts for someone to publish."),
    Permission("kb_delete", "Knowledge base", "Delete articles", "admins", "Anyone who may publish can archive an article instead."),
    Permission("devices_add", "Assets", "Add devices by hand", "admins", "Devices from AD come in by themselves."),
    Permission("devices_delete", "Assets", "Delete devices", "admins"),
    Permission("vendors_delete", "Assets", "Delete vendors and systems", "admins"),
    Permission("ipam_delete", "Assets", "Delete subnets and VLANs", "admins"),
    Permission("po_delete", "Assets", "Delete purchase orders", "admins"),
    Permission("queues_manage", "Settings areas", "Queues: create them and change any queue", "admins", "Including its managers, whether it's the default or in use, and who may see it. A queue's managers can always change the rest of their own queue."),
    Permission("rules_manage", "Settings areas", "Ticket rules: create, change, test and run", "admins"),
    Permission("notices_manage", "Settings areas", "Service notices: post and take down", "admins"),
    Permission("templates_manage", "Settings areas", "Templates: checklists, changes and onboarding", "admins"),
    Permission("scripts_manage", "Settings areas", "Scripts: write and change them", "admins", "Who may run each script is set on the script. Writing scripts is as powerful as running them: a writer can mark a script as one technicians may run."),
]
BY_KEY = {p.key: p for p in CATALOG}


class PermissionError_(ValueError):
    """The message is safe to show."""


def stored(session: Session) -> dict:
    row = session.get(AppSetting, KEY)
    return dict(row.value) if row else {}


def rule(session: Session, key: str, saved: dict | None = None) -> dict:
    """{"who", "group_ids"} for one permission, its default when not set."""
    saved = stored(session) if saved is None else saved
    r = saved.get(key) or {}
    who = r.get("who") if r.get("who") in WHO else BY_KEY[key].default
    return {"who": who, "group_ids": [int(g) for g in r.get("group_ids") or []] if who == "groups" else []}


def _member(session: Session, user: User, group_ids: list[int]) -> bool:
    if not group_ids:
        return False
    return session.scalar(
        select(group_members.c.user_id)
        .join(Group, Group.id == group_members.c.group_id)
        .where(group_members.c.user_id == user.id, Group.id.in_(group_ids), Group.is_active)
        .limit(1)
    ) is not None


def not_allowed(session: Session, user: User, key: str, saved: dict | None = None) -> str | None:
    """Why the user may not (a sentence to show), or None when they may."""
    p = BY_KEY[key]
    if user.role == UserRole.admin and not user.portal_only:
        return None
    if user.role != UserRole.agent or user.portal_only:
        return "Technicians only"
    r = rule(session, key, saved)
    if r["who"] == "agents" or (r["who"] == "groups" and _member(session, user, r["group_ids"])):
        return None
    if r["who"] == "groups":
        names = list(session.scalars(select(Group.name).where(Group.id.in_(r["group_ids"] or [0])).order_by(Group.name)))
        return f"{p.label}: only admins" + (f" and members of {', '.join(names)}" if names else "") + " may (Settings > Permissions)"
    return f"{p.label}: only admins may (Settings > Permissions)"


def allowed(session: Session, user: User, key: str, saved: dict | None = None) -> bool:
    return not_allowed(session, user, key, saved) is None


def granted(session: Session, user: User) -> list[str]:
    """Every permission the user has (for the UI to show only what they can do)."""
    saved = stored(session)
    return [p.key for p in CATALOG if allowed(session, user, p.key, saved)]


def save(session: Session, rules: dict[str, dict]) -> None:
    """Replace the given permissions' rules (others keep theirs). Doesn't commit."""
    current = stored(session)
    for key, r in rules.items():
        if key not in BY_KEY:
            raise PermissionError_(f"Unknown permission {key!r}")
        who = r.get("who")
        if who not in WHO:
            raise PermissionError_(f"{BY_KEY[key].label}: choose agents, admins or groups")
        ids = sorted({int(g) for g in r.get("group_ids") or []}) if who == "groups" else []
        if who == "groups":
            found = set(session.scalars(select(Group.id).where(Group.id.in_(ids or [0]))))
            if not ids or found != set(ids):
                raise PermissionError_(f"{BY_KEY[key].label}: pick at least one existing group")
        current[key] = {"who": who, "group_ids": ids}
    row = session.get(AppSetting, KEY)
    if row is None:
        session.add(AppSetting(key=KEY, value=current))
    else:
        row.value = current


def require(key: str):
    """A FastAPI dependency: the signed-in technician, if the permission allows them (403 with the reason if not)."""

    def check(session: SessionDep, user: Annotated[User, Depends(require_agent)]) -> User:
        reason = not_allowed(session, user, key)
        if reason:
            raise HTTPException(status.HTTP_403_FORBIDDEN, reason)
        return user

    return Annotated[User, Depends(check)]
