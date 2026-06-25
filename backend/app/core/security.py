"""Security utilities — placeholder for future redaction middleware.

R4: No real redaction scanning is performed.
This module provides stub helpers for the security slot.
"""


def redact_value(value: str) -> str:
    """Stub redaction — R4 does not perform real redaction scanning."""
    return value


def is_sensitive_key(key_name: str) -> bool:
    """Check if a key name suggests sensitive content (for .env detection only)."""
    sensitive_patterns = [
        "api_key", "apikey", "secret", "password", "token",
        "credential", "private_key", "access_key",
    ]
    key_lower = key_name.lower().replace("_", "").replace("-", "")
    return any(p in key_lower for p in sensitive_patterns)
