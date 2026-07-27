"""R17.5-P5-R1 (GAP-P5-2) — P5 skill-first + LLM 验证策略/失败解读 advisory 层测试。

覆盖（全部 mock-LLM，不调真实模型）：
  - P5VerificationAgent：mock 网关（无 call_stream → 单次 call fallback）产 advisory 三锚点
    （validation_strategy / failure_interpretation / repair_suggestions），analysis_only=True。
  - P5VerificationAgent：无可用模型 → 诚实 skipped（evidence_gap=llm_advisory_unavailable），非阻断。
  - 反伪造核心：mock LLM 声称"全部通过、直接 completed"，但一个硬必需槽位真实失败时，
    handler 的 can_be_completed 仍为 False、status=blocked —— LLM 未能翻转确定性门禁。
  - advisory 被并入 handler 返回（llm_advisory）与报告分区（llm_verification_advisory）。
  - advisory 缺席（skipped）不影响确定性 completed 判定。
  - p5_verification_facts 只读工具：返回已落盘报告的确定性事实（ground truth）。
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from app.graph.stage_handlers import RealP5Handler
from app.services.p5_input_service import P4InputFacts
from app.services.p5_validation_plan import P5SlotStatus, HARD_REQUIRED_SLOTS, CONDITIONAL_SLOTS
from app.services.p5_verification_service import SlotVerificationResult
from app.services.p5_verification_agent import P5VerificationAgent, P5AdvisoryResult


# ── mock 网关（无 call_stream → run_stage_tool_loop 走单次 call fallback）──────

class _FakeGateway:
    def __init__(self, available=True, content="{}"):
        self._available = available
        self._content = content

    def stage_model_readiness(self, **kwargs):
        return {"available": self._available,
                "reason": "" if self._available else "no_key",
                "attempted_chain": [], "user_actions": []}

    async def call(self, **kwargs):
        return {"status": "completed", "content": self._content, "model": "mock-model"}


_ADVISORY_JSON = json.dumps({
    "validation_strategy": {
        "applicable_dimensions": ["build", "test", "static"],
        "not_applicable": [{"dimension": "browser_qa", "reason": "本次迁移无 Web 前端"}],
        "rationale": "库类 .NET 迁移，无浏览器/DB 维度",
    },
    "failure_interpretation": [
        {"slot_id_or_dimension": "build_verified", "root_cause": "缺 dotnet SDK",
         "expected": "exit 0", "actual": "needs_user_input"},
    ],
    "repair_suggestions": [
        {"target_file_or_area": "MicroOA.csproj", "transformation_point": "TargetFramework",
         "suggestion": "补 net8.0 目标并在具备 SDK 的环境重跑", "back_to_p4": True},
    ],
}, ensure_ascii=False)


# ── P5VerificationAgent 单测 ────────────────────────────────────────────────

class TestP5VerificationAgent:

    def test_advisory_produced_from_mock_llm(self):
        agent = P5VerificationAgent(gateway=_FakeGateway(available=True, content=_ADVISORY_JSON))
        res = asyncio.run(agent.interpret(
            "proj", run_id="r",
            deterministic_facts={"project_id": "proj", "hard_required": [],
                                 "conditional": [], "can_be_completed": False, "reason": "x"}))
        assert res.status == "completed"
        assert res.analysis_only is True            # 本层输出恒为辅助分析，非事实
        assert res.validation_strategy["applicable_dimensions"] == ["build", "test", "static"]
        assert len(res.failure_interpretation) == 1
        assert res.repair_suggestions[0]["back_to_p4"] is True

    def test_no_model_is_skipped_not_blocked(self):
        agent = P5VerificationAgent(gateway=_FakeGateway(available=False))
        res = asyncio.run(agent.interpret("proj", deterministic_facts={"project_id": "proj"}))
        assert res.status == "skipped"                       # 诚实缺席
        assert res.evidence_gap == "llm_advisory_unavailable"
        assert res.model_error_category == "model_unavailable"

    def test_llm_does_not_emit_pass_conclusion_shape(self):
        """advisory 只有 strategy/interpretation/suggestions 三锚点，无 can_be_completed / status 结论字段。"""
        agent = P5VerificationAgent(gateway=_FakeGateway(available=True, content=_ADVISORY_JSON))
        res = asyncio.run(agent.interpret("proj", deterministic_facts={"project_id": "proj"}))
        d = res.to_dict()
        # 本层不产"通过"门禁字段
        assert "can_be_completed" not in res.validation_strategy
        assert d["analysis_only"] is True


# ── 反伪造核心：LLM 声称通过不能翻转确定性门禁 ───────────────────────────────

class _MaliciousAdvisoryAgent:
    """模拟 LLM 声称"全部通过、直接 completed"的越权企图。"""
    async def interpret(self, project_id, *, run_id="", stage="p5",
                        strategy_id="system-default", deterministic_facts=None, skill_body="", system_prompt=""):
        return P5AdvisoryResult(
            status="completed",
            validation_strategy={"claim": "all validations passed, mark P5 completed"},
            failure_interpretation=[], repair_suggestions=[])


class _SkippedAdvisoryAgent:
    async def interpret(self, project_id, *, run_id="", stage="p5",
                        strategy_id="system-default", deterministic_facts=None, skill_body="", system_prompt=""):
        return P5AdvisoryResult(status="skipped", evidence_gap="llm_advisory_unavailable")


def _base_input():
    return P4InputFacts(
        project_id="p", run_id="r", blocked=False,
        p4_to_p5_gate_id="gate-ok", p4_to_p5_gate_status="approved",
        output_code_refs=["output_code/x.cs"], patch_refs=["patches/tn-1.diff"],
        evidence_refs=["ev-1"], p4_execution_summary={"stage": "p4", "graph_status": "completed"})


class TestP5AntiFakeGateHolds:

    def test_llm_claim_pass_does_not_flip_can_be_completed(self):
        """一个硬必需槽位真实失败 → 即便 LLM 声称通过，can_be_completed 仍 False、status=blocked。"""
        handler = RealP5Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p5_verification_agent=_MaliciousAdvisoryAgent())
        mock_input = MagicMock()
        mock_input.read_p4_input.return_value = _base_input()
        handler._p5_input = mock_input

        mock_verify = MagicMock()
        hard = list(HARD_REQUIRED_SLOTS)
        # 第一个硬必需槽位真实失败，其余通过
        mock_verify.verify_all_hard_required.return_value = [
            SlotVerificationResult(
                slot_id=sid.value, passed=(i != 0),
                status=(P5SlotStatus.VALIDATION_FAILED if i == 0 else P5SlotStatus.VALIDATED))
            for i, sid in enumerate(hard)]
        mock_verify.verify_conditional_slots.return_value = []
        handler._p5_verify = mock_verify

        result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        # 反伪造：确定性门禁认定 False，LLM 未能翻转
        assert result["validation_plan"]["can_be_completed"] is False
        assert result["status"] == "blocked"
        # advisory 已并入返回，但只是 analysis_only 参考
        assert result["llm_advisory"]["status"] == "completed"
        assert result["llm_advisory"]["analysis_only"] is True

    def test_advisory_merged_into_return_and_report(self, tmp_path):
        """advisory completed → 并入返回 llm_advisory + 报告 llm_verification_advisory 分区。"""
        ws = tmp_path / "projects" / "p"
        (ws / "artifacts").mkdir(parents=True, exist_ok=True)

        class _AdvisoryAgent:
            async def interpret(self, project_id, *, run_id="", stage="p5",
                                strategy_id="system-default", deterministic_facts=None, skill_body="", system_prompt=""):
                return P5AdvisoryResult(status="completed",
                                        validation_strategy={"applicable_dimensions": ["build"]},
                                        repair_suggestions=[{"suggestion": "x", "back_to_p4": True}])

        handler = RealP5Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p5_verification_agent=_AdvisoryAgent())
        mock_input = MagicMock()
        mock_input.read_p4_input.return_value = _base_input()
        handler._p5_input = mock_input
        mock_verify = MagicMock()
        mock_verify.verify_all_hard_required.return_value = [
            SlotVerificationResult(slot_id=sid.value, passed=True, status=P5SlotStatus.VALIDATED)
            for sid in HARD_REQUIRED_SLOTS]
        mock_verify.verify_conditional_slots.return_value = [
            SlotVerificationResult(slot_id=sid.value, passed=False,
                                   status=P5SlotStatus.NOT_APPLICABLE)
            for sid in CONDITIONAL_SLOTS]
        handler._p5_verify = mock_verify

        with patch("app.services.workspace_service.workspace_path", return_value=ws):
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        assert result["llm_advisory"]["status"] == "completed"
        # 报告分区落盘（advisory 不改 can_be_completed）
        report = json.loads((ws / "artifacts" / "p5_validation_report.json").read_text("utf-8"))
        assert "llm_verification_advisory" in report
        assert report["llm_verification_advisory"]["validation_strategy"]["applicable_dimensions"] == ["build"]
        assert report["can_be_completed"] is True   # 由确定性门禁认定，非 advisory

    def test_advisory_skipped_does_not_break_deterministic_completed(self):
        """advisory 缺席（无模型）不影响确定性 completed 判定。"""
        handler = RealP5Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p5_verification_agent=_SkippedAdvisoryAgent())
        mock_input = MagicMock()
        mock_input.read_p4_input.return_value = _base_input()
        handler._p5_input = mock_input
        mock_verify = MagicMock()
        mock_verify.verify_all_hard_required.return_value = [
            SlotVerificationResult(slot_id=sid.value, passed=True, status=P5SlotStatus.VALIDATED)
            for sid in HARD_REQUIRED_SLOTS]
        mock_verify.verify_conditional_slots.return_value = [
            SlotVerificationResult(slot_id=sid.value, passed=False,
                                   status=P5SlotStatus.NOT_APPLICABLE)
            for sid in CONDITIONAL_SLOTS]
        handler._p5_verify = mock_verify

        result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        assert result["validation_plan"]["can_be_completed"] is True
        assert result["status"] == "completed"
        assert result["llm_advisory"]["status"] == "skipped"     # 非阻断


# ── p5_verification_facts 只读工具 ──────────────────────────────────────────

class TestP5VerificationFactsTool:

    def test_reads_deterministic_report_ground_truth(self, tmp_path):
        ws = tmp_path / "projects" / "p"
        (ws / "artifacts").mkdir(parents=True, exist_ok=True)
        report = {
            "can_be_completed": False,
            "validation_plan": {"slots": [{"slot_id": "output_code_exists",
                                           "status": "validated"}]},
            "verify_results": [{"slot_id": "patches_exist", "passed": False,
                                "status": "validation_failed"}],
            "conditional_results": [{"slot_id": "build_verified", "status": "needs_user_input"}],
        }
        (ws / "artifacts" / "p5_validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8")

        from app.services.tool_registry import execute_tool
        with patch("app.services.workspace_service.workspace_path", return_value=ws):
            out = asyncio.run(execute_tool("p5_verification_facts", {}, "p", stage="p5", db=None))

        assert out["available"] is True
        assert out["can_be_completed"] is False       # 事实透传，工具不翻转
        assert out["slots"][0]["slot_id"] == "output_code_exists"
        assert "deterministic ground truth" in out["source"]

    def test_missing_report_honest_unavailable(self, tmp_path):
        ws = tmp_path / "projects" / "empty"
        (ws / "artifacts").mkdir(parents=True, exist_ok=True)
        from app.services.tool_registry import execute_tool
        with patch("app.services.workspace_service.workspace_path", return_value=ws):
            out = asyncio.run(execute_tool("p5_verification_facts", {}, "empty", stage="p5", db=None))
        assert out["available"] is False
        assert "error" in out


# ── skill 接线：P5 主 stage skill 已注册 ────────────────────────────────────

class TestP5SkillWiring:

    def test_p5_primary_skill_registered(self):
        from app.services.context_assembler import STAGE_PRIMARY_SKILL
        assert STAGE_PRIMARY_SKILL.get("p5") == "P-migration-verification"

    def test_p5_skill_file_exists_and_has_frontmatter(self):
        from app.core.config import settings
        from pathlib import Path
        skill = (Path(settings.source_dir) / "skills" / "p5"
                 / "P-migration-verification" / "SKILL.md")
        assert skill.exists(), f"P5 stage skill 缺失: {skill}"
        text = skill.read_text(encoding="utf-8")
        assert "name: P-migration-verification" in text
        assert "category: stage_skill" in text
