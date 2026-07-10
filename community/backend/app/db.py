"""Community service DB — independent SQLite, NOT shared with the main platform.

R15-4-C3. The community service is fully decoupled from the platform DB
(D-089 / Q-R15-10 / red line #13: 社区服务不得反向控制部署实例).
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session


# Independent data dir (container volume in compose); default local .data
_DATA_DIR = Path(os.environ.get("COMMUNITY_DATA_DIR", str(Path(__file__).resolve().parent.parent / ".data")))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Packages (zip) live here; the /download endpoint serves them.
PACKAGE_DIR = _DATA_DIR / "packages"
PACKAGE_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("COMMUNITY_DATABASE_URL", f"sqlite:///{_DATA_DIR / 'community.db'}")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables (community service is standalone; create_all is fine here)."""
    from app import models  # noqa: F401  (register models)
    Base.metadata.create_all(bind=engine)
