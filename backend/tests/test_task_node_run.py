"""R10 T1 tests: TaskNodeRun runtime-instance ORM + NODE_STATUSES enum.

Evidence for T1 (planning §5 V1/V3): task_node_run table creates + CRUD works,
node_status flows through the 11-state NODE_STATUSES enum including escalate
branches, and definition/runtime split (Q-R10-1 分表) linkage holds.
"""

from datetime import datetime, timezone

from app.core.status import NODE_STATUSES
from app.core.database import get_session
from app.models.task_node_run import TaskNodeRun


def test_node_statuses_enum_complete():
    """NODE_STATUSES = §8.3 12 states (blocked added per Q-R10-5, user-resolved)."""
    assert NODE_STATUSES == [
        "pending", "running", "waiting_gate", "waiting_resource",
        "self_checking", "acceptance_checking", "completed",
        "failed", "retrying", "skipped", "rework_required", "blocked",
    ]
    # 9-step happy path + escalate branches all present
    for st in ("pending", "running", "self_checking", "acceptance_checking",
               "completed", "waiting_gate", "waiting_resource", "retrying",
               "failed", "rework_required", "skipped", "blocked"):
        assert st in NODE_STATUSES


def test_task_node_run_crud():
    """task_node_run row persists and round-trips (build-table success V1)."""
    db = get_session()
    try:
        run = TaskNodeRun(
            node_id="tn-abc12345",
            task_graph_run_id="tgr-def67890",
            task_graph_id="tg-11112222",
            project_id="proj-1",
            run_id="run-1",
            stage="p3",
            node_status="pending",
            artifact_refs=["art-1"],
            evidence_refs=["ev-1"],
        )
        db.add(run)
        db.commit()
        rid = run.task_node_run_id
        assert rid.startswith("tnr-")

        fetched = db.get(TaskNodeRun, rid)
        assert fetched is not None
        assert fetched.node_id == "tn-abc12345"
        assert fetched.task_graph_run_id == "tgr-def67890"
        assert fetched.node_status == "pending"
        assert fetched.retry_count == 0
        assert fetched.artifact_refs == ["art-1"]
        assert fetched.started_at is not None
    finally:
        db.close()


def test_task_node_run_status_transition():
    """node_status flows pending → running → ... → completed + escalate branches."""
    db = get_session()
    try:
        run = TaskNodeRun(
            node_id="tn-xyz",
            task_graph_run_id="tgr-xyz",
            project_id="proj-2",
            node_status="pending",
        )
        db.add(run)
        db.commit()
        rid = run.task_node_run_id

        # walk the 9-step happy path
        for st in ("running", "self_checking", "acceptance_checking", "completed"):
            assert st in NODE_STATUSES
            run.node_status = st
            run.updated_at = datetime.now(timezone.utc)
            db.commit()
            assert db.get(TaskNodeRun, rid).node_status == st

        # escalate branch: retry increments retry_count + failure_reason recorded
        run.node_status = "retrying"
        run.retry_count = 1
        run.failure_reason = "self-check failed round 1"
        db.commit()
        refreshed = db.get(TaskNodeRun, rid)
        assert refreshed.node_status == "retrying"
        assert refreshed.retry_count == 1
        assert refreshed.failure_reason == "self-check failed round 1"
    finally:
        db.close()
