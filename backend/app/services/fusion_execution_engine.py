"""FusionExecutionEngine — full execution layer (R13-5).

Orchestrates multi-model aggregation behind a Fusion virtual model (方案 E, D-035):
    Panel fan-out  →  Judge (temp=0, 5-field JSON)  →  Synthesizer (single content)
plus Self-MoA standard degradation, fallback, budget/timeout, degraded states.

Anti-recursion is guaranteed at the CALL-PRIMITIVE level: `_raw_call()` connects
directly to LiteLLMAdapter and NEVER passes through ProviderRegistry.resolve_model(),
so it can never be re-routed to a Fusion profile. This is the third layer of the
triple-guard (participant validation + max_fusion_depth=1 + raw primitive).

方案 E hard constraint: Panel / Judge / Synthesizer NEVER execute tools. The
engine never passes `tools`/`tool_choice` to the adapter — verified by tests.

External evidence absorbed (R13-2C A-grade): OpenRouter Judge 5-field JSON shape,
dual-degradation (judge-failed → degraded; hard-failed → status:error), recursion
depth=1. Sakana excluded_providers field. Hermes reference_max_tokens → panel_max_tokens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.adapters.litellm_adapter import LiteLLMAdapter, ModelCallResult
from app.models.fusion_profile import FusionProfile, FusionRun, FusionRunParticipant

logger = logging.getLogger("rebuild.fusion_engine")

# ── Constants ─────────────────────────────────────────────────────────

MAX_PANEL_MODELS = 8          # OpenRouter caps panel at 8
DEFAULT_SELF_MOA_SAMPLES = 3  # K samples for Self-MoA
JUDGE_MAX_RETRY = 1           # JSON parse retry budget


# ── Lightweight result types ──────────────────────────────────────────

@dataclass
class RawCallResult:
    """Result of one `_raw_call()` sub-call."""
    call_id: str
    role: str                      # panel / judge / synthesizer / self_moa_sample
    profile_ref: str
    provider_id: str
    model: str
    perspective: str
    status: str                    # completed / failed
    content: str
    latency_ms: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    error_category: str = ""
    error_message: str = ""
    trace_ref: str = ""


@dataclass
class EngineResult:
    """Top-level result of one FusionExecutionEngine.execute()."""
    status: str                    # completed / degraded / failed
    strategy: str                  # panel_judge_synth / self_moa / fallback_single
    content: str                   # single final content (上层透明)
    fusion_run_id: str
    fusion_profile_id: str
    degraded: bool = False
    degrade_reason: Optional[str] = None
    fusion_metadata: dict = field(default_factory=dict)
    error_message: str = ""


# ── WP-5.1: _raw_call() anti-recursion primitive ─────────────────────

async def _raw_call(
    *,
    provider_id: str,
    model: str,                  # already litellm-normalized (openai/...)
    messages: list[dict],
    api_base: str,
    api_key: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: Optional[float] = None,
    role: str = "panel",
    perspective: str = "",
    call_type: str = "fusion_panel",
    parent_call_id: str = "",
    adapter: LiteLLMAdapter,
) -> RawCallResult:
    """Call a real model DIRECTLY via the adapter — bypass resolve_model entirely.

    Anti-recursion guarantee: this function never touches ProviderRegistry, so it
    cannot be re-dispatched to a Fusion profile. It is the primitive-level guard.

    方案 E: never passes tools/tool_choice — Fusion sub-calls are text-only.
    """
    t0 = time.monotonic()
    result: ModelCallResult = await adapter.complete(
        model=model,
        messages=messages,
        api_base=api_base,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    latency = (time.monotonic() - t0) * 1000

    usage = result.usage_summary or {}
    return RawCallResult(
        call_id=result.call_id,
        role=role,
        profile_ref=f"{provider_id}/{model.split('/')[-1]}" if "/" in model else f"{provider_id}/{model}",
        provider_id=provider_id,
        model=model,
        perspective=perspective,
        status=result.status,
        content=result.content if result.status == "completed" else "",
        latency_ms=result.latency_ms or round(latency, 1),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
        error_category=result.error_category,
        error_message=result.error_message,
    )


# ── Engine ────────────────────────────────────────────────────────────

class FusionExecutionEngine:
    """Full Fusion execution layer. R13-5.

    Stateless w.r.t. model calls — each execute() is one Fusion run. Persists a
    fusion_run + N fusion_run_participants and returns a single content + metadata.
    """

    def __init__(
        self,
        db: Session,
        gateway,            # ModelGateway (for _resolve_key + registry lookups)
        trace_writer=None,
        audit_writer=None,
        adapter: Optional[LiteLLMAdapter] = None,
    ):
        self.db = db
        self.gateway = gateway
        self.trace_writer = trace_writer
        self.audit_writer = audit_writer
        self.adapter = adapter or LiteLLMAdapter()

    # ── Provider + key resolution (reuses gateway, NOT resolve_model) ──

    def _lookup(self, profile_ref: str) -> Optional[dict]:
        """Resolve a participant profile_ref → {profile, provider, api_base, api_key, model}.

        Uses registry READS (get_profile/get_provider) + gateway._resolve_key() —
        never resolve_model(), so no dispatch recursion.
        """
        reg = self.gateway._registry
        profile = reg.get_profile(profile_ref)
        if not profile:
            return None
        provider = reg.get_provider(profile.provider_id)
        if not provider:
            return None
        api_format = provider.api_format
        from app.providers.provider_registry import resolve_api_model_name
        litellm_model = resolve_api_model_name(profile, api_format)
        api_base = (provider.endpoint_anthropic
                    if api_format == "anthropic" and provider.endpoint_anthropic
                    else provider.endpoint_openai or provider.endpoint_anthropic)
        key_val, key_source = self.gateway._resolve_key(provider, explicit_provider=False)
        return {
            "profile": profile,
            "provider": provider,
            "api_format": api_format,
            "api_base": api_base or "",
            "api_key": key_val,
            "key_source": key_source,
            "litellm_model": litellm_model,
            "profile_ref": profile_ref,
        }

    # ── Trace / Audit helpers ─────────────────────────────────────────

    def _trace(self, action: str, **extra) -> str:
        if self.trace_writer:
            try:
                t = self.trace_writer.write("fusion_call", action=action, **extra)
                tid = t.get("trace_id", "") if isinstance(t, dict) else ""
                return tid or f"trace-{uuid.uuid4().hex[:12]}"
            except Exception:
                return f"trace-{uuid.uuid4().hex[:12]}"
        return f"trace-{uuid.uuid4().hex[:12]}"

    def _audit(self, action: str, **extra):
        if self.audit_writer:
            try:
                self.audit_writer.write("fusion_high_risk", action=action, **extra)
            except Exception:
                pass

    # ── Persistence helpers ───────────────────────────────────────────

    def _new_run(self, profile: FusionProfile, project_id: str | None, stage: str | None, source: str) -> FusionRun:
        run = FusionRun(
            fusion_profile_id=profile.fusion_profile_id,
            project_id=project_id,
            stage=stage,
            source=source,
            status="running",
            strategy="panel_judge_synth",
        )
        self.db.add(run)
        self.db.flush()
        return run

    def _persist_subcall(self, run: FusionRun, sub: RawCallResult, parent_call_id: str):
        """Write one fusion_run_participant + call_log row (with fusion columns) + Trace."""
        p = FusionRunParticipant(
            fusion_run_id=run.fusion_run_id,
            role=sub.role,
            profile_ref=sub.profile_ref,
            provider_id=sub.provider_id,
            model=sub.model,
            perspective=sub.perspective,
            status=sub.status,
            latency_ms=sub.latency_ms,
            prompt_tokens=sub.prompt_tokens,
            completion_tokens=sub.completion_tokens,
            total_tokens=sub.total_tokens,
            cost=sub.cost,
            trace_ref=sub.trace_ref,
            model_call_id=sub.call_id,
            error_category=sub.error_category,
        )
        self.db.add(p)

        # call_log row with R13-4 fusion columns (best-effort)
        try:
            from app.models.call_log import CallLog
            self.db.add(CallLog(
                model_call_id=sub.call_id,
                provider_id=sub.provider_id,
                profile_id=sub.profile_ref,
                selected_model=sub.model,
                selection_reason=f"fusion:{sub.role}",
                status=sub.status,
                latency_ms=sub.latency_ms,
                error_category=sub.error_category,
                prompt_tokens=sub.prompt_tokens,
                completion_tokens=sub.completion_tokens,
                total_tokens=sub.total_tokens,
                source="fusion",
                fusion_parent_id=parent_call_id or None,
                call_type=sub.role,
                fusion_run_id=run.fusion_run_id,
            ))
        except Exception:
            logger.debug("fusion call_log write failed (non-fatal)")

    def _finalize_run(self, run: FusionRun, result: EngineResult):
        run.status = result.status
        run.strategy = result.strategy
        run.degraded = result.degraded
        run.degrade_reason = result.degrade_reason
        run.completed_at = self._now()
        # cost_sum / latency_sum computed from participants
        parts = self.db.query(FusionRunParticipant).filter(
            FusionRunParticipant.fusion_run_id == run.fusion_run_id).all()
        run.cost_sum = sum((p.cost or 0.0) for p in parts)
        run.latency_sum_ms = sum((p.latency_ms or 0.0) for p in parts)
        run.trace_refs = [p.trace_ref for p in parts if p.trace_ref]
        run.summary = {
            "strategy": result.strategy,
            "status": result.status,
            "degraded": result.degraded,
            "degrade_reason": result.degrade_reason,
            "participant_count": len(parts),
        }
        self.db.commit()

    # ── WP-5.2: Panel fan-out ─────────────────────────────────────────

    async def _run_panel(
        self,
        run: FusionRun,
        profile: FusionProfile,
        messages: list[dict],
        parent_call_id: str,
    ) -> list[RawCallResult]:
        """Concurrent fan-out to all panel participants. Single failure ≠ whole failure."""
        participants: list[dict] = profile.panel_participants or []
        sem = asyncio.Semaphore(MAX_PANEL_MODELS)

        async def _one(p: dict) -> RawCallResult:
            async with sem:
                ref = p.get("profile_ref", "")
                info = self._lookup(ref)
                if not info or not info["api_key"]:
                    return RawCallResult(
                        call_id=f"call_{uuid.uuid4().hex[:12]}", role="panel", profile_ref=ref,
                        provider_id="", model="", perspective=p.get("perspective", ""),
                        status="failed", content="", latency_ms=0,
                        error_category="participant_unavailable",
                        error_message=f"无法解析 participant '{ref}' 或其 Key 不可用",
                    )
                # 匿名化: 不暴露 identity，仅保留 perspective
                perspective = p.get("perspective", "general")
                panel_messages = _build_panel_messages(messages, perspective)
                sub = await _raw_call(
                    provider_id=info["provider"].provider_id,
                    model=info["litellm_model"],
                    messages=panel_messages,
                    api_base=info["api_base"],
                    api_key=info["api_key"],
                    temperature=p.get("temperature", 0.7),
                    max_tokens=p.get("max_tokens", 4096),
                    timeout=profile.timeout_seconds,
                    role="panel",
                    perspective=perspective,
                    call_type="fusion_panel",
                    parent_call_id=parent_call_id,
                    adapter=self.adapter,
                )
                sub.trace_ref = self._trace(
                    f"fusion_panel:{perspective}",
                    summary=f"{info['provider'].provider_id}/{info['litellm_model']} → {sub.status}",
                    project_id=run.project_id,
                )
                self._persist_subcall(run, sub, parent_call_id)
                return sub

        tasks = [_one(p) for p in participants[:MAX_PANEL_MODELS]]
        return list(await asyncio.gather(*tasks))

    # ── WP-5.3: Judge ─────────────────────────────────────────────────

    async def _run_judge(
        self,
        run: FusionRun,
        profile: FusionProfile,
        panel_outputs: list[RawCallResult],
        parent_call_id: str,
    ) -> tuple[RawCallResult, dict]:
        """Judge: temp=0, 5-field JSON. Returns (sub_call, parsed_judge_result)."""
        ref = (profile.judge or {}).get("profile_ref", "")
        info = self._lookup(ref)
        if not info or not info["api_key"]:
            empty = RawCallResult(
                call_id=f"call_{uuid.uuid4().hex[:12]}", role="judge", profile_ref=ref,
                provider_id="", model="", perspective="", status="failed",
                content="", latency_ms=0, error_category="judge_unavailable",
                error_message=f"无法解析 judge '{ref}' 或其 Key 不可用",
            )
            return empty, {}
        judge_messages = _build_judge_messages(panel_outputs, profile.judge or {})
        sub = await _raw_call(
            provider_id=info["provider"].provider_id,
            model=info["litellm_model"],
            messages=judge_messages,
            api_base=info["api_base"],
            api_key=info["api_key"],
            temperature=0.0,
            max_tokens=2048,
            timeout=profile.timeout_seconds,
            role="judge",
            call_type="fusion_judge",
            parent_call_id=parent_call_id,
            adapter=self.adapter,
        )
        sub.trace_ref = self._trace(
            "fusion_judge", summary=f"judge → {sub.status}",
            project_id=run.project_id,
        )
        # JSON parse with retry budget
        parsed = _safe_parse_judge_json(sub.content)
        if not parsed and sub.status == "completed":
            # retry once with an explicit JSON instruction
            retry_messages = judge_messages + [
                {"role": "assistant", "content": sub.content},
                {"role": "user", "content": "请严格按照 JSON 格式重新输出，不要包含任何额外文本。"},
            ]
            sub2 = await _raw_call(
                provider_id=info["provider"].provider_id,
                model=info["litellm_model"],
                messages=retry_messages,
                api_base=info["api_base"],
                api_key=info["api_key"],
                temperature=0.0, max_tokens=2048, timeout=profile.timeout_seconds,
                role="judge", call_type="fusion_judge", parent_call_id=parent_call_id,
                adapter=self.adapter,
            )
            sub2.trace_ref = self._trace("fusion_judge:retry", summary=f"retry → {sub2.status}",
                                         project_id=run.project_id)
            self._persist_subcall(run, sub2, parent_call_id)
            parsed = _safe_parse_judge_json(sub2.content) or parsed
            if sub2.status == "completed":
                sub = sub2  # prefer retry result
        else:
            self._persist_subcall(run, sub, parent_call_id)
        return sub, parsed

    # ── WP-5.4: Synthesizer (NO tools) ────────────────────────────────

    async def _run_synthesizer(
        self,
        run: FusionRun,
        profile: FusionProfile,
        messages: list[dict],
        panel_outputs: list[RawCallResult],
        judge_result: dict,
        parent_call_id: str,
    ) -> RawCallResult:
        """Synthesizer: read panel + judge → single content. NO tools (方案 E)."""
        ref = (profile.synthesizer or {}).get("profile_ref", "")
        info = self._lookup(ref)
        if not info or not info["api_key"]:
            return RawCallResult(
                call_id=f"call_{uuid.uuid4().hex[:12]}", role="synthesizer", profile_ref=ref,
                provider_id="", model="", perspective="", status="failed",
                content="", latency_ms=0, error_category="synthesizer_unavailable",
                error_message=f"无法解析 synthesizer '{ref}' 或其 Key 不可用",
            )
        synth_messages = _build_synthesizer_messages(messages, panel_outputs, judge_result,
                                                     profile.synthesizer or {})
        sub = await _raw_call(
            provider_id=info["provider"].provider_id,
            model=info["litellm_model"],
            messages=synth_messages,
            api_base=info["api_base"],
            api_key=info["api_key"],
            temperature=0.5,
            max_tokens=4096,
            timeout=profile.timeout_seconds,
            role="synthesizer",
            call_type="fusion_synthesizer",
            parent_call_id=parent_call_id,
            adapter=self.adapter,
        )
        sub.trace_ref = self._trace(
            "fusion_synthesizer", summary=f"synth → {sub.status}",
            project_id=run.project_id,
        )
        self._persist_subcall(run, sub, parent_call_id)
        return sub

    # ── WP-5.5: Self-MoA ──────────────────────────────────────────────

    async def _run_self_moa(
        self,
        run: FusionRun,
        profile: FusionProfile,
        messages: list[dict],
        parent_call_id: str,
    ) -> tuple[list[RawCallResult], RawCallResult]:
        """Self-MoA: single-model multi-sample (K=3) + light judge."""
        ref = (profile.judge or {}).get("profile_ref") or \
              (profile.synthesizer or {}).get("profile_ref") or \
              (profile.panel_participants or [{}])[0].get("profile_ref", "")
        info = self._lookup(ref)
        if not info or not info["api_key"]:
            empty = RawCallResult(
                call_id=f"call_{uuid.uuid4().hex[:12]}", role="self_moa_sample",
                profile_ref=ref, provider_id="", model="", perspective="",
                status="failed", content="", latency_ms=0,
                error_category="self_moa_unavailable",
                error_message=f"无法解析 Self-MoA 模型 '{ref}' 或其 Key 不可用",
            )
            return [], empty

        samples: list[RawCallResult] = []
        for k in range(DEFAULT_SELF_MOA_SAMPLES):
            sub = await _raw_call(
                provider_id=info["provider"].provider_id,
                model=info["litellm_model"],
                messages=_build_moa_messages(messages, k),
                api_base=info["api_base"],
                api_key=info["api_key"],
                temperature=0.7 + 0.1 * k,  # slight diversity
                max_tokens=4096,
                timeout=profile.timeout_seconds,
                role="self_moa_sample",
                perspective=f"sample_{k+1}",
                call_type="self_moa_sample",
                parent_call_id=parent_call_id,
                adapter=self.adapter,
            )
            sub.trace_ref = self._trace(
                f"fusion_self_moa:sample_{k+1}", summary=f"→ {sub.status}",
                project_id=run.project_id,
            )
            self._persist_subcall(run, sub, parent_call_id)
            samples.append(sub)

        # pick best sample (longest completed content as a simple heuristic; R13-5 v1)
        completed = [s for s in samples if s.status == "completed" and s.content]
        best = max(completed, key=lambda s: len(s.content)) if completed else (
            samples[0] if samples else None
        )
        return samples, best

    # ── Strategy selection ─────────────────────────────────────────────

    def _select_strategy(self, profile: FusionProfile) -> str:
        """Decide panel_judge_synth / self_moa / fallback_single from panel composition."""
        parts = profile.panel_participants or []
        if not parts:
            return "fallback_single"
        providers = set()
        for p in parts:
            ref = p.get("profile_ref", "")
            providers.add(ref.split("/")[0] if "/" in ref else ref)
        if len(parts) < 2:
            return "self_moa"
        if len(providers) < 2:
            return "self_moa" if profile.self_moa_enabled else "panel_judge_synth"
        return "panel_judge_synth"

    # ── Main entry ─────────────────────────────────────────────────────

    async def execute(
        self,
        profile: FusionProfile,
        messages: list[dict],
        *,
        project_id: str | None = None,
        stage: str | None = None,
        source: str = "manual",
    ) -> EngineResult:
        """Run one full Fusion aggregation. Returns single content + metadata."""
        strategy = self._select_strategy(profile)
        run = self._new_run(profile, project_id, stage, source)
        run.strategy = strategy
        parent_call_id = f"call_{uuid.uuid4().hex[:12]}"

        result = EngineResult(
            status="completed", strategy=strategy, content="",
            fusion_run_id=run.fusion_run_id, fusion_profile_id=profile.fusion_profile_id,
        )

        try:
            if strategy == "self_moa":
                result.strategy = "self_moa"
                samples, best = await self._run_self_moa(run, profile, messages, parent_call_id)
                if best and best.status == "completed":
                    result.content = best.content
                    result.degraded = True
                    result.degrade_reason = "self_moa_degradation"
                else:
                    result.status = "failed"
                    result.error_message = "Self-MoA 所有采样均失败"
            else:
                # panel_judge_synth (or fallback_single with 0 participants)
                if not profile.panel_participants:
                    result.status = "failed"
                    result.strategy = "fallback_single"
                    result.degraded = True
                    result.degrade_reason = "no_panel_participants"
                    result.error_message = "无可用 Panel 参与模型"
                    self._finalize_run(run, result)
                    return result

                # Panel
                panel_outputs = await self._run_panel(run, profile, messages, parent_call_id)
                done = [p for p in panel_outputs if p.status == "completed"]
                if not done:
                    result.status = "failed"
                    result.error_message = "所有 Panel 参与模型调用均失败"
                    self._finalize_run(run, result)
                    return result
                if len(done) < len(panel_outputs):
                    result.degraded = True
                    result.degrade_reason = (result.degrade_reason or "") + ";partial_panel"

                # Judge
                judge_sub, judge_result = await self._run_judge(run, profile, panel_outputs, parent_call_id)
                winner_ref = judge_result.get("winner") if judge_result else None
                if judge_sub.status != "completed" or not judge_result:
                    result.degraded = True
                    result.degrade_reason = (result.degrade_reason or "") + ";judge_failed"

                # Synthesizer
                synth_sub = await self._run_synthesizer(
                    run, profile, messages, panel_outputs, judge_result, parent_call_id)
                if synth_sub.status == "completed" and synth_sub.content:
                    result.content = synth_sub.content
                elif winner_ref:
                    # fallback: use winner's raw panel output (match by full or model-suffix ref)
                    winner_out = next((p for p in panel_outputs
                                       if p.profile_ref == winner_ref
                                       or p.profile_ref.endswith("/" + winner_ref.split("/")[-1])
                                       or winner_ref in p.profile_ref), None)
                    if winner_out is None:
                        winner_out = done[0]
                    result.content = winner_out.content
                    result.degraded = True
                    result.degrade_reason = (result.degrade_reason or "") + ";synthesizer_fallback_to_winner"
                else:
                    result.content = done[0].content if done else ""
                    result.degraded = True
                    result.degrade_reason = (result.degrade_reason or "") + ";synthesizer_fallback_to_first"

                result.fusion_metadata = self._assemble_metadata(
                    profile, run, panel_outputs, judge_sub, judge_result, synth_sub,
                    parent_call_id,
                )

        except Exception as e:
            result.status = "failed"
            result.error_message = f"引擎执行异常: {type(e).__name__}"
            logger.exception("FusionExecutionEngine.execute failed")

        self._finalize_run(run, result)
        # Audit on degraded/failed aggregation (high-risk辅助结论落 Audit)
        if result.degraded or result.status == "failed":
            self._audit(
                f"fusion_run:{result.status}",
                reason=result.degrade_reason or result.error_message,
                risk_level="L2",
                project_id=project_id,
            )
        return result

    # ── WP-5.6: fusion_metadata 组装 ─────────────────────────────────

    def _assemble_metadata(
        self,
        profile: FusionProfile,
        run: FusionRun,
        panel_outputs: list[RawCallResult],
        judge_sub: RawCallResult,
        judge_result: dict,
        synth_sub: RawCallResult,
        parent_call_id: str,
    ) -> dict:
        """Build the runtime fusion_metadata (R13-3 §12.2). 单一事实源：本函数。"""
        winner_ref = judge_result.get("winner") if judge_result else None
        confidence = judge_result.get("confidence", "medium") if judge_result else "medium"
        return {
            "fusion_profile_id": profile.fusion_profile_id,
            "fusion_run_id": run.fusion_run_id,
            "strategy": run.strategy,
            "participants": [p.profile_ref for p in panel_outputs],
            "panel_outputs": [
                {
                    "role": p.role, "provider_id": p.provider_id, "model": p.model,
                    "perspective": p.perspective, "content": p.content,
                    "status": p.status, "latency_ms": p.latency_ms,
                    "trace_ref": p.trace_ref, "model_call_id": p.call_id,
                }
                for p in panel_outputs
            ],
            "judge_result": {
                "scores": judge_result.get("scores", {}),
                "winner": winner_ref or "",
                "confidence": confidence,
                "consensus": judge_result.get("consensus", []),
                "contradictions": judge_result.get("contradictions", []),
                "partial_coverage": judge_result.get("partial_coverage", []),
                "unique_insights": judge_result.get("unique_insights", []),
                "blind_spots": judge_result.get("blind_spots", []),
            },
            "synthesizer_result": {
                "content": synth_sub.content,
                "template": (profile.synthesizer or {}).get("output_template", "default"),
            },
            "winner": winner_ref or (panel_outputs[0].profile_ref if panel_outputs else ""),
            "confidence": confidence,
            "cost_sum": run.cost_sum,
            "latency_sum_ms": run.latency_sum_ms,
            "degraded": run.degraded,
            "degrade_reason": run.degrade_reason,
            "trace_refs": [p.trace_ref for p in panel_outputs + [judge_sub, synth_sub] if p.trace_ref],
            "audit_refs": [],
            "source_status": "real",
        }

    @staticmethod
    def _now():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)


# ── Message builder helpers (prompt engineering) ───────────────────────

PANEL_SYSTEM = """你是一位资深信创迁移专家。请从 {perspective} 角度，对下面的用户请求/材料进行分析。
给出结构化、可操作的分析意见（不超过 800 字）。用中文回答。"""

JUDGE_SYSTEM = """你是一位客观的评审官。下面是一段用户请求和多个模型的分析意见。
请比较所有意见，按以下 5 个维度输出严格的 JSON（不要包含 Markdown 代码围栏、不要任何额外文本）：

- scores: {{模型标识: 分数(0-10)}}  — 每个参与模型的综合评分
- winner: "胜出模型标识(与 panel 条目的 provider/model 一致)"
- confidence: "high|medium|low"
- consensus: [达成一致的观点列表]
- contradictions: [互相矛盾的观点列表]
- partial_coverage: [仅部分模型覆盖到的盲区]
- unique_insights: [单一模型独有的洞察]
- blind_spots: [所有模型均未覆盖的盲区]

只输出 JSON。"""

SYNTHESIZER_SYSTEM = """你是一位综合分析专家。下面是用户请求、多个模型的评审意见和评审官的评估。
请综合以上内容，生成一段完整的最终回复（中文，不超过 1200 字）。
要求：吸收共识、标注矛盾点、指出盲区。不要发起任何工具调用或命令，只输出综合文本。"""

MOA_SYSTEM = """你是一位资深信创迁移专家（采样视角 {sample}）。请针对下面的用户请求给出分析（中文，不超过 800 字）。"""


def _build_panel_messages(messages: list[dict], perspective: str) -> list[dict]:
    system = PANEL_SYSTEM.format(perspective=perspective)
    return [{"role": "system", "content": system}] + list(messages)


def _panel_label(p: RawCallResult) -> str:
    return f"[{p.perspective}-{p.provider_id}/{p.model}]"


def _build_judge_messages(panel_outputs: list[RawCallResult], judge_cfg: dict) -> list[dict]:
    header = "以下是各参与模型对同一用户请求的分析意见：\n\n"
    blocks = []
    for i, p in enumerate(panel_outputs, 1):
        blocks.append(f"--- 模型 {i} ({_panel_label(p)}) ---\n{p.content}\n")
    body = header + "\n".join(blocks) + "\n\n请按要求输出 JSON 评审结果。"
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": body},
    ]


def _build_synthesizer_messages(
    messages: list[dict],
    panel_outputs: list[RawCallResult],
    judge_result: dict,
    synth_cfg: dict,
) -> list[dict]:
    parts = ["以下是用户请求、Panel 意见和 Judge 评审结果：\n"]
    for p in panel_outputs:
        parts.append(f"[{_panel_label(p)}]\n{p.content}\n")
    parts.append(f"\n[Judge 评审]\n{json.dumps(judge_result, ensure_ascii=False, indent=2)}\n")
    parts.append("\n请按要求生成综合最终文本。")
    return [
        {"role": "system", "content": SYNTHESIZER_SYSTEM},
        {"role": "user", "content": "".join(parts)},
    ]


def _build_moa_messages(messages: list[dict], sample_idx: int) -> list[dict]:
    return [
        {"role": "system", "content": MOA_SYSTEM.format(sample=sample_idx + 1)},
    ] + list(messages)


def _safe_parse_judge_json(content: str) -> dict:
    """Extract 5-field JSON from possibly Markdown-fenced judge output."""
    if not content:
        return {}
    txt = content.strip()
    # strip ```json ... ``` fences
    if txt.startswith("```"):
        import re
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", txt, re.DOTALL)
        if m:
            txt = m.group(1).strip()
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            return data
    except Exception:
        # try to find a JSON object substring
        import re
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}
