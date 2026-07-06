"""FusionProfile ORM models (R13-4).

Three independent tables backing Fusion-as-a-virtual-model (方案 E, D-035):
  - fusion_profile   : one Fusion virtual-model = one profile config
  - fusion_run       : one triggered Fusion run (manual or per-stage)
  - fusion_run_participant : one row per sub-call (panel/judge/synthesizer/self_moa_sample)

Designed per R13-3 全量方案 §10 + 文档/04-模型与资源/02-Fusion作为模型能力.md §2
(11-element enhanced_evidence contract) + 01-ModelGateway §8 (is_fusion dispatch).

Hard constraints:
  - panel_participants MUST NOT contain an is_fusion=true profile (anti-recursion)
  - synthesizer has NO tool config (方案 E: Fusion never executes tools)
  - max_fusion_depth = 1 always (forced)
  - enabled defaults to False; trigger defaults to "manual"
  - degraded=true REQUIRES degrade_reason (non-null)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, JSON, Text, Integer, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FusionProfile(Base):
    """One Fusion virtual model profile. R13-3 §10.1.

    panel_participants / judge / synthesizer are JSON blobs (the full shapes are
    documented in the R13-3 主方案; see fusion_profile_service.validate() for the
    exact field-level contracts). Keeping them as JSON (not normalized) follows
    the same precedent as task_graph.edges — they have no independent runtime
    identity and are always accessed via their parent profile.
    """

    __tablename__ = "fusion_profile"

    fusion_profile_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: _new_id("fp")
    )
    # Stable virtual-model ref used by resolve_model() dispatch (R13-5/6) and the
    # anti-recursion validator (R13-4). Format: "fusion/<fusion_profile_id>".
    # HARD CONSTRAINT: no panel_participant.profile_ref may equal any live
    # fusion_profile's virtual_profile_ref (prevents Fusion-of-Fusion recursion).
    virtual_profile_ref: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # HARD CONSTRAINT: default OFF — Fusion must never be on by default (D-035 / R13-2C)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # panel_participants: [{profile_ref, perspective, weight, temperature, max_tokens}]
    panel_participants: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # judge: {profile_ref, dimensions[], weights{}, confidence_threshold, temperature=0}
    judge: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # synthesizer: {profile_ref, output_template, writeback_target} — NO tool config (方案 E)
    synthesizer: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # visual/preset style: 保守(balance/budget) / 平衡 / 激进(frontier)
    style: Mapped[str] = mapped_column(String(16, collation="NOCASE"), default="balanced")
    enabled_stages: Mapped[list] = mapped_column(JSON, default=list)  # ["p2","p3","p5","p6"] or []
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual | auto
    cost_limit: Mapped[float] = mapped_column(Float, default=0.0)       # 0 = inherit default
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120)
    audit_level: Mapped[str] = mapped_column(String(16), default="summary")  # summary | complete
    fallback_single_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    excluded_providers: Mapped[list] = mapped_column(JSON, default=list)
    self_moa_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # HARD CONSTRAINT: always 1 (anti-recursion); forced in code, default here is documentation
    max_fusion_depth: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FusionRun(Base):
    """One Fusion trigger/execution record. R13-3 §10.2.

    status: completed | degraded | failed
    strategy: panel_judge_synth | self_moa | fallback_single
    """
    __tablename__ = "fusion_run"

    fusion_run_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: _new_id("fr")
    )
    fusion_profile_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual | stage_auto | api
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    strategy: Mapped[str] = mapped_column(String(24), default="panel_judge_synth")
    winner_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[str] = mapped_column(String(8), default="medium")
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    # HARD CONSTRAINT: degraded=true -> degrade_reason MUST be non-null/empty
    degrade_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost_sum: Mapped[float] = mapped_column(Float, default=0.0)
    latency_sum_ms: Mapped[float] = mapped_column(Float, default=0.0)
    trace_refs: Mapped[list] = mapped_column(JSON, default=list)
    audit_refs: Mapped[list] = mapped_column(JSON, default=list)
    # Optional structured result snapshot (subset of fusion_metadata) for fast list views
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FusionRunParticipant(Base):
    """One sub-call row per Fusion run. R13-3 §10.3.

    role: panel | judge | synthesizer | self_moa_sample
    HARD CONSTRAINT: trace_ref MUST be non-null/empty for every row.
    """
    __tablename__ = "fusion_run_participant"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: _new_id("fpn")
    )
    fusion_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    profile_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    perspective: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    # HARD CONSTRAINT: non-null in practice
    trace_ref: Mapped[str] = mapped_column(String(64), default="")
    model_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Artifact ref (optional)
    error_category: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
