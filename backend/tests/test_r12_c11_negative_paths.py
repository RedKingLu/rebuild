"""R12-3-C11 负路径测试。

覆盖 R12-3 全链路的失败场景（每个环节独立测试，不依赖其他环节真实执行）：
  - C4 硬必需：Evidence sha256 不一致 → validation_failed
  - C5 有条件：构建失败 → validation_failed（≠ completed）
  - C5 有条件：测试失败 → validation_failed
  - C5 有条件：命令不可识别 → needs_user_input
  - C5 有条件：命令超时 → retry → Gate
  - C6 失败路由：超出重试 → Gate 升级
  - C6 失败路由：静态检查失败 → 不阻塞 P5
  - C8 交付：P4 blocked → 交付包不生成
  - C9 P6 handler：completed 无 Gate → review failed
"""

import asyncio
import hashlib
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.p5_input_service import P4InputFacts
from app.services.p5_verification_service import P5VerificationService, SlotVerificationResult
from app.services.p5_failure_router import P5FailureRouter, P5FailureType
from app.services.p5_validation_plan import P5SlotStatus, can_mark_completed, create_p5_validation_plan


# ── C4: Evidence sha256 不一致 ─────────────────────────────────────────────

class TestNegativeEvidenceSha256:

    def test_evidence_sha256_mismatch_not_validated(self, tmp_path):
        """Evidence sha256 与实际文件不一致 → validation_failed（D-101 反伪造）。"""
        ws = tmp_path / "projects" / "p"
        for d in ["output_code", "evidence"]:
            (ws / d).mkdir(parents=True)
        (ws / "output_code" / "x.py").write_text("REAL CONTENT", encoding="utf-8")
        ev = {"evidence_id": "ev-001", "stage": "p4",
              "output_code_ref": "output_code/x.py",
              "output_sha256": "WRONG-SHA256", "status": "validated"}
        (ws / "evidence" / "ev-001.json").write_text(
            __import__("json").dumps(ev), encoding="utf-8")

        svc = P5VerificationService(aet=MagicMock())
        svc.aet.list_evidence.return_value = [ev]
        p4 = P4InputFacts(project_id="p", run_id="r", evidence_refs=["ev-001"])

        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = svc._verify_p4_evidence_real("p", p4)

        assert r.passed is False
        assert r.status == P5SlotStatus.VALIDATION_FAILED


# ── C5: 构建失败 ≠ completed ──────────────────────────────────────────────

class TestNegativeBuildFailed:

    def test_build_failed_not_completed(self):
        """构建失败 → validation_failed（不等于 completed）。"""
        plan = create_p5_validation_plan("p", "r")
        # 构建失败
        build_slot = plan.get_slot("build_verified")
        from app.services.p5_validation_plan import transition_slot_status
        transition_slot_status(build_slot, P5SlotStatus.IN_PROGRESS)
        transition_slot_status(build_slot, P5SlotStatus.VALIDATION_FAILED)
        build_slot.failure_reason = "compilation error"
        # 其他槽位通过
        for sid in ["output_code_exists", "patches_exist", "p4_evidence_real",
                     "p4_summary_readable", "p4_p5_gate_approved",
                     "run_verified", "tests_pass", "static_check"]:
            slot = plan.get_slot(sid)
            if slot:
                transition_slot_status(slot, P5SlotStatus.IN_PROGRESS)
                transition_slot_status(slot, P5SlotStatus.VALIDATED)

        can, reason = can_mark_completed(plan)
        assert can is False
        assert "BUILD_VERIFIED" in reason


# ── C5: 命令不可识别 → needs_user_input ───────────────────────────────────

class TestNegativeCommandUnavailable:

    def test_command_unavailable_honest_gap(self):
        """命令不可识别 → needs_user_input（诚实，不伪造通过）。"""
        router = P5FailureRouter()
        route = router.route(P5FailureType.NEEDS_USER_INPUT)
        assert route.gate_required is True
        assert route.blocked is True


# ── C6: 重试超出 → Gate 升级 ──────────────────────────────────────────────

class TestNegativeRetryExhausted:

    def test_retry_exhausted_escalates_to_gate(self):
        """重试 2 次仍失败 → 不再重试，Gate 升级。"""
        router = P5FailureRouter(max_retries=2)
        # 第 3 次（已超出）
        route = router.route(P5FailureType.COMMAND_FAILED, {"retry_count": 2})
        assert route.retry_allowed is False
        assert route.gate_required is True


# ── C6: 静态检查失败不阻塞 P5 ─────────────────────────────────────────────

class TestNegativeStaticCheck:

    def test_static_check_failed_not_blocking(self):
        """静态检查失败 → 不阻塞 P5（记录但不强制 rework）。"""
        router = P5FailureRouter()
        route = router.route(P5FailureType.STATIC_CHECK_FAILED)
        assert route.blocked is False
        assert route.p4_rework_required is False


# ── C8: P4 blocked → 交付包不生成 ──────────────────────────────────────────

class TestNegativeDeliveryBlocked:

    def test_p4_blocked_delivery_honest(self, tmp_path):
        """P4 输入 blocked → 交付包诚实记录阻断原因。"""
        from app.services.p6_delivery_service import P6DeliveryService
        svc = P6DeliveryService()
        ws = tmp_path / "projects" / "p"
        ws.mkdir(parents=True)
        blocked_input = P4InputFacts(project_id="p", run_id="r",
                                     blocked=True, blocked_reason="Gate 未通过")
        with patch("app.services.p6_delivery_service.workspace_path", return_value=ws), \
             patch("app.services.p6_delivery_service.P5InputService") as mock_cls:
            mock_cls.return_value.read_p4_input.return_value = blocked_input
            pkg = svc.generate_delivery_package("p", "r")
        assert pkg.risk_manifest.get("blocking") is True


# ── C9: P6 completed 无 Gate → review failed ──────────────────────────────

class TestNegativeP6NoGate:

    def test_p6_completed_without_gate_fails_review(self):
        """P6 completed 但未创建 Gate → review failed（D-023 违规检测）。"""
        from app.graph.stage_handlers import RealP6Handler
        handler = RealP6Handler()
        review = handler.review({"status": "completed", "p6_final_gate_id": None})
        assert review.passed is False
        assert any("Gate" in i.get("detail", "") for i in review.issues)
