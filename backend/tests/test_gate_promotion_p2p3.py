"""R10 T20 tests: P2→P3 / P3→P4 stage-promotion Gate decision (backs Gate 前端集成).

The promotion Gate mechanism is stage-agnostic (make_gate_node factory + GateService).
These prove a P2 and a P3 promotion Gate can be created and resolved through the same
API StagePageP2/P3 + GatePanel drive (approve advances the stage; reject/request_changes
resolve without advancing). Offline: no graph thread → direct promote path, no LLM.
"""

import pytest


def _ensure_task_graph(run_id: str, stage: str = "p2"):
    """R17-2 V-R17-1B-2: gate 晋级强绑阶段产物。测试需注入 task_graph 以满足校验。"""
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph
    import uuid
    db = get_session()
    try:
        tg = db.query(TaskGraph).filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage).first()
        if tg is None:
            tg = TaskGraph(
                task_graph_id=f"tg-test-{uuid.uuid4().hex[:8]}",
                project_id="",
                run_id=run_id,
                stage=stage,
                title=f"test gate {stage}",
                graph_status="completed",
            )
            db.add(tg)
            db.commit()
    finally:
        db.close()


@pytest.fixture
def project_run(client):
    pid = client.post("/api/projects", json={"name": "T20 Gate", "source_type": "manual"}).json()["data"]["project_id"]
    rid = client.post(f"/api/projects/{pid}/runs", json={"run_goal": "t20", "mode": "plan"}).json()["data"]["run_id"]
    return pid, rid


def test_p2_promotion_gate_create_and_approve(client, project_run):
    pid, rid = project_run
    _ensure_task_graph(rid, "p2")
    g = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p2/promotion-gate",
                    json={"target_stage": "P3"}).json()["data"]
    assert g["gate_type"] == "stage_promotion" and g["stage"] == "p2"
    assert set(["approve", "reject", "request_changes"]).issubset(set(g.get("options", [])))
    # approve → resolved (direct promote path; graph_driven flag present)
    r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p2/promotion-decision",
                    json={"decision": "approve", "reason": "T20 approve"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["decision"] == "approve" and data["gate_status"] == "approved"
    assert "graph_driven" in data


def _new_run(client, pid):
    return client.post(f"/api/projects/{pid}/runs", json={"run_goal": "t20", "mode": "plan"}).json()["data"]["run_id"]


def test_p3_promotion_gate_reject_and_request_changes(client, project_run):
    pid, rid = project_run
    _ensure_task_graph(rid, "p3")
    # P3→P4 gate, reject
    client.post(f"/api/projects/{pid}/runs/{rid}/stages/p3/promotion-gate", json={"target_stage": "P4"})
    r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p3/promotion-decision",
                    json={"decision": "reject", "reason": "计划不可接受"})
    assert r.json()["data"]["gate_status"] == "rejected"
    # separate run (one gate per stage+run) → request_changes → changes_requested (rework, not advance)
    rid2 = _new_run(client, pid)
    _ensure_task_graph(rid2, "p3")
    client.post(f"/api/projects/{pid}/runs/{rid2}/stages/p3/promotion-gate", json={"target_stage": "P4"})
    r2 = client.post(f"/api/projects/{pid}/runs/{rid2}/stages/p3/promotion-decision",
                     json={"decision": "request_changes", "reason": "补充边策略说明"})
    assert r2.json()["data"]["gate_status"] == "changes_requested"


def test_stage_promotion_gate_rejected_without_artifact(client, project_run):
    """R17-2 V-R17-1B-2 新增：无产物 stage_promotion gate 应被 422 拒绝。"""
    pid, rid = project_run
    # 不注入 task_graph → 无产物
    g = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p2/promotion-gate",
                    json={"target_stage": "P3"}).json()["data"]
    assert g["gate_type"] == "stage_promotion"
    r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p2/promotion-decision",
                    json={"decision": "approve", "reason": "should be rejected"})
    assert r.status_code == 422, f"无产物晋级应被 422 拒绝，实际 {r.status_code}: {r.text[:200]}"
