"""Database engine and session — SQLAlchemy 2.0 + SQLite dev / PostgreSQL path."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        db_url = settings.database_url
        # Ensure directory exists for SQLite
        if db_url.startswith("sqlite:///"):
            import os
            path = db_url.replace("sqlite:///", "")
            os.makedirs(os.path.dirname(path), exist_ok=True)
        _engine = create_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
        )
        # Dev convenience: ensure all tables exist on first engine use (idempotent).
        # Importing the models package registers every model on Base.metadata, so this
        # self-heals even if the DB file was deleted and lifespan/init_db never ran
        # (e.g. TestClient without a context manager). Production still uses Alembic.
        from app.models import Base  # noqa: F401 — imports all model classes
        Base.metadata.create_all(bind=_engine)
    return _engine


def get_session() -> Session:
    """Session factory — returns a new Session.

    For DIRECT/internal callers (services, startup seed) that manage their own
    lifecycle with an explicit `try/finally: db.close()`.
    FastAPI route dependencies must use `get_db` (generator) below instead, so the
    connection is reliably returned to the pool after each request.
    """
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal()


def get_db():
    """FastAPI dependency — yields a Session and ALWAYS closes it.

    FastAPI only auto-closes *generator* dependencies. A plain function that
    `return`s a Session leaks the underlying connection (the pool — size 5 +
    overflow 10 — is exhausted after ~15 requests, then every DB request hangs
    30s and 500s). Use this for every `Depends(...)` on a DB session.
    """
    db = get_session()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables (dev convenience — production uses Alembic migrations)."""
    from app.models.base import Base
    Base.metadata.create_all(bind=get_engine())
