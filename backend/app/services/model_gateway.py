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
from app.core.audit_writer import AuditWriter

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

        # 2. Get API key — try credential_ref first, then env fallback
        explicit_provider = user_override is not None
        key_val, key_source = self._resolve_key(provider, explicit_provider)
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
        self._calls.append(call_record)
        self._persist_call(call_record)

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
                )
                db.add(cl)
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        except Exception:
            pass  # best-effort — in-memory log is still available

    def list_calls(self, limit: int = 10, offset: int = 0) -> tuple[list[dict], int]:
        """List calls from DB with pagination (FB-M). Returns (records, total_count)."""
        try:
            from app.core.database import get_session
            from app.models.call_log import CallLog
            db = get_session()
            try:
                total = db.query(CallLog).count()
                rows = (
                    db.query(CallLog)
                    .order_by(CallLog.created_at.desc())
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
        except Exception:
            pass
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
