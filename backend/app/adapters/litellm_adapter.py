"""LiteLLM Adapter — thin wrapper over litellm for ModelGateway.

R5: Supports OpenAI and Anthropic API formats via litellm.acompletion().
Uses litellm.Router for multi-provider routing with fallback support.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import litellm

logger = logging.getLogger("rebuild.litellm_adapter")

# ── Retry config (tunable via env) ───────────────────────────────────
_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "3"))
_RETRY_BASE_DELAY = float(os.environ.get("LLM_RETRY_BASE_DELAY", "2.0"))
# R9-5-1: hard request timeout so a non-responding provider FAILS FAST instead of
# hanging forever (公理3 失败必发声). Configurable via LLM_REQUEST_TIMEOUT (seconds).
_REQUEST_TIMEOUT = float(os.environ.get("LLM_REQUEST_TIMEOUT", "60"))
# R17.5-P4-FIX 批2.6: overall wall-clock ceiling for a STREAMING call. The per-chunk
# litellm `timeout` only bounds connect / single-chunk read — it does NOT bound the whole
# stream, so a slow-dripping / stalling provider could run 13+ minutes for ~7.5k tokens
# (real P0→P2 call_log: single calls of 791s / 531s). This ceiling wraps the entire stream
# iteration so a stalled stream FAILS FAST and can be retried/fallen back. A caller may pass
# a longer per-call `timeout` for legitimately slow stages (P3 planning / P4 multi-file
# nodes); we honour it as the ceiling — but the ceiling is always finite (never unbounded).
# Configurable via LLM_STREAM_TOTAL_TIMEOUT (seconds).
_STREAM_TOTAL_TIMEOUT = float(os.environ.get("LLM_STREAM_TOTAL_TIMEOUT", "240"))
# B-R18-1-STREAM-HANG: 剩余预算低于此值就不再发起新的重试请求（否则只是空转出一次
# 必然立刻超时的调用）。非策略参数，不做可配置（Skill §3.3 不必要的可配置性）。
_MIN_RETRY_BUDGET_SECONDS = 1.0


class StreamStallTimeout(TimeoutError):
    """流式调用挂起保护触发（B-R18-1-STREAM-HANG）。

    TimeoutError 子类 —— 既有 `_classify_litellm_error` / 重试与回退语义按 "timeout"
    瞬时错误处理，不变；同时携带 phase 便于日志与上层识别到底是哪一层保护生效：
      first_token — 流已建立但首个 chunk 迟迟不来
      inter_chunk — 两个 chunk 之间静默超阈值（服务端已关流 / 静默，客户端仍在等读）
      total       — 单次流式调用的总时长硬上限（含重试）
    """

    def __init__(self, phase: str, limit_seconds: float):
        self.phase = phase
        self.limit_seconds = limit_seconds
        super().__init__(
            f"Streaming stalled: no data within {limit_seconds:.1f}s (phase={phase})")


@dataclass
class ModelCallResult:
    """Result of a model call through the adapter."""
    call_id: str = ""
    provider_id: str = ""
    profile_id: str = ""
    model_name: str = ""  # litellm-normalized name (e.g. openai/deepseek-v4-flash)
    status: str = "unknown"  # completed / failed / blocked
    content: str = ""
    latency_ms: float = 0.0
    error_category: str = ""
    error_message: str = ""  # redacted — never contains Key
    usage_summary: dict = field(default_factory=dict)
    retry_count: int = 0
    fallback_used: bool = False
    fallback_from: str = ""
    trace_data: dict = field(default_factory=dict)


def _classify_litellm_error(e: Exception) -> tuple[str, str]:
    """Classify a litellm exception into error_category + redacted message."""
    if isinstance(e, StreamStallTimeout):
        # 挂起保护：保留 timeout 分类（既有重试/回退语义不变），消息带上具体哪一层 + 阈值。
        return "timeout", str(e)
    error_str = str(e)
    if "AuthenticationError" in type(e).__name__ or "auth" in error_str.lower():
        return "auth_failed", "Authentication failed — credentials invalid or expired"
    if "RateLimitError" in type(e).__name__ or "rate" in error_str.lower():
        return "rate_limited", "Rate limited by provider"
    if "Timeout" in type(e).__name__ or "timeout" in error_str.lower():
        return "timeout", "Request timed out"
    if "APIConnectionError" in type(e).__name__ or "connection" in error_str.lower():
        return "provider_unreachable", "Provider endpoint unreachable"
    if "BadRequestError" in type(e).__name__:
        # May contain model name — redact specifics
        return "bad_request", "Invalid request — check model name and parameters"
    if "ContextWindowExceededError" in type(e).__name__:
        return "context_exceeded", "Input exceeds model context window"
    return "model_call_failed", "Model call failed"


class LiteLLMAdapter:
    """Thin adapter over litellm for ModelGateway.

    Supports:
    - Multi-provider routing via litellm.acompletion()
    - OpenAI and Anthropic API formats
    - Error classification and redaction
    - Fallback on failure
    - Usage tracking callbacks
    """

    def __init__(self):
        # Toggle litellm debug based on env
        litellm.suppress_debug_info = True
        if os.environ.get("LITELLM_LOG", "") == "DEBUG":
            litellm.set_verbose = True

    # ── Main call ────────────────────────────────────────────────────

    async def complete(
        self,
        *,
        model: str,  # litellm-normalized: "openai/deepseek-v4-flash"
        messages: list[dict],
        api_base: str,
        api_key: str,
        api_format: str = "openai",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
        timeout: Optional[float] = None,
        extra_params: Optional[dict] = None,
    ) -> ModelCallResult:
        """Execute a completion call via litellm.

        IMPORTANT: api_key is used in-memory only for this call; never persisted.

        R11-7: `timeout` overrides the module default per call — slow domains (e.g. P3
        planning, single call 60-120s) need a longer request timeout than the fail-fast
        default so they don't time out on a live-but-slow provider (B-P3-NO-TASKPLANS).
        """
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        t0 = time.monotonic()
        result = ModelCallResult(call_id=call_id, model_name=model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "api_key": api_key,
            "api_base": api_base,
            "stream": stream,
            # R9-5-1: never hang on a dead provider; R11-7: per-call override for slow domains
            "timeout": timeout if timeout is not None else _REQUEST_TIMEOUT,
        }
        if extra_params:
            kwargs.update(extra_params)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await litellm.acompletion(**kwargs)
                latency = (time.monotonic() - t0) * 1000
                content = response.choices[0].message.content or ""

                # Extract usage
                usage = {}
                if hasattr(response, "usage") and response.usage:
                    usage = {
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                        "total_tokens": getattr(response.usage, "total_tokens", 0),
                    }

                result.status = "completed"
                result.content = content
                result.latency_ms = round(latency, 1)
                result.usage_summary = usage
                result.retry_count = attempt
                return result

            except Exception as e:
                error_cat, error_msg = _classify_litellm_error(e)
                if attempt < _MAX_RETRIES and error_cat in ("rate_limited", "timeout", "provider_unreachable"):
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning("litellm call retry %d/%d after %.1fs: %s", attempt + 1, _MAX_RETRIES, delay, error_cat)
                    await _async_sleep(delay)
                    continue

                latency = (time.monotonic() - t0) * 1000
                result.status = "failed"
                result.latency_ms = round(latency, 1)
                result.error_category = error_cat
                result.error_message = error_msg
                result.retry_count = attempt
                return result

        # Should not reach here, but handle edge case
        result.status = "failed"
        result.error_category = "max_retries_exceeded"
        result.error_message = "Max retries exceeded"
        result.retry_count = _MAX_RETRIES
        return result

    # ── Streaming call ────────────────────────────────────────────────

    async def stream_complete(
        self,
        *,
        model: str,
        messages: list[dict],
        api_base: str,
        api_key: str,
        api_format: str = "openai",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tools: Optional[list[dict]] = None,
        extra_params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ):
        """Stream a completion as an async generator yielding dicts.

        Yields:
          {"type": "token", "content": "<str>"}          — text delta
          {"type": "tool_calls", "tool_calls": [...]}     — tool call chunks (accumulated per chunk)
          {"type": "usage", "usage": {...}}               — final usage (last chunk)
          {"type": "done", "usage": {...}}                — stream complete sentinel
          {"type": "error", "error_category": "...", "error_message": "..."} — failure

        Chunk parsing logic adapted from AgentLoop real streaming (三步法吸收, T1).
        Once any token has been yielded, errors emit an error frame — no silent swallow (公理3).

        R17.5-P4-FIX 批2.6 (performance / robustness hardening):
        - Overall wall-clock ceiling (`_STREAM_TOTAL_TIMEOUT`, or the caller's per-call
          `timeout` when longer) wraps the WHOLE stream iteration via asyncio.wait_for, so a
          slow-dripping / stalling provider FAILS FAST at the ceiling (timeout) instead of
          running many minutes — the per-chunk litellm timeout alone cannot bound this.
        - Backoff retry (up to `_MAX_RETRIES`, mirroring complete()) for transient failures
          (timeout / rate_limited / provider_unreachable) — BUT only while nothing has been
          committed to the consumer yet. Once a token / tool_calls frame has been yielded we
          NEVER dirty-retry (would duplicate output); we emit an honest error frame instead.

        B-R18-1-STREAM-HANG (挂起保护三层，超时诚实报错、绝不无限等待 / 绝不伪造完成):
        1. **chunk 间读超时** = `_REQUEST_TIMEOUT` (LLM_REQUEST_TIMEOUT, 默认 60s)。两个
           chunk 之间静默超过该阈值即判定挂起（实测缺陷现象：服务端已关流、连接 CLOSE-WAIT，
           客户端仍阻塞在 socket read 近 500s）。**故意不随调用方的长 `timeout` 放大** ——
           调用方的 `timeout` 表达的是「本阶段整体可以很慢」，不是「允许流中途静默更久」。
        2. **首字节超时** = 调用方 `timeout`，否则 `_REQUEST_TIMEOUT` —— 即已交给 litellm
           的那个「单次请求超时」，语义一致（慢域 P3/P4 显式声明更长的首字节等待）。
        3. **总时长上限** = 上述 ceiling，且 deadline 在**重试循环之外**计算 —— 旧实现每个
           attempt 各拿一份完整上限，最坏耗时 = 上限 ×(_MAX_RETRIES+1) 再乘 gateway 的
           fallback 链长度（240s → 单 profile 约 974s，多 profile 达数十分钟），正是「≥10
           分钟无进展」的放大机制。预算不足时不再空转重试。
        """
        base_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "api_key": api_key,
            "api_base": api_base,
            "stream": True,
            "stream_options": {"include_usage": True},
            "timeout": timeout if timeout is not None else _REQUEST_TIMEOUT,
        }
        if tools:
            base_kwargs["tools"] = tools
            base_kwargs["tool_choice"] = "auto"
        if extra_params:
            base_kwargs.update(extra_params)

        # Wall-clock ceiling for the entire stream. Honour a longer per-call timeout (slow
        # stages), else the module default; always finite (兜底).
        stream_total_timeout = (
            max(float(timeout), _STREAM_TOTAL_TIMEOUT) if timeout is not None
            else _STREAM_TOTAL_TIMEOUT
        )
        # 挂起保护阈值（见 docstring；阈值来源均为既有配置变量，不新造环境变量）。
        first_token_timeout = float(timeout) if timeout is not None else _REQUEST_TIMEOUT
        inter_chunk_timeout = _REQUEST_TIMEOUT
        # 总上限 deadline 覆盖整次调用（含全部重试 attempt），不在循环内重置。
        deadline = time.monotonic() + stream_total_timeout

        # Committed = a token/tool_calls frame has been yielded to the consumer. Once True,
        # a clean retry is impossible (would duplicate already-emitted output) → error frame.
        committed = False
        for attempt in range(_MAX_RETRIES + 1):
            call_id = f"scall_{uuid.uuid4().hex[:12]}"
            usage: dict = {}
            # R17.5-P4-FIX 批2.8: reasoning-model bypass. kimi/deepseek 等推理模型把推理链
            # 走 delta.reasoning_content、最终答案走 delta.content。若只读 delta.content，
            # 推理阶段会产出 0 个 token 帧，上层误判 empty_content（P3 强制合成实测：满 8192
            # completion_tokens 但 final_text 为空）。这里累积 reasoning_content 作兜底：
            #   - 正常有 content → 按原逻辑 emit token，reasoning 只旁路累积、绝不混入答案；
            #   - 整段流结束仍无任何 content token 且非工具轮 → 把 reasoning 作为最终内容 emit，
            #     避免把"其实产出了内容、只是落在 reasoning 流"误判成空失败。
            reasoning_buf = ""
            content_emitted = False   # 本次流是否 emit 过真正的 delta.content token
            tool_emitted = False      # 本次流是否 emit 过 tool_calls（工具轮不注入 reasoning 旁白）
            chunk_seen = False        # 本次流是否收到过任意 chunk（区分「首字节」与「chunk 间」）
            response = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StreamStallTimeout("total", stream_total_timeout)
                response = await asyncio.wait_for(
                    litellm.acompletion(**base_kwargs),
                    timeout=min(remaining, first_token_timeout),
                )
                stream_iter = response.__aiter__()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise StreamStallTimeout("total", stream_total_timeout)
                    # 首字节 vs chunk 间读超时，且均不得超过剩余总预算。
                    gap_limit = inter_chunk_timeout if chunk_seen else first_token_timeout
                    wait_budget = min(remaining, gap_limit)
                    try:
                        chunk = await asyncio.wait_for(
                            stream_iter.__anext__(), timeout=wait_budget)
                    except StopAsyncIteration:
                        break
                    except (asyncio.TimeoutError, TimeoutError) as te:
                        raise StreamStallTimeout(
                            "inter_chunk" if chunk_seen else "first_token",
                            wait_budget) from te
                    chunk_seen = True
                    if not chunk.choices:
                        # Usage-only final chunk
                        if hasattr(chunk, "usage") and chunk.usage:
                            usage = {
                                "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                                "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                                "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                            }
                        continue
                    delta = chunk.choices[0].delta
                    if delta is None:
                        continue
                    # Text token
                    if delta.content:
                        committed = True
                        content_emitted = True
                        yield {"type": "token", "content": delta.content, "call_id": call_id}
                    else:
                        # 推理模型旁路：content 为空的这一帧若带 reasoning_content，不静默丢弃，
                        # 累积起来作兜底（仅当整段流最终没有任何 content 时才使用，不污染答案）。
                        rc = getattr(delta, "reasoning_content", None)
                        if rc:
                            reasoning_buf += rc
                    # Tool call delta
                    if delta.tool_calls:
                        committed = True
                        tool_emitted = True
                        yield {"type": "tool_calls", "tool_calls": delta.tool_calls, "call_id": call_id}
                    # Usage in delta (some providers)
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage = {
                            "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                            "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                            "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                        }
                # 兜底：整段流结束、没吐过任何真正 content token、也不是工具轮，但推理流有内容
                # → 把 reasoning_content 作为最终答案 emit，避免上层假 empty_content 失败。
                # （对某些推理模型，最终 JSON 就落在 reasoning 流里；extract_json_object 能从
                # 散文/围栏中提取锚点 JSON。有 content 或有工具调用时绝不注入，防污染。）
                if not content_emitted and not tool_emitted and reasoning_buf.strip():
                    committed = True
                    yield {"type": "token", "content": reasoning_buf, "call_id": call_id}
                yield {"type": "done", "usage": usage, "call_id": call_id}
                return
            except Exception as e:
                error_cat, error_msg = _classify_litellm_error(e)
                budget_left = deadline - time.monotonic()
                # Only clean-retry when nothing was emitted yet (公理3: no dirty retry) and
                # the call-scoped budget still allows a meaningful attempt (不空转重试)。
                if (not committed and attempt < _MAX_RETRIES
                        and error_cat in ("rate_limited", "timeout", "provider_unreachable")
                        and budget_left > _MIN_RETRY_BUDGET_SECONDS):
                    delay = min(_RETRY_BASE_DELAY * (2 ** attempt), budget_left)
                    logger.warning(
                        "stream_complete pre-token retry %d/%d after %.1fs: %s (%s)",
                        attempt + 1, _MAX_RETRIES, delay, error_cat, error_msg)
                    await _aclose_stream(response)
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "stream_complete error committed=%s attempt=%d budget_left=%.1fs: %s %s",
                    committed, attempt, budget_left, error_cat, error_msg)
                await _aclose_stream(response)
                yield {"type": "error", "error_category": error_cat,
                       "error_message": error_msg, "call_id": call_id}
                return

    # ── Self-test / connectivity check ────────────────────────────────

    async def test_connectivity(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        api_format: str = "openai",
    ) -> ModelCallResult:
        """Minimal connectivity test with fixed safe prompt."""
        return await self.complete(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            api_base=api_base,
            api_key=api_key,
            api_format=api_format,
            max_tokens=10,
            temperature=0.0,
        )


async def _async_sleep(seconds: float) -> None:
    """Async sleep helper."""
    import asyncio
    await asyncio.sleep(seconds)


async def _aclose_stream(response) -> None:
    """Best-effort close of a litellm streaming response so a stalled/aborted stream does
    not leak the underlying connection before a retry or on error. Never raises."""
    if response is None:
        return
    for attr in ("aclose", "close"):
        fn = getattr(response, attr, None)
        if fn is None:
            continue
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass
        return
