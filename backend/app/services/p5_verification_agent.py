"""P5 verification LLM advisory layer (R17.5-P5-R1, GAP-P5-2, D-108).

方向A 的"LLM 验证策略/失败解读层"。P5 验证内核（确定性 + 反伪造）保持权威不变：
编译/测试/diff/DB/浏览器这类**事实**由 `P5VerificationService` 的确定性验证器真实产生，
`can_mark_completed` 是唯一的 completed 认定门禁。本层是**advisory（辅助分析，非事实）**：

  - 规划本次该验哪些维度（LLM 判断适用性，不硬编码枚举——维度写在 P5 stage skill 正文）
  - 解读失败根因
  - 提回 P4 rework 的可执行修复建议

红线（本层严格遵守，不越权）：
  - LLM 绝不产出"通过/pass"结论替代真实测试；不改槽位状态；不翻转 can_be_completed。
    所有产出显式标 analysis_only=True。
  - 无可用模型 Key → 诚实 status="skipped"（advisory 缺席，evidence_gap），**非阻断**：
    确定性验证照常产事实、P5 照常按确定性门禁认定 completed/blocked。
  - 模型调用经 ModelGateway（D-098，不硬编码模型名/endpoint）；走 run_stage_tool_loop
    与 P2/P3/P4 同构（可按需 read_artifact/fs_read/code_grep 探读真实产物做证据化解读）。

`gateway` 可注入，测试可用 mock-LLM 走 fallback 单次 call 路径验证本层而不触真实模型。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("rebuild.p5_verification_agent")

# D-06（V26.2 总验收真实规模真跑，2026-09-11，批次B）：真实规模下本调用的 completion
# **1 次触顶原硬编码 max_tokens=8192**（存档响应头部为 `{"validation_strategy":
# {"applicable_dimensions"…` 即被砍断）→ advisory（维度适用性/失败解读/修复建议）解析失败。
# 复用 acceptance_baseline_service 已建立的 env 可调范式（不新造机制）。
#
# 默认值依据（实测，非拍脑袋）：
#   · 16384 = 触顶值 8192 的 2 倍，同时落在本批修复（acceptance_baseline_service /
#     profiling_service）已使用的同一档预算上——advisory 层输出 4 个锚点（validation_strategy/
#     failure_interpretation/repair_suggestions/structure_mapping），体量与 acceptance_
#     baseline 的静态基线（3 个数组字段）同级，故直接复用同一档，不再新增一个只比 8192
#     略高的中间数字。
#   · 240s：与 acceptance_baseline_service 对同一 16384 预算的取值一致（依据实测 60-120s
#     单次调用延迟 ×2 余量）；本调用同为单轮 run_stage_tool_loop（advisory 层按已有确定性
#     事实解读，无需像 profiling 那样多轮读源），故沿用该量级，不再另加时长。
# 二者只调请求超时与输出预算，不涉及模型/endpoint 选择（策略仍由 ModelGateway 解析，D-098）。
_P5_VERIFICATION_MAX_TOKENS = int(os.environ.get("P5_VERIFICATION_MAX_TOKENS", "16384"))
_P5_VERIFICATION_TIMEOUT = float(os.environ.get("P5_VERIFICATION_TIMEOUT", "240"))

# 编排 + 锚点字段（skill-first，D-108）：验证方法论/维度/反伪造红线随 P5 stage skill
# (P-migration-verification) 正文走，此处只保留"怎么编排 + 输出什么锚点键 + 不可越权红线"。
_SYSTEM_PROMPT = (
    "你是 rebuild 平台的 P5 验证 Agent 的【策略/解读辅助层】。遵循已加载的 P-migration-verification "
    "stage skill 完成迁移验证的策略规划与失败解读（该验哪些维度、维度适用性、失败根因、修复建议以 skill 为准）。"
    "输入是 P4 迁移产物 + 已由确定性验证器产出的【真实验证事实】（各槽位状态/退出码/issues）。"
    "你的职责【只有四件】：① 规划本次适用的验证维度（按本次迁移的技术栈/形态/环境判断适用/不适用）；"
    "② 解读未通过/evidence_gap 维度的失败根因；③ 提出回 P4 rework 的可执行修复建议；"
    "④ 对【重写式迁移】（非行级 diff：如 WebForms→Razor 整体重写、一个源产出多个目标文件/转换脚本）"
    "产出 source→target 的【结构映射】作 diff 等价证据（说明每个源结构对应哪些产物、映射类型），"
    "供 patches_exist 这类只做存在性核验的确定性槽位补上「语义对应」的判断参考。\n"
    "【绝对红线，违反即失效】：你【不产出「通过/pass」结论】、不替代真实测试、不修改任何槽位状态、"
    "不翻转 can_be_completed——是否通过由确定性门禁认定，你只给判断参考。No Evidence No Completed。"
    "证据不足只解读为待补充/未通过，绝不粉饰为通过。structure_mapping 是【分析证据】非「通过」结论。\n"
    "严格输出单个 JSON 对象，锚点键（供机器解析）：\n"
    "  validation_strategy(对象：applicable_dimensions[数组，本次适用维度名]、not_applicable[数组，含"
    "每项 {dimension, reason}]、rationale[字符串，规划依据])；\n"
    "  failure_interpretation(数组，每项 {slot_id_or_dimension, root_cause, expected, actual})；\n"
    "  repair_suggestions(数组，每项 {target_file_or_area, transformation_point, suggestion, back_to_p4:true})；\n"
    "  structure_mapping(数组，重写式迁移的 source→target 结构对应，每项 "
    "{source, target, mapping_type[如 rewrite/split/merge/rename], note}；非重写式或无从判断时给空数组)。\n"
    "无失败项时 failure_interpretation/repair_suggestions 给空数组。你的输出是【辅助分析，非事实】。"
    "输出格式（硬约束）：只输出单个 JSON 对象本身，不要包裹散文说明、前后缀或 markdown 代码围栏。"
)


@dataclass
class P5AdvisoryResult:
    status: str                       # completed / skipped / failed
    reason: str = ""
    analysis_only: bool = True        # 本层输出恒为辅助分析，非事实、非门禁
    validation_strategy: dict = field(default_factory=dict)
    failure_interpretation: list = field(default_factory=list)
    repair_suggestions: list = field(default_factory=list)
    # GAP-P5-4: 重写式迁移的 source→target 结构映射（diff 等价证据，analysis_only，非事实门禁）。
    structure_mapping: list = field(default_factory=list)
    model_used: Optional[str] = None
    evidence_gap: str = ""            # advisory 缺席时的诚实登记（非阻断）
    attempted_chain: list = field(default_factory=list)
    model_error_category: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "analysis_only": self.analysis_only,
            "validation_strategy": self.validation_strategy,
            "failure_interpretation": self.failure_interpretation,
            "repair_suggestions": self.repair_suggestions,
            "structure_mapping": self.structure_mapping,
            "model_used": self.model_used,
            "evidence_gap": self.evidence_gap,
            "attempted_chain": self.attempted_chain,
            "model_error_category": self.model_error_category,
        }


class P5VerificationAgent:
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
        stage: str = "p5",
        strategy_id: str = "system-default",
        deterministic_facts: dict,
        skill_body: str = "",
        system_prompt: str = "",
    ) -> P5AdvisoryResult:
        """产出 P5 验证的策略/解读/修复建议（advisory，非事实、非门禁）。

        deterministic_facts：确定性验证器已产出的真实事实（十槽位状态/verify_results/
        conditional_results/can_be_completed/reason/p4_summary 摘要）——本层据此规划与解读，
        绝不改写其结论。任何异常/无模型 → status="skipped"，非阻断（确定性验证内核照常）。

        skill_body / system_prompt：由 handler 层（stage_handlers.py，X-4-5 白名单内）经
        canonical 上下文装配器组装后传入——本 agent 是纯消费者，不自行触碰上下文装配器
        （X-4-5 单一事实源：context_assembler 只由白名单执行节点调用）。skill-first 不受影响：
        P5 主 stage skill 正文仍被加载注入。
        """
        try:
            gw = self._get_gateway()
            # 无可用模型 → advisory 缺席，诚实 skipped（非阻断，不降级为规则伪造分析）。
            readiness = gw.stage_model_readiness(strategy_id=strategy_id, project_id=project_id,
                                                 require_tool_calling=True)
            if not readiness.get("available"):
                return P5AdvisoryResult(
                    status="skipped",
                    reason=("no_model_key: P5 LLM 策略/解读层需要 LLM 支持；无可用 Key 时本层缺席"
                            "（非阻断，确定性验证与反伪造门禁照常）"
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
                strategy_id=strategy_id, max_tokens=_P5_VERIFICATION_MAX_TOKENS, temperature=0.3,
                timeout=_P5_VERIFICATION_TIMEOUT, tracer=self.tracer)
            if loop["status"] != "completed":
                reason = loop.get("error_message") or loop.get("error_category") or "model_call_failed"
                return P5AdvisoryResult(
                    status="skipped", reason=f"llm_advisory_failed: {reason}",
                    evidence_gap="llm_advisory_unavailable",
                    model_used=loop.get("model_used"),
                    attempted_chain=loop.get("attempted_chain", []),
                    model_error_category=loop.get("error_category", "model_unavailable"))

            parsed = self._parse(loop.get("content", ""))
            return P5AdvisoryResult(
                status="completed", analysis_only=True, model_used=loop.get("model_used"),
                validation_strategy=parsed.get("validation_strategy", {}) or {},
                failure_interpretation=parsed.get("failure_interpretation", []) or [],
                repair_suggestions=parsed.get("repair_suggestions", []) or [],
                structure_mapping=parsed.get("structure_mapping", []) or [])
        except Exception as e:
            # 公理3：发声但不阻断——advisory 层任何异常都不得拖垮确定性 P5 验证。
            logger.warning("P5 advisory interpret failed (non-blocking): %s", e, exc_info=True)
            return P5AdvisoryResult(status="skipped", reason=f"llm_advisory_exception: {e}",
                                    evidence_gap="llm_advisory_unavailable",
                                    model_error_category="advisory_exception")

    # ── prompts ──────────────────────────────────────────────────────────────
    def _build_user_prompt(self, facts: dict) -> str:
        return (
            f"项目 ID：{facts.get('project_id')}\n"
            f"P4 执行摘要（迁移改了什么/目标栈/PoC 分级）：{json.dumps(facts.get('p4_summary', {}), ensure_ascii=False)[:2000]}\n"
            f"确定性验证事实——硬必需槽位（真实事实，勿改写）：{json.dumps(facts.get('hard_required', []), ensure_ascii=False)[:2500]}\n"
            f"确定性验证事实——条件必需槽位（真实命令结果）：{json.dumps(facts.get('conditional', []), ensure_ascii=False)[:2500]}\n"
            f"验证维度能力+环境探测（capability-first，非门禁；据此判断各维度适用性/是否待环境真验）："
            f"{json.dumps(facts.get('dimension_capabilities', {}), ensure_ascii=False)[:2000]}\n"
            f"确定性门禁认定：can_be_completed={facts.get('can_be_completed')}，reason={facts.get('reason')}\n"
            "请据以上【真实验证事实】产出：① validation_strategy（本次适用/不适用的验证维度 + 依据）；"
            "② failure_interpretation（对未通过/evidence_gap 槽位的失败根因，含期望 vs 实际）；"
            "③ repair_suggestions（回 P4 rework 的可执行修复建议）；"
            "④ structure_mapping（若为重写式迁移，给 source→target 结构对应作 diff 等价证据；否则空数组）。"
            "严禁产出「通过」结论、严禁改写上述事实或 can_be_completed。"
        )

    @staticmethod
    def _scan_json_shape(text: str) -> tuple[int, bool]:
        """扫描 JSON 文本的结构收敛状态：返回 (未闭合的括号深度, 是否停在字符串内部)。
        纯诊断，不做修补（同 acceptance_baseline_service / profiling_service 范式，D-06 同族）。"""
        depth = 0
        in_string = False
        escaped = False
        for ch in text:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
        return depth, in_string

    def _diagnose_parse_failure(self, text: str) -> dict:
        """构造可诊断信息（D-06）：响应字符长度 + 是否疑似截断 + 本次预算取值。"""
        depth, in_string = self._scan_json_shape(text)
        signals: list[str] = []
        if not text:
            signals.append("empty_content: 响应为空（可能推理链耗尽输出预算后无最终文本）")
        if in_string:
            signals.append("unclosed_string: 文本末尾停在未闭合的字符串内")
        if depth > 0:
            signals.append(f"unbalanced_depth={depth}: 有 {depth} 层 {{/[ 未闭合")
        if text and not text.rstrip().endswith(("}", "]")):
            signals.append("tail_not_closed: 末尾字符不是 } 或 ]")
        return {
            "content_len": len(text),
            "suspected_truncation": bool(signals),
            "truncation_signals": signals,
            "tail_snippet": text[-80:] if text else "",
            "max_tokens": _P5_VERIFICATION_MAX_TOKENS,
            "timeout_s": _P5_VERIFICATION_TIMEOUT,
            "env_knobs": "P5_VERIFICATION_MAX_TOKENS / P5_VERIFICATION_TIMEOUT",
        }

    def _parse(self, content: str) -> dict:
        from app.services.stage_agent_loop import extract_json_object
        data = extract_json_object(content)
        if isinstance(data, dict):
            return data
        text = (content or "").strip()
        diagnosis = self._diagnose_parse_failure(text)
        if diagnosis["suspected_truncation"]:
            logger.error(
                "P5 advisory: LLM 输出**疑似被截断**导致 JSON 解析失败（D-06 同族）—— "
                "响应长度=%d 字符, 截断信号=%s, 尾部=%r, "
                "本次预算 max_tokens=%s / timeout=%ss（可经 %s 调整）",
                diagnosis["content_len"], diagnosis["truncation_signals"],
                diagnosis["tail_snippet"], diagnosis["max_tokens"],
                diagnosis["timeout_s"], diagnosis["env_knobs"])
        else:
            logger.warning(
                "P5 advisory: LLM 输出**结构已收敛但非法 JSON**（非截断，需修 prompt 契约）—— "
                "响应长度=%d 字符, 尾部=%r", diagnosis["content_len"], diagnosis["tail_snippet"])
        return {"validation_strategy": {"raw": text[:1500], "parse_error": True,
                                        "parse_diagnosis": diagnosis},
                "failure_interpretation": [], "repair_suggestions": []}
