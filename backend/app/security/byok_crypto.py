"""BYOK AES-256-GCM + HKDF encryption (adapted from V10 byok_crypto.py)."""

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


def get_master_key() -> bytes:
    """Derive master key from REBUILD_MASTER_KEY env var.

    SECURITY: dev fallback has been removed (T7/公理3/公理7).
    Set REBUILD_MASTER_KEY=<32-byte hex> in backend/.env before using BYOK encryption.
    """
    import os as _os
    raw = _os.environ.get("REBUILD_MASTER_KEY")
    if raw:
        return hashlib.sha256(raw.encode("utf-8")).digest()
    raise RuntimeError(
        "REBUILD_MASTER_KEY is not set. "
        "Add REBUILD_MASTER_KEY=<random-32-byte-hex> to backend/.env before using BYOK encryption. "
        "Example: python -c \"import secrets; print(secrets.token_hex(32))\""
    )


def hkdf_derive(tenant_id: str, salt: bytes | None = None) -> bytes:
    """Derive per-tenant encryption key via HKDF-SHA256."""
    if salt is None:
        salt = hashlib.sha256(tenant_id.encode("utf-8")).digest()[:16]
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"rebuild-byok-credential-encryption",
    )
    return hkdf.derive(get_master_key())


def encrypt_api_key(plaintext: str, tenant_id: str = "default") -> bytes:
    """Encrypt an API key using AES-256-GCM. Returns nonce + ciphertext."""
    key = hkdf_derive(tenant_id)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt_api_key(encrypted: bytes, tenant_id: str = "default") -> str:
    """Decrypt an API key. `encrypted` must be nonce (12 bytes) + ciphertext."""
    key = hkdf_derive(tenant_id)
    aesgcm = AESGCM(key)
    nonce = encrypted[:12]
    ciphertext = encrypted[12:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")


def hash_api_key(plaintext: str) -> str:
    """SHA-256 fingerprint of a key for dedup detection (does NOT reveal the key)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def mask_key(plaintext: str, visible: int = 4) -> str:
    """Mask a key for display: first `visible` + '****' + last `visible`."""
    if len(plaintext) <= visible * 2:
        return "*" * len(plaintext)
    return plaintext[:visible] + "****" + plaintext[-visible:]
