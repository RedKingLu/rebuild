"""StageLoop — generic intra-stage small loop for every P-stage (R9-5-1, T7 / D-091).

Wraps the existing ReviewPass (execute→review→retry≤N→escalate) into the full
small-loop shape required by D-091:

    read context → stage plan → execute → self-check → Review Pass
        → rework (≤N rounds) → exceed/high-risk ⇒ escalate to Gate
        → pass ⇒ proceed to promotion Gate

It also produces the three D-092 reports (start_plan / construction / acceptance)
around the loop. P0/P1 nodes use it now; P2-P6 reuse it from R10 (00-总规划 §1.2).
This is the NodeLoop skeleton abstraction — the full 9-step NodeLoop body is R10.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from app.services.review_pass import ReviewPass, ReviewResult
from app.graph.stage_reports import StageReports


@dataclass
class StageLoopResult:
    stage: str
    passed: bool
    escalated_to_gate: bool
    result: Optional[dict]
    rounds: List[dict]
    report_refs: List[str] = field(default_factory=list)
    escalation_reason: Optional[str] = None


class StageLoop:
    """Run one stage's small loop and emit the three review reports.

    Args injected by the node:
      goal / acceptance_criteria / planned_actions  → start_plan report
      read_context_fn (opt) → returns a context dict (recipe assembly hooks here, R9-5-3)
      execute_fn  → async/sync callable doing the real stage work, returns result dict
      self_check_fn (opt) → sync callable(result)->list[str] of self-check issues
      review_fn   → sync callable(result)->ReviewResult (independent review skill)
    """

    def __init__(self, project_id: str, stage: str, *, tracer=None, auditor=None,
                 max_rounds: int = 2):
        self.project_id = project_id
        self.stage = stage
        self.tracer = tracer
        self.auditor = auditor
        self.max_rounds = max_rounds

    async def run(
        self,
        *,
        goal: str,
        acceptance_criteria: List[str],
        planned_actions: List[str],
        execute_fn: Callable[[], "dict | Awaitable[dict]"],
        review_fn: Callable[[dict], ReviewResult],
        read_context_fn: Optional[Callable[[], dict]] = None,
        self_check_fn: Optional[Callable[[dict], List[str]]] = None,
    ) -> StageLoopResult:
        reports = StageReports(self.project_id, self.stage)

        # ① 起始计划报告
        reports.start_plan(goal=goal, acceptance_criteria=acceptance_criteria,
                           planned_actions=planned_actions)
        if self.tracer:
            self.tracer.write("stage_loop", action="start_plan",
                              summary=f"{self.stage} start plan: {goal}",
                              project_id=self.project_id)

        # read context (C0-C6 assembly接入点 → R9-5-3)
        context = read_context_fn() if read_context_fn else {}

        # wrap execute with optional self-check appended to the review
        def _review_with_self_check(result: dict) -> ReviewResult:
            review = review_fn(result)
            if self_check_fn:
                sc_issues = self_check_fn(result) or []
                if sc_issues:
                    merged = list(review.issues) + [{"self_check": i} for i in sc_issues]
                    return ReviewResult(passed=False, issues=merged,
                                        recommendations=review.recommendations,
                                        reviewer=review.reviewer)
            return review

        rp = ReviewPass(max_rounds=self.max_rounds, tracer=self.tracer, auditor=self.auditor)

        # Wrap execute_fn in an async function so ReviewPass (_is_async) always awaits
        # it — execute_fn may be sync or return an awaitable (handler.execute).
        async def _execute():
            r = execute_fn()
            return await r if inspect.isawaitable(r) else r

        outcome = await rp.run(
            execute_fn=_execute,
            review_fn=_review_with_self_check,
            project_id=self.project_id,
            stage=self.stage,
            context=context,
        )

        passed = bool(outcome.get("passed"))
        escalated = bool(outcome.get("escalated_to_gate"))
        rounds = outcome.get("rounds", [])
        last_issues: List[str] = []
        last_recs: List[str] = []
        for r in reversed(rounds):
            if r.get("issues") or r.get("recommendations"):
                last_issues = [str(i) for i in r.get("issues", [])]
                last_recs = [str(x) for x in r.get("recommendations", [])]
                break

        # ② 中间施工报告
        produced = []
        result = outcome.get("final_result") or {}
        if isinstance(result, dict):
            produced = result.get("artifacts") or result.get("produced_artifacts") or []
        reports.construction(rounds=rounds, actions=planned_actions,
                             produced_artifacts=[str(a) for a in produced])

        # ③ 验收报告
        reports.acceptance(passed=passed, issues=last_issues, recommendations=last_recs,
                           reviewer="review_pass")

        if self.tracer:
            self.tracer.write("stage_loop", action="acceptance",
                              summary=f"{self.stage} loop {'PASSED' if passed else ('ESCALATED' if escalated else 'FAILED')}",
                              project_id=self.project_id)

        return StageLoopResult(
            stage=self.stage,
            passed=passed,
            escalated_to_gate=escalated,
            result=outcome.get("final_result"),
            rounds=rounds,
            report_refs=reports.all_refs(),
            escalation_reason=outcome.get("escalation_reason"),
        )
