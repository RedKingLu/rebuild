"""Integration summary service — aggregate integration status."""

from typing import Optional
from sqlalchemy.orm import Session

from app.models.integration_config import IntegrationConfig, IntegrationType, IntegrationStatus
from app.models.git_host import GitHost, GitHostStatus
from app.models.remote_host import RemoteHost, RemoteHostStatus


class IntegrationSummaryService:
    def __init__(self, db: Session):
        self.db = db

    def create_config(self, integration_type: str, name: str,
                      config: dict = None, credential_ref: str = None) -> IntegrationConfig:
        ic = IntegrationConfig(
            integration_type=IntegrationType(integration_type),
            name=name,
            config=config or {},
            credential_ref=credential_ref,
            status=IntegrationStatus.not_configured,
        )
        self.db.add(ic)
        self.db.commit()
        self.db.refresh(ic)
        return ic

    def get_config(self, config_id: str) -> Optional[IntegrationConfig]:
        return self.db.get(IntegrationConfig, config_id)

    def get_by_type(self, integration_type: str) -> Optional[IntegrationConfig]:
        return self.db.query(IntegrationConfig).filter(
            IntegrationConfig.integration_type == integration_type
        ).first()

    def list_all(self) -> list[IntegrationConfig]:
        return self.db.query(IntegrationConfig).order_by(IntegrationConfig.created_at.desc()).all()

    def update_config(self, config_id: str, **fields) -> Optional[IntegrationConfig]:
        ic = self.get_config(config_id)
        if ic is None:
            return None
        from datetime import datetime, timezone
        for k, v in fields.items():
            if v is not None and hasattr(ic, k):
                setattr(ic, k, v)
        ic.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(ic)
        return ic

    def delete_config(self, config_id: str) -> bool:
        ic = self.get_config(config_id)
        if ic is None:
            return False
        self.db.delete(ic)
        self.db.commit()
        return True

    def get_summary(self) -> dict:
        """Return aggregate counts for all integration types."""
        # Count Git hosts
        git_hosts = self.db.query(GitHost).all()
        git_connected = sum(1 for g in git_hosts if g.status == GitHostStatus.connected)
        git_total = len(git_hosts)

        # Count remote hosts
        remote_hosts = self.db.query(RemoteHost).all()
        remote_connected = sum(1 for r in remote_hosts if r.status == RemoteHostStatus.connected)
        remote_total = len(remote_hosts)

        # Count execution integrations (opencode)
        opencode_configs = self.db.query(IntegrationConfig).filter(
            IntegrationConfig.integration_type == IntegrationType.opencode
        ).all()
        opencode_connected = sum(1 for o in opencode_configs if o.status == IntegrationStatus.connected)
        opencode_total = len(opencode_configs)

        # Count other integrations (feishu)
        feishu_configs = self.db.query(IntegrationConfig).filter(
            IntegrationConfig.integration_type == IntegrationType.feishu
        ).all()
        feishu_connected = sum(1 for f in feishu_configs if f.status == IntegrationStatus.connected)
        feishu_total = len(feishu_configs)

        return {
            "git": {"connected": git_connected, "total": git_total},
            "remote": {"connected": remote_connected, "total": remote_total},
            "execution": {"connected": opencode_connected, "total": opencode_total},
            "other": {"connected": feishu_connected, "total": feishu_total},
            "source_status": "real",
        }

    @staticmethod
    def to_response(ic: IntegrationConfig) -> dict:
        return {
            "config_id": ic.config_id,
            "integration_type": ic.integration_type.value if hasattr(ic.integration_type, 'value') else ic.integration_type,
            "name": ic.name,
            "config": _sanitize_config(ic.config),
            "status": ic.status.value if hasattr(ic.status, 'value') else ic.status,
            "credential_ref": ic.credential_ref,
            "enabled": ic.enabled,
            "created_at": ic.created_at.isoformat() if ic.created_at else "",
            "updated_at": ic.updated_at.isoformat() if ic.updated_at else "",
            "source_status": "real",
            "capability_status": "available" if ic.status == IntegrationStatus.connected else "configured_not_verified",
        }


# Keys inside IntegrationConfig.config that hold (encrypted) secrets and must
# never be returned to clients, logged, or written to Trace/Audit.
_SENSITIVE_CONFIG_KEYS = {"webhook_url", "webhook_url_enc", "signing_secret", "signing_secret_enc"}


def _sanitize_config(config: dict | None) -> dict:
    """Strip secret material from a config dict before it leaves the backend.

    The raw webhook URL (a bearer-style secret) is stored ENCRYPTED under
    `webhook_url_enc` and must never be returned. We expose only a masked form
    plus boolean status flags so the UI can show configuration state.
    """
    config = config or {}
    safe = {k: v for k, v in config.items() if k not in _SENSITIVE_CONFIG_KEYS}
    if config.get("webhook_url_enc") or config.get("webhook_url"):
        safe["webhook_masked"] = config.get("webhook_masked", "***hook/****")
        safe["webhook_set"] = True
    else:
        safe["webhook_set"] = False
    safe["signing_secret_set"] = bool(config.get("signing_secret_set") or config.get("signing_secret_enc"))
    return safe
