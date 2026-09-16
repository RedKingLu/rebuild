"""Stage service — read-only stage state + stage-plan persistence + real promotion.

R10 T12: get_stages / submit_stage_plan are REAL — stage plans persist to the
`stage_plan` table (models/stage_plan.py, T4) and stages report their real
stage_plan_ref. promote() routes through the real Gate. (Was R4 mock.)
"""

from typing import Optional
import uuid

from sqlalchemy.orm import Session

from app.schemas.stage import StageResponse, StagePlanRequest, PromotionRequest, PromotionDecision
from app.core.status import STAGE_STATUSES


P_STAGES = [
    ("P0", "接入"),
    ("P1", "建档"),
    ("P2", "评估"),
    ("P3", "规划"),
    ("P4", "执行"),
    ("P5", "验证"),
    ("P6", "交付"),
]


class StageService:
    def __init__(self, services):
        self._svc = services

    def _db(self) -> Session:
        from app.core.database import get_session
        return get_session()

    def _latest_stage_plan(self, db: Session, project_id: str, stage_code: str):
        """Latest StagePlan for (project, stage) by version then recency (real ref)."""
        from app.models.stage_plan import StagePlan
        return (db.query(StagePlan)
                .filter(StagePlan.project_id == project_id,
                        StagePlan.stage == stage_code.lower())
                .order_by(StagePlan.version.desc(), StagePlan.created_at.desc())
                .first())

    def get_stages(self, project_id: str, run_id: str) -> list[StageResponse]:
        """Return all P0-P6 stages for a run, with real stage_plan_ref from DB."""
        run = self._svc.run_service.get(run_id)
        db = self._db()
        try:
            stages = []
            for code, name in P_STAGES:
                status = "not_enabled"
                if run and run.stage_status:
                    # stage_status keys are written both cased (create=lower, sync=upper) — tolerate both
                    status = (run.stage_status.get(code)
                              or run.stage_status.get(code.lower())
                              or "not_enabled")
                sp = self._latest_stage_plan(db, project_id, code)
                stages.append(StageResponse(
                    stage_code=code,
                    stage_name=f"{code} {name}",
                    stage_status=status,
                    stage_plan_ref=sp.stage_plan_id if sp else None,
                    source_status="real",
                    transition_mode="real",
                ))
            return stages
        finally:
            db.close()

    def get_stage(self, project_id: str, run_id: str, stage_code: str) -> Optional[StageResponse]:
        stages = self.get_stages(project_id, run_id)
        for s in stages:
            if s.stage_code.upper() == stage_code.upper():
                return s
        return None

    def submit_stage_plan(self, project_id: str, run_id: str, stage: str, req: StagePlanRequest) -> dict:
        """Persist a submitted stage plan to the stage_plan table (real, R10 T12)."""
        from app.models.stage_plan import StagePlan
        db = self._db()
        try:
            sp = StagePlan(
                project_id=project_id,
                run_id=run_id or None,
                stage=(stage or "").lower() or None,
                plan_status="under_review",
                plan_summary=req.plan_summary,
                plan_detail=req.plan_detail or {},
            )
            db.add(sp)
            db.commit()
            db.refresh(sp)
            return {
                "stage_plan_id": sp.stage_plan_id,
                "project_id": project_id,
                "run_id": run_id,
                "stage": stage,
                "plan_summary": sp.plan_summary,
                "status": sp.plan_status,
                "version": sp.version,
                "source_status": "real",
                "transition_mode": "real",
            }
        finally:
            db.close()

    def promote(self, project_id: str, run_id: str, stage: str, req: PromotionDecision,
                drive_promotion: bool = True, target_gate_id: str | None = None) -> dict:
        """Resolve the stage-promotion Gate for this stage (R9-3F).

        Routes the decision through GateService.decide(), which advances Project/Run state
        and writes Audit. Raises ValueError on illegal decision or missing Gate
        (routes map ValueError to HTTP 400).

        W9: when the decision is already driving a LangGraph checkpoint thread
        (thread_id=run_id), the caller passes drive_promotion=False so the graph
        advances the stage (single thread, no double-advancement); GateService
        still records the decision + Audit.

        ── B-ACC-PROMOTE-DIRECT-RUNBLIND ────────────────────────────────────────
        `target_gate_id`（解除条件 ①）：调用方**已经解析好**的被决策对象。
        `routes_stages.decide_promotion()` 在进入本函数之前就用
        `_resolve_promotion_target()` 按 (run_id, stage) 指名了 Gate A，并**对 Gate A**
        跑了 R17-2 空壳晋级校验。旧实现从头到尾不读 `req.gate_id`、也不接受任何入参，
        而是自己按 stage **全局**重查一遍（无 `run_id` 过滤）⇒ 可能挑中另一个 run 的
        Gate B ⇒ **校验的对象与执行的对象不是同一个**，而 Gate B 若属于一个没有真实
        产物的 run，它就借 Gate A 的产物通过了空壳晋级校验（与已修的
        `B-ACC-PROMOTION-DECISION-NOGUARD` 同一形状，只是换了一条路径）。
        ⇒ 传入即用，**不在内部重新查找**（复用批次一的解析结果，不造第二份查找逻辑）。

        内部查找仅作兜底（解除条件 ②③），且已补 `run_id` 过滤 + 去掉 project 级
        `get_active()` 兜底，详见下方分支注释。
        """
        from app.schemas.gate import GateDecisionRequest

        gate = None
        # ① 调用方已指名（routes_stages 的直连分支恒走这条）——不再自己查。
        resolved_id = (target_gate_id or getattr(req, "gate_id", None) or "").strip()
        if resolved_id:
            gate = self._svc.gate_service.get(resolved_id)
            if gate is None:
                raise ValueError(f"Gate {resolved_id} 不存在，无法对其提交晋级决策")
        else:
            # ② 兜底：无调用方指名时（旧签名的既有调用方 / 直接调 service 的测试）按
            #    (run_id, stage, gate_type, gate_status) 查找。**run_id 过滤是本次新增的**
            #    —— 旧实现只过滤 stage/gate_type/gate_status，跨 run 命中完全可能。
            #    `list_by_project` 按 `Gate.gate_id` 排序，而 gate_id 是 `gate-{uuid hex}`
            #    与创建时间无关 ⇒ 多命中时命中哪个取决于 uuid 字符串序。故多命中时**不猜**，
            #    直接报错要求指名（与 routes_stages._resolve_promotion_target 的 409 同口径）。
            candidates = [
                g for g in self._svc.gate_service.list_by_project(project_id)
                if (g.stage or "").lower() == (stage or "").lower()
                and g.gate_type == "stage_promotion"
                and g.gate_status == "waiting_decision"
                and (g.run_id or "") == (run_id or "")
            ]
            if len(candidates) > 1:
                raise ValueError(
                    f"该 run+stage 有 {len(candidates)} 个待决晋级 Gate："
                    f"{sorted(g.gate_id for g in candidates)}，无法判定要决策哪一个；请传 gate_id 指名。")
            if candidates:
                gate = candidates[0]
            # ③ 旧实现在此还有 `gate = get_active(project_id)` 兜底 —— **已删除**。
            #    它是 project 级、连 stage 都不过滤，且叠加 B-V262-ACTIVEGATE-NOORDER
            #    （无 ORDER BY，陈旧 L4 Gate 永久遮蔽）⇒ 一次 p3 的决策可以落到一个
            #    action_approval Gate 或另一个 run 的 Gate 上。删除它是本条解除条件 ③
            #    的"一并评估"结论：这个兜底除了扩大误命中面之外没有任何正确用途，
            #    真正无待决 Gate 时下面那句 ValueError 才是正确回答。
        if gate is None:
            raise ValueError(f"无待决 Gate 可决策（project={project_id}, stage={stage}）")

        g, audit = self._svc.gate_service.decide(
            gate.gate_id, GateDecisionRequest(decision=req.decision, reason=req.reason),
            drive_promotion=drive_promotion,
        )
        return {
            "promotion_id": f"promo-{uuid.uuid4().hex[:8]}",
            "gate_id": gate.gate_id,
            "from_stage": stage,
            "decision": req.decision,
            "gate_status": g.gate_status,
            "audit_ref": g.audit_ref,
            "transition_mode": "real",
        }
