"""PlanDelta service (R10 T8).

Records plan changes as immutable PlanDelta rows and manages the supersede version
chain, per `文档/03-流程与运行时/02-StagePlan-TaskPlan-TaskGraph规范.md` §5/§9:

  - §5.2: 9 trigger conditions → DELTA_TYPES
  - §5.4-2: high-risk or out-of-scope change → gate_required
  - §5.4-3 / §9-5: never silently overwrite — the superseded plan is preserved
    (supersedes/superseded_by kept), the reason recorded in a PlanDelta
  - §9-7: high-risk supersede must Gate

The version chain lives on the plan rows (StagePlan.version/supersedes, T4);
this service writes the PlanDelta audit and, optionally, applies the supersede.
"""

from __future__ import annotations

from typing import Optional

# §5.2 nine trigger conditions → delta_type vocabulary
DELTA_TYPES = [
    "scope_change",           # 1. 范围变化
    "risk_change",            # 2. 风险级别变化
    "permission_change",      # 3. 权限边界变化
    "task_change",            # 4. 增加或删除任务
    "edge_strategy_change",   # 5. 修改 TaskGraph 边策略
    "model_resource_change",  # 6. 改变模型策略或资源调用方式
    "validation_change",      # 7. 改变验证方法或验收标准
    "blocking_adjustment",    # 8. 出现阻塞项导致计划调整
    "user_requested",         # 9. 用户要求变更
]

# delta types that are inherently boundary-crossing → always Gate (§5.4-2)
_GATE_DELTA_TYPES = {"scope_change", "permission_change"}
_HIGH_RISK = ("L4", "L5")


def needs_gate(delta_type: str, risk_impact: Optional[str] = None) -> bool:
    """§5.4-2: high-risk or out-of-scope change must Gate."""
    if delta_type in _GATE_DELTA_TYPES:
        return True
    if risk_impact and any(lvl in risk_impact for lvl in _HIGH_RISK):
        return True
    return False


class PlanDeltaService:
    def __init__(self, db, tracer=None, auditor=None):
        self.db = db
        self.tracer = tracer
        self.auditor = auditor

    def create_delta(
        self,
        *,
        project_id: str,
        delta_type: str,
        source_plan_ref: Optional[str] = None,
        target_plan_ref: Optional[str] = None,
        changed_fields: Optional[list] = None,
        reason: str = "",
        change_summary: str = "",
        risk_impact: Optional[str] = None,
        permission_impact: Optional[str] = None,
        artifact_impact: Optional[dict] = None,
        evidence_impact: Optional[dict] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        created_by: Optional[str] = None,
    ):
        """Record a plan change (§5.3). Returns the persisted PlanDelta row."""
        if delta_type not in DELTA_TYPES:
            raise ValueError(f"invalid delta_type '{delta_type}' (see §5.2 / DELTA_TYPES)")
        from app.models.plan_delta import PlanDelta

        gate_required = needs_gate(delta_type, risk_impact)

        # high-risk / boundary change → Audit the escalation (§5.4-2, traceable)
        audit_ref = None
        if gate_required and self.auditor is not None:
            try:
                self.auditor.write(
                    "policy_conflict" if delta_type == "permission_change" else "high_risk_action",
                    run_id=run_id, stage=stage, project_id=project_id,
                    action="plan_delta_gate",
                    summary=f"Plan Delta requires Gate: {delta_type} — {reason or change_summary}")
                audit_ref = f"audit-pd-{delta_type}"
            except Exception as e:
                self._trace("plan_delta audit write failed", detail=str(e))

        row = PlanDelta(
            project_id=project_id, run_id=run_id, stage=stage,
            source_plan_ref=source_plan_ref, target_plan_ref=target_plan_ref,
            delta_type=delta_type, change_summary=change_summary,
            changed_fields=changed_fields or [], reason=reason,
            risk_impact=risk_impact, permission_impact=permission_impact,
            artifact_impact=artifact_impact, evidence_impact=evidence_impact,
            gate_required=gate_required, audit_ref=audit_ref, created_by=created_by,
        )
        self.db.add(row)
        self.db.commit()
        self._trace(f"plan_delta {delta_type} gate={gate_required}",
                    project_id=project_id, run_id=run_id, stage=stage)
        return row

    def supersede_stage_plan(self, old_plan_id: str, new_plan_id: str):
        """Apply the supersede version chain on StagePlan rows without deleting the
        fact chain (§9-5): old → plan_status='superseded'; new.supersedes=old,
        new.version=old.version+1. Returns the updated new-plan row (or None)."""
        from app.models.stage_plan import StagePlan
        old = self.db.get(StagePlan, old_plan_id)
        new = self.db.get(StagePlan, new_plan_id)
        if old is None or new is None:
            return None
        old.plan_status = "superseded"
        new.supersedes = old_plan_id
        new.version = (old.version or 1) + 1
        self.db.commit()
        return new

    def list_deltas(self, project_id: str, source_plan_ref: Optional[str] = None):
        from app.models.plan_delta import PlanDelta
        q = self.db.query(PlanDelta).filter(PlanDelta.project_id == project_id)
        if source_plan_ref:
            q = q.filter(PlanDelta.source_plan_ref == source_plan_ref)
        return q.all()

    def _trace(self, summary: str, **extra):
        if self.tracer is None:
            return
        try:
            self.tracer.write("plan_change", action="plan_delta", summary=summary, **extra)
        except Exception:
            pass  # tracing advisory
