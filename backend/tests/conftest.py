# SPDX-License-Identifier: AGPL-3.0-or-later
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from app import configstore
from app.config import settings
from app.db import get_session, get_session_factory
from app.main import app
from app.models import Base, Priority, Queue, SlaPolicy, User, UserRole

TEST_URL = os.environ.get(
    "HELPDESK_TEST_DATABASE_URL", "postgresql+psycopg://helpdesk:helpdesk@localhost:5432/helpdesk_test"
)
BACKEND = Path(__file__).resolve().parents[1]
RULES_PASSWORD = "helpdesk_rules"
RULES_URL = make_url(TEST_URL).set(username="helpdesk_rules", password=RULES_PASSWORD).render_as_string(hide_password=False)


def alembic_config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    # Build the schema through the real migrations, not create_all.
    command.upgrade(alembic_config(), "head")
    with eng.begin() as conn:
        # The installer's job in production; the dev database user can do it here.
        conn.execute(text(f"ALTER ROLE helpdesk_rules LOGIN PASSWORD '{RULES_PASSWORD}'"))
        # Every test starts empty, the first one too (migrations seed some starting data).
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    # Never pick up a developer's real mailbox from .env; files go to a temp dir.
    from app.api import auth as auth_api

    auth_api._failures.clear()  # every test signs in from the same address
    monkeypatch.setattr(settings, "mailbox", "")
    # SQL features are opt-in (Settings > General); most tests exercise them.
    monkeypatch.setattr(settings, "sql_enabled", True)
    monkeypatch.setattr(settings, "portal_url", "")
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(settings, "secrets_dir", tmp_path / "secrets")
    # Most tests identify users with X-Dev-User; test_auth.py covers real sign-in.
    monkeypatch.setattr(settings, "dev_auth", True)
    # The test client talks plain http.
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "rules_database_url", RULES_URL)
    # SLA times in wall-clock time, so tests don't depend on the day they run; test_sla_hours.py
    # covers the desk-hours clock.
    monkeypatch.setattr(settings, "sla_clock", "always")
    yield
    # Stored settings a test saved were laid over settings; take them off again.
    configstore.reset()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(engine, session_factory):
    factory = session_factory

    def override():
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_session_factory] = lambda: factory
    with factory() as session:
        yield session
    app.dependency_overrides.clear()
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(db):
    return TestClient(app)


# Portal impact/urgency answers that give medium priority, the pre-matrix default.
ANSWERS = {"impact": "many", "urgency": "medium"}


def as_user(user: User) -> dict:
    return {"X-Dev-User": user.email}


def make_user(db: Session, email: str, role: UserRole = UserRole.requester, name: str | None = None) -> User:
    user = User(email=email, display_name=name or email.split("@")[0], role=role)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def admin(db):
    return make_user(db, "admin@corp.local", UserRole.admin)


@pytest.fixture
def agent(db):
    return make_user(db, "jpark@corp.local", UserRole.agent, "J. Park")


@pytest.fixture
def requester(db):
    return make_user(db, "ralvarez@corp.local", name="R. Alvarez")


@pytest.fixture
def service_desk(db):
    queue = Queue(name="Service desk", is_default=True)
    db.add(queue)
    db.commit()
    return queue


@pytest.fixture
def slas(db):
    db.add_all(
        [
            SlaPolicy(priority=Priority.critical, first_response_minutes=15, resolution_minutes=240),
            SlaPolicy(priority=Priority.high, first_response_minutes=60, resolution_minutes=480),
            SlaPolicy(priority=Priority.medium, first_response_minutes=240, resolution_minutes=4320),
            SlaPolicy(priority=Priority.low, first_response_minutes=480, resolution_minutes=7200),
        ]
    )
    db.commit()
