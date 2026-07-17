"""R10 T21: P2/P3 integration-path tests — the full chain the earlier unit tests
only cover in pieces:

  RealP2Handler.execute (fake LLM) → 6 outputs + Artifacts + Evidence 落库
      → GET /assessment-summary reads them back  (produce → persist → read-API)
  RealP3Handler.execute (fake LLM) → StagePlan + TaskPlan(Batch) + TaskGraph 落 DB
      → GET /planning-summary reads them back (incl. DAG nodes/edges)

Plus the §2 offline-blocked 范式 through BOTH read endpoints: no model → blocked →
no artifacts/plan → endpoints report available=false (never a fabricated completed).

Fully offline — a fake gateway dispatched by system prompt stands in for the LLM,
so this exercises the real handlers + services + endpoints without a live call and
without the real-LLM hang the handoff §2 warns about.
"""

import json
from types import SimpleNamespace

from app.graph.stage_handlers import RealP2Handler, RealP3Handler
from app.services.assessment_service import AssessmentService
from app.services.planning_service import PlanningService
from app.services import aet_service as aet_mod


_P2_CONTENT = json.dumps({
    "assessment_report": {"feasibility": "可行", "complexity": "中等"},
    "risk_list": [{"title": "内核系统调用不兼容", "risk_level": "L4",
                   "source": "profiling_summary", "basis": "直接 syscall"}],
    "blocker_list": [{"title": "缺少交叉编译器", "source": "environment.json"}],
    "uncertainty_list": [{"title": "第三方库兼容性未确认"}],
    "validation_gap_list": [{"title": "无集成测试", "source": "profiling_summary"}],
    "resource_needs": [{"title": "确定性转换器"}],
})

_P3_STAGE_PLAN = json.dumps({
    "objective": "迁移到信创平台", "scope": ["源码适配", "依赖替换"],
    "out_of_scope": ["重写业务逻辑"], "risk_level": "L4",
    "permission_boundary": "workspace-only", "gate_policy": {"high_risk": "L4 需 Gate"},
    "completion_criteria": ["编译通过"], "validation_strategy": "P5 集成测试",
})
_P3_TASK_PLANS = json.dumps({
    "batch_objective": "迁移任务批次", "validation_strategy": "集成测试",
    "task_plans": [
        {"title": "源码适配", "objective": "字节序适配", "risk_level": "L3",
         "validation_method": "单元测试", "scope": ["src"]},
        {"title": "依赖替换", "objective": "换信创版本", "risk_level": "L4",
         "validation_method": "集成测试", "scope": ["deps"]},
    ],
})
_P3_EDGES = json.dumps({"edges": [{"source_index": 0, "target_index": 1, "edge_type": "sequence"}]})


class _FakeGateway:
    """Dispatches call() by the P3 system prompt so one gateway drives the whole
    P2/P3 chain deterministically. `overall` drives the blocked path."""

    def __init__(self, overall="available"):
        self._overall = overall

    def get_status(self):
        return SimpleNamespace(overall_status=self._overall)

    def stage_model_readiness(self, **kwargs):
        ok = self._overall == "available"
        return {"available": ok, "capability_ok": ok, "reason": "" if ok else "无可用模型",
                "attempted_chain": [] if ok else [{"profile_id": "fake/m", "provider_id": "fake",
                    "model": "m", "is_fallback": False, "outcome": "not_configured",
                    "error_category": "not_configured", "error_message": "未配置"}],
                "user_actions": [{"action": "configure", "label": "配置模型 / API Key", "target": "models"}]}

    async def call(self, *, messages, **kwargs):
        sys = messages[0]["content"] if messages else ""
        if "Stage Plan" in sys and "拆解" not in sys:
            content = _P3_STAGE_PLAN
        elif "拆解出一批任务级 Task Plan" in sys:
            content = _P3_TASK_PLANS
        elif "TaskGraph 的边" in sys:
            content = _P3_EDGES
        else:  # P2 assessment
            content = _P2_CONTENT
        return {"status": "completed", "content": content, "model": "fake-model"}


def _aet():
    return aet_mod.AETService(services=None)


def _mk_project(client) -> str:
    return client.post("/api/projects", json={"name": "T21 integ", "source_type": "manual"}).json()["data"]["project_id"]


# ── P2 chain: handler produces → assessment-summary reads back ────────────────
async def test_p2_chain_handler_to_assessment_summary(client):
    pid = _mk_project(client)
    svc = AssessmentService(gateway=_FakeGateway(), aet_service=_aet())
    res = await RealP2Handler(assessment_service=svc).execute(
        {"project_id": pid, "run_id": "r1", "user_goal": "迁移到信创栈"})
    assert res["status"] == "completed" and len(res["artifacts"]) == 5

    data = client.get(f"/api/projects/{pid}/assessment-summary").json()["data"]
    assert data["available"] is True and data["analysis_only"] is True
    assert data["risk_list"][0]["risk_level"] == "L4"                 # handler output round-trips
    assert data["blocker_list"][0]["source"] == "environment.json"
    assert data["uncertainty_list"][0]["title"].startswith("第三方库")
    assert len(data["artifacts"]) == 5
    assert data["model_used"] == "fake-model"                          # from persisted Evidence


# ── P3 chain: handler produces → planning-summary reads back (incl. DAG) ──────
async def test_p3_chain_handler_to_planning_summary(client):
    pid = _mk_project(client)
    svc = PlanningService(gateway=_FakeGateway(), aet_service=_aet())
    res = await RealP3Handler(planning_service=svc).execute(
        {"project_id": pid, "run_id": "r1", "user_goal": "迁移到信创栈"})
    assert res["status"] == "completed"
    assert res["stage_plan_ref"] and res["task_graph_ref"]             # Q-R10-3 必生

    data = client.get(f"/api/projects/{pid}/planning-summary").json()["data"]
    assert data["available"] is True
    assert data["stage_plan"]["risk_level"] == "L4"
    assert data["stage_plan"]["scope"]["out_of_scope"] == ["重写业务逻辑"]
    assert data["task_batch"]["task_count"] == 2
    assert data["task_batch"]["gate_required"] is True                 # L4 batch → Gate
    # DAG view: 2 nodes + at least the sequence edge, all traceable
    tg = data["task_graph"]
    assert tg["node_count"] == 2 and tg["edge_count"] >= 1
    ids = {n["node_id"] for n in tg["nodes"]}
    assert tg["edges"][0]["source_node_id"] in ids and tg["edges"][0]["target_node_id"] in ids


# ── §2 offline-blocked 范式 through both endpoints (no LLM, no hang) ──────────
async def test_p2_p3_blocked_offline_endpoints_report_unavailable(client):
    pid = _mk_project(client)
    off = _FakeGateway(overall="not_configured")   # Q-R10-2: no model → blocked, no fallback

    p2 = await RealP2Handler(assessment_service=AssessmentService(gateway=off, aet_service=_aet())).execute({"project_id": pid})
    assert p2["status"] == "blocked" and p2["artifacts"] == [] and p2["evidence_refs"] == []
    p3 = await RealP3Handler(planning_service=PlanningService(gateway=off, aet_service=_aet())).execute({"project_id": pid})
    assert p3["status"] == "blocked" and p3["artifacts"] == []

    # endpoints report honest empty state — never a fabricated completed
    assert client.get(f"/api/projects/{pid}/assessment-summary").json()["data"]["available"] is False
    assert client.get(f"/api/projects/{pid}/planning-summary").json()["data"]["available"] is False
