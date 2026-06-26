"""Resource Registry model — unified resource entry for 12 types."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, JSON, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class ResourceType(str, enum.Enum):
    agent = "agent"
    skill = "skill"
    tool = "tool"
    hook = "hook"
    case = "case"
    knowledge = "knowledge"
    template = "template"
    mcp = "mcp"
    expert_agent = "expert_agent"
    policy = "policy"
    deterministic_transformer = "deterministic_transformer"
    execution_provider = "execution_provider"


class SourceType(str, enum.Enum):
    internal_current = "internal_current"
    internal_archive = "internal_archive"
    user_provided = "user_provided"
    community = "community"
    external_online = "external_online"
    third_party = "third_party"
    generated = "generated"


class TrustLevel(str, enum.Enum):
    trusted_current = "trusted_current"
    reviewed_reference = "reviewed_reference"
    read_only_reference = "read_only_reference"
    unreviewed = "unreviewed"
    blocked = "blocked"
    unknown = "unknown"


class RiskLevel(str, enum.Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"


class ResourceStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    read_only = "read_only"
    planned = "planned"
    local_existing = "local_existing"
    externally_available = "externally_available"
    not_connected = "not_connected"
    deprecated = "deprecated"
    superseded = "superseded"
    disabled = "disabled"
    blocked = "blocked"
    archived = "archived"


class ResourceEntry(Base):
    __tablename__ = "resource_entry"

    resource_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    resource_type: Mapped[ResourceType] = mapped_column(SAEnum(ResourceType), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(50), default="1.0.0")

    # Source
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType), default=SourceType.internal_current)
    source_trust_level: Mapped[TrustLevel] = mapped_column(SAEnum(TrustLevel), default=TrustLevel.trusted_current)
    source_path_or_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Risk & Permission
    risk_level: Mapped[RiskLevel] = mapped_column(SAEnum(RiskLevel), default=RiskLevel.L0)
    status: Mapped[ResourceStatus] = mapped_column(SAEnum(ResourceStatus), default=ResourceStatus.draft)
    permission_scope: Mapped[str] = mapped_column(String(100), default="read_only")

    # Capabilities & Contracts
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    allowed_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    blocked_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    input_contract: Mapped[str] = mapped_column(Text, default="")
    output_contract: Mapped[str] = mapped_column(Text, default="")
    artifact_evidence_contract: Mapped[str] = mapped_column(Text, default="")

    # Policies
    gate_policy: Mapped[str] = mapped_column(Text, default="")
    trace_policy: Mapped[str] = mapped_column(String(50), default="required")
    audit_policy: Mapped[str] = mapped_column(String(50), default="conditional")

    # Credential (reference only, no value)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Version chain
    supersedes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Review
    review_status: Mapped[str] = mapped_column(String(50), default="not_reviewed")
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, default="")

    # Type-specific metadata (JSON)
    type_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Source markers
    source_status: Mapped[str] = mapped_column(String(50), default="real")
    capability_status: Mapped[str] = mapped_column(String(50), default="active")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
