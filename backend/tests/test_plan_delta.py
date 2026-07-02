"""R10 T8 tests: PlanDelta ORM + service (规范 §5/§9).

Evidence for T8 (planning §5 V1/V3): 9 trigger types; every change → a PlanDelta
row; high-risk / out-of-scope change → gate_required (§5.4-2); supersede preserves
the fact chain (§5.4-3/§9-5: old→superseded, new.supersedes=old, version bumped);
invalid delta_type rejected.
"""

import pytest

from app.core.database import get_session
from app.services.plan_delta_service import PlanDeltaService, DELTA_TYPES, needs_gate


def test_delta_types_nine():
    assert len(DELTA_TYPES) == 9
    for t in ("scope_change", "risk_change", "permission_change", "task_change",
              "edge_strategy_change", "model_resource_change", "validation_change",
              "blocking_adjustment", "user_requested"):
        assert t in DELTA_TYPES


def test_needs_gate_rules():
    # out-of-scope / permission → always Gate (§5.4-2)
    assert needs_gate("scope_change") is True
    assert needs_gate("permission_change") is True
    # risk escalation to L4/L5 → Gate
    assert needs_gate("risk_change", "L2→L4") is True
    assert needs_gate("edge_strategy_change", "L5") is True
    # benign change without high risk → no Gate
    assert needs_gate("validation_change") is False
    assert needs_gate("risk_change", "L1→L2") is False


def test_create_delta_persists(client):
    db = get_session()
    try:
        svc = PlanDeltaService(db=db)
        d = svc.create_delta(
            project_id="proj-1", delta_type="task_change",
            source_plan_ref="sp-1", target_plan_ref="sp-2",
            changed_fields=["tasks"], reason="新增验证任务",
            change_summary="+1 task", risk_impact="L1",
        )
        assert d.plan_delta_id.startswith("pd-")
        assert d.delta_type == "task_change"
        assert d.gate_required is False
        # round-trip
        from app.models.plan_delta import PlanDelta
        fetched = db.get(PlanDelta, d.plan_delta_id)
        assert fetched is not None
        assert fetched.source_plan_ref == "sp-1"
        assert fetched.changed_fields == ["tasks"]
    finally:
        db.close()


def test_create_delta_scope_change_requires_gate(client):
    db = get_session()
    try:
        svc = PlanDeltaService(db=db)
        d = svc.create_delta(project_id="proj-1", delta_type="scope_change",
                             reason="扩大 scope", risk_impact="L2")
        assert d.gate_required is True
    finally:
        db.close()


def test_create_delta_invalid_type_rejected(client):
    db = get_session()
    try:
        svc = PlanDeltaService(db=db)
        with pytest.raises(ValueError):
            svc.create_delta(project_id="proj-1", delta_type="teleport")
    finally:
        db.close()


def test_supersede_preserves_fact_chain(client):
    """§9-5: superseded plan is not deleted — old→superseded, new.supersedes=old."""
    db = get_session()
    try:
        from app.models.stage_plan import StagePlan
        old = StagePlan(project_id="proj-1", stage="p3", plan_status="approved",
                        objective="v1", version=1)
        new = StagePlan(project_id="proj-1", stage="p3", plan_status="draft",
                        objective="v2", version=1)
        db.add(old); db.add(new); db.commit()
        old_id, new_id = old.stage_plan_id, new.stage_plan_id

        svc = PlanDeltaService(db=db)
        updated = svc.supersede_stage_plan(old_id, new_id)
        assert updated is not None
        assert db.get(StagePlan, old_id).plan_status == "superseded"  # not deleted
        assert db.get(StagePlan, new_id).supersedes == old_id
        assert db.get(StagePlan, new_id).version == 2  # bumped
    finally:
        db.close()


def test_list_deltas(client):
    db = get_session()
    try:
        svc = PlanDeltaService(db=db)
        svc.create_delta(project_id="proj-X", delta_type="risk_change", source_plan_ref="sp-a")
        svc.create_delta(project_id="proj-X", delta_type="scope_change", source_plan_ref="sp-a")
        svc.create_delta(project_id="proj-X", delta_type="user_requested", source_plan_ref="sp-b")
        assert len(svc.list_deltas("proj-X")) == 3
        assert len(svc.list_deltas("proj-X", source_plan_ref="sp-a")) == 2
    finally:
        db.close()
