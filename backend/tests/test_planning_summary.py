"""R10 T19 tests: P3 planning-summary read endpoint (backs StagePageP3).

Proves GET /planning-summary reads the P3 plan from the DB (stage_plan / task_plan /
task_graph / task_node) into the Stage Plan + Task Plan Batch + TaskGraph (nodes +
edges) structure StagePageP3's DAG view renders, and returns an honest empty state
when P3 has produced no plan. Fully offline: seeds DB rows directly, no LLM/graph.
"""

from app.core.database import get_session
from app.models.stage_plan import StagePlan, TaskPlan
from app.models.task_graph import TaskGraph, TaskNode


def _mk_project(client) -> str:
    return client.post("/api/projects", json={
        "name": "T19 P3", "source_type": "manual",
    }).json()["data"]["project_id"]


def _seed_p3_plan(pid: str) -> dict:
    """Seed a Stage Plan + 2 Task Plans + a TaskGraph (2 nodes, 1 sequence edge)."""
    db = get_session()
    try:
        sp = StagePlan(
            project_id=pid, stage="p3", plan_status="draft",
            objective="将 x86 服务迁移到信创平台",
            scope={"scope": ["源码适配", "依赖替换"], "out_of_scope": ["重写业务逻辑"]},
            risk_level="L4", permission_boundary="workspace-only",
            plan_detail={"completion_criteria": ["编译通过", "冒烟测试通过"],
                         "validation_strategy": "P5 集成测试 + 性能基线对比",
                         "model_used": "glm-5.2",
                         "task_plan_batch": {"batch_id": "tpb-t19test",
                                             "batch_objective": "迁移任务批次",
                                             "batch_risk_level": "L4", "gate_required": True}},
        )
        db.add(sp)
        db.flush()
        sp_id = sp.stage_plan_id
        t1 = TaskPlan(project_id=pid, stage="p3", stage_plan_ref=sp_id, batch_id="tpb-t19test",
                      title="源码字节序适配", objective="适配大小端", risk_level="L3",
                      validation_method="单元测试", status="draft")
        t2 = TaskPlan(project_id=pid, stage="p3", stage_plan_ref=sp_id, batch_id="tpb-t19test",
                      title="替换闭源依赖", objective="换信创版本", risk_level="L4",
                      validation_method="集成测试", status="draft")
        db.add_all([t1, t2])
        db.flush()
        tg = TaskGraph(project_id=pid, stage="p3", stage_plan_ref=sp_id,
                       task_plan_refs=[t1.task_plan_id, t2.task_plan_id],
                       title="P3 TaskGraph", graph_status="draft", version=1, edges=[])
        db.add(tg)
        db.flush()
        n1 = TaskNode(node_id="tn-t19a", task_graph_id=tg.task_graph_id, project_id=pid,
                      stage="p3", node_type="execution", title="源码字节序适配", risk_level="L3")
        n2 = TaskNode(node_id="tn-t19b", task_graph_id=tg.task_graph_id, project_id=pid,
                      stage="p3", node_type="execution", title="替换闭源依赖", risk_level="L4")
        db.add_all([n1, n2])
        tg.edges = [{"edge_id": "e-1", "source_node_id": "tn-t19a",
                     "target_node_id": "tn-t19b", "edge_type": "sequence"}]
        db.commit()
        return {"sp_id": sp_id, "tg_id": tg.task_graph_id}
    finally:
        db.close()


def test_planning_summary_empty_when_not_planned(client):
    pid = _mk_project(client)
    data = client.get(f"/api/projects/{pid}/planning-summary").json()["data"]
    assert data["available"] is False        # honest empty state, not 404 / not fabricated
    assert "reason" in data


def test_planning_summary_returns_full_plan(client):
    pid = _mk_project(client)
    refs = _seed_p3_plan(pid)
    data = client.get(f"/api/projects/{pid}/planning-summary").json()["data"]

    assert data["available"] is True
    assert data["model_used"] == "glm-5.2"          # model id (not a Key), for the page banner
    # Stage Plan
    sp = data["stage_plan"]
    assert sp["stage_plan_id"] == refs["sp_id"]
    assert sp["risk_level"] == "L4"
    assert sp["scope"]["scope"] == ["源码适配", "依赖替换"]
    assert sp["scope"]["out_of_scope"] == ["重写业务逻辑"]
    assert sp["validation_strategy"].startswith("P5")
    assert "编译通过" in sp["completion_criteria"]
    # Task Plan Batch
    b = data["task_batch"]
    assert b["task_count"] == 2 and b["gate_required"] is True and b["batch_risk_level"] == "L4"
    assert len(data["task_plans"]) == 2
    assert data["task_plans"][0]["validation_method"] == "单元测试"
    # TaskGraph DAG (nodes + edges for the view)
    tg = data["task_graph"]
    assert tg["task_graph_id"] == refs["tg_id"]
    assert tg["node_count"] == 2 and tg["edge_count"] == 1
    assert {n["node_id"] for n in tg["nodes"]} == {"tn-t19a", "tn-t19b"}
    assert tg["edges"][0]["edge_type"] == "sequence"
    assert tg["edges"][0]["source_node_id"] == "tn-t19a"


def test_planning_summary_404_for_unknown_project(client):
    assert client.get("/api/projects/none-xyz/planning-summary").status_code == 404
