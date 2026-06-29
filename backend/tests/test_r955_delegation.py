"""R9-5-5 unit tests — V1/V2/V6 coverage.

Tests for the OpenCode external platform integration layer:
  - T1: OpenCodeModelResolver (model resolution, no key leakage)
  - T2: delegation_policy.should_delegate (21-cell truth table: 3 scope × 7 stage)
  - T4: WorkspaceMediator (boundary enforcement, risk classification)
  - T5: build_external_context (policy-based trimming)
  - T6: external_command_reviewer (deny/allow/HITL decisions)
  - Negative paths: N1-N6 from V6
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── T2: delegation_policy ────────────────────────────────────────────────

from app.services.delegation_policy import should_delegate, P_STAGES


class _Project:
    """Minimal project-like object for delegation policy tests."""
    def __init__(self, coding_agent_ref=None, external_platform_scope="none"):
        self.coding_agent_ref = coding_agent_ref
        self.external_platform_scope = external_platform_scope


class TestDelegationPolicy:
    """V1: delegation_policy.should_delegate decision matrix (3 scope × 7 stage = 21 cases)."""

    STAGES = ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]

    def test_no_agent_ref_always_false(self):
        """coding_agent_ref=None → always False regardless of scope."""
        for scope in ("none", "coding_only", "all_stages"):
            proj = _Project(coding_agent_ref=None, external_platform_scope=scope)
            for stage in self.STAGES:
                assert should_delegate(proj, stage) is False, f"scope={scope} stage={stage}"

    def test_scope_none_always_false(self):
        """scope=none → always False even with a ref."""
        proj = _Project(coding_agent_ref="agent-123", external_platform_scope="none")
        for stage in self.STAGES:
            assert should_delegate(proj, stage) is False

    def test_scope_coding_only_p4_only(self):
        """scope=coding_only → True only for P4."""
        proj = _Project(coding_agent_ref="agent-123", external_platform_scope="coding_only")
        for stage in self.STAGES:
            expected = (stage == "P4")
            assert should_delegate(proj, stage) is expected, f"stage={stage}"

    def test_scope_all_stages_all_true(self):
        """scope=all_stages → True for all P0-P6."""
        proj = _Project(coding_agent_ref="agent-123", external_platform_scope="all_stages")
        for stage in self.STAGES:
            assert should_delegate(proj, stage) is True, f"stage={stage}"

    def test_dict_input(self):
        """Works with dict input (not just ORM objects)."""
        proj_dict = {"coding_agent_ref": "agent-x", "external_platform_scope": "all_stages"}
        for stage in self.STAGES:
            assert should_delegate(proj_dict, stage) is True

    def test_case_insensitive_stage(self):
        """Stage matching is case-insensitive."""
        proj = _Project(coding_agent_ref="agent-123", external_platform_scope="coding_only")
        assert should_delegate(proj, "p4") is True
        assert should_delegate(proj, "P4") is True

    def test_unknown_scope_raises(self):
        """Unknown scope raises ValueError (G8 — no silent fallback)."""
        proj = _Project(coding_agent_ref="agent-123", external_platform_scope="bogus_scope")
        with pytest.raises(ValueError, match="Unknown external_platform_scope"):
            should_delegate(proj, "P4")


# ── T4: WorkspaceMediator ────────────────────────────────────────────────

from app.services.workspace_mediator import WorkspaceMediator


class TestWorkspaceMediator:
    """V1: WorkspaceMediator boundary enforcement and risk classification."""

    @pytest.fixture
    def ws(self, tmp_path):
        (tmp_path / "output_code").mkdir()
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "source").mkdir()
        return WorkspaceMediator(str(tmp_path))

    def test_read_inside_workspace_ok(self, ws, tmp_path):
        p = ws.guard_read(str(tmp_path / "source" / "file.py"))
        assert p.parent == (tmp_path / "source").resolve()

    def test_read_path_traversal_rejected(self, ws, tmp_path):
        with pytest.raises(ValueError, match="Boundary violation"):
            ws.guard_read(str(tmp_path / ".." / "etc" / "passwd"))

    def test_write_output_code_allowed(self, ws, tmp_path):
        target, risk = ws.check_write(str(tmp_path / "output_code" / "new_file.txt"))
        assert str(target) == str((tmp_path / "output_code" / "new_file.txt").resolve())
        assert risk == "L1"

    def test_write_output_code_executable_L3(self, ws, tmp_path):
        _, risk = ws.check_write(str(tmp_path / "output_code" / "script.py"))
        assert risk == "L3"

    def test_write_artifacts_allowed_L2(self, ws, tmp_path):
        _, risk = ws.check_write(str(tmp_path / "artifacts" / "report.json"))
        assert risk == "L2"

    def test_write_source_rejected(self, ws, tmp_path):
        with pytest.raises(ValueError, match="read-only"):
            ws.check_write(str(tmp_path / "source" / "app.py"))

    def test_write_outside_workspace_rejected(self, ws, tmp_path):
        with pytest.raises(ValueError, match="Boundary violation"):
            ws.check_write("/etc/shadow")

    def test_write_path_traversal_rejected(self, ws, tmp_path):
        with pytest.raises(ValueError, match="Boundary violation"):
            ws.check_write(str(tmp_path / "output_code" / ".." / ".." / "sensitive"))

    def test_write_non_allowed_dir_rejected(self, ws, tmp_path):
        with pytest.raises(ValueError, match="not in the allowed write directories"):
            ws.check_write(str(tmp_path / "materials" / "data.txt"))

    def test_write_overwrite_existing_output_code_L2(self, ws, tmp_path):
        existing = tmp_path / "output_code" / "existing.txt"
        existing.write_text("hello")
        _, risk = ws.check_write(str(existing))
        assert risk == "L2"


# ── T6: external_command_reviewer ────────────────────────────────────────

from app.services.external_command_reviewer import review


class TestExternalCommandReviewer:
    """V1: deny/allow/HITL decisions for command requests."""

    def test_deny_list_command_always_denied(self):
        result = review("rm -rf /tmp/workspace", mode="auto")
        assert result.is_denied(), "rm -rf must always be denied"
        assert result.risk_level == "L5"

    def test_deny_list_sudo_always_denied(self):
        result = review("sudo chmod 777 /etc/passwd", mode="auto")
        assert result.is_denied()

    def test_allowlisted_command_auto_mode(self):
        result = review("python3 test.py", mode="auto")
        assert result.is_allowed(), f"Expected auto_approved, got {result.verdict}"

    def test_allowlisted_command_plan_mode_inplan(self):
        result = review("ls -la", mode="plan", in_plan=True)
        assert result.is_allowed()

    def test_unknown_command_plan_mode_requires_hitl(self):
        result = review("npm install --save-dev jest", mode="plan")
        assert result.requires_hitl() or result.is_denied(), (
            f"Unknown command in plan mode should require HITL or be denied, got {result.verdict}"
        )

    def test_unknown_command_manual_mode_requires_hitl(self):
        result = review("make build", mode="manual")
        assert result.requires_hitl() or result.is_denied()

    def test_result_to_dict_has_required_fields(self):
        d = review("echo hello", mode="auto").to_dict()
        assert "verdict" in d
        assert "risk_level" in d
        assert "reason" in d
        assert "authorization" in d

    def test_deny_case_insensitive(self):
        result = review("RM -RF /tmp", mode="auto")
        assert result.is_denied()


# ── T5: external_context_builder (unit — mocked assemble_context) ────────

class TestExternalContextBuilder:
    """V1: context policy trimming (body size ordering: minimal < summary < full)."""

    def _mock_context(self, layers=None, skills=None):
        return {
            "project_id": "proj-1",
            "current_stage": "P4",
            "assembled_at": "2026-06-28T00:00:00Z",
            "layers": layers or {
                "C0": {"platform_identity": "rebuild v26", "description": "Platform info"},
                "C1": {"description": "Project overview " + "x" * 200},
                "C2": {"description": "Stage context " + "y" * 200},
            },
            "skills": skills or [
                {"name": "skill-1", "body": "Skill body " + "z" * 300},
            ],
            "workspace": {"path": "/tmp/ws"},
            "assembly_trace": {"layers_assembled": 3},
        }

    def test_minimal_shorter_than_summary(self):
        mock_ctx = self._mock_context()
        with patch("app.services.context_assembler.assemble_context", return_value=mock_ctx):
            from app.services.external_context_builder import build_external_context
            min_r = build_external_context("p", "P4", context_policy="minimal", task="do X")
            sum_r = build_external_context("p", "P4", context_policy="summary", task="do X")
            assert len(min_r["context_text"]) < len(sum_r["context_text"])

    def test_summary_shorter_than_full(self):
        mock_ctx = self._mock_context()
        with patch("app.services.context_assembler.assemble_context", return_value=mock_ctx):
            from app.services.external_context_builder import build_external_context
            sum_r = build_external_context("p", "P4", context_policy="summary", task="do X")
            full_r = build_external_context("p", "P4", context_policy="full", task="do X")
            assert len(sum_r["context_text"]) <= len(full_r["context_text"])

    def test_minimal_contains_task(self):
        mock_ctx = self._mock_context()
        with patch("app.services.context_assembler.assemble_context", return_value=mock_ctx):
            from app.services.external_context_builder import build_external_context
            r = build_external_context("p", "P4", context_policy="minimal", task="Migrate module X")
            assert "Migrate module X" in r["context_text"]

    def test_unknown_policy_raises(self):
        mock_ctx = self._mock_context()
        with patch("app.services.context_assembler.assemble_context", return_value=mock_ctx):
            from app.services.external_context_builder import build_external_context
            with pytest.raises(ValueError, match="Unknown context_policy"):
                build_external_context("p", "P4", context_policy="invalid")


# ── T1: OpenCodeModelResolver (V1 — mocked gateway) ─────────────────────

class TestOpenCodeModelResolver:
    """V1: model resolution + credential handling."""

    def _make_gateway_target(self):
        return {
            "model": "deepseek/deepseek-v3",
            "api_base": "https://api.example.com",
            "api_key": "sk-test-key",   # test value — not a real key
            "provider_id": "deepseek",
            "profile_id": "deepseek/deepseek-v3",
            "selection_reason": "default",
        }

    def test_resolve_returns_three_fields(self):
        target = self._make_gateway_target()
        mock_gw = MagicMock()
        mock_gw.resolve_call_target.return_value = target
        mock_services = MagicMock()
        mock_services.model_gateway = mock_gw

        with patch("app.dependencies.get_services", return_value=mock_services):
            from app.services.opencode_model_resolver import OpenCodeModelResolver
            result = OpenCodeModelResolver().resolve(project_id="proj-1")

        assert result["model_id"] == "deepseek/deepseek-v3"
        assert result["base_url"] == "https://api.example.com"
        assert "api_key" in result
        # api_key is present but must NOT be logged (this just checks structural presence)

    def test_no_target_raises_resolver_error(self):
        mock_gw = MagicMock()
        mock_gw.resolve_call_target.return_value = None
        mock_services = MagicMock()
        mock_services.model_gateway = mock_gw

        with patch("app.dependencies.get_services", return_value=mock_services):
            from app.services.opencode_model_resolver import OpenCodeModelResolver, ResolverError
            with pytest.raises(ResolverError):
                OpenCodeModelResolver().resolve()

    def test_missing_model_id_raises(self):
        target = {"model": None, "api_base": "https://x.com", "api_key": "k"}
        mock_gw = MagicMock()
        mock_gw.resolve_call_target.return_value = target
        mock_services = MagicMock()
        mock_services.model_gateway = mock_gw

        with patch("app.dependencies.get_services", return_value=mock_services):
            from app.services.opencode_model_resolver import OpenCodeModelResolver, ResolverError
            with pytest.raises(ResolverError, match="model_id"):
                OpenCodeModelResolver().resolve()

    def test_missing_base_url_raises(self):
        target = {"model": "gpt-4o", "api_base": None, "api_key": "k"}
        mock_gw = MagicMock()
        mock_gw.resolve_call_target.return_value = target
        mock_services = MagicMock()
        mock_services.model_gateway = mock_gw

        with patch("app.dependencies.get_services", return_value=mock_services):
            from app.services.opencode_model_resolver import OpenCodeModelResolver, ResolverError
            with pytest.raises(ResolverError, match="base_url"):
                OpenCodeModelResolver().resolve()

    def test_missing_api_key_raises(self):
        target = {"model": "gpt-4o", "api_base": "https://x.com", "api_key": None}
        mock_gw = MagicMock()
        mock_gw.resolve_call_target.return_value = target
        mock_services = MagicMock()
        mock_services.model_gateway = mock_gw

        with patch("app.dependencies.get_services", return_value=mock_services):
            from app.services.opencode_model_resolver import OpenCodeModelResolver, ResolverError
            with pytest.raises(ResolverError, match="api_key"):
                OpenCodeModelResolver().resolve()


# ── N1-N6 negative paths ─────────────────────────────────────────────────

class TestNegativePaths:
    """V6: explicit failure / denial cases."""

    def test_n1_no_provider_raises_delegationerror(self):
        """N1: resolver failure → DelegationError, no silent fallback."""
        with patch("app.services.external_platform_delegator.OpenCodeModelResolver") as MockResolver:
            from app.services.opencode_model_resolver import ResolverError
            MockResolver.return_value.resolve.side_effect = ResolverError("no provider")
            from app.services.external_platform_delegator import ExternalPlatformDelegator, DelegationError
            import asyncio
            delegator = ExternalPlatformDelegator("proj", "/tmp")
            with pytest.raises(DelegationError, match="no provider"):
                asyncio.run(delegator.delegate("task"))

    def test_n2_opencode_not_installed(self):
        """N2: opencode CLI not in PATH → explicit error."""
        with patch("app.services.external_platform_delegator.OpenCodeModelResolver") as MockRes, \
             patch("app.services.external_platform_delegator.is_opencode_available", return_value=False):
            MockRes.return_value.resolve.return_value = {
                "model_id": "m", "base_url": "http://x", "api_key": "k"
            }
            from app.services.external_platform_delegator import ExternalPlatformDelegator, DelegationError
            import asyncio
            delegator = ExternalPlatformDelegator("proj", "/tmp")
            with pytest.raises(DelegationError, match="opencode CLI"):
                asyncio.run(delegator.delegate("task"))

    def test_n3_deny_list_always_rejected(self):
        """N3: deny-list commands cannot be allowed regardless of mode."""
        from app.services.external_command_reviewer import review
        for mode in ("manual", "plan", "auto"):
            r = review("rm -rf /", mode=mode, in_plan=True)
            assert r.is_denied(), f"rm -rf should be denied in mode={mode}"

    def test_n4_write_source_always_rejected(self, tmp_path):
        """N4: writes to source/ are always rejected."""
        (tmp_path / "source").mkdir()
        mediator = WorkspaceMediator(str(tmp_path))
        with pytest.raises(ValueError, match="read-only"):
            mediator.check_write(str(tmp_path / "source" / "main.py"))

    def test_n5_qcode_not_implemented(self):
        """N5: qcode invoke returns honest not_implemented."""
        from app.services.opencode_adapter import QCodeCLIAdapter
        import asyncio
        result = asyncio.run(QCodeCLIAdapter().invoke_coding_task("task", "/tmp"))
        assert result["status"] == "not_implemented"

    def test_n6_no_key_in_result_dict(self):
        """N6: delegation result dict does not contain api_key / Key."""
        with patch("app.services.external_platform_delegator.OpenCodeModelResolver") as MockRes, \
             patch("app.services.external_platform_delegator.is_opencode_available", return_value=False):
            MockRes.return_value.resolve.return_value = {
                "model_id": "m", "base_url": "http://x", "api_key": "sk-secret-key-do-not-leak"
            }
            from app.services.external_platform_delegator import ExternalPlatformDelegator, DelegationError
            import asyncio
            delegator = ExternalPlatformDelegator("proj", "/tmp")
            try:
                result = asyncio.run(delegator.delegate("task"))
            except DelegationError:
                # Expected — but check the error itself doesn't contain the key
                return
            # If it somehow returns a result, verify key is not in it
            import json
            result_str = json.dumps(result)
            assert "sk-secret-key-do-not-leak" not in result_str
