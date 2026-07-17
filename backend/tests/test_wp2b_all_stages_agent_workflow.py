"""R17.3-6 WP-2 批 B — P0/P2/P3/P4/P5/P6 全阶段 Agent 工作流真实单测（非 mock，D-097）。

承批 A（P1 样例）。本批证明同一骨架（make_work_node 内 StageLoop.execute_fn=WorkAgent /
review_fn=独立 ValidationAgent，不新增图节点 D-037）扩展到 P0-P6 全阶段，每阶段完整闭环：
  WorkAgent 上下文装配（Stage Skill 正文）→ 动态工作计划 → 调 handler（重定位为确定性/LLM
  Tool，r3 约束 2）→ 汇总产物 → evidence map（确定性 fact / LLM claim）→ Gate Brief →
  独立 ValidationAgent（含 rework 打回实证）。

红线：
  - 确定性阶段（P0/P5/P6）：无 Key 端到端可跑。
  - LLM 阶段（P2/P3/P4）：有 Key（本处以 fake gateway 模拟 Key-present，同现存 P2/P3 handler
    测试范式）→ claim-evidence + LLM 输出级内联引用真实达成；无 Key → WorkAgent 诚实 blocked +
    evidence_gap（不伪造 completed / 不假 claim-evidence，D-097/WP-6）。真实 provider Key 缺失，
    LLM 阶段真实 provider 端到端另需有效 Key（evidence_gap，见施工记录）。
"""

import json
from types import SimpleNamespace

from app.graph import nodes
from app.graph.stage_loop import StageLoop
from app.services import workspace_service
from app.services.review_pass import ReviewResult
from app.services.work_agent import WorkAgent
from app.services.validation_agent import ValidationAgent


def _seed(pid: str) -> None:
    workspace_service.init_workspace(pid)
    src = workspace_service.workspace_path(pid) / "source"
    (src / "app.py").write_text("import flask\nprint('x')\n", encoding="utf-8")
    (src / "requirements.txt").write_text("flask==2.0\n", encoding="utf-8")


# ── 全阶段接线 ───────────────────────────────────────────────────────────
def test_all_stages_agent_workflow_enabled():
    """_AGENT_WORKFLOW_STAGES 覆盖 P0-P6 全阶段（批 B 出口）。"""
    assert nodes._AGENT_WORKFLOW_STAGES == {"p0", "p1", "p2", "p3", "p4", "p5", "p6"}
    for s in ("p0", "p1", "p2", "p3", "p4", "p5", "p6"):
        assert nodes._agent_workflow_enabled(s)


# ── P0 接入（确定性，无 Key 端到端） ─────────────────────────────────────
async def test_p0_deterministic_workflow(isolated_data):
    from app.graph.stage_handlers import RealP0Handler
    pid = "wp2b-p0"
    _seed(pid)
    handler = RealP0Handler()
    wa = WorkAgent("p0", pid, run_id="r1", handler=handler)
    # 动态工作计划随真实事实
    plan_ref = wa.build_work_plan({"source_type": "manual"})
    plan = json.loads((workspace_service.workspace_path(pid) / plan_ref).read_text("utf-8"))
    assert plan["generated_by"] == "work_agent"
    assert plan["planned_actions"], "P0 动态计划有编排动作"

    result = await wa.execute({"source_type": "manual", "project_id": pid})
    assert result["status"] == "completed"
    assert any(tc["tool"] == "p0_handler.execute" for tc in result["tool_calls"])
    # fact-evidence map（确定性）绑定真实 intake_report.json
    cem = json.loads((workspace_service.workspace_path(pid) /
                      result["claim_evidence_map_ref"]).read_text("utf-8"))
    assert cem["map_type"] == "fact_evidence"
    assert cem["entries"] and all(e["bindings"]["artifact_refs"] for e in cem["entries"])

    # 独立 ValidationAgent 通过（handler.review 域校验 + 磁盘反伪造 + AcceptanceService）
    va = ValidationAgent("p0", pid, run_id="r1", handler=handler)
    rr = va.validate(result)
    assert rr.passed, f"P0 独立验收应通过 issues={rr.issues}"
    assert va.last_result.read_from_disk_only is True
    # 独立验收产物落盘
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p0_validation.json").exists()


# ── P2 评估（LLM）：有 Key（fake gateway）→ claim-evidence + 内联引用 ─────
class _SmartGateway:
    """模拟 Key-present LLM：域调用返回结构化产出（主输出已内联携带 evidence_refs/basis_refs）；
    ValidationAgent LLM 语义验收调用返回 verdict JSON。C1 后 WorkAgent 不再发起第二遍归因调用，
    内联引用来自域调用主输出本身。同现存 P2/P3 handler 测试的 fake gateway 范式。"""

    def __init__(self, domain_contents):
        self._domain = list(domain_contents)
        self._i = 0

    def get_status(self):
        return SimpleNamespace(overall_status="available")

    def stage_model_readiness(self, **kwargs):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, *, messages=None, **kw):
        text = json.dumps(messages, ensure_ascii=False) if messages else ""
        if "verdict" in text:  # ValidationAgent LLM 语义验收
            return {"status": "completed", "model": "m",
                    "content": json.dumps({"verdict": "accepted", "reason": "结构合理"})}
        c = self._domain[min(self._i, len(self._domain) - 1)] if self._domain else "{}"
        self._i += 1
        return {"status": "completed", "content": c, "model": "m"}


_P2_GOOD = json.dumps({
    "assessment_report": {"feasibility": "可行"},
    # C1: 主输出每条 risk 内联携带 evidence_refs（引用真实存在的上游产物）。
    "risk_list": [{"title": ".NET Framework→.NET 8 迁移风险", "risk_level": "L3",
                   "source": "tech_stack.json", "basis": "运行时不兼容",
                   "evidence_refs": ["artifacts/tech_stack.json"]}],
    "blocker_list": [{"title": "缺少数据库凭据", "evidence_refs": ["artifacts/tech_stack.json"]}],
    "uncertainty_list": [{"title": "运行环境未知"}],
    "validation_gap_list": [{"title": "无集成测试", "evidence_refs": []}],
    "resource_needs": [{"title": "需确定性转换工具"}],
})


def _p2_handler_with(gateway):
    from app.graph.stage_handlers import RealP2Handler
    from app.services.assessment_service import AssessmentService
    from app.services.aet_service import AETService
    svc = AssessmentService(gateway=gateway, aet_service=AETService(services=None))
    return RealP2Handler(assessment_service=svc)


async def test_p2_llm_workflow_with_key_inline_citations(isolated_data):
    pid = "wp2b-p2"
    _seed(pid)
    # 上游 P1 产物存在（claim 内联引用绑定目标）
    (workspace_service.workspace_path(pid) / "artifacts").mkdir(parents=True, exist_ok=True)
    (workspace_service.workspace_path(pid) / "artifacts" / "tech_stack.json").write_text(
        json.dumps({"languages": {"primary_language": "C#"}}), encoding="utf-8")

    gw = _SmartGateway([_P2_GOOD])
    handler = _p2_handler_with(gw)
    wa = WorkAgent("p2", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1", "user_goal": "迁移到信创栈"})

    assert result["status"] == "completed"
    cem = json.loads((workspace_service.workspace_path(pid) /
                      result["claim_evidence_map_ref"]).read_text("utf-8"))
    assert cem["map_type"] == "claim_evidence"
    assert cem["entries"], "P2 claim-evidence 有条目"
    # C1 主输出真内联：inline_citation 来自主任务 LLM 输出自带的 evidence_refs（非二遍归因）
    assert any(e["inline_citation"] for e in cem["entries"]), "主输出内联引用应达成（有 Key）"
    cited = [e for e in cem["entries"] if e["inline_citation"]]
    assert cited[0]["bindings"].get("cited_upstream_refs"), "内联引用绑定上游 artifact ref"
    # 被引 ref 必须真实存在（校验）
    for r in cited[0]["bindings"]["cited_upstream_refs"]:
        assert (workspace_service.workspace_path(pid) / r).exists(), f"被引 ref 真实存在: {r}"
    assert "evidence_gap" not in result, "有 Key 完成 → 无 evidence_gap"

    # 独立 ValidationAgent（LLM 语义 + 内联引用校验）通过
    va = ValidationAgent("p2", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)
    assert rr.passed, f"P2 独立验收应通过 issues={rr.issues}"
    cev = va.last_result.claim_evidence_verification
    assert cev["inline_citation"]["with_inline_citation"] >= 1
    assert cev.get("llm_semantic", {}).get("status") == "completed"


async def test_p2_llm_blocked_no_key_evidence_gap(isolated_data):
    """无 Key（gateway not_configured）→ P2 handler blocked → WorkAgent 诚实 blocked +
    evidence_gap（不伪造 completed / 不假 claim-evidence，D-097）；ValidationAgent 不通过。"""
    class _NoKeyGW:
        def get_status(self):
            return SimpleNamespace(overall_status="not_configured")
        def stage_model_readiness(self, **kwargs):
            return {"available": False, "capability_ok": False,
                    "reason": "无任一已配置且具备有效凭据的模型可用",
                    "attempted_chain": [{"profile_id": "fake/m", "provider_id": "fake",
                        "model": "m", "is_fallback": False, "outcome": "credential_missing",
                        "error_category": "credential_missing", "error_message": "无凭据"}],
                    "user_actions": [{"action": "configure", "label": "配置模型 / API Key", "target": "models"}]}
        async def call(self, **kw):
            return {"status": "blocked", "content": "", "error_category": "credential_missing"}

    pid = "wp2b-p2-nokey"
    _seed(pid)
    handler = _p2_handler_with(_NoKeyGW())
    wa = WorkAgent("p2", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1", "user_goal": "迁移"})

    assert result["status"] == "blocked", "无 Key 诚实 blocked，不伪造 completed"
    assert result.get("evidence_gap"), "标 evidence_gap（需有效 Key 端到端验证）"
    assert "有效模型 Key" in result["evidence_gap"]["detail"] or "Key" in result["evidence_gap"]["detail"]
    # claim-evidence map 无伪造 claim
    cem = json.loads((workspace_service.workspace_path(pid) /
                      result["claim_evidence_map_ref"]).read_text("utf-8"))
    assert all(not e.get("verified_on_disk") for e in cem["entries"])

    va = ValidationAgent("p2", pid, run_id="r1", handler=handler)
    rr = va.validate(result)
    assert not rr.passed, "无 Key blocked → 独立验收不通过（诚实升级 Gate）"


# ── P3 规划（LLM）：有 Key（fake gateway）→ claim-evidence ────────────────
_P3_SP = json.dumps({
    "objective": "迁移到信创栈", "scope": ["Web 层"], "out_of_scope": ["前端"],
    "risk_level": "L3", "permission_boundary": "source/",
    "expected_artifacts": ["plan.md"], "expected_evidence": ["对照表"],
    "gate_policy": {"high_risk": "require_gate"}, "completion_criteria": ["计划完成"],
    "validation_strategy": "回归测试",
    # C1: Stage Plan 主输出内联携带 basis_refs（引用真实存在的上游 P2 产物）。
    "basis_refs": ["artifacts/p2_risk_list.json"],
})
_P3_BATCH = json.dumps({
    "batch_objective": "迁移任务", "batch_scope": ["路由"], "permission_boundary": "source/",
    "validation_strategy": "逐任务回归", "exception_policy": "升级 Gate",
    "task_plans": [{"objective": "替换路由", "risk_level": "L2", "validation_method": "回归",
                    "title": "路由", "basis_refs": ["artifacts/p2_risk_list.json"]}],
})
_P3_EDGES = '{"edges": []}'


async def test_p3_llm_workflow_with_key(isolated_data):
    from app.graph.stage_handlers import RealP3Handler
    from app.services.planning_service import PlanningService
    from app.services.aet_service import AETService
    pid = "wp2b-p3"
    _seed(pid)
    (workspace_service.workspace_path(pid) / "artifacts").mkdir(parents=True, exist_ok=True)
    (workspace_service.workspace_path(pid) / "artifacts" / "p2_risk_list.json").write_text(
        json.dumps({"items": [{"title": "风险"}]}), encoding="utf-8")

    gw = _SmartGateway([_P3_SP, _P3_BATCH, _P3_EDGES])
    svc = PlanningService(gateway=gw, aet_service=AETService(services=None))
    handler = RealP3Handler(planning_service=svc)
    wa = WorkAgent("p3", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1", "user_goal": "迁移"})

    assert result["status"] == "completed"
    assert result.get("task_graph_ref"), "TaskGraph 必生"
    cem = json.loads((workspace_service.workspace_path(pid) /
                      result["claim_evidence_map_ref"]).read_text("utf-8"))
    assert cem["map_type"] == "claim_evidence"
    assert any(e["id"].endswith("task_graph") for e in cem["entries"])
    # C1 主输出真内联：P3 计划类结论内联引用其依据的上游 P2 产物（来自主输出 basis_refs）
    assert any(e["inline_citation"] for e in cem["entries"]), "P3 主输出内联引用应达成（有 Key）"
    cited = [e for e in cem["entries"] if e["inline_citation"]]
    assert "artifacts/p2_risk_list.json" in cited[0]["bindings"].get("cited_upstream_refs", [])

    va = ValidationAgent("p3", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)
    assert rr.passed, f"P3 独立验收应通过 issues={rr.issues}"


# ── P4 执行（LLM）：无模型 → 诚实 blocked + evidence_gap ─────────────────
async def test_p4_blocked_evidence_gap(isolated_data):
    from app.graph.stage_handlers import RealP4Handler
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph, TaskNode

    class _BlockedGW:
        async def call(self, **kw):
            return {"status": "not_configured", "content": "", "error": "no model"}

    pid = "wp2b-p4"
    _seed(pid)
    db = get_session()
    try:
        tg = TaskGraph(project_id=pid, stage="p3", stage_plan_ref="sp", title="g",
                       graph_status="draft", edges=[], version=1)
        db.add(tg); db.flush()
        db.add(TaskNode(task_graph_id=tg.task_graph_id, project_id=pid, stage="p3",
                        node_type="execution", title="n", risk_level="L2"))
        db.commit()
    finally:
        db.close()

    handler = RealP4Handler(gateway=_BlockedGW())
    wa = WorkAgent("p4", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1"})
    assert result["status"] == "blocked", "无模型 → 诚实 blocked（不伪造 completed）"
    assert result.get("evidence_gap"), "P4 无 Key/无模型 → evidence_gap"

    va = ValidationAgent("p4", pid, run_id="r1", handler=handler)
    rr = va.validate(result)
    assert not rr.passed


# ── P5 验证（确定性，无 Key 端到端）+ GATE-01 报告挂 Gate ───────────────
async def test_p5_deterministic_workflow_and_gate_material(isolated_data):
    from app.graph.stage_handlers import RealP5Handler
    pid = "wp2b-p5"
    _seed(pid)
    handler = RealP5Handler()
    wa = WorkAgent("p5", pid, run_id="r1", handler=handler)
    va = ValidationAgent("p5", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1"})
    # P5 无 P4 输入 → 诚实 blocked（不伪造）；WorkAgent 如实传导
    assert result["status"] in ("blocked", "completed")
    assert any(tc["tool"] == "p5_handler.execute" for tc in result["tool_calls"])
    # 动态计划 + Gate Brief 落盘
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p5_work_plan.json").exists()
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p5_gate_brief.json").exists()

    rr = va.validate(result)
    # blocked → 不通过（诚实）
    if result["status"] == "blocked":
        assert not rr.passed

    # GATE-01：_finalize_agent_gate_material 把 p5_validation_report.json 挂 Gate（若存在）
    (workspace_service.workspace_path(pid) / "artifacts" / "p5_validation_report.json").write_text(
        json.dumps({"stage": "p5", "can_be_completed": True}), encoding="utf-8")
    refs = nodes._finalize_agent_gate_material("p5", pid, "r1", wa, va)
    assert "artifacts/p5_validation_report.json" in refs, "GATE-01：P5 验证报告挂 Gate 材料"


# ── P6 交付（确定性）：P5 未过 → 诚实 blocked（WorkAgent 编排 RealP6Handler） ─
async def test_p6_deterministic_workflow_honest_blocked(isolated_data):
    from app.graph.stage_handlers import RealP6Handler
    pid = "wp2b-p6"
    _seed(pid)
    handler = RealP6Handler()
    wa = WorkAgent("p6", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1"})
    assert result["status"] == "blocked", "P5 未通过 → P6 诚实 blocked（不伪造交付）"
    assert any(tc["tool"] == "p6_handler.execute" for tc in result["tool_calls"])
    # 动态计划 + Gate Brief 落盘（编排闭环成立）
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p6_work_plan.json").exists()

    va = ValidationAgent("p6", pid, run_id="r1", handler=handler)
    rr = va.validate(result)
    assert not rr.passed


# ── rework 打回实证（通用路径，非 P1）────────────────────────────────────
async def test_generic_stage_rework_then_pass(isolated_data):
    """通用 ValidationAgent 打回实证：首轮 handler.review 不合格 → StageLoop rework 轮 →
    WorkAgent 携反馈重跑 → 第二轮通过。证明批 B 骨架对全阶段 rework 生效（承 D-082）。"""
    pid = "wp2b-rework"
    _seed(pid)

    class _FlakyHandler:
        goal = "P0 接入"
        acceptance_criteria = ["产出接入产物"]
        planned_actions = ["物化源码", "写接入报告"]

        def __init__(self):
            self.calls = 0

        async def execute(self, state):
            self.calls += 1
            art = workspace_service.workspace_path(pid) / "artifacts"
            art.mkdir(parents=True, exist_ok=True)
            if self.calls == 1:
                # 首轮：不产出 intake_report（真实退化，review 判不合格）
                return {"status": "completed", "artifacts": []}
            # rework 重跑：补齐 intake_report
            (art / "intake_report.json").write_text(json.dumps({"stage": "p0"}), encoding="utf-8")
            return {"status": "completed", "artifacts": ["artifacts/intake_report.json"],
                    "source_type": "manual", "file_count": 2,
                    "materialized": {"materialization_status": "empty"}}

        def review(self, result):
            ok = bool(result.get("artifacts"))
            return ReviewResult(passed=ok, reviewer="p0_review_skill",
                                issues=[] if ok else [{"type": "no_artifacts", "detail": "缺接入产物"}],
                                recommendations=[] if ok else ["补齐 intake 产物后重跑"])

    handler = _FlakyHandler()
    wa = WorkAgent("p0", pid, run_id="r1", handler=handler)
    va = ValidationAgent("p0", pid, run_id="r1", handler=handler)

    def _review(work_result):
        rr = va.validate(work_result)
        if not rr.passed:
            wa.set_rework_feedback({"issues": rr.issues, "recommendations": rr.recommendations})
        return rr

    loop = StageLoop(pid, "p0", max_rounds=2, run_id="r1")
    res = await loop.run(goal="P0", acceptance_criteria=["c"], planned_actions=["a"],
                         execute_fn=lambda: wa.execute({"project_id": pid, "source_type": "manual"}),
                         review_fn=_review)
    assert handler.calls == 2, "handler 被调两轮（首轮退化 + rework 重跑）"
    assert res.passed, "rework 后第二轮通过"
    assert res.rounds[0]["status"] == "retry"
    assert res.rounds[1]["status"] == "passed"
    first_issues = json.dumps(res.rounds[0]["issues"], ensure_ascii=False)
    assert "no_artifacts" in first_issues or "接入产物" in first_issues


# ── C4：ValidationAgent 独立性加固（从盘重读 + AcceptanceService 参与门控）─────
async def test_c4_validation_reads_domain_from_disk_not_process(isolated_data):
    """C4 独立性：ValidationAgent 域基线从落盘产物重读（read_from_disk_only 名实相符），
    不消费 WorkAgent 进程内推理。构造【域产物落盘不一致】——WorkAgent 声明 completed 且
    claim-evidence 引用某域产物，但该产物在验收前从盘删除 → ValidationAgent 独立重读磁盘
    判不通过（反伪造），即便进程内 work_result 仍自称完成。"""
    from app.graph.stage_handlers import RealP2Handler
    from app.services.assessment_service import AssessmentService
    from app.services.aet_service import AETService
    pid = "wp2b-c4-disk"
    _seed(pid)
    art = workspace_service.workspace_path(pid) / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "tech_stack.json").write_text(
        json.dumps({"languages": {"primary_language": "C#"}}), encoding="utf-8")

    gw = _SmartGateway([_P2_GOOD])
    svc = AssessmentService(gateway=gw, aet_service=AETService(services=None))
    handler = RealP2Handler(assessment_service=svc)
    wa = WorkAgent("p2", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1", "user_goal": "迁移"})
    assert result["status"] == "completed"

    # 域产物落盘不一致：删除全部被 claim-evidence 引用的域产物（风险/阻塞/验证缺口清单）。
    for name in ("p2_risk_list.json", "p2_blocker_list.json", "p2_validation_gaps.json"):
        fp = art / name
        if fp.exists():
            fp.unlink()

    va = ValidationAgent("p2", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)  # 传入的仍是进程内 work_result（自称 completed）
    assert not rr.passed, "落盘域产物缺失 → 独立重读磁盘判不通过（不采信进程内自称完成）"
    assert va.last_result.read_from_disk_only is True
    issue_types = {i.get("type") for i in va.last_result.issues}
    # 反伪造 / evidence 未解析 之一被触发（独立磁盘校验，非进程内）
    assert issue_types & {"fabricated_completion", "evidence_partially_unresolved"}


async def test_c4_acceptance_service_participates_in_gating(isolated_data, monkeypatch):
    """C4 门控：AcceptanceService 独立结构核验参与 pass/fail（不再仅异常记 issue）。
    令独立核验异常 → ValidationAgent 独立验收不通过（acceptance_check_error 门控）。"""
    from app.graph.stage_handlers import RealP0Handler
    pid = "wp2b-c4-gate"
    _seed(pid)
    handler = RealP0Handler()
    wa = WorkAgent("p0", pid, run_id="r1", handler=handler)
    result = await wa.execute({"source_type": "manual", "project_id": pid})
    assert result["status"] == "completed"

    va = ValidationAgent("p0", pid, run_id="r1", handler=handler)
    # 未打桩时应通过（基线）
    assert va.validate(result).passed

    # 令 AcceptanceService 独立核验抛错 → 门控判不通过（非静默 accepted，REC-1）
    va2 = ValidationAgent("p0", pid, run_id="r1", handler=handler)
    monkeypatch.setattr(va2, "_acceptance_check",
                        lambda *a, **k: {"result": "error", "agent_id": None,
                                         "error": "InjectedError", "recommendations": []})
    rr = va2.validate(result)
    assert not rr.passed, "独立结构核验异常 → 独立验收门控不通过"
    assert any(i.get("type") == "acceptance_check_error" for i in va2.last_result.issues)
