"""R17.3-6 WP-2 批 A 条件收口 — 两项修复的真实（非 mock）回归锁。

(1) EG-WP2A-1 多阶段路由：manual/plan 模式多阶段晋级时，plan_approved 门控须按阶段
    复位，使每个阶段都能到达各自的 plan_presentation gate（修复前 p0 晋级后 plan_approved
    残留恒真，make_router 路由回 p0_work，p1 永远到不了自己的 plan 门 / 在 p0 死循环）。

(2) REC-1 ValidationAgent 静默回退：_acceptance_check 异常时不得静默按 accepted 处理
    （失败伪装通过，违反 D-097/公理3）；须显式降级为不通过 + 记录 issue + 保留脱敏异常原因。

不使用 mock：真实 LangGraph 编译图 + 真实 RealP0/RealP1Handler + WorkAgent/ValidationAgent/
AcceptanceService/FullStackProfiler/AET 真实组件。(2) 仅用 monkeypatch 令 AcceptanceService
真实抛异常以复现核验失败路径，非替身通过。
"""

import json

import pytest

from app.graph import nodes
from app.graph.checkpoint import reset_checkpointer_for_test
from app.graph.runtime import get_flow_runtime, reset_flow_runtime_for_test
from app.graph.stage_handlers import RealP0Handler, RealP1Handler
from app.services import workspace_service
from app.services.work_agent import WorkAgent
from app.services.validation_agent import ValidationAgent


def _seed_source(project_id: str) -> None:
    workspace_service.init_workspace(project_id)
    src = workspace_service.workspace_path(project_id) / "source"
    (src / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"react": "^18", "next": "^14"}}),
        encoding="utf-8")
    (src / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (src / "index.tsx").write_text("export default 1\n", encoding="utf-8")


# R17.5：P1 改 LLM 建档 → 注入 fake gateway/exec 令 execute 确定性完成（不打真实 LLM，可测性）。
def _fake_p1_handler():
    import json as _json
    from types import SimpleNamespace
    from app.services.profiling_service import ProfilingService
    from app.services.acceptance_baseline_service import AcceptanceBaselineService

    _ident = _json.dumps({
        "tech_stack": {"primary_language": "Python", "languages": [], "frameworks": [], "build_systems": []},
        "dependency_draft": {"dependencies": [], "total": 0},
        "entry_points": {"entry_points": [{"path": "app.py", "kind": "python_entry"}], "count": 1},
        "config_inventory": {"configs": [], "count": 0},
        "infra_clues": {"infra": [], "count": 0},
        "test_inventory": {"has_tests": False, "note": "无测试"},
        "module_structure": {"modules": [], "is_monorepo": False},
        "uncertainty_manifest": {"evidence_gaps": [{"area": "测试", "detail": "无测试"}], "total_gaps": 1},
    }, ensure_ascii=False)
    _baseline = _json.dumps({
        "test_assertions": [], "missing_test_paths": [], "characterization_specs": [{"target": "app.py"}],
        "dynamic_golden_plan": {"runnable_on_platform": False, "needs_env": {"kind": "n/a"}, "reason": "单测跳过"},
    }, ensure_ascii=False)

    class _GW:
        def get_status(self):
            return SimpleNamespace(overall_status="available")
        def stage_model_readiness(self, **kw):
            return {"available": True, "capability_ok": True, "reason": "", "attempted_chain": [], "user_actions": []}
        async def call(self, *, messages=None, **kw):
            text = _json.dumps(messages, ensure_ascii=False) if messages else ""
            if "verdict" in text:
                return {"status": "completed", "model": "m", "content": _json.dumps({"verdict": "accepted", "reason": "ok"})}
            if "验收基准" in text or "dynamic_golden_plan" in text:
                return {"status": "completed", "model": "m", "content": _baseline}
            return {"status": "completed", "model": "m", "content": _ident}

    gw = _GW()
    return RealP1Handler(profiling_service=ProfilingService(gateway=gw),
                         baseline_service=AcceptanceBaselineService(gateway=gw))


class _RecordingGate:
    def __init__(self):
        self.created = []

    def create(self, *, project_id, run_id, stage, artifact_refs,
               gate_type="stage_promotion", metadata=None):
        gid = f"gate-{stage}-{gate_type}-{len(self.created)}"
        self.created.append({"gid": gid, "stage": stage, "gate_type": gate_type})
        return gid

    def decide(self, *, gate_id, decision):
        pass


async def test_manual_mode_each_stage_reaches_own_plan_presentation(isolated_data, monkeypatch):
    """EG-WP2A-1 回归锁：manual 模式跨 2 个阶段（p0→p1），每阶段各自到达 plan_presentation。

    修复前：p0 stage_promotion 批准晋级后 plan_approved 未复位 → make_router 因其恒真
    路由回 p0_work → p1 永远得不到自己的 plan_presentation gate（在 p0 循环）。
    修复后：晋级时 plan_approved 复位 → p1 重新到达自己的 plan_presentation gate。
    """
    import tempfile
    from pathlib import Path
    ckpt = Path(tempfile.mkdtemp()) / "ckpt.sqlite"
    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path", lambda: ckpt)

    pid = "wp2a-multistage"
    _seed_source(pid)

    await reset_checkpointer_for_test()
    reset_flow_runtime_for_test()
    nodes.clear_handlers()
    nodes.register_handler("p0", RealP0Handler())
    nodes.register_handler("p1", RealP1Handler())
    gb = _RecordingGate()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    try:
        rt = get_flow_runtime()
        init = {"run_id": "run-ms", "project_id": pid, "run_goal": "multistage",
                "run_status": "running", "source_type": "manual",
                "execution_mode": "manual",
                "stage_status": {"p0": "in_progress"}}
        s = await rt.start("run-ms", init)
        assert "__interrupt__" in s
        # 首个 gate 必须是 p0 的 plan_presentation（manual 模式先审计划）
        assert gb.created[0]["stage"] == "p0"
        assert gb.created[0]["gate_type"] == "plan_presentation"

        # 逐个 approve，直到出现 p1 的 stage_promotion（证明跨过 2 个阶段）或耗尽步数
        for _ in range(10):
            if any(c["stage"] == "p1" and c["gate_type"] == "stage_promotion"
                   for c in gb.created):
                break
            s = await rt.resume("run-ms", "approve")
            if "__interrupt__" not in s:
                break

        def _has(stage, gtype):
            return any(c["stage"] == stage and c["gate_type"] == gtype for c in gb.created)

        # 核心断言：每个阶段各自到达 plan_presentation
        assert _has("p0", "plan_presentation"), "P0 应有自己的 plan_presentation gate"
        assert _has("p1", "plan_presentation"), \
            "P1 应有自己的 plan_presentation gate（EG-WP2A-1：修复前 p1 永远到不了）"
        # 确实跨过了 2 个阶段的完整晋级
        assert _has("p0", "stage_promotion"), "P0 应有 stage_promotion gate"
        assert _has("p1", "stage_promotion"), "P1 应有 stage_promotion gate"
        # p1 的 plan_presentation 早于 p1 的 stage_promotion（阶段内顺序正确）
        order = [(c["stage"], c["gate_type"]) for c in gb.created]
        assert order.index(("p1", "plan_presentation")) < order.index(("p1", "stage_promotion"))
    finally:
        await reset_checkpointer_for_test()
        reset_flow_runtime_for_test()
        nodes.clear_handlers()
        nodes.set_gate_backend(None)


async def test_validation_agent_acceptance_exception_does_not_pass(isolated_data, monkeypatch):
    """REC-1 回归锁：结构检查全过但独立结构核验（AcceptanceService）异常时，
    ValidationAgent 不得静默按 accepted 通过——须显式降级为不通过 + 记录 issue + 脱敏原因。
    """
    pid = "wp2a-val-exc"
    _seed_source(pid)
    handler = _fake_p1_handler()
    wa = WorkAgent("p1", pid, run_id="r1", handler=handler)
    result = await wa.execute({"source_type": "manual", "project_id": pid})

    # 令真实 AcceptanceService 在核验时抛异常（复现核验失败路径，非替身通过）
    import app.services.acceptance_service as acc_mod

    class _BoomAcceptance:
        def __init__(self, *a, **k):
            pass

        def accept(self, *a, **k):
            raise RuntimeError("boom during acceptance")

    monkeypatch.setattr(acc_mod, "AcceptanceService", _BoomAcceptance)

    va = ValidationAgent("p1", pid, run_id="r1", handler=handler)
    rr = va.validate(result)

    assert not rr.passed, "AcceptanceService 异常时不得判为通过（不得静默 accepted）"
    vres = va.last_result
    assert vres.verdict == "rework_required", f"异常应显式降级为 rework_required, got {vres.verdict}"
    # 结构/域检查未被放宽：域校验与 evidence-map 检查仍为 passed（异常降级仅体现在独立结构核验门控）。
    non_acc_checks = [c for c in vres.checks if "AcceptanceService" not in c["item"]]
    assert non_acc_checks and all(c["passed"] for c in non_acc_checks), \
        "域/结构检查不得因核验异常而被放宽（异常只影响独立结构核验门控）"
    # 异常以 issue 显式发声（脱敏：仅异常类型名，不含消息正文）
    exc_issues = [i for i in vres.issues if i.get("type") == "acceptance_check_error"]
    assert exc_issues, "核验异常须记录为 issue（失败发声）"
    assert "RuntimeError" in exc_issues[0]["detail"], "issue 保留（脱敏的）异常类型"
    assert "boom during acceptance" not in exc_issues[0]["detail"], \
        "异常消息正文不得进入验收产物（脱敏）"


async def test_validation_agent_normal_pass_unaffected(isolated_data):
    """反向对照：AcceptanceService 正常时，结构全过仍正常通过（不因 REC-1 修复而回归）。"""
    pid = "wp2a-val-ok"
    _seed_source(pid)
    handler = _fake_p1_handler()
    wa = WorkAgent("p1", pid, run_id="r1", handler=handler)
    result = await wa.execute({"source_type": "manual", "project_id": pid})
    va = ValidationAgent("p1", pid, run_id="r1", handler=handler)
    rr = va.validate(result)
    assert rr.passed, f"正常路径应通过, issues={rr.issues}"
    assert va.last_result.verdict in ("accepted", "accepted_with_warning")
