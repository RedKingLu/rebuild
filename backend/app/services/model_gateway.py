"""ModelGateway — unified model call entry point.

R5: Thin abstraction over LiteLLMAdapter + ProviderRegistry.
All model calls must go through this service — Agent/Skill must not call Provider directly.
"""

from __future__ import annotations

import logging
import time
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
    _resolve_api_key,
)

logger = logging.getLogger("rebuild.model_gateway")


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

    # ── Model call ────────────────────────────────────────────────────

    async def call(
        self,
        *,
        messages: list[dict],
        user_override: Optional[str] = None,
        strategy_id: str = "system-default",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
        source: str = "api",  # "api" | "self_test" | "platform_assistant"
    ) -> dict:
        """Execute a model call through the gateway."""
        t0 = time.monotonic()

        # 1. Resolve model
        profile, reason, provider = self._registry.resolve_model(
            user_override=user_override, strategy_id=strategy_id,
        )

        if not profile or not provider:
            return _call_error("not_configured", "No configured model available", reason)

        # 2. Get API key (in-memory only, never persisted)
        key_val, key_source = _resolve_api_key(provider.env_key_var, provider.provider_id)
        if not key_val:
            return _call_error("credential_missing", f"No API key configured for {provider.provider_id}", reason)

        # 3. Determine endpoint based on api_format
        api_format = provider.api_format
        if api_format == "anthropic" and provider.endpoint_anthropic:
            api_base = provider.endpoint_anthropic
        else:
            api_base = provider.endpoint_openai or provider.endpoint_anthropic

        # 4. Normalize model name
        litellm_model = normalize_model_name(profile.model_name, api_format)

        # 5. Execute via adapter
        result = await self._adapter.complete(
            model=litellm_model,
            messages=messages,
            api_base=api_base,
            api_key=key_val,
            api_format=api_format,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
        )

        # 6. Record call log
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
        self._calls.append(call_record)

        return {
            "call_id": result.call_id,
            "status": result.status,
            "content": result.content if result.status == "completed" else "",
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
        }

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

        # Check credential
        key_val, key_source = _resolve_api_key(provider.env_key_var, provider_id)
        if not key_val:
            return {
                "status": "not_configured",
                "credential_status": "missing",
                "checked_at": _now_iso(),
            }

        # Determine endpoint
        api_format = provider.api_format
        if api_format == "anthropic" and provider.endpoint_anthropic:
            api_base = provider.endpoint_anthropic
        else:
            api_base = provider.endpoint_openai or provider.endpoint_anthropic

        litellm_model = normalize_model_name(profile.model_name, api_format)

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

    # ── Call log ──────────────────────────────────────────────────────

    def list_calls(self, limit: int = 50) -> list[dict]:
        return list(reversed(self._calls))[:limit]

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

    def set_credential(self, provider_id: str, api_key: str) -> Optional[dict]:
        provider = self._registry.set_credential(provider_id, api_key)
        return _provider_to_dict(provider) if provider else None

    def update_strategy(self, strategy_id: str, default_profile_ref: Optional[str] = None,
                        fallback_profile_refs: Optional[list] = None) -> Optional[dict]:
        st = self._registry.update_strategy(strategy_id, default_profile_ref, fallback_profile_refs)
        return _strategy_to_dict(st) if st else None

    # ── 用量聚合（来自进程内 call log，volatile） ──────────────────────

    def get_usage(self) -> dict:
        """从进程内 call log 聚合真实用量。

        token 计数为真实值（来自 LiteLLM usage）；成本无计价数据，标记不可用。
        持久化用量库/趋势属 R8+（无真实数据时不臆造，见 06 §9）。
        """
        total_calls = len(self._calls)
        completed = [c for c in self._calls if c.get("status") == "completed"]
        failed = [c for c in self._calls if c.get("status") not in ("completed", None)]
        prompt = sum(c.get("usage_summary", {}).get("prompt_tokens", 0) for c in self._calls)
        completion = sum(c.get("usage_summary", {}).get("completion_tokens", 0) for c in self._calls)
        total_tokens = sum(c.get("usage_summary", {}).get("total_tokens", 0) for c in self._calls)

        by_provider: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        for c in self._calls:
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
            "cost_available": False,
            "cost_unavailable_reason": "无 Provider 计价数据；持久化用量与成本统计属 R8+",
            "by_provider": list(by_provider.values()),
            "by_model": list(by_model.values()),
            "volatile": True,
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


def _call_error(error_category: str, message: str, reason: str = "") -> dict:
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
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
