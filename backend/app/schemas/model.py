"""Model domain schemas — R5 ModelGateway full implementation."""

from typing import Optional
from pydantic import BaseModel, Field


# ── Provider ─────────────────────────────────────────────────────────

class ProviderResponse(BaseModel):
    provider_id: str
    provider_name: str
    provider_type: str
    api_format: str
    endpoint_openai: str = ""
    endpoint_anthropic: str = ""
    credential_status: str  # configured / missing / invalid / redacted / not_checked
    key_source: str = ""    # env / generic_fallback / none / in_memory
    status: str             # available / not_connected / error / disabled
    model_count: int = 0
    origin: str = "seed"    # seed（内置）/ user（用户导入）
    note: str = ""
    homepage: str = ""
    last_checked_at: str = ""
    capability_marker: str = "not_checked"  # 14 种真实能力标记之一（06 §2）

    source_status: str = ""
    capability_status: str = ""


# ── 用户导入供应商 ───────────────────────────────────────────────────

class ImportModelInput(BaseModel):
    model_name: str
    display_name: str = ""
    capability_tags: list[str] = Field(default_factory=list)
    cost_tier: str = "medium"
    supports_streaming: bool = True
    supports_tool_calling: bool = False
    context_window_note: str = ""
    recommended_use: str = ""


class CreateProviderRequest(BaseModel):
    """导入用户供应商。api_key 仅注入进程内存（volatile，永不落盘，AGENTS.md §12.1）。"""
    provider_id: str = ""          # 留空则由 provider_name 生成
    provider_name: str
    provider_type: str = "openai_compatible"
    api_format: str = "openai"     # openai / anthropic
    endpoint_openai: str = ""
    endpoint_anthropic: str = ""
    env_key_var: str = ""          # 留空则按 {PROVIDER}_API_KEY 生成
    note: str = ""
    homepage: str = ""
    api_key: Optional[str] = None  # 可选；仅进程内存，绝不持久化
    models: list[ImportModelInput] = Field(default_factory=list)


class SetCredentialRequest(BaseModel):
    """为已有供应商（重新）设置 Key。"""
    api_key: str


class UpdateProviderRequest(BaseModel):
    """更新供应商配置（FB-005）。仅允许更新非敏感配置字段。"""
    provider_name: Optional[str] = None
    api_format: Optional[str] = None       # openai / anthropic
    endpoint_openai: Optional[str] = None
    endpoint_anthropic: Optional[str] = None
    env_key_var: Optional[str] = None
    note: Optional[str] = None
    homepage: Optional[str] = None
    models: Optional[list[ImportModelInput]] = None  # 全量替换模型列表


class UpdateStrategyRequest(BaseModel):
    """编辑/创建策略：默认模型 + fallback 链（R5 仅开放这两项）。
    strategy_id 仅在创建时必填；编辑时忽略（使用路径参数）。"""
    strategy_id: str = ""  # 创建时必填
    default_profile_ref: Optional[str] = None
    fallback_profile_refs: Optional[list[str]] = None


# ── 用量聚合 ─────────────────────────────────────────────────────────

class UsageByProvider(BaseModel):
    provider_id: str
    calls: int = 0
    total_tokens: int = 0


class UsageByModel(BaseModel):
    model: str
    calls: int = 0
    total_tokens: int = 0


class UsageResponse(BaseModel):
    total_calls: int = 0
    completed_calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_read_tokens: int = 0
    cache_hit_rate: float = 0.0
    cost_available: bool = False
    cost_unavailable_reason: str = ""
    by_provider: list[UsageByProvider] = Field(default_factory=list)
    by_model: list[UsageByModel] = Field(default_factory=list)
    persisted: bool = True


class ProviderListData(BaseModel):
    providers: list[ProviderResponse] = Field(default_factory=list)


# ── ModelProfile ─────────────────────────────────────────────────────

class ModelProfileResponse(BaseModel):
    profile_id: str
    provider_id: str
    model_name: str
    display_name: str = ""
    capability_tags: list[str] = Field(default_factory=list)
    cost_tier: str = "medium"
    supports_streaming: bool = True
    supports_tool_calling: bool = False
    is_fusion_capable: bool = False
    context_window_note: str = ""
    recommended_use: str = ""
    not_recommended_use: str = ""
    status: str  # configured / not_connected

    source_status: str = ""
    capability_status: str = ""


class ModelProfileListData(BaseModel):
    profiles: list[ModelProfileResponse] = Field(default_factory=list)


# ── ModelStrategy ────────────────────────────────────────────────────

class RetryPolicyResponse(BaseModel):
    max_retries: int = 3
    retry_delay_sec: float = 2.0
    backoff: str = "exponential"


class CostBudgetPolicyResponse(BaseModel):
    enabled: bool = False


class StrategyResponse(BaseModel):
    strategy_id: str
    scope: str
    default_profile_ref: str = ""
    fallback_profile_refs: list[str] = Field(default_factory=list)
    fallback_policy: str = "sequential"
    retry_policy: RetryPolicyResponse = Field(default_factory=RetryPolicyResponse)
    cost_budget_policy: CostBudgetPolicyResponse = Field(default_factory=CostBudgetPolicyResponse)
    fusion_allowed: bool = False
    streaming_allowed: bool = True
    tool_calling_allowed: bool = True
    trace_policy: str = "always"
    audit_policy: str = "on_error_or_high_risk"


class StrategyListData(BaseModel):
    strategies: list[StrategyResponse] = Field(default_factory=list)


# ── ModelGateway Status ──────────────────────────────────────────────

class ModelStatusResponse(BaseModel):
    total_providers: int = 0
    configured_providers: int = 0
    reachable_providers: int = 0
    total_profiles: int = 0
    configured_profiles: int = 0
    default_profile: str = ""
    overall_status: str = "not_configured"


# ── Self-test ────────────────────────────────────────────────────────

class SelfTestRequest(BaseModel):
    provider_id: str
    profile_id: Optional[str] = None


class SelfTestResponse(BaseModel):
    provider_id: str = ""
    profile_id: str = ""
    model: str = ""
    status: str = "not_configured"  # reachable / not_configured / auth_failed / not_connected / timeout / rate_limited / error
    latency_ms: float = 0.0
    credential_status: str = "missing"
    error_category: str = ""
    error_message: str = ""  # redacted
    checked_at: str = ""
    call_id: str = ""


# ── Model Call ───────────────────────────────────────────────────────

class ModelCallRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    user_override: Optional[str] = None  # profile_id for temporary override
    strategy_id: str = "system-default"
    max_tokens: int = 4096
    temperature: float = 0.7
    stream: bool = False
    source: str = "api"  # api / self_test / platform_assistant


class UsageSummaryResponse(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelCallResponse(BaseModel):
    call_id: str = ""
    status: str = "unknown"
    content: str = ""
    model: str = ""
    profile_id: str = ""
    provider_id: str = ""
    selection_reason: str = ""
    latency_ms: float = 0.0
    error_category: str = ""
    error_message: str = ""
    usage_summary: UsageSummaryResponse = Field(default_factory=UsageSummaryResponse)
    retry_count: int = 0
    fallback_used: bool = False


# ── Call log entry ───────────────────────────────────────────────────

class CallLogEntry(BaseModel):
    model_call_id: str
    provider_id: str = ""
    profile_id: str = ""
    strategy_id: str = ""
    selected_model: str = ""
    selection_reason: str = ""
    status: str = "unknown"
    latency_ms: float = 0.0
    error_category: str = ""
    retry_count: int = 0
    fallback_used: bool = False
    usage_summary: UsageSummaryResponse = Field(default_factory=UsageSummaryResponse)
    source: str = "api"
    created_at: str = ""
    completed_at: str = ""


class CallLogListData(BaseModel):
    calls: list[CallLogEntry] = Field(default_factory=list)
    total: int = 0
    limit: int = 10
    offset: int = 0
    persisted: bool = True
    note: str = "DB persisted. Data survives server restart."


# ── Platform Assistant chat ──────────────────────────────────────────

class AssistantChatRequest(BaseModel):
    message: str
    profile_id: Optional[str] = None  # optional: override default profile


class AssistantChatResponse(BaseModel):
    reply: str = ""
    model: str = ""
    profile_id: str = ""
    provider_id: str = ""
    latency_ms: float = 0.0
    status: str = "unknown"
    error_message: str = ""  # redacted
    source: str = "ModelGateway"


# ── Legacy placeholder (backward compat) ─────────────────────────────

class ModelProviderResponse(BaseModel):
    provider_id: str = ""
    name: str = ""
    api_format: str = "openai"
    source_status: str = "not_connected"
    capability_status: str = "future"


class ModelProfileResponse_Legacy(BaseModel):
    profile_id: str = ""
    provider_id: str = ""
    model: str = ""
    display_name: str = ""
    supports_streaming: bool = True
    source_status: str = "not_connected"
    capability_status: str = "future"


class ModelBindingResponse(BaseModel):
    binding_id: str = ""
    project_id: str = ""
    profile_id: str = ""
    source_status: str = "not_connected"
