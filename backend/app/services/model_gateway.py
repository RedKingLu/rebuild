"""ModelGateway — unified model call entry point.

R5: Thin abstraction over LiteLLMAdapter + ProviderRegistry.
All model calls must go through this service — Agent/Skill must not call Provider directly.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.adapters.litellm_adapter import LiteLLMAdapter, ModelCallResult
from app.providers.provider_registry import (
    ProviderRegistry,
    ProviderInfo,
    ModelProfileInfo,
    StrategyInfo,
    get_provider_registry,
    normalize_model_name,
    resolve_api_model_name,
    _resolve_api_key,
)
from app.core.audit_writer import AuditWriter

logger = logging.getLogger("rebuild.model_gateway")


# R17.3-6 WP-6 (Q-R17.3-6-2): 模型全失败强制中断时，前端可向用户呈现的可采取操作。
# 结构化（action + 中文 label + 前端跳转 target），供 GatePanel / StagePage 显式报错渲染。
MODEL_UNAVAILABLE_USER_ACTIONS: list[dict] = [
    {"action": "configure", "label": "配置模型 / API Key", "target": "models"},
    {"action": "switch", "label": "切换模型策略（默认 / 回退链）", "target": "models"},
    {"action": "retry", "label": "重新执行本阶段", "target": "re_execute"},
]


def _attempt_entry(profile, provider, model: str, outcome: str, *,
                   error_category: str = "", error_message: str = "",
                   is_fallback: bool = False) -> dict:
    """构造「已尝试模型链路」的一条记录（Q-R17.3-6-2 前端显式报错所需）。

    仅含非敏感标识（profile_id/provider_id/model 名）与已脱敏的错误分类/消息
    （error_message 由 LiteLLMAdapter._classify_litellm_error 预脱敏，不含 Key）。
    outcome ∈ completed / failed / credential_missing / not_configured / skipped。
    """
    return {
        "profile_id": getattr(profile, "profile_id", "") if profile else "",
        "provider_id": getattr(provider, "provider_id", "") if provider else "",
        "model": model or "",
        "is_fallback": is_fallback,
        "outcome": outcome,
        "error_category": error_category,
        "error_message": error_message,
    }


def _effective_api_format(profile, provider) -> str:
    """B-R20-NO-RESPONSES-CHANNEL: profile 级 api_format_override（若设置）优先于
    provider 的默认 api_format。使同一 provider 能对不同 model profile 混用不同调用通道
    （如 maas-icompify 大多数 profile 走 openai chat completions，新增一个 profile 走
    Responses），不需要整 provider 切换、不影响既有 fallback 链行为。"""
    return getattr(profile, "api_format_override", "") or provider.api_format


def _select_api_base(provider, api_format: str) -> str:
    """按 api_format 选 endpoint（openai / anthropic / responses 三态）。非封闭枚举——
    新增一种 api_format 取值时只需再加一个 elif 分支，无需改动调用方或 schema。"""
    if api_format == "responses" and provider.endpoint_responses:
        return provider.endpoint_responses
    if api_format == "anthropic" and provider.endpoint_anthropic:
        return provider.endpoint_anthropic
    return provider.endpoint_openai or provider.endpoint_anthropic


@dataclass
class ModelGatewayStatus:
    """Aggregate status for GET /api/model/status."""
    total_providers: int = 0
    configured_providers: int = 0
    reachable_providers: int = 0
    total_profiles: int = 0
    configured_profiles: int = 0
    default_profile: str = ""
    overall_status: str = "not_configured"  # not_configured / degraded / available / no_providers


class ModelGateway:
    """Unified model gateway service."""

    def __init__(self, registry: Optional[ProviderRegistry] = None):
        self._registry = registry or get_provider_registry()
        self._adapter = LiteLLMAdapter()
        self._calls: list[dict] = []  # in-memory call log (volatile)
        self._fusion_service = None  # lazy R13-6

    # ── R13-6: Fusion dispatch helpers ─────────────────────────────────

    @property
    def fusion_service(self):
        """Lazy FusionProfileService (R13-4 config service). Re-created per call to
        get a fresh DB session — call() may be invoked from long-lived singletons."""
        if self._fusion_service is None:
            from app.core.database import get_session
            from app.services.fusion_profile_service import FusionProfileService
            self._fusion_service = FusionProfileService(get_session())
        return self._fusion_service

    def _load_fusion_profile(self, virtual_ref: str) -> Optional[object]:
        """Look up a FusionProfile DB row by its virtual_profile_ref. Returns None
        if the ref is not a registered Fusion virtual model (→ ordinary model path)."""
        from app.models.fusion_profile import FusionProfile
        try:
            svc = self.fusion_service
            row = svc.db.query(FusionProfile).filter(
                FusionProfile.virtual_profile_ref == virtual_ref).first()
            return row
        except Exception:
            return None

    async def _dispatch_fusion(self, fp, messages, *, source, project_id=None, stage=None,
                               strategy_id="system-default"):
        """Run FusionExecutionEngine for a resolved Fusion virtual profile and return
        the result in the SAME shape as an ordinary model call (上层透明)."""
        from app.services.fusion_execution_engine import FusionExecutionEngine
        from app.dependencies import get_services

        services = get_services()
        engine = FusionExecutionEngine(
            db=self.fusion_service.db,
            gateway=self,
            trace_writer=services.trace_writer,
            audit_writer=services.audit_writer,
            adapter=self._adapter,
        )
        msgs = messages
        result = await engine.execute(
            fp, msgs, project_id=project_id, stage=stage, source=source)

        # 普通模型调用格式 — 完全等价；外加 fusion_metadata 供需要溯源的上层使用。
        return {
            "call_id": f"fusion-{result.fusion_run_id}",
            "status": result.status,
            "content": result.content if result.status == "completed" else "",
            "model": fp.virtual_profile_ref,
            "profile_id": fp.virtual_profile_ref,
            "provider_id": "fusion",
            "selection_reason": f"fusion:{result.strategy}",
            "latency_ms": result.fusion_metadata.get("latency_sum_ms", 0),
            "error_category": "" if result.status != "failed" else "fusion_failed",
            "error_message": result.error_message,
            "usage_summary": self._fusion_usage(result),
            "retry_count": 0,
            "fallback_used": result.degraded,
            "fusion_metadata": result.fusion_metadata,
            "fusion_run_id": result.fusion_run_id,
            "fusion_profile_id": result.fusion_profile_id,
        }

    @staticmethod
    def _fusion_usage(result) -> dict:
        md = result.fusion_metadata or {}
        usage = md.get("usage_summary", {})
        if not usage:
            # 从 panel 输出聚合一个估算用量
            total_prompt = sum((p.get("prompt_tokens", 0) for p in md.get("panel_outputs", [])))
            total_completion = sum((p.get("completion_tokens", 0) for p in md.get("panel_outputs", [])))
            usage = {"prompt_tokens": total_prompt, "completion_tokens": total_completion,
                     "total_tokens": total_prompt + total_completion}
        return usage

    def _soft_fusion_profile(self, preferred_ref: Optional[str], strategy_id: str):
        """按 SOFT 优先级链（preferred_ref → strategy 默认）判断"有效候选 ref"是否指向
        一个 Fusion 虚拟模型；是则返回对应 FusionProfile，否则 None。

        这让"选中 Fusion 模型"经 **任一来源**（项目默认 / Agent 默认 / strategy 默认）
        都能像普通模型一样透明分派到 FusionExecutionEngine（AC-U3）。user_override（HARD）
        由调用方单独处理，不经此软链。若更高优先级已命中一个普通已配置模型，则该普通模型
        获胜，不分派 Fusion（返回 None）。"""
        strategy = self._registry.get_strategy(strategy_id)
        chain: list[str] = []
        if preferred_ref:
            chain.append(preferred_ref)
        if strategy and strategy.default_profile_ref:
            chain.append(strategy.default_profile_ref)
        for ref in chain:
            if not isinstance(ref, str) or not ref:
                continue
            if ref.startswith("fusion/"):
                fp = self._load_fusion_profile(ref)
                if fp is not None:
                    return fp
                # fusion 引用但无此 profile → 诚实回落到下一优先级
                continue
            prof = self._registry.get_profile(ref)
            if prof and prof.status == "configured":
                return None  # 普通已配置模型在更高优先级获胜，不分派 Fusion
        return None

    async def _dispatch_fusion_stream(self, fp, messages, *, source, project_id, strategy_id):
        """把一次 Fusion 聚合结果以 token 流式回吐（上层透明，与普通流式等价）。
        Panel 内部并发 fan-out 对上层不可见，仅回吐最终单一 content。"""
        fusion_run_id = f"scall_{uuid.uuid4().hex[:12]}"
        try:
            result = await self._dispatch_fusion(
                fp, messages, source=source, project_id=project_id,
                stage=None, strategy_id=strategy_id)
            if result["status"] == "completed":
                for ch in result.get("content", ""):
                    yield {"type": "token", "content": ch, "call_id": result["call_id"]}
                yield {"type": "done", "usage": result["usage_summary"],
                       "call_id": result["call_id"]}
            else:
                yield {"type": "error",
                       "error_category": result.get("error_category", "fusion_failed"),
                       "error_message": result.get("error_message", "Fusion 聚合失败"),
                       "call_id": result["call_id"]}
        except Exception:
            logger.exception("call_stream fusion dispatch failed")
            yield {"type": "error", "error_category": "fusion_failed",
                   "error_message": "Fusion 聚合异常", "call_id": fusion_run_id}

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    # ── Status ────────────────────────────────────────────────────────

    def get_status(self) -> ModelGatewayStatus:
        providers = self._registry.list_providers()
        profiles = self._registry.list_profiles()
        configured = [p for p in providers if p.credential_status == "configured"]
        default = self._registry.get_default_strategy()

        return ModelGatewayStatus(
            total_providers=len(providers),
            configured_providers=len(configured),
            reachable_providers=0,  # requires self-test to determine
            total_profiles=len(profiles),
            configured_profiles=len([p for p in profiles if p.status == "configured"]),
            default_profile=default.default_profile_ref if default else "",
            overall_status="available" if configured else "not_configured",
        )

    # ── Provider/Profile/Strategy queries ─────────────────────────────

    def list_providers(self) -> list[dict]:
        return [_provider_to_dict(p) for p in self._registry.list_providers()]

    def get_provider(self, provider_id: str) -> Optional[dict]:
        p = self._registry.get_provider(provider_id)
        return _provider_to_dict(p) if p else None

    def list_profiles(self, provider_id: Optional[str] = None) -> list[dict]:
        return [_profile_to_dict(p) for p in self._registry.list_profiles(provider_id)]

    def get_profile(self, profile_id: str) -> Optional[dict]:
        p = self._registry.get_profile(profile_id)
        return _profile_to_dict(p) if p else None

    def list_strategies(self) -> list[dict]:
        return [_strategy_to_dict(s) for s in self._registry.list_strategies()]

    # ── Key resolution (credential_ref → env fallback) ─────────────────

    def _resolve_key(self, provider: ProviderInfo, explicit_provider: bool = False) -> tuple:
        """Resolve API key: try credential_ref (DB encrypted via BYOK), then env fallback.

        Args:
            provider: Provider info from registry (has credential_ref field from R6)
            explicit_provider: True if user explicitly specified this provider/model.
                When True AND credential_ref is missing, do NOT fall back silently
                — return None to force clear error.
        Returns:
            (key_value: str | None, key_source: str)
        """
        # Try credential_ref first (R6 BYOK path)
        if provider.credential_ref:
            try:
                from app.core.database import get_session
                from app.services.credential_service import CredentialService
                db = get_session()
                try:
                    csvc = CredentialService(db)
                    plaintext = csvc.decrypt(provider.credential_ref)
                    if plaintext:
                        logger.info(
                            f"Resolved key via credential_ref for {provider.provider_id}"
                        )
                        return plaintext, "credential_ref"
                finally:
                    db.close()
            except Exception as e:
                logger.warning(
                    f"credential_ref resolution failed for {provider.provider_id}: {e}"
                )
            # If explicit provider has credential_ref but decryption fails, don't fallback
            if explicit_provider:
                return None, "credential_ref_decrypt_failed"

        # Fall back to env var (legacy, clearly marked)
        key_val, key_source = _resolve_api_key(provider.env_key_var, provider.provider_id)
        if key_val:
            return key_val, f"env_fallback:{key_source}"

        # Explicit provider with no key at all → clear error
        if explicit_provider:
            return None, "explicit_provider_no_key"

        return None, "none"

    # ── Model call ────────────────────────────────────────────────────

    def _build_profiles_to_try(self, profile, provider, reason: str,
                               strategy_id: str, user_override: Optional[str]) -> list[tuple]:
        """按策略构造顺位尝试链：主 profile + strategy.fallback_profile_refs（仅非显式
        override 路径，D-098 策略化顺位回退）。每项 (profile, provider, reason, is_fallback)。
        跳过未配置 / provider 缺失的 fallback（诚实：不把不可用项计入可尝试链）。"""
        profiles_to_try: list[tuple] = [(profile, provider, reason, False)]
        if user_override is not None:
            return profiles_to_try  # 显式指定 → 不跨 provider 回退（D-036/D-098）
        strategy = self._registry.get_strategy(strategy_id)
        for fb_ref in (strategy.fallback_profile_refs if strategy else []):
            if fb_ref == profile.profile_id:
                continue
            fb_p = self._registry.get_profile(fb_ref)
            if not fb_p or fb_p.status != "configured":
                continue
            fb_prov = self._registry.get_provider(fb_p.provider_id)
            if fb_prov:
                profiles_to_try.append((fb_p, fb_prov, f"fallback:{fb_ref}", True))
        return profiles_to_try

    def stage_model_readiness(self, *, strategy_id: str = "system-default",
                              user_override: Optional[str] = None,
                              preferred_ref: Optional[str] = None,
                              project_id: Optional[str] = None,
                              require_tool_calling: bool = False) -> dict:
        """阶段模型就绪度预检（Q-R17.3-6-2）：在真正调用前判断「是否存在任一按策略
        可用的模型」以及「阶段所需能力是否可满足」。返回结构化候选链，供阶段服务在
        无模型可用时**诚实中断**并把「已尝试/候选模型链路」透传前端——即便一次网络调用
        都未发生（纯未配置场景），前端仍能显式看到考察过的模型链路。

        available=False → 阶段应彻底中断（禁止规则兜底冒充 LLM，D-097/公理3）。
        探查为「configured + 具备凭据」（非实时网络可达；可达失败由运行期调用驱动回退
        捕获，见 call()/call_stream 的 attempted_chain）。

        批2 (D-110/task B): 传入 project_id → 预检解析【项目模型选择】(global_model_ref via
        preferred_ref) 作为首选，使 P0-P3 预检与真实 call_stream 路径一致地尊重用户模型选择
        （否则预检只看 strategy 默认，会因默认档位凭据缺失而误判 blocked，而项目所选模型其实可用）。
        """
        if preferred_ref is None and user_override is None and project_id:
            preferred_ref = self._project_preferred_ref(project_id, None)
        profile, reason, provider = self._registry.resolve_model(
            user_override=user_override, strategy_id=strategy_id, preferred_ref=preferred_ref)
        candidates: list[dict] = []
        capability_ok = False
        available = False

        if profile is not None and provider is not None:
            chain = self._build_profiles_to_try(profile, provider, reason, strategy_id, user_override)
        else:
            # 无 resolved 主模型：仍从策略默认 + 回退链枚举候选，给前端可见的候选链路。
            chain = []
            strategy = self._registry.get_strategy(strategy_id)
            for ref in ([strategy.default_profile_ref] + list(strategy.fallback_profile_refs)
                        if strategy else []):
                if not ref:
                    continue
                p = self._registry.get_profile(ref)
                prov = self._registry.get_provider(p.provider_id) if p else None
                if p and prov:
                    chain.append((p, prov, f"candidate:{ref}", ref != (strategy.default_profile_ref if strategy else None)))

        for cand_profile, cand_provider, cand_reason, is_fb in chain:
            model_name = resolve_api_model_name(cand_profile, _effective_api_format(cand_profile, cand_provider))
            if cand_profile.status != "configured":
                candidates.append(_attempt_entry(cand_profile, cand_provider, model_name,
                                                  "not_configured", error_category="not_configured",
                                                  error_message="模型未配置", is_fallback=is_fb))
                continue
            key_val, _ = self._resolve_key(cand_provider, False)
            if not key_val:
                candidates.append(_attempt_entry(cand_profile, cand_provider, model_name,
                                                  "credential_missing", error_category="credential_missing",
                                                  error_message=f"{cand_provider.provider_id} 无可用凭据",
                                                  is_fallback=is_fb))
                continue
            cap_ok = (not require_tool_calling) or bool(getattr(cand_profile, "supports_tool_calling", False))
            candidates.append(_attempt_entry(cand_profile, cand_provider, model_name,
                                             "candidate_ready" if cap_ok else "capability_unmet",
                                             error_category="" if cap_ok else "capability_unmet",
                                             error_message="" if cap_ok else
                                             f"所选模型 {model_name} 不支持 tool_calling（工具调用），"
                                             f"本阶段需要模型调用工具才能完成",
                                             is_fallback=is_fb))
            if cap_ok:
                available = True
                capability_ok = True

        if available:
            rsn = ""
        elif require_tool_calling and any(c["outcome"] == "capability_unmet" for c in candidates):
            rsn = ("所选模型不支持 tool_calling（工具调用）：本阶段需模型调用工具才能完成，"
                   "请改选支持工具调用的模型（平台尊重你的模型选择，不会自动降级或替换）")
        else:
            rsn = "无任一已配置且具备有效凭据的模型可用"
        return {"available": available, "capability_ok": capability_ok,
                "reason": rsn, "attempted_chain": candidates,
                "user_actions": list(MODEL_UNAVAILABLE_USER_ACTIONS)}

    def _emit_model_event(self, *, action: str, summary: str, project_id: Optional[str],
                          run_id: Optional[str], stage: Optional[str],
                          attempted_chain: Optional[list] = None,
                          audit: bool = False, risk_level: str = "L2") -> None:
        """回退 / 全失败中断的 Trace（每一步）+ Audit（中断）记录（Q-R17.3-6-2）。
        best-effort：记录失败不影响主调用。链路仅含非敏感标识 + 已脱敏错误消息。"""
        try:
            from app.dependencies import get_services
            services = get_services()
        except Exception:
            return
        try:
            tw = getattr(services, "trace_writer", None)
            if tw is not None:
                tw.write("model_gateway", action=action, summary=summary,
                         project_id=project_id, run_id=run_id, stage=stage)
        except Exception:
            logger.debug("model_gateway trace(%s) 写入失败（advisory）", action, exc_info=True)
        if audit:
            try:
                aw = getattr(services, "audit_writer", None)
                if aw is not None:
                    chain_txt = "; ".join(
                        f"{c['profile_id']}={c['outcome']}({c['error_category']})"
                        for c in (attempted_chain or []))
                    aw.write(audit_type="model_unavailable", action=action,
                             decision="blocked", risk_level=risk_level,
                             project_id=project_id, run_id=run_id, stage=stage,
                             reason=f"{summary}；已尝试模型链路：{chain_txt}")
            except Exception:
                logger.debug("model_gateway audit(%s) 写入失败（advisory）", action, exc_info=True)

    async def call(
        self,
        *,
        messages: list[dict],
        user_override: Optional[str] = None,
        strategy_id: str = "system-default",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
        timeout: Optional[float] = None,
        source: str = "api",  # "api" | "self_test" | "platform_assistant"
        project_id: Optional[str] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> dict:
        """Execute a model call through the gateway.

        R13-6: when user_override starts with "fusion/", the caller has selected a Fusion
        virtual model — dispatch directly to the aggregation engine, bypassing the
        yaml-based registry (Fusion profiles are DB entities, not yaml entries).
        This is the entry point for Agent/Skill/P0-P6 selecting Fusion just like any
        other model (方案 E: 与普通模型没有差别)."""
        t0 = time.monotonic()

        # R13-6: 虚拟模型前缀分派（必须在 resolve_model 之前，因为 registry 不含 DB 虚拟模型）
        if user_override and user_override.startswith("fusion/"):
            fp = self._load_fusion_profile(user_override)
            if fp is not None:
                return await self._dispatch_fusion(fp, messages, source=source)
            return _call_error("fusion_not_found",
                               f"Fusion virtual model '{user_override}' 不存在", "fusion_lookup")

        # R13-8-FIX: 软优先级链（strategy 默认）命中 Fusion → 透明分派（像普通模型；AC-U3）。
        # 批2 (D-110/task B): call() 现在也解析项目模型选择（global_model_ref via preferred_ref），
        # 使非流式路径（如 P3 边提议、测试兜底单调）同样尊重用户模型选择，与 call_stream 对齐。
        preferred_ref = (self._project_preferred_ref(project_id, None)
                         if user_override is None else None)
        if user_override is None:
            soft_fp = self._soft_fusion_profile(preferred_ref, strategy_id)
            if soft_fp is not None:
                return await self._dispatch_fusion(soft_fp, messages, source=source)

        # 1. Resolve model
        profile, reason, provider = self._registry.resolve_model(
            user_override=user_override, strategy_id=strategy_id, preferred_ref=preferred_ref,
        )

        if not profile or not provider:
            # 无任一按策略可用的模型 → 全失败强制中断（Q-R17.3-6-2）。给出候选链路供前端显式报错。
            readiness = self.stage_model_readiness(strategy_id=strategy_id, user_override=user_override,
                                                   preferred_ref=preferred_ref)
            self._emit_model_event(
                action="model_unavailable", summary="无任一按策略可用的模型（未配置 / 无凭据）",
                project_id=project_id, run_id=run_id, stage=stage,
                attempted_chain=readiness["attempted_chain"], audit=True, risk_level="L2")
            return _call_error("model_unavailable", readiness["reason"] or "No configured model available",
                               reason, attempted_chain=readiness["attempted_chain"])

        # R13-6: resolved 结果碰巧是一个 Fusion virtual_ref（防御性分派，罕见路径）
        fusion_fp = self._load_fusion_profile(profile.profile_id)
        if fusion_fp is not None:
            return await self._dispatch_fusion(fusion_fp, messages, source=source)

        # 2. 按策略构造顺位尝试链（主 + fallback），逐个尝试并记录「已尝试模型链路」
        #    （D-098 策略化顺位回退；Q-R17.3-6-2 全失败强制中断 + attempted_chain）。
        profiles_to_try = self._build_profiles_to_try(
            profile, provider, reason, strategy_id, user_override)
        attempted_chain: list[dict] = []
        result: Optional[ModelCallResult] = None
        sel_profile, sel_provider, sel_reason, sel_is_fb = profile, provider, reason, False
        explicit_provider = user_override is not None

        for try_profile, try_provider, try_reason, is_fb in profiles_to_try:
            model_name = resolve_api_model_name(try_profile, try_provider.api_format)
            key_val, key_source = self._resolve_key(try_provider, explicit_provider)
            if not key_val:
                attempted_chain.append(_attempt_entry(
                    try_profile, try_provider, model_name, "credential_missing",
                    error_category="credential_missing",
                    error_message=f"No API key configured for {try_provider.provider_id}",
                    is_fallback=is_fb))
                continue

            api_format = _effective_api_format(try_profile, try_provider)
            api_base = _select_api_base(try_provider, api_format)

            if api_format == "responses":
                r = await self._adapter.complete_via_responses(
                    model=model_name, messages=messages, api_base=api_base, api_key=key_val,
                    max_tokens=max_tokens, temperature=temperature, timeout=timeout,
                )
            else:
                r = await self._adapter.complete(
                    model=model_name, messages=messages, api_base=api_base, api_key=key_val,
                    api_format=api_format, max_tokens=max_tokens, temperature=temperature,
                    stream=stream, timeout=timeout,
                )
            if r.status == "completed":
                attempted_chain.append(_attempt_entry(
                    try_profile, try_provider, model_name, "completed", is_fallback=is_fb))
                result = r
                sel_profile, sel_provider, sel_reason, sel_is_fb = \
                    try_profile, try_provider, (try_reason if is_fb else reason), is_fb
                if is_fb:
                    r.fallback_used = True
                    r.fallback_from = profile.profile_id
                    self._emit_model_event(
                        action="model_fallback",
                        summary=f"主模型 {profile.profile_id} 不可用，已按策略回退至 {try_profile.profile_id}",
                        project_id=project_id, run_id=run_id, stage=stage,
                        attempted_chain=attempted_chain)
                break
            # 失败 → 记录并继续尝试下一个 fallback（除非显式 override）
            attempted_chain.append(_attempt_entry(
                try_profile, try_provider, model_name, "failed",
                error_category=r.error_category, error_message=r.error_message, is_fallback=is_fb))
            result = r
            sel_profile, sel_provider, sel_reason, sel_is_fb = try_profile, try_provider, try_reason, is_fb

        # 全失败强制中断：无任一 profile completed（禁止静默降级 / 假成功，D-097/公理3）。
        if result is None or result.status != "completed":
            if result is not None and result.status == "failed":
                err_cat, err_msg, err_status = (result.error_category or "model_unavailable",
                                                result.error_message or "所有可用模型均调用失败", "failed")
            else:
                err_cat, err_msg, err_status = ("credential_missing",
                                                "无任一已配置且具备有效凭据的模型", "blocked")
            self._emit_model_event(
                action="model_unavailable",
                summary=f"所有可用模型均不可用（{err_cat}），阶段模型调用强制中断",
                project_id=project_id, run_id=run_id, stage=stage,
                attempted_chain=attempted_chain, audit=True, risk_level="L2")
            err = _call_error(err_cat, err_msg, sel_reason, attempted_chain=attempted_chain)
            err["status"] = err_status
            return err

        litellm_model = resolve_api_model_name(sel_profile, sel_provider.api_format)
        profile, provider, reason = sel_profile, sel_provider, sel_reason

        # 6. Record call log — persist to DB (FB-006) + in-memory
        latency = (time.monotonic() - t0) * 1000
        call_record = {
            "model_call_id": result.call_id,
            "provider_id": provider.provider_id,
            "profile_id": profile.profile_id,
            "strategy_id": strategy_id,
            "selected_model": litellm_model,
            "selection_reason": reason,
            "status": result.status,
            "latency_ms": result.latency_ms or round(latency, 1),
            "error_category": result.error_category,
            "retry_count": result.retry_count,
            "fallback_used": result.fallback_used,
            "usage_summary": result.usage_summary,
            "source": source,
            "created_at": _now_iso(),
            "completed_at": _now_iso(),
        }
        # D-111: 落脱敏后的调用内容 + 项目/阶段/运行归因（绝不落明文密钥）。
        _attach_call_content(
            call_record, messages=messages,
            response=(result.content if result.status == "completed" else ""),
            project_id=project_id, run_id=run_id, stage=stage)
        self._calls.append(call_record)
        self._persist_call(call_record)

        return {
            "call_id": result.call_id,
            "status": result.status,
            "content": result.content if result.status == "completed" else "",
            # B-R20-NO-RESPONSES-CHANNEL: Responses 通道结构化 reasoning/thinking 文本，
            # 单独暴露（不混入 content）。非 Responses 通道恒为 ""，现有调用方无需处理。
            "reasoning_content": getattr(result, "reasoning_content", "") or "",
            "model": litellm_model,
            "profile_id": profile.profile_id,
            "provider_id": provider.provider_id,
            "selection_reason": reason,
            "latency_ms": result.latency_ms,
            "error_category": result.error_category,
            "error_message": result.error_message,
            "usage_summary": result.usage_summary,
            "retry_count": result.retry_count,
            "fallback_used": result.fallback_used,
            "call_record": call_record,
            "attempted_chain": attempted_chain,
            "model_unavailable": False,
        }

    async def call_stream(
        self,
        *,
        messages: list[dict],
        user_override: Optional[str] = None,
        strategy_id: str = "system-default",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tools: Optional[list[dict]] = None,
        source: str = "api",
        project_id: Optional[str] = None,
        agent_ref: Optional[str] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        """Stream a model call through the gateway as an async generator.

        Yields {"type": "token"/"tool_calls"/"done"/"error"} frames.
        Fallback policy (T3): if an error frame arrives BEFORE any token is yielded to the
        caller, and user_override is None, try next profile in strategy.fallback_profile_refs.
        Once a token/tool_calls frame has been yielded (committed), errors pass through directly.
        Call log is always written at stream end (公理3: failures must declare).
        """
        t0 = time.monotonic()

        # 1. Resolve primary model
        preferred_ref = self._project_preferred_ref(project_id, agent_ref)

        # R13-6: 虚拟模型前缀流式分派（user_override 显式选中 Fusion，HARD）
        if user_override and user_override.startswith("fusion/"):
            fp = self._load_fusion_profile(user_override)
            if fp is None:
                yield {"type": "error", "error_category": "fusion_not_found",
                       "error_message": f"Fusion 虚拟模型 '{user_override}' 不存在",
                       "call_id": f"scall_{uuid.uuid4().hex[:12]}"}
                return
            async for fr in self._dispatch_fusion_stream(
                    fp, messages, source=source, project_id=project_id, strategy_id=strategy_id):
                yield fr
            return

        # R13-8-FIX: 软优先级链（项目默认 / Agent 默认 / strategy 默认）命中 Fusion → 透明流式分派。
        # 这让 Agent 在 P0-P6 真实流程里把 Fusion 设为默认模型即可像普通模型一样使用（AC-U3）。
        if user_override is None:
            soft_fp = self._soft_fusion_profile(preferred_ref, strategy_id)
            if soft_fp is not None:
                async for fr in self._dispatch_fusion_stream(
                        soft_fp, messages, source=source, project_id=project_id, strategy_id=strategy_id):
                    yield fr
                return

        profile, reason, provider = self._registry.resolve_model(
            user_override=user_override, strategy_id=strategy_id, preferred_ref=preferred_ref,
        )

        if not profile or not provider:
            readiness = self.stage_model_readiness(
                strategy_id=strategy_id, user_override=user_override, preferred_ref=preferred_ref)
            self._emit_model_event(
                action="model_unavailable", summary="无任一按策略可用的模型（流式，未配置 / 无凭据）",
                project_id=project_id, run_id=None, stage=None,
                attempted_chain=readiness["attempted_chain"], audit=True)
            yield {
                "type": "error", "error_category": "model_unavailable",
                "error_message": readiness["reason"] or "No configured model available",
                "attempted_chain": readiness["attempted_chain"], "model_unavailable": True,
            }
            return

        # 2. Build ordered profiles_to_try: primary + strategy fallbacks（D-098 策略化顺位回退）
        profiles_to_try = self._build_profiles_to_try(
            profile, provider, reason, strategy_id, user_override)

        # State for call log
        status = "failed"
        last_error_cat = "not_configured"
        last_error_msg = "No model available"
        usage: dict = {}
        call_id = f"scall_{uuid.uuid4().hex[:12]}"
        fallback_used = False
        fallback_from = ""
        selected_profile = profile
        selected_provider = provider
        selected_reason = reason
        tokens_committed = False
        stream_done = False
        # D-111: 累积完成文本（脱敏后落 call_log；有界，超过上限即停止累积 + 标记截断）。
        resp_buf: list[str] = []
        resp_len = 0
        resp_truncated = False
        # Q-R17.3-6-2: 已尝试模型链路（流式），供全失败中断时前端显式报错。
        attempted_chain: list[dict] = []

        # UX-1 FIX: the call-log write below MUST live in `finally`. Streaming consumers
        # (agent_loop and every SSE endpoint) `break` out of their `async for` on the
        # `done` frame — which triggers GeneratorExit at the suspended `yield frame`
        # above/below and skips any code placed after the loop. Before this fix the
        # post-loop persist never ran for broken-early streams, so ALL workspace
        # (source="api") calls were silently dropped from call_log while non-streaming
        # platform_assistant calls (which persist inline in call()) were the only rows.
        try:
            for try_profile, try_provider, try_reason, is_fb in profiles_to_try:
                _model_name = resolve_api_model_name(try_profile, try_provider.api_format)
                key_val, _ = self._resolve_key(try_provider, user_override is not None)
                if not key_val:
                    last_error_cat = "credential_missing"
                    last_error_msg = f"No API key for {try_provider.provider_id}"
                    attempted_chain.append(_attempt_entry(
                        try_profile, try_provider, _model_name, "credential_missing",
                        error_category="credential_missing", error_message=last_error_msg,
                        is_fallback=is_fb))
                    continue

                api_format = _effective_api_format(try_profile, try_provider)
                api_base = _select_api_base(try_provider, api_format)
                litellm_model = _model_name

                profile_failed_pre_token = False

                stream_source = (
                    self._adapter.stream_via_responses(
                        model=litellm_model, messages=messages, api_base=api_base,
                        api_key=key_val, max_tokens=max_tokens, temperature=temperature,
                        timeout=timeout,
                    ) if api_format == "responses" else
                    self._adapter.stream_complete(
                        model=litellm_model, messages=messages, api_base=api_base, api_key=key_val,
                        max_tokens=max_tokens, temperature=temperature, tools=tools,
                        timeout=timeout,
                    )
                )

                async for frame in stream_source:
                    ftype = frame.get("type")

                    if ftype == "error":
                        if tokens_committed:
                            # Committed to this profile — yield error, stop
                            call_id = frame.get("call_id", call_id)
                            last_error_cat = frame.get("error_category", "stream_error")
                            last_error_msg = frame.get("error_message", "")
                            status = "failed"
                            attempted_chain.append(_attempt_entry(
                                try_profile, try_provider, litellm_model, "failed",
                                error_category=last_error_cat, error_message=last_error_msg,
                                is_fallback=is_fb))
                            yield frame
                            stream_done = True
                            break
                        else:
                            # Pre-token — record, allow fallback
                            last_error_cat = frame.get("error_category", "stream_error")
                            last_error_msg = frame.get("error_message", "")
                            call_id = frame.get("call_id", call_id)
                            profile_failed_pre_token = True
                            attempted_chain.append(_attempt_entry(
                                try_profile, try_provider, litellm_model, "failed",
                                error_category=last_error_cat, error_message=last_error_msg,
                                is_fallback=is_fb))
                            break

                    elif ftype == "done":
                        usage = frame.get("usage", {})
                        call_id = frame.get("call_id", call_id)
                        selected_profile = try_profile
                        selected_provider = try_provider
                        selected_reason = try_reason
                        if is_fb:
                            fallback_used = True
                            fallback_from = profiles_to_try[0][0].profile_id
                        status = "completed"
                        last_error_cat = ""
                        last_error_msg = ""
                        attempted_chain.append(_attempt_entry(
                            try_profile, try_provider, litellm_model, "completed",
                            is_fallback=is_fb))
                        # Surface the resolved model on the done frame so streaming
                        # consumers (stage tool loops / agent_loop) can attribute the
                        # selected model without re-querying (批2: model_used 归因)。
                        frame.setdefault("selected_model", litellm_model)
                        yield frame
                        stream_done = True
                        break

                    else:
                        # token / tool_calls — commit to this profile on first yield
                        if not tokens_committed:
                            tokens_committed = True
                            selected_profile = try_profile
                            selected_provider = try_provider
                            selected_reason = try_reason
                            if is_fb:
                                fallback_used = True
                                fallback_from = profiles_to_try[0][0].profile_id
                                self._emit_model_event(
                                    action="model_fallback",
                                    summary=(f"主模型 {profiles_to_try[0][0].profile_id} 不可用，"
                                             f"已按策略回退至 {try_profile.profile_id}（流式）"),
                                    project_id=project_id, run_id=None, stage=None,
                                    attempted_chain=attempted_chain)
                        # D-111: 累积完成文本（仅 token 文本；tool_calls 不入内容正文）。
                        if frame.get("type") == "token" and resp_len < _CONTENT_CAP:
                            _tok = frame.get("content", "") or ""
                            resp_buf.append(_tok)
                            resp_len += len(_tok)
                            if resp_len >= _CONTENT_CAP:
                                resp_truncated = True
                        yield frame

                if stream_done:
                    break
                if not profile_failed_pre_token:
                    stream_done = True
                    break
                logger.info(
                    "call_stream: profile %s failed pre-token (%s), trying fallback",
                    try_profile.profile_id, last_error_cat,
                )

            # If all profiles exhausted without any commit or done → 全失败强制中断
            if not stream_done and not tokens_committed:
                self._emit_model_event(
                    action="model_unavailable",
                    summary=f"所有可用模型均不可用（{last_error_cat}），流式阶段模型调用强制中断",
                    project_id=project_id, run_id=None, stage=None,
                    attempted_chain=attempted_chain, audit=True)
                yield {
                    "type": "error", "error_category": last_error_cat or "model_unavailable",
                    "error_message": last_error_msg, "call_id": call_id,
                    "attempted_chain": attempted_chain, "model_unavailable": True,
                }
        finally:
            # Write call log (always — 公理3; runs even when the consumer breaks early
            # and GeneratorExit unwinds through the yields above).
            latency = (time.monotonic() - t0) * 1000
            final_model = resolve_api_model_name(selected_profile, selected_provider.api_format)
            call_record = {
                "model_call_id": call_id,
                "provider_id": selected_provider.provider_id,
                "profile_id": selected_profile.profile_id,
                "strategy_id": strategy_id,
                "selected_model": final_model,
                "selection_reason": selected_reason,
                "status": status,
                "latency_ms": round(latency, 1),
                "error_category": last_error_cat,
                "retry_count": 0,
                "fallback_used": fallback_used,
                "usage_summary": usage,
                "source": source,
                "created_at": _now_iso(),
                "completed_at": _now_iso(),
            }
            # D-111: 落脱敏后的调用内容（累积的完成文本）+ 项目/阶段/运行归因。
            _attach_call_content(call_record, messages=messages, response="".join(resp_buf),
                                 project_id=project_id, run_id=run_id, stage=stage)
            if resp_truncated:
                call_record["content_truncated"] = True
            self._calls.append(call_record)
            self._persist_call(call_record)

    def resolve_call_target(self, *, user_override: Optional[str] = None,
                            strategy_id: str = "system-default",
                            project_id: Optional[str] = None,
                            agent_ref: Optional[str] = None) -> Optional[dict]:
        """Single source of truth for 'which model/endpoint/key to use' (D-098).

        Resolves the user's strategy (priority + availability fallback) and returns the
        litellm-ready target. Call sites (agent_loop streaming, etc.) MUST use this
        instead of hardcoding a model/endpoint (B-ORCH-02). Returns None if nothing
        configured (caller surfaces an honest error, never fabricates).

        Priority (D-098/D-036): user_override → project preferred (global_model_ref in
        global mode / agent model in custom mode) → system strategy default → availability
        fallback. The project preference is read dynamically from DB.
        """
        preferred_ref = self._project_preferred_ref(project_id, agent_ref)
        profile, reason, provider = self._registry.resolve_model(
            user_override=user_override, strategy_id=strategy_id, preferred_ref=preferred_ref,
        )
        if not profile or not provider:
            return None
        key_val, key_source = self._resolve_key(provider, user_override is not None)
        if not key_val:
            return None
        api_format = _effective_api_format(profile, provider)
        api_base = _select_api_base(provider, api_format)
        return {
            "model": resolve_api_model_name(profile, api_format),
            "api_base": api_base,
            "api_key": key_val,
            "api_format": api_format,
            "profile_id": profile.profile_id,
            "provider_id": provider.provider_id,
            "selection_reason": reason,
        }

    def _project_preferred_ref(self, project_id: Optional[str],
                               agent_ref: Optional[str]) -> Optional[str]:
        """Dynamically read the project's model strategy preference (D-098)."""
        if not project_id:
            return None
        try:
            from app.core.database import get_session
            from app.models.project import Project
            db = get_session()
            try:
                p = db.get(Project, project_id)
                if p is None:
                    return None
                mode = getattr(p, "model_strategy_mode", "global_unified")
                if mode == "global_unified":
                    return getattr(p, "global_model_ref", None) or None
                if mode == "custom" and agent_ref:
                    from app.models.agent_definition import AgentDefinition
                    a = db.get(AgentDefinition, agent_ref)
                    return getattr(a, "model_policy_ref", None) if a else None
            finally:
                db.close()
        except Exception:
            return None
        return None

    # ── Self-test ─────────────────────────────────────────────────────

    async def self_test(self, provider_id: str, profile_id: Optional[str] = None) -> dict:
        """Test connectivity for a provider or specific profile."""
        provider = self._registry.get_provider(provider_id)
        if not provider:
            return {"status": "error", "error": f"Provider not found: {provider_id}"}

        # Determine which profile to test
        if profile_id:
            profile = self._registry.get_profile(profile_id)
            if not profile:
                return {"status": "error", "error": f"Profile not found: {profile_id}"}
        elif provider.models:
            profile = provider.models[0]
        else:
            return {"status": "not_configured", "error": "No models configured for provider"}

        # Check credential — same path as call() so self-test result = actual call result (T6, P-1.4)
        key_val, key_source = self._resolve_key(provider, explicit_provider=True)
        if not key_val:
            return {
                "status": "not_configured",
                "credential_status": "missing",
                "checked_at": _now_iso(),
            }

        # Determine endpoint
        api_format = _effective_api_format(profile, provider)
        api_base = _select_api_base(provider, api_format)

        litellm_model = resolve_api_model_name(profile, api_format)

        # Execute connectivity test
        result = await self._adapter.test_connectivity(
            model=litellm_model,
            api_base=api_base,
            api_key=key_val,
            api_format=api_format,
        )

        checked_at = _now_iso()
        reachable = result.status == "completed"
        # 记录 self-test 结果到 registry，更新状态与能力标记
        self._registry.mark_self_test_result(
            provider_id, reachable, result.error_category, checked_at,
        )

        return {
            "provider_id": provider_id,
            "profile_id": profile.profile_id,
            "model": litellm_model,
            "status": "reachable" if reachable else result.error_category,
            "latency_ms": result.latency_ms,
            "credential_status": provider.credential_status,
            "error_category": result.error_category,
            "error_message": result.error_message,
            "checked_at": checked_at,
            "call_id": result.call_id,
        }

    # ── Call log (FB-006: DB-persisted, fallback to in-memory) ────────

    def _persist_call(self, record: dict) -> None:
        """Persist a call record to the DB (best-effort, non-blocking)."""
        try:
            from app.core.database import get_session
            from app.models.call_log import CallLog
            from datetime import datetime, timezone
            db = get_session()
            try:
                usage = record.get("usage_summary", {}) or {}
                cl = CallLog(
                    model_call_id=record["model_call_id"],
                    provider_id=record.get("provider_id", ""),
                    profile_id=record.get("profile_id", ""),
                    strategy_id=record.get("strategy_id", "system-default"),
                    selected_model=record.get("selected_model", ""),
                    selection_reason=record.get("selection_reason", ""),
                    status=record.get("status", "unknown"),
                    latency_ms=record.get("latency_ms", 0),
                    error_category=record.get("error_category", ""),
                    retry_count=record.get("retry_count", 0),
                    fallback_used=int(record.get("fallback_used", False)),
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    source=record.get("source", "api"),
                    project_id=record.get("project_id"),
                    stage=record.get("stage"),
                    run_id=record.get("run_id"),
                    request_messages=record.get("request_messages"),
                    response_content=record.get("response_content"),
                    content_truncated=int(bool(record.get("content_truncated", False))),
                )
                db.add(cl)
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            logger.debug("background call-log DB write failed (in-memory log still available): %s", e)

    def list_calls(self, limit: int = 10, offset: int = 0, *,
                   project_id: Optional[str] = None,
                   stage: Optional[str] = None) -> tuple[list[dict], int]:
        """List calls from DB with pagination (FB-M). Returns (records, total_count).

        D-111: 可按 project_id / stage 归因过滤（None = 不过滤）。
        """
        try:
            from app.core.database import get_session
            from app.models.call_log import CallLog
            db = get_session()
            try:
                q = db.query(CallLog)
                if project_id is not None:
                    q = q.filter(CallLog.project_id == project_id)
                if stage is not None:
                    q = q.filter(CallLog.stage == stage)
                total = q.count()
                rows = (
                    q.order_by(CallLog.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                    .all()
                )
                return [r.to_dict() for r in rows], total
            finally:
                db.close()
        except Exception:
            # DB unavailable — fallback to in-memory
            mem = list(reversed(self._calls))
            if project_id is not None:
                mem = [c for c in mem if c.get("project_id") == project_id]
            if stage is not None:
                mem = [c for c in mem if c.get("stage") == stage]
            return mem[offset:offset + limit], len(mem)

    def get_call(self, call_id: str) -> Optional[dict]:
        for c in self._calls:
            if c["model_call_id"] == call_id:
                return c
        return None

    # ── 用户导入 / 凭据管理 ────────────────────────────────────────────

    def add_provider(self, config: dict, api_key: Optional[str] = None) -> dict:
        """导入用户供应商（非敏感配置落盘，Key 仅进程内存）。"""
        provider = self._registry.add_user_provider(config, api_key)
        return _provider_to_dict(provider)

    def remove_provider(self, provider_id: str) -> bool:
        return self._registry.remove_user_provider(provider_id)

    def update_provider(self, provider_id: str, **kwargs) -> Optional[dict]:
        """更新供应商非敏感配置（FB-005）。"""
        provider = self._registry.update_provider(provider_id, **kwargs)
        return _provider_to_dict(provider) if provider else None

    def set_credential(self, provider_id: str, api_key: str) -> Optional[dict]:
        """设置供应商凭据：同时注入进程内存 + BYOK 加密持久化到 DB（FB-002 修复）。

        1. 进程内存注入（volatile，保障当前进程立即可用）；
        2. BYOK 持久化：创建/更新 Credential 记录（AES-256-GCM 加密落 DB），
           并设置 provider.credential_ref 指向该记录。
        后续 _resolve_key 优先走 credential_ref 解密路径。
        """
        provider = self._registry.set_credential(provider_id, api_key)
        if not provider:
            return None

        # BYOK 持久化：将 Key 加密存入 Credential 表，设置 credential_ref
        try:
            from app.core.database import get_session
            from app.services.credential_service import CredentialService
            from app.schemas.credential import CredentialCreate
            db = get_session()
            try:
                csvc = CredentialService(db)
                cred_data = CredentialCreate(
                    name=f"provider:{provider_id}",
                    provider_ref=provider_id,
                    plaintext_key=api_key,
                    key_source="user",
                )
                cred = csvc.create(cred_data)
                provider.credential_ref = cred.credential_id
                provider.key_source = "credential_ref"
                logger.info(
                    f"BYOK persisted for {provider_id}: credential_ref={cred.credential_id}"
                )
            finally:
                db.close()
        except Exception as e:
            # BYOK 持久化失败不阻断——Key 仍在进程内存中可用
            logger.warning(f"BYOK persist failed for {provider_id}: {e}")

        return _provider_to_dict(provider)

    def update_strategy(self, strategy_id: str, default_profile_ref: Optional[str] = None,
                        fallback_profile_refs: Optional[list] = None) -> Optional[dict]:
        st = self._registry.update_strategy(strategy_id, default_profile_ref, fallback_profile_refs)
        return _strategy_to_dict(st) if st else None

    def create_strategy(self, strategy_id: str, default_profile_ref: Optional[str] = None,
                        fallback_profile_refs: Optional[list] = None) -> dict:
        """创建新策略（FB-004 新增）。"""
        st = self._registry.create_strategy(strategy_id, default_profile_ref, fallback_profile_refs)
        return _strategy_to_dict(st)

    def delete_strategy(self, strategy_id: str) -> bool:
        """删除策略（FB-007 新增）。"""
        return self._registry.delete_strategy(strategy_id)

    # ── 用量聚合（来自进程内 call log，volatile） ──────────────────────

    def get_usage(self) -> dict:
        """从 DB call_log + 进程内 call log 聚合真实用量（FB-M 修复：DB 持久化统计）。

        token 计数为真实值；缓存命中暂为占位（LiteLLM 未暴露 cache 指标时显示 0）。
        """
        # Merge DB + in-memory records (dedup by model_call_id)
        all_calls: dict[str, dict] = {}
        try:
            from app.core.database import get_session
            from app.models.call_log import CallLog
            db = get_session()
            try:
                rows = db.query(CallLog).all()
                for r in rows:
                    all_calls[r.model_call_id] = r.to_dict()
            finally:
                db.close()
        except Exception as e:
            logger.warning("usage DB read failed, falling back to in-memory: %s", e)
        # In-memory records (may have calls not yet flushed to DB)
        for c in self._calls:
            cid = c.get("model_call_id", "")
            if cid and cid not in all_calls:
                all_calls[cid] = c

        calls = list(all_calls.values())
        total_calls = len(calls)
        completed = [c for c in calls if c.get("status") == "completed"]
        failed = [c for c in calls if c.get("status") not in ("completed", None)]
        prompt = sum(c.get("usage_summary", {}).get("prompt_tokens", 0) for c in calls)
        completion = sum(c.get("usage_summary", {}).get("completion_tokens", 0) for c in calls)
        total_tokens = sum(c.get("usage_summary", {}).get("total_tokens", 0) for c in calls)
        # Cache tokens — LiteLLM may report these; default 0 if not available
        cache_hit_tokens = sum(c.get("usage_summary", {}).get("cache_hit_tokens", 0) for c in calls)
        cache_read_tokens = sum(c.get("usage_summary", {}).get("cache_read_input_tokens", 0) for c in calls)

        # Cache hit rate (0-100, percentage of prompt tokens that were cache hits)
        cache_hit_rate = round((cache_hit_tokens / prompt * 100), 1) if prompt > 0 else 0.0

        by_provider: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        for c in calls:
            pid = c.get("provider_id", "") or "—"
            mid = c.get("selected_model", "") or "—"
            bp = by_provider.setdefault(pid, {"provider_id": pid, "calls": 0, "total_tokens": 0})
            bp["calls"] += 1
            bp["total_tokens"] += c.get("usage_summary", {}).get("total_tokens", 0)
            bm = by_model.setdefault(mid, {"model": mid, "calls": 0, "total_tokens": 0})
            bm["calls"] += 1
            bm["total_tokens"] += c.get("usage_summary", {}).get("total_tokens", 0)

        return {
            "total_calls": total_calls,
            "completed_calls": len(completed),
            "failed_calls": len(failed),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_hit_rate": cache_hit_rate,
            "cost_available": False,
            "cost_unavailable_reason": "无 Provider 计价数据",
            "by_provider": list(by_provider.values()),
            "by_model": list(by_model.values()),
            "persisted": True,
        }


# ── Singleton ────────────────────────────────────────────────────────
_gateway: Optional[ModelGateway] = None


def get_model_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway


# ── Serialization helpers (key-safe) ─────────────────────────────────

def _provider_to_dict(p: ProviderInfo) -> dict:
    return {
        "provider_id": p.provider_id,
        "provider_name": p.provider_name,
        "provider_type": p.provider_type,
        "api_format": p.api_format,
        "endpoint_openai": p.endpoint_openai,
        "endpoint_anthropic": p.endpoint_anthropic,
        "endpoint_responses": p.endpoint_responses,
        "credential_status": p.credential_status,
        "key_source": p.key_source,
        "status": p.status,
        "model_count": len(p.models),
        "origin": p.origin,
        "note": p.note,
        "homepage": p.homepage,
        "last_checked_at": p.last_checked_at,
        "capability_marker": p.capability_marker,
    }


def _profile_to_dict(p: ModelProfileInfo) -> dict:
    return {
        "profile_id": p.profile_id,
        "provider_id": p.provider_id,
        "model_name": p.model_name,
        "display_name": p.display_name,
        "capability_tags": p.capability_tags,
        "cost_tier": p.cost_tier,
        "supports_streaming": p.supports_streaming,
        "supports_tool_calling": p.supports_tool_calling,
        "is_fusion_capable": p.is_fusion_capable,
        "context_window_note": p.context_window_note,
        "recommended_use": p.recommended_use,
        "not_recommended_use": p.not_recommended_use,
        "api_format_override": p.api_format_override,
        "status": p.status,
    }


def _strategy_to_dict(s: StrategyInfo) -> dict:
    return {
        "strategy_id": s.strategy_id,
        "scope": s.scope,
        "default_profile_ref": s.default_profile_ref,
        "fallback_profile_refs": s.fallback_profile_refs,
        "fallback_policy": s.fallback_policy,
        "retry_policy": s.retry_policy,
        "cost_budget_policy": s.cost_budget_policy,
        "fusion_allowed": s.fusion_allowed,
        "streaming_allowed": s.streaming_allowed,
        "tool_calling_allowed": s.tool_calling_allowed,
        "trace_policy": s.trace_policy,
        "audit_policy": s.audit_policy,
    }


def _call_error(error_category: str, message: str, reason: str = "",
                attempted_chain: Optional[list] = None) -> dict:
    return {
        "call_id": "",
        "status": "blocked",
        "content": "",
        "model": "",
        "profile_id": "",
        "provider_id": "",
        "selection_reason": reason,
        "latency_ms": 0,
        "error_category": error_category,
        "error_message": message,
        "usage_summary": {},
        "retry_count": 0,
        "fallback_used": False,
        "call_record": None,
        "attempted_chain": attempted_chain or [],
        "model_unavailable": True,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── D-111: 调用内容落库前的脱敏 + 截断（可观测性；绝不落明文密钥）─────────────────
# 单一事实源：脱敏复用 intake_service.redact_config_text（连接串 + api_key/secret/
# password/token → [REDACTED]，D-032）。内容可能很大 → 逐条截断控体积。
_CONTENT_CAP = 8000          # 单条 messages / 完成文本落库上限（字符）
_MSG_CONTENT_CAP = 2000      # 单条 message.content 截断上限（字符），避免超长单条挤占


def _redact_text(text: str) -> str:
    """脱敏任意文本（D-032）：复用采集层单一事实源，绝不把密钥/连接串值落库。"""
    if not text:
        return text or ""
    try:
        from app.services.intake_service import redact_config_text
        return redact_config_text(text)
    except Exception:  # 公理3: 脱敏工具异常必须发声，但不得因此泄漏原文 → 保守回退为占位
        logger.warning("call-log 内容脱敏失败，保守丢弃原文（不落明文）", exc_info=True)
        return "[REDACTION_FAILED]"


def _redacted_messages(messages: list[dict] | None) -> tuple[str, bool]:
    """把请求 messages 脱敏 + 截断为可落库的 JSON 文本。返回 (json_text, truncated)。"""
    import json as _json
    if not messages:
        return "", False
    truncated = False
    out: list[dict] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", ""))
        raw = m.get("content", "")
        # content 可能是 str 或 OpenAI 多模态 list → 统一转文本再脱敏
        if not isinstance(raw, str):
            raw = _json.dumps(raw, ensure_ascii=False)
        red = _redact_text(raw)
        if len(red) > _MSG_CONTENT_CAP:
            red = red[:_MSG_CONTENT_CAP] + "…[截断]"
            truncated = True
        out.append({"role": role, "content": red})
    text = _json.dumps(out, ensure_ascii=False)
    if len(text) > _CONTENT_CAP:
        text = text[:_CONTENT_CAP] + "…[截断]"
        truncated = True
    return text, truncated


def _redacted_completion(content: str | None) -> tuple[str, bool]:
    """把完成文本脱敏 + 截断为可落库文本。返回 (text, truncated)。"""
    if not content:
        return "", False
    red = _redact_text(content)
    if len(red) > _CONTENT_CAP:
        return red[:_CONTENT_CAP] + "…[截断]", True
    return red, False


def _attach_call_content(record: dict, *, messages: list[dict] | None, response: str | None,
                         project_id: Optional[str], run_id: Optional[str],
                         stage: Optional[str]) -> None:
    """给 call_record 注入脱敏后的内容 + 归因字段（D-111）。就地修改 record。"""
    req_text, req_trunc = _redacted_messages(messages)
    resp_text, resp_trunc = _redacted_completion(response)
    record["request_messages"] = req_text
    record["response_content"] = resp_text
    record["content_truncated"] = bool(req_trunc or resp_trunc)
    record["project_id"] = project_id
    record["run_id"] = run_id
    record["stage"] = stage

