# SPDX-License-Identifier: AGPL-3.0-or-later
"""What sync and sign-in need from a directory. LDAP/AD implements it; tests use a fake."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class DirectoryError(RuntimeError):
    """The directory couldn't be reached or answered with an error."""


@dataclass
class DirectoryUser:
    guid: str  # objectGUID (AD) or entryUUID: stable across renames and moves
    dn: str
    email: str
    display_name: str
    enabled: bool = True
    upn: str | None = None
    sam: str | None = None
    department: str | None = None
    title: str | None = None
    phone: str | None = None
    mobile: str | None = None
    office: str | None = None
    company: str | None = None
    # The manager's DN; sync links it to their account once everyone is in.
    manager_dn: str | None = None
    # Other addresses (proxyAddresses), not including email.
    aliases: list[str] = field(default_factory=list)


@dataclass
class DirectoryComputer:
    guid: str
    dn: str
    name: str
    dns_hostname: str | None = None
    os: str | None = None
    os_version: str | None = None
    description: str | None = None
    enabled: bool = True
    last_logon_at: datetime | None = None
    # DN of the managedBy user, if any.
    managed_by: str | None = None


@dataclass
class DirectoryGroup:
    guid: str
    dn: str
    name: str
    description: str | None = None


class Directory(Protocol):
    def describe(self) -> dict:
        """Connection check: servers, base DN, who we bound as. Raises DirectoryError."""

    def users(self) -> Iterator[DirectoryUser]:
        """Every account in scope (ldap_user_filter under the base DN)."""

    def find_user(self, login: str) -> DirectoryUser | None:
        """By UPN, email, alias, sAMAccountName or DOMAIN\\name."""

    def check_password(self, user: DirectoryUser, password: str) -> bool:
        """Bind as the user. False for wrong password; DirectoryError if the directory is down."""

    def computers(self) -> Iterator[DirectoryComputer]:
        """Computer objects in scope (AD only; other directories have none)."""

    def search_groups(self, query: str, limit: int = 25) -> list[DirectoryGroup]: ...

    def group_by_guid(self, guid: str) -> DirectoryGroup | None: ...

    def group_member_guids(self, group: DirectoryGroup) -> set[str]:
        """GUIDs of users in the group, including through nested groups."""

    def user_group_guids(self, user: DirectoryUser) -> set[str]:
        """GUIDs of every group the user is in, including nested."""

    def direct_groups(self, member_dn: str) -> list[DirectoryGroup]:
        """Groups the object (user or group) is directly a member of."""

    def group_children(self, group_dn: str) -> list[DirectoryGroup]:
        """Groups that are members of this group (nesting)."""

    def groups_named(self, prefix: str, bases: list[str], limit: int = 500) -> list[DirectoryGroup]:
        """Groups whose name starts with prefix (e.g. "[EMAIL]" for distribution lists), under these OUs."""
        ...

    def account_taken(self, *, sam: str, upn: str, mail: str) -> list[tuple[str, str]]:
        """What's already used by another object: ("account name" | "UPN" | "email address", its DN)."""
        ...

    def search_ous(self, query: str, bases: list[str], limit: int = 50) -> list[tuple[str, str]]:
        """Organizational units under the bases, as (dn, name)."""
