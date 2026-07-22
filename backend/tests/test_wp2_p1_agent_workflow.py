"""R17.5 P1 — P1 建档 LLM Agent 工作流真实单测（非 mock，D-097）。

架构改动说明（R17.5 P1，非"真 bug"）：P1 从"确定性 FullStackProfiler 当主线（识别零 LLM，
误判 .NET 为 JS）"重构为"目标驱动 Node Worker Agent 建档"——确定性【只做采集】
（FullStackProfiler.collect_facts 列文件/结构/大小写不敏感依赖配置测试候选/脱敏原文），识别
（技术栈/依赖/入口/配置/infra/测试断言/盲区）【一律 LLM】（ProfilingService 经 ModelGateway），
并复用 P0 已 LLM 产出的接入识别结论（WP-2，解 P1-ARCH-1），新增 D-106 原始验收基准捕获（WP-B）。
因此原批 A 的"profiler 作确定性 Tool + fact-evidence map + flaky profiler rework"单测已随架构
改动失效——本文件按 P0 LLM 范式（test_wp2b）重写：注入 gateway 测 LLM/blocked 路径，claim-evidence
（produced_by=llm）、复用 P0 primary_language、原始验收基准产出、无 Key 诚实 blocked。

不使用 mock 生产路径：gateway/execution_provider 注入仅为可测性（同 IntakeService/AssessmentService 范式）。
"""

import json
from types import SimpleNamespace

import pytest

from app.graph import nodes
from app.graph.checkpoint import reset_checkpointer_for_test
from app.graph.runtime import get_flow_runtime, reset_flow_runtime_for_test
from app.graph.stage_handlers import RealP0Handler, RealP1Handler
from app.services import workspace_service
from app.services.work_agent import WorkAgent
from app.services.validation_agent import ValidationAgent
from app.services.profiling_service import ProfilingService
from app.services.acceptance_baseline_service import AcceptanceBaselineService
from app.services.intake_service import IntakeService


# ── LLM 输出样例（样本值 LLM 生成；此处为 fake gateway 回放） ─────────────────
_P0_IDENT = json.dumps({
    "primary_language": "Python",
    "detected_stack": ["Python", "TypeScript"],
    "key_files": [{"path": "app.py", "why_key": "应用入口"}],
    "availability_classification": {"class": "B", "label": "源码可用未验证运行", "reasoning": ["源码已物化"]},
    "entry_points": [{"path": "app.py", "kind": "python_entry"}],
    "uncertainty": [{"area": "运行环境", "detail": "未验证实跑"}],
}, ensure_ascii=False)

_P1_IDENT = json.dumps({
    "tech_stack": {
        "primary_language": "Python",   # 复用 P0 结论（不改判）
        "languages": [{"language": "Python", "file_count": 1, "confidence": "high"},
                      {"language": "TypeScript", "file_count": 1, "confidence": "medium"}],
        "frameworks": [{"framework": "Next.js", "confidence": "high", "evidence": "package.json deps"}],
        "build_systems": [{"build_system": "npm", "file": "package.json"}],
    },
    "dependency_draft": {"dependencies": [{"name": "react", "version": "^18", "source": "package.json", "type": "npm"},
                                          {"name": "next", "version": "^14", "source": "package.json", "type": "npm"}],
                         "total": 2, "note": "从 package.json 解析"},
    "entry_points": {"entry_points": [{"path": "app.py", "kind": "python_entry", "why": "顶层脚本入口"}], "count": 1},
    "config_inventory": {"configs": [{"path": "package.json", "config_type": "npm-manifest", "purpose": "依赖/脚本"}], "count": 1},
    "infra_clues": {"infra": [], "count": 0},
    "test_inventory": {"has_tests": False, "test_directories": [], "test_frameworks": [],
                       "assertions_understood": [], "note": "未发现测试"},
    "module_structure": {"modules": [{"path": "root", "role": "单模块应用"}], "is_monorepo": False},
    "uncertainty_manifest": {"evidence_gaps": [{"area": "测试", "detail": "无测试，回归基线需特征化测试"},
                                               {"area": "运行环境", "detail": "目标环境未验证"}],
                             "total_gaps": 2},
}, ensure_ascii=False)

_BASELINE = json.dumps({
    "test_assertions": [],
    "missing_test_paths": [{"path_or_module": "app.py", "why_critical": "唯一入口无测试"}],
    "characterization_specs": [{"target": "app.py::main", "input": "run", "expected_output_anchor": "prints 'hi'",
                                "rationale": "锁定原始行为供迁移后回归"}],
    "dynamic_golden_plan": {"runnable_on_platform": True, "language": "bash",
                            "run_command": "python app.py", "working_subdir": "",
                            "reason": "Python 脚本本平台可直接跑", "needs_env": {}},
}, ensure_ascii=False)


class _P1Gateway:
    """Key-present fake gateway：按 messages 内容分派 P0 识别 / P1 建档 / 原始基准 / 验收 verdict。
    同 IntakeService/AssessmentService 的 fake gateway 可测性范式（无 mock 生产路径）。"""

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
            return {"status": "completed", "model": "fake-model", "content": _P1_IDENT}
        return {"status": "completed", "model": "fake-model", "content": _P0_IDENT}


class _NoKeyGateway:
    def get_status(self):
        return SimpleNamespace(overall_status="not_configured")

    def stage_model_readiness(self, **kw):
        return {"available": False, "capability_ok": False,
                "reason": "无任一已配置且具备有效凭据的模型可用",
                "attempted_chain": [{"provider": "x", "outcome": "credential_missing"}],
                "user_actions": [{"action": "configure_key"}]}


class _FakeExec:
    """Fake ExecutionProvider：动态黄金捕获用（单测不跑真实子进程；真跑证据见施工记录真跑段）。"""
    async def execute(self, code, language="bash", timeout=30, model=None, cwd=None):
        return {"exit_code": 0, "stdout": "hi\n", "stderr": "", "provider": "fake",
                "execution_mode": "fake", "elapsed_ms": 1, "blocked": False}


def _seed_source(project_id: str) -> None:
    workspace_service.init_workspace(project_id)
    src = workspace_service.workspace_path(project_id) / "source"
    (src / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"react": "^18", "next": "^14"}}),
        encoding="utf-8")
    (src / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (src / "index.tsx").write_text("export default 1\n", encoding="utf-8")


def _seed_p0_upstream(project_id: str, primary="Python") -> None:
    """WP-2：写 P0 上游产物（intake_report + source_index）供 P1 复用（不重算）。D-107: artifacts/p0/。"""
    from app.services.source_materializer import generate_source_index
    generate_source_index(project_id, source_type="local_dir")
    art = workspace_service.workspace_path(project_id) / "artifacts" / "p0"
    art.mkdir(parents=True, exist_ok=True)
    intake = {"artifact_type": "intake_report", "produced_by": "llm_node_worker_agent",
              "primary_language": primary, "migration_target": {"os": "openEuler", "cpu": "ARM64"},
              "identification": {"primary_language": primary, "detected_stack": [primary, "TypeScript"],
                                 "availability_classification": {"class": "B"}}}
    (art / "intake_report.json").write_text(json.dumps(intake, ensure_ascii=False), encoding="utf-8")


def _p1_handler(gw=None, exec_provider=None) -> RealP1Handler:
    gw = gw or _P1Gateway()
    return RealP1Handler(
        profiling_service=ProfilingService(gateway=gw),
        baseline_service=AcceptanceBaselineService(gateway=gw,
                                                   execution_provider=exec_provider or _FakeExec()))


def test_work_agent_dynamic_plan_reflects_real_facts(isolated_data):
    """WorkAgent 动态工作计划基于真实项目事实（file_count/候选栈），非静态模板。"""
    pid = "wp2-plan"
    _seed_source(pid)
    wa = WorkAgent("p1", pid, run_id="r1")
    plan_ref = wa.build_work_plan({"source_type": "manual"})
    plan = json.loads((workspace_service.workspace_path(pid) / plan_ref).read_text("utf-8"))
    assert plan["generated_by"] == "work_agent"
    based = plan["based_on"]
    assert based["file_count"] == 3
    assert any("Python" in s or "TSX" in s or "JavaScript" in s for s in based["detected_stack"])
    assert "package.json" in based["build_files"]
    assert "3 源文件" in plan["goal"]


async def test_p1_llm_profiling_produces_claim_evidence_and_reuses_p0(isolated_data):
    """P1 改 LLM 建档：注入 gateway → 采集事实包 + 复用 P0 识别 → LLM 产建档识别字段 →
    claim-evidence（produced_by=llm）绑定真实产物；主语言复用 P0（不再 P0=X/P1=Y 分歧）。"""
    pid = "wp2-exec"
    _seed_source(pid)
    _seed_p0_upstream(pid, primary="Python")
    handler = _p1_handler()
    wa = WorkAgent("p1", pid, run_id="r1", handler=handler)
    result = await wa.execute({"source_type": "manual", "project_id": pid})

    assert result["status"] == "completed"
    assert result["reviewer"] == "work_agent"
    # handler 作为 LLM Tool 被调（非 full_stack_profiler.profile）
    assert any(tc["tool"] == "p1_handler.execute" for tc in result["tool_calls"])
    # 复用 P0 结论：P1 主语言 == P0 主语言（解 P1-ARCH-1）
    assert result["primary_language"] == "Python"
    assert result["reused_p0_primary_language"] == "Python"
    # claim-evidence map（LLM claim，非 deterministic_tool）
    cem = json.loads((workspace_service.workspace_path(pid) /
                      result["claim_evidence_map_ref"]).read_text("utf-8"))
    assert cem["map_type"] == "claim_evidence"
    assert cem["entries"] and all(e["produced_by"] == "llm" for e in cem["entries"])
    assert all(e["bindings"]["artifact_refs"] for e in cem["entries"])
    # LLM 建档识别产物真实落盘（produced_by 非 deterministic；D-107: artifacts/p1/）
    art = workspace_service.workspace_path(pid) / "artifacts" / "p1"
    tech = json.loads((art / "tech_stack.json").read_text("utf-8"))
    assert tech["primary_language"] == "Python"
    # WP-B：原始验收基准 acceptance_baseline.json 产出（静态 + 动态黄金真捕获）
    baseline = json.loads((art / "acceptance_baseline.json").read_text("utf-8"))
    assert baseline["static_baseline"]["characterization_specs"], "静态基线含特征化规格（必产）"
    assert baseline["dynamic_golden"]["captured"] is True, "动态黄金真跑捕获"
    assert baseline["dynamic_golden"]["exit_code"] == 0
    # 盲区非 0-gap（LLM 主动发声）
    unc = json.loads((art / "uncertainty_manifest.json").read_text("utf-8"))
    assert unc.get("total_gaps", 0) >= 1, "uncertainty 非 0-gap（修 0-gap 病灶）"
    # AET 可查询 p1_claim
    from app.services.aet_service import AETService
    evs = AETService(None).list_evidence(pid, stage="p1")
    assert any(ev.get("type") == "p1_claim" for ev in evs)


async def test_p1_validation_agent_independent_pass(isolated_data):
    """独立 ValidationAgent（LLM 验收路径）通过：独立 agent_id + read_from_disk_only。"""
    pid = "wp2-val"
    _seed_source(pid)
    _seed_p0_upstream(pid)
    handler = _p1_handler()
    gw = _P1Gateway()
    wa = WorkAgent("p1", pid, run_id="r1", handler=handler)
    result = await wa.execute({"source_type": "manual", "project_id": pid})

    va = ValidationAgent("p1", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)
    assert rr.passed, f"P1 独立验收应通过, issues={rr.issues}"
    vres = va.last_result
    assert vres.read_from_disk_only is True
    assert vres.verdict in ("accepted", "accepted_with_warning")
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p1" / "p1_validation.json").exists()


async def test_p1_llm_profiling_blocked_no_key(isolated_data):
    """WP-3：P1 建档识别无有效 Key → 诚实 blocked（不回退确定性冒充 completed，D-097）。"""
    pid = "wp2-nokey"
    _seed_source(pid)
    _seed_p0_upstream(pid)
    handler = _p1_handler(gw=_NoKeyGateway())
    wa = WorkAgent("p1", pid, run_id="r1", handler=handler)
    result = await wa.execute({"source_type": "manual", "project_id": pid})
    assert result["status"] == "blocked", "无 Key 诚实 blocked，不伪造 completed"
    assert result.get("attempted_chain"), "透传已尝试/候选模型链路"
    va = ValidationAgent("p1", pid, run_id="r1", handler=handler)
    rr = va.validate(result)
    assert not rr.passed, "无 Key blocked → 独立验收不通过（诚实升级 Gate）"


async def test_p1_rework_then_pass(isolated_data):
    """request_changes 打回实证：首轮 blocked（无 Key）→ rework → 有 Key 重跑 → 通过。
    全链路真实：StageLoop → ReviewPass → WorkAgent(handler LLM Tool) → ValidationAgent。"""
    from app.graph.stage_loop import StageLoop
    pid = "wp2-rework"
    _seed_source(pid)
    _seed_p0_upstream(pid)

    class _FlipGateway(_P1Gateway):
        """首轮 readiness 不可用（无 Key）→ blocked；第二轮可用 → 完成（真实 rework 场景）。"""
        def __init__(self):
            self.readiness_calls = 0
        def stage_model_readiness(self, **kw):
            self.readiness_calls += 1
            if self.readiness_calls <= 1:
                return {"available": False, "capability_ok": False, "reason": "首轮无 Key",
                        "attempted_chain": [{"provider": "x", "outcome": "credential_missing"}],
                        "user_actions": [{"action": "configure_key"}]}
            return {"available": True, "capability_ok": True, "reason": "",
                    "attempted_chain": [], "user_actions": []}

    gw = _FlipGateway()
    handler = _p1_handler(gw=gw)
    wa = WorkAgent("p1", pid, run_id="r1", handler=handler)
    va = ValidationAgent("p1", pid, run_id="r1", handler=handler, gateway=gw)

    def _review(work_result):
        rr = va.validate(work_result)
        if not rr.passed:
            wa.set_rework_feedback({"issues": rr.issues, "recommendations": rr.recommendations})
        return rr

    loop = StageLoop(pid, "p1", max_rounds=2, run_id="r1")
    res = await loop.run(
        goal="P1 建档", acceptance_criteria=["占位"], planned_actions=["占位"],
        execute_fn=lambda: wa.execute({"source_type": "manual", "project_id": pid}),
        review_fn=_review,
    )
    assert res.passed, "rework 后第二轮应通过"
    rounds = res.rounds
    assert len(rounds) == 2
    assert rounds[0]["status"] == "retry", "首轮 blocked → 验收未通过（rework）"
    assert rounds[1]["status"] == "passed", "第二轮（有 Key）通过"


async def test_p1_plan_presentation_carries_dynamic_plan(isolated_data):
    """make_work_node P1 plan_only 分支：plan_presentation gate 挂 WorkAgent 动态工作计划。"""
    pid = "wp2-plan-node"
    _seed_source(pid)

    class RecordingGate:
        def __init__(self):
            self.created = []
        def create(self, *, project_id, run_id, stage, artifact_refs,
                   gate_type="stage_promotion", metadata=None):
            gid = f"gate-{stage}-{gate_type}-{len(self.created)}"
            self.created.append({"gid": gid, "stage": stage, "gate_type": gate_type,
                                 "artifact_refs": list(artifact_refs)})
            return gid
        def decide(self, *, gate_id, decision):
            pass

    nodes.clear_handlers()
    nodes.register_handler("p1", _p1_handler())
    gb = RecordingGate()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    try:
        work_node = nodes.make_work_node("p1")
        state = {"project_id": pid, "run_id": "run-plan", "source_type": "manual",
                 "execution_mode": "manual", "stage_status": {"p1": "in_progress"}}
        upd = await work_node(state)
        assert upd["stage_status"]["p1"] == "waiting_plan_presentation"
        plan_gate = gb.created[0]
        assert plan_gate["gate_type"] == "plan_presentation"
        assert any(r.endswith("p1_work_plan.json") for r in plan_gate["artifact_refs"])
        plan = json.loads((workspace_service.workspace_path(pid) / "artifacts" / "p1" /
                           "p1_work_plan.json").read_text("utf-8"))
        assert plan["generated_by"] == "work_agent"
        assert plan["based_on"]["file_count"] == 3
    finally:
        nodes.clear_handlers()
        nodes.set_gate_backend(None)


async def test_p1_end_to_end_agent_workflow_in_graph(isolated_data, monkeypatch):
    """make_work_node P0→P1 端到端（注入 gateway）：P1 promotion Gate 挂 Agent 侧审核材料
    （work_plan/gate_brief/validation/claim_evidence_map），Gate Brief 内容真实。"""
    import tempfile
    from pathlib import Path
    ckpt = Path(tempfile.mkdtemp()) / "ckpt.sqlite"
    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path", lambda: ckpt)

    pid = "wp2-e2e"
    _seed_source(pid)

    class RecordingGate:
        def __init__(self):
            self.created = []
        def create(self, *, project_id, run_id, stage, artifact_refs,
                   gate_type="stage_promotion", metadata=None):
            gid = f"gate-{stage}-{gate_type}-{len(self.created)}"
            self.created.append({"gid": gid, "stage": stage, "gate_type": gate_type,
                                 "artifact_refs": list(artifact_refs)})
            return gid
        def decide(self, *, gate_id, decision):
            pass

    gw = _P1Gateway()
    await reset_checkpointer_for_test()
    reset_flow_runtime_for_test()
    nodes.clear_handlers()
    nodes.register_handler("p0", RealP0Handler(intake_service=IntakeService(gateway=gw)))
    nodes.register_handler("p1", _p1_handler(gw=gw))
    gb = RecordingGate()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    try:
        rt = get_flow_runtime()
        init = {"run_id": "run-e2e", "project_id": pid, "run_goal": "e2e",
                "run_status": "running", "source_type": "manual",
                "stage_status": {"p0": "in_progress"}}
        s = await rt.start("run-e2e", init)
        assert "__interrupt__" in s
        p1_promo = None
        for _ in range(8):
            s = await rt.resume("run-e2e", "approve")
            for c in gb.created:
                if c["stage"] == "p1" and c["gate_type"] == "stage_promotion":
                    p1_promo = c
            if p1_promo:
                break
            if "__interrupt__" not in s:
                break

        assert p1_promo is not None, "P1 promotion gate 应创建"
        refs = p1_promo["artifact_refs"]
        assert any(r.endswith("p1_work_plan.json") for r in refs)
        assert any(r.endswith("p1_gate_brief.json") for r in refs)
        assert any(r.endswith("p1_validation.json") for r in refs)
        assert any(r.endswith("p1_claim_evidence_map.json") for r in refs)

        gb_ref = next(r for r in refs if r.endswith("p1_gate_brief.json"))
        brief = json.loads((workspace_service.workspace_path(pid) / gb_ref).read_text("utf-8"))
        assert brief["what_happened"]
        assert brief["validation_verdict"]["passed"] is True
    finally:
        await reset_checkpointer_for_test()
        reset_flow_runtime_for_test()
        nodes.clear_handlers()
        nodes.set_gate_backend(None)
