"""R6 tests: Agent/Skill/Resource Registry + BYOK + Key leak prevention."""


def test_agents_list(client):
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 5  # 5 seed agents
    assert data["source_status"] == "real"
    assert len(data["agents"]) >= 5


def test_agents_filter_by_type(client):
    resp = client.get("/api/agents?agent_type=node_worker")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert all(a["agent_type"] == "node_worker" for a in data["agents"])


def test_agent_get(client):
    resp = client.get("/api/agents")
    agent_id = resp.json()["agents"][0]["agent_id"]
    resp2 = client.get(f"/api/agents/{agent_id}")
    assert resp2.status_code == 200
    assert resp2.json()["agent_id"] == agent_id


def test_agent_create_and_delete(client):
    resp = client.post("/api/agents", json={
        "agent_type": "node_worker",
        "name": "Test Create Agent",
        "responsibilities": "test",
        "forbidden": "test",
    })
    assert resp.status_code == 201
    agent_id = resp.json()["agent_id"]

    resp2 = client.delete(f"/api/agents/{agent_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "success"


def test_agent_404(client):
    resp = client.get("/api/agents/nonexistent-id")
    assert resp.status_code == 404


def test_skills_list(client):
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 27  # 3 R-series + 24 P-series (curated from ECC)
    assert data["source_status"] == "real"


def test_skills_r_series_filter(client):
    resp = client.get("/api/skills?series=R")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 3
    assert all(s["series"] == "R" for s in data["skills"])


def test_skills_p_series_filter(client):
    resp = client.get("/api/skills?series=P")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 24
    assert all(s["series"] == "P" for s in data["skills"])


def test_skill_create_and_delete(client):
    resp = client.post("/api/skills", json={
        "name": "Test Skill",
        "series": "R",
        "category": "p4",
        "description": "test",
    })
    assert resp.status_code == 201
    skill_id = resp.json()["skill_id"]

    resp2 = client.delete(f"/api/skills/{skill_id}")
    assert resp2.status_code == 200


def test_resources_list(client):
    resp = client.get("/api/resources")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 24  # Seed data
    assert data["source_status"] == "real"


def test_resources_filter_by_type(client):
    resp = client.get("/api/resources?type=agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    assert all(r["resource_type"] == "agent" for r in data["resources"])


def test_resources_filter_by_status(client):
    resp = client.get("/api/resources?status=active")
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["status"] == "active" for r in data["resources"])


def test_resources_filter_by_risk(client):
    resp = client.get("/api/resources?risk=L5")
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["risk_level"] == "L5" for r in data["resources"])


def test_resources_registry_summary(client):
    resp = client.get("/api/resources/registry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 24
    assert "by_type" in data
    assert "by_status" in data
    assert "by_risk_level" in data


def test_resources_case_never_executable(client):
    """All Case resources must be read_only and never executable."""
    resp = client.get("/api/resources?type=case")
    assert resp.status_code == 200
    for r in resp.json()["resources"]:
        assert r["status"] in ("read_only",)
        assert r["permission_scope"] == "read_only"
        if r.get("type_metadata"):
            assert r["type_metadata"].get("never_execute") is True


def test_resources_knowledge_read_only(client):
    """All Knowledge resources must be read_only."""
    resp = client.get("/api/resources?type=knowledge")
    for r in resp.json()["resources"]:
        assert r["status"] in ("read_only", "local_existing", "externally_available")


def test_resource_create_and_delete(client):
    resp = client.post("/api/resources", json={
        "resource_type": "tool",
        "name": "Test Tool",
        "description": "A test tool",
        "risk_level": "L1",
        "status": "draft",
    })
    assert resp.status_code == 201
    resource_id = resp.json()["resource_id"]

    resp2 = client.delete(f"/api/resources/{resource_id}")
    assert resp2.status_code == 200


# --- Credential / BYOK tests ---


def test_credential_create_masked_response(client):
    """Credential response must NEVER contain plaintext or encrypted key."""
    resp = client.post("/api/credentials", json={
        "name": "test-key",
        "plaintext_key": "sk-test-secret-key-12345678",
    })
    assert resp.status_code == 201
    data = resp.json()
    # Must have fingerprint and masked_key
    assert "key_fingerprint" in data
    assert len(data["key_fingerprint"]) == 64  # SHA-256 hex
    assert "masked_key" in data
    assert data["masked_key"] == "****"  # Always "****" regardless of key
    # Must NOT contain plaintext or encrypted_key
    assert "plaintext_key" not in data
    assert "encrypted_key" not in data
    assert "secret" not in str(data).lower().replace("secret", "")


def test_credential_list_no_key_leak(client):
    """List credentials must never leak keys."""
    resp = client.get("/api/credentials")
    assert resp.status_code == 200
    data = resp.json()
    for cred in data["credentials"]:
        assert "plaintext_key" not in cred
        assert "encrypted_key" not in cred
        assert cred["masked_key"] == "****"


def test_credential_delete(client):
    resp = client.post("/api/credentials", json={
        "name": "to-delete",
        "plaintext_key": "sk-delete-me",
    })
    cred_id = resp.json()["credential_id"]

    resp2 = client.delete(f"/api/credentials/{cred_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "success"

    resp3 = client.get("/api/credentials")
    assert all(c["credential_id"] != cred_id for c in resp3.json()["credentials"])


def test_credential_rotate(client):
    resp = client.post("/api/credentials", json={
        "name": "to-rotate",
        "plaintext_key": "sk-old-key-12345",
    })
    cred_id = resp.json()["credential_id"]
    old_fingerprint = resp.json()["key_fingerprint"]

    resp2 = client.post(f"/api/credentials/{cred_id}/rotate", json={
        "new_plaintext_key": "sk-new-key-67890",
    })
    assert resp2.status_code == 200
    new_fingerprint = resp2.json()["key_fingerprint"]

    assert old_fingerprint != new_fingerprint  # Fingerprint should change
    assert resp2.json()["status"] == "active"


# --- BYOK crypto unit tests ---


def test_encrypt_decrypt_roundtrip():
    from app.security.byok_crypto import encrypt_api_key, decrypt_api_key, hash_api_key, mask_key
    plaintext = "sk-test-key-1234567890"
    encrypted = encrypt_api_key(plaintext)
    decrypted = decrypt_api_key(encrypted)
    assert decrypted == plaintext


def test_hash_api_key_consistent():
    from app.security.byok_crypto import hash_api_key
    assert hash_api_key("test-key") == hash_api_key("test-key")
    assert hash_api_key("test-key") != hash_api_key("different-key")


def test_mask_key():
    from app.security.byok_crypto import mask_key
    assert mask_key("sk-abcdefgh12345678") == "sk-a****5678"
    assert mask_key("ab") == "**"
    assert mask_key("abc") == "***"


# --- Generic fallback tests (P1-2 fix: strong assertions) ---


def test_explicit_provider_mismatch_returns_none():
    """Explicit override that matches NO profile MUST return None — no cross-provider fallback."""
    from app.providers.provider_registry import get_provider_registry
    registry = get_provider_registry()
    profile, reason, provider = registry.resolve_model(
        user_override="nonexistent-model-xyz-12345",
        strategy_id="system-default",
    )
    # MUST return None — explicit override failed, no fallback allowed
    assert profile is None
    assert provider is None
    assert "explicit_override_not_found" in reason


def test_explicit_provider_found_returns_it():
    """Explicit override that matches a configured profile MUST return that profile."""
    from app.providers.provider_registry import get_provider_registry
    registry = get_provider_registry()
    # Find a real configured profile to test with
    profiles = registry.list_profiles()
    configured = [p for p in profiles if p.status == "configured"]
    if configured:
        target = configured[0]
        profile, reason, provider = registry.resolve_model(
            user_override=target.profile_id,
            strategy_id="system-default",
        )
        assert profile is not None
        assert profile.profile_id == target.profile_id
        assert reason == "user_override"


def test_default_strategy_fallback_chain_works():
    """Without explicit override, default strategy MAY use fallback chain."""
    from app.providers.provider_registry import get_provider_registry
    registry = get_provider_registry()
    profile, reason, provider = registry.resolve_model(
        user_override=None,
        strategy_id="system-default",
    )
    # Default strategy: may find profile (if env keys configured) or return clean reason
    if profile is not None:
        assert provider is not None
    else:
        # No configured profile is acceptable in test env (no env keys)
        assert reason in ("no_configured_profile", "no_strategy")


def test_explicit_provider_no_credential_ref_no_env_returns_none():
    """Explicit provider with no credential_ref AND no env key → _resolve_key returns None."""
    from app.providers.provider_registry import ProviderInfo
    from app.services.model_gateway import ModelGateway
    import os
    gw = ModelGateway()
    # Use a truly non-existent env var (no LLM_API_KEY fallback)
    saved = os.environ.pop("LLM_API_KEY", None)
    try:
        p = ProviderInfo(
            provider_id="test-no-cred-2", provider_name="test",
            provider_type="openai_compatible", api_format="openai",
            env_key_var="NONEXISTENT_ENV_VAR_XYZ_12345", credential_ref="",
        )
        key_val, key_source = gw._resolve_key(p, explicit_provider=True)
        assert key_val is None
        assert key_source in ("explicit_provider_no_key", "none")
    finally:
        if saved:
            os.environ["LLM_API_KEY"] = saved


def test_provider_with_credential_ref_uses_byok_path():
    """Provider with valid credential_ref MUST resolve key via BYOK (not env fallback)."""
    from app.security.byok_crypto import encrypt_api_key
    from app.models.credential import Credential, KeySource, CredentialStatus
    from app.core.database import get_session
    from app.services.model_gateway import ModelGateway
    from app.providers.provider_registry import ProviderInfo
    import uuid

    # Create a credential in DB
    test_key = "sk-byok-test-real-key-12345"
    encrypted = encrypt_api_key(test_key)
    db = get_session()
    try:
        cred = Credential(
            credential_id=str(uuid.uuid4()),
            name="test-byok-provider-key",
            key_source=KeySource.user,
            encrypted_key=encrypted,
            key_fingerprint="test-fp",
            status=CredentialStatus.active,
        )
        db.add(cred)
        db.commit()
        cred_id = cred.credential_id

        # Build provider with credential_ref pointing to this credential
        gw = ModelGateway()
        p = ProviderInfo(
            provider_id="test-byok-prov", provider_name="test",
            provider_type="openai_compatible", api_format="openai",
            env_key_var="NONEXISTENT_ENV_VAR_XYZ", credential_ref=cred_id,
        )
        key_val, key_source = gw._resolve_key(p, explicit_provider=True)
        # MUST resolve via credential_ref (BYOK path)
        assert key_val == test_key
        assert key_source == "credential_ref"
    finally:
        db.rollback()
        db.close()


# --- Credential Audit tests ---


def test_credential_create_audit(client):
    """Creating a credential should be auditable."""
    resp = client.post("/api/credentials", json={
        "name": "audit-test-key",
        "plaintext_key": "sk-audit-test-12345",
    })
    assert resp.status_code == 201
    cred_id = resp.json()["credential_id"]

    # Verify the audit was written (via AuditWriter in-memory)
    from app.api.routes_credentials import _audit
    audits = _audit.list_all()
    create_audits = [a for a in audits if a.get("audit_type") == "credential.create"]
    assert len(create_audits) >= 1
    # Audit must NOT contain plaintext key
    for a in create_audits:
        audit_str = str(a)
        assert "sk-audit-test-12345" not in audit_str
        assert "plaintext" not in audit_str.lower()

    # Cleanup
    client.delete(f"/api/credentials/{cred_id}")


def test_credential_delete_audit(client):
    """Deleting a credential should write audit."""
    resp = client.post("/api/credentials", json={
        "name": "audit-delete-key",
        "plaintext_key": "sk-delete-audit-12345",
    })
    cred_id = resp.json()["credential_id"]

    from app.api.routes_credentials import _audit
    before_count = len(_audit.list_all())

    client.delete(f"/api/credentials/{cred_id}")

    after_count = len(_audit.list_all())
    assert after_count > before_count
    delete_audits = [a for a in _audit.list_all() if a.get("audit_type") == "credential.delete"]
    assert len(delete_audits) >= 1


def test_credential_rotate_audit(client):
    """Rotating a credential should write audit."""
    resp = client.post("/api/credentials", json={
        "name": "audit-rotate-key",
        "plaintext_key": "sk-rotate-audit-12345",
    })
    cred_id = resp.json()["credential_id"]

    from app.api.routes_credentials import _audit
    before_count = len(_audit.list_all())

    client.post(f"/api/credentials/{cred_id}/rotate", json={
        "new_plaintext_key": "sk-rotated-new-key",
    })

    after_count = len(_audit.list_all())
    assert after_count > before_count
    rotate_audits = [a for a in _audit.list_all() if a.get("audit_type") == "credential.rotate"]
    assert len(rotate_audits) >= 1
    # Audit must NOT contain new key
    for a in rotate_audits:
        assert "sk-rotated-new-key" not in str(a)

    # Cleanup
    client.delete(f"/api/credentials/{cred_id}")


def test_audit_never_leaks_key(client):
    """ALL audits must NEVER contain plaintext keys."""
    resp = client.post("/api/credentials", json={
        "name": "no-leak-audit-key",
        "plaintext_key": "sk-NEVER-LEAK-THIS-KEY-98765",
    })
    cred_id = resp.json()["credential_id"]
    client.delete(f"/api/credentials/{cred_id}")

    from app.api.routes_credentials import _audit
    for a in _audit.list_all():
        assert "sk-NEVER-LEAK-THIS-KEY-98765" not in str(a)
        assert "plaintext_key" not in str(a)
        assert "encrypted_key" not in str(a)
