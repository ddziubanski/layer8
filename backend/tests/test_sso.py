# SPDX-License-Identifier: AGPL-3.0-or-later
"""Portal single sign-on (Kerberos): who gets in, and that it's portal-only.

The Kerberos check itself is faked here: a "token" is just the principal it proves.
test_kerberos_live.py runs the real thing against the dev Samba AD.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from app import auth, kerberos
from app.main import app
from tests.conftest import ANSWERS, as_user
from tests.test_directory import PW, ad, import_group, map_roles, mapped, org, run_sync, user  # noqa: F401

CSRF = {auth.CSRF_HEADER: "1"}


@pytest.fixture(autouse=True)
def sso_on(db, monkeypatch):
    # dev_auth stays on for the fixtures' admin setup; a session cookie always takes precedence.
    kerberos.keytab_path().parent.mkdir(parents=True)
    kerberos.keytab_path().write_bytes(b"fake")
    config = kerberos.WindowsSignIn(enabled=True, realm="CORP.LOCAL", keytab_principals=["HTTP/helpdesk.corp.local@CORP.LOCAL"])
    kerberos.save(db, config, None)
    db.commit()

    def fake_accept(token):
        if token == b"bad":
            raise kerberos.KerberosError("Decrypt integrity check failed")
        return token.decode(), b"mutual"

    monkeypatch.setattr(kerberos, "accept", fake_accept)


def negotiate(principal: str) -> dict:
    return {"Authorization": "Negotiate " + base64.b64encode(principal.encode()).decode()}


def sso(client, principal="ralvarez@CORP.LOCAL"):
    return client.post("/api/auth/sso", headers=negotiate(principal))


def test_first_request_asks_the_browser_for_a_ticket(client):
    r = client.post("/api/auth/sso")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Negotiate"


def test_options_follow_settings(client, db):
    assert client.get("/api/auth/options").json() == {"windows_sign_in": True, "windows_sign_in_silent": True, "entra_sign_in": False}
    config = kerberos.load(db)
    config.silent = False
    kerberos.save(db, config, None)
    db.commit()
    assert client.get("/api/auth/options").json() == {"windows_sign_in": True, "windows_sign_in_silent": False, "entra_sign_in": False}
    # No keytab on the server: off, whatever the setting says.
    kerberos.keytab_path().unlink()
    assert client.post("/api/auth/sso").status_code == 404
    assert client.get("/api/auth/options").json() == {"windows_sign_in": False, "windows_sign_in_silent": False, "entra_sign_in": False}


def test_signs_in_a_directory_user_to_the_portal(client, db, mapped, service_desk, slas):
    r = sso(client)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "ralvarez@corp.local" and r.json()["portal_only"] is True
    # Mutual authentication: the KDC's reply goes back to the browser.
    assert r.headers["www-authenticate"] == "Negotiate " + base64.b64encode(b"mutual").decode()
    assert client.get("/api/auth/me").json()["portal_only"] is True
    r = client.post("/api/portal/tickets", json={"subject": "Printer jam", "description": "x", **ANSWERS}, headers=CSRF)
    assert r.status_code == 201, r.text
    assert client.get("/api/portal/tickets").json()["total"] == 1


def test_agents_get_the_portal_only(client, db, admin, mapped, service_desk, slas):
    # Someone else's ticket, with an attachment the agent could see from the console.
    other = TestClient(app)
    ticket = other.post("/api/tickets", json={"subject": "Switch down"}, headers=as_user(admin)).json()
    r = other.post(f"/api/tickets/{ticket['id']}/attachments", files={"file": ("cfg.txt", b"x", "text/plain")}, headers=as_user(admin))
    assert r.status_code == 201, r.text

    r = sso(client, "jpark@CORP.LOCAL")
    assert r.status_code == 200, r.text
    assert (r.json()["role"], r.json()["portal_only"]) == ("agent", True)
    assert client.get("/api/portal/tickets").status_code == 200
    for path in ("/api/tickets", "/api/queues", f"/api/tickets/{ticket['id']}", "/api/users"):
        r = client.get(path)
        assert r.status_code == 403, path
        assert "password" in r.json()["detail"]
    # Through the paths a portal session can reach, the agent is just a requester.
    assert client.get(f"/api/tickets/{ticket['id']}/attachments").status_code == 404

    # Signing in with the AD password gives the full console.
    r = client.post("/api/auth/login", json={"email": "jpark", "password": PW})
    assert r.status_code == 200 and r.json()["portal_only"] is False
    assert client.get("/api/tickets").status_code == 200
    assert client.get(f"/api/tickets/{ticket['id']}/attachments").status_code == 200


def test_email_style_windows_sign_in(client, db, ad, org, mapped):
    # Signed in to Windows as ralvarez@corp.local (the UPN), the ticket names the UPN.
    r = sso(client, "ralvarez\\@corp.local@CORP.LOCAL")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "ralvarez@corp.local"
    # A UPN that merely looks like someone's alias isn't enough.
    assert sso(TestClient(app), "frontdesk\\@corp.local@CORP.LOCAL").status_code == 401
    ad.down = True
    assert sso(TestClient(app), "RAlvarez\\@corp.local@CORP.LOCAL").status_code == 200


def test_new_starter_is_synced_at_sign_in(client, db, ad, org):
    # Never synced yet: found in AD and created on the spot.
    assert user(db, "mosei@corp.local") is None
    r = sso(client, "MOSEI@CORP.LOCAL")
    assert r.status_code == 200, r.text
    assert user(db, "mosei@corp.local").sam_account_name == "mosei"


@pytest.mark.parametrize(
    "principal",
    [
        "ralvarez@OTHER.LOCAL",  # another realm
        "nobody@CORP.LOCAL",  # not in AD
        "HTTP/helpdesk.corp.local@CORP.LOCAL",  # a service, not a person
        "PC01$@CORP.LOCAL",  # a computer account
    ],
)
def test_refused_principals(client, db, mapped, principal):
    r = sso(client, principal)
    assert r.status_code == 401
    # No Negotiate challenge: the browser stops and the portal shows the sign-in form.
    assert "www-authenticate" not in r.headers
    assert auth.COOKIE not in client.cookies


def test_disabled_in_ad_is_refused(client, db, ad, org, mapped):
    org["user"].enabled = False
    assert sso(client).status_code == 401


def test_bad_or_malformed_tokens_are_refused(client):
    r = client.post("/api/auth/sso", headers={"Authorization": "Negotiate " + base64.b64encode(b"bad").decode()})
    assert r.status_code == 401 and "www-authenticate" not in r.headers
    r = client.post("/api/auth/sso", headers={"Authorization": "Negotiate !!not-base64!!"})
    assert r.status_code == 401 and "www-authenticate" not in r.headers


def test_directory_down_uses_the_synced_record(client, db, ad, org, mapped):
    ad.down = True
    assert sso(client).status_code == 200
    # Someone who was never synced can't be checked, so isn't let in.
    ad.add_user("newhire@corp.local", "New Hire", sam="newhire")
    assert sso(TestClient(app), "newhire@CORP.LOCAL").status_code == 401


def test_portal_only_session_can_use_the_whole_portal(client, db, mapped, service_desk, slas):
    sso(client)
    r = client.post("/api/portal/tickets", json={"subject": "Printer jam", "description": "x", **ANSWERS}, headers=CSRF)
    tid = r.json()["id"]
    assert client.post(f"/api/tickets/{tid}/comments", json={"body": "Any news?"}, headers=CSRF).status_code == 201
    assert [c["body"] for c in client.get(f"/api/tickets/{tid}/comments").json()] == ["Any news?"]
    assert client.get(f"/api/tickets/{tid}/attachments").status_code == 200
    assert client.get("/api/notices/current").status_code == 200
    assert client.get("/api/kb/articles").status_code == 200
    # Still not the agent API or password/2FA changes.
    assert client.get(f"/api/tickets/{tid}").status_code == 403
    assert client.post("/api/auth/password", json={"current_password": "x", "new_password": "new-password-123"}, headers=CSRF).status_code == 403
