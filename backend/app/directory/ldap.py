# SPDX-License-Identifier: AGPL-3.0-or-later
"""Active Directory / LDAP over ldap3.

Security choices:
- LDAPS (or StartTLS) with certificate checks; plain ldap:// is refused unless
  ldap_allow_insecure is set, because user passwords travel in the bind.
- Empty passwords are refused before binding: LDAP treats a simple bind with a
  DN and no password as an anonymous bind, which "succeeds".
- Every value put into a filter is escaped.
"""

import ssl
import uuid
from collections.abc import Iterator

from ldap3 import FIRST, SUBTREE, Connection, Server, ServerPool, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException, LDAPInvalidCredentialsResult
from ldap3.utils.conv import escape_bytes, escape_filter_chars

from datetime import UTC, datetime, timedelta

from app.directory.types import DirectoryComputer, DirectoryError, DirectoryGroup, DirectoryUser

# AD's "in chain" matching rule: membership through any depth of nesting.
IN_CHAIN = "1.2.840.113556.1.4.1941"
ACCOUNTDISABLE = 0x2

USER_ATTRS = {
    "ad": ["objectGUID", "mail", "proxyAddresses", "displayName", "givenName", "sn", "userPrincipalName",
           "sAMAccountName", "department", "title", "telephoneNumber", "userAccountControl",
           "mobile", "physicalDeliveryOfficeName", "company", "manager"],
    "generic": ["entryUUID", "mail", "cn", "displayName", "givenName", "sn", "uid", "departmentNumber", "title", "telephoneNumber",
                "mobile", "physicalDeliveryOfficeName", "o", "manager"],
}
GUID_ATTR = {"ad": "objectGUID", "generic": "entryUUID"}
COMPUTER_ATTRS = ["objectGUID", "cn", "dNSHostName", "operatingSystem", "operatingSystemVersion", "description",
                  "userAccountControl", "lastLogonTimestamp", "managedBy"]
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
GROUP_FILTER = {"ad": "(objectClass=group)", "generic": "(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))"}
PAGE = 500
MAX_GROUP_DEPTH = 20


def filetime(value) -> datetime | None:
    """AD's 100-nanosecond intervals since 1601 (0 or the maximum mean never)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n <= 0 or n >= 0x7FFFFFFFFFFFFFFF:
        return None
    return FILETIME_EPOCH + timedelta(microseconds=n // 10)


def _first(entry: dict, name: str) -> str | None:
    value = entry["attributes"].get(name)
    if isinstance(value, list):
        value = value[0] if value else None
    if value in (None, "", []):
        return None
    return str(value)


class LdapDirectory:
    def __init__(
        self,
        urls: list[str],
        *,
        bind_dn: str,
        bind_password: str,
        base_dn: str,
        user_filter: str,
        group_base_dn: str = "",
        computer_base_dn: str = "",
        computer_filter: str = "(objectCategory=computer)",
        kind: str = "ad",
        ca_file: str = "",
        start_tls: bool = False,
        allow_insecure: bool = False,
        timeout: int = 10,
    ):
        if kind not in USER_ATTRS:
            raise DirectoryError(f"ldap_kind must be 'ad' or 'generic', not {kind!r}")
        if not urls or not base_dn:
            raise DirectoryError("Set ldap_url and ldap_base_dn")
        secure = all(u.lower().startswith("ldaps://") for u in urls) or start_tls
        if not secure and not allow_insecure:
            raise DirectoryError("Use ldaps:// (or StartTLS): plain LDAP would send passwords in the clear")
        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=ca_file or None, version=ssl.PROTOCOL_TLS_CLIENT)
        servers = [Server(u, use_ssl=u.lower().startswith("ldaps://"), tls=tls, connect_timeout=timeout) for u in urls]
        # Try servers in order; fail over to the next DC if one is down.
        self._pool = ServerPool(servers, FIRST, active=1, exhaust=60)
        self._bind = (bind_dn, bind_password)
        self._base = base_dn
        self._group_base = group_base_dn or base_dn
        self._computer_base = computer_base_dn or base_dn
        self._computer_filter = computer_filter
        self._user_filter = user_filter
        self._kind = kind
        self._start_tls = start_tls
        self._timeout = timeout

    # Connections

    def _connect(self, user: str, password: str) -> Connection:
        conn = Connection(self._pool, user=user, password=password, read_only=True, receive_timeout=self._timeout, raise_exceptions=True)
        try:
            conn.open()
            if self._start_tls:
                conn.start_tls()
            conn.bind()
        except (LDAPInvalidCredentialsResult, LDAPBindError):
            conn.unbind()
            raise
        except LDAPException as exc:
            raise DirectoryError(f"Directory unavailable: {exc}") from None
        return conn

    def _service(self) -> Connection:
        try:
            return self._connect(*self._bind)
        except (LDAPInvalidCredentialsResult, LDAPBindError):
            raise DirectoryError("The directory service account was refused (check ldap_bind_dn and its password)") from None

    def _search(self, conn: Connection, base: str, flt: str, attrs: list[str], *, size: int = 0) -> Iterator[dict]:
        try:
            if size:
                conn.search(base, flt, SUBTREE, attributes=attrs, size_limit=size)
                yield from (e for e in conn.response if e.get("type") == "searchResEntry")
                return
            yield from (
                e
                for e in conn.extend.standard.paged_search(base, flt, SUBTREE, attributes=attrs, paged_size=PAGE, generator=True)
                if e.get("type") == "searchResEntry"
            )
        except LDAPException as exc:
            raise DirectoryError(f"Directory search failed: {exc}") from None

    # Mapping entries

    def _guid(self, entry: dict) -> str:
        raw = entry["raw_attributes"].get(GUID_ATTR[self._kind])
        raw = raw[0] if isinstance(raw, list) and raw else raw
        if not raw:
            raise DirectoryError(f"{entry['dn']} has no {GUID_ATTR[self._kind]}")
        if self._kind == "ad":
            # objectGUID is 16 bytes in Microsoft's mixed-endian layout.
            return str(uuid.UUID(bytes_le=bytes(raw)))
        return str(uuid.UUID(raw.decode() if isinstance(raw, bytes) else str(raw)))

    def _guid_filter(self, guid: str) -> str:
        if self._kind == "ad":
            return f"(objectGUID={escape_bytes(uuid.UUID(guid).bytes_le)})"
        return f"(entryUUID={escape_filter_chars(guid)})"

    def _user(self, entry: dict) -> DirectoryUser:
        a = entry["attributes"]
        proxies = [str(p) for p in (a.get("proxyAddresses") or [])]
        primary = next((p[5:] for p in proxies if p.startswith("SMTP:")), None)
        aliases = [p[5:] for p in proxies if p.lower().startswith("smtp:") and not p.startswith("SMTP:")]
        upn = _first(entry, "userPrincipalName")
        email = _first(entry, "mail") or primary or upn or ""
        if primary and primary.lower() != email.lower():
            aliases.append(primary)
        name = _first(entry, "displayName") or " ".join(filter(None, [_first(entry, "givenName"), _first(entry, "sn")])) or _first(entry, "cn") or email
        uac = int(_first(entry, "userAccountControl") or 0)
        return DirectoryUser(
            guid=self._guid(entry),
            dn=entry["dn"],
            email=email,
            display_name=name,
            enabled=not uac & ACCOUNTDISABLE,
            upn=upn,
            sam=_first(entry, "sAMAccountName") or _first(entry, "uid"),
            department=_first(entry, "department") or _first(entry, "departmentNumber"),
            title=_first(entry, "title"),
            phone=_first(entry, "telephoneNumber"),
            mobile=_first(entry, "mobile"),
            office=_first(entry, "physicalDeliveryOfficeName"),
            company=_first(entry, "company") or _first(entry, "o"),
            manager_dn=_first(entry, "manager"),
            aliases=sorted({x for x in aliases if x.lower() != email.lower()}, key=str.lower),
        )

    def _computer(self, entry: dict) -> DirectoryComputer:
        raw = entry["raw_attributes"].get("lastLogonTimestamp")
        raw = raw[0] if isinstance(raw, list) and raw else raw
        uac = int(_first(entry, "userAccountControl") or 0)
        return DirectoryComputer(
            guid=self._guid(entry),
            dn=entry["dn"],
            name=_first(entry, "cn") or entry["dn"],
            dns_hostname=_first(entry, "dNSHostName"),
            os=_first(entry, "operatingSystem"),
            os_version=_first(entry, "operatingSystemVersion"),
            description=_first(entry, "description"),
            enabled=not uac & ACCOUNTDISABLE,
            last_logon_at=filetime(raw.decode() if isinstance(raw, bytes) else raw),
            managed_by=_first(entry, "managedBy"),
        )

    def _group(self, entry: dict) -> DirectoryGroup:
        return DirectoryGroup(
            guid=self._guid(entry),
            dn=entry["dn"],
            name=_first(entry, "cn") or _first(entry, "sAMAccountName") or entry["dn"],
            description=_first(entry, "description"),
        )

    # Directory protocol

    def describe(self) -> dict:
        conn = self._service()
        try:
            who = conn.extend.standard.who_am_i() if self._kind == "ad" else self._bind[0]
            return {"server": str(conn.server.host), "bound_as": who or self._bind[0], "base_dn": self._base, "kind": self._kind}
        finally:
            conn.unbind()

    def users(self) -> Iterator[DirectoryUser]:
        conn = self._service()
        try:
            for entry in self._search(conn, self._base, self._user_filter, USER_ATTRS[self._kind]):
                yield self._user(entry)
        finally:
            conn.unbind()

    def computers(self) -> Iterator[DirectoryComputer]:
        if self._kind != "ad" or not self._computer_filter:
            return
        conn = self._service()
        try:
            for entry in self._search(conn, self._computer_base, self._computer_filter, COMPUTER_ATTRS):
                yield self._computer(entry)
        finally:
            conn.unbind()

    def find_user(self, login: str) -> DirectoryUser | None:
        login = login.strip()
        if "\\" in login:
            login = login.split("\\", 1)[1]  # CORP\jpark
        if not login:
            return None
        v = escape_filter_chars(login)
        if self._kind == "ad":
            by = f"(|(userPrincipalName={v})(mail={v})(proxyAddresses=smtp:{v})(sAMAccountName={v}))"
        else:
            by = f"(|(mail={v})(uid={v}))"
        conn = self._service()
        try:
            found = list(self._search(conn, self._base, f"(&{self._user_filter}{by})", USER_ATTRS[self._kind], size=2))
        finally:
            conn.unbind()
        # Ambiguous logins don't sign anyone in.
        return self._user(found[0]) if len(found) == 1 else None

    def check_password(self, user: DirectoryUser, password: str) -> bool:
        if not password:
            return False
        try:
            conn = self._connect(user.dn, password)
        except (LDAPInvalidCredentialsResult, LDAPBindError):
            return False
        conn.unbind()
        return True

    def search_groups(self, query: str, limit: int = 25) -> list[DirectoryGroup]:
        v = escape_filter_chars(query.strip())
        name = f"(|(cn=*{v}*)(sAMAccountName=*{v}*)(description=*{v}*))" if self._kind == "ad" else f"(|(cn=*{v}*)(description=*{v}*))"
        conn = self._service()
        try:
            entries = self._search(conn, self._group_base, f"(&{GROUP_FILTER[self._kind]}{name})", [GUID_ATTR[self._kind], "cn", "sAMAccountName", "description"], size=limit)
            return sorted((self._group(e) for e in entries), key=lambda g: g.name.lower())
        finally:
            conn.unbind()

    def group_by_guid(self, guid: str) -> DirectoryGroup | None:
        conn = self._service()
        try:
            found = list(self._search(conn, self._group_base, f"(&{GROUP_FILTER[self._kind]}{self._guid_filter(guid)})", [GUID_ATTR[self._kind], "cn", "sAMAccountName", "description"], size=1))
            return self._group(found[0]) if found else None
        finally:
            conn.unbind()

    def group_member_guids(self, group: DirectoryGroup) -> set[str]:
        conn = self._service()
        try:
            if self._kind == "ad":
                flt = f"(&{self._user_filter}(memberOf:{IN_CHAIN}:={escape_filter_chars(group.dn)}))"
                return {self._guid(e) for e in self._search(conn, self._base, flt, ["objectGUID"])}
            return self._walk_members(conn, group.dn)
        finally:
            conn.unbind()

    def direct_groups(self, member_dn: str) -> list[DirectoryGroup]:
        conn = self._service()
        try:
            flt = f"(&{GROUP_FILTER[self._kind]}(member={escape_filter_chars(member_dn)}))"
            return sorted((self._group(e) for e in self._search(conn, self._base, flt, [GUID_ATTR[self._kind], "cn", "sAMAccountName", "description"])), key=lambda g: g.name.lower())
        finally:
            conn.unbind()

    def group_children(self, group_dn: str) -> list[DirectoryGroup]:
        """Member groups: found by their own memberOf (AD) or by reading member (generic)."""
        conn = self._service()
        try:
            if self._kind == "ad":
                flt = f"(&(objectClass=group)(memberOf={escape_filter_chars(group_dn)}))"
                return [self._group(e) for e in self._search(conn, self._base, flt, ["objectGUID", "cn", "sAMAccountName", "description"])]
            return []
        finally:
            conn.unbind()

    def search_ous(self, query: str, bases: list[str], limit: int = 50) -> list[tuple[str, str]]:
        v = escape_filter_chars(query.strip())
        flt = f"(&(objectClass=organizationalUnit)(ou=*{v}*))" if v else "(objectClass=organizationalUnit)"
        conn = self._service()
        try:
            out: dict[str, str] = {}
            for base in bases or [self._base]:
                for e in self._search(conn, base, flt, ["ou"], size=limit):
                    out[e["dn"]] = _first(e, "ou") or e["dn"]
            return sorted(out.items(), key=lambda x: x[0].lower())[:limit]
        finally:
            conn.unbind()

    def groups_named(self, prefix: str, bases: list[str], limit: int = 500) -> list[DirectoryGroup]:
        v = escape_filter_chars(prefix.strip())
        flt = f"(&{GROUP_FILTER[self._kind]}(|(cn={v}*)(displayName={v}*)))"
        conn = self._service()
        try:
            out: dict[str, DirectoryGroup] = {}
            for base in bases or [self._group_base]:
                for e in self._search(conn, base, flt, [GUID_ATTR[self._kind], "cn", "sAMAccountName", "description"], size=limit):
                    g = self._group(e)
                    out[g.dn.lower()] = g
            return sorted(out.values(), key=lambda g: g.name.lower())[:limit]
        finally:
            conn.unbind()

    def account_taken(self, *, sam: str, upn: str, mail: str) -> list[tuple[str, str]]:
        checks = [("account name", "sAMAccountName", sam), ("UPN", "userPrincipalName", upn), ("email address", "mail", mail), ("email address", "proxyAddresses", f"smtp:{mail}" if mail else "")]
        checks = [(what, attr, v) for what, attr, v in checks if v]
        if not checks:
            return []
        flt = "(|" + "".join(f"({attr}={escape_filter_chars(v)})" for _, attr, v in checks) + ")"
        conn = self._service()
        try:
            out = []
            for e in self._search(conn, self._domain_base(), flt, [attr for _, attr, _ in checks], size=10):
                for what, attr, v in checks:
                    have = e["attributes"].get(attr) or []
                    have = have if isinstance(have, list) else [have]
                    if any(str(h).lower() == v.lower() for h in have) and (what, e["dn"]) not in out:
                        out.append((what, e["dn"]))
            return out
        finally:
            conn.unbind()

    def _domain_base(self) -> str:
        """The whole domain (names are unique domain-wide, not just under the search base)."""
        parts = [p for p in self._base.split(",") if p.strip().lower().startswith("dc=")]
        return ",".join(parts) or self._base

    def _walk_members(self, conn: Connection, group_dn: str) -> set[str]:
        """Generic LDAP: follow member/uniqueMember through nested groups, cycle-safe."""
        users: set[str] = set()
        seen: set[str] = set()
        todo = [(group_dn, 0)]
        while todo:
            dn, depth = todo.pop()
            if dn.lower() in seen or depth > MAX_GROUP_DEPTH:
                continue
            seen.add(dn.lower())
            conn.search(dn, "(objectClass=*)", "BASE", attributes=["member", "uniqueMember"])
            for entry in conn.response or []:
                for member in list(entry["attributes"].get("member") or []) + list(entry["attributes"].get("uniqueMember") or []):
                    conn.search(member, "(objectClass=*)", "BASE", attributes=["objectClass", GUID_ATTR[self._kind]])
                    for m in conn.response or []:
                        classes = {c.lower() for c in m["attributes"].get("objectClass", [])}
                        if classes & {"groupofnames", "groupofuniquenames", "group"}:
                            todo.append((m["dn"], depth + 1))
                        else:
                            users.add(self._guid(m))
        return users

    def user_group_guids(self, user: DirectoryUser) -> set[str]:
        conn = self._service()
        try:
            if self._kind == "ad":
                flt = f"(&(objectClass=group)(member:{IN_CHAIN}:={escape_filter_chars(user.dn)}))"
                return {self._guid(e) for e in self._search(conn, self._group_base, flt, ["objectGUID"])}
            # Generic: groups naming the user, then the groups containing those, and so on.
            found: set[str] = set()
            frontier = [user.dn]
            seen: set[str] = set()
            depth = 0
            while frontier and depth <= MAX_GROUP_DEPTH:
                nxt = []
                for dn in frontier:
                    v = escape_filter_chars(dn)
                    for e in self._search(conn, self._group_base, f"(&{GROUP_FILTER['generic']}(|(member={v})(uniqueMember={v})))", ["entryUUID"]):
                        if e["dn"].lower() not in seen:
                            seen.add(e["dn"].lower())
                            found.add(self._guid(e))
                            nxt.append(e["dn"])
                frontier, depth = nxt, depth + 1
            return found
        finally:
            conn.unbind()


def from_settings(settings) -> LdapDirectory | None:
    if not settings.ldap_url:
        return None
    return LdapDirectory(
        [u.strip() for u in settings.ldap_url.split(",") if u.strip()],
        bind_dn=settings.ldap_bind_dn,
        bind_password=settings.ldap_bind_password,
        base_dn=settings.ldap_base_dn,
        user_filter=settings.ldap_user_filter,
        group_base_dn=settings.ldap_group_base_dn,
        computer_base_dn=settings.ldap_computer_base_dn,
        computer_filter=settings.ldap_computer_filter,
        kind=settings.ldap_kind,
        ca_file=settings.ldap_ca_file,
        start_tls=settings.ldap_start_tls,
        allow_insecure=settings.ldap_allow_insecure,
        timeout=settings.ldap_timeout_seconds,
    )

