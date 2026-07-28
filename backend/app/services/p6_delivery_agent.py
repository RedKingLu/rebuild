"""P6 delivery LLM advisory layer (R17.5-P6-R1, GAP-P6-1, D-108).

对称 P5 的 `p5_verification_agent.py`（方向A "LLM 策略/解读层"）。P6 交付内核
（确定性 + 双向门禁 + 脱敏硬门禁 + final gate + 反伪造）保持权威不变：交付清单/hash
（SHA-256）/脱敏扫描/AETA 索引/门禁认定这类**事实与门禁**由 `P6DeliveryService` +
`RealP6Handler` 的确定性内核真实产生，`can_be_completed`（P5 读回）+ 用户 final Gate
是唯一的可交付认定门禁。本层是 **advisory（辅助分析，非事实、非门禁）**：

  - 组织面向用户的【交付叙述】（交付了什么、覆盖范围、限制；PoC vs Production 作叙述）
  - 组织【部署/运维/回退提示框架】（来源须为真实 P2/P3/P4 产物；R1 先给占位框架，R5 充实）
  - 给【验收结论建议】（accepted / accepted_with_warning / rework_required / blocked，advisory）
  - 产【迁移经验候选】（Skill/Case/ADR 候选，反过拟合）

红线（本层严格遵守，不越权）：
  - LLM 绝不产出"通过/accepted/可交付"确定性结论替代确定性门禁；不改任何门禁；
    不翻转 P5→P6 双向门禁 / 脱敏门禁 / final gate。所有产出显式标 analysis_only=True。
  - 绝不把 evidence_gap / risk_manifest 项渲染成"通过/可交付"；PoC 不得建议为 Production accepted。
  - 无可用模型 Key → 诚实 status="skipped"（advisory 缺席，evidence_gap），**非阻断**：
    确定性交付内核照常产事实、P6 照常按确定性门禁 + 用户 Gate 认定 completed/blocked。
  - 模型调用经 ModelGateway（D-098，不硬编码模型名/endpoint）；走 run_stage_tool_loop
    与 P2/P3/P4/P5 同构（可按需 read_artifact/fs_read/code_grep 探读真实产物做证据化叙述）。

`gateway` 可注入，测试可用 mock-LLM 走 fallback 单次 call 路径验证本层而不触真实模型。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("rebuild.p6_delivery_agent")

# 编排 + 锚点字段（skill-first，D-108）：交付方法论/环节/许可纪律/反伪造红线随 P6 stage skill
# (P-migration-delivery) 正文走，此处只保留"怎么编排 + 输出什么锚点键 + 不可越权红线"。
_SYSTEM_PROMPT = (
    "你是 rebuild 平台的 P6 交付 Agent 的【交付叙述/验收建议辅助层】。遵循已加载的 P-migration-delivery "
    "stage skill 完成交付叙述的组织与验收结论建议（交付了什么、怎么部署运维回退、验收建议哪一档以 skill 为准）。"
    "输入是 P5 真实验证结论（p5_validation_report：十槽位状态 + can_be_completed 权威门禁）+ 已由确定性内核"
    "产出的【真实交付事实】（交付清单/hash SHA-256/风险清单/AETA 索引/脱敏结论/final gate）。"
    "你的职责【只有四件】：① 组织面向用户的【交付叙述】（交付了什么、覆盖范围、限制；PoC vs Production 作叙述说明）；"
    "② 组织【部署/运维/回退提示框架】（deploy/operation/rollback，来源须为真实 P2/P3/P4 产物，缺来源标 evidence_gap，不臆造目标环境）；"
    "③ 给【验收结论建议】（accepted / accepted_with_warning / rework_required / blocked + 依据 + 注意事项，"
    "advisory——PoC + evidence_gap + 许可不清 → 诚实建议 accepted_with_warning，绝不建议伪 Production accepted）；"
    "④ 产【迁移经验候选】（Skill/Case/ADR 候选，来源真实产物，反过拟合）。\n"
    "【绝对红线，违反即失效】：你【不产出「通过/accepted/可交付」确定性结论】、不替代确定性门禁、"
    "不翻转 P5→P6 双向门禁 / 脱敏门禁 / final gate——是否可交付由确定性门禁 + 用户 final Gate 认定，你只给判断参考。"
    "No Fake Delivery。证据不足 / 未通过项 / 许可不清只解读为待补充 / 建议警告或返工，绝不粉饰为可交付。"
    "acceptance_advice 是【建议】非「通过」结论；绝不把 evidence_gap / risk_manifest 项渲染成通过。\n"
    "严格输出单个 JSON 对象，锚点键（供机器解析）：\n"
    "  delivery_narrative(对象：delivered[数组，交付了什么]、scope_note[字符串，覆盖范围/PoC-Production 说明]、"
    "limitations[数组，限制/未覆盖])；\n"
    "  operation_rollback_notes(对象：deploy_notes[数组]、operation_notes[数组]、rollback_notes[数组]，"
    "每项须能追溯真实 P2/P3/P4 产物；无来源给空数组 + 记 evidence_gap)；\n"
    "  acceptance_advice(对象：suggested_result[accepted|accepted_with_warning|rework_required|blocked]、"
    "rationale[字符串]、caveats[数组])；\n"
    "  experience_notes(数组，每项 {kind[skill|case|adr], title, note})。\n"
    "无可组织内容的键给空数组/空对象。你的输出是【辅助分析，非事实、非门禁】。"
    "输出格式（硬约束）：只输出单个 JSON 对象本身，不要包裹散文说明、前后缀或 markdown 代码围栏。"
)


@dataclass
class P6AdvisoryResult:
    status: str                       # completed / skipped / failed
    reason: str = ""
    analysis_only: bool = True        # 本层输出恒为辅助分析，非事实、非门禁
    delivery_narrative: dict = field(default_factory=dict)
    operation_rollback_notes: dict = field(default_factory=dict)
    acceptance_advice: dict = field(default_factory=dict)
    experience_notes: list = field(default_factory=list)
    model_used: Optional[str] = None
    evidence_gap: str = ""            # advisory 缺席时的诚实登记（非阻断）
    attempted_chain: list = field(default_factory=list)
    model_error_category: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "analysis_only": self.analysis_only,
            "delivery_narrative": self.delivery_narrative,
            "operation_rollback_notes": self.operation_rollback_notes,
            "acceptance_advice": self.acceptance_advice,
            "experience_notes": self.experience_notes,
            "model_used": self.model_used,
            "evidence_gap": self.evidence_gap,
            "attempted_chain": self.attempted_chain,
            "model_error_category": self.model_error_category,
        }


class P6DeliveryAgent:
    def __init__(self, *, gateway=None, tracer=None):
        self._gateway = gateway
        self.tracer = tracer

    def _get_gateway(self):
        if self._gateway is not None:
            return self._gateway
        from app.dependencies import get_services
        return get_services().model_gateway

    async def interpret(
        self,
        project_id: str,
        *,
        run_id: str = "",
        stage: str = "p6",
        strategy_id: str = "system-default",
        deterministic_facts: dict,
        skill_body: str = "",
        system_prompt: str = "",
    ) -> P6AdvisoryResult:
        """产出 P6 交付的叙述/验收建议/经验候选（advisory，非事实、非门禁）。

        deterministic_facts：确定性交付内核已产出的真实事实（交付清单 contents/风险清单/
        hash 摘要/AETA 索引摘要/脱敏结论/P5 验证摘要 can_be_completed/final gate）——本层据此
        组织叙述与建议，绝不改写其结论或翻转任何门禁。任何异常/无模型 → status="skipped"，
        非阻断（确定性交付内核 + 门禁照常）。

        skill_body / system_prompt：由 handler 层（stage_handlers.py，X-4-5 白名单内）经
        canonical 上下文装配器组装后传入——本 agent 是纯消费者，不自行触碰上下文装配器
        （X-4-5 单一事实源：context_assembler 只由白名单执行节点调用）。skill-first 不受影响：
        P6 主 stage skill 正文仍被加载注入。
        """
        try:
            gw = self._get_gateway()
            # 无可用模型 → advisory 缺席，诚实 skipped（非阻断，不降级为规则伪造叙述）。
            readiness = gw.stage_model_readiness(strategy_id=strategy_id, project_id=project_id,
                                                 require_tool_calling=True)
            if not readiness.get("available"):
                return P6AdvisoryResult(
                    status="skipped",
                    reason=("no_model_key: P6 LLM 交付叙述/验收建议层需要 LLM 支持；无可用 Key 时本层缺席"
                            "（非阻断，确定性交付内核与门禁照常）"
                            f"；{readiness.get('reason','')}"),
                    evidence_gap="llm_advisory_unavailable",
                    attempted_chain=readiness.get("attempted_chain", []),
                    model_error_category="model_unavailable")

            parts = [p for p in (system_prompt, (skill_body or None), _SYSTEM_PROMPT) if p]
            system_content = "\n\n---\n\n".join(parts)
            user_content = self._build_user_prompt(deterministic_facts)

            from app.services.stage_agent_loop import run_stage_tool_loop
            loop = await run_stage_tool_loop(
                gw, system_content=system_content, user_content=user_content,
                project_id=project_id, run_id=run_id or "", stage=stage,
                strategy_id=strategy_id, max_tokens=8192, temperature=0.3, tracer=self.tracer)
            if loop["status"] != "completed":
                reason = loop.get("error_message") or loop.get("error_category") or "model_call_failed"
                return P6AdvisoryResult(
                    status="skipped", reason=f"llm_advisory_failed: {reason}",
                    evidence_gap="llm_advisory_unavailable",
                    model_used=loop.get("model_used"),
                    attempted_chain=loop.get("attempted_chain", []),
                    model_error_category=loop.get("error_category", "model_unavailable"))

            parsed = self._parse(loop.get("content", ""))
            return P6AdvisoryResult(
                status="completed", analysis_only=True, model_used=loop.get("model_used"),
                delivery_narrative=parsed.get("delivery_narrative", {}) or {},
                operation_rollback_notes=parsed.get("operation_rollback_notes", {}) or {},
                acceptance_advice=parsed.get("acceptance_advice", {}) or {},
                experience_notes=parsed.get("experience_notes", []) or [])
        except Exception as e:
            # 公理3：发声但不阻断——advisory 层任何异常都不得拖垮确定性 P6 交付与门禁。
            logger.warning("P6 advisory interpret failed (non-blocking): %s", e, exc_info=True)
            return P6AdvisoryResult(status="skipped", reason=f"llm_advisory_exception: {e}",
                                    evidence_gap="llm_advisory_unavailable",
                                    model_error_category="advisory_exception")

    # ── prompts ──────────────────────────────────────────────────────────────
    def _build_user_prompt(self, facts: dict) -> str:
        return (
            f"项目 ID：{facts.get('project_id')}\n"
            f"P5 验证摘要（真实门禁事实，勿改写）：can_be_completed={facts.get('p5_can_be_completed')}；"
            f"{json.dumps(facts.get('p5_summary', {}), ensure_ascii=False)[:2000]}\n"
            f"交付清单（确定性事实：交付了什么/数量/字节）：{json.dumps(facts.get('delivery_manifest', {}), ensure_ascii=False)[:2500]}\n"
            f"风险清单（未通过项/evidence_gap，勿渲染成通过）：{json.dumps(facts.get('risk_manifest', {}), ensure_ascii=False)[:2000]}\n"
            f"hash 摘要（SHA-256 文件数）：{json.dumps(facts.get('hash_summary', {}), ensure_ascii=False)[:800]}\n"
            f"AETA 索引摘要（artifact/evidence/trace/audit）：{json.dumps(facts.get('index_summary', {}), ensure_ascii=False)[:1200]}\n"
            f"脱敏结论：desensitization_ok={facts.get('desensitization_ok')}\n"
            f"P4 执行摘要（目标栈/PoC 分级/变更）：{json.dumps(facts.get('p4_summary', {}), ensure_ascii=False)[:2000]}\n"
            f"上游产物可用性（P2/P3/P4 真实产物，供部署/运维/回退叙述与经验沉淀接地；缺→evidence_gap，勿臆造目标环境）："
            f"{json.dumps(facts.get('upstream_artifacts', {}), ensure_ascii=False)[:1500]}\n"
            f"final gate：{facts.get('final_gate_id')}\n"
            "请据以上【真实交付事实与门禁认定】产出：① delivery_narrative（面向用户的可读交付叙述：交付了什么/覆盖范围/限制）；"
            "② operation_rollback_notes（部署/运维/回退提示框架，须追溯真实 P2/P3/P4 产物，无来源标 evidence_gap）；"
            "③ acceptance_advice（验收结论建议 + 依据 + 注意事项，PoC/evidence_gap/许可不清→建议 accepted_with_warning）；"
            "④ experience_notes（迁移经验候选 Skill/Case/ADR）。"
            "严禁产出「通过/accepted/可交付」确定性结论、严禁翻转任何门禁、严禁把风险/缺口渲染成通过。"
        )

    def _parse(self, content: str) -> dict:
        from app.services.stage_agent_loop import extract_json_object
        data = extract_json_object(content)
        if not isinstance(data, dict):
            return {"delivery_narrative": {"raw": (content or "").strip()[:1500],
                                           "parse_error": True},
                    "operation_rollback_notes": {}, "acceptance_advice": {},
                    "experience_notes": []}
        return data
