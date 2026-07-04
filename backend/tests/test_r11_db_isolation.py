"""R11-3 B-DB-ISOLATION-1 regression: pytest must NEVER touch the real DB.

Root cause (坐实): conftest.isolated_data redirected only the GLOBAL
settings.workspace_dir, never database_url. database.get_engine() reads the
GLOBAL settings.database_url, so drop_all/create_all ran against the real
.data/rebuild.db and wiped the user's projects on every pytest run.

These tests fail on the pre-fix code and pass once the global database_url /
data_dir are redirected to the per-test temp path (and the drop_all guard is in
place). No fabricated pass: they assert on the LIVE engine binding, not a mock.
"""

from app.core.config import settings
from app.core.database import get_engine


# The real, production default DB path (config.py:21). A test must never bind here.
REAL_DB_URL = "sqlite:////home/king/rebuild/backend/.data/rebuild.db"


def test_settings_database_url_redirected_to_temp():
    """During a test, the GLOBAL settings.database_url must point at a temp DB."""
    assert settings.database_url != REAL_DB_URL, (
        "settings.database_url still points at the real DB — isolation broken, "
        "pytest would drop the user's real data."
    )
    assert "rebuild-test-" in settings.database_url, (
        f"settings.database_url {settings.database_url!r} is not a per-test temp path"
    )


def test_settings_data_dir_redirected_to_temp():
    """data_dir (checkpointer sqlite, etc.) must also be a temp path during tests."""
    assert "rebuild-test-" in settings.data_dir, (
        f"settings.data_dir {settings.data_dir!r} is not a per-test temp path"
    )


def test_engine_bound_to_temp_not_real_db():
    """The LIVE SQLAlchemy engine must be bound to the temp DB, not .data/rebuild.db."""
    engine = get_engine()
    url = str(engine.url)
    assert url != REAL_DB_URL, (
        f"Engine bound to the real DB {url!r} — drop_all/create_all would wipe real data."
    )
    assert "/rebuild/backend/.data/rebuild.db" not in url, (
        f"Engine URL {url!r} resolves into the real .data directory."
    )
    assert url == settings.database_url, (
        "Engine URL and settings.database_url disagree — engine cached a stale binding."
    )


def test_engine_and_global_settings_are_consistent(client):
    """Even with a TestClient built (which triggers app startup), the engine that
    the app uses stays bound to the temp DB — the request path can't reach real data."""
    # Touch a DB-backed endpoint; must not 500 and must not have needed the real DB.
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert str(get_engine().url) == settings.database_url
    assert "rebuild-test-" in str(get_engine().url)
