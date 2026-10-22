from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session_factory() -> sessionmaker:
    """For work that opens its own sessions (one per message); tests swap the database."""
    return SessionLocal


def get_session() -> Iterator[Session]:
    from app import configstore

    with SessionLocal() as session:
        # Pick up settings changed in another process (throttled).
        configstore.refresh(session)
        yield session
