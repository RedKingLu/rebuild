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

# §4.5 six core assessment outputs
ASSESSMENT_OUTPUTS = [
    "assessment_report", "risk_list", "blocker_list",
    "uncertainty_list", "validation_gap_list", "resource_needs",
]

_SYSTEM_PROMPT = (
    "你是 rebuild 平台的 P2 评估 Agent。基于 P1 项目档案、Environment Profile 草案与 "
    "p2_input_manifest，评估软件重构/信创迁移的风险、阻塞项、不确定项、验证缺口与资源需求。"
    "严格输出 JSON，键为：assessment_report(对象), risk_list(数组，每项含 title/risk_level[L0-L5]/"
    "source/basis), blocker_list(数组), uncertainty_list(数组), validation_gap_list(数组), "
    "resource_needs(数组)。你的输出是【辅助分析，非事实】，不得替代 Evidence、不做代码修改、"
    "不替代 P3 规划或 P5 验证。"
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
        context_package: Optional[dict] = None,
    ) -> AssessmentResult:
        """Run the P2 assessment. Returns AssessmentResult (status ∈ completed/blocked/failed).

        R10-5 P1-C: when a `system_prompt` (assembled via context_assembler.build_system_prompt
        with C0-C6 + C3 Skill metadata) is provided, it is combined with the P2 domain
        output contract so the model receives the unified context AND the strict JSON
        schema. `context_package` supplies context_refs/skill_refs for Evidence/Trace.
        """
        gw = self._get_gateway()

        # Q-R10-2: no available model → blocked, no rule fallback.
        status = gw.get_status()
        overall = getattr(status, "overall_status", None) or (
            status.get("overall_status") if isinstance(status, dict) else None)
        if overall != "available":
            self._trace("P2 assessment blocked: no model", project_id, run_id, stage)
            return AssessmentResult(
                status="blocked",
                reason="no_model_key: 评估需要 LLM 支持，请配置有效 API Key（P2 不降级为规则评估）")

        context_refs, skill_refs = self._context_refs(context_package)
        inputs = self._gather_inputs(project_id, user_goal)
        # Combine assembled context (governance/product/architecture/skill metadata)
        # with the P2 domain output contract — the contract stays last so the JSON
        # schema instruction is preserved (real products, not weakened).
        system_content = (f"{system_prompt}\n\n---\n\n{_SYSTEM_PROMPT}"
                          if system_prompt else _SYSTEM_PROMPT)
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": self._build_user_prompt(inputs)},
        ]

        result = await gw.call(messages=messages, strategy_id=strategy_id,
                               max_tokens=4096, temperature=0.3, source="api")
        if result.get("status") != "completed":
            reason = result.get("error_message") or result.get("error_category") or "model_call_failed"
            self._trace(f"P2 assessment model call not completed: {reason}", project_id, run_id, stage)
            # a configured-but-failing model is a failure, not a silent success (公理3)
            return AssessmentResult(status="failed", reason=str(reason),
                                    model_used=result.get("model"))

        parsed = self._parse(result.get("content", ""))
        model_used = result.get("model")
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

    # ── inputs ───────────────────────────────────────────────────────────
    def _gather_inputs(self, project_id: str, user_goal: str) -> dict:
        """Read P1 archive / p2_input_manifest / env profile from the workspace
        (best-effort; missing inputs are recorded, not hallucinated — §4.7-1)."""
        inputs: dict[str, Any] = {"project_id": project_id, "user_goal": user_goal,
                                  "sources_read": [], "missing": []}
        try:
            from app.services.workspace_service import workspace_path
            ws = workspace_path(project_id)
            for name in ("p2_input_manifest.json", "profiling_summary.json",
                         "intake_report.json", "environment.json"):
                fp = ws / "artifacts" / name
                if not fp.exists():
                    fp = ws / name if (ws / name).exists() else fp
                if fp.exists():
                    try:
                        inputs[name] = json.loads(fp.read_text(encoding="utf-8"))
                        inputs["sources_read"].append(name)
                    except Exception:
                        inputs["missing"].append(f"{name}(unreadable)")
                else:
                    inputs["missing"].append(name)
        except Exception as e:
            inputs["missing"].append(f"workspace_error: {e}")
        return inputs

    def _build_user_prompt(self, inputs: dict) -> str:
        return (
            f"项目 ID：{inputs.get('project_id')}\n"
            f"用户目标：{inputs.get('user_goal') or '（未提供）'}\n"
            f"已读取输入：{inputs.get('sources_read')}\n"
            f"缺失输入（登记为不确定项来源）：{inputs.get('missing')}\n"
            f"P1 档案/清单摘要：{json.dumps({k: v for k, v in inputs.items() if k.endswith('.json')}, ensure_ascii=False)[:3000]}\n"
            "请据此产出 6 类评估输出（JSON）。"
        )

    # ── parse ──────────────────────────────────────────────────────────────
    def _parse(self, content: str) -> dict:
        """Parse the LLM JSON output defensively into the 6 outputs."""
        out: dict[str, Any] = {}
        text = (content or "").strip()
        # tolerate ```json fences
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                out = data
        except Exception:
            # unparseable → keep raw text in the report; lists stay empty (honest,
            # the NodeLoop ReviewPass retries structured output — T2)
            out = {"assessment_report": {"raw": text[:2000], "parse_error": True}}
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
            pass  # tracing advisory
