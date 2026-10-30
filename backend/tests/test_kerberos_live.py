# SPDX-License-Identifier: AGPL-3.0-or-later
"""Portal single sign-on with real Kerberos against the dev Samba AD (docker compose --profile ad up -d ad),
or the Windows Server lab with HELPDESK_TEST_AD=lab (tests/live_ad.py).

Skipped unless HELPDESK_TEST_LDAP=1. The container exports the portal's keytab
(HTTP/helpdesk.corp.local) to dev/ad/out; here we play the browser: get a ticket
as a seeded user with their password and send it as "Authorization: Negotiate".
"""

import base64
import os
from pathlib import Path

import pytest

from app import kerberos
from app.directory import sync
from app.directory.ldap import LdapDirectory
from tests.live_ad import AD
from tests.test_ldap_live import CFG

pytestmark = pytest.mark.skipif(os.environ.get("HELPDESK_TEST_LDAP") != "1", reason="set HELPDESK_TEST_LDAP=1 with the dev AD running")

gssapi = pytest.importorskip("gssapi")

KEYTAB = AD.keytab
SPNEGO = gssapi.OID.from_int_seq("1.3.6.1.5.5.2")
KRB5_CONF = f"""\
[libdefaults]
    default_realm = {AD.realm}
    dns_lookup_kdc = false
    dns_lookup_realm = false
    rdns = false
    dns_canonicalize_hostname = false
    udp_preference_limit = 1
[realms]
    {AD.realm} = {{
        kdc = {AD.kdc}
    }}
"""


@pytest.fixture(autouse=True)
def kerberos_env(monkeypatch, tmp_path):
    kerberos.keytab_path().parent.mkdir(parents=True)
    kerberos.keytab_path().write_bytes(Path(KEYTAB).read_bytes())
    conf = tmp_path / "krb5.conf"
    conf.write_text(KRB5_CONF)
    monkeypatch.setenv("KRB5_CONFIG", str(conf))
    monkeypatch.setenv("KRB5CCNAME", "MEMORY:helpdesk-test")
    # Keep the test's replay cache out of /var/tmp.
    monkeypatch.setenv("KRB5RCACHENAME", f"file2:{tmp_path / 'rcache'}")


ENTERPRISE = gssapi.OID.from_int_seq("1.2.840.113554.1.2.2.6")


def browser_token(user: str, password: str = "Correct-horse-1", host: str = AD.portal_host) -> bytes:
    """What Edge/Chrome send for http://<host>/: a SPNEGO token holding a service ticket.
    user is an account name (jpark) or, as at an email-style Windows sign-in, the UPN."""
    if "@" in user:
        name = gssapi.Name(user, ENTERPRISE)
    else:
        name = gssapi.Name(f"{user}@{AD.realm}", gssapi.NameType.kerberos_principal)
    creds = gssapi.raw.acquire_cred_with_password(name, password.encode(), usage="initiate", mechs=[SPNEGO]).creds
    target = gssapi.Name(f"HTTP@{host}", gssapi.NameType.hostbased_service)
    return gssapi.SecurityContext(name=target, creds=gssapi.Credentials(creds), usage="initiate", mech=SPNEGO).step()


def test_accepts_a_real_ticket():
    principal, reply = kerberos.accept(browser_token("jpark"))
    assert principal == f"jpark@{AD.realm}"
    assert reply  # mutual authentication


def test_email_style_sign_in_names_the_upn():
    principal, _ = kerberos.accept(browser_token(f"jpark@{AD.domain}"))
    assert principal == f"jpark\\@{AD.domain}@{AD.realm}"


def test_ticket_for_another_service_is_refused():
    # A ticket for a host whose key isn't in our keytab (e.g. the portal reached by another name).
    with pytest.raises(gssapi.exceptions.GSSError):
        browser_token("jpark", host=f"other.{AD.domain}")


def test_tampered_token_is_refused():
    token = bytearray(browser_token("jpark"))
    token[-20] ^= 0xFF
    with pytest.raises(kerberos.KerberosError):
        kerberos.accept(bytes(token))


def test_replayed_token_is_refused():
    token = browser_token("jpark")
    kerberos.accept(token)
    with pytest.raises(kerberos.KerberosError):
        kerberos.accept(token)


def test_ntlm_is_refused():
    # What a browser sends when Kerberos isn't possible (IP address, off the domain): an NTLM negotiate message.
    with pytest.raises(kerberos.KerberosError):
        kerberos.accept(b"NTLMSSP\x00\x01\x00\x00\x00\x07\x82\x08\xa2" + bytes(16))


def test_dev_keytab_passes_the_upload_checks(db, admin):
    config = kerberos.install_keytab(db, Path(KEYTAB).read_bytes(), admin)
    assert config.realm == AD.realm
    assert config.keytab_principals == [f"HTTP/{AD.portal_host}@{AD.realm}"]
    assert "aes256-cts-hmac-sha1-96" in config.keytab_enctypes


def test_portal_sign_in_end_to_end(client, db, monkeypatch):
    kerberos.save(db, kerberos.WindowsSignIn(enabled=True, realm=AD.realm), None)
    db.commit()
    directory = LdapDirectory([AD.url], **CFG)
    monkeypatch.setattr(sync, "get_directory", lambda: directory)
    token = base64.b64encode(browser_token(f"ralvarez@{AD.domain}")).decode()
    r = client.post("/api/auth/sso", headers={"Authorization": f"Negotiate {token}"})
    assert r.status_code == 200, r.text
    assert (r.json()["email"], r.json()["portal_only"]) == (f"ralvarez@{AD.domain}", True)
    assert r.headers["www-authenticate"].startswith("Negotiate ")
    assert client.get("/api/portal/tickets").status_code == 200
