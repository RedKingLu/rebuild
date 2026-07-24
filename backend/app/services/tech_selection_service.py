"""P1→P2 技术路线选型服务 (R17.5-P4-FIX 批3, D-109).

在 P1 建档完成、提交 P1→P2 gate 时运行：LLM(Node Worker Agent) 基于 P0/P1 已识别的
真实源码事实（tech_stack / dependency / entry_points / config / infra）+ 项目
`migration_target`（目标 CPU 架构 + OS，引导期采集的硬约束）+ 约束，产出结构化
【技术路线选型建议】——目标语言/运行时、数据库、Web 框架、中间件替换、关键架构决策，
每项含 推荐 + 理由 + 备选。用户经 gate 裁决批准后落 `project.tech_selection` 成为项目红线。

设计（对齐 D-108 skill-first + D-110 工具循环 + D-098 ModelGateway）：
  - 选型【维度/需求/原则】写在 stage skill 正文（source/skills/p1/P-tech-selection/SKILL.md），
    本服务按需加载其正文注入 system prompt——Python 只保留【编排 + 锚点键】瘦身提示词，不把
    维度硬编码进代码（skill-first，可改 skill 不改码）。
  - 走 run_stage_tool_loop（与 P1 profiling 同构）：事实包 + migration_target 作为帮助，agent
    可按需 list_files/code_grep/fs_read 探读真实源深化选型、多轮推理，末轮产出结构化 JSON。
  - LLM 必需——无有效 Key/调用失败 → status="blocked"/"failed"，不降级为规则选型（D-097/公理3）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("rebuild.tech_selection_service")

from app.services.model_gateway import MODEL_UNAVAILABLE_USER_ACTIONS as _MODEL_USER_ACTIONS

# 通用锚点键（结构固定，样本值由 LLM 按项目生成）。维度需求/原则在 skill 正文，不在此硬编码。
TECH_SELECTION_KEYS = [
    "target_language", "runtime", "database", "web_framework",
    "middleware_replacements", "key_arch_decisions",
]

# 瘦身编排提示词（skill-first）：只声明身份 + 锚点键 + 接地/服从目标环境/脱敏的硬约束。
# 详细选型维度/原则/信创替代清单等一律走 skill 正文（P-tech-selection SKILL.md），此处不复制。
_SYSTEM_PROMPT = (
    "你是 rebuild 信创迁移平台的【P1→P2 技术路线选型 Agent】（Node Worker Agent）。平台已用确定性"
    "工具采集并由上游 LLM 识别了目标项目的真实源码事实（技术栈/依赖/入口/配置/基础设施线索），并"
    "提供了项目【目标运行环境 migration_target】（目标 CPU 架构 + 目标 OS，引导期用户点选的硬约束）。\n"
    "你的职责：基于【真实源码事实 + 目标运行环境 + 约束】综合推荐迁移的目标技术路线，供用户裁决。\n"
    "严格输出 JSON，顶层键：target_language / runtime / database / web_framework "
    "（各为对象 {recommendation, reasoning, alternatives:[{option, reasoning}]}）、"
    "middleware_replacements（数组 [{component, from, recommendation, reasoning, alternatives}]）、"
    "key_arch_decisions（数组 [{decision, recommendation, reasoning, alternatives}]）、"
    "overall_rationale（字符串）、open_questions（数组[字符串]）。\n"
    "硬约束：①每项 reasoning 必须援引输入事实中【真实存在】的信号，禁止输出与源无关的通用模板；"
    "②选型必须能在 migration_target 的 CPU/OS 上运行（信创优先国产化替代但以真实源+目标环境为准）；"
    "③证据不足项在 reasoning 标注不确定性并列入 open_questions，不臆造确定性；④连接串/密钥/口令值"
    "一律不输出。详细选型维度与原则见随附的选型 skill 正文，遵循之。"
)


@dataclass
class TechSelectionResult:
    status: str                       # completed / blocked / failed
    reason: str = ""
    selection: dict = field(default_factory=dict)
    model_used: Optional[str] = None
    tools_invoked: list = field(default_factory=list)
    attempted_chain: list = field(default_factory=list)
    model_error_category: str = ""
    model_user_actions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "selection": self.selection,
            "model_used": self.model_used,
            "tools_invoked": self.tools_invoked,
            "attempted_chain": self.attempted_chain,
            "model_error_category": self.model_error_category,
            "model_user_actions": self.model_user_actions,
        }


class TechSelectionService:
    """技术路线选型 via ModelGateway. `gateway` injectable for tests (blocked/parse paths
    without a live LLM), mirroring ProfilingService/IntakeService (no mock in prod path)."""

    _SKILL_REL = ("p1", "P-tech-selection")

    def __init__(self, *, gateway=None, tracer=None, auditor=None):
        self._gateway = gateway
        self.tracer = tracer
        self.auditor = auditor

    def _get_gateway(self):
        if self._gateway is not None:
            return self._gateway
        from app.dependencies import get_services
        return get_services().model_gateway

    def _skill_body(self, max_chars: int = 6000) -> str:
        """加载 P-tech-selection SKILL.md 正文（skill-first）。缺失 → 空串（编排提示词兜底，
        never 把维度重新硬编码进 Python，公理3 加载失败会由 skill_loader 记 not_connected）。"""
        try:
            from app.core.config import settings
            from app.services.skill_loader import load_skill_body
            skill_dir = settings.source_path / "skills" / self._SKILL_REL[0] / self._SKILL_REL[1]
            loaded = load_skill_body(str(skill_dir), max_chars=max_chars)
            return loaded.get("body", "") or ""
        except Exception:
            logger.warning("tech_selection: 选型 skill 正文加载失败（advisory，走编排提示词兜底）",
                           exc_info=True)
            return ""

    async def select(
        self,
        project_id: str,
        *,
        identification: dict,
        upstream: Optional[dict] = None,
        migration_target: Optional[dict] = None,
        run_id: Optional[str] = None,
        stage: str = "p1",
        strategy_id: str = "system-default",
    ) -> TechSelectionResult:
        """产出技术路线选型建议。identification=P1 建档识别（tech_stack/dependency/entry_points/
        config/infra），upstream=P0 识别结论，migration_target=目标 CPU/OS 红线。无 Key → blocked。"""
        gw = self._get_gateway()

        readiness = gw.stage_model_readiness(strategy_id=strategy_id, project_id=project_id,
                                             require_tool_calling=True)
        if not readiness.get("available"):
            self._trace("P1 tech_selection blocked: no model", project_id, run_id, stage)
            return TechSelectionResult(
                status="blocked",
                reason=("no_model_key: 技术路线选型需要 LLM 支持，请配置有效 API Key"
                        f"（不降级为规则选型）；{readiness.get('reason','')}"),
                attempted_chain=readiness.get("attempted_chain", []),
                model_error_category="model_unavailable",
                model_user_actions=readiness.get("user_actions", []))

        skill_body = self._skill_body()
        system_content = ((skill_body.strip() + "\n\n---\n\n" + _SYSTEM_PROMPT)
                          if skill_body.strip() else _SYSTEM_PROMPT)

        from app.services.stage_agent_loop import run_stage_tool_loop
        loop = await run_stage_tool_loop(
            gw, system_content=system_content,
            user_content=self._build_user_prompt(identification, upstream or {}, migration_target),
            project_id=project_id, run_id=run_id or "", stage=stage,
            strategy_id=strategy_id, max_tokens=16384, temperature=0.3, tracer=self.tracer)
        if loop["status"] != "completed":
            reason = loop.get("error_message") or loop.get("error_category") or "model_call_failed"
            self._trace(f"P1 tech_selection model call not completed: {reason}", project_id, run_id, stage)
            return TechSelectionResult(status="failed", reason=str(reason),
                                       model_used=loop.get("model_used"),
                                       attempted_chain=loop.get("attempted_chain", []),
                                       model_error_category=loop.get("error_category", "model_unavailable"),
                                       model_user_actions=_MODEL_USER_ACTIONS)

        parsed = self._parse(loop.get("content", ""))
        self._trace("P1 tech_selection completed (LLM)", project_id, run_id, stage)
        return TechSelectionResult(
            status="completed", selection=parsed, model_used=loop.get("model_used"),
            tools_invoked=loop.get("tools_invoked", []),
        )

    def _build_user_prompt(self, identification: dict, upstream: dict,
                           migration_target: Optional[dict]) -> str:
        """从 P0/P1 识别事实 + migration_target 组装 user 提示（bounded, 已脱敏源）。"""
        # 只挑选型相关的识别键，控体积。
        facts = {k: identification.get(k) for k in
                 ("tech_stack", "dependency_draft", "entry_points", "config_inventory",
                  "infra_clues", "module_structure")
                 if identification.get(k) is not None}
        facts_blob = json.dumps(facts, ensure_ascii=False, default=str)
        if len(facts_blob) > 20000:
            facts_blob = facts_blob[:20000] + "\n…[识别事实超长已截断，请就已给事实推理并在 open_questions 声明截断]"
        up = {k: upstream.get(k) for k in
              ("primary_language", "detected_stack", "source_environment_clues", "database_entry")
              if upstream.get(k) is not None}
        up_blob = json.dumps(up, ensure_ascii=False, default=str)
        mt_blob = json.dumps(migration_target, ensure_ascii=False, default=str) if migration_target \
            else "（未采集到 migration_target；请在 reasoning/open_questions 中声明目标环境未定，并给出与常见信创目标环境兼容的稳妥推荐）"
        return (
            "以下是平台采集并由上游 LLM 识别的目标项目【真实源码事实】、上游 P0 识别结论，以及项目"
            "【目标运行环境 migration_target】。请据此产出迁移的目标技术路线选型建议（严格输出契约 JSON）。\n\n"
            f"【目标运行环境 migration_target（硬约束）】：\n{mt_blob}\n\n"
            f"【上游 P0 识别结论】：\n{up_blob}\n\n"
            f"【P1 建档识别事实（技术栈/依赖/入口/配置/基础设施/模块）】：\n{facts_blob}\n\n"
            "要求：每项 recommendation 附 reasoning（须援引上述真实事实中的具体信号，勿套通用模板）"
            "与 alternatives；选型必须能在 migration_target 的 CPU/OS 上落地；证据不足项列入 "
            "open_questions；连接串/密钥值不得回显。如需查阅真实源可调用工具按需读取。严格输出上述 JSON。"
        )

    def _parse(self, content: str) -> dict:
        from app.services.stage_agent_loop import extract_json_object
        data = extract_json_object(content)
        if data is not None:
            return data
        logger.warning("P1 tech_selection: LLM 输出无法解析为 JSON（返工重试结构化输出）")
        return {"parse_error": True, "raw": (content or "").strip()[:2000]}

    def _trace(self, summary: str, project_id, run_id, stage) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.write("model_call", action="p1_tech_selection", summary=summary,
                              project_id=project_id, run_id=run_id, stage=stage)
        except Exception:
            logger.debug("P1 tech_selection trace 写入失败（advisory）", exc_info=True)
