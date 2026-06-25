"""Capability and source status enums for mock/not_connected marking.

R4: All API responses carry capability/source status metadata
so the frontend can accurately display mock vs real capability.
"""

# === Capability / Source status (for response meta) ===
CAPABILITY_STATUSES = [
    "mock",
    "fixture",
    "in_memory",
    "placeholder",
    "not_connected",
    "future",
    "read_only",
    "redacted",
]

# === Source status (alias, same enum space) ===
SOURCE_STATUSES = CAPABILITY_STATUSES
