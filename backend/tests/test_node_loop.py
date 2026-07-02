"""R10 T2 tests: NodeLoop 9-step executor.

Evidence for T2 (planning §5 V1/V6): every step has a real code path; Step 1
missing input → blocked; Step 3 out-of-scope → Plan Delta + Gate (waiting_gate);
Step 8 calls the independent Acceptance; Step 9 routes back to a TaskGraph edge
hint; three-mode differences (Manual substantive → Gate, Auto L4/L5 → Gate);
task_node_run persistence.
"""

from app.services.node_loop import (
    NodeLoop, NodeSpec, NodeLoopResult,
    STEP_RECEIVED, STEP_PLAN, STEP_ROUTE, SPLIT_STRATEGIES,
)
from app.core.status import NODE_STATUSES


def _spec(**over) -> NodeSpec:
    base = dict(
        node_id="tn-1", stage="p3", project_id="proj-1",
        task_plan={"task_plan_id": "tp-1", "objective": "生成计划",
                   "expected_artifacts": ["plan"], "expected_evidence": ["ev"]},
        acceptance_criteria=["crit-a"],
        permission_boundary="workspace_write",
        risk_level="L1", mode="plan",
    )
    base.update(over)
    return NodeSpec(**base)


async def _good_execute():
    return {
        "summary": "done",
        "artifacts": ["artifacts/plan.json"],
        "evidence": [{"evidence_id": "ev-1"}],
        "trace_refs": ["trace-1"],
        "criteria_met": {"crit-a": True},
    }


async def test_full_loop_accepted_reaches_route():
    """All 9 steps traversed → accepted → completed → route 'next'."""
    loop = NodeLoop()
    res = await loop.run(_spec(), execute_fn=_good_execute)
    assert isinstance(res, NodeLoopResult)
    assert res.node_status == "completed"
    assert res.step_reached == STEP_ROUTE
    assert res.next_route == "next"
    assert res.acceptance is not None and res.acceptance["result"] == "accepted"
    # every step emitted a real event (non-empty path)
    steps = [e["step"] for e in res.events]
    assert steps[0] == STEP_RECEIVED and steps[-1] == STEP_ROUTE
    assert len(steps) == 9


async def test_step1_missing_task_plan_blocked():
    loop = NodeLoop()
    res = await loop.run(_spec(task_plan={}), execute_fn=_good_execute)
    assert res.node_status == "blocked"
    assert res.step_reached == STEP_RECEIVED
    assert res.next_route == "blocked"


async def test_step1_missing_permission_boundary_gate():
    loop = NodeLoop()
    res = await loop.run(_spec(permission_boundary=None), execute_fn=_good_execute)
    assert res.node_status == "waiting_gate"
    assert res.next_route == "gate"


async def test_step3_out_of_scope_plan_delta_gate():
    """Step 3 out-of-scope → Plan Delta + Gate."""
    loop = NodeLoop()
    res = await loop.run(_spec(), execute_fn=_good_execute,
                         node_plan={"actions": [], "out_of_scope": True, "risk_level": "L1"})
    assert res.node_status == "waiting_gate"
    assert res.step_reached == STEP_PLAN
    assert res.next_route == "gate"
    assert "Plan Delta" in res.reason


async def test_manual_mode_substantive_change_gate():
    """Manual mode: substantive modification → Gate before execute (§4.1)."""
    loop = NodeLoop()
    res = await loop.run(_spec(mode="manual"), execute_fn=_good_execute,
                         node_plan={"actions": [], "substantive_change": True, "risk_level": "L1"})
    assert res.node_status == "waiting_gate"
    assert res.next_route == "gate"


async def test_auto_mode_high_risk_gate():
    """Auto mode: L4/L5 action → Gate (§4.3-4)."""
    loop = NodeLoop()
    res = await loop.run(_spec(mode="auto", risk_level="L5"), execute_fn=_good_execute,
                         node_plan={"actions": [], "risk_level": "L5", "out_of_scope": False})
    assert res.node_status == "waiting_gate"
    assert res.next_route == "gate"


async def test_step8_missing_evidence_routes_rework():
    """Step 8 independent Acceptance: missing Evidence → rework_required (D-066)."""
    async def _no_evidence():
        return {"summary": "x", "artifacts": ["a"], "evidence": [],
                "trace_refs": ["t"], "criteria_met": {"crit-a": True}}
    loop = NodeLoop()
    res = await loop.run(_spec(), execute_fn=_no_evidence)
    assert res.node_status == "rework_required"
    assert res.next_route == "rework"
    assert res.acceptance["result"] == "rework_required"


async def test_execute_raises_hard_failure():
    async def _boom():
        raise RuntimeError("worker crashed")
    loop = NodeLoop(max_rounds=1)
    res = await loop.run(_spec(), execute_fn=_boom)
    assert res.node_status == "failed"
    assert res.next_route == "failure"


async def test_result_status_always_valid_enum():
    loop = NodeLoop()
    for over in ({}, {"permission_boundary": None}, {"task_plan": {}}):
        res = await loop.run(_spec(**over), execute_fn=_good_execute)
        assert res.node_status in NODE_STATUSES


async def test_split_strategy_nested_loop_needs_exit():
    """nested_loop without exit condition falls back to inline (§Step4-3)."""
    loop = NodeLoop()
    res = await loop.run(_spec(split_strategy="nested_loop"), execute_fn=_good_execute)
    split_event = next(e for e in res.events if e["step"] == "split_decision")
    assert split_event["split_strategy"] == "inline"
    # valid strategy passes through
    res2 = await loop.run(_spec(split_strategy="serial"), execute_fn=_good_execute)
    split2 = next(e for e in res2.events if e["step"] == "split_decision")
    assert split2["split_strategy"] == "serial"


async def test_task_node_run_persisted(client):
    """T1 persistence: a TaskNodeRun row is written with terminal node_status."""
    from app.core.database import get_session
    from app.models.task_node_run import TaskNodeRun
    db = get_session()
    try:
        loop = NodeLoop(db=db)
        res = await loop.run(_spec(task_graph_run_id="tgr-1", run_id="run-1"),
                             execute_fn=_good_execute)
        assert res.task_node_run_id is not None
        row = db.get(TaskNodeRun, res.task_node_run_id)
        assert row is not None
        assert row.node_status == "completed"
        assert row.node_id == "tn-1"
        assert row.task_graph_run_id == "tgr-1"
        assert row.completed_at is not None
        assert row.artifact_refs  # refs persisted at Step 9
    finally:
        db.close()
