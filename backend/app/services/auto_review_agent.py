"""Auto Review / Safety Agent (R17.3-6 WP-4 / GAP-AGT-1,承 D-025 / D-028).

D-025/D-028: in Auto Mode the Stage Plan / Task Plan are reviewed by an Agent (not by the
user). R17.3-5 GAP-AGT-1 found this "Agent review" was never wired — Auto authorization was
a deterministic `mode_policy.authorize_action` risk-rank, with no LLM reasoning about the
plan / risk / out-of-bounds.

This module is the real Auto Review Agent. In Auto mode it reviews the stage plan with an
LLM (risk / out-of-bounds / low-confidence judgement) and decides whether to auto-proceed or
escalate to a user Gate. D-031 is enforced in code: the Policy floor is authoritative — any
L4+ planned action forces a user Gate REGARDLESS of the LLM verdict, so the LLM can never
"approve" past the Policy floor. When no LLM Key is available the agent honestly degrades to
the deterministic Policy floor and records an evidence_gap (D-097: never fake an LLM verdict).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.services.mode_policy import risk_for_action, _rank, HIGH_RISK_FLOOR

logger = logging.getLogger("rebuild.auto_review_agent")


@dataclass
class AutoReviewResult:
    stage: str
    needs_user_gate: bool = False
    verdict: str = "proceed"                 # "proceed" | "escalate"
    reason: str = ""
    risk_flags: list = field(default_factory=list)
    policy_floor_escalated: bool = False     # True iff Policy floor (L4+) forced the gate
    llm_participated: bool = False
    evidence_gap: Optional[str] = None
    audit_ref: Optional[str] = None
    reviewer: str = "auto_review_agent"

    def to_dict(self) -> dict:
        return {
            "stage": self.stage, "needs_user_gate": self.needs_user_gate,
            "verdict": self.verdict, "reason": self.reason, "risk_flags": self.risk_flags,
            "policy_floor_escalated": self.policy_floor_escalated,
            "llm_participated": self.llm_participated, "evidence_gap": self.evidence_gap,
            "audit_ref": self.audit_ref, "reviewer": self.reviewer,
        }


class AutoReviewAgent:
    def __init__(self, stage: str, project_id: str, run_id: str,
                 tracer=None, auditor=None, gateway=None):
        self.stage = stage
        self.project_id = project_id
        self.run_id = run_id
        self.tracer = tracer
        self.auditor = auditor
        self._gateway = gateway

    def review_plan(self, planned_actions: list, goal: str = "",
                    action_types: Optional[list] = None) -> AutoReviewResult:
        """Review the stage plan in Auto mode.

        planned_actions: human-readable plan steps.
        action_types: optional list of action/tool types (for Policy-floor risk classification).
        """
        res = AutoReviewResult(stage=self.stage)

        # 1. Policy floor (D-031, authoritative): any L4+ action → force user Gate, no matter
        #    what the LLM says. This is where the LLM is prevented from bypassing Policy.
        floor_flags = []
        for at in (action_types or []):
            r = risk_for_action(at)
            if _rank(r) >= HIGH_RISK_FLOOR:
                floor_flags.append(f"{at}={r}")
        if floor_flags:
            res.policy_floor_escalated = True
            res.needs_user_gate = True
            res.verdict = "escalate"
            res.risk_flags.extend(floor_flags)

        # 2. LLM review (risk / out-of-bounds / low-confidence). Advisory: can only ADD an
        #    escalation, never remove the Policy floor's.
        llm = self._llm_review(planned_actions, goal)
        if llm.get("status") == "completed":
            res.llm_participated = True
            verdict = (llm.get("verdict") or "proceed").lower()
            if verdict == "escalate":
                res.needs_user_gate = True
                res.verdict = "escalate"
                if llm.get("reason"):
                    res.risk_flags.append(f"llm:{llm['reason'][:120]}")
        else:
            res.evidence_gap = llm.get("detail", "LLM 审核未执行")

        # 3. Compose reason.
        if res.policy_floor_escalated:
            res.reason = (f"Policy 底线：计划含高风险动作（{', '.join(floor_flags)}），"
                          f"强制用户 Gate（D-031，LLM 不得绕过）。")
        elif res.needs_user_gate:
            res.reason = f"Auto Review Agent 判定需升级用户确认：{'; '.join(res.risk_flags) or '存在风险/越界/低置信'}"
        elif res.llm_participated:
            res.reason = "Auto Review Agent（LLM）审核通过：无高风险/越界/低置信，Auto 模式自动放行。"
        else:
            res.reason = (f"LLM 审核不可用（{res.evidence_gap}）；按 Policy 底线处理："
                          f"无 L4+ 动作 → Auto 放行（诚实降级，非伪造 LLM 结论）。")

        self._audit(res)
        return res

    def _llm_review(self, planned_actions: list, goal: str) -> dict:
        gw = self._gateway
        if gw is None:
            try:
                from app.dependencies import get_services
                gw = get_services().model_gateway
            except Exception:
                gw = None
        if gw is None:
            return {"status": "evidence_gap", "detail": "模型网关不可用（需有效 Key 端到端验证 LLM 审核）"}
        plan_txt = "\n".join(f"- {a}" for a in (planned_actions or [])[:20]) or "(无显式步骤)"
        prompt = (
            "你是 Auto 模式下的审核/安全 Agent。请审核以下阶段工作计划，判断是否存在：高风险动作、"
            "越界（超出迁移/重构范围）、低置信度或需人工确认的情形。仅输出 JSON："
            "{\"verdict\":\"proceed|escalate\",\"reason\":\"...\"}。\n"
            f"阶段目标：{goal}\n计划步骤：\n{plan_txt}"
        )
        try:
            from app.services.work_agent import _run_coro
            resp = _run_coro(gw.call(messages=[{"role": "user", "content": prompt}],
                                     source="api", max_tokens=512, temperature=0.0))
        except Exception:
            return {"status": "evidence_gap", "detail": "LLM 审核调用异常（需有效 Key 复验）"}
        if not resp or resp.get("status") != "completed":
            cat = (resp or {}).get("error_category", "unknown")
            return {"status": "evidence_gap", "detail": f"LLM 审核未完成（{cat}）"}
        content = (resp.get("content") or "").strip()
        verdict, reason, parsed = self._parse_verdict(content)
        if not parsed:
            # 无法从 LLM 输出解析出结构化结论 = 未获得可用审核结论（非「LLM 判 escalate」）。
            # 诚实标 evidence_gap（D-097 不伪造结论），交由 Policy 底线处理，不据此升级。
            return {"status": "evidence_gap",
                    "detail": "LLM 审核输出无法解析为结构化结论（需有效 Key 端到端复验）"}
        return {"status": "completed", "verdict": verdict, "reason": reason, "raw": content[:200]}

    @staticmethod
    def _parse_verdict(content: str) -> tuple[str, str, bool]:
        """Parse the LLM JSON verdict. Returns (verdict, reason, parsed_ok).

        parsed_ok=False when no structured verdict could be extracted — the caller treats
        that as an evidence_gap (LLM review inconclusive), NOT as a fabricated escalate.
        """
        try:
            start = content.index("{")
            end = content.rindex("}") + 1
            obj = json.loads(content[start:end])
            v = (obj.get("verdict") or "").lower()
            if v not in ("proceed", "escalate"):
                return "proceed", "", False
            return v, (obj.get("reason") or "")[:200], True
        except Exception:
            return "proceed", "", False

    def _audit(self, res: AutoReviewResult) -> None:
        if self.auditor is not None:
            try:
                audit = self.auditor.write(
                    audit_type="auto_review",
                    risk_level="L4" if res.policy_floor_escalated else "L2",
                    action=f"auto_review_plan:{self.stage}",
                    decision="escalate" if res.needs_user_gate else "auto_approved",
                    reason=res.reason[:300],
                    project_id=self.project_id, run_id=self.run_id, stage=self.stage,
                    llm_participated=res.llm_participated,
                    policy_floor_escalated=res.policy_floor_escalated,
                )
                if isinstance(audit, dict):
                    res.audit_ref = audit.get("audit_id")
            except Exception:
                logger.warning("auto_review_agent: 审计写入失败 stage=%s", self.stage, exc_info=True)
        if self.tracer is not None:
            try:
                self.tracer.write("agent_review", action=f"auto_review:{self.stage}",
                                  summary=(f"Auto Review {self.stage}: "
                                           f"{'escalate' if res.needs_user_gate else 'proceed'} "
                                           f"(llm={res.llm_participated}, floor={res.policy_floor_escalated})"),
                                  project_id=self.project_id, run_id=self.run_id, stage=self.stage)
            except Exception:
                logger.warning("auto_review_agent: trace 写入失败", exc_info=True)
