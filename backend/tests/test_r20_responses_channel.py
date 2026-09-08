"""B-R20-NO-RESPONSES-CHANNEL — Codex/OpenAI Responses API 通道接线测试。

覆盖：
1. LiteLLMAdapter.complete_via_responses() 用 mock litellm.aresponses 验证：正确拆分
   content / reasoning_content，usage 正确拍平，错误分类复用既有 _classify_litellm_error。
2. ModelGateway.call() / call_stream() 在 api_format=="responses"（由 profile 级
   api_format_override 决定）时正确路由到新方法，且不误触 complete()/stream_complete()。
3. call() 的返回结构与既有 openai 通道契约兼容（同一组 key，reasoning_content 为新增
   additive key）。
4. 一个不 mock、真实发起网络请求的集成测试（无真实 Key 时 skip，不在 CI/无 Key 环境报错）。

真实 provider：maas-icompify 新增的 "qwen3.8-27b-responses" profile（与既有
"qwen3.8-27b" 是同一个底层模型，仅切换调用通道 —— 见 model_profiles.yaml 注释）。
"""

import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# NOTE（已知坑，见任务交接记录）：.env 不是在进程启动时自动加载的 —— 它是 litellm 包
# `__init__.py` 顶层 `dotenv.load_dotenv()` 的**导入副作用**。若本模块在别的测试文件/
# conftest 先导入 litellm 之前就在【模块级】读 os.environ 判断"是否有真实 Key"，会因为
# import 顺序偶然性拿到假阴性（之前踩过的坑）。显式导入 litellm，确保 .env 已加载，
# 不依赖其它模块的导入顺序。
import litellm  # noqa: F401  — 触发 .env 加载副作用，见上方说明


# ═══════════════════════════════════════════════════════════════════════
# Adapter-level: complete_via_responses() with mocked litellm.aresponses
# ═══════════════════════════════════════════════════════════════════════

def _fake_responses_payload(content_text="2", reasoning_text="thinking...", usage=None):
    """构造与真实 maas-icompify 调用实测一致的 output/usage 形状（plain dict）。"""
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "content": [
                {"type": "reasoning_text", "text": reasoning_text}]},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": content_text}]},
        ],
        "usage": usage or {
            "prompt_tokens": 10, "completion_tokens": 5,
            "total_tokens": 15, "reasoning_tokens": 3,
        },
    }


class TestCompleteViaResponses:
    """LiteLLMAdapter.complete_via_responses() — mocked litellm.aresponses."""

    @pytest.mark.asyncio
    async def test_success_splits_content_and_reasoning(self):
        from app.adapters.litellm_adapter import LiteLLMAdapter

        payload = _fake_responses_payload(content_text="\n\n2", reasoning_text="1+1=2\n")
        with patch("litellm.aresponses", new_callable=AsyncMock, return_value=payload):
            adapter = LiteLLMAdapter()
            result = await adapter.complete_via_responses(
                model="openai/qwen3.8-27b",
                messages=[{"role": "user", "content": "1+1=?"}],
                api_base="https://api.icompify.com/v1",
                api_key="test-key",
            )

        assert result.status == "completed"
        assert result.content == "\n\n2"
        # reasoning 必须单独暴露，不混进 content
        assert result.reasoning_content == "1+1=2\n"
        assert "1+1=2" not in result.content
        assert result.usage_summary == {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
            "reasoning_tokens": 3,
        }

    @pytest.mark.asyncio
    async def test_passes_input_and_endpoint_kwargs(self):
        """messages 原样作为 input 传入；api_base/api_key/model 原样转发给 litellm."""
        from app.adapters.litellm_adapter import LiteLLMAdapter

        payload = _fake_responses_payload()
        with patch("litellm.aresponses", new_callable=AsyncMock, return_value=payload) as mock_call:
            adapter = LiteLLMAdapter()
            messages = [{"role": "user", "content": "hi"}]
            await adapter.complete_via_responses(
                model="openai/qwen3.8-27b",
                messages=messages,
                api_base="https://api.icompify.com/v1",
                api_key="sk-test",
                max_tokens=200,
                temperature=0.3,
            )

        _, kwargs = mock_call.call_args
        assert kwargs["model"] == "openai/qwen3.8-27b"
        assert kwargs["input"] == messages
        assert kwargs["api_base"] == "https://api.icompify.com/v1"
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["max_output_tokens"] == 200
        assert kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_error_classified_and_key_redacted(self):
        from app.adapters.litellm_adapter import LiteLLMAdapter

        class AuthenticationError(Exception):
            pass

        with patch("litellm.aresponses", new_callable=AsyncMock,
                   side_effect=AuthenticationError("bad key sk-should-not-leak")):
            adapter = LiteLLMAdapter()
            result = await adapter.complete_via_responses(
                model="openai/qwen3.8-27b",
                messages=[{"role": "user", "content": "hi"}],
                api_base="https://api.icompify.com/v1",
                api_key="sk-should-not-leak",
            )

        assert result.status == "failed"
        assert result.error_category == "auth_failed"
        assert "sk-should-not-leak" not in result.error_message

    @pytest.mark.asyncio
    async def test_missing_output_defensive(self):
        """空/缺失 output 不应抛异常 — 返回空 content/reasoning（公理3：诚实空值而非崩溃）。"""
        from app.adapters.litellm_adapter import LiteLLMAdapter

        with patch("litellm.aresponses", new_callable=AsyncMock,
                   return_value={"status": "completed", "output": [], "usage": {}}):
            adapter = LiteLLMAdapter()
            result = await adapter.complete_via_responses(
                model="openai/qwen3.8-27b",
                messages=[{"role": "user", "content": "hi"}],
                api_base="https://api.icompify.com/v1",
                api_key="sk-test",
            )
        assert result.status == "completed"
        assert result.content == ""
        assert result.reasoning_content == ""


# ═══════════════════════════════════════════════════════════════════════
# Gateway-level: call() / call_stream() route to the responses channel
# ═══════════════════════════════════════════════════════════════════════

class TestGatewayRoutesResponsesChannel:
    """ModelGateway routes api_format=='responses' (profile override) to the new adapter
    methods instead of complete()/stream_complete() — and never both."""

    @pytest.mark.asyncio
    async def test_call_routes_to_complete_via_responses(self, monkeypatch):
        from app.services.model_gateway import ModelGateway
        from app.adapters.litellm_adapter import ModelCallResult

        monkeypatch.setenv("LLM_API_KEY", "sk-test-icompify")
        gw = ModelGateway()

        fake_result = ModelCallResult(
            call_id="call_test1", status="completed", content="4",
            reasoning_content="2+2=4", usage_summary={"prompt_tokens": 1,
            "completion_tokens": 1, "total_tokens": 2},
        )
        with patch.object(gw._adapter, "complete_via_responses", new_callable=AsyncMock,
                          return_value=fake_result) as mock_responses, \
             patch.object(gw._adapter, "complete", new_callable=AsyncMock) as mock_chat:
            res = await gw.call(
                messages=[{"role": "user", "content": "2+2=?"}],
                user_override="maas-icompify/qwen3.8-27b-responses",
            )

        mock_chat.assert_not_called()
        mock_responses.assert_called_once()
        _, kwargs = mock_responses.call_args
        assert kwargs["model"] == "openai/qwen3.8-27b"
        assert kwargs["api_base"] == "https://api.icompify.com/v1"
        assert kwargs["api_key"] == "sk-test-icompify"

        assert res["status"] == "completed"
        assert res["content"] == "4"
        assert res["reasoning_content"] == "2+2=4"

    @pytest.mark.asyncio
    async def test_call_stream_routes_to_stream_via_responses(self, monkeypatch):
        from app.services.model_gateway import ModelGateway

        monkeypatch.setenv("LLM_API_KEY", "sk-test-icompify")
        gw = ModelGateway()

        async def fake_stream(**kwargs):
            yield {"type": "token", "content": "4", "call_id": "scall_test1"}
            yield {"type": "done", "usage": {"total_tokens": 2}, "call_id": "scall_test1",
                   "reasoning_content": "2+2=4"}

        async def fake_chat_stream(**kwargs):
            raise AssertionError("stream_complete (chat) must not be called for responses channel")
            yield  # pragma: no cover — makes this an async generator

        with patch.object(gw._adapter, "stream_via_responses", side_effect=fake_stream), \
             patch.object(gw._adapter, "stream_complete", side_effect=fake_chat_stream):
            frames = []
            async for frame in gw.call_stream(
                messages=[{"role": "user", "content": "2+2=?"}],
                user_override="maas-icompify/qwen3.8-27b-responses",
            ):
                frames.append(frame)

        assert frames[0] == {"type": "token", "content": "4", "call_id": "scall_test1"}
        done = frames[-1]
        assert done["type"] == "done"
        assert done["reasoning_content"] == "2+2=4"


# ═══════════════════════════════════════════════════════════════════════
# Contract compatibility: call() return shape identical key-set + additive field
# ═══════════════════════════════════════════════════════════════════════

class TestReturnContractCompatibility:
    """The responses-channel success dict must carry the same keys as the existing
    openai-channel call() contract, plus the additive 'reasoning_content' key — callers
    must not need a different code path just because the responses channel was used."""

    @pytest.mark.asyncio
    async def test_responses_and_openai_success_dicts_share_key_set(self, monkeypatch):
        from app.services.model_gateway import ModelGateway
        from app.adapters.litellm_adapter import ModelCallResult

        monkeypatch.setenv("LLM_API_KEY", "sk-test-icompify")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
        gw = ModelGateway()

        openai_result = ModelCallResult(call_id="call_a", status="completed", content="ok",
                                        usage_summary={"total_tokens": 1})
        responses_result = ModelCallResult(call_id="call_b", status="completed", content="ok",
                                           reasoning_content="because", usage_summary={"total_tokens": 1})

        with patch.object(gw._adapter, "complete", new_callable=AsyncMock, return_value=openai_result):
            openai_res = await gw.call(
                messages=[{"role": "user", "content": "hi"}],
                user_override="deepseek-official/deepseek-v4-flash",
            )
        with patch.object(gw._adapter, "complete_via_responses", new_callable=AsyncMock,
                          return_value=responses_result):
            responses_res = await gw.call(
                messages=[{"role": "user", "content": "hi"}],
                user_override="maas-icompify/qwen3.8-27b-responses",
            )

        assert openai_res["status"] == "completed"
        assert responses_res["status"] == "completed"
        # responses 通道的 key 集合是 openai 通道的超集（只多 reasoning_content）
        assert set(openai_res.keys()) <= set(responses_res.keys())
        assert set(responses_res.keys()) - set(openai_res.keys()) <= {"reasoning_content"}
        assert responses_res["reasoning_content"] == "because"
        assert openai_res["reasoning_content"] == ""


# ═══════════════════════════════════════════════════════════════════════
# Real network integration test — NOT mocked. Skips honestly when no real key /
# when explicitly running in mock-LLM mode (R176_MOCK_LLM=1).
# ═══════════════════════════════════════════════════════════════════════

_HAS_REAL_ICOMPIFY_KEY = bool(os.environ.get("LLM_API_KEY")) and os.environ.get("R176_MOCK_LLM") != "1"


@pytest.mark.skipif(not _HAS_REAL_ICOMPIFY_KEY,
                    reason="No real LLM_API_KEY configured (or R176_MOCK_LLM=1) — "
                           "real-call verification for the Responses channel requires "
                           "a live maas-icompify credential (see .env).")
class TestRealResponsesChannelCall:
    """真实调用验证（不 mock，须真实发起网络请求）——B-R20-NO-RESPONSES-CHANNEL 的
    「须有真实调用证据方可标 implemented」要求的落地。Skip（不报错）当没有真实 Key。"""

    @pytest.mark.asyncio
    async def test_real_call_via_responses_channel(self):
        from app.services.model_gateway import ModelGateway

        gw = ModelGateway()
        res = await gw.call(
            messages=[{"role": "user", "content": "1+1=? Reply with only the digit."}],
            user_override="maas-icompify/qwen3.8-27b-responses",
            max_tokens=300,
            timeout=30,
        )

        assert res["status"] == "completed", (
            f"real Responses-channel call failed: "
            f"{res.get('error_category')} {res.get('error_message')}")
        assert res["content"].strip() != ""
        assert res["usage_summary"].get("total_tokens", 0) > 0
        # 这是 Responses 通道相对 chat completions 的核心增量价值：结构化 reasoning。
        assert isinstance(res.get("reasoning_content", ""), str)

    @pytest.mark.asyncio
    async def test_real_stream_via_responses_channel(self):
        from app.services.model_gateway import ModelGateway

        gw = ModelGateway()
        frames = []
        async for frame in gw.call_stream(
            messages=[{"role": "user", "content": "Say OK and nothing else."}],
            user_override="maas-icompify/qwen3.8-27b-responses",
            max_tokens=300,
            timeout=30,
        ):
            frames.append(frame)

        assert frames, "expected at least one frame from a real stream"
        assert frames[-1]["type"] == "done", f"stream ended with: {frames[-1]}"
        assert any(f["type"] == "token" for f in frames), "expected at least one real token frame"
