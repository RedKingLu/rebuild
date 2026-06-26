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
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal()


def init_db():
    """Create all tables (dev convenience — production uses Alembic migrations)."""
    from app.models.base import Base
    Base.metadata.create_all(bind=get_engine())
