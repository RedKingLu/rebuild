"""LangGraph node functions for the P0-P6 orchestration graph (R9-5-1, T3 + T9).

Design — work/gate node separation (RK: interrupt re-runs the whole node on resume):
  {stage}_work : runs the stage business via StageLoop (small loop, D-091), produces
                 the three reports (D-092), creates the promotion Gate. Runs once per
                 entry; re-runs only on request_changes (rework).
  {stage}_gate : calls interrupt() to pause for the user Gate decision; on resume,
                 applies the decision via the gate backend and emits routing state.
                 Kept minimal so resume re-execution is cheap and side-effect-safe.

Stage handlers are pluggable (register_handler): P0/P1 wire to real services
(SourceMaterializer / FullStackProfiler / ReviewPass) in 阶段 C (T10/T11); P2-P6 are
`future_r10` stubs that keep the graph structurally walkable without doing business
(00-总规划 §1.2, G7 诚实标记). Tests inject fakes.
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


# ── Node factories ────────────────────────────────────────────────────────
def make_work_node(stage: str) -> Callable[[GraphState], Awaitable[dict]]:
    async def work(state: GraphState) -> dict:
        project_id = state.get("project_id", "")
        run_id = state.get("run_id", "")
        handler = get_handler(stage)

        if handler is None:
            # P2-P6 stub: structurally walkable, no business (future_r10, G7 honest)
            return {
                "current_stage": stage,
                "stage_status": {stage: "future_r10"},
                "events": [_ev(stage, "future_r10",
                               f"{stage} node is a future_r10 stub (business in R10-R12)")],
            }

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
        pg = state.get("pending_gate") or {}
        gate_id = pg.get("gate_id", "")

        # Pause here for the user's Gate decision. On resume, `decision` is the
        # value passed via Command(resume=...). P121 HITL resume接入点 (R9-5-7).
        decision = interrupt({"gate_id": gate_id, "stage": stage,
                              "type": "stage_promotion"})
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
            nxt = next_stage(stage)
            return f"{nxt}_work" if nxt else "__end__"
        if decision == "reject":
            return "__end__"
        # request_changes (or unknown) → rework current stage
        return f"{stage}_work"
    return route
