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
        """With no override, should return strategy default.

        判据为**结构性**而非硬编码具体 provider（2026-09-06 改）：原写法把
        `fallback:maas-icompify/deepseek-v4-flash` 钉成常量，一旦 provider 清单变化
        （本轮按用户指令删除美团 BYOK provider 后 fallback 顺序即改变，实际落到
        `fallback:deepseek-official/deepseek-v4-flash`）测试就假失败。
        该用例的真实意图是"resolve 必须给出一个合理且可解释的选择理由"，
        与"落在哪个具体 provider"无关 —— 后者本就随环境配置变化。
        与 `test_r14_6_migrated_integration` / `test_r17_2_migration_drift` 硬编码
        Alembic head 属同一类维护点：**把可变的运行期结果钉成常量**。
        """
        from app.providers.provider_registry import get_provider_registry

        registry = get_provider_registry()
        profile, reason, provider = registry.resolve_model()
        # May return None if no keys configured, but in test env there may be keys
        if profile:
            assert reason in ("strategy_default", "first_available") or \
                reason.startswith("fallback:"), \
                f"resolve 理由须为已知类别之一，实际={reason!r}"
            if reason.startswith("fallback:"):
                # fallback 必须指向一个真实存在的 provider/model，不得是空壳
                assert "/" in reason.split("fallback:", 1)[1], \
                    f"fallback 理由须含 provider/model，实际={reason!r}"
                assert provider, "fallback 时 provider 不得为空"

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


# ═══════════════════════════════════════════════════════════════════════
# R17.5-P4-FIX 批2.6: streaming robustness — wall-clock timeout + pre-token
# backoff retry + no dirty-retry-after-commit.
# ═══════════════════════════════════════════════════════════════════════

class TestStreamRobustness:
    """stream_complete: total wall-clock timeout, retry-before-commit, no dirty retry."""

    @pytest.mark.asyncio
    async def test_stream_total_wall_clock_timeout_fails_fast(self, monkeypatch):
        """A stream that drips tokens then stalls forever must FAIL FAST at the wall-clock
        ceiling (timeout) rather than hang — this is the 791s-stall fix."""
        import asyncio as _asyncio
        from app.adapters import litellm_adapter as la

        # Tiny ceiling so the test is fast; caller passes no timeout → module default used.
        monkeypatch.setattr(la, "_STREAM_TOTAL_TIMEOUT", 0.3)

        class _Choice:
            def __init__(self, content):
                self.delta = type("D", (), {"content": content, "tool_calls": None})()

        class _Chunk:
            def __init__(self, content):
                self.choices = [_Choice(content)]
                self.usage = None

        async def _stalling_stream():
            yield _Chunk("partial ")   # commit a token
            await _asyncio.sleep(10)   # then stall well past the ceiling
            yield _Chunk("never")

        async def _fake_acompletion(**kwargs):
            return _stalling_stream()

        monkeypatch.setattr("litellm.acompletion", _fake_acompletion)

        adapter = la.LiteLLMAdapter()
        frames = []
        t0 = _asyncio.get_event_loop().time()
        async for fr in adapter.stream_complete(
                model="openai/deepseek-v4-pro", messages=[{"role": "user", "content": "hi"}],
                api_base="https://x/v1", api_key="k"):
            frames.append(fr)
        elapsed = _asyncio.get_event_loop().time() - t0

        # Fails fast near the ceiling, not after 10s.
        assert elapsed < 3.0, f"did not fail fast: {elapsed:.1f}s"
        assert frames[0]["type"] == "token" and frames[0]["content"] == "partial "
        assert frames[-1]["type"] == "error"
        assert frames[-1]["error_category"] == "timeout"
        # committed → no duplicate 'partial ' token from a dirty retry
        assert sum(1 for f in frames if f.get("type") == "token") == 1

    @pytest.mark.asyncio
    async def test_stream_pre_token_timeout_retries_then_succeeds(self, monkeypatch):
        """A transient pre-token failure must be retried with backoff and then succeed —
        this is the 'network blip at connect' recovery (isolated-P4 death fix)."""
        from app.adapters import litellm_adapter as la

        monkeypatch.setattr(la, "_RETRY_BASE_DELAY", 0.01)  # keep test fast

        class _Choice:
            def __init__(self, content):
                self.delta = type("D", (), {"content": content, "tool_calls": None})()

        class _Chunk:
            def __init__(self, content, usage=None):
                self.choices = [_Choice(content)] if content is not None else []
                self.usage = usage

        async def _good_stream():
            yield _Chunk("ok")
            yield _Chunk(None, usage=type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                                    "total_tokens": 2})())

        calls = {"n": 0}

        class _Timeout(Exception):
            pass

        async def _fake_acompletion(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _Timeout("Request timed out")  # pre-token transient failure
            return _good_stream()

        monkeypatch.setattr("litellm.acompletion", _fake_acompletion)

        adapter = la.LiteLLMAdapter()
        frames = [fr async for fr in adapter.stream_complete(
            model="openai/deepseek-v4-pro", messages=[{"role": "user", "content": "hi"}],
            api_base="https://x/v1", api_key="k")]

        assert calls["n"] == 2, "should have retried once"
        assert any(f["type"] == "token" and f["content"] == "ok" for f in frames)
        assert frames[-1]["type"] == "done"
        assert frames[-1]["usage"]["total_tokens"] == 2

    @pytest.mark.asyncio
    async def test_stream_no_dirty_retry_after_commit(self, monkeypatch):
        """Once a token is committed, a subsequent transient failure must NOT re-run the
        stream (no duplicated output) — it must emit an honest error frame instead."""
        from app.adapters import litellm_adapter as la

        monkeypatch.setattr(la, "_RETRY_BASE_DELAY", 0.01)

        class _Choice:
            def __init__(self, content):
                self.delta = type("D", (), {"content": content, "tool_calls": None})()

        class _Chunk:
            def __init__(self, content):
                self.choices = [_Choice(content)]
                self.usage = None

        calls = {"n": 0}

        class _Conn(Exception):
            pass

        async def _stream_then_fail():
            yield _Chunk("committed-token")  # commit
            raise _Conn("connection dropped mid-stream")

        async def _fake_acompletion(**kwargs):
            calls["n"] += 1
            return _stream_then_fail()

        monkeypatch.setattr("litellm.acompletion", _fake_acompletion)

        adapter = la.LiteLLMAdapter()
        frames = [fr async for fr in adapter.stream_complete(
            model="openai/deepseek-v4-pro", messages=[{"role": "user", "content": "hi"}],
            api_base="https://x/v1", api_key="k")]

        assert calls["n"] == 1, "must not dirty-retry after commit"
        assert sum(1 for f in frames if f.get("type") == "token") == 1
        assert frames[-1]["type"] == "error"

    @pytest.mark.asyncio
    async def test_stream_caller_longer_timeout_is_honoured_as_ceiling(self, monkeypatch):
        """A caller-supplied longer per-call timeout raises the ceiling (slow P3/P4 stages)
        while the default remains the floor."""
        from app.adapters import litellm_adapter as la
        import asyncio as _a
        monkeypatch.setattr(la, "_STREAM_TOTAL_TIMEOUT", 5.0)

        class _Choice:
            def __init__(self, content):
                self.delta = type("D", (), {"content": content, "tool_calls": None})()

        class _Chunk:
            def __init__(self, content):
                self.choices = [_Choice(content)]
                self.usage = None

        async def _slow_but_ok():
            await _a.sleep(0.4)  # slow, but within the 6s caller ceiling
            yield _Chunk("done-slow")

        async def _fake_acompletion(**kwargs):
            return _slow_but_ok()

        monkeypatch.setattr("litellm.acompletion", _fake_acompletion)
        adapter = la.LiteLLMAdapter()
        frames = [fr async for fr in adapter.stream_complete(
            model="openai/deepseek-v4-pro", messages=[{"role": "user", "content": "hi"}],
            api_base="https://x/v1", api_key="k", timeout=6.0)]
        assert any(f["type"] == "token" for f in frames)
        assert frames[-1]["type"] == "done"


# ═══════════════════════════════════════════════════════════════════════
# R17.5-P4-FIX 批2.8: reasoning-model reasoning_content fallback + max_tokens
# ═══════════════════════════════════════════════════════════════════════
class TestStreamReasoningFallback:
    """stream_complete: reasoning models (kimi/deepseek) stream the chain-of-thought on
    delta.reasoning_content while delta.content is empty. The adapter must not silently
    drop it (→ upstream假 empty_content); it surfaces reasoning as the final content ONLY
    when no real content token was emitted, and NEVER mixes it into a real answer."""

    @staticmethod
    def _delta(content=None, reasoning_content=None, tool_calls=None):
        # Plain object so hasattr/getattr behave like a real litellm delta (no auto-attrs).
        return type("D", (), {"content": content,
                              "reasoning_content": reasoning_content,
                              "tool_calls": tool_calls})()

    @classmethod
    def _chunk(cls, delta=None, usage=None):
        obj = type("C", (), {})()
        obj.choices = [type("Ch", (), {"delta": delta})()] if delta is not None else []
        if usage is not None:
            obj.usage = usage
        return obj

    @pytest.mark.asyncio
    async def test_reasoning_only_stream_falls_back_not_empty(self, monkeypatch):
        """(a) content empty for the WHOLE stream but reasoning_content produced → the
        adapter emits the accumulated reasoning as a final token so upstream does NOT
        判 empty_content."""
        from app.adapters import litellm_adapter as la

        chunks = [
            self._chunk(self._delta(content=None, reasoning_content="推理：先看源码，")),
            self._chunk(self._delta(content="", reasoning_content='{"objective":"迁移"}')),
        ]

        async def _stream():
            for c in chunks:
                yield c

        async def _fake_acompletion(**kwargs):
            return _stream()

        monkeypatch.setattr("litellm.acompletion", _fake_acompletion)
        adapter = la.LiteLLMAdapter()
        frames = [fr async for fr in adapter.stream_complete(
            model="openai/deepseek-v4-pro", messages=[{"role": "user", "content": "hi"}],
            api_base="https://x/v1", api_key="k")]

        token_frames = [f for f in frames if f["type"] == "token"]
        assert len(token_frames) == 1, "reasoning should be surfaced as exactly one fallback token"
        assert token_frames[0]["content"] == '推理：先看源码，{"objective":"迁移"}'
        assert frames[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_content_present_reasoning_not_mixed(self, monkeypatch):
        """(b) When delta.content is present, reasoning_content is a side-channel only —
        it must NEVER be mixed into the answer (would pollute the JSON contract)."""
        from app.adapters import litellm_adapter as la

        chunks = [
            self._chunk(self._delta(content=None, reasoning_content="思考中……")),
            self._chunk(self._delta(content='{"objective":', reasoning_content="旁白")),
            self._chunk(self._delta(content='"迁移"}', reasoning_content=None)),
        ]

        async def _stream():
            for c in chunks:
                yield c

        async def _fake_acompletion(**kwargs):
            return _stream()

        monkeypatch.setattr("litellm.acompletion", _fake_acompletion)
        adapter = la.LiteLLMAdapter()
        frames = [fr async for fr in adapter.stream_complete(
            model="openai/deepseek-v4-pro", messages=[{"role": "user", "content": "hi"}],
            api_base="https://x/v1", api_key="k")]

        answer = "".join(f["content"] for f in frames if f["type"] == "token")
        assert answer == '{"objective":"迁移"}'
        assert "思考中" not in answer and "旁白" not in answer

    @pytest.mark.asyncio
    async def test_tool_round_reasoning_not_injected(self, monkeypatch):
        """Guard: on a tool-calling round (content empty, tool_calls emitted) the reasoning
        side-channel must NOT be injected as a token — otherwise it would pollute the
        assistant turn / an intermediate round."""
        from app.adapters import litellm_adapter as la

        tc = type("TC", (), {"index": 0,
                             "id": "call_1",
                             "function": type("F", (), {"name": "list_files",
                                                        "arguments": "{}"})()})()
        chunks = [
            self._chunk(self._delta(content=None, reasoning_content="要先列文件")),
            self._chunk(self._delta(content=None, reasoning_content=None, tool_calls=[tc])),
        ]

        async def _stream():
            for c in chunks:
                yield c

        async def _fake_acompletion(**kwargs):
            return _stream()

        monkeypatch.setattr("litellm.acompletion", _fake_acompletion)
        adapter = la.LiteLLMAdapter()
        frames = [fr async for fr in adapter.stream_complete(
            model="openai/deepseek-v4-pro", messages=[{"role": "user", "content": "hi"}],
            api_base="https://x/v1", api_key="k", tools=[{"type": "function"}])]

        assert not any(f["type"] == "token" for f in frames), "no reasoning token on a tool round"
        assert any(f["type"] == "tool_calls" for f in frames)
        assert frames[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_max_tokens_forwarded_to_litellm(self, monkeypatch):
        """(c) The raised max_tokens is forwarded verbatim into litellm.acompletion so the
        reasoning-model output cap is actually lifted at the provider call."""
        from app.adapters import litellm_adapter as la
        captured = {}

        async def _stream():
            yield self._chunk(self._delta(content="ok"))

        async def _fake_acompletion(**kwargs):
            captured.update(kwargs)
            return _stream()

        monkeypatch.setattr("litellm.acompletion", _fake_acompletion)
        adapter = la.LiteLLMAdapter()
        _ = [fr async for fr in adapter.stream_complete(
            model="openai/deepseek-v4-pro", messages=[{"role": "user", "content": "hi"}],
            api_base="https://x/v1", api_key="k", max_tokens=32768)]

        assert captured["max_tokens"] == 32768

    def test_stage_and_planning_max_tokens_raised(self):
        """(c) The stage loop default + P3 planning cap are raised so reasoning + large JSON
        产物 fit (was 8192 → truncated at the cap producing empty_content)."""
        import inspect
        from app.services import stage_agent_loop, planning_service
        sig = inspect.signature(stage_agent_loop.run_stage_tool_loop)
        assert sig.parameters["max_tokens"].default == 32768
        assert planning_service._PLANNING_MAX_TOKENS >= 32768

    def test_p4_gen_max_tokens_raised(self):
        """(c) P4 generation cap raised above the former hardcoded 16384 so reasoning models
        don't truncate the migrated code / multi-file product mid-output."""
        from app.services import p4_execution_worker
        assert p4_execution_worker._GEN_MAX_TOKENS >= 32768
