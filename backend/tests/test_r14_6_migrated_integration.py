"""R14-6 migration-based integration tests.

The rest of the suite builds the schema with Base.metadata.create_all (via
conftest). That is exactly what masked the R14-4 defects in R14-5 acceptance
(revision-id collision, remote_host missing columns, full unique constraint
blocking multi-binding). These tests instead build the DB purely from the
alembic migration chain (`alembic upgrade head`) and prove, BEFORE any
create_all touches the file, that:

  1. the R14-4 migration applies cleanly on top of the real single head;
  2. remote_host gains environment_tags/driver FROM THE MIGRATION;
  3. the default-binding PARTIAL unique index (WHERE is_default = 1) exists;
  4. bind / multi-binding / detect endpoints work on a migrated schema;
  5. P5 remote dispatch records an invocation with real audit_ref / trace_ref.

Guards against the recurring "tests green != real DB" failure mode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Pre-R14 remote_host schema (exactly the columns create_all produced before R14,
# i.e. WITHOUT environment_tags / driver) — used to faithfully reproduce the P0-2
# broken real-DB state: DB stamped at the real head f7a8b9c0d1e2 with a
# remote_host that lacks the new columns. The R14-4 migration must then ADD them.
_PRE_R14_REMOTE_HOST_DDL = """
CREATE TABLE remote_host (
    remote_host_id VARCHAR(36) NOT NULL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    host_type VARCHAR(32) NOT NULL,
    address VARCHAR(255) NOT NULL,
    masked_address VARCHAR(255),
    port INTEGER,
    os_name VARCHAR(128),
    credential_ref VARCHAR(255),
    status VARCHAR(32) NOT NULL,
    last_connected_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    enabled BOOLEAN,
    host_key_fingerprint VARCHAR(255)
)
"""

_REAL_HEAD = "f7a8b9c0d1e2"  # R13-4, the single head R14-4 descends from


def _migrate_fresh_db(tmp_path):
    """Reproduce the P0-2 real-DB state and apply ONLY the R14-4 migration.

    Steps: fresh temp DB stamped at the real head f7a8b9c0d1e2 with a pre-R14
    remote_host (missing environment_tags/driver), then `alembic upgrade head`
    runs the single R14-4 migration on top. This proves the migration (not
    create_all) adds the columns + creates the tables + partial index, exactly
    the scenario R14-5 flagged.

    (A full from-base `alembic upgrade` is not used because some pre-existing
    early migrations in this repo use non-batch ALTER that SQLite rejects — that
    is an unrelated, pre-existing condition outside R14 scope.)
    """
    import app.core.config as cfg
    from app.dependencies import clear_services_cache
    import app.core.database as db_mod
    from alembic.config import Config
    from alembic import command

    db_file = tmp_path / "migrated" / "rebuild.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    ws_dir = tmp_path / "migrated_ws"
    ws_dir.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"

    object.__setattr__(cfg.settings, "database_url", db_url)
    object.__setattr__(cfg.settings, "workspace_dir", str(ws_dir))
    object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "migrated"))
    db_mod._engine = None
    db_mod._SessionLocal = None
    clear_services_cache()

    # Seed the pre-R14 state directly on the file.
    c = sqlite3.connect(db_file)
    c.execute(_PRE_R14_REMOTE_HOST_DDL)
    c.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    c.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (_REAL_HEAD,))
    c.commit()
    c.close()

    alembic_cfg = Config(str(BACKEND / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    # env.py overrides sqlalchemy.url from cfg.settings.database_url (already set).
    command.upgrade(alembic_cfg, "head")
    return db_url, db_file


class TestMigratedSchema:
    def test_migration_produces_remote_binding_schema(self, tmp_path):
        _, db_file = _migrate_fresh_db(tmp_path)
        # Inspect the raw file BEFORE any ORM/create_all touches it.
        c = sqlite3.connect(db_file)
        try:
            rh_cols = [r[1] for r in c.execute("PRAGMA table_info(remote_host)").fetchall()]
            assert "environment_tags" in rh_cols, rh_cols
            assert "driver" in rh_cols, rh_cols
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            assert "workspace_environment_binding" in tables
            assert "remote_invocation" in tables
            idx = c.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='uq_binding_workspace_default'").fetchone()
            assert idx is not None, "partial unique index missing"
            assert "is_default = 1" in idx[0], idx[0]
        finally:
            c.close()

    def test_alembic_current_is_head(self, tmp_path):
        db_url, db_file = _migrate_fresh_db(tmp_path)
        c = sqlite3.connect(db_file)
        try:
            ver = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            # R15-4 C1/C2 c4f1a9d7e2b8, C8 7c407d04e07b, C10 39528fb4d798;
            # R16-B E3 e4f5a6b7c8d9; R17.5 WP-6 f8a1b2c3d4e5 (project.migration_target);
            # R17.5-P4-FIX 批3 a1c2e3f40901 (project.tech_selection, D-109);
            # R17.5-P4-FIX 批4 b1c2d3e4f5a6 (call_log 内容/归因列, D-111). Head must be latest.
            assert ver == "b1c2d3e4f5a6", ver
        finally:
            c.close()


# Pre-R15 resource_entry schema: exactly the columns create_all produced before
# R15-4, i.e. WITHOUT the 6 distribution/soft-delete columns. Used to prove the
# R15-4 migration (c4f1a9d7e2b8) ADDS them on a real DB — the same "tests green
# != real DB" guard the R14 tests above enforce.
_PRE_R15_RESOURCE_ENTRY_DDL = """
CREATE TABLE resource_entry (
    resource_id VARCHAR(36) NOT NULL PRIMARY KEY,
    resource_type VARCHAR(32) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    version VARCHAR(50),
    source_type VARCHAR(32),
    source_trust_level VARCHAR(32),
    review_status VARCHAR(50),
    reviewer VARCHAR(255),
    review_notes TEXT,
    enabled BOOLEAN NOT NULL,
    created_at DATETIME
)
"""


class TestR15Migration:
    """R15-4-C1/C2: prove distribution + soft-delete columns come FROM the migration."""

    def test_r15_migration_adds_distribution_columns(self, tmp_path):
        import app.core.config as cfg
        from app.dependencies import clear_services_cache
        import app.core.database as db_mod
        from alembic.config import Config
        from alembic import command

        db_file = tmp_path / "r15mig" / "rebuild.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{db_file}"
        object.__setattr__(cfg.settings, "database_url", db_url)
        object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "r15mig"))
        db_mod._engine = None
        db_mod._SessionLocal = None
        clear_services_cache()

        # Pre-R15 state: resource_entry WITHOUT the new columns, stamped at R14 head.
        c = sqlite3.connect(db_file)
        c.execute(_PRE_R15_RESOURCE_ENTRY_DDL)
        c.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        c.execute("INSERT INTO alembic_version (version_num) VALUES (?)", ("a8b9c0d1e2f3",))
        c.commit()
        c.close()

        alembic_cfg = Config(str(BACKEND / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        command.upgrade(alembic_cfg, "head")

        c = sqlite3.connect(db_file)
        try:
            cols = [r[1] for r in c.execute("PRAGMA table_info(resource_entry)").fetchall()]
            for col in ("package_url", "checksum_sha256", "manifest_json",
                        "download_count", "icon_url", "deleted_at"):
                assert col in cols, f"{col} not added by migration; cols={cols}"
            # review columns intentionally KEPT (deprecated, D-061 修订), not dropped
            assert "review_status" in cols
            assert "reviewer" in cols
            # R16-B E3 must ALSO have added imported_version
            assert "imported_version" in cols, f"imported_version missing; cols={cols}"
            ver = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            # R15-4 = 39528fb4d798; R16-B E3 = e4f5a6b7c8d9; R17.5 WP-6 = f8a1b2c3d4e5;
            # R17.5-P4-FIX 批3 = a1c2e3f40901 (D-109); 批4 = b1c2d3e4f5a6 (call_log 内容/归因列, D-111, head).
            assert ver == "b1c2d3e4f5a6", ver
        finally:
            c.close()


class TestMigratedEndpoints:
    def _client(self):
        from fastapi.testclient import TestClient
        from app.dependencies import clear_services_cache, get_services
        clear_services_cache()
        get_services()
        from app.main import app
        return TestClient(app)

    def _make_project(self):
        from app.dependencies import get_services
        from app.schemas.project import ProjectCreate
        svc = get_services()
        p = svc.project_service.create(ProjectCreate(
            name="r14-6-mig", description="migrated integration", source_type="manual",
        ))
        return p.project_id

    def _make_host(self, i: int) -> str:
        from app.core.database import get_session
        from app.models.remote_host import RemoteHost, HostType, RemoteHostStatus
        with get_session() as db:
            h = RemoteHost(name=f"mig-h{i}", host_type=HostType.virtual_machine,
                           address=f"10.7.0.{i}", status=RemoteHostStatus.connected)
            db.add(h)
            db.commit()
            db.refresh(h)
            return h.remote_host_id

    def test_bind_multibinding_on_migrated_db(self, tmp_path):
        _migrate_fresh_db(tmp_path)
        client = self._client()
        pid = self._make_project()
        host_ids = [self._make_host(i) for i in range(3)]

        # Bind 3 remote hosts to one workspace — first default, rest non-default.
        for idx, hid in enumerate(host_ids):
            resp = client.post(
                f"/api/projects/{pid}/environment/bindings",
                json={"remote_host_id": hid, "is_default": idx == 0},
            )
            assert resp.status_code == 200, resp.text

        env = client.get(f"/api/projects/{pid}/environment/block").json()["data"]
        assert len(env["bindings"]) == 3
        assert env["default_binding_id"] is not None

        # DB-level: exactly one default row for this workspace.
        from app.core.database import get_session
        from app.models.workspace_environment_binding import WorkspaceEnvironmentBinding
        with get_session() as db:
            rows = (db.query(WorkspaceEnvironmentBinding)
                    .filter_by(workspace_id=pid).all())
            assert len(rows) == 3
            assert sum(1 for r in rows if r.is_default) == 1

    def test_detect_endpoint_on_migrated_db(self, tmp_path):
        _migrate_fresh_db(tmp_path)
        client = self._client()
        pid = self._make_project()
        hid = self._make_host(99)

        fake_provider = MagicMock()
        fake_provider.detect_environment = AsyncMock(return_value={
            "ok": True, "os": "Ubuntu 24.04 LTS", "python": "3.12.3", "services": [],
        })
        with patch("app.services.execution_provider.get_execution_provider",
                   return_value=fake_provider):
            resp = client.get(f"/api/projects/{pid}/environment/detect/{hid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["ok"] is True
        assert "Ubuntu" in data["os"]


class TestMigratedP5Invocation:
    def test_p5_remote_dispatch_records_invocation_with_refs(self, tmp_path):
        """P5 remote dispatch on a migrated DB persists a RemoteInvocation whose
        audit_ref / trace_ref are REAL back-links (B-R14-INV-REF-1)."""
        _migrate_fresh_db(tmp_path)
        from app.dependencies import clear_services_cache, get_services
        clear_services_cache()
        get_services()

        from app.core.database import get_session
        from app.models.remote_host import RemoteHost, HostType, RemoteHostStatus
        from app.models.workspace_environment_binding import WorkspaceEnvironmentBinding
        from app.models.remote_invocation import RemoteInvocation
        from app.services import workspace_service as ws_mod
        from app.services.p5_command_service import P5CommandExecutionService

        # Register host + workspace default binding.
        with get_session() as db:
            h = RemoteHost(name="p5-host", host_type=HostType.virtual_machine,
                           address="10.6.0.1", status=RemoteHostStatus.connected)
            db.add(h)
            db.commit()
            db.refresh(h)
            hid = h.remote_host_id
            b = WorkspaceEnvironmentBinding(
                workspace_id="p5proj", remote_host_id=hid, is_default=True)
            db.add(b)
            db.commit()
            db.refresh(b)
            bid = b.binding_id

        ws_mod.add_binding_to_workspace("p5proj", bid, set_default=True)

        # Mocked remote provider result (provider=remote_ssh triggers recording).
        async def fake_execute(code, language="bash", timeout=120, model=None, cwd=None):
            return {"exit_code": 0, "stdout": "ok", "stderr": "",
                    "blocked": False, "risk_level": "L1", "provider": "remote_ssh"}
        fake_provider = MagicMock()
        fake_provider.execute = fake_execute

        # Real in-memory tracer/auditor so back-link ids are real (not mock magic).
        from app.core.trace_writer import TraceWriter
        from app.core.audit_writer import AuditWriter
        tracer, auditor = TraceWriter(), AuditWriter()
        svc = P5CommandExecutionService(tracer=tracer, auditor=auditor)

        with patch("app.services.p5_command_service.get_execution_provider",
                   return_value=fake_provider), \
             patch("app.services.p5_command_service.workspace_path",
                   return_value=tmp_path / "p5ws"):
            (tmp_path / "p5ws").mkdir(parents=True, exist_ok=True)
            result = svc.execute_slot_command("p5proj", "run_verified", "echo ok")

        assert result.executed is True
        assert result.invocation_id is not None
        # Invocation persisted with real audit_ref + trace_ref.
        with get_session() as db:
            inv = db.get(RemoteInvocation, result.invocation_id)
            assert inv is not None
            assert inv.audit_ref and inv.audit_ref.startswith("AU-"), inv.audit_ref
            assert inv.trace_ref and inv.trace_ref.startswith("trace-"), inv.trace_ref
