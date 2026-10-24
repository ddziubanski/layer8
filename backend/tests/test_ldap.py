# SPDX-License-Identifier: AGPL-3.0-or-later
"""LdapDirectory without a server: TLS rules, empty passwords, AD attribute parsing, filter escaping."""

import uuid

import pytest

from app.directory.ldap import LdapDirectory
from app.directory.types import DirectoryError

BASE = dict(bind_dn="CN=svc,DC=corp,DC=local", bind_password="x", base_dn="DC=corp,DC=local", user_filter="(objectClass=user)")


def ad(**kw):
    return LdapDirectory(["ldaps://dc1.corp.local"], **{**BASE, **kw})


def test_plain_ldap_is_refused_unless_explicitly_allowed():
    with pytest.raises(DirectoryError, match="in the clear"):
        LdapDirectory(["ldap://dc1.corp.local"], **BASE)
    LdapDirectory(["ldap://dc1.corp.local"], **BASE, start_tls=True)
    LdapDirectory(["ldap://lab"], **BASE, allow_insecure=True)
    # One insecure server in the list is enough to refuse.
    with pytest.raises(DirectoryError):
        LdapDirectory(["ldaps://dc1", "ldap://dc2"], **BASE)


def test_empty_password_never_binds():
    d = ad()
    d._connect = lambda *a: pytest.fail("must not bind")  # noqa: E731
    from app.directory.types import DirectoryUser

    assert d.check_password(DirectoryUser(guid="g", dn="CN=x", email="x@y", display_name="x"), "") is False


def entry(**attrs):
    raw_guid = attrs.pop("guid")
    return {"dn": attrs.pop("dn", "CN=J. Park,OU=Staff,DC=corp,DC=local"), "attributes": attrs, "raw_attributes": {"objectGUID": [raw_guid]}}


def test_parses_ad_user_with_proxy_addresses_and_disabled_flag():
    g = uuid.uuid4()
    u = ad()._user(
        entry(
            guid=g.bytes_le,
            mail="",
            proxyAddresses=["SMTP:jpark@corp.local", "smtp:j.park@corp.com", "X500:/o=Org", "sip:jpark@corp.local"],
            displayName="J. Park",
            userPrincipalName="jpark@corp.onmicrosoft.com",
            sAMAccountName="jpark",
            userAccountControl=514,  # 512 normal + 2 disabled
            department="IT",
        )
    )
    assert u.guid == str(g)
    assert u.email == "jpark@corp.local"  # primary SMTP when mail is empty
    assert u.aliases == ["j.park@corp.com"]
    assert (u.enabled, u.sam, u.upn, u.department) == (False, "jpark", "jpark@corp.onmicrosoft.com", "IT")


def test_primary_smtp_differing_from_mail_becomes_an_alias():
    u = ad()._user(entry(guid=uuid.uuid4().bytes_le, mail="j.park@corp.local", proxyAddresses=["SMTP:jpark@corp.com"], displayName="J", userAccountControl=512))
    assert u.email == "j.park@corp.local" and u.aliases == ["jpark@corp.com"] and u.enabled


def test_login_input_is_escaped_in_filters():
    d = ad()
    seen = []

    class Conn:
        def unbind(self):
            pass

    d._service = lambda: Conn()  # noqa: E731
    d._search = lambda conn, base, flt, attrs, size=0: seen.append(flt) or iter(())  # noqa: E731
    assert d.find_user("CORP\\*)(objectClass=*") is None
    assert "\\2a\\29\\28objectClass=\\2a" in seen[0]
    assert ")(objectClass=*" not in seen[0].split("(objectClass=user)", 1)[1]


def test_guid_filter_escapes_binary():
    g = uuid.UUID("01020304-0506-0708-090a-0b0c0d0e0f10")
    assert ad()._guid_filter(str(g)) == "(objectGUID=\\04\\03\\02\\01\\06\\05\\08\\07\\09\\0a\\0b\\0c\\0d\\0e\\0f\\10)"
