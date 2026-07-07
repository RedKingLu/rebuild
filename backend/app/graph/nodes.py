"""LangGraph node functions for the P0-P6 orchestration graph (R9-5-1, T3 + T9).

Design — work/gate node separation (RK: interrupt re-runs the whole node on resume):
  {stage}_work : runs the stage business via StageLoop (small loop, D-091), produces
                 the three reports (D-092), creates the promotion Gate. Runs once per
                 entry; re-runs only on request_changes (rework).
  {stage}_gate : calls interrupt() to pause for the user Gate decision; on resume,
                 applies the decision via the gate backend and emits routing state.
                 Kept minimal so resume re-execution is cheap and side-effect-safe.

Stage handlers are pluggable (register_handler): P0/P1/P2 wire to real services
(SourceMaterializer / FullStackProfiler / AssessmentService via ReviewPass); P3/P4/P5
are real/skeleton handlers (P3 planning / P4 TaskGraph execution / P5 verification
skeleton — C3 reads P4 input + creates validation plan, C5 runs real commands). P6
stays a future stub (00-总规划 §1.2, G7 诚实标记). Tests inject fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Protocol

from langgraph.types import interrupt

from app.graph.state import GraphState, next_stage
from app.graph.stage_loop import StageLoop
from app.services.review_pass import ReviewResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ev(stage: str, action: str, summary: str, **extra) -> dict:
    return {"stage": stage, "action": action, "summary": summary,
            "at": _now(), **extra}


# ── Stage handler registry ──────────────────────────────────────────────
class StageHandler(Protocol):
    """Real business for a stage. execute returns a result dict; review judges it."""
    goal: str
    acceptance_criteria: List[str]
    planned_actions: List[str]

    async def execute(self, state: GraphState) -> dict: ...
    def review(self, result: dict) -> ReviewResult: ...


_HANDLERS: Dict[str, StageHandler] = {}


def register_handler(stage: str, handler: StageHandler) -> None:
    _HANDLERS[stage] = handler


def get_handler(stage: str) -> Optional[StageHandler]:
    return _HANDLERS.get(stage)


def clear_handlers() -> None:  # test helper
    _HANDLERS.clear()


# ── Gate backend (DB-persisted Gate create/decide) ───────────────────────
class GateBackend(Protocol):
    def create(self, *, project_id: str, run_id: str, stage: str,
               artifact_refs: List[str],
               gate_type: str = "stage_promotion",
               metadata: Optional[dict] = None) -> str: ...
    def decide(self, *, gate_id: str, decision: str) -> None: ...


_gate_backend: Optional[GateBackend] = None


def set_gate_backend(backend: Optional[GateBackend]) -> None:
    global _gate_backend
    _gate_backend = backend


# tracer/auditor are optional process singletons injected by the app/runtime
_tracer = None
_auditor = None


def set_tracer_auditor(tracer=None, auditor=None) -> None:
    global _tracer, _auditor
    _tracer = tracer
    _auditor = auditor


_DECISION_TO_STATUS = {
    "approve": "completed",
    "reject": "blocked",
    "request_changes": "changes_requested",
}


# ── B-PLAN-1: pre-execution plan review (D-025 / D-026) ─────────────────────
def _write_stage_plan_report(project_id: str, stage: str, handler) -> str:
    """Produce the stage's 起始计划报告 ({stage}_start_plan.json) BEFORE execution so
    the user can审核 it at the plan_review Gate. Same content StageLoop would write —
    from the handler's declared goal / acceptance_criteria / planned_actions."""
    from app.graph.stage_reports import StageReports
    reports = StageReports(project_id, stage)
    return reports.start_plan(
        goal=getattr(handler, "goal", f"{stage} stage"),
        acceptance_criteria=getattr(handler, "acceptance_criteria", []),
        planned_actions=getattr(handler, "planned_actions", []),
    )


async def _run_plan_review(stage: str, project_id: str, run_id: str, mode: str,
                           handler) -> str:
    """Run the pre-execution plan_review Gate for manual/plan mode and return the
    user decision. Idempotent across the interrupt/resume re-run of the work node:
    on resume the node re-executes from the top, so an already-created (or decided)
    plan_review Gate is reused/short-circuited instead of duplicated (D-025/D-026)."""
    existing = None
    if _gate_backend is not None and hasattr(_gate_backend, "find_stage_gate"):
        try:
            existing = _gate_backend.find_stage_gate(
                project_id=project_id, run_id=run_id, stage=stage,
                gate_type="plan_review")
        except Exception:
            existing = None

    if existing:
        status = existing.get("gate_status")
        if status == "approved":
            return "approve"
        if status == "rejected":
            return "reject"
        if status == "changes_requested":
            return "request_changes"
        gid = existing.get("gate_id", "")  # waiting_decision → reuse (no duplicate)
    else:
        # R17-3: plan_review gate 不挂 start_plan.json 作为审核材料（避免前端显示原始 JSON）。
        # start_plan 仍写入工作区供后续 stage_promotion gate 使用，但 plan_review 仅作欢迎 + 确认。
        plan_ref = _write_stage_plan_report(project_id, stage, handler)
        gid = ""
        if _gate_backend is not None:
            gid = _gate_backend.create(
                project_id=project_id, run_id=run_id, stage=stage,
                artifact_refs=[], gate_type="plan_review",
                metadata={"mode": mode, "plan_ref": plan_ref})

    decision = interrupt({"gate_id": gid, "stage": stage, "type": "plan_review"})
    decision = (decision or "").strip() if isinstance(decision, str) else decision
    if _gate_backend is not None and gid and decision in _DECISION_TO_STATUS:
        _gate_backend.decide(gate_id=gid, decision=decision)
    return decision if decision in _DECISION_TO_STATUS else "approve"


# ── Node factories ────────────────────────────────────────────────────────
def make_work_node(stage: str) -> Callable[[GraphState], Awaitable[dict]]:
    async def work(state: GraphState) -> dict:
        project_id = state.get("project_id", "")
        run_id = state.get("run_id", "")
        handler = get_handler(stage)

        if handler is None:
            # P7+ stub: structurally walkable, no business (future, G7 honest)
            return {
                "current_stage": stage,
                "stage_status": {stage: "future"},
                "events": [_ev(stage, "future",
                               f"{stage} node is a future stub (business in later R-series)")],
            }

        # R17-3 两阶段流程：
        #   阶段 1（plan_review）: 欢迎 + 确认开始（不显示计划内容）
        #   阶段 2（plan_presentation）: 展示生成的计划 → 用户/Agent 审核
        #   阶段 3（stage_promotion）: 执行完成 → 审核结果
        # Auto 模式跳过阶段 1 和 2，直接执行。
        mode = (state.get("execution_mode") or "").lower()
        plan_approved = state.get("plan_approved") or False

        if mode in ("manual", "plan") and not plan_approved:
            # 阶段 1: plan_review（欢迎 + 确认）
            plan_decision = await _run_plan_review(stage, project_id, run_id, mode, handler)
            if plan_decision != "approve":
                return {
                    "current_stage": stage,
                    "stage_status": {stage: "blocked"},
                    "run_status": "blocked",
                    "plan_halt": plan_decision,
                    "events": [_ev(stage, "plan_not_approved",
                                   f"{stage} 欢迎 Gate 未通过（{plan_decision}）：阶段动作未执行",
                                   decision=plan_decision)],
                }
            # 阶段 2: plan_only 生成计划 → 创建 plan_presentation gate → 等待审核
            loop = StageLoop(project_id, stage, tracer=_tracer, auditor=_auditor, max_rounds=2)
            plan_res = await loop.run(
                goal=getattr(handler, "goal", f"{stage} stage"),
                acceptance_criteria=getattr(handler, "acceptance_criteria", []),
                planned_actions=getattr(handler, "planned_actions", []),
                execute_fn=lambda: handler.execute(state),
                review_fn=handler.review,
                plan_only=True,
            )
            plan_gate_id = ""
            if _gate_backend is not None:
                plan_gate_id = _gate_backend.create(
                    project_id=project_id, run_id=run_id, stage=stage,
                    artifact_refs=plan_res.report_refs,
                    gate_type="plan_presentation",
                    metadata={"mode": mode})
            return {
                "current_stage": stage,
                "stage_status": {stage: "waiting_plan_presentation"},
                "plan_approved": True,
                "gates": {plan_gate_id: {"stage": stage, "gate_status": "waiting_decision",
                                          "gate_type": "plan_presentation"}} if plan_gate_id else {},
                "pending_gate": {"gate_id": plan_gate_id, "stage": stage,
                                 "gate_type": "plan_presentation"} if plan_gate_id else None,
                "events": [_ev(stage, "plan_presentation_created",
                               f"{stage} 接入计划已生成，等待审核")],
            }

        # 阶段 3（或 Auto 模式直接）: 执行完整 StageLoop
        loop = StageLoop(project_id, stage, tracer=_tracer, auditor=_auditor, max_rounds=2)
        res = await loop.run(
            goal=getattr(handler, "goal", f"{stage} stage"),
            acceptance_criteria=getattr(handler, "acceptance_criteria", []),
            planned_actions=getattr(handler, "planned_actions", []),
            execute_fn=lambda: handler.execute(state),
            review_fn=handler.review,
        )

        if not res.passed:
            # escalated / failed → block on a Gate too, but mark escalation honestly
            # WP-1: git/empty-source failure → source_pending gate type with retry_action
            escalation_reason = res.escalation_reason or f"{stage} loop did not pass"
            _gate_type = "stage_promotion"
            _metadata: Optional[dict] = None
            # Check rounds for empty_source issue (StageLoopResult.rounds contains issue lists)
            _has_empty_source = "empty_source" in escalation_reason
            if not _has_empty_source:
                for _rnd in (res.rounds or []):
                    for _iss in (_rnd.get("issues") or []):
                        if isinstance(_iss, dict) and _iss.get("type") == "empty_source":
                            _has_empty_source = True
                            break
                    if _has_empty_source:
                        break
            if _has_empty_source:
                _gate_type = "source_pending"
                _metadata = {
                    "retry_action": "update_source_config",
                    "retry_hint": "请补充源码凭据或切换为手动导入后重新执行",
                }
            gate_id = ""
            if _gate_backend is not None:
                gate_id = _gate_backend.create(
                    project_id=project_id, run_id=run_id, stage=stage,
                    artifact_refs=res.report_refs,
                    gate_type=_gate_type,
                    metadata=_metadata)
            return {
                "current_stage": stage,
                "stage_status": {stage: "waiting_gate"},
                "gates": {gate_id: {"stage": stage, "gate_status": "waiting_decision",
                                    "escalated": True,
                                    "gate_type": _gate_type}} if gate_id else {},
                "pending_gate": {"gate_id": gate_id, "stage": stage, "escalated": True,
                                 "gate_type": _gate_type,
                                 **({"retry_action": "update_source_config"} if _has_empty_source else {})},
                "events": [_ev(stage, "escalated_to_gate", escalation_reason,
                               gate_type=_gate_type,
                               **({"retry_action": "update_source_config"} if _has_empty_source else {}))],
            }

        # passed → create promotion Gate with the three reports + domain artifacts, then interrupt
        result = res.result if isinstance(res.result, dict) else {}
        domain_artifacts = [str(a) for a in (result.get("artifacts") or [])]
        gate_refs = list(res.report_refs) + domain_artifacts
        gate_id = ""
        if _gate_backend is not None:
            gate_id = _gate_backend.create(
                project_id=project_id, run_id=run_id, stage=stage,
                artifact_refs=gate_refs)
        artifacts_inc = ([{"stage": stage, "ref": r, "kind": "stage_report"}
                          for r in res.report_refs]
                         + [{"stage": stage, "ref": r, "kind": "domain"}
                            for r in domain_artifacts])
        return {
            "current_stage": stage,
            "stage_status": {stage: "waiting_gate"},
            "gates": {gate_id: {"stage": stage, "gate_status": "waiting_decision",
                                "artifact_refs": gate_refs}} if gate_id else {},
            "pending_gate": {"gate_id": gate_id, "stage": stage},
            "artifacts": artifacts_inc,
            "events": [_ev(stage, "promotion_gate_created",
                           f"{stage} small loop passed; promotion Gate created")],
        }

    work.__name__ = f"{stage}_work"
    return work


def make_gate_node(stage: str) -> Callable[[GraphState], Awaitable[dict]]:
    async def gate(state: GraphState) -> dict:
        # B-PLAN-1: if the work node halted at an un-approved plan_review Gate, there
        # is no promotion Gate to decide. Skip the interrupt and route to END (reject)
        # so we neither hang on a nonexistent promotion decision nor loop.
        if state.get("plan_halt"):
            ph = state.get("plan_halt")
            return {
                "last_decision": "reject",
                "plan_halt": None,
                "pending_gate": None,
                "stage_status": {stage: "blocked"},
                "run_status": "blocked",
                "events": [_ev(stage, "promotion_skipped_plan_halt",
                               f"{stage} 计划未批准（{ph}），跳过晋级门")],
            }

        pg = state.get("pending_gate") or {}
        gate_id = pg.get("gate_id", "")
        gate_type = pg.get("gate_type", "stage_promotion")

        # Pause here for the user's Gate decision. On resume, `decision` is the
        # value passed via Command(resume=...). P121 HITL resume接入点 (R9-5-7).
        # R17-3: 支持 stage_promotion + plan_presentation 两种 gate 类型。
        decision = interrupt({"gate_id": gate_id, "stage": stage, "type": gate_type})
        decision = (decision or "").strip() if isinstance(decision, str) else decision

        if _gate_backend is not None and gate_id and decision in _DECISION_TO_STATUS:
            _gate_backend.decide(gate_id=gate_id, decision=decision)

        upd: dict = {
            "last_decision": decision,
            "pending_gate": None,
            "events": [_ev(stage, "gate_decided", f"{stage} promotion Gate: {decision}")],
        }
        if gate_id:
            upd["gates"] = {gate_id: {"gate_status": {
                "approve": "approved", "reject": "rejected",
                "request_changes": "changes_requested"}.get(decision, "unknown")}}

        if decision == "approve":
            # R17-3: plan_presentation 批准 → 不进阶，由 router 路由回 work 节点执行完整 StageLoop
            if gate_type == "plan_presentation":
                upd["stage_status"] = {stage: "plan_approved"}
            else:
                nxt = next_stage(stage)
                ss = {stage: "completed"}
                if nxt:
                    ss[nxt] = "in_progress"
                    upd["current_stage"] = nxt
                else:
                    upd["run_status"] = "completed"
                upd["stage_status"] = ss
        elif decision == "reject":
            upd["stage_status"] = {stage: "blocked"}
            upd["run_status"] = "blocked"
        else:  # request_changes → rerun stage work (rework, D-091)
            upd["stage_status"] = {stage: "changes_requested"}
        return upd

    gate.__name__ = f"{stage}_gate"
    return gate


def make_router(stage: str) -> Callable[[GraphState], str]:
    """Conditional edge: route from {stage}_gate by the user's decision."""
    def route(state: GraphState) -> str:
        decision = state.get("last_decision")
        if decision == "approve":
            # R17-3: plan_presentation 批准 → 回到 work 节点执行完整 StageLoop
            #（work 节点检测到 plan_approved=True 会跳过 plan_review/plan_presentation，直接执行）
            if state.get("plan_approved"):
                return f"{stage}_work"
            nxt = next_stage(stage)
            return f"{nxt}_work" if nxt else "__end__"
        if decision == "reject":
            return "__end__"
        # request_changes (or unknown) → rework current stage
        return f"{stage}_work"
    return route
