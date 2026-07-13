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
        _migrate_add_columns_on_engine(_engine)
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
    import app.models  # noqa: F401 — ensure all model tables are registered with Base.metadata
    from app.models.base import Base
    Base.metadata.create_all(bind=get_engine())
    _migrate_add_columns_on_engine(get_engine())


def _migrate_add_columns_on_engine(engine) -> None:
    """Additive SQLite column migrations for columns added after initial table creation.

    SQLite does not support IF NOT EXISTS on ALTER TABLE (< 3.37), so we check
    the pragma first. Idempotent — safe to run on every startup.
    """
    import sqlalchemy as _sa
    db_url = str(engine.url)
    if not db_url.startswith("sqlite"):
        return  # PostgreSQL uses proper Alembic migrations
    with engine.connect() as conn:
        def _add_if_missing(table: str, col: str, col_def: str):
            res = conn.execute(_sa.text(f"PRAGMA table_info({table})"))
            existing = [row[1] for row in res.fetchall()]
            if col not in existing:
                conn.execute(_sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))
                conn.commit()

        # WP-6: checkpoint_ref / interrupt_ref added to p_gate (D-037 checkpoint link)
        _add_if_missing("p_gate", "checkpoint_ref", "VARCHAR(64)")
        _add_if_missing("p_gate", "interrupt_ref", "VARCHAR(64)")

        # R13-4: call_log 3 fusion columns (additive; Alembic is authoritative on PG)
        _add_if_missing("call_log", "fusion_parent_id", "VARCHAR(36)")
        _add_if_missing("call_log", "call_type", "VARCHAR(32)")
        _add_if_missing("call_log", "fusion_run_id", "VARCHAR(36)")

    # R13-4: call_log fusion indexes (additive). SQLite has no IF NOT EXISTS on
    # CREATE INDEX, so attempt/ignore. Idempotent across restarts.
    if str(engine.url).startswith("sqlite"):
        with engine.connect() as conn:
            for _idx_stmt in (
                "CREATE INDEX IF NOT EXISTS ix_call_log_fusion_parent_id ON call_log (fusion_parent_id)",
                "CREATE INDEX IF NOT EXISTS ix_call_log_fusion_run_id ON call_log (fusion_run_id)",
                "CREATE INDEX IF NOT EXISTS ix_call_log_call_type ON call_log (call_type)",
            ):
                try:
                    conn.execute(_sa.text(_idx_stmt))
                    conn.commit()
                except Exception:
                    # 发声：索引用 IF NOT EXISTS，此处异常代表真实 DB 错误（如表缺失/锁），
                    # 而非"已存在"；须可见以便定位。索引为附加性能项，故不抛出中断启动。
                    import logging
                    logging.getLogger("rebuild.database").warning(
                        "call_log fusion 索引创建失败（非致命，索引为附加性能项）: %s",
                        _idx_stmt, exc_info=True)


def check_migration_drift(engine=None):
    """Read-only: compare the SQLite DB's applied Alembic revision to the migration head.

    Returns ``(in_sync: bool, db_revision: str | None, head_revision: str | None)``.

    - SQLite only. On PostgreSQL, Alembic owns the migration flow authoritatively
      (see ``_migrate_add_columns_on_engine`` which returns early for PG), so this
      returns ``(True, None, None)`` and does not probe.
    - NEVER mutates the DB and NEVER runs ``create_all`` — this is the honest drift
      detector that ``create_all`` was masking (R16: ``resource_entry.package_url``).
    - ``db_revision`` is ``None`` when the DB has no ``alembic_version`` table (a
      ``create_all``-provisioned dev/test DB that Alembic never stamped).
    - ``head_revision`` is read dynamically from the migration chain — no revision
      string is hardcoded here.
    """
    if engine is None:
        engine = get_engine()
    if not str(engine.url).startswith("sqlite"):
        return True, None, None  # PostgreSQL: Alembic is authoritative
    import os
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    head_revision = ",".join(sorted(heads)) if heads else None
    with engine.connect() as conn:
        db_revision = MigrationContext.configure(conn).get_current_revision()
    in_sync = db_revision is not None and db_revision in set(heads)
    return in_sync, db_revision, head_revision


def verify_migration_head_on_startup(engine=None) -> None:
    """Startup self-check (R17.2 WP-A): surface real-DB migration drift LOUDLY (公理3),
    never mask it.

    Root cause addressed: ``init_db()`` / ``get_engine()`` call ``create_all``, which
    only creates MISSING tables — it never adds columns to EXISTING tables. A column
    added by an Alembic migration but not applied to a persistent SQLite DB is therefore
    silently absent until a query crashes with ``no such column`` (R16:
    ``resource_entry.package_url``). Nothing verified, at startup, that the real DB was
    at the migration head — this is that check.

    Non-fatal by design: ``create_all`` has already run for dev self-heal, and hard-
    crashing would also block brand-new / test DBs. But drift is NEVER silent — genuine
    drift is logged at ERROR with the exact remediation (``alembic upgrade head``). This
    is a detection/alarm only: it does NOT auto-migrate and does NOT ``create_all`` to
    paper over the gap.
    """
    import logging
    log = logging.getLogger("uvicorn")
    try:
        in_sync, db_rev, head_rev = check_migration_drift(engine)
    except Exception:
        log.warning("alembic head 启动自检执行失败（非致命）", exc_info=True)
        return
    if in_sync:
        return
    if db_rev is None:
        # Not an Alembic-managed DB (create_all-provisioned dev/test). Informational only.
        log.info(
            "数据库未被 alembic 管理（create_all provisioned，开发/测试场景）；"
            "持久化真实库应经 `alembic upgrade head` 建立版本记录。迁移链 head=%s",
            head_rev,
        )
        return
    # Genuine drift: an Alembic-managed DB sitting behind the migration head.
    log.error(
        "真实库落后于迁移 head：DB 当前 revision=%s，迁移链 head=%s。"
        "create_all 只建缺表不补既有表缺列，缺失列将在查询时崩溃（no such column）。"
        "请在 backend 目录执行 `alembic upgrade head` 后重启服务。"
        "（本自检不自动迁移、不静默、不以 create_all 掩盖）",
        db_rev,
        head_rev,
    )

