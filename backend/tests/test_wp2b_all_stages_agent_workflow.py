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


# ── P0 接入（R17.5：LLM 识别阶段；有 Key=注入 gateway → 识别完成；无 Key → 诚实 blocked）──
class _IntakeGateway:
    """模拟 Key-present LLM（P0 识别）：readiness available=True；域调用返回结构化识别 JSON；
    ValidationAgent LLM 语义验收调用返回 verdict JSON。同 P2/P3 handler 测试的 fake gateway 范式。"""

    def __init__(self, ident_json):
        self._ident = ident_json

    def get_status(self):
        return SimpleNamespace(overall_status="available")

    def stage_model_readiness(self, **kwargs):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, *, messages=None, **kw):
        text = json.dumps(messages, ensure_ascii=False) if messages else ""
        if "verdict" in text:   # ValidationAgent LLM 语义验收
            return {"status": "completed", "model": "m",
                    "content": json.dumps({"verdict": "accepted", "reason": "识别结构合理"})}
        return {"status": "completed", "model": "fake-model", "content": self._ident}


_P0_IDENT = json.dumps({
    "primary_language": "Python",
    "detected_stack": ["Python"],
    "key_files": [{"path": "requirements.txt", "why_key": "依赖清单"},
                  {"path": "app.py", "why_key": "应用入口"}],
    "source_environment_clues": {"languages": ["Python"], "frameworks": ["Flask"],
                                 "project_type": "web_app"},
    "database_entry": {"present": False, "note": "未发现 .sql 脚本"},
    "availability_classification": {"class": "B", "label": "源码可用未验证运行",
                                    "reasoning": ["源码已物化", "无原环境实跑证据 → B"]},
    "migration_intent": {"text": None, "source": "user_intent", "confidence": "unverified",
                         "decision_status": "pending_p2_assessment", "note": "not_provided_at_p0"},
    "entry_points": [{"path": "app.py", "kind": "python_entry"}],
    "p1_intake_tasks": [{"category": "dependencies", "task": "解析 requirements.txt",
                         "scope_ref": "requirements.txt"}],
    "missing_information": ["未发现 LICENSE"],
    "questions_for_user": ["目标运行环境？"],
    "uncertainty": [{"area": "运行环境", "detail": "未验证实跑"}],
}, ensure_ascii=False)


async def test_p0_llm_intake_workflow_with_key(isolated_data):
    """R17.5：P0 改 LLM 识别。注入 Key-present gateway → 采集事实包 → LLM 产识别字段 →
    claim-evidence（produced_by=llm）绑定真实 intake_report.json；独立 LLM 验收通过。"""
    from app.graph.stage_handlers import RealP0Handler
    from app.services.intake_service import IntakeService
    from app.services.source_materializer import generate_source_index
    pid = "wp2b-p0"
    _seed(pid)
    generate_source_index(pid, source_type="local_dir")  # 采集：源码索引落盘（供 LLM 引用）

    gw = _IntakeGateway(_P0_IDENT)
    handler = RealP0Handler(intake_service=IntakeService(gateway=gw))
    wa = WorkAgent("p0", pid, run_id="r1", handler=handler)
    plan_ref = wa.build_work_plan({"source_type": "manual"})
    plan = json.loads((workspace_service.workspace_path(pid) / plan_ref).read_text("utf-8"))
    assert plan["generated_by"] == "work_agent"
    assert plan["planned_actions"], "P0 动态计划有编排动作"

    result = await wa.execute({"source_type": "manual", "project_id": pid})
    assert result["status"] == "completed"
    assert any(tc["tool"] == "p0_handler.execute" for tc in result["tool_calls"])
    # intake 识别字段由 LLM 产出（非 deterministic_tool）
    assert result["primary_language"] == "Python"
    assert result["availability_class"] == "B"
    # claim-evidence map（LLM claim）绑定真实 intake_report.json
    cem = json.loads((workspace_service.workspace_path(pid) /
                      result["claim_evidence_map_ref"]).read_text("utf-8"))
    assert cem["map_type"] == "claim_evidence"
    assert cem["entries"] and all(e["produced_by"] == "llm" for e in cem["entries"])
    assert all(e["bindings"]["artifact_refs"] for e in cem["entries"])
    # intake_report.json 落盘且识别字段来自 LLM（produced_by 非 deterministic_tool；D-107: artifacts/p0/）
    intake = json.loads((workspace_service.workspace_path(pid) /
                         "artifacts" / "p0" / "intake_report.json").read_text("utf-8"))
    assert intake["produced_by"] == "llm_node_worker_agent"
    assert intake["identification"]["primary_language"] == "Python"

    # 独立 ValidationAgent（LLM 验收路径）通过
    va = ValidationAgent("p0", pid, run_id="r1", handler=handler, gateway=gw)
    rr = va.validate(result)
    assert rr.passed, f"P0 独立验收应通过 issues={rr.issues}"
    assert va.last_result.read_from_disk_only is True
    # D-107: validation report lives in artifacts/{stage}/ subdirectory.
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p0" / "p0_validation.json").exists()


async def test_p0_llm_intake_blocked_no_key(isolated_data):
    """R17.5 WP-5：P0 识别无有效 Key → 诚实 blocked（不回退规则识别冒充 completed，D-097）。"""
    from app.graph.stage_handlers import RealP0Handler
    from app.services.intake_service import IntakeService
    pid = "wp2b-p0-nokey"
    _seed(pid)

    class _NoKeyGW:
        def stage_model_readiness(self, **kw):
            return {"available": False, "capability_ok": False,
                    "reason": "无任一已配置且具备有效凭据的模型可用",
                    "attempted_chain": [{"provider": "x", "outcome": "credential_missing"}],
                    "user_actions": [{"action": "configure_key"}]}

    handler = RealP0Handler(intake_service=IntakeService(gateway=_NoKeyGW()))
    wa = WorkAgent("p0", pid, run_id="r1", handler=handler)
    result = await wa.execute({"source_type": "manual", "project_id": pid})
    assert result["status"] == "blocked", "无 Key 诚实 blocked，不伪造 completed"
    assert result.get("attempted_chain"), "透传已尝试/候选模型链路"
    va = ValidationAgent("p0", pid, run_id="r1", handler=handler)
    rr = va.validate(result)
    assert not rr.passed, "无 Key blocked → 独立验收不通过（诚实升级 Gate）"



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
                   "evidence_refs": ["artifacts/p1/tech_stack.json"]}],
    "blocker_list": [{"title": "缺少数据库凭据", "evidence_refs": ["artifacts/p1/tech_stack.json"]}],
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
    # 上游 P1 产物存在（claim 内联引用绑定目标；D-107: artifacts/p1/）
    (workspace_service.workspace_path(pid) / "artifacts" / "p1").mkdir(parents=True, exist_ok=True)
    (workspace_service.workspace_path(pid) / "artifacts" / "p1" / "tech_stack.json").write_text(
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
    # C1: Stage Plan 主输出内联携带 basis_refs（引用真实存在的上游 P2 产物；D-107: artifacts/p2/）。
    "basis_refs": ["artifacts/p2/p2_risk_list.json"],
})
_P3_BATCH = json.dumps({
    "batch_objective": "迁移任务", "batch_scope": ["路由"], "permission_boundary": "source/",
    "validation_strategy": "逐任务回归", "exception_policy": "升级 Gate",
    "task_plans": [{"objective": "替换路由", "risk_level": "L2", "validation_method": "回归",
                    "title": "路由", "basis_refs": ["artifacts/p2/p2_risk_list.json"]}],
})
_P3_EDGES = '{"edges": []}'


async def test_p3_llm_workflow_with_key(isolated_data):
    from app.graph.stage_handlers import RealP3Handler
    from app.services.planning_service import PlanningService
    from app.services.aet_service import AETService
    pid = "wp2b-p3"
    _seed(pid)
    (workspace_service.workspace_path(pid) / "artifacts" / "p2").mkdir(parents=True, exist_ok=True)
    (workspace_service.workspace_path(pid) / "artifacts" / "p2" / "p2_risk_list.json").write_text(
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
    assert "artifacts/p2/p2_risk_list.json" in cited[0]["bindings"].get("cited_upstream_refs", [])

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


# ── P4 内容保真语义门禁（R17.5 P4 T3：负例三条）─────────────────────────
class _P4SemGW:
    """模拟有效 Key 的独立验收语义模型：按构造 verdict/grounded/reason 返回结构化 JSON。
    P4 语义 prompt 已给源清单+产物内容+裁决路线，此 fake 只回既定判定，用于验证门禁生效。"""

    def __init__(self, verdict, grounded=None, reason=""):
        self._v, self._g, self._r = verdict, grounded, reason
        self.calls = []

    async def call(self, *, messages=None, **kw):
        self.calls.append(messages)
        obj = {"verdict": self._v, "reason": self._r}
        if self._g is not None:
            obj["grounded"] = self._g
        return {"status": "completed", "model": "fake-strong",
                "content": json.dumps(obj, ensure_ascii=False)}


class _NoKeyGW:
    """模拟无 Key / 模型不可用：语义调用返回 not_configured（同 _BlockedGW 范式）。"""

    async def call(self, **kw):
        return {"status": "not_configured", "content": "", "error_category": "model_unavailable"}


class _PassReviewHandler:
    """域基线通过的 P4 handler 替身（review 返回 passed）：使结构门控可全通过，从而隔离
    验证「语义结论是否真正作 pass/fail 门禁」——同一结构产物集，仅语义 verdict 不同→结果翻转。"""
    goal = "P4 执行迁移工作包"
    acceptance_criteria: list = []
    planned_actions: list = []

    def review(self, view):
        return ReviewResult(passed=True, issues=[], recommendations=[])


def _seed_p4_completed(pid: str) -> dict:
    """构造【结构门控全通过】的 P4 completed 产物集：真实源 + output_code/patches 迁移产物 +
    artifacts/p4 领域产物 + 磁盘可解析 claim-evidence map + AET 证据（source_ref/output_code_ref）
    + 上游 P2/P3 裁决路线。语义 verdict 由注入 gateway 决定。返回 work_result。"""
    import hashlib
    _seed(pid)
    ws = workspace_service.workspace_path(pid)
    # 真实迁移产物（output_code + patch）——语义检查按需读取其内容
    (ws / "output_code").mkdir(parents=True, exist_ok=True)
    (ws / "output_code" / "Program.cs").write_text(
        "// 迁移自 source/app.py（Flask）→ ASP.NET Core Minimal API\n"
        "var builder = WebApplication.CreateBuilder(args);\n"
        "var app = builder.Build();\napp.MapGet(\"/\", () => \"x\");\napp.Run();\n",
        encoding="utf-8")
    (ws / "patches").mkdir(parents=True, exist_ok=True)
    (ws / "patches" / "app.diff").write_text(
        "--- a/source/app.py\n+++ b/output_code/Program.cs\n", encoding="utf-8")
    # 领域产物（供 disk_refs / AcceptanceService artifact 门控）
    p4dir = ws / "artifacts" / "p4"
    p4dir.mkdir(parents=True, exist_ok=True)
    (p4dir / "execution_summary.json").write_text(
        json.dumps({"stage": "p4", "nodes": 1, "build": "evidence_gap"}, ensure_ascii=False),
        encoding="utf-8")
    # 上游裁决路线（P3 目标栈 + P2 候选）——供语义 prompt 的路线摘要
    (ws / "artifacts" / "p3").mkdir(parents=True, exist_ok=True)
    (ws / "artifacts" / "p3" / "p3_stage_plan.json").write_text(
        json.dumps({"stage_plan": {"objective": "Flask→ASP.NET Core 迁移", "target_stack": ".NET 8"}},
                   ensure_ascii=False), encoding="utf-8")
    (ws / "artifacts" / "p2").mkdir(parents=True, exist_ok=True)
    (ws / "artifacts" / "p2" / "p2_assessment_report.json").write_text(
        json.dumps({"report": {"adr_candidates": [{"option": ".NET 8", "note": "目标运行时"}]}},
                   ensure_ascii=False), encoding="utf-8")
    # claim-evidence map（磁盘可解析 fact，避开 AGT-05 内联路径）
    exec_rel = "artifacts/p4/execution_summary.json"
    sha = hashlib.sha256((ws / exec_rel).read_bytes()).hexdigest()
    cem = {"map_type": "claim_evidence", "entries": [
        {"id": "ev-p4-exec", "kind": "fact", "produced_by": "deterministic_tool",
         "statement": "P4 执行汇总落盘",
         "bindings": {"artifact_refs": [exec_rel], "sha256": sha}}]}
    cem_rel = "artifacts/p4/p4_claim_evidence_map.json"
    (ws / cem_rel).write_text(json.dumps(cem, ensure_ascii=False), encoding="utf-8")
    # AET 证据（含 output_code_ref/source_ref）——供 acc 门控 evidence + 语义源片段读取
    from app.services.aet_service import AETService
    AETService(None).write_evidence(
        pid, "ev-p4-code", "code", stage="p4",
        extra={"output_code_ref": "output_code/Program.cs", "source_ref": "source/app.py",
               "evidence_basis": "real_file_on_disk"})
    return {"status": "completed", "claim_evidence_map_ref": cem_rel,
            "output_code_ref": "output_code/Program.cs"}


async def test_p4_semantic_gate_rejects_fabricated(isolated_data):
    """负例①：结构门控全通过，但独立语义验收判 rework（脱离真实源臆造/违反裁决路线）→ 验收
    拒绝。证明语义内容保真结论作 pass/fail 门禁（非当前 advisory，T3.2）。"""
    pid = "wp2b-p4-fab"
    wr = _seed_p4_completed(pid)
    gw = _P4SemGW("rework", grounded=False, reason="产出为通用 EMPLOYEE 表，脱离真实 Flask 源")
    va = ValidationAgent("p4", pid, run_id="r1", handler=_PassReviewHandler(), gateway=gw)
    rr = va.validate(wr)
    assert gw.calls, "语义验收必须真实发起模型调用（读产物+源，非只喂声明语句）"
    assert not rr.passed, "臆造/违反路线 → 验收必须拒绝"
    assert any(i.get("type") == "semantic_fidelity_fail" for i in rr.issues), \
        f"必须因内容保真语义门禁拒绝 issues={rr.issues}"


async def test_p4_semantic_gate_passes_real_migration(isolated_data):
    """负例②（正向对照）：同一结构产物集，语义验收判 accepted+grounded → 通过。与①仅 verdict
    不同→结果翻转，证明门禁真正作数（若结果不随语义 verdict 变，即证明「上层仍按结构判」）。"""
    pid = "wp2b-p4-real"
    wr = _seed_p4_completed(pid)
    gw = _P4SemGW("accepted", grounded=True,
                  reason="真实 Flask→.NET 迁移，接地 source/app.py，遵守 .NET 8 路线")
    va = ValidationAgent("p4", pid, run_id="r1", handler=_PassReviewHandler(), gateway=gw)
    rr = va.validate(wr)
    assert gw.calls, "语义验收真实发起模型调用"
    assert rr.passed, f"真实迁移 + 结构门控通过 → 应通过 issues={rr.issues}"
    assert not any(i.get("type", "").startswith("semantic_") for i in rr.issues), \
        f"accepted+grounded 不应产生语义门禁 issue issues={rr.issues}"


async def test_p4_semantic_gate_no_key_not_pass(isolated_data):
    """负例③：无 Key / 模型不可用 → 语义 evidence_gap → 诚实不通过（不假 pass，D-097）。即使
    结构门控全通过，也因无法确认内容保真而拒绝（去掉「结构过了就 accepted」）。"""
    pid = "wp2b-p4-nokey"
    wr = _seed_p4_completed(pid)
    va = ValidationAgent("p4", pid, run_id="r1", handler=_PassReviewHandler(), gateway=_NoKeyGW())
    rr = va.validate(wr)
    assert not rr.passed, "无 Key 不得假 pass（D-097 诚实降级）"
    assert any(i.get("type") == "semantic_evidence_gap" for i in rr.issues), \
        f"无 Key → semantic_evidence_gap（诚实不通过）issues={rr.issues}"


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
    # 动态计划 + Gate Brief 落盘（D-107: artifacts/p5/）
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p5" / "p5_work_plan.json").exists()
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p5" / "p5_gate_brief.json").exists()

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
    # 动态计划 + Gate Brief 落盘（编排闭环成立；D-107: artifacts/p6/）
    assert (workspace_service.workspace_path(pid) / "artifacts" / "p6" / "p6_work_plan.json").exists()

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
            art = workspace_service.workspace_path(pid) / "artifacts" / "p0"
            art.mkdir(parents=True, exist_ok=True)
            if self.calls == 1:
                # 首轮：不产出 intake_report（真实退化，review 判不合格）
                return {"status": "completed", "artifacts": []}
            # rework 重跑：补齐 intake_report（D-107: artifacts/p0/）
            (art / "intake_report.json").write_text(json.dumps({"stage": "p0"}), encoding="utf-8")
            return {"status": "completed", "artifacts": ["artifacts/p0/intake_report.json"],
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
    (art / "p1").mkdir(parents=True, exist_ok=True)
    (art / "p1" / "tech_stack.json").write_text(
        json.dumps({"languages": {"primary_language": "C#"}}), encoding="utf-8")

    gw = _SmartGateway([_P2_GOOD])
    svc = AssessmentService(gateway=gw, aet_service=AETService(services=None))
    handler = RealP2Handler(assessment_service=svc)
    wa = WorkAgent("p2", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1", "user_goal": "迁移"})
    assert result["status"] == "completed"

    # 域产物落盘不一致：删除全部被 claim-evidence 引用的域产物（风险/阻塞/验证缺口清单，D-107: p2/）。
    for name in ("p2_risk_list.json", "p2_blocker_list.json", "p2_validation_gaps.json"):
        fp = art / "p2" / name
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
