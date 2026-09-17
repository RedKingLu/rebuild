"""Provider Registry — reads model_profiles.yaml + env, manages Provider/Profile/Strategy.

R5: Config-driven registry. Non-sensitive config in YAML; keys from env only.
Supports OpenAI and Anthropic API formats.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("rebuild.provider_registry")

# ── Env key resolution per provider family ──────────────────────────
# Standard provider env vars are preferred; LLM_API_KEY is generic_fallback only.
FAMILY_KEY_ENV: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "agnes": "AGNES_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "maas": "LLM_API_KEY",
}

# ── API format → litellm model prefix ───────────────────────────────
# B-R20-NO-RESPONSES-CHANNEL: "responses" (Codex/OpenAI Responses API，经
# litellm.aresponses()) 复用 openai-compatible 自定义端点路由，故沿用 "openai/" 前缀。
API_FORMAT_PREFIX: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "responses": "openai",
}

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "model_profiles.yaml"
# 用户自定义供应商落盘文件（仅非敏感配置；Key 绝不写入此处，见 AGENTS.md §12.1）。
# 该文件已纳入 .gitignore，不进入版本控制。
_USER_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "user_providers.yaml"
# 用户对策略的覆盖（默认模型 / fallback 链）。同样 gitignore，仅非敏感。
_USER_STRATEGY_PATH = Path(__file__).resolve().parent.parent / "config" / "user_strategies.yaml"


@dataclass
class ProviderInfo:
    provider_id: str
    provider_name: str
    provider_type: str  # openai / anthropic / openai_compatible
    api_format: str     # openai / anthropic / responses (Provider 级默认；见 ModelProfileInfo.api_format_override)
    endpoint_openai: str = ""
    endpoint_anthropic: str = ""
    # B-R20-NO-RESPONSES-CHANNEL: Codex/OpenAI Responses API 端点（litellm.aresponses()
    # 消费）。约定与 endpoint_openai/endpoint_anthropic 一致：存"base URL"，不带
    # "/responses" 路径后缀 —— litellm 会自动拼接该后缀，若这里存了带后缀的完整路径会变成
    # ".../responses/responses" 触发 404（真实调用实测确认）。
    endpoint_responses: str = ""
    env_key_var: str = ""
    credential_status: str = "not_checked"  # configured / missing / redacted / not_checked
    key_source: str = ""  # env / generic_fallback / none / in_memory
    models: list[ModelProfileInfo] = field(default_factory=list)
    status: str = "not_connected"  # available / not_connected / error / disabled
    origin: str = "seed"  # seed（model_profiles.yaml 内置）/ user（用户导入）
    note: str = ""        # 用户备注（非敏感）
    homepage: str = ""    # 官网链接（可选，非敏感）
    last_checked_at: str = ""  # 最近一次 self-test 时间
    capability_marker: str = "not_checked"  # 见 文档/06-UX与前端/06 §2 的 14 种真实能力标记
    credential_ref: str = ""  # R6 BYOK: Credential.credential_id 引用（非明文 Key）


@dataclass
class ModelProfileInfo:
    profile_id: str  # "{provider_id}/{model_name}"
    provider_id: str
    model_name: str
    api_model_name: str = ""  # 发送时原样使用（不走 normalize 加前缀）；用于不接受 litellm 前缀的自定义端点（如 LongCat）
    # B-R20-NO-RESPONSES-CHANNEL: 单个 model profile 覆盖所属 Provider 的默认 api_format
    # （""=沿用 provider.api_format）。使同一 provider 能对不同 profile 混用不同通道
    # （如 maas-icompify 大多数 profile 走 openai chat completions，同时新增一个 profile
    # 走 responses），不需要整 provider 切换、不影响既有 fallback 链行为。
    api_format_override: str = ""
    display_name: str = ""
    capability_tags: list[str] = field(default_factory=list)
    cost_tier: str = "medium"
    supports_streaming: bool = True
    supports_tool_calling: bool = False
    is_fusion_capable: bool = False
    context_window_note: str = ""
    recommended_use: str = ""
    not_recommended_use: str = ""
    status: str = "not_connected"


@dataclass
class StrategyInfo:
    strategy_id: str
    scope: str  # system
    default_profile_ref: str = ""
    fallback_profile_refs: list[str] = field(default_factory=list)
    fallback_policy: str = "sequential"
    retry_policy: dict = field(default_factory=dict)
    cost_budget_policy: dict = field(default_factory=dict)
    fusion_allowed: bool = False
    streaming_allowed: bool = True
    tool_calling_allowed: bool = True
    trace_policy: str = "always"
    audit_policy: str = "on_error_or_high_risk"


def _resolve_api_key(env_key_var: str, provider_id: str) -> tuple[Optional[str], str]:
    """Resolve API key from env. Returns (key_value, key_source)."""
    # 1. Try explicit provider env var
    if env_key_var:
        val = os.environ.get(env_key_var)
        if val and val != "__USER_TO_FILL__":
            source = "generic_fallback" if env_key_var == "LLM_API_KEY" else "env"
            return val, source

    # 2. Try LLM_API_KEY as generic fallback
    fallback = os.environ.get("LLM_API_KEY")
    if fallback and fallback != "__USER_TO_FILL__":
        return fallback, "generic_fallback"

    return None, "none"


def normalize_model_name(model_name: str, api_format: str) -> str:
    """Ensure model name has litellm provider prefix (openai/ or anthropic/)."""
    if "/" in model_name:
        return model_name
    prefix = API_FORMAT_PREFIX.get(api_format, "openai")
    return f"{prefix}/{model_name}"


def resolve_api_model_name(profile: "ModelProfileInfo", api_format: str) -> str:
    """决定实际发送给 litellm 的模型名：优先用 api_model_name 原样发送，
    否则走 normalize_model_name 自动加 openai/ 前缀。"""
    return profile.api_model_name or normalize_model_name(profile.model_name, api_format)


def _compute_capability_marker(credential_status: str, status: str, error_category: str) -> str:
    """计算真实能力标记（文档/06-UX与前端/06 §2 的子集，R5 实际可判定的状态）。

    前端据此显示颜色+中文文字双通道标签，不由前端臆测。
    """
    if credential_status == "missing":
        return "credential_missing"
    if error_category == "auth_failed":
        return "credential_invalid"
    if status == "available":
        return "real_available"
    if error_category in ("provider_unreachable", "timeout", "rate_limited"):
        return "not_connected"
    if credential_status == "configured":
        return "configured_not_verified"
    return "not_checked"


class ProviderRegistry:
    """In-memory registry built from model_profiles.yaml + env."""

    def __init__(self, config_path: Optional[Path] = None, user_config_path: Optional[Path] = None,
                 user_strategy_path: Optional[Path] = None):
        self._providers: dict[str, ProviderInfo] = {}
        self._profiles: dict[str, ModelProfileInfo] = {}
        self._strategies: dict[str, StrategyInfo] = {}
        self._loaded = False
        self._config_path = config_path or _CONFIG_PATH
        self._user_config_path = user_config_path or _USER_CONFIG_PATH
        self._user_strategy_path = user_strategy_path or _USER_STRATEGY_PATH

    @property
    def loaded(self) -> bool:
        return self._loaded

    def _lookup_db_credential_ref(self, provider_id: str) -> str:
        """R6/BYOK 持久绑定：返回 provider_ref==provider_id 的最新 active DB 凭证 id。

        修复病根：前端"添加 Key"经 set_credential/add_user_provider 只注入进程内存
        （volatile，重启即丢），而 CredentialService 持久化的加密凭证从不被绑到
        provider.credential_ref → 加密凭证成孤儿、解析退回易变的 LLM_API_KEY 兜底 →
        "刚加能用、重启失效"。此处在加载时按 provider_ref 自动绑定持久加密凭证，使
        _resolve_key 优先用可解密、跨重启稳定的 DB Key。
        Best-effort：DB 不可用 / 无匹配 → "" （回退 env 解析，不抛错）。"""
        try:
            from app.core.database import get_session
            from app.models.credential import Credential, CredentialStatus
            db = get_session()
            try:
                cred = (db.query(Credential)
                        .filter(Credential.provider_ref == provider_id,
                                Credential.status == CredentialStatus.active)
                        .order_by(Credential.created_at.desc())
                        .first())
                return cred.credential_id if cred else ""
            finally:
                db.close()
        except Exception:
            logger.debug("DB credential auto-link skipped for %s (advisory)",
                         provider_id, exc_info=True)
            return ""

    def _build_provider(self, p: dict, origin: str) -> ProviderInfo:
        """从原始配置字典构建 ProviderInfo（内置/用户共用）。Key 仅从 env 解析。"""
        env_key_var = p.get("env_key_var", "")
        key_val, key_source = _resolve_api_key(env_key_var, p["provider_id"])

        # R6/BYOK 持久绑定：yaml 未显式给 credential_ref 时，按 provider_ref==provider_id
        # 自动绑定持久化的 DB 加密凭证（修复"前端加 Key 仅进内存、重启即失效"）。
        credential_ref = p.get("credential_ref", "")
        if not credential_ref:
            credential_ref = self._lookup_db_credential_ref(p["provider_id"])

        cred_status = "missing"
        if key_val:
            cred_status = "configured"
        elif key_source == "generic_fallback":
            cred_status = "configured"
        elif credential_ref:
            cred_status = "configured"        # 绑定了持久加密凭证 → 已配置
            key_source = "credential_ref"

        provider = ProviderInfo(
            provider_id=p["provider_id"],
            provider_name=p.get("provider_name", p["provider_id"]),
            provider_type=p.get("provider_type", "openai_compatible"),
            api_format=p.get("api_format", "openai"),
            endpoint_openai=p.get("endpoint_openai", ""),
            endpoint_anthropic=p.get("endpoint_anthropic", ""),
            endpoint_responses=p.get("endpoint_responses", ""),
            env_key_var=env_key_var,
            credential_status=cred_status,
            key_source=key_source if cred_status == "configured" else "none",
            status="not_connected",
            origin=origin,
            note=p.get("note", ""),
            homepage=p.get("homepage", ""),
            credential_ref=credential_ref,
        )
        provider.capability_marker = _compute_capability_marker(cred_status, "not_connected", "")

        for m in p.get("models", []):
            profile_id = f"{p['provider_id']}/{m['model_name']}"
            profile = ModelProfileInfo(
                profile_id=profile_id,
                provider_id=p["provider_id"],
                model_name=m["model_name"],
                api_model_name=m.get("api_model_name", ""),
                api_format_override=m.get("api_format_override", ""),
                display_name=m.get("display_name", m["model_name"]),
                capability_tags=m.get("capability_tags", []),
                cost_tier=m.get("cost_tier", "medium"),
                supports_streaming=m.get("supports_streaming", True),
                supports_tool_calling=m.get("supports_tool_calling", False),
                is_fusion_capable=m.get("is_fusion_capable", False),
                context_window_note=m.get("context_window_note", ""),
                recommended_use=m.get("recommended_use", ""),
                not_recommended_use=m.get("not_recommended_use", ""),
                status=cred_status if cred_status == "configured" else "not_connected",
            )
            provider.models.append(profile)
            self._profiles[profile_id] = profile

        self._providers[p["provider_id"]] = provider
        return provider

    def load(self) -> None:
        """Load config from YAML (seed + user) and resolve env key status."""
        if not self._config_path.exists():
            logger.warning("model_profiles.yaml not found at %s", self._config_path)
            self._loaded = True
            return

        with open(self._config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # ── Load seed providers (model_profiles.yaml) ───────────────
        for p in raw.get("providers", []):
            self._build_provider(p, origin="seed")

        # ── Load user-imported providers (user_providers.yaml, 非敏感) ──
        if self._user_config_path.exists():
            try:
                with open(self._user_config_path, "r", encoding="utf-8") as f:
                    user_raw = yaml.safe_load(f) or {}
                for p in user_raw.get("providers", []):
                    if p.get("provider_id") in self._providers:
                        continue  # 不覆盖内置同名
                    self._build_provider(p, origin="user")
            except Exception as e:  # 用户文件损坏不应阻断启动
                logger.warning("failed to load user_providers.yaml: %s", e)

        # ── Load strategies ─────────────────────────────────────────
        for s in raw.get("strategies", []):
            strategy = StrategyInfo(
                strategy_id=s["strategy_id"],
                scope=s.get("scope", "system"),
                default_profile_ref=s.get("default_profile_ref", ""),
                fallback_profile_refs=s.get("fallback_profile_refs", []),
                fallback_policy=s.get("fallback_policy", "sequential"),
                retry_policy=s.get("retry_policy", {}),
                cost_budget_policy=s.get("cost_budget_policy", {}),
                fusion_allowed=s.get("fusion_allowed", False),
                streaming_allowed=s.get("streaming_allowed", True),
                tool_calling_allowed=s.get("tool_calling_allowed", True),
                trace_policy=s.get("trace_policy", "always"),
                audit_policy=s.get("audit_policy", "on_error_or_high_risk"),
            )
            self._strategies[s["strategy_id"]] = strategy

        # ── 应用用户策略覆盖（仅 default_profile_ref / fallback_profile_refs） ──
        if self._user_strategy_path.exists():
            try:
                with open(self._user_strategy_path, "r", encoding="utf-8") as f:
                    ov = yaml.safe_load(f) or {}
                for sid, fields in (ov.get("overrides", {}) or {}).items():
                    st = self._strategies.get(sid)
                    if not st:
                        continue
                    if "default_profile_ref" in fields:
                        st.default_profile_ref = fields["default_profile_ref"]
                    if "fallback_profile_refs" in fields:
                        st.fallback_profile_refs = list(fields["fallback_profile_refs"])
            except Exception as e:
                logger.warning("failed to load user_strategies.yaml: %s", e)

        self._loaded = True
        # Restore persisted self-test results
        self._load_self_test_state()
        logger.info(
            "ProviderRegistry loaded: %d providers, %d profiles, %d strategies",
            len(self._providers), len(self._profiles), len(self._strategies),
        )

    # ── Query API ────────────────────────────────────────────────────

    def list_providers(self) -> list[ProviderInfo]:
        return list(self._providers.values())

    def get_provider(self, provider_id: str) -> Optional[ProviderInfo]:
        return self._providers.get(provider_id)

    def list_profiles(self, provider_id: Optional[str] = None) -> list[ModelProfileInfo]:
        if provider_id:
            provider = self._providers.get(provider_id)
            return provider.models if provider else []
        return list(self._profiles.values())

    def get_profile(self, profile_id: str) -> Optional[ModelProfileInfo]:
        return self._profiles.get(profile_id)

    def list_strategies(self) -> list[StrategyInfo]:
        return list(self._strategies.values())

    def get_strategy(self, strategy_id: str) -> Optional[StrategyInfo]:
        return self._strategies.get(strategy_id)

    def get_default_strategy(self) -> Optional[StrategyInfo]:
        return self._strategies.get("system-default")

    # ── Model selection resolution ───────────────────────────────────

    def resolve_model(
        self,
        user_override: Optional[str] = None,
        strategy_id: str = "system-default",
        preferred_ref: Optional[str] = None,
    ) -> tuple[Optional[ModelProfileInfo], str, Optional[ProviderInfo]]:
        """Resolve which model to use. Returns (profile, selection_reason, provider).

        When user_override is set (explicit provider), NO cross-provider fallback
        is performed — if the override doesn't match, return (None, reason, None).

        preferred_ref (D-098: project global_model_ref / agent custom model) is a SOFT
        preference — used if configured, otherwise falls through to the strategy default +
        fallback chain (availability-driven, unlike the hard user_override).
        """
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            return None, "no_strategy", None

        # 1. User temporary override (highest priority — NO fallback on mismatch)
        if user_override:
            profile = self._profiles.get(user_override)
            if not profile:
                # Try matching by profile_id suffix (e.g. "deepseek-chat" → "deepseek/deepseek-chat")
                for pid, p in self._profiles.items():
                    if pid.endswith(f"/{user_override}") or pid == user_override:
                        profile = p
                        break
            if profile and profile.status == "configured":
                provider = self._providers.get(profile.provider_id)
                return profile, "user_override", provider
            # Explicit override → NO fallback to other providers
            return None, f"explicit_override_not_found:{user_override}", None

        # 1.5 Preferred ref (project global / agent custom) — SOFT: falls back if unavailable
        if preferred_ref:
            profile = self._profiles.get(preferred_ref)
            if profile and profile.status == "configured":
                provider = self._providers.get(profile.provider_id)
                return profile, "preferred_ref", provider
            # not configured → continue to strategy default + availability fallback

        # 2. Strategy default
        if strategy.default_profile_ref:
            profile = self._profiles.get(strategy.default_profile_ref)
            if profile and profile.status == "configured":
                provider = self._providers.get(profile.provider_id)
                return profile, "strategy_default", provider

        # 3. Fallback chain (only for non-explicit / default-strategy paths)
        for fb_ref in strategy.fallback_profile_refs:
            profile = self._profiles.get(fb_ref)
            if profile and profile.status == "configured":
                provider = self._providers.get(profile.provider_id)
                return profile, f"fallback:{fb_ref}", provider

        # 4. Any configured profile
        for profile in self._profiles.values():
            if profile.status == "configured":
                provider = self._providers.get(profile.provider_id)
                return profile, "first_available", provider

        return None, "no_configured_profile", None

    # ── 用户导入 / 运行时变更 ───────────────────────────────────────

    def add_user_provider(self, config: dict, api_key: Optional[str] = None) -> ProviderInfo:
        """导入用户自定义供应商。

        - 非敏感配置（名称/endpoint/模型/API格式/env变量名等）持久化到 user_providers.yaml；
        - api_key（如提供）仅注入进程内存环境变量（volatile，永不落盘，见 AGENTS.md §12.1）。
        """
        pid = config["provider_id"]
        if pid in self._providers:
            raise ValueError(f"provider 已存在: {pid}")

        # 1. 先注入 Key 到进程内存（不落盘），供 _build_provider 解析 credential_status
        if api_key:
            env_var = config.get("env_key_var") or f"{pid.upper().replace('-', '_')}_API_KEY"
            config["env_key_var"] = env_var
            os.environ[env_var] = api_key  # 仅进程内存，volatile

        provider = self._build_provider(config, origin="user")
        if api_key:
            provider.key_source = "in_memory"

        # 2. 仅持久化非敏感配置
        self._persist_user_providers()
        logger.info("user provider added: %s (key %s)", pid, "in-memory" if api_key else "none")
        return provider

    def remove_user_provider(self, provider_id: str) -> bool:
        """删除用户导入的供应商（仅 origin=user 可删；内置不可删）。"""
        provider = self._providers.get(provider_id)
        if not provider or provider.origin != "user":
            return False
        # 清理 profiles
        for pf in list(self._profiles.keys()):
            if self._profiles[pf].provider_id == provider_id:
                del self._profiles[pf]
        # 清理进程内存 Key（不影响 .env 中的内置变量，因 user 变量为 import 时注入）
        if provider.key_source == "in_memory" and provider.env_key_var in os.environ:
            os.environ.pop(provider.env_key_var, None)
        del self._providers[provider_id]
        self._persist_user_providers()
        return True

    def set_credential(self, provider_id: str, api_key: str) -> Optional[ProviderInfo]:
        """为供应商（重新）设置 Key——仅注入进程内存，永不落盘。"""
        provider = self._providers.get(provider_id)
        if not provider:
            return None
        env_var = provider.env_key_var or f"{provider_id.upper().replace('-', '_')}_API_KEY"
        provider.env_key_var = env_var
        os.environ[env_var] = api_key  # volatile
        provider.credential_status = "configured"
        provider.key_source = "in_memory"
        provider.capability_marker = _compute_capability_marker("configured", provider.status, "")
        for pf in provider.models:
            pf.status = "configured"
        return provider

    def update_provider(self, provider_id: str, **kwargs) -> Optional[ProviderInfo]:
        """更新供应商非敏感配置（FB-005）。仅 user 来源可编辑名称/端点/模型等。"""
        provider = self._providers.get(provider_id)
        if not provider:
            return None

        # 更新简单字段
        for field in ("provider_name", "api_format", "endpoint_openai",
                      "endpoint_anthropic", "endpoint_responses", "env_key_var", "note", "homepage"):
            if field in kwargs and kwargs[field] is not None:
                setattr(provider, field, kwargs[field])

        # 全量替换模型列表
        if "models" in kwargs and kwargs["models"] is not None:
            # 清理旧 profiles
            for old in list(provider.models):
                self._profiles.pop(old.profile_id, None)
            provider.models.clear()
            # 重建新模型
            for m in kwargs["models"]:
                profile_id = f"{provider_id}/{m['model_name']}"
                cred_status = provider.credential_status
                profile = ModelProfileInfo(
                    profile_id=profile_id,
                    provider_id=provider_id,
                    model_name=m["model_name"],
                    api_model_name=m.get("api_model_name", ""),
                    display_name=m.get("display_name", m["model_name"]),
                    capability_tags=m.get("capability_tags", []),
                    cost_tier=m.get("cost_tier", "medium"),
                    supports_streaming=m.get("supports_streaming", True),
                    supports_tool_calling=m.get("supports_tool_calling", False),
                    is_fusion_capable=m.get("is_fusion_capable", False),
                    context_window_note=m.get("context_window_note", ""),
                    recommended_use=m.get("recommended_use", ""),
                    not_recommended_use=m.get("not_recommended_use", ""),
                    status="configured" if cred_status == "configured" else "not_connected",
                )
                provider.models.append(profile)
                self._profiles[profile_id] = profile

        # 仅 user 来源持久化到 user_providers.yaml
        if provider.origin == "user":
            self._persist_user_providers()

        logger.info("provider updated: %s", provider_id)
        return provider

    def mark_self_test_result(self, provider_id: str, reachable: bool,
                              error_category: str, checked_at: str) -> None:
        """记录 self-test 结果，更新 provider 状态与能力标记，并同步旗下所有 model profile 状态。"""
        provider = self._providers.get(provider_id)
        if not provider:
            return
        provider.status = "available" if reachable else "not_connected"
        provider.last_checked_at = checked_at
        provider.capability_marker = _compute_capability_marker(
            provider.credential_status, provider.status, error_category,
        )
        # 同步旗下所有 model profile 的状态（FB-003 修复）
        # 当 provider 连通测试通过（available）时，旗下配置了凭据的 model 也应标记为 configured
        for pf in provider.models:
            if reachable and provider.credential_status == "configured":
                pf.status = "configured"
            elif not reachable:
                pf.status = "not_connected"
        # Persist self-test result for restart survival
        self._persist_self_test_state()

    def _persist_user_providers(self) -> None:
        """仅写入用户供应商的非敏感配置。Key 绝不写入（AGENTS.md §12.1）。"""
        user_providers = []
        for p in self._providers.values():
            if p.origin != "user":
                continue
            user_providers.append({
                "provider_id": p.provider_id,
                "provider_name": p.provider_name,
                "provider_type": p.provider_type,
                "api_format": p.api_format,
                "endpoint_openai": p.endpoint_openai,
                "endpoint_anthropic": p.endpoint_anthropic,
                "endpoint_responses": p.endpoint_responses,
                "env_key_var": p.env_key_var,
                "note": p.note,
                "homepage": p.homepage,
                "models": [
                    {
                        "model_name": m.model_name,
                        "display_name": m.display_name,
                        "capability_tags": m.capability_tags,
                        "cost_tier": m.cost_tier,
                        "supports_streaming": m.supports_streaming,
                        "supports_tool_calling": m.supports_tool_calling,
                        "context_window_note": m.context_window_note,
                        "recommended_use": m.recommended_use,
                    }
                    for m in p.models
                ],
            })
        self._user_config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._user_config_path, "w", encoding="utf-8") as f:
            f.write("# 用户导入的供应商（仅非敏感配置）。API Key 绝不写入此文件——仅进程内存。\n")
            f.write("# 本文件已纳入 .gitignore，不进入版本控制。见 AGENTS.md §12.1。\n")
            yaml.safe_dump({"providers": user_providers}, f, allow_unicode=True, sort_keys=False)

    def update_strategy(self, strategy_id: str, default_profile_ref: Optional[str] = None,
                        fallback_profile_refs: Optional[list] = None) -> Optional[StrategyInfo]:
        """编辑策略的默认模型 / fallback 链（其余字段 R5 不开放编辑）。覆盖落盘 user_strategies.yaml。"""
        st = self._strategies.get(strategy_id)
        if not st:
            return None
        if default_profile_ref is not None:
            st.default_profile_ref = default_profile_ref
        if fallback_profile_refs is not None:
            st.fallback_profile_refs = list(fallback_profile_refs)
        self._persist_user_strategies()
        return st

    def create_strategy(self, strategy_id: str, default_profile_ref: Optional[str] = None,
                        fallback_profile_refs: Optional[list] = None) -> StrategyInfo:
        """创建新策略（FB-004 新增），覆盖落盘 user_strategies.yaml。"""
        st = StrategyInfo(
            strategy_id=strategy_id,
            scope="system",
            default_profile_ref=default_profile_ref or "",
            fallback_profile_refs=list(fallback_profile_refs) if fallback_profile_refs else [],
            fallback_policy="sequential",
            retry_policy={"max_retries": 3, "retry_delay_sec": 2.0, "backoff": "exponential"},
            cost_budget_policy={"enabled": False},
            fusion_allowed=False,
            streaming_allowed=True,
            tool_calling_allowed=True,
            trace_policy="always",
            audit_policy="on_error_or_high_risk",
        )
        self._strategies[strategy_id] = st
        self._persist_user_strategies()
        logger.info("strategy created: %s", strategy_id)
        return st

    def delete_strategy(self, strategy_id: str) -> bool:
        """删除策略（FB-007 新增）。仅 user 来源可删，system-default 不可删。"""
        if strategy_id == "system-default":
            return False
        if strategy_id not in self._strategies:
            return False
        del self._strategies[strategy_id]
        self._persist_user_strategies()
        logger.info("strategy deleted: %s", strategy_id)
        return True

    def _persist_self_test_state(self) -> None:
        """Persist self-test results to JSON, survive restart."""
        import json
        sp = self._config_path.parent / "self_test_state.json"
        state = {}
        for pid, p in self._providers.items():
            if p.last_checked_at:
                state[pid] = {"status": p.status, "capability_marker": p.capability_marker,
                              "last_checked_at": p.last_checked_at, "credential_status": p.credential_status}
        try:
            sp.parent.mkdir(parents=True, exist_ok=True)
            with open(sp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Failed to persist self-test state: %s", e)

    def _load_self_test_state(self) -> None:
        """Restore self-test results from persisted file."""
        import json
        sp = self._config_path.parent / "self_test_state.json"
        if not sp.exists(): return
        try:
            with open(sp, "r", encoding="utf-8") as f:
                state = json.load(f)
            for pid, s in state.items():
                p = self._providers.get(pid)
                if p:
                    p.status = s.get("status", "not_connected")
                    p.capability_marker = s.get("capability_marker", "not_checked")
                    p.last_checked_at = s.get("last_checked_at", "")
                    if s.get("credential_status") == "configured" and p.status == "available":
                        for pf in p.models: pf.status = "configured"
        except Exception as e:
            logger.warning("Failed to load self-test state: %s", e)

    def _persist_user_strategies(self) -> None:
        """落盘用户对策略的覆盖（仅 default/fallback；非敏感）。"""
        overrides = {}
        for sid, st in self._strategies.items():
            overrides[sid] = {
                "default_profile_ref": st.default_profile_ref,
                "fallback_profile_refs": st.fallback_profile_refs,
            }
        self._user_strategy_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._user_strategy_path, "w", encoding="utf-8") as f:
            f.write("# 用户对模型策略的覆盖（仅默认模型 / fallback 链，非敏感）。gitignore。\n")
            yaml.safe_dump({"overrides": overrides}, f, allow_unicode=True, sort_keys=False)
_registry: Optional[ProviderRegistry] = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.load()
    return _registry
