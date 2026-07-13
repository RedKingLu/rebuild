"""Acceptance executor (R10 T3).

NodeLoop Step 8 (`文档/03-流程与运行时/03-NodeLoop与执行模式.md` §Step8/§Step9):
an INDEPENDENT Acceptance Agent checks a completed node package against its Task
Plan and acceptance criteria, then returns one of 8 routing results.

Design (surfaced decisions):
  - The 8 checks are STRUCTURAL / deterministic verification (does the required
    Artifact/Evidence exist? is Trace/Audit complete? is there a permission
    boundary violation?). These are facts, not LLM judgments — Q-R10-2 ("real
    LLM, no Key = blocked") governs LLM-driven features like P2 risk analysis,
    not structural verification. Making an LLM decide "does file X exist" would
    be wrong.
  - Independence (D-082): the executor is created as a SEPARATE instance that
    does NOT reuse the node worker's session; it stamps the seed Acceptance
    Agent definition (agent_type=acceptance) for traceability. Per Q-R10-4 only
    the instance is independent — no ModelProfile constraint.

Boundaries (§7): self-check ≠ Acceptance ≠ user Gate ≠ P5 validation; Acceptance
must NOT approve Policy-forbidden actions (→ gate_required/failed); every
conclusion is traceable (each of the 8 checks records passed + reason).

This module freezes the interface T2 (NodeLoop 9-step executor) calls at Step 8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.status import ACCEPTANCE_RESULTS

logger = logging.getLogger("rebuild.acceptance_service")


# The 8 check items (§Step8), kept as stable identifiers for traceability.
CHECK_TASK_PLAN_ALIGNMENT = "符合 Task Plan 的目标和范围"
CHECK_ACCEPTANCE_CRITERIA = "符合 acceptance_criteria"
CHECK_ARTIFACT_PRESENT = "有必要 Artifact"
CHECK_EVIDENCE_PRESENT = "有必要 Evidence"
CHECK_TRACE_COMPLETE = "Trace 完整"
CHECK_AUDIT_COMPLETE = "Audit 完整（高风险动作）"
CHECK_NO_BOUNDARY_VIOLATION = "无越界（permission_boundary）"
CHECK_ROUTING_NEED = "是否需要返工、重试或升级 Gate"

_HIGH_RISK_LEVELS = {"L4", "L5"}


@dataclass
class AcceptanceCheck:
    """One of the 8 acceptance check items with its verdict + reason (§7-5 可追踪)."""

    item: str
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {"item": self.item, "passed": self.passed, "reason": self.reason}


@dataclass
class AcceptanceResult:
    """Acceptance verdict routed per NodeLoop Step 9. `result` ∈ ACCEPTANCE_RESULTS."""

    result: str
    checks: list[AcceptanceCheck] = field(default_factory=list)
    reason: str = ""
    recommendations: list[str] = field(default_factory=list)
    agent_id: Optional[str] = None  # independent Acceptance Agent used (D-082 traceability)

    def to_dict(self) -> dict:
        return {
            "result": self.result,
            "checks": [c.to_dict() for c in self.checks],
            "reason": self.reason,
            "recommendations": self.recommendations,
            "agent_id": self.agent_id,
            "reviewer": "acceptance_agent",
        }


class AcceptanceService:
    """Independent Acceptance executor (D-082). Interface frozen for T2."""

    def __init__(self, db=None, tracer=None, auditor=None):
        self.db = db
        self.tracer = tracer
        self.auditor = auditor

    # ── independence (D-082) ─────────────────────────────────────────────
    def _get_acceptance_agent_id(self) -> Optional[str]:
        """Fetch the seed Acceptance Agent definition — the independent identity
        this executor acts as (separate from the node worker Agent). Best-effort:
        traceability stamp only; a missing seed does not block verification."""
        if self.db is None:
            return None
        try:
            from app.models.agent_definition import AgentDefinition, AgentType
            agent = (
                self.db.query(AgentDefinition)
                .filter(AgentDefinition.agent_type == AgentType.acceptance)
                .first()
            )
            return agent.agent_id if agent else None
        except Exception as e:  # honest: record, do not fake an id (D-097/公理3)
            self._trace("acceptance_agent lookup failed", detail=str(e))
            return None

    # ── main entry (NodeLoop Step 8) ─────────────────────────────────────
    def accept(
        self,
        node_package: dict[str, Any],
        task_plan: Optional[dict[str, Any]] = None,
        acceptance_criteria: Optional[list[str]] = None,
        *,
        project_id: Optional[str] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> AcceptanceResult:
        """Run the 8 checks over a node package and route the verdict (§Step8/§9)."""
        node_package = node_package or {}
        task_plan = task_plan or {}
        acceptance_criteria = acceptance_criteria or task_plan.get("acceptance_criteria") or []
        agent_id = self._get_acceptance_agent_id()

        # short-circuit: upstream already terminal (blocked/skipped) — do not fake completed (§Step9)
        node_status = node_package.get("node_status")
        if node_status == "blocked":
            return self._finalize("blocked", [], "节点上游已阻塞，登记阻塞原因，等待解除条件",
                                  [], agent_id, project_id, run_id, stage)
        if node_status == "skipped":
            return self._finalize("skipped", [], "节点已跳过，登记跳过原因，不得伪装 completed",
                                  [], agent_id, project_id, run_id, stage)

        checks: list[AcceptanceCheck] = []
        risk_level = (task_plan.get("risk_level") or node_package.get("risk_level") or "L0")

        # 1. Task Plan alignment: node must not violate scope; must reference the plan
        scope_violation = bool(node_package.get("scope_violation") or node_package.get("out_of_scope"))
        checks.append(AcceptanceCheck(
            CHECK_TASK_PLAN_ALIGNMENT, not scope_violation,
            "在 Task Plan 目标/范围内" if not scope_violation else "检测到超出 Task Plan 范围"))

        # 2. acceptance_criteria coverage: each criterion marked addressed
        met = node_package.get("criteria_met")
        if isinstance(met, dict) and acceptance_criteria:
            unmet = [c for c in acceptance_criteria if not met.get(c)]
            crit_ok = not unmet
            crit_reason = "全部 acceptance_criteria 已满足" if crit_ok else f"未满足: {unmet}"
        elif acceptance_criteria:
            # no explicit coverage map → cannot confirm satisfaction (honest, D-066)
            crit_ok = False
            crit_reason = "未提供 criteria_met 覆盖映射，无法确认 acceptance_criteria 满足"
        else:
            crit_ok = True
            crit_reason = "无 acceptance_criteria 约束"
        checks.append(AcceptanceCheck(CHECK_ACCEPTANCE_CRITERIA, crit_ok, crit_reason))

        # 3. Artifact present (when the plan expects artifacts)
        artifacts = node_package.get("artifacts") or node_package.get("artifact_refs") or []
        expects_artifact = bool(task_plan.get("expected_artifacts")) or not task_plan
        art_ok = bool(artifacts) or not expects_artifact
        checks.append(AcceptanceCheck(
            CHECK_ARTIFACT_PRESENT, art_ok,
            f"Artifact 数={len(artifacts)}" if art_ok else "缺少必要 Artifact"))

        # 4. Evidence present (when the plan expects evidence) — D-066
        evidence = (node_package.get("evidence") or node_package.get("evidence_refs")
                    or node_package.get("evidence_candidates") or [])
        expects_evidence = bool(task_plan.get("expected_evidence")) or not task_plan
        ev_ok = bool(evidence) or not expects_evidence
        checks.append(AcceptanceCheck(
            CHECK_EVIDENCE_PRESENT, ev_ok,
            f"Evidence 数={len(evidence)}" if ev_ok else "缺少必要 Evidence（D-066 不得标记 completed）"))

        # 5. Trace complete
        traces = node_package.get("trace_refs") or node_package.get("traces") or []
        trace_ok = bool(traces)
        checks.append(AcceptanceCheck(
            CHECK_TRACE_COMPLETE, trace_ok,
            "Trace 存在" if trace_ok else "Trace 缺失或不完整"))

        # 6. Audit complete for high-risk actions
        audits = node_package.get("audit_refs") or node_package.get("audits") or []
        high_risk = risk_level in _HIGH_RISK_LEVELS
        audit_ok = (not high_risk) or bool(audits)
        checks.append(AcceptanceCheck(
            CHECK_AUDIT_COMPLETE, audit_ok,
            ("非高风险，无强制 Audit 要求" if not high_risk
             else ("高风险 Audit 存在" if audit_ok else "高风险动作缺少 Audit"))))

        # 7. No permission boundary violation / no Policy-forbidden action (§7-4)
        boundary_violation = bool(node_package.get("boundary_violation"))
        policy_forbidden = bool(node_package.get("policy_forbidden"))
        no_violation = not (boundary_violation or policy_forbidden)
        checks.append(AcceptanceCheck(
            CHECK_NO_BOUNDARY_VIOLATION, no_violation,
            "未越界" if no_violation else
            ("检测到 Policy 禁止动作" if policy_forbidden else "超出 permission_boundary")))

        # 8. Routing need — derived from checks 1-7
        result, reason, recommendations = self._route(checks, policy_forbidden, boundary_violation)
        checks.append(AcceptanceCheck(
            CHECK_ROUTING_NEED, result in ("accepted", "accepted_with_warning"),
            f"路由结论: {result}"))

        return self._finalize(result, checks, reason, recommendations,
                              agent_id, project_id, run_id, stage)

    # ── verdict routing (NodeLoop Step 9) ────────────────────────────────
    def _route(self, checks: list[AcceptanceCheck], policy_forbidden: bool,
               boundary_violation: bool) -> tuple[str, str, list[str]]:
        by_item = {c.item: c for c in checks}
        failed = [c for c in checks if not c.passed]

        # Policy-forbidden / boundary violation must escalate to Gate — never approved (§7-4)
        if policy_forbidden or boundary_violation:
            return ("gate_required",
                    "检测到越界或 Policy 禁止动作，升级 Gate（Acceptance 不得批准）",
                    ["提交用户 Gate 审批", "缩减动作至 permission_boundary 内"])

        # Missing Evidence/Artifact → cannot mark completed → rework (D-066)
        if not by_item[CHECK_EVIDENCE_PRESENT].passed or not by_item[CHECK_ARTIFACT_PRESENT].passed:
            recs = []
            if not by_item[CHECK_ARTIFACT_PRESENT].passed:
                recs.append("补齐必要 Artifact 后重新执行")
            if not by_item[CHECK_EVIDENCE_PRESENT].passed:
                recs.append("补齐必要 Evidence（D-066）后重新执行")
            return ("rework_required", "产物/证据不完整，需返工", recs)

        # Task Plan / acceptance_criteria not met → rework
        if not by_item[CHECK_TASK_PLAN_ALIGNMENT].passed or not by_item[CHECK_ACCEPTANCE_CRITERIA].passed:
            return ("rework_required", "未满足 Task Plan 目标或 acceptance_criteria，需返工",
                    ["按未满足项重新计划（Step 3）或重新执行（Step 5）"])

        # Structural warnings (missing trace / high-risk audit) → accepted_with_warning
        warnings = [c for c in (by_item[CHECK_TRACE_COMPLETE], by_item[CHECK_AUDIT_COMPLETE])
                    if not c.passed]
        if warnings:
            return ("accepted_with_warning",
                    "核心产物齐备但存在可观测性告警（Trace/Audit）",
                    [f"补齐: {w.item}" for w in warnings])

        if not failed:
            return ("accepted", "全部检查通过，进入 TaskGraph 下一节点", [])

        return ("rework_required", "存在未通过检查项，需返工",
                [f"处理: {c.item}" for c in failed])

    # ── finalize + trace/audit ───────────────────────────────────────────
    def _finalize(self, result: str, checks: list[AcceptanceCheck], reason: str,
                  recommendations: list[str], agent_id: Optional[str],
                  project_id, run_id, stage) -> AcceptanceResult:
        assert result in ACCEPTANCE_RESULTS, f"invalid acceptance result: {result}"
        res = AcceptanceResult(result=result, checks=checks, reason=reason,
                               recommendations=recommendations, agent_id=agent_id)
        self._trace(f"acceptance result={result}", project_id=project_id, run_id=run_id,
                    stage=stage, result=result, agent_id=agent_id)
        # high-risk escalations produce an Audit record (§7 traceable conclusion)
        if result in ("gate_required", "failed") and self.auditor is not None:
            try:
                self.auditor.write("high_risk_action", run_id=run_id, stage=stage,
                                   project_id=project_id, action="acceptance_escalation",
                                   summary=f"Acceptance escalated: {reason}")
            except Exception as e:
                self._trace("acceptance audit write failed", detail=str(e))
        return res

    def _trace(self, summary: str, **extra) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.write("acceptance_event", action="acceptance",
                              summary=summary, **extra)
        except Exception:
            # advisory：trace 仅用于可观测，验收结论不受影响；记录以便定位偶发写失败。
            logger.debug("acceptance trace 写入失败（advisory）", exc_info=True)
