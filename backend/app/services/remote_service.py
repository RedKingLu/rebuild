"""Remote host service — DB-backed remote resource management (VM/physical/container)."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.models.remote_host import RemoteHost, RemoteHostStatus, HostType


class RemoteService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, name: str, host_type: str = "virtual_machine",
               address: str = "", port: int = 22, os_name: str = None,
               credential_ref: str = None) -> RemoteHost:
        host = RemoteHost(
            name=name,
            host_type=HostType(host_type) if host_type in [e.value for e in HostType] else HostType.virtual_machine,
            address=address,
            masked_address=_mask_address(address),
            port=port,
            os_name=os_name,
            credential_ref=credential_ref,
            status=RemoteHostStatus.unconfigured,
        )
        self.db.add(host)
        self.db.commit()
        self.db.refresh(host)
        return host

    def get(self, remote_host_id: str) -> Optional[RemoteHost]:
        return self.db.get(RemoteHost, remote_host_id)

    def list_all(self) -> list[RemoteHost]:
        return self.db.query(RemoteHost).order_by(RemoteHost.created_at.desc()).all()

    def update(self, remote_host_id: str, **fields) -> Optional[RemoteHost]:
        host = self.get(remote_host_id)
        if host is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(host, k):
                setattr(host, k, v)
        # Re-mask address if changed
        if "address" in fields and fields["address"] is not None:
            host.masked_address = _mask_address(fields["address"])
        host.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(host)
        return host

    def delete(self, remote_host_id: str) -> bool:
        host = self.get(remote_host_id)
        if host is None:
            return False
        self.db.delete(host)
        self.db.commit()
        return True

    @staticmethod
    def to_response(host: RemoteHost) -> dict:
        return {
            "remote_host_id": host.remote_host_id,
            "name": host.name,
            "host_type": host.host_type.value if hasattr(host.host_type, 'value') else host.host_type,
            "address": host.masked_address or "***",
            "port": host.port,
            "os_name": host.os_name,
            "credential_ref": host.credential_ref,
            "status": host.status.value if hasattr(host.status, 'value') else host.status,
            "last_connected_at": host.last_connected_at.isoformat() if host.last_connected_at else None,
            "created_at": host.created_at.isoformat() if host.created_at else "",
            "updated_at": host.updated_at.isoformat() if host.updated_at else "",
            "enabled": host.enabled,
            "source_status": "real",
            "capability_status": "available" if host.status == RemoteHostStatus.connected else "configured_not_verified",
        }


def _mask_address(address: str) -> str:
    """Mask IP address for display: 192.168.1.100 → 192.168.***.100"""
    if not address:
        return "***"
    parts = address.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.***.{parts[3]}"
    # For hostnames, just return as-is (hostname alone isn't sensitive)
    return address
