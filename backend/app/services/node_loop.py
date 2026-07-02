"""NodeLoop 9-step executor (R10 T2).

Implements the standard intra-node small loop defined in
`文档/03-流程与运行时/03-NodeLoop与执行模式.md` §2/§3:

  task_received → context_loaded → node_plan_created → split_decision
    → execution_finished → self_check_finished → node_package_created
    → acceptance_result_received → route_decided

Design (aligned with RK-2 / TP-1): NodeLoop is the NODE-level loop; it reuses the
SAME ReviewPass kernel (execute → self-check → retry≤N) that StageLoop uses at the
STAGE level, so the two loops do not diverge. Step 8 calls the INDEPENDENT
AcceptanceService (T3, D-082). Each run is persisted as a TaskNodeRun row (T1),
with node_status flowing through NODE_STATUSES.

Boundaries (§0): NodeLoop does NOT replace LangGraph orchestration (D-037/D-065),
does not hide TaskGraph edge strategy, does not replace Acceptance/Gate/P5. Step 9
always routes back to a TaskGraph edge-strategy hint — never an implicit jump
(§Step9). Three execution modes (§4) change Gate density, not the 9-step shape.

The actual node work (model_gateway / execution_provider / Skill / Tool / MCP
calls) is performed by the injected `execute_fn` — the Node Worker Agent — exactly
as StageLoop injects it; NodeLoop orchestrates and gates, it does not hardcode the
work. Step 2 assembles context via context_assembler by default.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from app.core.status import NODE_STATUSES
from app.services.review_pass import ReviewPass, ReviewResult
from app.services.acceptance_service import AcceptanceService

# 9-step markers (§2)
STEP_RECEIVED = "task_received"
STEP_CONTEXT = "context_loaded"
STEP_PLAN = "node_plan_created"
STEP_SPLIT = "split_decision"
STEP_EXECUTE = "execution_finished"
STEP_SELF_CHECK = "self_check_finished"
STEP_PACKAGE = "node_package_created"
STEP_ACCEPTANCE = "acceptance_result_received"
STEP_ROUTE = "route_decided"

SPLIT_STRATEGIES = ("inline", "serial", "parallel", "hybrid", "nested_loop")
_HIGH_RISK = {"L4", "L5"}

# Acceptance result → (node_status, edge-strategy route hint) — Step 9 routing table
_ROUTE_MAP = {
    "accepted": ("completed", "next"),
    "accepted_with_warning": ("completed", "next"),
    "rework_required": ("rework_required", "rework"),
    "retry_required": ("retrying", "retry"),
    "gate_required": ("waiting_gate", "gate"),
    "failed": ("failed", "failure"),
    "blocked": ("blocked", "blocked"),
    "skipped": ("skipped", "skip"),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class NodeSpec:
    """Descriptor of one Task Node execution (Step 1 inputs)."""

    node_id: str
    stage: str
    project_id: str
    task_plan: dict = field(default_factory=dict)
    acceptance_criteria: list[str] = field(default_factory=list)
    permission_boundary: Optional[str] = None
    risk_level: str = "L0"
    input_refs: list = field(default_factory=list)
    task_graph_run_id: Optional[str] = None
    run_id: Optional[str] = None
    mode: str = "plan"                # manual / plan / auto (§4)
    split_strategy: str = "inline"


@dataclass
class NodeLoopResult:
    node_id: str
    node_status: str                  # terminal value ∈ NODE_STATUSES
    step_reached: str
    next_route: str                   # edge-strategy hint: next/rework/retry/gate/failure/blocked/skip
    acceptance: Optional[dict] = None
    node_package: Optional[dict] = None
    task_node_run_id: Optional[str] = None
    reason: str = ""
    events: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_status": self.node_status,
            "step_reached": self.step_reached,
            "next_route": self.next_route,
            "acceptance": self.acceptance,
            "task_node_run_id": self.task_node_run_id,
            "reason": self.reason,
            "events": self.events,
        }


class NodeLoop:
    """Run one Task Node through the 9-step loop. Callable by the TaskGraph engine
    (T6) and by StageLoop (RK-2 seam: StageLoop → NodeLoop)."""

    def __init__(self, *, db=None, tracer=None, auditor=None,
                 acceptance_service: Optional[AcceptanceService] = None,
                 max_rounds: int = 2):
        self.db = db
        self.tracer = tracer
        self.auditor = auditor
        # independent Acceptance executor (D-082) — separate instance from the worker
        self.acceptance = acceptance_service or AcceptanceService(
            db=db, tracer=tracer, auditor=auditor)
        self.max_rounds = max_rounds

    async def run(
        self,
        spec: NodeSpec,
        *,
        execute_fn: Callable[[], "dict | Awaitable[dict]"],
        self_check_fn: Optional[Callable[[dict], list[str]]] = None,
        context_fn: Optional[Callable[[], dict]] = None,
        node_plan: Optional[dict] = None,
    ) -> NodeLoopResult:
        events: list[dict] = []
        run_row = self._create_run(spec)  # node_status=pending
        tnr_id = getattr(run_row, "task_node_run_id", None) if run_row else None

        def emit(step: str, **extra):
            events.append({"step": step, **extra})
            self._trace(f"NodeLoop {step}", spec=spec, step=step, **extra)

        # ── Step 1: receive task ─────────────────────────────────────────
        emit(STEP_RECEIVED)
        gate = self._step1_validate(spec)
        if gate is not None:
            status, route, reason = gate
            self._finalize_run(run_row, status)
            return NodeLoopResult(spec.node_id, status, STEP_RECEIVED, route,
                                  reason=reason, task_node_run_id=tnr_id, events=events)
        self._update_run(run_row, "running")

        # ── Step 2: load context (C0-C6, via context_assembler by default) ─
        context = self._step2_context(spec, context_fn)
        emit(STEP_CONTEXT, context_keys=sorted(context.keys()) if isinstance(context, dict) else [])

        # ── Step 3: node plan (+ scope / mode gate) ──────────────────────
        plan = node_plan or self._default_node_plan(spec)
        emit(STEP_PLAN, plan_actions=plan.get("actions", []))
        gate = self._step3_plan_gate(spec, plan)
        if gate is not None:
            status, route, reason = gate
            self._update_run(run_row, status)
            self._finalize_run(run_row, status)
            return NodeLoopResult(spec.node_id, status, STEP_PLAN, route,
                                  reason=reason, task_node_run_id=tnr_id, events=events)

        # ── Step 4: split decision ───────────────────────────────────────
        split = self._step4_split(spec, plan)
        emit(STEP_SPLIT, split_strategy=split)

        # ── Step 5+6: execute + self-check via ReviewPass kernel ─────────
        exec_outcome = await self._step5_execute(spec, execute_fn, self_check_fn, context)
        result = exec_outcome.get("result") or {}
        emit(STEP_EXECUTE, execute_error=exec_outcome.get("hard_fail", False))
        if exec_outcome.get("hard_fail"):
            # execute_fn never produced output — route to failure_policy, nothing to accept
            self._update_run(run_row, "failed", failure_reason=exec_outcome.get("reason"))
            self._finalize_run(run_row, "failed")
            return NodeLoopResult(spec.node_id, "failed", STEP_EXECUTE, "failure",
                                  reason=exec_outcome.get("reason", "execution failed"),
                                  task_node_run_id=tnr_id, events=events)
        self._update_run(run_row, "self_checking")
        emit(STEP_SELF_CHECK, self_check_passed=exec_outcome.get("passed"))

        # ── Step 7: build node package ───────────────────────────────────
        package = self._step7_package(spec, plan, result, exec_outcome)
        emit(STEP_PACKAGE, artifact_count=len(package.get("artifacts", [])))

        # ── Step 8: independent Acceptance (D-082) ───────────────────────
        self._update_run(run_row, "acceptance_checking")
        acc = self.acceptance.accept(
            package, spec.task_plan, spec.acceptance_criteria,
            project_id=spec.project_id, run_id=spec.run_id, stage=spec.stage, mode=spec.mode)
        emit(STEP_ACCEPTANCE, acceptance_result=acc.result)

        # ── Step 9: route back to TaskGraph edge strategy ────────────────
        node_status, route = _ROUTE_MAP.get(acc.result, ("failed", "failure"))
        self._persist_refs(run_row, package, acc)
        self._finalize_run(run_row, node_status,
                           acceptance_result_ref=(acc.agent_id or None))
        emit(STEP_ROUTE, node_status=node_status, next_route=route)
        return NodeLoopResult(
            spec.node_id, node_status, STEP_ROUTE, route,
            acceptance=acc.to_dict(), node_package=package,
            task_node_run_id=tnr_id, reason=acc.reason, events=events)

    # ── Step 1 validation ────────────────────────────────────────────────
    def _step1_validate(self, spec: NodeSpec):
        """Return (node_status, route, reason) if the node cannot proceed, else None."""
        if not spec.node_id or not spec.task_plan:
            return ("blocked", "blocked", "缺少关键输入（node_id / task_plan_ref），登记缺失项")
        if not spec.acceptance_criteria and not spec.task_plan.get("acceptance_criteria"):
            return ("blocked", "blocked", "缺少 acceptance_criteria，无法验收，登记缺失项")
        if spec.permission_boundary is None:
            # 权限不清 → policy_gate (§Step1)
            return ("waiting_gate", "gate", "权限边界不明确（policy_gate），需用户授权")
        if spec.task_plan.get("consistency_conflict"):
            return ("waiting_gate", "gate", "任务与 Stage Plan 冲突（consistency_conflict）")
        return None

    # ── Step 2 context ───────────────────────────────────────────────────
    def _step2_context(self, spec: NodeSpec, context_fn) -> dict:
        if context_fn is not None:
            try:
                return context_fn() or {}
            except Exception as e:
                self._trace("context_fn failed", detail=str(e))
                return {"uncertain_input": [f"context_fn error: {e}"]}
        try:
            from app.services.context_assembler import assemble_context
            return assemble_context(
                spec.project_id, spec.stage,
                node_state={"node_task": spec.task_plan.get("objective", ""),
                            "node_id": spec.node_id},
                task_type=spec.task_plan.get("task_type", "default"),
            )
        except Exception as e:
            # context assembly is advisory; record uncertainty, do not hallucinate (§Step2-5)
            self._trace("context assembly failed", detail=str(e))
            return {"uncertain_input": [f"context assembly error: {e}"]}

    def _default_node_plan(self, spec: NodeSpec) -> dict:
        return {
            "objective": spec.task_plan.get("objective", ""),
            "actions": spec.task_plan.get("planned_actions", []),
            "risk_level": spec.risk_level,
            "permission_boundary": spec.permission_boundary,
            "out_of_scope": False,
            "substantive_change": bool(spec.task_plan.get("substantive_change")),
        }

    # ── Step 3 plan gate (scope + mode) ──────────────────────────────────
    def _step3_plan_gate(self, spec: NodeSpec, plan: dict):
        # out-of-scope → Plan Delta + Gate (§Step3-2)
        if plan.get("out_of_scope"):
            return ("waiting_gate", "gate",
                    "节点计划超出 Task Plan scope，需生成 Plan Delta 并 Gate")
        # Manual mode: substantive modification → Gate before execute (§4.1-1)
        if spec.mode == "manual" and plan.get("substantive_change"):
            return ("waiting_gate", "gate",
                    "Manual 模式下涉及实质修改，执行前先进入 Gate")
        # Auto mode: high-risk (L4/L5) action → Gate (§4.3-4)
        if spec.mode == "auto" and (plan.get("risk_level") in _HIGH_RISK):
            return ("waiting_gate", "gate",
                    "Auto 模式不放行高风险（L4-L5）动作，进入 Gate")
        return None

    # ── Step 4 split ─────────────────────────────────────────────────────
    def _step4_split(self, spec: NodeSpec, plan: dict) -> str:
        strategy = spec.split_strategy if spec.split_strategy in SPLIT_STRATEGIES else "inline"
        if strategy == "nested_loop" and not plan.get("max_iterations"):
            # nested_loop must declare an exit condition (§Step4-3) — fall back safely
            self._trace("nested_loop missing exit condition → inline", spec=spec)
            return "inline"
        return strategy

    # ── Step 5+6 execute + self-check (ReviewPass kernel) ────────────────
    async def _step5_execute(self, spec: NodeSpec, execute_fn, self_check_fn, context) -> dict:
        def review_fn(result: dict) -> ReviewResult:
            issues = []
            if self_check_fn:
                issues = [{"self_check": i} for i in (self_check_fn(result) or [])]
            return ReviewResult(passed=not issues, issues=issues, reviewer="node_self_check")

        rp = ReviewPass(max_rounds=self.max_rounds, tracer=self.tracer, auditor=self.auditor)

        # holder captures the last executed result — ReviewPass drops final_result
        # to None on escalation, but a self-check failure still produced output that
        # must go to the independent Acceptance (Step 8 always runs unless execute
        # itself never produced anything).
        holder: dict = {}

        async def _execute():
            r = execute_fn()
            res = await r if inspect.isawaitable(r) else r
            holder["result"] = res
            return res

        try:
            outcome = await rp.run(
                execute_fn=_execute, review_fn=review_fn,
                project_id=spec.project_id, stage=spec.stage, context=context)
        except Exception as e:
            return {"hard_fail": True, "reason": f"execution raised: {e}",
                    "result": {}, "passed": False, "rounds": []}

        passed = bool(outcome.get("passed"))
        result = outcome.get("final_result") if passed else holder.get("result")
        if result is None:
            # execute_fn never produced a result (raised every round) → hard failure
            return {"hard_fail": True,
                    "reason": outcome.get("escalation_reason", "execution failed"),
                    "result": {}, "passed": False, "rounds": outcome.get("rounds", [])}
        # self-check may have failed (passed=False) but output exists → proceed to Acceptance
        return {"hard_fail": False, "result": result, "passed": passed,
                "rounds": outcome.get("rounds", [])}

    # ── Step 7 node package ──────────────────────────────────────────────
    def _step7_package(self, spec: NodeSpec, plan: dict, result: dict, exec_outcome: dict) -> dict:
        result = result if isinstance(result, dict) else {}
        return {
            "node_id": spec.node_id,
            "task_plan_ref": spec.task_plan.get("task_plan_id"),
            "stage": spec.stage,
            "risk_level": spec.risk_level,
            "execution_summary": result.get("summary", ""),
            "artifacts": result.get("artifacts") or result.get("artifact_refs") or [],
            "evidence": (result.get("evidence") or result.get("evidence_refs")
                         or result.get("evidence_candidates") or []),
            "trace_refs": result.get("trace_refs") or [f"trace-node-{spec.node_id}"],
            "audit_refs": result.get("audit_refs") or [],
            "criteria_met": result.get("criteria_met"),
            "scope_violation": bool(result.get("scope_violation") or plan.get("out_of_scope")),
            "boundary_violation": bool(result.get("boundary_violation")),
            "policy_forbidden": bool(result.get("policy_forbidden")),
            "self_check_passed": bool(exec_outcome.get("passed")),
            "self_check_rounds": exec_outcome.get("rounds", []),
            "node_status": result.get("node_status"),
        }

    # ── TaskNodeRun persistence (T1) ─────────────────────────────────────
    def _create_run(self, spec: NodeSpec):
        if self.db is None:
            return None
        try:
            from app.models.task_node_run import TaskNodeRun
            row = TaskNodeRun(
                node_id=spec.node_id,
                task_graph_run_id=spec.task_graph_run_id or "",
                project_id=spec.project_id,
                run_id=spec.run_id,
                stage=spec.stage,
                node_status="pending",
            )
            self.db.add(row)
            self.db.commit()
            return row
        except Exception as e:
            self._trace("task_node_run create failed", detail=str(e))
            return None

    def _update_run(self, row, node_status: str, *, failure_reason: str | None = None):
        if row is None or self.db is None:
            return
        assert node_status in NODE_STATUSES, f"invalid node_status: {node_status}"
        try:
            row.node_status = node_status
            row.updated_at = _utcnow()
            if failure_reason:
                row.failure_reason = failure_reason
            self.db.commit()
        except Exception as e:
            self._trace("task_node_run update failed", detail=str(e))

    def _persist_refs(self, row, package: dict, acc):
        if row is None or self.db is None:
            return
        try:
            row.artifact_refs = package.get("artifacts") or []
            row.evidence_refs = package.get("evidence") or []
            row.trace_refs = package.get("trace_refs") or []
            row.audit_refs = package.get("audit_refs") or []
            self.db.commit()
        except Exception as e:
            self._trace("task_node_run refs persist failed", detail=str(e))

    def _finalize_run(self, row, node_status: str, *, acceptance_result_ref: str | None = None):
        if row is None or self.db is None:
            return
        assert node_status in NODE_STATUSES, f"invalid node_status: {node_status}"
        try:
            row.node_status = node_status
            row.updated_at = _utcnow()
            if acceptance_result_ref:
                row.acceptance_result_ref = acceptance_result_ref
            if node_status in ("completed", "failed", "skipped"):
                row.completed_at = _utcnow()
            self.db.commit()
        except Exception as e:
            self._trace("task_node_run finalize failed", detail=str(e))

    def _trace(self, summary: str, *, spec: NodeSpec | None = None, step: str | None = None, **extra):
        if self.tracer is None:
            return
        try:
            self.tracer.write(
                "state_change", action="node_loop", summary=summary,
                project_id=(spec.project_id if spec else None),
                run_id=(spec.run_id if spec else None),
                stage=(spec.stage if spec else None), **extra)
        except Exception:
            pass  # tracing advisory
