"""Credential service — BYOK key management with AES-256-GCM encryption."""

from sqlalchemy.orm import Session

from app.models.credential import Credential, KeySource, CredentialStatus
from app.schemas.credential import CredentialCreate
from app.security.byok_crypto import encrypt_api_key, decrypt_api_key, hash_api_key, mask_key


class CredentialService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: CredentialCreate) -> Credential:
        fingerprint = hash_api_key(data.plaintext_key)
        encrypted = encrypt_api_key(data.plaintext_key, data.tenant_id)
        cred = Credential(
            name=data.name,
            provider_ref=data.provider_ref,
            key_source=KeySource(data.key_source),
            encrypted_key=encrypted,
            key_fingerprint=fingerprint,
            status=CredentialStatus.active,
            tenant_id=data.tenant_id,
        )
        self.db.add(cred)
        self.db.commit()
        self.db.refresh(cred)
        return cred

    def get(self, credential_id: str) -> Credential | None:
        return self.db.get(Credential, credential_id)

    def list_all(self) -> list[Credential]:
        return self.db.query(Credential).order_by(Credential.created_at.desc()).all()

    def delete(self, credential_id: str) -> bool:
        cred = self.get(credential_id)
        if cred is None:
            return False
        self.db.delete(cred)
        self.db.commit()
        return True

    def rotate(self, credential_id: str, new_plaintext_key: str) -> Credential | None:
        cred = self.get(credential_id)
        if cred is None:
            return None
        from datetime import datetime, timezone
        cred.encrypted_key = encrypt_api_key(new_plaintext_key, cred.tenant_id)
        cred.key_fingerprint = hash_api_key(new_plaintext_key)
        cred.status = CredentialStatus.active
        cred.rotated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(cred)
        return cred

    def decrypt(self, credential_id: str) -> str | None:
        """Decrypt and return plaintext key — only for ModelGateway internal use."""
        cred = self.get(credential_id)
        if cred is None:
            return None
        return decrypt_api_key(cred.encrypted_key, cred.tenant_id)

    @staticmethod
    def to_response(cred: Credential) -> dict:
        return {
            "credential_id": cred.credential_id,
            "name": cred.name,
            "provider_ref": cred.provider_ref,
            "key_source": cred.key_source.value,
            "key_fingerprint": cred.key_fingerprint,
            "masked_key": "****",  # Never expose actual key or key length
            "status": cred.status.value,
            "tenant_id": cred.tenant_id,
            "created_at": cred.created_at,
            "rotated_at": cred.rotated_at,
            "source_status": "real",
            "capability_status": "active",
        }
