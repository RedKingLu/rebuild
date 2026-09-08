"""Review Pass Framework (R9-3D).

Provides a reusable review loop for P-stage execution:
  execute → review → passed? → return
           → failed? → retry (max N rounds)
           → still failed? → escalate to human Gate

Review Pass is a SKILL loaded by Node Worker Agent — NOT a separate Agent.
Independent review/acceptance uses separate Agents per D-082.
"""

from __future__ import annotations

from typing import Optional, Callable

# R21 dsh 范式吸收「目标状态与推进权分离」②：记录"这一轮为什么能继续"。ReviewPass 是
# NodeLoop（及 StageLoop）实际驱动的"执行循环"内核（node_loop.py Step5+6 直接
# new 一个 ReviewPass(max_rounds=self.max_rounds, ...)），真实存在的推进路径（读代码
# 确认，未臆造）只有两种：
#   - within_round_budget：round_num 仍 <= max_rounds，本轮正常进入（含首轮）
#   - round_budget_exhausted：全部轮次跑完仍未通过 review → 强制 escalate 到 Gate
# 取值必须与 stage_agent_loop.py 顶部的同名字符串保持字面一致；未跨模块 import 只是
# 为了不为 2 个字符串常量新增模块耦合（YAGNI），不是取值上的分歧。
_ADVANCE_WITHIN_ROUND_BUDGET = "within_round_budget"
_ADVANCE_ROUND_BUDGET_EXHAUSTED = "round_budget_exhausted"


class ReviewResult:
    """Standard review output from any review pass."""

    def __init__(self, passed: bool, issues: list[dict] | None = None,
                 recommendations: list[str] | None = None,
                 reviewer: str = "review_skill"):
        self.passed = passed
        self.issues = issues or []
        self.recommendations = recommendations or []
        self.reviewer = reviewer

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "issues": self.issues,
            "recommendations": self.recommendations,
            "reviewer": self.reviewer,
        }


class ReviewPass:
    """Executes a review pass with retry logic.

    Usage:
        rp = ReviewPass(max_rounds=2, tracer=trace_writer, auditor=audit_writer)
        result = await rp.run(
            execute_fn=do_profiling,
            review_fn=check_profiling_results,
            project_id=project_id,
            stage="p1",
        )
    """

    def __init__(self, max_rounds: int = 2, tracer=None, auditor=None, run_id: str = ""):
        self.max_rounds = max_rounds
        self.tracer = tracer
        self.auditor = auditor
        # NEW-04: carry run_id so every review_pass Trace / review_escalation Audit
        # written below is scoped to the driving run (enables /trace?run_id= filtering).
        self.run_id = run_id
        self.rounds: list[dict] = []

    async def run(
        self,
        execute_fn: Callable,
        review_fn: Callable,
        project_id: str,
        stage: str = "p0",
        context: dict | None = None,
    ) -> dict:
        """Run execute→review→retry loop.

        Args:
            execute_fn: async callable that performs the action and returns result dict
            review_fn: callable that takes result dict and returns ReviewResult
            project_id: project UUID
            stage: current stage
            context: optional context package

        Returns:
            dict with final_result, passed, rounds, escalated_to_gate
        """
        for round_num in range(1, self.max_rounds + 1):
            # Execute
            if self.tracer:
                self.tracer.write("review_pass", action="round_start",
                    summary=f"Review pass round {round_num}/{self.max_rounds} for {stage}",
                    project_id=project_id, run_id=self.run_id or None, stage=stage,
                    advancement_basis=_ADVANCE_WITHIN_ROUND_BUDGET)

            try:
                result = await execute_fn() if hasattr(execute_fn, '__call__') and _is_async(execute_fn) else execute_fn()
            except Exception as e:
                self.rounds.append({"round": round_num, "status": "execute_error", "error": str(e)})
                if round_num >= self.max_rounds:
                    return self._escalate(project_id, stage, f"Execute error after {round_num} rounds: {e}")
                continue

            # Review
            review: ReviewResult = review_fn(result)
            self.rounds.append({
                "round": round_num, "status": "passed" if review.passed else "retry",
                "issues": review.issues, "recommendations": review.recommendations,
            })

            if self.tracer:
                self.tracer.write("review_pass", action="round_end",
                    summary=f"Round {round_num}: {'PASSED' if review.passed else 'RETRY'} ({len(review.issues)} issues)",
                    project_id=project_id, run_id=self.run_id or None, stage=stage)

            if review.passed:
                return {"final_result": result, "passed": True, "rounds": self.rounds, "escalated_to_gate": False}

            # Retry: fix issues and loop
            if round_num < self.max_rounds and review.issues:
                result = self._apply_fixes(result, review.issues)

        # All rounds exhausted
        return self._escalate(project_id, stage, f"Failed after {self.max_rounds} review rounds")

    def _apply_fixes(self, result: dict, issues: list[dict]) -> dict:
        """Attempt to auto-fix review issues. Marks issues as retry_context."""
        result["_retry_context"] = {"previous_issues": issues}
        return result

    def _escalate(self, project_id: str, stage: str, reason: str) -> dict:
        """Escalate to human Gate after max rounds exhausted."""
        if self.tracer:
            # R21 ②：轮次预算耗尽是这一轮"为什么不能再继续"的真实终止原因，记入 Trace
            # （与既有 round_start/round_end 用同一 tracer.write 机制，不新建通道）。
            self.tracer.write("review_pass", action="round_budget_exhausted",
                summary=f"轮次预算耗尽（{self.max_rounds} 轮）：{reason}",
                project_id=project_id, run_id=self.run_id or None, stage=stage,
                advancement_basis=_ADVANCE_ROUND_BUDGET_EXHAUSTED)
        if self.auditor:
            self.auditor.write(
                audit_type="review_escalation", action="escalate_to_gate",
                decision="escalate", risk_level="L3",
                project_id=project_id, run_id=self.run_id or None, stage=stage,
                reason=f"Review pass escalated after {self.max_rounds} rounds: {reason}",
            )
        return {
            "final_result": None, "passed": False,
            "rounds": self.rounds, "escalated_to_gate": True,
            "escalation_reason": reason,
        }


def _is_async(fn) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)
