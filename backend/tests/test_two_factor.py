# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-factor sign-in (authenticator app) and Sign in with Microsoft (Entra ID, mocked)."""
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest

from app import auth, entra, totp
from app.api.auth import TWO_FACTOR_NEEDED
from app.config import settings
from app.models import AuthSource, User, UserRole
from tests.conftest import as_user

PASSWORD = "Correct-horse-battery-9"


def test_rfc6238_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert totp.code_at(secret, 59 // 30) == "287082"  # RFC 6238: 94287082, last six digits
    assert totp.check(secret, "287082", None, now=59) == 1
    assert totp.check(secret, "287082", 1, now=59) is None  # once only


def _local(db, role=UserRole.agent, email="sam@example.com"):
    user = User(email=email, display_name="Sam Lee", role=role, auth_source=AuthSource.local)
    db.add(user)
    db.flush()
    auth.set_password(db, user, PASSWORD)
    db.commit()
    return user


def _login(client, email, **extra):
    return client.post("/api/auth/login", json={"email": email, "password": PASSWORD, **extra}, headers={"X-Helpdesk-CSRF": "1"})


def _h(user):
    # With a real session cookie in the client, unsafe requests need the header a browser sends.
    return {**as_user(user), "X-Helpdesk-CSRF": "1"}


def _turn_on(client, db, user):
    h = _h(user)
    setup = client.post("/api/auth/two-factor/setup", headers=h).json()
    assert setup["uri"].startswith("otpauth://totp/") and f"secret={setup['secret']}" in setup["uri"]
    assert client.post("/api/auth/two-factor/confirm", json={"code": "000000"}, headers=h).status_code == 422
    step = int(time.time() // totp.STEP)
    codes = client.post("/api/auth/two-factor/confirm", json={"code": totp.code_at(setup["secret"], step)}, headers=h).json()["recovery_codes"]
    db.expire_all()
    return setup["secret"], codes, step


def test_sign_in_needs_the_code_once_on_and_codes_work_once(client, db):
    user = _local(db)
    secret, codes, used = _turn_on(client, db, user)
    assert len(codes) == 8 and db.get(User, user.id).totp_enabled_at is not None
    first = _login(client, user.email)
    assert first.status_code == 401 and first.json()["detail"] == TWO_FACTOR_NEEDED
    assert _login(client, user.email, code="123456").status_code == 401
    # The code used to confirm can't be used again; the next step's can.
    assert _login(client, user.email, code=totp.code_at(secret, used)).status_code == 401
    assert _login(client, user.email, code=totp.code_at(secret, used + 1)).status_code == 200
    # A recovery code works once.
    assert _login(client, user.email, code=codes[0]).status_code == 200
    assert _login(client, user.email, code=codes[0]).status_code == 401
    assert client.get("/api/auth/two-factor", headers=as_user(user)).json()["recovery_codes_left"] == 7
    # Turning it off needs the password and a code.
    h = _h(user)
    assert client.post("/api/auth/two-factor/off", json={"password": "wrong", "code": codes[1]}, headers=h).status_code == 422
    assert client.post("/api/auth/two-factor/off", json={"password": PASSWORD, "code": codes[1]}, headers=h).status_code == 204
    assert _login(client, user.email).status_code == 200
    db.expire_all()
    assert db.get(User, user.id).totp_secret is None


def test_secret_is_encrypted_at_rest(client, db):
    user = _local(db)
    secret, _, _ = _turn_on(client, db, user)
    stored = db.get(User, user.id).totp_secret
    assert secret not in stored and totp.unseal(stored) == secret


def test_admins_with_a_local_password_must_set_it_up_when_required(client, db, monkeypatch, admin):
    boss = _local(db, UserRole.admin, "boss@example.com")
    monkeypatch.setattr(settings, "totp_required_admins", True)
    signed_in = _login(client, boss.email)
    assert signed_in.status_code == 200 and signed_in.json()["must_set_up_two_factor"] is True
    assert client.get("/api/tickets", headers={"X-Helpdesk-CSRF": "1"}).json()["detail"] == "Set up two-factor sign-in first"
    _turn_on(client, db, boss)
    assert client.get("/api/tickets", headers={"X-Helpdesk-CSRF": "1"}).status_code == 200
    # Required: can't turn it off; an admin can reset it for a lost phone.
    assert client.post("/api/auth/two-factor/off", json={"password": PASSWORD, "code": "000000"}, headers=_h(boss)).status_code == 403
    client.cookies.clear()
    assert client.delete(f"/api/users/{boss.id}/two-factor", headers=as_user(admin)).status_code == 204
    db.expire_all()
    assert db.get(User, boss.id).totp_enabled_at is None


# Sign in with Microsoft

TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def microsoft(monkeypatch):
    monkeypatch.setattr(settings, "entra_sign_in_enabled", True)
    monkeypatch.setattr(settings, "entra_tenant_id", TENANT)
    monkeypatch.setattr(settings, "entra_client_id", "66666666-7777-8888-9999-000000000000")
    monkeypatch.setattr(settings, "entra_client_secret", "secret")
    monkeypatch.setattr(settings, "portal_url", "https://helpdesk.example.com")

    class FakeApp:
        def initiate_auth_code_flow(self, scopes, redirect_uri, state, prompt):
            return {"auth_uri": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state={state}", "state": state, "code_verifier": "v"}

    @contextmanager
    def fake_app(config):
        yield FakeApp()

    claims = {"tid": TENANT, "oid": "AAAAAAAA-0000-0000-0000-000000000001", "preferred_username": "dana@corp.example"}
    monkeypatch.setattr(entra, "_app", fake_app)
    monkeypatch.setattr(entra, "_exchange", lambda config, flow, params: dict(claims))
    return claims


def _start(client):
    r = client.get("/api/auth/entra/start", follow_redirects=False)
    assert r.status_code == 303
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return state, r.cookies.get(entra.COOKIE)


def test_microsoft_sign_in_matches_an_existing_directory_account(client, db, microsoft):
    dana = User(email="dana@corp.example", upn="dana@corp.example", display_name="Dana Cole", role=UserRole.agent, auth_source=AuthSource.directory)
    db.add(dana)
    db.commit()
    assert client.get("/api/auth/options").json()["entra_sign_in"] is True
    state, browser = _start(client)
    client.cookies.set(entra.COOKIE, browser, path="/api/auth/entra")
    done = client.get(f"/api/auth/entra/callback?code=x&state={state}", follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/"
    assert client.get("/api/auth/me").json()["email"] == "dana@corp.example"
    db.expire_all()
    assert db.get(User, dana.id).entra_id == "aaaaaaaa-0000-0000-0000-000000000001"
    # The same state can't be used twice.
    again = client.get(f"/api/auth/entra/callback?code=x&state={state}", follow_redirects=False)
    assert "expired" in again.headers["location"]


def test_microsoft_sign_in_refusals(client, db, microsoft):
    _local(db, UserRole.admin, "dana@corp.example")  # a break-glass local account with that name
    state, browser = _start(client)
    client.cookies.set(entra.COOKIE, browser, path="/api/auth/entra")
    assert "signs%20in%20with%20its%20password" in client.get(f"/api/auth/entra/callback?code=x&state={state}", follow_redirects=False).headers["location"]
    # Another browser (no binding cookie) can't finish someone's sign-in.
    state, _ = _start(client)
    client.cookies.clear()
    assert "expired" in client.get(f"/api/auth/entra/callback?code=x&state={state}", follow_redirects=False).headers["location"]
    # Another tenant's account.
    microsoft["tid"] = "99999999-9999-9999-9999-999999999999"
    state, browser = _start(client)
    client.cookies.set(entra.COOKIE, browser, path="/api/auth/entra")
    assert "organization" in client.get(f"/api/auth/entra/callback?code=x&state={state}", follow_redirects=False).headers["location"]


def test_wrong_codes_add_up_to_a_lockout_even_with_the_right_password(client, db):
    user = _local(db)
    secret, _, used = _turn_on(client, db, user)
    client.cookies.clear()
    for _ in range(auth.MAX_FAILED_LOGINS):
        assert _login(client, user.email, code="000000").status_code == 401
    db.expire_all()
    assert db.get(User, user.id).locked_until is not None
    # Locked: even the right code doesn't get in until the lockout ends.
    assert _login(client, user.email, code=totp.code_at(secret, used + 1)).status_code == 401


def test_wrong_codes_on_two_factor_off_count_toward_lockout(client, db):
    user = _local(db)
    _turn_on(client, db, user)
    h = _h(user)
    for _ in range(auth.MAX_FAILED_LOGINS):
        assert client.post("/api/auth/two-factor/off", json={"password": PASSWORD, "code": "000000"}, headers=h).status_code == 422
    db.expire_all()
    assert db.get(User, user.id).locked_until is not None


def test_recovery_codes_are_long(client, db):
    _, codes, _ = _turn_on(client, db, _local(db))
    assert all(len(c.replace("-", "")) == 16 for c in codes)


def test_microsoft_sign_in_never_matches_by_the_email_claim(client, db, microsoft):
    db.add(User(email="boss@corp.example", display_name="Boss", role=UserRole.admin, auth_source=AuthSource.directory, upn="boss@corp.example"))
    db.commit()
    microsoft.update(preferred_username="intern@corp.example", email="boss@corp.example")
    state, browser = _start(client)
    client.cookies.set(entra.COOKIE, browser, path="/api/auth/entra")
    assert "no%20account" in client.get(f"/api/auth/entra/callback?code=x&state={state}", follow_redirects=False).headers["location"]
