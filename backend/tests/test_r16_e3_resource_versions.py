"""R16-B E3: ResourceEntry.imported_version column + migration + ResourceResponse schema.

NOTE: the import flow itself (routes_community.import_resource persisting imported_version +
warning on version mismatch) is exercised end-to-end in the D-074 Playwright evidence
(test_r16_d074_e3_import_version.mjs) against a live stack. The unit tests here verify the
schema/migration scaffolding that the e2e presumes.
"""
from __future__ import annotations

import os
import pytest


def test_resourceentry_has_imported_version_column():
    """Model exposes imported_version nullable str column."""
    from app.models.resource_entry import ResourceEntry
    assert hasattr(ResourceEntry, "imported_version")
    col = ResourceEntry.__table__.c.get("imported_version")
    assert col is not None
    assert col.nullable is True


def test_resourceresponse_schema_has_imported_version():
    """ResourceResponse carries imported_version (nullable, defaults None, settable)."""
    from app.schemas.registry import ResourceResponse
    assert "imported_version" in ResourceResponse.model_fields
    obj = ResourceResponse(
        resource_id="x", resource_type="knowledge", name="x", version="1.0.0",
        source_type="community", source_trust_level="trusted_current",
        risk_level="L0", status="active", permission_scope="read_only",
    )
    assert obj.imported_version is None
    dumped = obj.model_dump()
    dumped["imported_version"] = "1.2.0"
    obj2 = ResourceResponse.model_validate(dumped)
    assert obj2.imported_version == "1.2.0"


def test_alembic_migration_chains_from_r15_4_head():
    """The R16-B imported_version migration references R15-4 head (39528fb4d798)."""
    mig_dir = os.path.join(os.path.dirname(__file__), "..", "alembic", "versions")
    found = False
    for f in os.listdir(mig_dir):
        if not f.endswith(".py") or f.startswith("__"):
            continue
        t = open(os.path.join(mig_dir, f)).read()
        if "e4f5a6b7c8d9" in t and "imported_version" in t:
            assert "39528fb4d798" in t, "migration must chain from R15-4 head"
            found = True
    assert found, "e4f5a6b7c8d9_r16_b_imported_version migration not found"
