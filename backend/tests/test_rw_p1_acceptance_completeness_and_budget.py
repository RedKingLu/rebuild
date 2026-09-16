"""V26.2 总验收真跑返工 · 批次 B（第二部分）测试（D-05 / D-06 同族两处截断）。

覆盖：
  1. profiling_service / p5_verification_agent 的 env 可调 max_tokens/timeout 生效（取值 +
     实际透传给 run_stage_tool_loop 的调用）。
  2. D-05 核心：P1 独立验收（ValidationAgent）新增的领域产物集完整性 + acceptance_criteria
     第 4 条（盲区主动发声）核验——uncertainty_manifest 诚实占位时能被识别；8 项占位时
     issues 不为空；全部真实产出时不受影响（防回归）；单一次要占位不被惩罚成阶段失败。
  3. llm_semantic：raw 为空时不再报 completed，改报 evidence_gap。

不发起真实 LLM 调用：全部经注入的 fake gateway（无 call_stream → run_stage_tool_loop 单次
call fallback，同 test_wp2_p1_agent_workflow.py 既有范式）。
"""

from __future__ import annotations

import importlib
import json
import os
from types import SimpleNamespace

import pytest

from app.graph.stage_handlers import RealP1Handler
from app.services import workspace_service
from app.services.validation_agent import ValidationAgent
from app.services.work_agent import WorkAgent
from app.services.profiling_service import ProfilingService
from app.services.acceptance_baseline_service import AcceptanceBaselineService


# ══════════════════════════════════════════════════════════════════════════
# §1 profiling_service / p5_verification_agent：env 可调预算生效
# ══════════════════════════════════════════════════════════════════════════

def _reload_with_env(module, env: dict):
    """临时设置 env 后 reload 模块，返回 (旧值字典, reload 后的模块)；调用方须在 finally 里
    还原并再 reload 一次，避免污染同进程内其它测试（模块级常量在 import 时读一次 env）。"""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    importlib.reload(module)
    return old


def _restore_env(module, old: dict):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    importlib.reload(module)


def test_profiling_max_tokens_and_timeout_env_tunable():
    """profiling_service 的预算/超时经 env 可调（复用 acceptance_baseline_service 范式）。"""
    from app.services import profiling_service as ps
    old = _reload_with_env(ps, {"P1_PROFILING_MAX_TOKENS": "12345", "P1_PROFILING_TIMEOUT": "99"})
    try:
        assert ps._PROFILING_MAX_TOKENS == 12345
        assert ps._PROFILING_TIMEOUT == 99.0
    finally:
        _restore_env(ps, old)
        # V26.2 返工批次二（用户裁决 Q-B2-1 / Q-B2-5，2026-09-16）：默认值口径由"32768"改为
        # **不设**（None = 无平台侧上限）。旋钮仍然生效（上面的断言即证），只是不设时不再有
        # 平台天花板。此处是**机械事实随实现前移**，不是放宽守卫。
        assert ps._PROFILING_MAX_TOKENS is None
        assert ps._PROFILING_TIMEOUT == 300.0


def test_p5_verification_max_tokens_and_timeout_env_tunable():
    """p5_verification_agent 的预算/超时经 env 可调（同一范式）。"""
    from app.services import p5_verification_agent as p5va
    old = _reload_with_env(p5va, {"P5_VERIFICATION_MAX_TOKENS": "23456",
                                  "P5_VERIFICATION_TIMEOUT": "77"})
    try:
        assert p5va._P5_VERIFICATION_MAX_TOKENS == 23456
        assert p5va._P5_VERIFICATION_TIMEOUT == 77.0
    finally:
        _restore_env(p5va, old)
        # V26.2 返工批次二：默认口径改为**不设上限**（同上，机械事实随实现前移）。
        assert p5va._P5_VERIFICATION_MAX_TOKENS is None
        assert p5va._P5_VERIFICATION_TIMEOUT == 240.0


class _ReadyGateway:
    """仅供预算透传测试：readiness 可用，无 call_stream（走单次 call fallback）。"""
    def get_status(self):
        return SimpleNamespace(overall_status="available")

    def stage_model_readiness(self, **kw):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, **kw):
        return {"status": "completed", "model": "fake-model", "content": "{}"}


async def test_profiling_profile_uses_configured_budget(isolated_data, monkeypatch):
    """ProfilingService.profile() 把配置好的 max_tokens/timeout 真实传给 run_stage_tool_loop
    （而非继续硬编码 16384）。"""
    from app.services import profiling_service as ps

    captured: dict = {}

    async def _fake_loop(gw, **kwargs):
        captured.update(kwargs)
        return {"status": "completed", "content": "{}", "model_used": "fake-model"}

    monkeypatch.setattr("app.services.stage_agent_loop.run_stage_tool_loop", _fake_loop)
    svc = ps.ProfilingService(gateway=_ReadyGateway())
    await svc.profile("proj-budget", facts={"file_count": 1}, upstream={})
    assert captured.get("max_tokens") == ps._PROFILING_MAX_TOKENS
    assert captured.get("timeout") == ps._PROFILING_TIMEOUT


async def test_p5_verification_interpret_uses_configured_budget(isolated_data, monkeypatch):
    """P5VerificationAgent.interpret() 把配置好的 max_tokens/timeout 真实传给
    run_stage_tool_loop（而非继续硬编码 8192）。"""
    from app.services import p5_verification_agent as p5va

    captured: dict = {}

    async def _fake_loop(gw, **kwargs):
        captured.update(kwargs)
        return {"status": "completed", "content": "{}", "model_used": "fake-model"}

    monkeypatch.setattr("app.services.stage_agent_loop.run_stage_tool_loop", _fake_loop)
    agent = p5va.P5VerificationAgent(gateway=_ReadyGateway())
    await agent.interpret("proj-budget", deterministic_facts={"project_id": "proj-budget"})
    assert captured.get("max_tokens") == p5va._P5_VERIFICATION_MAX_TOKENS
    assert captured.get("timeout") == p5va._P5_VERIFICATION_TIMEOUT


# ══════════════════════════════════════════════════════════════════════════
# §2 解析失败可诊断信息（D-06 同族：响应长度 + 疑似截断标志）
# ══════════════════════════════════════════════════════════════════════════

def test_profiling_parse_failure_records_truncation_diagnosis():
    """profiling_service._parse 对截断响应给出可诊断信息（非只报 parse_error）。"""
    from app.services.profiling_service import ProfilingService
    svc = ProfilingService()
    truncated = '{"tech_stack": {"primary_language": "C#", "languages": [{"language": "C#"'
    parsed = svc._parse(truncated, model_used="fake-model")
    assert parsed.get("parse_error") is True
    diag = parsed.get("parse_diagnosis") or {}
    assert diag.get("suspected_truncation") is True
    assert diag.get("content_len") == len(truncated)
    assert diag.get("max_tokens") and diag.get("timeout_s")
    assert "P1_PROFILING_MAX_TOKENS" in diag.get("env_knobs", "")


def test_p5_verification_parse_failure_records_truncation_diagnosis():
    """p5_verification_agent._parse 对截断响应给出可诊断信息。"""
    from app.services.p5_verification_agent import P5VerificationAgent
    agent = P5VerificationAgent()
    truncated = '{"validation_strategy": {"applicable_dimensions": ["build", "test"'
    parsed = agent._parse(truncated)
    vs = parsed.get("validation_strategy") or {}
    assert vs.get("parse_error") is True
    diag = vs.get("parse_diagnosis") or {}
    assert diag.get("suspected_truncation") is True
    assert diag.get("content_len") == len(truncated)
    assert "P5_VERIFICATION_MAX_TOKENS" in diag.get("env_knobs", "")


# ══════════════════════════════════════════════════════════════════════════
# §3 D-05：P1 独立验收 —— acceptance_criteria 第 4 条 + 领域产物集完整性
# ══════════════════════════════════════════════════════════════════════════

_P0_IDENT = json.dumps({
    "primary_language": "C#",
    "detected_stack": ["C#", "ASP.NET"],
    "key_files": [{"path": "Global.asax", "why_key": "应用入口"}],
    "availability_classification": {"class": "B", "label": "源码可用未验证运行", "reasoning": ["源码已物化"]},
    "entry_points": [{"path": "Global.asax", "kind": "dotnet_entry"}],
    "uncertainty": [{"area": "运行环境", "detail": "未验证实跑"}],
}, ensure_ascii=False)

_BASELINE = json.dumps({
    "test_assertions": [],
    "missing_test_paths": [{"path_or_module": "Global.asax", "why_critical": "唯一入口无测试"}],
    "characterization_specs": [{"target": "Global.asax::Application_Start", "input": "启动",
                                "expected_output_anchor": "初始化完成",
                                "rationale": "锁定原始行为供迁移后回归"}],
    "dynamic_golden_plan": {"runnable_on_platform": False, "language": "csharp",
                            "run_command": "", "working_subdir": "",
                            "reason": ".NET 需 Windows/SQL Server 环境，本平台不可直接跑",
                            "needs_env": {"kind": "dotnet-framework", "note": "需 Windows 环境"}},
}, ensure_ascii=False)

# 完整（8 项全真实）建档识别，供各场景按需删减若干键模拟"LLM 未产出"。
_P1_IDENT_FULL = {
    "tech_stack": {
        "primary_language": "C#",
        "languages": [{"language": "C#", "file_count": 84, "confidence": "high"}],
        "frameworks": [{"framework": "ASP.NET WebForms", "confidence": "high", "evidence": "*.aspx"}],
        "build_systems": [{"build_system": "msbuild", "file": "*.csproj"}],
    },
    "dependency_draft": {"dependencies": [{"name": "Newtonsoft.Json", "version": "12.0.3",
                                           "source": "packages.config", "type": "nuget"}],
                         "total": 1, "note": "从 packages.config 解析"},
    "entry_points": {"entry_points": [{"path": "Global.asax", "kind": "dotnet_entry",
                                       "why": "应用生命周期入口"}], "count": 1},
    "config_inventory": {"configs": [{"path": "Web.config", "config_type": "dotnet-config",
                                      "purpose": "运行时配置"}], "count": 1},
    "infra_clues": {"infra": [{"infra_type": "sqlserver", "evidence": "connectionStrings",
                               "confidence": "high"}], "count": 1},
    "test_inventory": {"has_tests": False, "test_directories": [], "test_frameworks": [],
                       "assertions_understood": [], "note": "未发现测试"},
    "module_structure": {"modules": [{"path": "root", "role": "单体 WebForms 应用"}],
                         "is_monorepo": False},
    "uncertainty_manifest": {"evidence_gaps": [{"area": "测试", "detail": "无测试，回归基线需特征化测试"},
                                               {"area": "运行环境", "detail": ".NET 目标环境未验证"}],
                             "total_gaps": 2},
}


class _ScenarioGateway:
    """P1 建档 fake gateway：可按场景省略 identification 中若干键，从而在真实 stage_handlers
    写盘路径上如实复现「LLM 未产出 → 诚实占位」（不改 stage_handlers.py，只改造 LLM 响应内容，
    让平台既有逻辑自然触发）。同 test_wp2_p1_agent_workflow.py 的 fake gateway 范式（无
    call_stream → run_stage_tool_loop 单次 call fallback；不发起真实 LLM 调用）。"""

    def __init__(self, omit_keys: list[str] | None = None):
        self._omit = set(omit_keys or [])

    def get_status(self):
        return SimpleNamespace(overall_status="available")

    def stage_model_readiness(self, **kwargs):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, *, messages=None, **kw):
        text = json.dumps(messages, ensure_ascii=False) if messages else ""
        if "verdict" in text:
            return {"status": "completed", "model": "m",
                    "content": json.dumps({"verdict": "accepted", "reason": "建档识别结构合理"})}
        if "验收基准" in text or "dynamic_golden_plan" in text:
            return {"status": "completed", "model": "fake-model", "content": _BASELINE}
        if "建档识别 Agent" in text or "tech_stack" in text:
            ident = {k: v for k, v in _P1_IDENT_FULL.items() if k not in self._omit}
            return {"status": "completed", "model": "fake-model",
                    "content": json.dumps(ident, ensure_ascii=False)}
        return {"status": "completed", "model": "fake-model", "content": _P0_IDENT}


def _seed_source(project_id: str) -> None:
    workspace_service.init_workspace(project_id)
    src = workspace_service.workspace_path(project_id) / "source"
    (src / "Global.asax").write_text("<%@ Application %>\n", encoding="utf-8")
    (src / "Web.config").write_text("<configuration/>\n", encoding="utf-8")


def _seed_p0_upstream(project_id: str) -> None:
    from app.services.source_materializer import generate_source_index
    generate_source_index(project_id, source_type="local_dir")
    art = workspace_service.workspace_path(project_id) / "artifacts" / "p0"
    art.mkdir(parents=True, exist_ok=True)
    intake = {"artifact_type": "intake_report", "produced_by": "llm_node_worker_agent",
              "primary_language": "C#", "migration_target": {"os": "openEuler", "cpu": "ARM64"},
              "identification": {"primary_language": "C#", "detected_stack": ["C#", "ASP.NET"],
                                 "availability_classification": {"class": "B"}}}
    (art / "intake_report.json").write_text(json.dumps(intake, ensure_ascii=False), encoding="utf-8")


async def _run_p1(pid: str, omit_keys: list[str] | None = None):
    """真实跑一遍 P1（WorkAgent + RealP1Handler + fake gateway），返回 (work_result, gateway)。"""
    _seed_source(pid)
    _seed_p0_upstream(pid)
    gw = _ScenarioGateway(omit_keys=omit_keys)
    handler = RealP1Handler(
        profiling_service=ProfilingService(gateway=gw),
        baseline_service=AcceptanceBaselineService(gateway=gw, execution_provider=_FakeExec()))
    wa = WorkAgent("p1", pid, run_id="r1", handler=handler)
    result = await wa.execute({"source_type": "manual", "project_id": pid})
    assert result["status"] == "completed", f"P1 应 completed，实际={result}"
    return result, gw, handler


class _FakeExec:
    async def execute(self, code, language="bash", timeout=30, model=None, cwd=None):
        return {"exit_code": 0, "stdout": "", "stderr": "", "provider": "fake",
                "execution_mode": "fake", "elapsed_ms": 1, "blocked": False}


async def test_d05_all_domain_artifacts_present_no_regression(isolated_data):
    """防回归：8 项识别产物全部真实产出时，新检查不改变既有验收结论（不误报问题）。"""
    pid = "d05-full"
    result, gw, handler = await _run_p1(pid)
    va = ValidationAgent("p1", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)
    assert rr.passed, f"全部真实产出时应通过，issues={rr.issues}"
    assert not any(i.get("type") in ("p1_domain_artifacts_incomplete", "acceptance_criterion_unmet",
                                     "p1_identification_major_gap") for i in rr.issues), \
        f"不应误报领域产物不完整，issues={rr.issues}"
    checks = va.last_result.checks
    completeness = [c for c in checks if "领域产物集完整性" in c.get("item", "")]
    assert completeness and completeness[0]["passed"] is True


async def test_d05_uncertainty_manifest_placeholder_flags_unmet_criterion(isolated_data):
    """D-05 核心：uncertainty_manifest 是「LLM 未产出」诚实占位时，验收报告能识别出
    acceptance_criteria 第 4 条（盲区主动发声）未达成，且不得 passed=true/issues=[]。"""
    pid = "d05-uncertainty"
    result, gw, handler = await _run_p1(pid, omit_keys=["uncertainty_manifest"])

    art = workspace_service.workspace_path(pid) / "artifacts" / "p1"
    unc = json.loads((art / "uncertainty_manifest.json").read_text("utf-8"))
    assert "LLM 未产出" in unc.get("identification_note", ""), "应落成诚实占位（未伪造）"

    va = ValidationAgent("p1", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)

    criterion_issues = [i for i in rr.issues if i.get("type") == "acceptance_criterion_unmet"]
    assert criterion_issues, f"应识别出 acceptance_criteria 未达成，issues={rr.issues}"
    assert "uncertainty_manifest" in criterion_issues[0]["detail"]
    assert "盲区" in criterion_issues[0]["detail"] or "uncertainty" in criterion_issues[0]["detail"].lower()
    # 不得 passed:true 且 issues:[] 同时出现（任务书硬性要求）——这里 issues 已非空；
    # 且本场景（命中 acceptance_criteria 明确点名的第 4 条）判定客观未达标 → passed=False。
    assert not rr.passed, "uncertainty_manifest 占位命中明确点名的验收标准 → 应判未通过（触发 rework）"
    assert va.last_result.verdict == "rework_required"


async def test_d05_eight_empty_artifacts_issues_not_empty(isolated_data):
    """D-05：8 个空产物存在时（真跑实测场景），验收 issues 不为空、不是"0 项问题"。"""
    pid = "d05-all-empty"
    all_keys = list(RealP1Handler._LLM_ARTIFACT_KEYS)
    result, gw, handler = await _run_p1(pid, omit_keys=all_keys)

    art = workspace_service.workspace_path(pid) / "artifacts" / "p1"
    for key in all_keys:
        data = json.loads((art / f"{key}.json").read_text("utf-8"))
        assert "LLM 未产出" in data.get("identification_note", ""), f"{key} 应为诚实占位"

    va = ValidationAgent("p1", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)
    assert rr.issues, "8 个空产物存在时 issues 不应为空（不得 0 项问题掩盖）"
    assert any(i.get("type") == "p1_domain_artifacts_incomplete" for i in rr.issues)
    assert any(i.get("type") == "p1_identification_major_gap" for i in rr.issues)
    assert any(i.get("type") == "acceptance_criterion_unmet" for i in rr.issues)
    assert not rr.passed, "8/8 全占位（整体性缺失）→ 应判未通过（触发 rework，非阶段 blocked/failed）"
    assert va.last_result.verdict == "rework_required"


async def test_d05_single_minor_placeholder_not_punished_as_stage_failure(isolated_data):
    """诚实降级本身不被本次修复惩罚：单一次要产物（非 uncertainty_manifest、未达半数阈值）
    占位时，验收如实记录（issues 可见），但不 flip passed——不代表阶段判定失败。"""
    pid = "d05-minor"
    result, gw, handler = await _run_p1(pid, omit_keys=["infra_clues"])

    art = workspace_service.workspace_path(pid) / "artifacts" / "p1"
    infra = json.loads((art / "infra_clues.json").read_text("utf-8"))
    assert "LLM 未产出" in infra.get("identification_note", "")

    va = ValidationAgent("p1", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)
    assert any(i.get("type") == "p1_domain_artifacts_incomplete" for i in rr.issues), \
        "单一次要占位仍须如实记入 issues（不得静默补全）"
    assert not any(i.get("type") in ("acceptance_criterion_unmet", "p1_identification_major_gap")
                   for i in rr.issues), "未命中明确点名标准、未达半数阈值 → 不应升级为硬性未达标"
    assert rr.passed, "单一次要产物「LLM 未产出」不代表阶段判定失败，只代表验收报告如实记录"


# ══════════════════════════════════════════════════════════════════════════
# §4 llm_semantic：raw 为空不再报 completed
# ══════════════════════════════════════════════════════════════════════════

class _EmptyContentGateway:
    def get_status(self):
        return SimpleNamespace(overall_status="available")

    def stage_model_readiness(self, **kw):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, *, messages=None, **kw):
        return {"status": "completed", "model": "m", "content": ""}


def test_llm_semantic_empty_raw_not_reported_completed(isolated_data):
    """D-05 附带修复：模型响应 completed 但正文为空时，_run_semantic_call 不应再报
    {"status":"completed","raw":""}——应诚实降级为 evidence_gap。"""
    va = ValidationAgent("p1", "proj-empty-sem", run_id="r1")
    gw = _EmptyContentGateway()
    sem = va._run_semantic_call(gw, "任意 prompt 内容（含 verdict 字样以贴合真实提示词形态）")
    assert sem["status"] == "evidence_gap", f"raw 为空不应报 completed，实际={sem}"
    assert sem["status"] != "completed"
