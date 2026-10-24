# SPDX-License-Identifier: AGPL-3.0-or-later
"""An in-memory directory with nested groups, for sync and sign-in tests."""

import uuid
from dataclasses import replace

from app.directory.types import DirectoryComputer, DirectoryError, DirectoryGroup, DirectoryUser


def guid() -> str:
    return str(uuid.uuid4())


class FakeDirectory:
    def __init__(self):
        self.users_by_guid: dict[str, DirectoryUser] = {}
        self.passwords: dict[str, str] = {}
        self.groups: dict[str, DirectoryGroup] = {}
        # group guid -> member guids (users or groups)
        self.members: dict[str, set[str]] = {}
        self.computers_by_guid: dict[str, DirectoryComputer] = {}
        self.down = False
        self.binds: list[str] = []

    # Building

    def add_user(self, email, name, password="Correct-horse-1", *, enabled=True, sam=None, aliases=(), upn=None, dept=None):
        g = guid()
        user = DirectoryUser(
            guid=g, dn=f"CN={name},OU=Staff,DC=corp,DC=local", email=email, display_name=name, enabled=enabled,
            upn=upn or email, sam=sam, department=dept, aliases=list(aliases),
        )
        self.users_by_guid[g] = user
        self.passwords[g] = password
        return user

    def add_computer(self, name, **kw):
        g = guid()
        self.computers_by_guid[g] = DirectoryComputer(guid=g, dn=f"CN={name},OU=Workstations,DC=corp,DC=local", name=name, dns_hostname=f"{name.lower()}.corp.local", **kw)
        return self.computers_by_guid[g]

    def add_group(self, name, *members):
        g = guid()
        self.groups[g] = DirectoryGroup(guid=g, dn=f"CN={name},OU=Groups,DC=corp,DC=local", name=name)
        self.members[g] = {m.guid for m in members}
        return self.groups[g]

    def put(self, user: DirectoryUser, **changes):
        self.users_by_guid[user.guid] = replace(self.users_by_guid[user.guid], **changes)

    # Directory protocol

    def _check(self):
        if self.down:
            raise DirectoryError("Directory unavailable: dc1.corp.local:636 timed out")

    def describe(self):
        self._check()
        return {"server": "dc1.corp.local", "bound_as": "u:CORP\\svc-helpdesk", "base_dn": "DC=corp,DC=local", "kind": "ad"}

    def users(self):
        self._check()
        yield from list(self.users_by_guid.values())

    def computers(self):
        self._check()
        yield from list(self.computers_by_guid.values())

    def find_user(self, login):
        self._check()
        v = login.split("\\")[-1].lower()
        found = [
            u for u in self.users_by_guid.values()
            if v in {u.email.lower(), (u.upn or "").lower(), (u.sam or "").lower(), *(a.lower() for a in u.aliases)}
        ]
        return found[0] if len(found) == 1 else None

    def check_password(self, user, password):
        self._check()
        if not password:
            return False
        self.binds.append(user.dn)
        return self.passwords.get(user.guid) == password

    def direct_groups(self, member_dn):
        self._check()
        guid = next((u.guid for u in self.users_by_guid.values() if u.dn.lower() == member_dn.lower()), None)
        guid = guid or next((g.guid for g in self.groups.values() if g.dn.lower() == member_dn.lower()), None)
        return sorted((self.groups[g] for g, members in self.members.items() if guid in members), key=lambda g: g.name.lower())

    def group_children(self, group_dn):
        self._check()
        g = next((x for x in self.groups.values() if x.dn.lower() == group_dn.lower()), None)
        return [self.groups[m] for m in self.members.get(g.guid, set()) if m in self.groups] if g else []

    def search_ous(self, query, bases, limit=50):
        self._check()
        ous = getattr(self, "ous", [])
        inside = [dn for dn in ous if not bases or any(dn.lower().endswith(b.lower()) for b in bases)]
        return [(dn, dn.split(",")[0].split("=", 1)[1]) for dn in sorted(inside) if query.lower() in dn.lower()][:limit]

    def account_taken(self, *, sam, upn, mail):
        self._check()
        out = []
        for u in self.users_by_guid.values():
            if sam and (u.sam or "").lower() == sam.lower():
                out.append(("account name", u.dn))
            if upn and (u.upn or "").lower() == upn.lower():
                out.append(("UPN", u.dn))
            if mail and mail.lower() in [(u.email or "").lower(), *(x.lower() for x in u.aliases)]:
                out.append(("email address", u.dn))
        return out

    def groups_named(self, prefix, bases, limit=500):
        self._check()
        found = [g for g in self.groups.values() if g.name.lower().startswith(prefix.lower()) and (not bases or any(g.dn.lower().endswith(b.lower()) for b in bases))]
        return sorted(found, key=lambda g: g.name.lower())[:limit]

    def search_groups(self, query, limit=25):
        self._check()
        return sorted((g for g in self.groups.values() if query.lower() in g.name.lower()), key=lambda g: g.name)[:limit]

    def group_by_guid(self, g):
        self._check()
        return self.groups.get(g)

    def _expand(self, g, seen):
        out = set()
        for m in self.members.get(g, set()):
            if m in self.groups and m not in seen:
                seen.add(m)
                out |= self._expand(m, seen)
            elif m in self.users_by_guid:
                out.add(m)
        return out

    def group_member_guids(self, group):
        self._check()
        return self._expand(group.guid, {group.guid})

    def user_group_guids(self, user):
        self._check()
        return {g for g in self.groups if user.guid in self._expand(g, {g})}
