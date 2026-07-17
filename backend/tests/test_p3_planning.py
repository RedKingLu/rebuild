"""R10 T13 tests: P3 PlanningService.generate_stage_plan (契约 §5, 状态架构 §5.2).

Stage Plan is LLM-generated from the P2 assessment and persisted as a draft
pending the P3→P4 Gate. no model → blocked (Q-R10-2, no rule fallback);
model failure → failed (公理3); unparseable → parse_error (retry).
Uses a fake gateway + the conftest test DB — no live LLM call.
"""

import json
from types import SimpleNamespace

import pytest

from app.services.planning_service import (
    PlanningService, StagePlanResult, STAGE_PLAN_FIELDS, TaskPlanBatchResult, TaskGraphResult,
)


class _FakeGateway:
    def __init__(self, overall="available", call_result=None):
        self._overall = overall
        self._call_result = call_result or {}

    def get_status(self):
        return SimpleNamespace(overall_status=self._overall)

    def stage_model_readiness(self, **kwargs):
        # WP-6: emulate ModelGateway.stage_model_readiness.
        available = self._overall == "available"
        chain = [] if available else [{"profile_id": "fake/model", "provider_id": "fake",
                                       "model": "fake", "is_fallback": False,
                                       "outcome": "not_configured", "error_category": "not_configured",
                                       "error_message": "模型未配置"}]
        return {"available": available, "capability_ok": available,
                "reason": "" if available else "无任一已配置且具备有效凭据的模型可用",
                "attempted_chain": chain,
                "user_actions": [{"action": "configure", "label": "配置模型 / API Key", "target": "models"}]}

    async def call(self, **kwargs):
        return self._call_result


_GOOD = json.dumps({
    "objective": "将 Flask 服务迁移到信创栈",
    "scope": ["迁移 Web 层", "替换不兼容依赖"],
    "out_of_scope": ["前端重写"],
    "risk_level": "L3",
    "permission_boundary": "仅限 source/ 目录写",
    "expected_artifacts": ["migration_plan.md"],
    "expected_evidence": ["依赖替换对照表"],
    "gate_policy": {"high_risk": "require_gate"},
    "completion_criteria": ["全部任务计划已生成"],
    "validation_strategy": "回归测试 + 差异对比",
})


async def test_stage_plan_fields_nine():
    assert len(STAGE_PLAN_FIELDS) == 9


async def test_no_model_blocked():
    """Q-R10-2: no available model → blocked, no rule fallback."""
    svc = PlanningService(gateway=_FakeGateway(overall="not_configured"))
    res = await svc.generate_stage_plan("proj-t13-1")
    assert res.status == "blocked"
    assert "no_model_key" in res.reason
    assert res.stage_plan_id is None


async def test_model_failure_is_failed():
    svc = PlanningService(gateway=_FakeGateway(
        overall="available", call_result={"status": "failed", "error_message": "timeout"}))
    res = await svc.generate_stage_plan("proj-t13-2")
    assert res.status == "failed" and "timeout" in res.reason
    assert res.stage_plan_id is None


async def test_completed_persists_stage_plan_draft():
    svc = PlanningService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": _GOOD, "model": "deepseek/chat"}))
    res = await svc.generate_stage_plan("proj-t13-3", run_id="run-1", user_goal="迁移到信创栈")
    assert res.status == "completed"
    assert res.stage_plan_id and res.stage_plan_id.startswith("sp-")
    assert res.risk_level == "L3"
    assert res.scope["out_of_scope"] == ["前端重写"]     # §2.2-2 out_of_scope declared

    # real DB row, draft pending Gate (§5.4 / D-023)
    from app.core.database import get_session
    from app.models.stage_plan import StagePlan
    db = get_session()
    try:
        row = db.get(StagePlan, res.stage_plan_id)
        assert row is not None
        assert row.plan_status == "draft"
        assert row.stage == "p3"
        assert row.objective == "将 Flask 服务迁移到信创栈"
        assert row.plan_detail["validation_strategy"] == "回归测试 + 差异对比"  # §5.4-8
    finally:
        db.close()


async def test_unparseable_marks_parse_error_still_persists():
    svc = PlanningService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": "not json", "model": "m"}))
    res = await svc.generate_stage_plan("proj-t13-4")
    assert res.status == "completed"
    assert res.parse_error is True
    assert res.objective == ""                          # honest: not hallucinated


# ── T14: Task Plan (Batch) generation ────────────────────────────────────────

_GOOD_BATCH = json.dumps({
    "batch_objective": "迁移 Web 层的任务批次",
    "batch_scope": ["路由", "依赖替换"],
    "permission_boundary": "仅限 source/ 写",
    "validation_strategy": "逐任务回归",
    "exception_policy": "失败即升级 Gate",
    "task_plans": [
        {"objective": "替换 Flask 路由", "scope": ["路由"], "inputs": ["app.py"],
         "expected_outputs": ["migrated_routes"], "risk_level": "L2",
         "permission_boundary": "source/", "required_resources": [],
         "model_policy_override": None, "validation_method": "回归测试",
         "expected_artifacts": ["routes.py"], "expected_evidence": ["路由对照"],
         "title": "路由迁移"},
        {"objective": "替换不兼容依赖（高风险）", "scope": ["依赖替换"], "inputs": ["requirements.txt"],
         "expected_outputs": ["new_deps"], "risk_level": "L4",
         "permission_boundary": "source/", "required_resources": ["转换工具X"],
         "model_policy_override": None, "validation_method": "构建+回归",
         "expected_artifacts": ["requirements.txt"], "expected_evidence": ["依赖对照"],
         "title": "依赖替换"},
    ],
})


async def _make_stage_plan(svc):
    res = await svc.generate_stage_plan("proj-t14", run_id="run-14", user_goal="迁移")
    return res.stage_plan_id


async def test_task_plan_requires_existing_stage_plan():
    """§3.2-1: unknown stage_plan_ref → failed, no fabrication."""
    svc = PlanningService(gateway=_FakeGateway(
        overall="available", call_result={"status": "completed", "content": _GOOD_BATCH, "model": "m"}))
    res = await svc.generate_task_plans("proj-t14", "sp-does-not-exist")
    assert res.status == "failed"
    assert "stage_plan_not_found" in res.reason
    assert res.task_plan_ids == []


async def test_task_plan_no_model_blocked():
    svc = PlanningService(gateway=_FakeGateway(overall="not_configured"))
    res = await svc.generate_task_plans("proj-t14", "sp-anything")
    assert res.status == "blocked" and "no_model_key" in res.reason


async def test_task_plan_batch_persists_and_links_stage_plan():
    svc = PlanningService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": _GOOD, "model": "m"}))
    sp_id = await _make_stage_plan(svc)

    svc._gateway = _FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": _GOOD_BATCH, "model": "deepseek/chat"})
    res = await svc.generate_task_plans("proj-t14", sp_id, run_id="run-14")
    assert res.status == "completed"
    assert res.batch_id.startswith("tpb-")
    assert len(res.task_plan_ids) == 2
    assert res.batch_risk_level == "L4"        # §4.2-3: max task risk
    assert res.gate_required is True           # §4.2-4: high-risk → Gate

    from app.core.database import get_session
    from app.models.stage_plan import TaskPlan, StagePlan
    db = get_session()
    try:
        rows = db.query(TaskPlan).filter(TaskPlan.batch_id == res.batch_id).all()
        assert len(rows) == 2
        assert all(r.stage_plan_ref == sp_id for r in rows)   # §3.2-1 traceable
        assert all(r.status == "draft" for r in rows)
        # batch metadata recorded on the owning Stage Plan (no separate batch table)
        sp = db.get(StagePlan, sp_id)
        assert sp.plan_detail["task_plan_batch"]["batch_risk_level"] == "L4"
        assert sp.plan_detail["task_plan_batch"]["task_plan_refs"] == res.task_plan_ids
    finally:
        db.close()


async def test_task_plan_empty_batch_fails_not_completed():
    """R11-7 (B-P3-NO-TASKPLANS) honesty: a model reply with no/unparseable task_plans
    must NOT be judged completed with an empty batch (which let P3 review pass while the
    TaskGraph then failed no_task_plans). Empty batch → failed, nothing persisted."""
    svc = PlanningService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": _GOOD, "model": "m"}))
    sp_id = await _make_stage_plan(svc)

    # (c) model returns valid JSON but an empty task_plans array
    svc._gateway = _FakeGateway(
        overall="available",
        call_result={"status": "completed",
                     "content": json.dumps({"batch_objective": "空", "task_plans": []}),
                     "model": "m"})
    res = await svc.generate_task_plans("proj-t14", sp_id, run_id="run-14")
    assert res.status == "failed"
    assert res.task_plan_ids == []
    assert "no_task_plans_generated" in res.reason

    # (b) model reply not parseable → parse_error, still failed (not completed)
    svc._gateway = _FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": "not json at all", "model": "m"})
    res2 = await svc.generate_task_plans("proj-t14", sp_id, run_id="run-14")
    assert res2.status == "failed"
    assert res2.parse_error is True
    assert "task_plan_parse_error" in res2.reason

    # nothing persisted for the empty batches
    from app.core.database import get_session
    from app.models.stage_plan import TaskPlan
    db = get_session()
    try:
        assert db.query(TaskPlan).filter(TaskPlan.stage_plan_ref == sp_id).count() == 0
    finally:
        db.close()



# ── T15: TaskGraph generation (必生, Q-R10-3) ─────────────────────────────────

async def _stage_plan_with_tasks(svc, project_id="proj-t15"):
    """Set up a Stage Plan + a 2-task batch (L2 + L4) for graph generation."""
    svc._gateway = _FakeGateway(overall="available",
                                call_result={"status": "completed", "content": _GOOD, "model": "m"})
    sp_id = (await svc.generate_stage_plan(project_id, run_id="run-15")).stage_plan_id
    svc._gateway = _FakeGateway(overall="available",
                                call_result={"status": "completed", "content": _GOOD_BATCH, "model": "m"})
    await svc.generate_task_plans(project_id, sp_id, run_id="run-15")
    return sp_id


async def test_task_graph_no_model_blocked():
    svc = PlanningService(gateway=_FakeGateway(overall="not_configured"))
    res = await svc.generate_task_graph("proj-t15", "sp-x")
    assert res.status == "blocked" and "no_model_key" in res.reason


async def test_task_graph_no_task_plans_fails():
    """必生 needs task source: a Stage Plan with no Task Plans → failed."""
    svc = PlanningService(gateway=_FakeGateway(
        overall="available", call_result={"status": "completed", "content": _GOOD, "model": "m"}))
    sp_id = (await svc.generate_stage_plan("proj-t15b", run_id="r")).stage_plan_id
    svc._gateway = _FakeGateway(overall="available",
                                call_result={"status": "completed", "content": '{"edges": []}', "model": "m"})
    res = await svc.generate_task_graph("proj-t15b", sp_id)
    assert res.status == "failed" and "no_task_plans" in res.reason


async def test_task_graph_generated_valid_from_llm():
    svc = PlanningService(gateway=_FakeGateway())
    sp_id = await _stage_plan_with_tasks(svc, "proj-t15c")
    # LLM proposes a valid sequence edge 0→1
    svc._gateway = _FakeGateway(overall="available", call_result={
        "status": "completed", "model": "deepseek/chat",
        "content": '{"edges": [{"source_index": 0, "target_index": 1, "edge_type": "sequence"}]}'})
    res = await svc.generate_task_graph("proj-t15c", sp_id, run_id="run-15")
    assert res.status == "completed"
    assert res.task_graph_id.startswith("tg-")
    assert res.node_count == 2 and res.edge_count == 1
    assert res.degraded is False

    from app.core.database import get_session
    from app.models.task_graph import TaskGraph, TaskNode
    from app.services.task_graph_service import validate_edges
    db = get_session()
    try:
        tg = db.get(TaskGraph, res.task_graph_id)
        assert tg.graph_status == "draft" and tg.stage_plan_ref == sp_id
        assert validate_edges(tg.edges).valid          # persisted edges pass T5
        nodes = db.query(TaskNode).filter(TaskNode.task_graph_id == res.task_graph_id).all()
        assert len(nodes) == 2
        # high-risk (L4) node → its edge carries gate_required (红线)
        hr_edge = [e for e in tg.edges if e.get("risk_level") == "L4"]
        assert hr_edge and hr_edge[0]["gate_policy"]["gate_required"] is True
    finally:
        db.close()


async def test_task_graph_degrades_to_single_chain_on_bad_edges():
    """Invalid/empty LLM edges → degrade to guaranteed-valid single chain (最简退化)."""
    svc = PlanningService(gateway=_FakeGateway())
    sp_id = await _stage_plan_with_tasks(svc, "proj-t15d")
    # out-of-range indices → no valid candidate → degrade
    svc._gateway = _FakeGateway(overall="available", call_result={
        "status": "completed", "model": "m",
        "content": '{"edges": [{"source_index": 9, "target_index": 42, "edge_type": "sequence"}]}'})
    res = await svc.generate_task_graph("proj-t15d", sp_id)
    assert res.status == "completed"
    assert res.degraded is True
    assert res.edge_count == 1        # single chain over 2 nodes
    assert res.validation_errors      # honest: records why it degraded

    from app.core.database import get_session
    from app.models.task_graph import TaskGraph
    from app.services.task_graph_service import validate_edges
    db = get_session()
    try:
        tg = db.get(TaskGraph, res.task_graph_id)
        assert validate_edges(tg.edges).valid    # degraded graph is still valid
    finally:
        db.close()


# ── T16: P3 Evidence (§5.7 six items) ─────────────────────────────────────────

class _FakeAet:
    def __init__(self):
        self.writes = []

    def write_evidence(self, **kw):
        self.writes.append(kw)
        return {**kw, "created_at": "t"}


async def _full_p3(svc, project_id="proj-t16"):
    """Run T13→T14→T15 and return (sp_res, batch_res, tg_res)."""
    svc._gateway = _FakeGateway(overall="available",
                                call_result={"status": "completed", "content": _GOOD, "model": "m"})
    sp = await svc.generate_stage_plan(project_id, run_id="run-16")
    svc._gateway = _FakeGateway(overall="available",
                                call_result={"status": "completed", "content": _GOOD_BATCH, "model": "m"})
    batch = await svc.generate_task_plans(project_id, sp.stage_plan_id, run_id="run-16")
    svc._gateway = _FakeGateway(overall="available", call_result={
        "status": "completed", "model": "m",
        "content": '{"edges": [{"source_index": 0, "target_index": 1, "edge_type": "sequence"}]}'})
    tg = await svc.generate_task_graph(project_id, sp.stage_plan_id, run_id="run-16")
    return sp, batch, tg


async def test_p3_evidence_six_items_persisted_and_traceable():
    svc = PlanningService(gateway=_FakeGateway())
    sp, batch, tg = await _full_p3(svc, "proj-t16a")
    aet = _FakeAet()
    persisted = svc.persist_p3_evidence("proj-t16a", sp, batch, tg, aet_service=aet)
    assert len(persisted) == 6 and len(aet.writes) == 6
    types = {w["evidence_type"] for w in aet.writes}
    assert types == {"plan_provenance", "task_coverage", "edge_strategy_explicit",
                     "high_risk_identified", "validation_in_plan", "open_uncertainties"}
    assert all(w["stage"] == "p3" and w["source"] == "p3_planning" for w in aet.writes)
    # §5.7-2: task coverage traces to real task plan refs
    cov = next(w for w in aet.writes if w["evidence_type"] == "task_coverage")
    assert cov["extra"]["count"] == 2 and len(cov["extra"]["task_plan_refs"]) == 2
    # §5.7-4: high-risk L4 task identified
    hr = next(w for w in aet.writes if w["evidence_type"] == "high_risk_identified")
    assert any(i["risk_level"] == "L4" for i in hr["extra"]["items"])
    # §5.7-3: edge strategy explicit + validated, traces to real graph
    es = next(w for w in aet.writes if w["evidence_type"] == "edge_strategy_explicit")
    assert es["extra"]["task_graph_ref"] == tg.task_graph_id and es["extra"]["validated"] is True


async def test_p3_evidence_not_written_when_incomplete():
    """§5.10: an incomplete plan (blocked task graph) never fakes completed Evidence."""
    svc = PlanningService(gateway=_FakeGateway())
    sp, batch, _ = await _full_p3(svc, "proj-t16b")
    blocked_tg = TaskGraphResult(status="blocked", reason="no_model_key")
    aet = _FakeAet()
    persisted = svc.persist_p3_evidence("proj-t16b", sp, batch, blocked_tg, aet_service=aet)
    assert persisted == [] and aet.writes == []
