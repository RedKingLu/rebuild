"""P2 assessment service (R10 T9).

Produces the 6 P2 assessment outputs defined by the stage contract
`文档/03-流程与运行时/01-P0-P6阶段契约.md` §4.5, based on the P1 project archive +
Environment Profile draft + p2_input_manifest:

  assessment_report / risk_list / blocker_list / uncertainty_list /
  validation_gap_list / resource_needs

Design locks (R10-2 §5.2, user-decided):
  - Q-R10-2: LLM is REQUIRED. No available model → status="blocked"
    (reason=no_model_key). NO rule-based fallback — the assessment is genuinely
    model-driven, and an honest blocked beats a fake rule evaluation.
  - §4.4-8 / §4.7-5 / STOP-4: model output is AUXILIARY analysis, never fact —
    every result carries analysis_only=True and the Evidence records that the
    model output was marked as analysis, not treated as Evidence itself.

The model is called through ModelGateway (D-098, no hardcoded model/endpoint).
`gateway` is injectable so tests exercise the blocked path and parsing without a
live LLM call.

R10 T10: `persist_evidence` writes the §4.7 five Evidence items to
workspace/evidence/ (via AETService) so they are queryable (D-066). A
blocked/failed assessment persists nothing (§4.10 — no fake completed Evidence).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# WP-6 (Q-R17.3-6-2): 模型全失败中断时前端可采取操作（复用 gateway 单一事实源）。
from app.services.model_gateway import MODEL_UNAVAILABLE_USER_ACTIONS as _MODEL_USER_ACTIONS
# V26.2 返工批次二（Q-B2-1 / Q-B2-2）：共享截断诊断 + "不设即无上限"的 env 旋钮解析。
from app.services.stage_agent_loop import (
    DEFAULT_STAGE_TIMEOUT_SECONDS as _STAGE_TIMEOUT,
    diagnose_parse_failure as _diagnose_parse_failure_shared,
    log_parse_failure as _log_parse_failure,
    optional_int_env,
)

# `B-V262-TOKENBUDGET-UNFIXED-4`（P0）：真实规模真跑实测本调用的 completion **两次触顶原硬编码
# max_tokens=16384**（call_log `scall_2028cf9b15fc`=16384 / `scall_1067d990b613`=16387）→ P2 评估
# 报告 JSON 中途截断 → 阶段判 `unparseable_output`，6 类评估产出全空。
# 用户 2026-09-16 裁决 Q-B2-1：取消平台侧硬预算（不再抬高天花板）。默认 None ⇒ 请求体无
# max_tokens 键；旋钮 `P2_ASSESSMENT_MAX_TOKENS` 保留作逃生阀。实测触顶值 16384/16387 仅作留痕。
_ASSESSMENT_MAX_TOKENS = optional_int_env("P2_ASSESSMENT_MAX_TOKENS")

# §4.5 six core assessment outputs
ASSESSMENT_OUTPUTS = [
    "assessment_report", "risk_list", "blocker_list",
    "uncertainty_list", "validation_gap_list", "resource_needs",
]

_SYSTEM_PROMPT = (
    "你是 rebuild 平台的 P2 评估 Agent。遵循已加载的 P-migration-assessment stage skill 完成迁移/重构评估"
    "（评估维度、目标库证据化对比 + ADR、evidence_gap 诚实、不预判终局、DB 方言证据来源等要求以 skill 为准）。"
    "基于 P0/P1 阶段完成包（含 acceptance_baseline 原始验收基准）与目标运行环境评估。"
    "严格输出 JSON，锚点键（供机器解析）：assessment_report(对象), risk_list(数组，每项含 title/"
    "risk_level[L0-L5]/source/basis/evidence_refs), blocker_list(数组，每项含 title/evidence_refs), "
    "uncertainty_list(数组), validation_gap_list(数组，每项含 title/evidence_refs), resource_needs(数组)。"
    # assessment_report 子字段仅列【输出 schema 键名】；含义/填法/rigor（证据化对比/不选定单一库/
    # evidence_gap 不预判终局/DB 方言证据来源）由已加载的 P-migration-assessment skill 正文承载（skill-first）。
    "assessment_report 对象必须含子字段键（含义与填法见 skill 正文）：compatibility_hosting, modernization, "
    "database_migration(含 adr_candidates 数组), deployment_hosting, poc_scope, questions_for_user。"
    "内联引用（硬约束）：每条 risk/blocker/validation_gap 的 evidence_refs 只能引用【可引用上游产物】清单中的 ref，"
    "不得杜撰；确无可依据时给空数组。你的输出是【辅助分析，非事实】，不做代码修改、不替代 P3 规划或 P5 验证。"
    "输出格式（硬约束）：只输出单个 JSON 对象本身，不要包裹任何散文说明、前后缀或 markdown 代码围栏。"
)


@dataclass
class AssessmentResult:
    status: str                       # completed / blocked / failed
    reason: str = ""
    analysis_only: bool = True        # §4.4-8: model output is auxiliary analysis, not fact
    assessment_report: dict = field(default_factory=dict)
    risk_list: list = field(default_factory=list)
    blocker_list: list = field(default_factory=list)
    uncertainty_list: list = field(default_factory=list)
    validation_gap_list: list = field(default_factory=list)
    resource_needs: list = field(default_factory=list)
    model_used: Optional[str] = None
    evidence: list = field(default_factory=list)
    context_refs: list = field(default_factory=list)   # R10-5 P1-C: assembled C0-C6 layers
    skill_refs: list = field(default_factory=list)      # R10-5 P1-C: C3 skill refs (metadata)
    # R17.3-6 WP-6 (Q-R17.3-6-2): 模型全失败强制中断时的「已尝试模型链路」+ 结构化错误分类
    # + 用户可采取操作，透传前端显式报错（不静默降级）。
    attempted_chain: list = field(default_factory=list)
    model_error_category: str = ""
    model_user_actions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "analysis_only": self.analysis_only,
            "assessment_report": self.assessment_report,
            "risk_list": self.risk_list,
            "blocker_list": self.blocker_list,
            "uncertainty_list": self.uncertainty_list,
            "validation_gap_list": self.validation_gap_list,
            "resource_needs": self.resource_needs,
            "model_used": self.model_used,
            "evidence": self.evidence,
            "context_refs": self.context_refs,
            "skill_refs": self.skill_refs,
            "attempted_chain": self.attempted_chain,
            "model_error_category": self.model_error_category,
            "model_user_actions": self.model_user_actions,
        }


class AssessmentService:
    def __init__(self, *, gateway=None, tracer=None, auditor=None, aet_service=None):
        self._gateway = gateway
        self.tracer = tracer
        self.auditor = auditor
        self._aet_service = aet_service

    def _get_gateway(self):
        if self._gateway is not None:
            return self._gateway
        from app.dependencies import get_services
        return get_services().model_gateway

    def _get_aet(self):
        if self._aet_service is not None:
            return self._aet_service
        from app.dependencies import get_services
        return get_services().aet_service

    async def assess(
        self,
        project_id: str,
        *,
        run_id: Optional[str] = None,
        stage: str = "p2",
        user_goal: str = "",
        strategy_id: str = "system-default",
        system_prompt: Optional[str] = None,
        skill_body: str = "",
        context_package: Optional[dict] = None,
    ) -> AssessmentResult:
        """Run the P2 assessment. Returns AssessmentResult (status ∈ completed/blocked/failed).

        R10-5 P1-C: when a `system_prompt` (assembled via context_assembler.build_system_prompt
        with C0-C6 + C3 Skill metadata) is provided, it is combined with the P2 domain
        output contract so the model receives the unified context AND the strict JSON
        schema. `context_package` supplies context_refs/skill_refs for Evidence/Trace.

        D-108: `skill_body` 是已加载的 P2 评估 stage skill 正文（P-migration-assessment）——
        评估需求（8 维度/ADR/evidence_gap 诚实/不预判终局等）随 skill 走，提示词只保留编排 +
        锚点字段（skill-first，换需求=改 skill 而非改代码）。
        """
        gw = self._get_gateway()

        # Q-R10-2 / WP-6: no available model → blocked, no rule fallback. 就绪度预检给出
        # 「候选模型链路」，即便一次调用都未发生也能让前端显式看到考察过的模型链路。
        readiness = gw.stage_model_readiness(strategy_id=strategy_id, project_id=project_id,
                                             require_tool_calling=True)
        if not readiness.get("available"):
            self._trace("P2 assessment blocked: no model", project_id, run_id, stage)
            return AssessmentResult(
                status="blocked",
                reason=("no_model_key: 评估需要 LLM 支持，请配置有效 API Key（P2 不降级为规则评估）"
                        f"；{readiness.get('reason','')}"),
                attempted_chain=readiness.get("attempted_chain", []),
                model_error_category="model_unavailable",
                model_user_actions=readiness.get("user_actions", []))

        context_refs, skill_refs = self._context_refs(context_package)
        inputs = self._gather_inputs(project_id, user_goal)
        # Combine assembled context (governance/product/architecture metadata) + the loaded
        # P2 assessment stage skill body (D-108: 评估需求随 skill 走) + the P2 orchestration
        # contract. 契约放最后以保留 JSON schema 指令（真实产物）。
        parts = [p for p in (system_prompt, (skill_body or None), _SYSTEM_PROMPT) if p]
        system_content = "\n\n---\n\n".join(parts)

        # 批2 (D-110): P2 从单次 chat → 工具循环 Node Worker Agent。前序产物清单/内容仍作 user 提示
        # 帮助，agent 可按需 read_artifact/fs_read/code_grep 探读真实源与上游产物做证据化评估、多轮
        # 推理，末轮产出 6 类结构化评估契约（契约不变，D-108）。走 call_stream 天然读项目模型选择。
        from app.services.stage_agent_loop import run_stage_tool_loop
        loop = await run_stage_tool_loop(
            gw, system_content=system_content, user_content=self._build_user_prompt(inputs),
            project_id=project_id, run_id=run_id or "", stage=stage,
            strategy_id=strategy_id, max_tokens=_ASSESSMENT_MAX_TOKENS, temperature=0.3,
            tracer=self.tracer)
        if loop["status"] != "completed":
            reason = loop.get("error_message") or loop.get("error_category") or "model_call_failed"
            self._trace(f"P2 assessment model call not completed: {reason}", project_id, run_id, stage)
            # a configured-but-failing model is a failure, not a silent success (公理3);
            # WP-6: 携「已尝试模型链路」透传前端显式报错（不静默降级）。
            return AssessmentResult(status="failed", reason=str(reason),
                                    model_used=loop.get("model_used"),
                                    attempted_chain=loop.get("attempted_chain", []),
                                    model_error_category=loop.get("error_category", "model_unavailable"),
                                    model_user_actions=_MODEL_USER_ACTIONS)

        parsed = self._parse(loop.get("content", ""))
        model_used = loop.get("model_used")
        evidence = self._build_evidence(inputs, parsed, model_used, context_refs, skill_refs)
        self._trace("P2 assessment completed (analysis_only)", project_id, run_id, stage)
        return AssessmentResult(
            status="completed", analysis_only=True, model_used=model_used, evidence=evidence,
            context_refs=context_refs, skill_refs=skill_refs,
            **{k: parsed.get(k, {} if k == "assessment_report" else []) for k in ASSESSMENT_OUTPUTS},
        )

    @staticmethod
    def _context_refs(context_package: Optional[dict]) -> tuple[list, list]:
        """Extract context_refs (assembled C0-C6 layers) + skill_refs (C3 skill metadata)
        from an assembled context package for Evidence/Trace provenance (R10-5 P1-C)."""
        if not context_package:
            return [], []
        trace = context_package.get("assembly_trace", {}) or {}
        context_refs = list(trace.get("layers_assembled", []))
        skill_refs = [s.get("skill_id") or s.get("name")
                      for s in (context_package.get("skills") or [])
                      if s.get("skill_id") or s.get("name")]
        return context_refs, skill_refs

    # ── inputs (D-107 清单驱动按需加载) ──────────────────────────────────
    def _gather_inputs(self, project_id: str, user_goal: str) -> dict:
        """读前序各阶段 Stage Completion Package 清单 → 据 key_for_next 按需加载关键产物内容。

        D-107 / WP-2：不再写死文件名列表（对齐 AGENTS §2.3——由清单决定读什么）。P1 完成包
        把 acceptance_baseline / tech_stack / dependency / entry_points / config / uncertainty /
        profiling_summary 标记为 key_for_next=True，故 P2 天然读到 acceptance_baseline 内容——
        结构性根治 GAP-P2-1（P2 曾因看不到基线内容而误报"No build/test validation evidence
        from P1"）。缺失的清单/产物如实记入 missing，不臆造（§4.7-1）。
        """
        inputs: dict[str, Any] = {"project_id": project_id, "user_goal": user_goal,
                                  "sources_read": [], "missing": [], "prior_packages": {}}
        try:
            from app.services.stage_package import read_stage_package, stage_artifact_dir
            from app.services.workspace_service import workspace_path
            # 前序阶段清单（P0/P1）；按需加载 key_for_next 产物内容。
            for stage in ("p0", "p1"):
                pkg = read_stage_package(project_id, stage)
                if not pkg:
                    inputs["missing"].append(f"{stage}/_stage_package.json")
                    continue
                # 清单摘要（产物描述 + 下阶段建议）进入 LLM 输入（Agent 据此判断读什么）。
                inputs["prior_packages"][stage] = {
                    "products": pkg.get("products", []),
                    "evidence_summary": pkg.get("evidence_summary", {}),
                    "next_stage_advice": pkg.get("next_stage_advice", ""),
                }
                stage_dir = stage_artifact_dir(project_id, stage)
                for prod in pkg.get("products", []):
                    if not prod.get("key_for_next"):
                        continue
                    fn = prod.get("file")
                    if not fn:
                        continue
                    fp = stage_dir / fn
                    ref = f"artifacts/{stage}/{fn}"
                    if not fp.exists():
                        inputs["missing"].append(ref)
                        continue
                    try:
                        if fn.endswith(".md"):
                            inputs[ref] = fp.read_text(encoding="utf-8")
                        else:
                            inputs[ref] = json.loads(fp.read_text(encoding="utf-8"))
                        inputs["sources_read"].append(ref)
                    except Exception:
                        inputs["missing"].append(f"{ref}(unreadable)")
            # Environment Profile（D-051，位于 .rebuild/environment.json，非 artifacts/）。
            env_fp = workspace_path(project_id) / ".rebuild" / "environment.json"
            if env_fp.exists():
                try:
                    inputs["environment"] = json.loads(env_fp.read_text(encoding="utf-8"))
                    inputs["sources_read"].append(".rebuild/environment.json")
                except Exception:
                    inputs["missing"].append(".rebuild/environment.json(unreadable)")
            else:
                inputs["missing"].append(".rebuild/environment.json")
        except Exception as e:
            inputs["missing"].append(f"workspace_error: {e}")
        return inputs

    def _build_user_prompt(self, inputs: dict) -> str:
        # 可引用上游产物清单：由本阶段真实读取到的输入产物构造（confirmed on disk，含
        # artifacts/{stage}/ 分层 ref），供模型在 evidence_refs 内联引用，避免杜撰 artifact id。
        citable = [r for r in inputs.get("sources_read", []) if r.startswith("artifacts/")]
        # 已加载产物内容（分层 ref → 内容），供 LLM 据真实基线评估可验证性/验证缺口。
        loaded = {k: v for k, v in inputs.items() if isinstance(k, str) and k.startswith("artifacts/")}
        return (
            f"项目 ID：{inputs.get('project_id')}\n"
            f"用户目标：{inputs.get('user_goal') or '（未提供）'}\n"
            f"前序阶段完成包清单（P0/P1 产物描述 + 下阶段建议）：{json.dumps(inputs.get('prior_packages', {}), ensure_ascii=False)[:1500]}\n"
            f"已读取输入：{inputs.get('sources_read')}\n"
            f"缺失输入（登记为不确定项来源）：{inputs.get('missing')}\n"
            f"目标运行环境（Environment Profile）：{json.dumps(inputs.get('environment', {}), ensure_ascii=False)[:800]}\n"
            f"【可引用上游产物】（evidence_refs 只能取自此清单，勿杜撰）：{citable}\n"
            f"P1 档案/基准/清单内容（含 acceptance_baseline，据此评估可验证性与验证缺口）："
            f"{json.dumps(loaded, ensure_ascii=False)[:4000]}\n"
            "请据此产出 6 类评估输出（JSON），并为每条 risk/blocker/validation_gap 内联填写 evidence_refs。"
        )

    # ── parse ──────────────────────────────────────────────────────────────
    def _parse(self, content: str) -> dict:
        """Parse the LLM JSON output defensively into the 6 outputs.

        批2: robust extractor handles prose-wrapped / fenced JSON from the tool loop.

        V26.2 返工批次二（甲 ②）：本处此前无截断诊断 —— 真实规模真跑撞顶 16384/16387 时，现场
        只看得到"输出非合法 JSON"，看不出"被砍断"。现接入共享诊断（`suspected_truncation` /
        `truncation_signals`），诊断随 `assessment_report.parse_diagnosis` 落进产物。
        """
        from app.services.stage_agent_loop import extract_json_object
        data = extract_json_object(content)
        if data is not None:
            out: dict[str, Any] = data
        else:
            # unparseable → keep raw text + 可诊断信息 in the report; lists stay empty
            # (honest, the NodeLoop ReviewPass retries structured output — T2)
            text = (content or "").strip()
            diagnosis = _diagnose_parse_failure_shared(
                text, max_tokens=_ASSESSMENT_MAX_TOKENS, timeout_s=_STAGE_TIMEOUT,
                env_knobs="P2_ASSESSMENT_MAX_TOKENS")
            _log_parse_failure(logger, "P2 assessment", diagnosis)
            out = {"assessment_report": {"raw": text[:2000], "parse_error": True,
                                         "parse_diagnosis": diagnosis}}
        # assessment_report must be an OBJECT; a non-dict value (model returned a bare
        # string / prose while the JSON itself parsed) is unstructured output — normalize
        # to a parse_error report so downstream never receives a bare str (single source
        # of truth for the "assessment_report 非对象" case, avoids .get on str).
        rep = out.get("assessment_report")
        if "assessment_report" in out and not isinstance(rep, dict):
            out["assessment_report"] = {"raw": str(rep)[:2000], "parse_error": True}
        return out

    # ── evidence (§4.7 five items + context provenance) ────────────────────
    def _build_evidence(self, inputs: dict, parsed: dict, model_used: Optional[str],
                        context_refs: Optional[list] = None,
                        skill_refs: Optional[list] = None) -> list:
        return [
            {"evidence_id": "ev-p2-inputs", "type": "assessment_inputs",
             "claim": "评估基于的输入", "sources": inputs.get("sources_read", []),
             "missing": inputs.get("missing", []),
             "context_refs": context_refs or [], "skill_refs": skill_refs or []},   # §4.7-1 + P1-C provenance
            {"evidence_id": "ev-p2-risk-basis", "type": "risk_basis",
             "claim": "风险判断依据", "count": len(parsed.get("risk_list", [])),
             "items": self._trace_items(parsed.get("risk_list", []),
                                        keys=("title", "risk_level", "source", "basis"))},  # §4.7-2
            {"evidence_id": "ev-p2-blocker-source", "type": "blocker_source",
             "claim": "阻塞项来源", "count": len(parsed.get("blocker_list", [])),
             "items": self._trace_items(parsed.get("blocker_list", []),
                                        keys=("title", "source"))},             # §4.7-3
            {"evidence_id": "ev-p2-gap-source", "type": "validation_gap_source",
             "claim": "验证缺口来源", "count": len(parsed.get("validation_gap_list", [])),
             "items": self._trace_items(parsed.get("validation_gap_list", []),
                                        keys=("title", "source"))},             # §4.7-4
            {"evidence_id": "ev-p2-model-analysis", "type": "model_output_marker",
             "claim": "模型输出已标记为辅助分析而非事实", "analysis_only": True,
             "model": model_used},                                              # §4.7-5 (STOP-4)
        ]

    @staticmethod
    def _trace_items(raw: list, *, keys: tuple) -> list:
        """Project each list item down to its traceable fields (source/basis/…)
        so every risk/blocker/gap traces back to a read file or identified item
        (T10 acceptance). Tolerates non-dict items without hallucinating."""
        items = []
        for it in raw:
            if isinstance(it, dict):
                items.append({k: it[k] for k in keys if k in it})
            else:
                items.append({"title": str(it)})
        return items

    # ── evidence persistence (R10 T10) ──────────────────────────────────────
    def persist_evidence(self, project_id: str, result: "AssessmentResult",
                         *, stage: str = "p2", aet_service=None) -> list:
        """Persist the §4.7 five Evidence items to workspace/evidence/ so they are
        queryable (D-066: 无 Evidence 不得 completed). Returns the persisted
        evidence dicts (their ids serve as Gate evidence_refs).

        §4.10 honest failure: a blocked/failed assessment has no grounded Evidence,
        so nothing is written — a non-completed stage never fakes completed Evidence.
        """
        if result.status != "completed":
            return []
        aet = aet_service or self._get_aet()
        persisted: list = []
        for ev in result.evidence:
            ev_id = ev["evidence_id"]
            # write_evidence owns evidence_id/type/status/source/claim/stage/created_at;
            # everything else (sources/missing/items/count/analysis_only/model) is extra.
            extra = {k: v for k, v in ev.items()
                     if k not in ("evidence_id", "type", "claim")}
            try:
                written = aet.write_evidence(
                    project_id=project_id, evidence_id=ev_id,
                    evidence_type=ev["type"], status="candidate",
                    source="p2_assessment", claim=ev.get("claim", ""),
                    stage=stage, extra=extra)
                persisted.append(written)
            except Exception:
                # D-066 gate: surface the failure (公理3), don't silently swallow —
                # a missing Evidence file means the handler must not mark P2 completed.
                logger.warning("P2 Evidence persist failed for %s", ev_id, exc_info=True)
        return persisted


    def _trace(self, summary: str, project_id, run_id, stage) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.write("model_call", action="p2_assessment", summary=summary,
                              project_id=project_id, run_id=run_id, stage=stage)
        except Exception:
            # advisory：trace 仅用于可观测，P2 评估结果不受影响；记录以便定位偶发写失败。
            logger.debug("P2 assessment trace 写入失败（advisory）", exc_info=True)
