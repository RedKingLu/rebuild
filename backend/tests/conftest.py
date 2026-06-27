"""pytest fixtures — isolated data directory + DB per test (absorbed from V26.0 pattern)."""

import pytest
import tempfile
import os

from app.core.config import Settings
from app.dependencies import clear_services_cache, get_services
from app.core.database import _engine, _SessionLocal


@pytest.fixture(autouse=True)
def isolated_data():
    """Isolate test data and database to a temporary directory per test.

    Pattern absorbed from V26.0 tests/conftest.py:
    tmp_path + monkeypatch + cache_clear for test isolation.
    """
    import shutil
    # Reset database globals so get_session picks up the new URL
    import app.core.database as db_mod
    db_mod._engine = None
    db_mod._SessionLocal = None

    tmp = tempfile.mkdtemp(prefix="rebuild-test-")
    db_url = f"sqlite:///{tmp}/rebuild.db"
    ws_tmp = os.path.join(tmp, "workspace")
    settings = Settings(data_dir=tmp, debug=True, database_url=db_url, workspace_dir=ws_tmp)
    clear_services_cache()
    svc = get_services(settings)
    # workspace_service / trace_writer / audit_writer read the GLOBAL settings
    # singleton directly (not the injected one), so isolate it too — otherwise
    # tests pollute the real 工作区/ tree. object.__setattr__ bypasses frozen.
    import app.core.config as cfg
    _orig_ws = cfg.settings.workspace_dir
    object.__setattr__(cfg.settings, "workspace_dir", ws_tmp)
    yield svc
    object.__setattr__(cfg.settings, "workspace_dir", _orig_ws)
    clear_services_cache()
    # Reset globals again so next test uses a fresh DB
    db_mod._engine = None
    db_mod._SessionLocal = None
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def client(isolated_data):
    """FastAPI TestClient with isolated services."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
