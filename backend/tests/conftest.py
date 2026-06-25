"""pytest fixtures — isolated data directory per test (absorbed from V26.0 pattern)."""

import pytest
import tempfile
import os

from app.core.config import Settings
from app.dependencies import clear_services_cache, get_services


@pytest.fixture(autouse=True)
def isolated_data():
    """Isolate test data to a temporary directory per test.

    Pattern absorbed from V26.0 tests/conftest.py:
    tmp_path + monkeypatch + cache_clear for test isolation.
    """
    import shutil
    tmp = tempfile.mkdtemp(prefix="rebuild-test-")
    settings = Settings(data_dir=tmp, debug=True)
    clear_services_cache()
    svc = get_services(settings)
    yield svc
    clear_services_cache()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def client(isolated_data):
    """FastAPI TestClient with isolated services."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
