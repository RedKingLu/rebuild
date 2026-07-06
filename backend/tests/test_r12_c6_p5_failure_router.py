"""R12-3-C6 P5 失败返工闭环测试。

覆盖 P5FailureRouter + RealP5Handler.review() 返工路由：
  - 有界重试（≤max_retries，超出 → Gate）
  - output_code 缺失 → blocked / P4 rework
  - 构建/测试失败 → PlanDelta + P4 rework
  - Evidence 缺失/无效 → blocked + Gate
  - L4/L5 高风险 → Gate 拦截
  - 命令不可识别 → Gate 请求用户提供
  - handler review() 返工建议
  - P5 不直接修改 output_code（代码问题回 P4 rework）
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from app.graph.stage_handlers import RealP5Handler
from app.services.p5_failure_router import (
    P5FailureRouter, P5FailureType, P5FailureRoute,
    create_plan_delta_for_failure,
)
from app.services.p5_validation_plan import P5SlotStatus


# ── P5FailureRouter ────────────────────────────────────────────────────────

class TestFailureRouter:
    """P5FailureRouter 路由决策。"""

    def setup_method(self):
        self.router = P5FailureRouter(max_retries=2)

    def test_output_code_missing_routes_to_p4_rework(self):
        route = self.router.route(P5FailureType.OUTPUT_CODE_MISSING)
        assert route.p4_rework_required is True
        assert route.blocked is True
        assert route.plan_delta_type == "blocking_adjustment"

    def test_patch_missing_routes_to_p4_rework(self):
        route = self.router.route(P5FailureType.PATCH_MISSING)
        assert route.p4_rework_required is True
        assert route.blocked is True

    def test_build_failed_routes_to_plan_delta_and_p4_rework(self):
        route = self.router.route(P5FailureType.BUILD_FAILED)
        assert route.p4_rework_required is True
        assert route.plan_delta_type == "blocking_adjustment"

    def test_test_failed_routes_to_plan_delta_and_p4_rework(self):
        route = self.router.route(P5FailureType.TEST_FAILED)
        assert route.p4_rework_required is True

    def test_static_check_failed_not_blocking(self):
        """静态检查失败不阻塞 P5（记录但不强制 rework）。"""
        route = self.router.route(P5FailureType.STATIC_CHECK_FAILED)
        assert route.blocked is False
        assert route.p4_rework_required is False

    def test_evidence_missing_routes_to_gate(self):
        route = self.router.route(P5FailureType.EVIDENCE_MISSING)
        assert route.gate_required is True
        assert "Evidence" in route.gate_reason

    def test_evidence_invalid_routes_to_gate_with_d101(self):
        route = self.router.route(P5FailureType.EVIDENCE_INVALID)
        assert route.gate_required is True
        assert route.blocked is True

    def test_l4_l5_risk_routes_to_gate(self):
        route = self.router.route(P5FailureType.L4_L5_RISK)
        assert route.gate_required is True
        assert route.blocked is True

    def test_needs_user_input_routes_to_gate(self):
        route = self.router.route(P5FailureType.NEEDS_USER_INPUT)
        assert route.gate_required is True

    def test_bounded_retry_within_limit(self):
        """重试未达上限 → retry_allowed。"""
        route = self.router.route(P5FailureType.COMMAND_FAILED,
                                   {"retry_count": 0})
        assert route.retry_allowed is True
        assert route.retry_count == 1
        assert route.max_retries == 2

    def test_bounded_retry_exhausted_routes_to_gate(self):
        """重试达上限 → Gate 升级。"""
        route = self.router.route(P5FailureType.COMMAND_FAILED,
                                   {"retry_count": 2})
        assert route.retry_allowed is False
        assert route.gate_required is True
        assert "重试" in route.gate_reason

    def test_p5_does_not_modify_output_code(self):
        """P5 不直接修改 output_code（代码问题回 P4 rework）。"""
        route = self.router.route(P5FailureType.OUTPUT_CODE_MISSING)
        assert route.p4_rework_required is True
        assert "P4 rework" in route.action or "P4" in route.action

    def test_should_create_gate_helper(self):
        route = self.router.route(P5FailureType.EVIDENCE_MISSING)
        assert self.router.should_create_gate(route) is True

    def test_should_p4_rework_helper(self):
        route = self.router.route(P5FailureType.BUILD_FAILED)
        assert self.router.should_p4_rework(route) is True


# ── RealP5Handler.review() 返工路由 ────────────────────────────────────────

class TestHandlerReviewRework:
    """RealP5Handler.review() 接入返工路由。"""

    def test_completed_passes(self):
        handler = RealP5Handler()
        review = handler.review({"status": "completed"})
        assert review.passed is True

    def test_blocked_build_failed_generates_p4_rework_recommendation(self):
        """构建失败 → review 建议回 P4 rework。"""
        handler = RealP5Handler()
        result = {
            "status": "blocked",
            "reason": "验证失败",
            "conditional_results": [
                {"slot_id": "build_verified", "command": "make",
                 "status": "validation_failed", "passed": False,
                 "stderr_tail": "compilation error"}
            ],
        }
        review = handler.review(result)
        assert review.passed is False
        assert any("P4" in r or "rework" in r for r in review.recommendations)

    def test_l4_risk_sets_gate_required(self):
        """L4 高风险 → review 设置 gate_required。"""
        handler = RealP5Handler()
        result = {
            "status": "blocked",
            "reason": "L4 risk",
            "conditional_results": [
                {"slot_id": "build_verified", "command": "chmod 777 /",
                 "status": "needs_user_input", "passed": False,
                 "gate_required": True, "gate_reason": "L4 risk"}
            ],
        }
        review = handler.review(result)
        assert review.passed is False
        assert result.get("gate_required") is True

    def test_needs_user_input_creates_evidence_gap(self):
        """命令不可识别 → review 生成 evidence_gap。"""
        handler = RealP5Handler()
        result = {
            "status": "blocked",
            "reason": "命令不可识别",
            "conditional_results": [
                {"slot_id": "build_verified", "command": "",
                 "status": "needs_user_input", "passed": False}
            ],
        }
        review = handler.review(result)
        assert review.passed is False
        assert result.get("evidence_gap") is not None

    def test_hard_required_failure_blocked(self):
        """硬必需槽位失败 → blocked。"""
        handler = RealP5Handler()
        result = {"status": "blocked", "reason": "output_code 缺失"}
        review = handler.review(result)
        assert review.passed is False


# ── PlanDelta 集成 ─────────────────────────────────────────────────────────

class TestPlanDeltaIntegration:
    """失败路由创建 PlanDelta 记录。"""

    def test_create_plan_delta_for_p4_rework_failure(self):
        """output_code 缺失 → PlanDelta 记录。"""
        route = P5FailureRoute(
            failure_type=P5FailureType.OUTPUT_CODE_MISSING,
            action="test",
            plan_delta_type="blocking_adjustment",
            plan_delta_reason="output_code missing",
        )
        # Mock database
        mock_db = MagicMock()
        mock_delta = MagicMock()
        mock_delta.plan_delta_id = "pd-test-001"
        with patch("app.services.p5_failure_router.get_session", return_value=mock_db), \
             patch("app.services.p5_failure_router.PlanDeltaService") as mock_cls:
            mock_svc = MagicMock()
            mock_svc.create_delta.return_value = mock_delta
            mock_cls.return_value = mock_svc
            result = create_plan_delta_for_failure(
                route, "proj-1", stage="p5", run_id="run-1")
        assert result == "pd-test-001"

    def test_no_plan_delta_when_not_needed(self):
        """无 plan_delta_type → None。"""
        route = P5FailureRoute(failure_type="test", action="test")
        result = create_plan_delta_for_failure(route, "proj-1")
        assert result is None
