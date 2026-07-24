"""R17.2 WP-A: real-DB Alembic migration head self-check.

These tests do NOT use Base.metadata.create_all to build the schema. They drive the
alembic_version state with real Alembic commands (stamp / upgrade) on a temp SQLite
file and assert that ``check_migration_drift()`` honestly detects when a persistent DB
is behind the migration head — the exact failure mode create_all masked in R16
(``resource_entry.package_url`` -> ``no such column``).

Guards against the recurring "tests green (create_all) != real DB (alembic)" mode.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

BACKEND = Path(__file__).resolve().parents[1]

# Reference revisions. _HEAD is ALSO asserted dynamically (test_head_is_read_dynamically)
# so this file does not become a second source of truth for the head value.
_HEAD = "b1c2d3e4f5a6"        # migration chain head (R17.5-P4-FIX 批4 D-111: call_log 内容/归因列)
_PREV_HEAD = "a1c2e3f40901"   # down_revision of head (R17.5-P4-FIX 批3 D-109) — used to manufacture drift


def _alembic_cfg() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


@pytest.fixture
def temp_migrated_db(tmp_path):
    """Point the global settings (and thus alembic env.py) at a fresh temp SQLite file.

    ``alembic/env.py`` forces ``sqlalchemy.url`` from ``settings.database_url``, so the
    only way to run alembic commands against the temp file is to override the global
    settings singleton for the duration of the commands. Restored on teardown (the
    autouse ``isolated_data`` fixture also restores — this is belt-and-braces).
    """
    import app.core.config as cfg_mod
    import app.core.database as db_mod

    db_file = tmp_path / "migrated" / "rebuild.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"

    _orig_db = cfg_mod.settings.database_url
    object.__setattr__(cfg_mod.settings, "database_url", db_url)
    db_mod._engine = None
    db_mod._SessionLocal = None
    try:
        yield db_url
    finally:
        object.__setattr__(cfg_mod.settings, "database_url", _orig_db)
        db_mod._engine = None
        db_mod._SessionLocal = None


def test_head_is_read_dynamically_from_chain(temp_migrated_db):
    """head_revision is read from the migration chain, not hardcoded in the checker."""
    from app.core.database import check_migration_drift

    eng = create_engine(temp_migrated_db)
    _, _, head_rev = check_migration_drift(eng)
    assert head_rev == _HEAD, head_rev


def test_detects_drift_when_db_behind_head(temp_migrated_db):
    """Stamp the temp DB at the PREVIOUS head (a real DB stuck at an old revision =
    the R16 drift scenario). The check must report drift, NOT in_sync."""
    from app.core.database import check_migration_drift

    command.stamp(_alembic_cfg(), _PREV_HEAD)  # genuine alembic stamp -> alembic_version = prev head
    eng = create_engine(temp_migrated_db)
    in_sync, db_rev, head_rev = check_migration_drift(eng)
    assert in_sync is False, "drift must be detected when DB is behind head"
    assert db_rev == _PREV_HEAD, db_rev
    assert head_rev == _HEAD, head_rev


def test_in_sync_after_real_alembic_upgrade_head(temp_migrated_db):
    """A genuinely alembic-migrated DB at head passes the check — no create_all used."""
    from app.core.database import check_migration_drift

    cfg = _alembic_cfg()
    command.stamp(cfg, _PREV_HEAD)
    command.upgrade(cfg, "head")  # real migration up to head
    eng = create_engine(temp_migrated_db)
    in_sync, db_rev, head_rev = check_migration_drift(eng)
    assert in_sync is True, (db_rev, head_rev)
    assert db_rev == _HEAD, db_rev


def test_unmanaged_db_reports_none_revision(temp_migrated_db):
    """A DB with no alembic_version table (create_all-provisioned) is reported as
    db_revision=None and NOT in_sync — the honest 'not alembic-managed' signal that
    the startup check downgrades to INFO rather than a false drift ERROR."""
    from app.core.database import check_migration_drift

    eng = create_engine(temp_migrated_db)  # empty file: no alembic_version table
    in_sync, db_rev, head_rev = check_migration_drift(eng)
    assert in_sync is False
    assert db_rev is None
    assert head_rev == _HEAD, head_rev
