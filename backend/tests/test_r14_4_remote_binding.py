"""R14-4 tests — Remote environment binding, dispatch, invocation, detection.

Covers WP1-WP7 safety gate + functional acceptance:
  - Binding model: one default per workspace, multiple bindings allowed.
  - Environment block single source in workspace.json.
  - P5 remote dispatch: workspace default binding → remote_host_id enters factory.
  - RemoteInvocation recording after remote execution.
  - Read-only environment detection (G8) does not mutate.
  - Safety gate: L4/L5 remote commands blocked; no shell injection; output sanitization.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from app.models.workspace_environment_binding import WorkspaceEnvironmentBinding, BindingStatus
from app.models.remote_invocation import RemoteInvocation, InvocationTrigger, InvocationProvider
from app.models.remote_host import RemoteHost, HostType, RemoteHostStatus


# ── helpers ────────────────────────────────────────────────────────────────

def _make_workspace(tmp: Path, project_id: str) -> Path:
    ws = tmp / "projects" / project_id
    (ws / ".rebuild").mkdir(parents=True, exist_ok=True)
    (ws / ".rebuild" / "workspace.json").write_text(json.dumps({
        "project_id": project_id,
        "execution_mode": "plan",
        "environment": {"default_binding_id": None, "bindings": []},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return ws


# ══════════════════════════════════════════════════════════════════
# WP1: Binding model
# ══════════════════════════════════════════════════════════════════

class TestBindingModel:
    def test_binding_creation(self):
        b = WorkspaceEnvironmentBinding(
            binding_id="b-1", workspace_id="ws-1", remote_host_id="host-1",
            remote_workdir="/workspace/ws-1",
            status=BindingStatus.active, is_default=False,
        )
        assert b.binding_id == "b-1"
        assert b.status == BindingStatus.active
        assert b.is_default is False

    def test_binding_status_enum(self):
        assert BindingStatus.active.value == "active"
        assert BindingStatus.disabled.value == "disabled"
        assert BindingStatus.error.value == "error"


class TestInvocationModel:
    def test_invocation_creation(self):
        inv = RemoteInvocation(
            invocation_id="i-1", workspace_id="ws-1", command_digest="abc123",
            exit_code=0, risk_level="L1", elapsed_ms=120,
            provider=InvocationProvider.remote_ssh, trigger=InvocationTrigger.user,
            status="success",
        )
        assert inv.invocation_id == "i-1"
        assert inv.provider == InvocationProvider.remote_ssh
        assert inv.trigger == InvocationTrigger.user
        assert inv.status == "success"

    def test_invocation_failure_status(self):
        inv = RemoteInvocation(
            invocation_id="i-2", workspace_id="ws-1", command_digest="x",
            exit_code=-1, risk_level="L5", status="failure",
        )
        assert inv.status == "failure"


# ══════════════════════════════════════════════════════════════════
# WP2: Environment block single source
# ══════════════════════════════════════════════════════════════════

class TestEnvironmentBlock:
    def _patch_ws(self, tmp_path, monkeypatch):
        """Point the real workspace_path at a temp dir via settings.workspace_dir."""
        import app.core.config as cfg
        ws_dir = str(tmp_path / "workspace")
        object.__setattr__(cfg.settings, "workspace_dir", ws_dir)
        return ws_dir

    def test_get_default_block(self, tmp_path, monkeypatch):
        self._patch_ws(tmp_path, monkeypatch)
        from app.services import workspace_service as ws_mod
        blk = ws_mod.get_environment_block("p1")
        assert blk["default_binding_id"] is None
        assert blk["bindings"] == []

    def test_add_and_set_default(self, tmp_path, monkeypatch):
        self._patch_ws(tmp_path, monkeypatch)
        from app.services import workspace_service as ws_mod
        env = ws_mod.add_binding_to_workspace("p1", "b1", set_default=True)
        assert "b1" in env["bindings"]
        assert env["default_binding_id"] == "b1"
        # second binding, not default
        env = ws_mod.add_binding_to_workspace("p1", "b2", set_default=False)
        assert "b2" in env["bindings"]
        assert env["default_binding_id"] == "b1"  # unchanged
        # switch default
        env = ws_mod.set_default_binding("p1", "b2")
        assert env["default_binding_id"] == "b2"

    def test_remove_binding_clears_default(self, tmp_path, monkeypatch):
        self._patch_ws(tmp_path, monkeypatch)
        from app.services import workspace_service as ws_mod
        ws_mod.add_binding_to_workspace("p1", "b1", set_default=True)
        env = ws_mod.remove_binding_from_workspace("p1", "b1")
        assert "b1" not in env["bindings"]
        assert env["default_binding_id"] is None

    def test_resolve_default_returns_none_when_no_binding(self, tmp_path, monkeypatch):
        self._patch_ws(tmp_path, monkeypatch)
        from app.services import workspace_service as ws_mod
        assert ws_mod.resolve_default_remote_host_id("p1") is None


# ══════════════════════════════════════════════════════════════════
# WP3: P5 remote dispatch
# ══════════════════════════════════════════════════════════════════

class TestP5RemoteDispatch:
    def test_factory_requires_remote_host_id(self):
        from app.services.execution_provider import get_execution_provider
        with pytest.raises(ValueError, match="remote_host_id"):
            get_execution_provider(mode="remote")

    def test_factory_remote_with_host_and_db(self, tmp_path):
        from app.services.execution_provider import get_execution_provider
        from app.core.database import get_session
        from app.models.remote_host import RemoteHost, HostType
        with get_session() as db:
            h = RemoteHost(
                name="t", host_type=HostType.virtual_machine,
                address="1.2.3.4", status=RemoteHostStatus.connected,
            )
            db.add(h)
            db.commit()
            db.refresh(h)
            provider = get_execution_provider(mode="remote", remote_host_id=h.remote_host_id, db=db)
            assert provider.name == "remote_ssh"


# ══════════════════════════════════════════════════════════════════
# WP4: Invocation recording
# ══════════════════════════════════════════════════════════════════

class TestInvocationRecording:
    def test_record_invocation_success(self, tmp_path, monkeypatch):
        from app.services import invocation_service as inv_mod
        monkeypatch.setattr(inv_mod, "workspace_path", lambda pid: _make_workspace(tmp_path, pid))
        from app.core.database import get_session
        with get_session() as db:
            inv = inv_mod.record_invocation(
                db=db, workspace_id="p1", binding_id="b1",
                provider="remote_ssh", command_digest="deadbeef",
                exit_code=0, risk_level="L1", elapsed_ms=100,
                stdout="ok", stderr="",
            )
            assert inv.invocation_id is not None
            assert inv.status == "success"
            assert inv.stdout_ref is not None
            # stdout persisted to workspace logs
            log_file = _make_workspace(tmp_path, "p1") / inv.stdout_ref
            assert log_file.exists()
            assert log_file.read_text() == "ok"

    def test_record_invocation_failure(self, tmp_path, monkeypatch):
        from app.services import invocation_service as inv_mod
        monkeypatch.setattr(inv_mod, "workspace_path", lambda pid: _make_workspace(tmp_path, pid))
        from app.core.database import get_session
        with get_session() as db:
            inv = inv_mod.record_invocation(
                db=db, workspace_id="p1", binding_id=None,
                provider="remote_ssh", command_digest="x",
                exit_code=-1, risk_level="L5", elapsed_ms=0,
                stdout="", stderr="boom",
            )
            assert inv.status == "failure"


# ══════════════════════════════════════════════════════════════════
# WP2b: Multi-binding on a real DB session (R14-6, B-R14-MULTIBIND-1)
# ══════════════════════════════════════════════════════════════════

class TestMultiBindingRealDB:
    def _mk_host(self, db, i: int) -> str:
        h = RemoteHost(
            name=f"h{i}", host_type=HostType.virtual_machine,
            address=f"10.9.0.{i}", status=RemoteHostStatus.connected,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        return h.remote_host_id

    def test_three_bindings_same_workspace_commit(self):
        """A workspace may bind >=3 remote hosts (all non-default) — no IntegrityError."""
        from app.core.database import get_session
        with get_session() as db:
            host_ids = [self._mk_host(db, i) for i in range(3)]
            for hid in host_ids:
                db.add(WorkspaceEnvironmentBinding(
                    workspace_id="ws-multi", remote_host_id=hid, is_default=False,
                ))
            db.commit()  # must NOT raise: partial index only guards is_default=1
            rows = (db.query(WorkspaceEnvironmentBinding)
                    .filter_by(workspace_id="ws-multi").all())
            assert len(rows) == 3
            assert sum(1 for r in rows if r.is_default) == 0

    def test_one_default_plus_many_nondefault(self):
        """One default + several non-default bindings coexist for a workspace."""
        from app.core.database import get_session
        with get_session() as db:
            host_ids = [self._mk_host(db, 10 + i) for i in range(3)]
            db.add(WorkspaceEnvironmentBinding(
                workspace_id="ws-def", remote_host_id=host_ids[0], is_default=True))
            db.add(WorkspaceEnvironmentBinding(
                workspace_id="ws-def", remote_host_id=host_ids[1], is_default=False))
            db.add(WorkspaceEnvironmentBinding(
                workspace_id="ws-def", remote_host_id=host_ids[2], is_default=False))
            db.commit()
            rows = (db.query(WorkspaceEnvironmentBinding)
                    .filter_by(workspace_id="ws-def").all())
            assert len(rows) == 3
            assert sum(1 for r in rows if r.is_default) == 1

    def test_two_defaults_rejected(self):
        """The partial unique index enforces at most one default per workspace."""
        from sqlalchemy.exc import IntegrityError
        from app.core.database import get_session
        with get_session() as db:
            host_ids = [self._mk_host(db, 20 + i) for i in range(2)]
            db.add(WorkspaceEnvironmentBinding(
                workspace_id="ws-two", remote_host_id=host_ids[0], is_default=True))
            db.commit()
            db.add(WorkspaceEnvironmentBinding(
                workspace_id="ws-two", remote_host_id=host_ids[1], is_default=True))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()


# ══════════════════════════════════════════════════════════════════
# WP5: Read-only detection (G8)
# ══════════════════════════════════════════════════════════════════

class TestEnvironmentDetection:
    def test_detect_returns_ok_structure(self):
        from app.services.remote_executor import RemoteSSHExecutionProvider
        host = MagicMock()
        host.credential_ref = None
        host.remote_host_id = "h1"
        provider = RemoteSSHExecutionProvider(host, MagicMock(), mode="auto")

        async def fake_execute(code, language="bash", timeout=30, model=None, cwd=None):
            # New detect issues separate commands; map each.
            if code.startswith("cat /etc/os-release"):
                return {"exit_code": 0, "stdout": "PRETTY_NAME=\"Ubuntu 24.04 LTS\"\nVERSION_ID=\"24.04\"\n", "stderr": "", "blocked": False}
            if code.startswith("uname"):
                return {"exit_code": 0, "stdout": "Linux ip-1 6.17.0 #7 SMP x86_64 GNU/Linux", "stderr": "", "blocked": False}
            if code.startswith("python3"):
                return {"exit_code": 0, "stdout": "Python 3.12.3", "stderr": "", "blocked": False}
            if code.startswith("command -v"):
                return {"exit_code": 0, "stdout": "/usr/bin/docker\n/usr/bin/node", "stderr": "", "blocked": False}
            if code.startswith("ss "):
                return {"exit_code": 0, "stdout": ":22\n:6379", "stderr": "", "blocked": False}
            return {"exit_code": 0, "stdout": "", "stderr": "", "blocked": False}
        provider.execute = fake_execute
        import asyncio
        result = asyncio.run(provider.detect_environment())
        assert result["ok"] is True
        assert "Ubuntu" in (result["os"] or "")
        assert result["python"] == "3.12.3"
        assert any(s["name"] == "redis" for s in result["services"])

    def test_detect_honest_on_block(self):
        from app.services.remote_executor import RemoteSSHExecutionProvider
        host = MagicMock()
        provider = RemoteSSHExecutionProvider(host, MagicMock(), mode="auto")

        async def fake_execute(code, language="bash", timeout=30, model=None, cwd=None):
            # If any command is blocked/denied, detect reports ok=False.
            return {"exit_code": 1, "stdout": "", "stderr": "blocked", "blocked": True}
        provider.execute = fake_execute
        import asyncio
        result = asyncio.run(provider.detect_environment())
        assert result["ok"] is False
        assert result["error"]


# ══════════════════════════════════════════════════════════════════
# WP7: Safety gate
# ══════════════════════════════════════════════════════════════════

class TestSafetyGate:
    def test_deny_list_blocks_dangerous_remote(self):
        """L5 dangerous patterns are hard-denied before any connection."""
        from app.services.execution_provider import DENY_SUBSTRINGS, _check_dangerous
        assert _check_dangerous("rm -rf /") is not None
        assert _check_dangerous("sudo cat /etc/shadow") is not None
        assert _check_dangerous("echo hello") is None

    def test_remote_exec_is_high_risk(self):
        from app.services.mode_policy import authorize_action
        # L4+ never auto-approved; requires confirmation
        authz = authorize_action("auto", "L4", "remote_exec: ls")
        assert authz["decision"] == "require_confirmation"

    def test_sanitize_output_redacts_credentials(self):
        from app.services.remote_executor import _sanitize_output
        out = "password=secret123\ntoken=abc456\nnormal line"
        cleaned = _sanitize_output(out)
        assert "[REDACTED" in cleaned
        assert "normal line" in cleaned
        assert "secret123" not in cleaned

    def test_deny_list_catches_dangerous_patterns(self):
        """DENY list must catch dangerous patterns (CWE-94 command injection)."""
        from app.services.execution_provider import _check_dangerous
        # rm -rf
        assert _check_dangerous("rm -rf /") is not None
        # sudo / system paths
        assert _check_dangerous("sudo rm /etc/passwd") is not None
        assert _check_dangerous("cat ~/.ssh/id_rsa") is not None
        assert _check_dangerous("echo hello > /etc/passwd") is not None
        # safe command passes
        assert _check_dangerous("python3 -m compileall -q .") is None
        assert _check_dangerous("echo connected") is None
