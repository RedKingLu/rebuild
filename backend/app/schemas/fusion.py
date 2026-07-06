"""Fusion domain schemas (R13-4).

Fusion-as-a-virtual-model request/response contracts per R13-3 主方案 §10–§12:
  - panel_participants / judge / synthesizer config shapes (JSON blobs)
  - fusion_metadata (runtime contract, companion to enhanced_evidence 11-element)
  - all responses wrapped by the unified SuccessEnvelope in routes

Hard constraints reflected here:
  - panel_participants MUST have >= 1 entry, none referencing an is_fusion virtual model
  - synthesizer has NO tool config (方案 E)
  - Fusion output never equals Evidence validated (D-066) — surfaced via enhanced_evidence
"""

from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Reusable sub-config shapes ──────────────────────────────────────────

class PanelParticipant(BaseModel):
    """One model participating in the Fusion Panel fan-out."""
    profile_ref: str                              # existing ModelProfile id, e.g. "deepseek-official/deepseek-v4-flash"
    perspective: str = "general"                   # 审议角度: general / architecture / security / performance / maintainability / compliance
    weight: float = 1.0
    temperature: float = 0.7
    max_tokens: int = 4096


class JudgeConfig(BaseModel):
    """Fusion Judge (structured evaluation, temperature=0)."""
    profile_ref: str
    dimensions: list[str] = Field(default_factory=lambda: ["accuracy", "completeness", "risk_awareness"])
    weights: dict[str, float] = Field(default_factory=lambda: {"accuracy": 0.4, "completeness": 0.3, "risk_awareness": 0.3})
    confidence_threshold: float = 0.5
    temperature: float = 0.0                       # 结构化评分强制 temperature=0 (OpenRouter 证据)


class SynthesizerConfig(BaseModel):
    """Fusion Synthesizer (方案 E: NO tool config present).

    extra="forbid": any tool-related key (tool_calling / tool_policy / tools / ...)
    passed by the client is rejected automatically at parse time — this is the
    front-line enforcement of "Fusion 不执行工具" (D-035 / R13-2C accepted).
    """
    model_config = {"extra": "forbid"}
    profile_ref: str
    output_template: str = "default"
    writeback_target: str = "artifact"            # artifact / trace / evidence_candidate
    # NOTE: intentionally NO tool / tool_policy fields — Fusion never executes tools.


class FusionGlobalConfig(BaseModel):
    """Global per-profile config (风格 / 预算 / 超时 / 触发)."""
    style: str = "balanced"                       # 保守(budget) | 平衡 | 激进(frontier) — corresponding to budget/balanced/frontier
    enabled_stages: list[str] = Field(default_factory=list)  # ["p2","p3","p5","p6"] or []
    trigger: str = "manual"                       # manual | auto
    cost_limit: float = 0.0                       # 单次 Fusion 调用成本上限 (0 = 默认)
    timeout_seconds: int = 120
    audit_level: str = "summary"                  # summary | complete
    fallback_single_model: Optional[str] = None
    excluded_providers: list[str] = Field(default_factory=list)
    self_moa_enabled: bool = True                 # 单模型 / 同构 → Self-MoA 标准降级


# ── Validate result ──────────────────────────────────────────────────

class ValidationResult(BaseModel):
    """Response from POST /profiles/{id}/validate — never touches DB."""
    valid: bool
    errors: list[str] = Field(default_factory=list)     # hard violations (block create/update)
    warnings: list[str] = Field(default_factory=list)    # soft (heterogeneity / reachability hints)


# ── Profile CRUD requests ──────────────────────────────────────────────

class FusionProfileCreate(BaseModel):
    name: str
    panel_participants: list[PanelParticipant] = Field(default_factory=list)
    judge: JudgeConfig | None = None
    synthesizer: SynthesizerConfig | None = None
    global_config: FusionGlobalConfig = Field(default_factory=FusionGlobalConfig)


class FusionProfileUpdate(BaseModel):
    name: Optional[str] = None
    panel_participants: Optional[list[PanelParticipant]] = None
    judge: Optional[JudgeConfig] = None
    synthesizer: Optional[SynthesizerConfig] = None
    global_config: Optional[FusionGlobalConfig] = None


class FusionProfileResponse(BaseModel):
    """Read model — wraps FusionProfile row for API responses."""
    fusion_profile_id: str
    virtual_profile_ref: str
    name: str
    enabled: bool
    panel_participants: list[dict]
    judge: dict
    synthesizer: dict
    style: str
    enabled_stages: list[str]
    trigger: str
    cost_limit: float
    timeout_seconds: int
    audit_level: str
    fallback_single_model: Optional[str] = None
    excluded_providers: list[str]
    self_moa_enabled: bool
    max_fusion_depth: int
    created_by: str
    created_at: str = ""
    updated_at: Optional[str] = None
    source_status: str = "real"


class FusionProfileListData(BaseModel):
    profiles: list[FusionProfileResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


# ── Trigger request ─────────────────────────────────────────────────

class FusionTriggerRequest(BaseModel):
    """POST /profiles/{id}/trigger — manual Fusion run (R13-5 live execution)."""
    project_id: Optional[str] = None
    stage: Optional[str] = None
    message: Optional[str] = None          # user prompt; defaults to a validation prompt


# ── Run / participant response ────────────────────────────────────────

class FusionRunParticipantResponse(BaseModel):
    id: str
    fusion_run_id: str
    role: str
    profile_ref: str
    provider_id: str
    model: str
    perspective: str
    status: str
    latency_ms: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    trace_ref: str
    model_call_id: Optional[str] = None
    error_category: str = ""


class FusionRunResponse(BaseModel):
    fusion_run_id: str
    fusion_profile_id: str
    project_id: Optional[str] = None
    stage: Optional[str] = None
    source: str
    status: str
    strategy: str
    winner_ref: Optional[str] = None
    confidence: str
    degraded: bool
    degrade_reason: Optional[str] = None
    cost_sum: float
    latency_sum_ms: float
    trace_refs: list[str]
    audit_refs: list[str]
    summary: Optional[dict] = None
    participants: list[FusionRunParticipantResponse] = Field(default_factory=list)
    created_at: str = ""
    completed_at: Optional[str] = None
    source_status: str = "real"


class FusionRunListData(BaseModel):
    runs: list[FusionRunResponse] = Field(default_factory=list)
    total: int = 0
