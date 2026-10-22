from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app import auth
from app.config import settings
from app.models import AuthSource, User, UserRole, UserSession
from tests.conftest import as_user, make_user

PASSWORD = "correct horse battery"
CSRF = {auth.CSRF_HEADER: "1"}


@pytest.fixture(autouse=True)
def no_dev_header(monkeypatch):
    # Real sign-in only in this module.
    monkeypatch.setattr(settings, "dev_auth", False)


@pytest.fixture
def local_agent(db):
    user = make_user(db, "jpark@corp.local", UserRole.agent, "J. Park")
    auth.set_password(db, user, PASSWORD)
    db.commit()
    return user


def login(client, email="jpark@corp.local", password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_sets_httponly_cookie_and_me_works(client, local_agent):
    r = login(client, email="JPark@corp.local")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "jpark@corp.local"
    assert "password_hash" not in r.json()
    assert r.json()["has_password"] is True
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/auth/me").json()["id"] == local_agent.id


def test_cookie_is_secure_by_default(client, local_agent, monkeypatch):
    monkeypatch.setattr(settings, "cookie_secure", True)
    assert "Secure" in login(client).headers["set-cookie"]


def test_only_a_hash_of_the_token_is_stored(client, db, local_agent):
    login(client)
    token = client.cookies[auth.COOKIE]
    row = db.scalar(select(UserSession))
    assert row.token_hash != token and len(row.token_hash) == 64


def test_wrong_password_and_unknown_email_look_the_same(client, local_agent):
    a = login(client, password="wrong password!!")
    b = login(client, email="nobody@corp.local")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "Wrong email or password"}


def test_lockout_after_repeated_failures(client, db, local_agent):
    for _ in range(auth.MAX_FAILED_LOGINS):
        login(client, password="wrong password!!")
    r = login(client)
    assert r.status_code == 401 and "Too many" in r.json()["detail"]
    db.execute(update(User).values(locked_until=auth.sla.utcnow() - timedelta(seconds=1)))
    db.commit()
    assert login(client).status_code == 200


def test_csrf_header_required_for_cookie_writes(client, local_agent, service_desk):
    login(client)
    assert client.post("/api/tickets", json={"subject": "x"}).status_code == 403
    assert client.post("/api/tickets", json={"subject": "x"}, headers=CSRF).status_code == 201
    assert client.get("/api/tickets").status_code == 200


def test_logout_ends_session(client, local_agent):
    login(client)
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected(client, db, local_agent):
    login(client)
    db.execute(update(UserSession).values(expires_at=auth.sla.utcnow() - timedelta(seconds=1)))
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_deactivating_user_ends_their_sessions(client, db, local_agent):
    admin = make_user(db, "admin@corp.local", UserRole.admin)
    auth.set_password(db, admin, PASSWORD)
    db.commit()
    login(client)
    from fastapi.testclient import TestClient
    from app.main import app

    admin_client = TestClient(app)
    login(admin_client, email="admin@corp.local")
    r = admin_client.patch(f"/api/users/{local_agent.id}", json={"is_active": False}, headers=CSRF)
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert login(client).status_code == 401


def test_change_password(client, local_agent):
    login(client)
    bad = client.post("/api/auth/password", json={"current_password": "nope", "new_password": "a" * 20}, headers=CSRF)
    assert bad.status_code == 422
    short = client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"}, headers=CSRF)
    assert short.status_code == 422
    ok = client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "a much better passphrase"}, headers=CSRF)
    assert ok.status_code == 204
    # This browser stays signed in; the old password stops working.
    assert client.get("/api/auth/me").status_code == 200
    client.cookies.clear()
    assert login(client).status_code == 401
    assert login(client, password="a much better passphrase").status_code == 200


def test_admin_creates_and_resets_local_users(client, db, monkeypatch):
    monkeypatch.setattr(settings, "dev_auth", True)
    admin = make_user(db, "admin@corp.local", UserRole.admin)
    r = client.post(
        "/api/users",
        json={"email": "new@corp.local", "display_name": "New", "role": "agent", "password": "short"},
        headers=as_user(admin),
    )
    assert r.status_code == 422
    r = client.post(
        "/api/users",
        json={"email": "new@corp.local", "display_name": "New", "role": "agent", "password": PASSWORD},
        headers=as_user(admin),
    )
    assert r.status_code == 201 and r.json()["has_password"] is True
    uid = r.json()["id"]
    monkeypatch.setattr(settings, "dev_auth", False)
    assert login(client, email="new@corp.local").status_code == 200

    monkeypatch.setattr(settings, "dev_auth", True)
    client.cookies.clear()  # a session cookie takes priority over the dev header
    assert client.put(f"/api/users/{uid}/password", json={"password": "another long one!"}, headers={**as_user(admin), **CSRF}).status_code == 204
    # Reset signs them out.
    client.cookies.clear()
    monkeypatch.setattr(settings, "dev_auth", False)
    assert login(client, email="new@corp.local").status_code == 401


def test_users_without_password_or_from_directory_cannot_sign_in_locally(client, db):
    make_user(db, "byemail@corp.local")  # created by email intake: no password
    directory = make_user(db, "ad@corp.local", UserRole.agent)
    directory.auth_source = AuthSource.directory
    directory.password_hash = auth._hasher.hash(PASSWORD)
    db.commit()
    assert login(client, email="byemail@corp.local").status_code == 401
    # A directory account never signs in with a local hash, even a leftover one.
    r = login(client, email="ad@corp.local")
    assert r.status_code == 401 and r.json()["detail"] == "Wrong email or password"
    with pytest.raises(auth.AuthError):
        auth.set_password(db, directory, PASSWORD)


def test_dev_header_is_ignored_unless_enabled(client, local_agent):
    assert client.get("/api/auth/me", headers=as_user(local_agent)).status_code == 401


def test_first_run_admin_must_change_the_password(client, db):
    # Fresh install: no admin yet, so admin/admin gets in...
    r = client.post("/api/auth/login", json={"email": "admin", "password": "admin"})
    assert r.status_code == 200, r.text
    assert (r.json()["role"], r.json()["must_change_password"]) == ("admin", True)
    # ...but can't do anything until the password is changed.
    r = client.get("/api/settings/general")
    assert r.status_code == 403 and r.json()["detail"] == "Change your password first"
    assert client.get("/api/auth/me").json()["must_change_password"] is True
    r = client.post("/api/auth/password", json={"current_password": "admin", "new_password": "a-much-better-one"}, headers=CSRF)
    assert r.status_code == 204, r.text
    assert client.get("/api/settings/general").status_code == 200
    # From now on "admin" is just a username with that password.
    client.post("/api/auth/logout", headers=CSRF)
    assert client.post("/api/auth/login", json={"email": "admin", "password": "admin"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "admin", "password": "a-much-better-one"}).status_code == 200


def test_no_first_run_login_once_an_admin_exists(client, db):
    admin = make_user(db, "boss@corp.local", UserRole.admin)
    auth.set_password(db, admin, PASSWORD)
    db.commit()
    assert client.post("/api/auth/login", json={"email": "admin", "password": "admin"}).status_code == 401
    assert db.scalar(select(User).where(User.email == auth.FIRST_RUN_EMAIL)) is None


def test_first_run_admin_can_sign_in_again_before_changing(client, db):
    client.post("/api/auth/login", json={"email": "admin", "password": "admin"})
    client.post("/api/auth/logout", headers=CSRF)
    r = client.post("/api/auth/login", json={"email": "ADMIN", "password": "admin"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
