"""R17.5-P5-R3 — patch 语义 + 死槽 + retry 穿透 + P6 字段来源收尾测试。

覆盖（全部确定性 / mock-LLM，不调真实模型；每个 gap 先复现旧缺陷再验修复）：

  GAP-P5-4  patches_exist 语义：确定性槽位【只核验存在+非空】，不再用 patch/output 计数启发式
            当 PASS/FAIL（重写式迁移一源多目标 → 旧启发式假阴 patch_output_mismatch）；
            source→target 结构对应由 LLM advisory 的 structure_mapping（diff 等价证据）产出。
  GAP-P5-5  source_unmodified 死槽 → 真 verifier：git 干净=validated(真证据)、被改=validation_failed、
            非 git 无基线=诚实 evidence_gap（capability_ready，绝不恒 True 假证据）；增强项不翻转门禁。
  GAP-P5-6  retry_count 真穿透：从 review_pass/round_end trace 计算真实重试次数（不再恒 0），
            无 tracer/无 query → 诚实回退 0。
  REC       P6 交付读【真 p5_validation_report.validation_plan（含十槽位）】而非 P4 执行摘要。
"""

import asyncio
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.graph.stage_handlers import RealP5Handler, RealP6Handler
from app.services.p5_input_service import P4InputFacts
from app.services.p5_validation_plan import (
    P5SlotStatus, HARD_REQUIRED_SLOTS, CONDITIONAL_SLOTS,
)
from app.services.p5_verification_service import P5VerificationService, SlotVerificationResult
from app.services.p5_verification_agent import P5VerificationAgent, P5AdvisoryResult


def _base_input():
    return P4InputFacts(
        project_id="p", run_id="r", blocked=False,
        p4_to_p5_gate_id="gate-ok", p4_to_p5_gate_status="approved",
        output_code_refs=["output_code/x.cs"], patch_refs=["patches/tn-1.diff"],
        evidence_refs=["ev-1"], p4_execution_summary={"stage": "p4", "graph_status": "completed"})


# ── GAP-P5-4: patches_exist 只产存在性事实，不做计数启发式判定 ─────────────────

class TestPatchesExistSemantics:

    def _rewrite_input(self, patch_count: int, out_count: int):
        return P4InputFacts(
            project_id="p", run_id="r", blocked=False,
            p4_to_p5_gate_id="g", p4_to_p5_gate_status="approved",
            output_code_refs=[f"output_code/f{i}.cs" for i in range(out_count)],
            patch_refs=[f"patches/tn-{i}.diff" for i in range(patch_count)],
            evidence_refs=["ev-1"], p4_execution_summary={})

    def test_rewrite_migration_many_patches_not_false_negative(self, tmp_path):
        """重写式迁移：73 patch > 72 output——旧启发式 correlation_ok 会假阴，现只要文件真实存在即 validated。"""
        ws = tmp_path / "projects" / "p"
        # 造 3 个 output_code + 73 个真实非空 patch（重写式：一源多目标+转换脚本）
        p4 = self._rewrite_input(patch_count=73, out_count=3)
        for ref in p4.output_code_refs + p4.patch_refs:
            f = ws / ref
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("content", encoding="utf-8")

        svc = P5VerificationService()
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            vr = svc._verify_patches_exist("p", p4)

        assert vr.passed is True
        assert vr.status == "validated"
        # 旧假阴 issue 不再出现，计数关系降级为中立事实
        assert not any(i.get("type") == "patch_output_mismatch" for i in vr.issues)
        assert "correlation_ok" not in vr.details
        assert vr.details["correspondence_assessed_by"] == "llm_structure_mapping_advisory"
        assert vr.details["patch_output_ratio"] is not None

    def test_missing_patch_file_is_real_validation_failed(self, tmp_path):
        """真实缺失/空 patch → validation_failed（存在性铁证仍严格，非放松反伪造）。"""
        ws = tmp_path / "projects" / "p"
        p4 = self._rewrite_input(patch_count=2, out_count=1)
        # 只造第一个 patch，第二个缺失
        f = ws / p4.patch_refs[0]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("diff", encoding="utf-8")

        svc = P5VerificationService()
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            vr = svc._verify_patches_exist("p", p4)

        assert vr.passed is False
        assert vr.status == "validation_failed"
        assert any(i.get("type") == "patch_missing" for i in vr.issues)

    def test_no_patch_refs_is_evidence_gap(self, tmp_path):
        ws = tmp_path / "projects" / "p"
        p4 = self._rewrite_input(patch_count=0, out_count=1)
        svc = P5VerificationService()
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            vr = svc._verify_patches_exist("p", p4)
        assert vr.status == "evidence_gap"


# ── GAP-P5-4: structure_mapping 经 advisory 层产出并透传（LLM 判断，非确定性脚本）──

class _FakeGateway:
    def __init__(self, content):
        self._content = content

    def stage_model_readiness(self, **kwargs):
        return {"available": True, "reason": "", "attempted_chain": [], "user_actions": []}

    async def call(self, **kwargs):
        return {"status": "completed", "content": self._content, "model": "mock-model"}


class TestStructureMappingAdvisory:

    def test_structure_mapping_parsed_and_carried(self):
        content = json.dumps({
            "validation_strategy": {"applicable_dimensions": ["build"]},
            "failure_interpretation": [],
            "repair_suggestions": [],
            "structure_mapping": [
                {"source": "Default.aspx", "target": "Pages/Index.cshtml",
                 "mapping_type": "rewrite", "note": "WebForms 页 → Razor 页整体重写"},
                {"source": "Default.aspx.cs", "target": "Pages/Index.cshtml.cs",
                 "mapping_type": "split", "note": "code-behind 拆分为 PageModel + service"},
            ],
        }, ensure_ascii=False)
        agent = P5VerificationAgent(gateway=_FakeGateway(content))
        res = asyncio.run(agent.interpret(
            "proj", run_id="r",
            deterministic_facts={"project_id": "proj", "hard_required": [], "conditional": [],
                                 "can_be_completed": True, "reason": "x"}))
        assert res.status == "completed"
        assert res.analysis_only is True                    # 结构映射是分析证据，非事实门禁
        assert len(res.structure_mapping) == 2
        assert res.structure_mapping[0]["mapping_type"] == "rewrite"
        assert res.to_dict()["structure_mapping"][1]["mapping_type"] == "split"

    def test_structure_mapping_defaults_empty_when_absent(self):
        agent = P5VerificationAgent(gateway=_FakeGateway(json.dumps({"validation_strategy": {}})))
        res = asyncio.run(agent.interpret("proj", deterministic_facts={"project_id": "proj"}))
        assert res.structure_mapping == []                  # 非重写式/未给 → 空数组，不臆造


# ── GAP-P5-5: source_unmodified 真 verifier / 诚实软证据（绝不假证据填死槽）─────

class TestSourceUnmodifiedVerifier:

    def _git_source(self, tmp_path, dirty: bool):
        ws = tmp_path / "projects" / "p"
        src = ws / "source"
        src.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=src, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=src, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
        (src / "a.cs").write_text("original", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=src, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=src, check=True)
        if dirty:
            (src / "a.cs").write_text("MODIFIED", encoding="utf-8")
        return ws

    def test_git_clean_is_validated_positive_proof(self, tmp_path):
        ws = self._git_source(tmp_path, dirty=False)
        svc = P5VerificationService()
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            vr = svc.verify_source_unmodified("p")
        assert vr.passed is True
        assert vr.status == "validated"                     # D-099 正向铁证
        assert vr.details["method"] == "git_status_porcelain"
        assert vr.details["changed_count"] == 0

    def test_git_dirty_is_validation_failed(self, tmp_path):
        ws = self._git_source(tmp_path, dirty=True)
        svc = P5VerificationService()
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            vr = svc.verify_source_unmodified("p")
        assert vr.status == "validation_failed"             # source/ 被改 → D-099 违背，真实报错
        assert vr.details["changed_count"] >= 1
        assert any(i.get("type") == "source_modified" for i in vr.issues)

    def test_non_git_is_honest_evidence_gap_not_fake_pass(self, tmp_path):
        """非 git 源无基线 → 诚实 evidence_gap（capability_ready），绝不恒 True 假证据填死槽。"""
        ws = tmp_path / "projects" / "p"
        (ws / "source").mkdir(parents=True, exist_ok=True)
        (ws / "source" / "a.cs").write_text("x", encoding="utf-8")
        svc = P5VerificationService()
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            vr = svc.verify_source_unmodified("p")
        assert vr.passed is False
        assert vr.status == "evidence_gap"
        assert vr.details["capability_ready"] is True
        assert vr.details["method"] == "no_baseline"
        assert any(i.get("type") == "no_source_baseline" for i in vr.issues)

    def test_no_source_is_honest_evidence_gap(self, tmp_path):
        ws = tmp_path / "projects" / "p"
        ws.mkdir(parents=True, exist_ok=True)
        svc = P5VerificationService()
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            vr = svc.verify_source_unmodified("p")
        assert vr.status == "evidence_gap"
        assert vr.details["source_present"] is False


class TestSourceUnmodifiedNonGating:

    def _handler_with_source(self, src_status):
        handler = RealP5Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p5_verification_agent=_SkipAdvisory())
        mock_input = MagicMock()
        mock_input.read_p4_input.return_value = _base_input()
        handler._p5_input = mock_input
        mock_verify = MagicMock()
        mock_verify.verify_all_hard_required.return_value = [
            SlotVerificationResult(slot_id=sid.value, passed=True, status=P5SlotStatus.VALIDATED)
            for sid in HARD_REQUIRED_SLOTS]
        mock_verify.verify_conditional_slots.return_value = [
            SlotVerificationResult(slot_id=sid.value, passed=False, status=P5SlotStatus.NOT_APPLICABLE)
            for sid in CONDITIONAL_SLOTS]
        mock_verify.verify_source_unmodified.return_value = SlotVerificationResult(
            slot_id="source_unmodified", passed=(src_status == P5SlotStatus.VALIDATED),
            status=src_status, details={"method": "git_status_porcelain"})
        handler._p5_verify = mock_verify
        return handler

    def _slot_status(self, result, slot_id):
        for s in result["validation_plan"]["slots"]:
            if s["slot_id"] == slot_id:
                return s["status"]
        return None

    def test_source_slot_transitions_and_does_not_flip_gate(self, tmp_path):
        """source_unmodified 增强槽被 verifier 真实驱动，但不参与门禁，不翻转 can_be_completed。"""
        ws = tmp_path / "projects" / "p"
        (ws / "artifacts").mkdir(parents=True, exist_ok=True)
        handler = self._handler_with_source(P5SlotStatus.VALIDATED)
        with patch("app.services.workspace_service.workspace_path", return_value=ws):
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))
        assert self._slot_status(result, "source_unmodified") == P5SlotStatus.VALIDATED.value
        assert result["validation_plan"]["can_be_completed"] is True   # 门禁只由硬/条件必需认定

    def test_source_modified_does_not_block_completed(self, tmp_path):
        """即便 source_unmodified=validation_failed（增强项），也不阻断 can_be_completed（非门禁）。"""
        ws = tmp_path / "projects" / "p"
        (ws / "artifacts").mkdir(parents=True, exist_ok=True)
        handler = self._handler_with_source(P5SlotStatus.VALIDATION_FAILED)
        with patch("app.services.workspace_service.workspace_path", return_value=ws):
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))
        assert self._slot_status(result, "source_unmodified") == P5SlotStatus.VALIDATION_FAILED.value
        assert result["validation_plan"]["can_be_completed"] is True   # 增强项失败不翻转门禁


class _SkipAdvisory:
    async def interpret(self, project_id, *, run_id="", stage="p5", strategy_id="system-default",
                        deterministic_facts=None, skill_body="", system_prompt=""):
        return P5AdvisoryResult(status="skipped", evidence_gap="llm_advisory_unavailable")


# ── GAP-P5-6: retry_count 从真实 run state（round_end trace）计算，不再恒 0 ──────

class _FakeTracer:
    def __init__(self, round_end_count=0):
        self._round_end = round_end_count

    def write(self, *a, **k):
        return {}

    def query(self, project_id=None, run_id=None, stage=None, trace_type=None, limit=50):
        if trace_type == "review_pass":
            return [{"action": "round_start"} for _ in range(self._round_end)] + \
                   [{"action": "round_end"} for _ in range(self._round_end)]
        return []


class TestRetryCountThreading:

    def test_retry_count_from_round_end_traces(self):
        handler = RealP5Handler(tracer=_FakeTracer(round_end_count=2))
        assert handler._p5_retry_count("p", "r") == 2

    def test_retry_count_zero_first_attempt(self):
        handler = RealP5Handler(tracer=_FakeTracer(round_end_count=0))
        assert handler._p5_retry_count("p", "r") == 0

    def test_no_query_tracer_falls_back_to_zero(self):
        handler = RealP5Handler(tracer=MagicMock(spec=[]))   # spec=[] → 无 query 属性
        assert handler._p5_retry_count("p", "r") == 0

    def test_review_uses_real_retry_count_and_exhausts(self):
        """command_failed + 已重试 2 次 → 路由到 retry_exhausted（Gate 升级），不再永不耗尽。"""
        handler = RealP5Handler(tracer=_FakeTracer(round_end_count=2))
        result = {"status": "blocked", "project_id": "p", "run_id": "r",
                  "reason": "命令执行失败",
                  "conditional_results": [{"slot_id": "run_verified", "status": "validation_failed",
                                           "command": "dotnet run"}]}
        review = handler.review(result)
        assert review.passed is False
        # retry_count=2 == max_retries → 升级 Gate（非无限重试）
        assert result.get("gate_required") is True

    def test_review_allows_bounded_retry_when_count_low(self):
        handler = RealP5Handler(tracer=_FakeTracer(round_end_count=0))
        result = {"status": "blocked", "project_id": "p", "run_id": "r",
                  "reason": "命令执行失败",
                  "conditional_results": [{"slot_id": "run_verified", "status": "validation_failed",
                                           "command": "dotnet run"}]}
        review = handler.review(result)
        assert any("有界重试" in r for r in review.recommendations)


# ── REC: P6 交付读【真 P5 验证计划（含十槽位）】，非 P4 执行摘要 ─────────────────

class _CapturingP6Service:
    def __init__(self):
        self.captured_p5_plan = None

    def generate_delivery_package(self, project_id, run_id, p5_plan=None):
        self.captured_p5_plan = p5_plan
        return SimpleNamespace(
            delivery_manifest={"contents": {"output_code_count": 1, "patch_count": 1}},
            risk_manifest={"risk_count": 0, "has_blocking": False, "blocking": False, "risks": []},
            hash_manifest={}, p6_delivery_report={},
            p5_validation_report={"evidence_refs": []},
            indexes={}, desensitization_ok=True, desensitization_issues=[])


class TestP6ReadsRealP5Plan:

    def _write_p5_report(self, ws):
        (ws / "artifacts").mkdir(parents=True, exist_ok=True)
        report = {
            "can_be_completed": True,
            "validation_plan": {
                "project_id": "p", "run_id": "r",
                "slots": [
                    {"slot_id": "output_code_exists", "status": "validated"},
                    {"slot_id": "static_check", "status": "evidence_gap",
                     "evidence_gap_reason": "无静态检查命令"},
                ],
                "can_be_completed": True,
            },
        }
        (ws / "artifacts" / "p5_validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return report

    def test_load_p5_report_returns_validation_plan_with_slots(self, tmp_path):
        ws = tmp_path / "projects" / "p"
        report = self._write_p5_report(ws)
        with patch("app.services.workspace_service.workspace_path", return_value=ws):
            loaded = RealP6Handler._load_p5_report("p", "r")
        assert loaded.get("can_be_completed") is True
        assert "slots" in loaded["validation_plan"]        # 真 P5 计划含十槽位状态

    def test_p6_passes_real_p5_plan_not_p4_summary(self, tmp_path):
        """REC 核心：p5_plan == 真 p5_validation_report.validation_plan（含 slots），非 p4_execution_summary。"""
        ws = tmp_path / "projects" / "p"
        report = self._write_p5_report(ws)
        fake_p6 = _CapturingP6Service()
        handler = RealP6Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p6_delivery_service=fake_p6)
        handler._services = lambda: MagicMock()            # gate_service.create → MagicMock

        # P5InputService.read_p4_input 返回非阻断输入（含 p4_execution_summary 作对照——不应被当 p5_plan）
        fake_input = MagicMock()
        fake_input.read_p4_input.return_value = _base_input()

        with patch("app.services.p5_input_service.P5InputService", return_value=fake_input), \
             patch("app.services.workspace_service.workspace_path", return_value=ws):
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        assert result["status"] == "completed"
        # 传给交付服务的 p5_plan 是【真 P5 计划】（有 slots），不是 P4 摘要（无 slots）
        assert fake_p6.captured_p5_plan is not None
        assert "slots" in fake_p6.captured_p5_plan
        assert fake_p6.captured_p5_plan == report["validation_plan"]
        assert "graph_status" not in fake_p6.captured_p5_plan   # 非 p4_execution_summary
