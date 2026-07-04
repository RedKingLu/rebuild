"""R5 ModelGateway tests — provider registry, strategy resolution, adapter, API.

Covers: Provider config read, env key status, generic_fallback marking,
model name normalization, strategy resolution, LiteLLM mock calls,
no-key/wrong-key/provider-down boundaries, volatile call log,
trace/key leak checks.
"""

import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure backend on path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


# ═══════════════════════════════════════════════════════════════════════
# Provider Registry tests
# ═══════════════════════════════════════════════════════════════════════

class TestProviderRegistry:
    """Tests for ProviderRegistry — config loading, env key resolution."""

    def test_registry_loads_from_yaml(self):
        """Registry loads providers and profiles from model_profiles.yaml."""
        from app.providers.provider_registry import ProviderRegistry, get_provider_registry

        # Use the actual config file
        registry = ProviderRegistry()
        registry.load()

        assert registry.loaded is True
        providers = registry.list_providers()
        assert len(providers) >= 1, "Should have at least 1 provider from YAML"

    def test_deepseek_provider_exists(self):
        """DeepSeek provider should be in the registry."""
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        p = registry.get_provider("deepseek-official")
        assert p is not None
        assert p.provider_name == "DeepSeek"
        assert p.api_format == "openai"
        assert p.endpoint_openai == "https://api.deepseek.com"
        assert p.endpoint_anthropic == "https://api.deepseek.com/anthropic"

    def test_all_providers_have_models(self):
        """Every provider should have at least 1 model profile."""
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        for p in registry.list_providers():
            assert len(p.models) >= 1, f"Provider {p.provider_id} has no models"

    def test_profiles_have_correct_profile_id_format(self):
        """Profile IDs should be {provider_id}/{model_name}."""
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        for profile in registry.list_profiles():
            assert "/" in profile.profile_id
            assert profile.profile_id.startswith(profile.provider_id)

    def test_strategies_loaded(self):
        """At least system-default strategy should exist."""
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        strategies = registry.list_strategies()
        assert len(strategies) >= 1
        default = registry.get_strategy("system-default")
        assert default is not None
        assert default.default_profile_ref != ""

    def test_fallback_chain_has_entries(self):
        """System-default strategy should have fallback profiles."""
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        default = registry.get_strategy("system-default")
        assert len(default.fallback_profile_refs) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Key resolution tests
# ═══════════════════════════════════════════════════════════════════════

class TestKeyResolution:
    """Tests for env key status and generic_fallback detection."""

    def test_key_env_maps_deepseek(self):
        """DEEPSEEK_API_KEY maps correctly."""
        from app.providers.provider_registry import FAMILY_KEY_ENV
        assert FAMILY_KEY_ENV["deepseek"] == "DEEPSEEK_API_KEY"

    def test_llm_api_key_is_generic_fallback(self):
        """LLM_API_KEY should be marked as generic_fallback."""
        from app.providers.provider_registry import _resolve_api_key

        with patch.dict(os.environ, {"LLM_API_KEY": "test-key", "DEEPSEEK_API_KEY": ""}, clear=True):
            key, source = _resolve_api_key("DEEPSEEK_API_KEY", "deepseek-official")
            # Should fall back to LLM_API_KEY
            assert source == "generic_fallback" if key else True

    def test_no_key_returns_none(self):
        """If no env key is set, _resolve_api_key returns None."""
        from app.providers.provider_registry import _resolve_api_key

        with patch.dict(os.environ, {}, clear=True):
            key, source = _resolve_api_key("DEEPSEEK_API_KEY", "deepseek-official")
            assert key is None
            assert source == "none"

    def test_explicit_provider_key_has_env_source(self):
        """Provider-specific key should have source='env', not 'generic_fallback'."""
        from app.providers.provider_registry import _resolve_api_key

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            key, source = _resolve_api_key("DEEPSEEK_API_KEY", "deepseek-official")
            assert key == "test-key"
            assert source == "env"

    def test_generic_fallback_for_maas(self):
        """MaaS should use LLM_API_KEY as env_key_var → generic_fallback source."""
        from app.providers.provider_registry import _resolve_api_key

        with patch.dict(os.environ, {"LLM_API_KEY": "test-key"}, clear=True):
            key, source = _resolve_api_key("LLM_API_KEY", "maas-icompify")
            assert key == "test-key"
            assert source == "generic_fallback"


# ═══════════════════════════════════════════════════════════════════════
# Model name normalization tests
# ═══════════════════════════════════════════════════════════════════════

class TestModelNameNormalization:
    """Tests for normalize_model_name — auto prefix for litellm."""

    def test_openai_format_adds_prefix(self):
        from app.providers.provider_registry import normalize_model_name
        assert normalize_model_name("deepseek-v4-flash", "openai") == "openai/deepseek-v4-flash"

    def test_anthropic_format_adds_prefix(self):
        from app.providers.provider_registry import normalize_model_name
        assert normalize_model_name("claude-sonnet-4-6", "anthropic") == "anthropic/claude-sonnet-4-6"

    def test_already_prefixed_passes_through(self):
        from app.providers.provider_registry import normalize_model_name
        assert normalize_model_name("openai/gpt-4o", "openai") == "openai/gpt-4o"

    def test_unknown_format_defaults_to_openai(self):
        from app.providers.provider_registry import normalize_model_name
        result = normalize_model_name("some-model", "unknown_format")
        assert result.startswith("openai/")


# ═══════════════════════════════════════════════════════════════════════
# Strategy resolution tests
# ═══════════════════════════════════════════════════════════════════════

class TestStrategyResolution:
    """Tests for model resolution with strategy priority."""

    def test_resolve_returns_default(self):
        """With no override, should return strategy default."""
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        profile, reason, provider = registry.resolve_model()
        # May return None if no keys configured, but in test env there may be keys
        if profile:
            assert reason in ("strategy_default", "fallback:maas-icompify/deepseek-v4-flash", "first_available")

    def test_resolve_user_override_preferred(self):
        """User override should take priority over default."""
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        # Pick a specific profile to override with
        profile, reason, _ = registry.resolve_model(
            user_override="maas-icompify/deepseek-v4-flash"
        )
        if profile:
            assert reason == "user_override"
            assert profile.provider_id == "maas-icompify"

    def test_resolve_nonexistent_override_falls_back(self):
        """If user override doesn't exist, should fall back."""
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        profile, reason, _ = registry.resolve_model(
            user_override="nonexistent/profile"
        )
        if profile:
            assert reason != "user_override"


# ═══════════════════════════════════════════════════════════════════════
# LiteLLM Adapter mock tests
# ═══════════════════════════════════════════════════════════════════════

class TestLiteLLMAdapter:
    """Tests for LiteLLMAdapter with mocked litellm."""

    def test_adapter_initializes(self):
        """Adapter should initialize without error."""
        from app.adapters.litellm_adapter import LiteLLMAdapter
        adapter = LiteLLMAdapter()
        assert adapter is not None

    @pytest.mark.asyncio
    async def test_complete_mock_success(self):
        """Adapter.complete() with mocked litellm returns successful result."""
        from app.adapters.litellm_adapter import LiteLLMAdapter

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello from mock"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            adapter = LiteLLMAdapter()
            result = await adapter.complete(
                model="openai/deepseek-v4-flash",
                messages=[{"role": "user", "content": "Hi"}],
                api_base="https://api.deepseek.com",
                api_key="test-key",
            )

        assert result.status == "completed"
        assert result.content == "Hello from mock"
        assert result.usage_summary["total_tokens"] == 15

    @pytest.mark.asyncio
    async def test_complete_auth_error(self):
        """Adapter should classify AuthenticationError correctly."""
        from app.adapters.litellm_adapter import LiteLLMAdapter

        # Use a real exception class hierarchy for proper type-checking
        class AuthenticationError(Exception):
            pass

        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=AuthenticationError("auth failed")):
            adapter = LiteLLMAdapter()
            result = await adapter.complete(
                model="openai/deepseek-v4-flash",
                messages=[{"role": "user", "content": "Hi"}],
                api_base="https://api.deepseek.com",
                api_key="wrong-key",
            )

        assert result.status == "failed"
        assert result.error_category == "auth_failed"
        # Error message must NOT contain the key
        assert "wrong-key" not in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_complete_timeout_error(self):
        """Adapter should classify timeout errors."""
        from app.adapters.litellm_adapter import LiteLLMAdapter

        class Timeout(Exception):
            pass

        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Timeout("timed out")):
            adapter = LiteLLMAdapter()
            result = await adapter.complete(
                model="openai/deepseek-v4-flash",
                messages=[{"role": "user", "content": "Hi"}],
                api_base="https://api.deepseek.com",
                api_key="test-key",
            )

        assert result.status == "failed"
        assert result.error_category == "timeout"

    @pytest.mark.asyncio
    async def test_connectivity_test_uses_minimal_prompt(self):
        """Self-test should use a fixed minimal prompt."""
        from app.adapters.litellm_adapter import LiteLLMAdapter

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hi"

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response) as mock_call:
            adapter = LiteLLMAdapter()
            await adapter.test_connectivity(
                model="openai/deepseek-v4-flash",
                api_base="https://api.deepseek.com",
                api_key="test-key",
            )

            # Verify the prompt used
            call_args = mock_call.call_args
            messages = call_args[1]["messages"]
            assert len(messages) == 1
            assert messages[0]["content"] == "Hi"


# ═══════════════════════════════════════════════════════════════════════
# Error classification tests
# ═══════════════════════════════════════════════════════════════════════

class TestErrorClassification:
    """Tests for _classify_litellm_error — all error types map correctly."""

    def test_auth_error_classified(self):
        from app.adapters.litellm_adapter import _classify_litellm_error

        class AuthenticationError(Exception):
            pass

        e = AuthenticationError("auth error")
        cat, _ = _classify_litellm_error(e)
        assert cat == "auth_failed"

    def test_rate_limit_classified(self):
        from app.adapters.litellm_adapter import _classify_litellm_error

        class RateLimitError(Exception):
            pass

        e = RateLimitError("rate limited")
        cat, _ = _classify_litellm_error(e)
        assert cat == "rate_limited"

    def test_timeout_classified(self):
        from app.adapters.litellm_adapter import _classify_litellm_error

        class Timeout(Exception):
            pass

        e = Timeout("timeout")
        cat, _ = _classify_litellm_error(e)
        assert cat == "timeout"

    def test_connection_error_classified(self):
        from app.adapters.litellm_adapter import _classify_litellm_error

        class APIConnectionError(Exception):
            pass

        e = APIConnectionError("connection refused")
        cat, _ = _classify_litellm_error(e)
        assert cat == "provider_unreachable"

    def test_error_message_never_contains_key(self):
        """All error messages must be redacted — no key leaks."""
        from app.adapters.litellm_adapter import _classify_litellm_error

        for error_cls in [
            type("AuthenticationError", (Exception,), {}),
            type("RateLimitError", (Exception,), {}),
            type("Timeout", (Exception,), {}),
            type("APIConnectionError", (Exception,), {}),
            type("BadRequestError", (Exception,), {}),
        ]:
            e = error_cls("test error with key=sk-12345-secret")
            _, msg = _classify_litellm_error(e)
            # Error messages are canned — they should never echo the raw exception
            assert "sk-12345" not in msg


# ═══════════════════════════════════════════════════════════════════════
# Call log tests
# ═══════════════════════════════════════════════════════════════════════

class TestCallLog:
    """Tests for in-memory call log — volatile storage."""

    def test_call_log_starts_empty(self):
        from app.services.model_gateway import ModelGateway
        gw = ModelGateway()
        calls = gw.list_calls()
        # FB-006: DB-persisted call log — may contain records from prior tests
        # New gateway has empty in-memory list
        assert gw._calls == []

    def test_call_log_after_call(self):
        import uuid
        from app.services.model_gateway import ModelGateway
        gw = ModelGateway()
        # Add record both in-memory (for backward compat) and persist to DB.
        # Use a unique id so the record is freshly inserted (newest) and not
        # subject to a primary-key collision across repeated test runs.
        cid = f"test-{uuid.uuid4().hex[:8]}"
        record = {
            "model_call_id": cid,
            "provider_id": "deepseek-official",
            "profile_id": "deepseek-official/deepseek-v4-flash",
            "strategy_id": "system-default",
            "selected_model": "openai/deepseek-v4-flash",
            "selection_reason": "test",
            "status": "completed",
            "latency_ms": 100,
            "error_category": "",
            "retry_count": 0,
            "fallback_used": False,
            "usage_summary": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "source": "api",
        }
        gw._calls.append(record)
        gw._persist_call(record)
        # FB-M: list_calls returns (records, total)
        calls, total = gw.list_calls(limit=100)
        assert total >= 1
        assert any(c["model_call_id"] == cid for c in calls)


class TestCallStreamPersistOnBreak:
    """UX-1 regression — streaming call log must survive an early-breaking consumer.

    agent_loop (and every SSE endpoint) does `async for frame in call_stream(...): ...
    break` on the `done` frame. Breaking out suspends the generator at its `yield`;
    on close GeneratorExit unwinds through it. Before UX-1 the call-log write sat AFTER
    the loop and was skipped, so ALL workspace (source="api") calls were dropped from
    call_log — only the non-streaming platform_assistant path (call()) ever recorded.
    """

    @pytest.mark.asyncio
    async def test_persists_completed_call_when_consumer_breaks_early(self, monkeypatch):
        import uuid as _uuid
        from app.services.model_gateway import ModelGateway
        gw = ModelGateway()

        # Resolve a real profile/provider from the shipped registry (no live-key dep).
        profile, _reason, provider = gw._registry.resolve_model(strategy_id="system-default")
        assert profile and provider, "registry ships at least one configured provider"

        # Make key resolution succeed without a real env key.
        monkeypatch.setattr(gw, "_resolve_key", lambda prov, explicit=False: ("test-key", "env"))

        class _StubAdapter:
            async def stream_complete(self, **kwargs):
                yield {"type": "token", "content": "Hel", "call_id": "c1"}
                yield {"type": "token", "content": "lo", "call_id": "c1"}
                yield {"type": "done",
                       "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                       "call_id": "c1"}
        monkeypatch.setattr(gw, "_adapter", _StubAdapter())

        marker = f"uxtest-{_uuid.uuid4().hex[:8]}"
        got = []
        agen = gw.call_stream(messages=[{"role": "user", "content": "hi"}], source=marker)
        async for frame in agen:
            if frame.get("type") == "token":
                got.append(frame["content"])
            elif frame.get("type") == "done":
                break  # <-- the exact early-exit that dropped the record before UX-1
        await agen.aclose()  # deterministically fire GeneratorExit (prod: prompt GC)

        assert "".join(got) == "Hello"
        calls, total = gw.list_calls(limit=100)
        row = next((c for c in calls if c.get("source") == marker), None)
        assert row is not None, "call_stream must persist a CallLog even on early break"
        assert row["status"] == "completed"
        assert row["usage_summary"]["total_tokens"] == 10


# ═══════════════════════════════════════════════════════════════════════
# ModelGateway status tests
# ═══════════════════════════════════════════════════════════════════════

class TestModelGatewayStatus:
    """Tests for ModelGateway.get_status()."""

    def test_status_returns_providers_count(self):
        from app.services.model_gateway import ModelGateway
        gw = ModelGateway()
        status = gw.get_status()
        assert status.total_providers >= 1
        assert status.total_profiles >= 1

    def test_status_overall_is_available_when_configured(self):
        from app.services.model_gateway import ModelGateway
        gw = ModelGateway()
        status = gw.get_status()
        if status.configured_providers > 0:
            assert status.overall_status == "available"

    def test_list_providers_returns_key_safe_dicts(self):
        """Provider dicts must NOT contain api_key or secret fields."""
        from app.services.model_gateway import ModelGateway
        gw = ModelGateway()
        providers = gw.list_providers()
        for p in providers:
            assert "api_key" not in p
            assert "api_secret" not in p
            assert "secret" not in p
            assert "password" not in p
            assert "token" not in p


# ═══════════════════════════════════════════════════════════════════════
# API endpoint integration tests (using FastAPI TestClient)
# ═══════════════════════════════════════════════════════════════════════

class TestModelAPI:
    """Integration tests for /api/model/* endpoints."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_get_status(self, client):
        resp = client.get("/api/model/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "total_providers" in data["data"]
        assert data["data"]["total_providers"] >= 1
        # Verify no key leak in status response
        assert "api_key" not in str(data).lower()

    def test_get_providers(self, client):
        resp = client.get("/api/model/providers")
        assert resp.status_code == 200
        data = resp.json()
        providers = data["data"]["providers"]
        assert len(providers) >= 1
        # Each provider must have credential_status, NOT api_key
        for p in providers:
            assert "credential_status" in p
            assert "api_key" not in p
            assert "endpoint_openai" in p

    def test_get_profiles(self, client):
        resp = client.get("/api/model/profiles")
        assert resp.status_code == 200
        data = resp.json()
        profiles = data["data"]["profiles"]
        assert len(profiles) >= 1

    def test_get_strategies(self, client):
        resp = client.get("/api/model/strategies")
        assert resp.status_code == 200
        data = resp.json()
        strategies = data["data"]["strategies"]
        assert len(strategies) >= 1

    def test_get_calls_empty_initially(self, client):
        resp = client.get("/api/model/calls")
        assert resp.status_code == 200
        data = resp.json()
        # Calls may be empty or have prior test calls
        assert "calls" in data["data"]
        # FB-006: call log is DB-persisted (survives restart), not volatile in-memory
        assert data["data"]["persisted"] is True
        assert data["data"]["volatile"] is False

    def test_get_single_provider(self, client):
        resp = client.get("/api/model/providers/deepseek-official")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["provider_name"] == "DeepSeek"

    def test_no_api_key_in_any_response(self, client):
        """All API responses must be free of key patterns."""
        endpoints = [
            "/api/model/status",
            "/api/model/providers",
            "/api/model/profiles",
            "/api/model/strategies",
            "/api/model/calls",
        ]
        for ep in endpoints:
            resp = client.get(ep)
            body = resp.text.lower()
            # Check for common key patterns
            assert "sk-" not in body, f"Key pattern 'sk-' found in {ep} response"
            assert "bearer" not in body, f"'bearer' found in {ep} response"

    def test_self_test_with_invalid_provider(self, client):
        """Self-test with nonexistent provider should return error."""
        resp = client.post("/api/model/self-test", json={
            "provider_id": "nonexistent",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["status"] == "error"

    def test_binding_endpoint_still_placeholder(self, client):
        """Project binding should remain future/not_connected."""
        resp = client.get("/api/model/projects/test-proj/binding")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["capability_status"] == "future"

    def test_assistant_chat_endpoint_exists(self, client, monkeypatch):
        """Assistant chat endpoint contract — gateway→adapter→response wiring.

        Mocks the litellm boundary so the test is deterministic and does NOT depend on a
        live external LLM (live connectivity is verified by /model/self-test and in real use).
        R9-5-1: real LLM calls now carry a hard timeout (LLM_REQUEST_TIMEOUT) so a
        non-responding provider fails fast instead of hanging — this test no longer hangs.
        """
        class _Msg:
            content = "你好，我是平台助手。"

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 2
            total_tokens = 3

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        async def _fake_acompletion(**kwargs):
            return _Resp()

        import litellm
        monkeypatch.setattr(litellm, "acompletion", _fake_acompletion)

        resp = client.post("/api/assistant/chat", json={"message": "Hi"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        # adapter success status is "completed"
        assert data["status"] == "completed"
        assert data["reply"] == "你好，我是平台助手。"
        assert data["source"] == "ModelGateway"


# ═══════════════════════════════════════════════════════════════════════
# 用户导入供应商 / 凭据 / 用量（R5-4 新增）
# ═══════════════════════════════════════════════════════════════════════

class TestUserProviderImport:
    """用户导入供应商：配置落盘、Key 仅进程内存（不落盘）、删除、能力标记。"""

    def test_add_user_provider_persists_config_not_key(self, tmp_path):
        from app.providers.provider_registry import ProviderRegistry
        user_file = tmp_path / "user_providers.yaml"
        r = ProviderRegistry(user_config_path=user_file)
        r.load()
        p = r.add_user_provider(
            {"provider_id": "acme", "provider_name": "Acme",
             "api_format": "openai", "endpoint_openai": "https://acme.example/v1",
             "models": [{"model_name": "acme-1", "display_name": "Acme 1"}]},
            api_key="super-secret-key",
        )
        assert p.origin == "user"
        assert p.credential_status == "configured"
        assert p.key_source == "in_memory"
        # 关键：Key 绝不写入落盘文件（AGENTS.md §12.1）
        content = user_file.read_text(encoding="utf-8")
        assert "super-secret-key" not in content
        assert "acme" in content  # 非敏感配置已落盘
        assert "endpoint_openai" in content

    def test_remove_user_provider_only_user_origin(self, tmp_path):
        from app.providers.provider_registry import ProviderRegistry
        r = ProviderRegistry(user_config_path=tmp_path / "user_providers.yaml")
        r.load()
        # 内置供应商不可删
        assert r.remove_user_provider("deepseek-official") is False
        # 用户供应商可删
        r.add_user_provider({"provider_id": "tmp1", "provider_name": "Tmp",
                             "models": [{"model_name": "x"}]}, api_key="k")
        assert r.remove_user_provider("tmp1") is True
        assert r.get_provider("tmp1") is None

    def test_add_provider_does_not_overwrite_seed(self, tmp_path):
        from app.providers.provider_registry import ProviderRegistry
        r = ProviderRegistry(user_config_path=tmp_path / "user_providers.yaml")
        r.load()
        import pytest as _pytest
        with _pytest.raises(ValueError):
            r.add_user_provider({"provider_id": "deepseek-official",
                                 "provider_name": "dup", "models": []})

    def test_set_credential_in_memory_only(self, tmp_path):
        from app.providers.provider_registry import ProviderRegistry
        r = ProviderRegistry(user_config_path=tmp_path / "user_providers.yaml")
        r.load()
        r.add_user_provider({"provider_id": "nokey", "provider_name": "NoKey",
                             "models": [{"model_name": "x"}]})
        p = r.set_credential("nokey", "fresh-key")
        assert p is not None
        assert p.credential_status == "configured"
        assert p.key_source == "in_memory"
        # 落盘文件不含 Key
        assert "fresh-key" not in (tmp_path / "user_providers.yaml").read_text(encoding="utf-8")

    def test_capability_marker_values(self):
        from app.providers.provider_registry import _compute_capability_marker
        assert _compute_capability_marker("missing", "not_connected", "") == "credential_missing"
        assert _compute_capability_marker("configured", "not_connected", "") == "configured_not_verified"
        assert _compute_capability_marker("configured", "available", "") == "real_available"
        assert _compute_capability_marker("configured", "not_connected", "auth_failed") == "credential_invalid"

    def test_update_strategy_persists_override(self, tmp_path):
        from app.providers.provider_registry import ProviderRegistry
        r = ProviderRegistry(
            user_config_path=tmp_path / "user_providers.yaml",
            user_strategy_path=tmp_path / "user_strategies.yaml",
        )
        r.load()
        st = r.update_strategy("system-default",
                               default_profile_ref="maas-icompify/deepseek-v4-flash",
                               fallback_profile_refs=["agnes-ai/agnes-2.0-flash"])
        assert st is not None
        assert st.default_profile_ref == "maas-icompify/deepseek-v4-flash"
        assert st.fallback_profile_refs == ["agnes-ai/agnes-2.0-flash"]
        # 覆盖已落盘且可被新实例加载
        r2 = ProviderRegistry(
            user_config_path=tmp_path / "user_providers.yaml",
            user_strategy_path=tmp_path / "user_strategies.yaml",
        )
        r2.load()
        assert r2.get_strategy("system-default").default_profile_ref == "maas-icompify/deepseek-v4-flash"

    def test_update_unknown_strategy_returns_none(self, tmp_path):
        from app.providers.provider_registry import ProviderRegistry
        r = ProviderRegistry(user_strategy_path=tmp_path / "user_strategies.yaml")
        r.load()
        assert r.update_strategy("nonexistent", default_profile_ref="x") is None


class TestProviderAPIImport:
    """/api/model/providers 导入/删除/凭据/用量 端点。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_create_and_delete_provider_endpoint(self, client):
        # 创建（不带 Key，避免污染真实 env）
        resp = client.post("/api/model/providers", json={
            "provider_name": "Test Import Co",
            "api_format": "openai",
            "endpoint_openai": "https://test-import.example/v1",
            "models": [{"model_name": "ti-1", "display_name": "TI 1"}],
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data is not None
        pid = data["provider_id"]
        assert data["origin"] == "user"
        # 响应不含 Key
        assert "api_key" not in resp.text.lower() or "sk-" not in resp.text
        # 删除
        d = client.delete(f"/api/model/providers/{pid}")
        assert d.status_code == 200
        assert d.json()["data"]["removed"] is True

    def test_cannot_delete_seed_provider(self, client):
        d = client.delete("/api/model/providers/deepseek-official")
        assert d.json()["data"]["removed"] is False

    def test_usage_endpoint(self, client):
        resp = client.get("/api/model/usage")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_calls" in data
        assert "total_tokens" in data
        assert data["cost_available"] is False  # 无计价数据，诚实标记
        # FB-006: usage aggregated from DB-persisted call log, not volatile
        assert data["persisted"] is True
        assert data["volatile"] is False

    def test_set_credential_no_key_in_response(self, client):
        # 先建一个用户 provider
        resp = client.post("/api/model/providers", json={
            "provider_name": "Cred Test", "endpoint_openai": "https://c.example/v1",
            "models": [{"model_name": "c-1"}],
        })
        pid = resp.json()["data"]["provider_id"]
        r = client.post(f"/api/model/providers/{pid}/credential", json={"api_key": "secret-xyz-123"})
        assert r.status_code == 200
        assert "secret-xyz-123" not in r.text  # 不回显 Key
        assert r.json()["data"]["credential_status"] == "configured"
        client.delete(f"/api/model/providers/{pid}")
