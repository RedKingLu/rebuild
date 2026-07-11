"""Resource Registry service — unified CRUD + query for 12 resource types."""

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.resource_entry import ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus
from app.schemas.registry import ResourceCreate, ResourceUpdate


class RegistryService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: ResourceCreate) -> ResourceEntry:
        entry = ResourceEntry(
            resource_type=ResourceType(data.resource_type),
            name=data.name,
            description=data.description,
            version=data.version,
            source_type=SourceType(data.source_type),
            source_trust_level=TrustLevel(data.source_trust_level),
            source_path_or_ref=data.source_path_or_ref,
            risk_level=RiskLevel(data.risk_level),
            status=ResourceStatus(data.status),
            permission_scope=data.permission_scope,
            capabilities=data.capabilities,
            allowed_actions=data.allowed_actions,
            blocked_actions=data.blocked_actions,
            input_contract=data.input_contract,
            output_contract=data.output_contract,
            artifact_evidence_contract=data.artifact_evidence_contract,
            gate_policy=data.gate_policy,
            trace_policy=data.trace_policy,
            audit_policy=data.audit_policy,
            credential_ref=data.credential_ref,
            review_status=data.review_status,
            type_metadata=data.type_metadata,
            enabled=data.enabled if data.enabled is not None else True,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get(self, resource_id: str) -> ResourceEntry | None:
        return self.db.get(ResourceEntry, resource_id)

    def list_all(
        self,
        resource_type: str | None = None,
        source_type: str | None = None,
        trust_level: str | None = None,
        risk_level: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> tuple[list[ResourceEntry], int]:
        q = self.db.query(ResourceEntry)
        if not include_deleted:
            # Soft-delete filter (R15-4-C1): 默认只返回未删除资源
            q = q.filter(ResourceEntry.deleted_at.is_(None))
        if resource_type:
            q = q.filter(ResourceEntry.resource_type == ResourceType(resource_type))
        if source_type:
            q = q.filter(ResourceEntry.source_type == SourceType(source_type))
        if trust_level:
            q = q.filter(ResourceEntry.source_trust_level == TrustLevel(trust_level))
        if risk_level:
            q = q.filter(ResourceEntry.risk_level == RiskLevel(risk_level))
        if status:
            q = q.filter(ResourceEntry.status == ResourceStatus(status))
        total = q.count()
        records = q.order_by(ResourceEntry.resource_type, ResourceEntry.name).offset(offset).limit(limit).all()
        return records, total

    def update(self, resource_id: str, data: ResourceUpdate) -> ResourceEntry | None:
        entry = self.get(resource_id)
        if entry is None:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key == "resource_type" and value:
                setattr(entry, key, ResourceType(value))
            elif key == "source_type" and value:
                setattr(entry, key, SourceType(value))
            elif key == "source_trust_level" and value:
                setattr(entry, key, TrustLevel(value))
            elif key == "risk_level" and value:
                setattr(entry, key, RiskLevel(value))
            elif key == "status" and value:
                setattr(entry, key, ResourceStatus(value))
            elif hasattr(entry, key):
                setattr(entry, key, value)
        entry.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def set_enabled(self, resource_id: str, enabled: bool) -> ResourceEntry | None:
        """Enable/disable a resource (R15-4-C1). Returns None if not found or soft-deleted."""
        entry = self.get(resource_id)
        if entry is None or entry.deleted_at is not None:
            return None
        entry.enabled = enabled
        entry.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def delete(self, resource_id: str) -> bool:
        """Soft-delete (R15-4-C1): write deleted_at instead of hard delete.

        Idempotent-ish: an already soft-deleted row is treated as not found.
        """
        entry = self.get(resource_id)
        if entry is None or entry.deleted_at is not None:
            return False
        entry.deleted_at = datetime.now(timezone.utc)
        entry.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    def summary(self) -> dict:
        entries = self.db.query(ResourceEntry).all()
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        enabled_count = 0
        schedulable_count = 0
        _schedulable_statuses = {"active", "read_only", "local_existing"}
        _remote_sources = {"external_online", "third_party"}
        for e in entries:
            by_type[e.resource_type.value] = by_type.get(e.resource_type.value, 0) + 1
            by_status[e.status.value] = by_status.get(e.status.value, 0) + 1
            by_risk[e.risk_level.value] = by_risk.get(e.risk_level.value, 0) + 1
            if e.enabled:
                enabled_count += 1
                if (e.status.value in _schedulable_statuses
                        and e.source_type.value not in _remote_sources):
                    schedulable_count += 1
        return {
            "by_type": by_type,
            "by_status": by_status,
            "by_risk_level": by_risk,
            "total": len(entries),
            "enabled_count": enabled_count,
            "schedulable_count": schedulable_count,
            "source_status": "real",
        }

    @staticmethod
    def to_response(entry: ResourceEntry) -> dict:
        return {
            "resource_id": entry.resource_id,
            "resource_type": entry.resource_type.value,
            "name": entry.name,
            "description": entry.description,
            "version": entry.version,
            "source_type": entry.source_type.value,
            "source_trust_level": entry.source_trust_level.value,
            "source_path_or_ref": entry.source_path_or_ref,
            "risk_level": entry.risk_level.value,
            "status": entry.status.value,
            "permission_scope": entry.permission_scope,
            "capabilities": entry.capabilities,
            "allowed_actions": entry.allowed_actions,
            "blocked_actions": entry.blocked_actions,
            "input_contract": entry.input_contract,
            "output_contract": entry.output_contract,
            "artifact_evidence_contract": entry.artifact_evidence_contract,
            "gate_policy": entry.gate_policy,
            "trace_policy": entry.trace_policy,
            "audit_policy": entry.audit_policy,
            "credential_ref": entry.credential_ref,
            "supersedes": entry.supersedes,
            "superseded_by": entry.superseded_by,
            "review_status": entry.review_status,
            "reviewer": entry.reviewer,
            "review_notes": entry.review_notes,
            "type_metadata": entry.type_metadata,
            "source_status": entry.source_status,
            "capability_status": entry.capability_status,
            "enabled": entry.enabled,
            "package_url": entry.package_url,
            "checksum_sha256": entry.checksum_sha256,
            "manifest_json": entry.manifest_json,
            "download_count": entry.download_count,
            "icon_url": entry.icon_url,
            "deleted_at": entry.deleted_at,
            "imported_version": entry.imported_version,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
