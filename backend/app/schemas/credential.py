"""Pydantic schemas for Credential (BYOK key management)."""

from datetime import datetime
from pydantic import BaseModel, Field


class CredentialCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider_ref: str | None = None
    plaintext_key: str = Field(..., min_length=1)  # Received plaintext, encrypted before storage
    key_source: str = "user"
    tenant_id: str = "default"


class CredentialUpdate(BaseModel):
    name: str | None = None
    provider_ref: str | None = None
    key_source: str | None = None


class CredentialRotate(BaseModel):
    new_plaintext_key: str = Field(..., min_length=1)


class CredentialResponse(BaseModel):
    credential_id: str
    name: str
    provider_ref: str | None = None
    key_source: str
    key_fingerprint: str  # SHA-256 fingerprint (safe to expose)
    masked_key: str       # First 4 + **** + Last 4
    status: str
    tenant_id: str
    created_at: datetime
    rotated_at: datetime | None = None

    # encrypted_key is NEVER returned
    source_status: str = "real"
    capability_status: str = "active"

    model_config = {"from_attributes": True}


class CredentialListData(BaseModel):
    credentials: list[CredentialResponse]
    total: int
    source_status: str = "real"
    capability_status: str = "active"
