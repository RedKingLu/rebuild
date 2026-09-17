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

# ── V26.2 返工批次二（§2.2 anthropic 协议防御）───────────────────────────────
# 本批次起 `max_tokens=None` 表示"不设平台侧上限"，请求体里**不写该键**（见
# `_apply_max_tokens`）。但 **Anthropic Messages API 的 `max_tokens` 是必填字段**，省略会直接
# 400 —— 所以只要 api_format=="anthropic"，就不能省略，必须回落到一个显式上限。
# 现状（2026-09-16 实测 model_profiles.yaml）：三个 provider 的 api_format 全为 "openai"，
# 无一走 anthropic 分支 ⇒ 本护栏当前不改变任何真实调用行为。但 `endpoint_anthropic` 已配置、
# `_select_api_base()` 支持该分支且其注释明写"非封闭枚举、预期会扩展" ⇒ 一旦有人把某
# provider 切成 anthropic，缺这个护栏就是该 provider 全部调用直接失败。
# 回落值的依据（不是拍脑袋的新数字）：取平台自己曾用过的**最高一档**预算 32768（批次 B/F 为
# P1 profiling / P3 planning / P4 生成设定的默认值，其推导见各处注释），而不是另立一个数。
# 可经 env 覆盖：某模型上限更高/更低时不必改代码。
_ANTHROPIC_FALLBACK_MAX_TOKENS = int(
    os.environ.get("LLM_ANTHROPIC_FALLBACK_MAX_TOKENS", "32768"))
_ANTHROPIC_FALLBACK_REASON = (
    "anthropic 协议要求 max_tokens 必填，本次调用未设平台侧上限，"
    f"已回落到显式上限 {_ANTHROPIC_FALLBACK_MAX_TOKENS}"
    "（可经 LLM_ANTHROPIC_FALLBACK_MAX_TOKENS 调整）"
)


def _resolve_max_tokens(api_format: str, max_tokens: Optional[int]) -> tuple[Optional[int], dict]:
    """决定本次请求实际使用的 max_tokens，并返回回落说明（空 dict = 未发生回落）。

    - 非 anthropic 协议：原样返回（None 表示后续不写该键）。
    - anthropic 协议且未设上限：回落到 `_ANTHROPIC_FALLBACK_MAX_TOKENS`，并给出结构化
      回落说明供调用方记录（**不静默省略导致 400，也不静默用一个无依据的数字**）。
    """
    if max_tokens is not None or api_format != "anthropic":
        return max_tokens, {}
    logger.warning("max_tokens 回落：%s", _ANTHROPIC_FALLBACK_REASON)
    return _ANTHROPIC_FALLBACK_MAX_TOKENS, {
        "max_tokens_fallback": {
            "applied": True,
            "api_format": api_format,
            "value": _ANTHROPIC_FALLBACK_MAX_TOKENS,
            "reason": _ANTHROPIC_FALLBACK_REASON,
            "env_knob": "LLM_ANTHROPIC_FALLBACK_MAX_TOKENS",
        }
    }


def _apply_max_tokens(kwargs: dict, key: str, max_tokens: Optional[int]) -> None:
    """**条件写入**输出上限键：仅在非 None 时写。

    为什么不能"传 None 就完事"（V26.2 返工批次二实测前提②）：旧实现无条件写
    `{"max_tokens": max_tokens}`，传 None 会把 `max_tokens: None` 真的发给 litellm，行为
    依赖库版本 / provider 实现（可能报 400、可能当 0、可能忽略），不可靠。取消预算的正确
    物理形态是**请求体里根本没有这个键**，不是这个键的值是 null。
    """
    if max_tokens is not None:
        kwargs[key] = max_tokens


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


class CallHangTimeout(TimeoutError):
    """非流式调用挂起保护触发（B-R19-1-COMPLETE-HANG）。

    与 `StreamStallTimeout` 同一模式（TimeoutError 子类 + phase + limit_seconds），只是
    口径为非流式，故消息不写 "streaming"。同样归入既有 "timeout" 错误分类 —— 不新造
    error_category（否则重试白名单与 gateway 的 pre-token / 顺位回退判断会同时失效）。
      request — 单次请求（建连 + 等完整响应体）超过硬边界。由 adapter 自己用
                asyncio.wait_for 兜底，**不依赖 litellm 内部 timeout**（该链已实测
                不可靠：服务端已关流、lastrcv≈497s 客户端仍阻塞在 socket read）
      total   — 单次 complete() 调用的总时长硬上限，**覆盖全部重试 attempt**
    """

    def __init__(self, phase: str, limit_seconds: float):
        self.phase = phase
        self.limit_seconds = limit_seconds
        super().__init__(
            f"Model call hung: no response within {limit_seconds:.1f}s (phase={phase})")


@dataclass
class ModelCallResult:
    """Result of a model call through the adapter."""
    call_id: str = ""
    provider_id: str = ""
    profile_id: str = ""
    model_name: str = ""  # litellm-normalized name (e.g. openai/deepseek-v4-flash)
    status: str = "unknown"  # completed / failed / blocked
    content: str = ""
    # B-R20-NO-RESPONSES-CHANNEL: Responses API 通道单独暴露的结构化 reasoning/thinking
    # 文本（与 content 分开，不混入正文）。仅 Responses 通道会填充；chat completions
    # 通道留空字符串（现有调用方无需处理新字段，向后兼容）。
    reasoning_content: str = ""
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
    if isinstance(e, (StreamStallTimeout, CallHangTimeout)):
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


# ── B-R20-NO-RESPONSES-CHANNEL: Codex/OpenAI Responses API 通道 helpers ──────
# 响应体形状实测（2026-09-08，真实 maas-icompify 调用，见交付报告的真实调用证据）：
# response.output = [ {type: "reasoning", content: [{type: "reasoning_text", text: "..."}]},
#                      {type: "message",  content: [{type: "output_text",   text: "..."}], role: "assistant"} ]
# response.usage = {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N,
#                    "reasoning_tokens": N, "prompt_tokens_details": None}  # flat dict
# 实测中 output 的每个 item / content part 都是**plain dict**（非 pydantic 对象），但防御性
# 地同时支持 attribute 访问，以防其它 litellm 版本或 provider 返回 typed 子对象。


def _get(obj, key: str, default=None):
    """Dict-or-attribute getter（见上方实测说明的二态防御）。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_responses_output(response) -> tuple[str, str]:
    """从 Responses API 响应的 `output` 列表拆出 (正文 content, 推理 reasoning_content)。

    type=="message"（或 part.type=="output_text"）的文本拼成正文；
    type=="reasoning"（或 part.type=="reasoning_text"）的文本拼成 reasoning ——
    两者严格分开，不混入 content（任务要求：thinking 单独暴露，不污染正文）。
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for item in (_get(response, "output", None) or []):
        item_type = _get(item, "type", "")
        for part in (_get(item, "content", None) or []):
            text = _get(part, "text", "") or ""
            if not text:
                continue
            part_type = _get(part, "type", "")
            if item_type == "reasoning" or part_type == "reasoning_text":
                reasoning_parts.append(text)
            elif item_type == "message" or part_type == "output_text":
                content_parts.append(text)
    return "".join(content_parts), "".join(reasoning_parts)


def _extract_responses_usage(usage_raw) -> dict:
    """Responses API 的 usage 实测为 flat dict，键名与 chat completions 的
    `usage.prompt_tokens/completion_tokens/total_tokens` 一致，额外带 reasoning_tokens
    （非 chat completions 惯用的 `completion_tokens_details.reasoning_tokens` 嵌套形状）。
    统一拍平成 adapter 既有 usage_summary 的标准三键 + 可选 reasoning_tokens。"""
    usage = {
        "prompt_tokens": _get(usage_raw, "prompt_tokens", 0) or 0,
        "completion_tokens": _get(usage_raw, "completion_tokens", 0) or 0,
        "total_tokens": _get(usage_raw, "total_tokens", 0) or 0,
    }
    reasoning_tokens = _get(usage_raw, "reasoning_tokens", None)
    if reasoning_tokens:
        usage["reasoning_tokens"] = reasoning_tokens
    return usage


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
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        stream: bool = False,
        timeout: Optional[float] = None,
        extra_params: Optional[dict] = None,
    ) -> ModelCallResult:
        """Execute a completion call via litellm.

        IMPORTANT: api_key is used in-memory only for this call; never persisted.

        V26.2 返工批次二（Q-B2-1）：`max_tokens=None`（默认）表示**不设平台侧输出上限** ——
        请求体里不写该键（`_apply_max_tokens`），上限交还给模型自身能力与本方法的时间护栏；
        anthropic 协议例外，见 `_resolve_max_tokens`。显式传值仍然生效（逃生阀）。

        R11-7: `timeout` overrides the module default per call — slow domains (e.g. P3
        planning, single call 60-120s) need a longer request timeout than the fail-fast
        default so they don't time out on a live-but-slow provider (B-P3-NO-TASKPLANS).

        B-R19-1-COMPLETE-HANG (外层超时兜底，与 stream_complete 同思路/同语义):
        旧实现的唯一时间边界是下面交给 litellm 的 `timeout` kwarg —— 即完全依赖 litellm/
        httpx 那条超时链，而该链已实测不可靠（服务端已关流、lastrcv≈497s 客户端仍阻塞在
        socket read），且随 provider / SDK 路径而变。链一失效 `complete()` 即可永久挂起。
        现补两层，**均在本层用 asyncio.wait_for 兜底，不依赖 litellm 内部 timeout**：
        1. **单次请求超时** = 调用方 `timeout`，否则 `_REQUEST_TIMEOUT` —— 与交给 litellm
           的那个值同源同语义（慢域显式声明更长等待）。
        2. **总时长硬上限** = 上述值与 `_STREAM_TOTAL_TIMEOUT` 的较大者；`deadline` 在
           **重试循环之外**计算，故上限覆盖整次调用含全部重试。若边界只落在单个 attempt
           上（旧实现即如此），最坏耗时 = 上限 ×(_MAX_RETRIES+1) + 退避 ≈ 254s，再乘
           gateway 顺位回退链长度与阶段轮次 —— 正是上一批实测的放大机制。预算不足时不再
           空转重试。阈值全部复用既有配置变量，零新增环境变量。
        超时按既有路径处理：抛 `CallHangTimeout` → `_classify_litellm_error` 归 "timeout"
        → 返回 `status="failed"` 的结果。**不静默返回空结果、不伪造完成**（D-097/公理3）。
        本方法对外仍不抛异常（gateway `call()` 依赖 `r.status` 逐个尝试 fallback，向外抛会
        击穿那条链）。
        """
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        t0 = time.monotonic()
        result = ModelCallResult(call_id=call_id, model_name=model)
        # anthropic 协议必填 max_tokens → 未设上限时回落并把原因带进 trace_data（不静默）。
        effective_max_tokens, fallback_trace = _resolve_max_tokens(api_format, max_tokens)
        if fallback_trace:
            result.trace_data.update(fallback_trace)

        # 单次请求超时：与下面交给 litellm 的值同源（双保险 —— litellm 那条链失效时本层兜住）。
        per_attempt_timeout = float(timeout) if timeout is not None else _REQUEST_TIMEOUT
        # 总上限：调用方声明的慢域上限与模块默认整体上限取大者；始终有限（兜底）。
        total_timeout = max(per_attempt_timeout, _STREAM_TOTAL_TIMEOUT)
        # deadline 在重试循环之外，故总上限覆盖整次调用（含全部 attempt 与退避），不被放大。
        deadline = t0 + total_timeout

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "api_key": api_key,
            "api_base": api_base,
            "stream": stream,
            # R9-5-1: never hang on a dead provider; R11-7: per-call override for slow domains
            "timeout": per_attempt_timeout,
        }
        _apply_max_tokens(kwargs, "max_tokens", effective_max_tokens)
        if extra_params:
            kwargs.update(extra_params)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CallHangTimeout("total", total_timeout)
                # 外层硬边界：单次请求超时，且不得超过剩余总预算。
                wait_budget = min(remaining, per_attempt_timeout)
                try:
                    response = await asyncio.wait_for(
                        litellm.acompletion(**kwargs), timeout=wait_budget)
                except (asyncio.TimeoutError, TimeoutError) as te:
                    raise CallHangTimeout("request", wait_budget) from te
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
                budget_left = deadline - time.monotonic()
                # 仅在剩余预算还够发起一次有意义的尝试时才重试（不空转重试出一次必然
                # 立刻超时的调用）；退避时长同样受剩余预算约束，不得越过总上限。
                if (attempt < _MAX_RETRIES
                        and error_cat in ("rate_limited", "timeout", "provider_unreachable")
                        and budget_left > _MIN_RETRY_BUDGET_SECONDS):
                    delay = min(_RETRY_BASE_DELAY * (2 ** attempt), budget_left)
                    logger.warning("litellm call retry %d/%d after %.1fs: %s", attempt + 1, _MAX_RETRIES, delay, error_cat)
                    await _async_sleep(delay)
                    continue

                latency = (time.monotonic() - t0) * 1000
                logger.warning(
                    "litellm call failed attempt=%d budget_left=%.1fs: %s %s",
                    attempt, budget_left, error_cat, error_msg)
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

    # ── Responses API call (B-R20-NO-RESPONSES-CHANNEL) ─────────────────

    async def complete_via_responses(
        self,
        *,
        model: str,  # litellm-normalized, e.g. "openai/qwen3.8-27b"
        messages: list[dict],
        api_base: str,
        api_key: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        timeout: Optional[float] = None,
    ) -> ModelCallResult:
        """Execute a completion call via litellm.aresponses() — Codex/OpenAI Responses API
        通道（D-065: 复用 litellm 现成的 aresponses()，不自造 HTTP 客户端）。

        与 complete() 参数解析共性一致（model/messages/api_base/api_key 均由调用方按同一套
        _resolve_key / resolve_api_model_name 解析出，见 ModelGateway.call()），但协议形状
        不同：
        - `messages` 直接作为 Responses API 的 `input` 传入 —— 已用真实 maas-icompify
          Responses 端点验证，litellm 接受与 chat completions 相同的 [{role, content}, ...]
          消息列表，无需额外转换（见交付报告真实调用证据）。
        - 响应体 `output` 是一组结构化 item（message / reasoning 等类型），用
          `_extract_responses_output` 拆出正文与 reasoning（分开返回，不混入 content）。
        - `usage` 是 flat dict（非 chat completions 的 `.usage` 属性对象），用
          `_extract_responses_usage` 统一拍平。

        如实上报的范围限定：本方法只做「单次请求超时 + 有限重试」，未移植 complete() 里
        B-R19-1-COMPLETE-HANG 那套「总时长跨重试预算」双层 CallHangTimeout 保护 —— 该保护
        是针对 complete()/stream_complete() 实测过的"服务端已关流、客户端仍阻塞"现象专门
        加的，Responses 通道目前只有 maas-icompify 一个真实 provider 验证过、未观测到同类
        现象。若后续实测该通道也会挂起，需要补齐同等力度的保护（当前已知缺口）。
        """
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        t0 = time.monotonic()
        result = ModelCallResult(call_id=call_id, model_name=model)
        per_attempt_timeout = float(timeout) if timeout is not None else _REQUEST_TIMEOUT

        kwargs: dict[str, Any] = {
            "model": model,
            "input": messages,
            "api_key": api_key,
            "api_base": api_base,
            "temperature": temperature,
            "timeout": per_attempt_timeout,
        }
        # V26.2 返工批次二：未设上限 ⇒ 请求体不含 max_output_tokens 键（Responses 通道口径）。
        _apply_max_tokens(kwargs, "max_output_tokens", max_tokens)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                try:
                    response = await asyncio.wait_for(
                        litellm.aresponses(**kwargs), timeout=per_attempt_timeout)
                except (asyncio.TimeoutError, TimeoutError) as te:
                    raise CallHangTimeout("request", per_attempt_timeout) from te

                latency = (time.monotonic() - t0) * 1000
                content, reasoning = _extract_responses_output(response)
                usage = _extract_responses_usage(_get(response, "usage", None) or {})

                result.status = "completed"
                result.content = content
                result.reasoning_content = reasoning
                result.latency_ms = round(latency, 1)
                result.usage_summary = usage
                result.retry_count = attempt
                return result

            except Exception as e:
                error_cat, error_msg = _classify_litellm_error(e)
                if (attempt < _MAX_RETRIES
                        and error_cat in ("rate_limited", "timeout", "provider_unreachable")):
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "litellm aresponses retry %d/%d after %.1fs: %s",
                        attempt + 1, _MAX_RETRIES, delay, error_cat)
                    await _async_sleep(delay)
                    continue

                latency = (time.monotonic() - t0) * 1000
                logger.warning("litellm aresponses failed attempt=%d: %s %s",
                               attempt, error_cat, error_msg)
                result.status = "failed"
                result.latency_ms = round(latency, 1)
                result.error_category = error_cat
                result.error_message = error_msg
                result.retry_count = attempt
                return result

        result.status = "failed"
        result.error_category = "max_retries_exceeded"
        result.error_message = "Max retries exceeded"
        result.retry_count = _MAX_RETRIES
        return result

    async def stream_via_responses(
        self,
        *,
        model: str,
        messages: list[dict],
        api_base: str,
        api_key: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        timeout: Optional[float] = None,
    ):
        """流式 Responses 通道（B-R20-NO-RESPONSES-CHANNEL）。解析 litellm
        aresponses(stream=True) 的原生 SSE 事件——真实 maas-icompify 调用实测事件序列：
        response.created → response.in_progress →
        response.output_item.added/done（reasoning 项先行，累积进 reasoning） →
        response.output_item.added（message 项）→ response.content_part.added →
        response.output_text.delta（×N，正文分片，逐个 yield token 帧）→
        response.output_text.done → response.content_part.done →
        response.output_item.done（message 项）→ response.completed（含最终 usage）。

        帧契约与 stream_complete() 保持一致（{"type": "token"/"done"/"error", ...}），
        额外在最终 "done" 帧上附 "reasoning_content" 键（累积的 reasoning 文本，不混入
        token 正文；上层可选读取，不读取也不影响既有帧处理逻辑，向后兼容）。

        如实上报的范围限定：与 stream_complete() 不同，本方法未移植 B-R18-1-STREAM-HANG
        的三层挂起保护（inter_chunk / first_token / total 超时 + pre-token 重试）—— 只对
        "建立流"这一步做了单次请求超时兜底，一旦进入 `async for` 迭代就没有 per-chunk
        超时边界。原因与 complete_via_responses 相同：该通道只有一个真实 provider 验证过，
        未观测到 stream_complete() 文档描述的挂起现象；若后续实测该通道也会挂起，需要补齐
        同等力度的保护（当前已知缺口）。同样未实现 tools（工具调用）参数透传 —— Responses
        API 的 tool 参数形状与 chat completions 不同，尚未验证，调用方若传了 tools 会被
        本方法忽略（不转换、不报错），这是另一个已知缺口。
        """
        call_id = f"scall_{uuid.uuid4().hex[:12]}"
        per_attempt_timeout = float(timeout) if timeout is not None else _REQUEST_TIMEOUT
        reasoning_buf = ""
        usage: dict = {}

        kwargs: dict[str, Any] = {
            "model": model,
            "input": messages,
            "api_key": api_key,
            "api_base": api_base,
            "temperature": temperature,
            "timeout": per_attempt_timeout,
            "stream": True,
        }
        # V26.2 返工批次二：未设上限 ⇒ 请求体不含 max_output_tokens 键。
        _apply_max_tokens(kwargs, "max_output_tokens", max_tokens)
        try:
            stream = await asyncio.wait_for(
                litellm.aresponses(**kwargs), timeout=per_attempt_timeout)
            async for ev in stream:
                ev_type_raw = getattr(ev, "type", "")
                ev_type = getattr(ev_type_raw, "value", None) or str(ev_type_raw)
                if ev_type == "response.output_text.delta":
                    delta = getattr(ev, "delta", "") or ""
                    if delta:
                        yield {"type": "token", "content": delta, "call_id": call_id}
                elif ev_type == "response.output_item.done":
                    item = getattr(ev, "item", None)
                    if _get(item, "type", "") == "reasoning":
                        for part in (_get(item, "content", None) or []):
                            reasoning_buf += _get(part, "text", "") or ""
                elif ev_type == "response.completed":
                    resp = getattr(ev, "response", None)
                    usage = _extract_responses_usage(_get(resp, "usage", None) or {})
            done_frame = {"type": "done", "usage": usage, "call_id": call_id}
            if reasoning_buf:
                done_frame["reasoning_content"] = reasoning_buf
            yield done_frame
        except Exception as e:
            error_cat, error_msg = _classify_litellm_error(e)
            logger.warning("stream_via_responses error: %s %s", error_cat, error_msg)
            yield {"type": "error", "error_category": error_cat,
                   "error_message": error_msg, "call_id": call_id}

    # ── Streaming call ────────────────────────────────────────────────

    async def stream_complete(
        self,
        *,
        model: str,
        messages: list[dict],
        api_base: str,
        api_key: str,
        api_format: str = "openai",
        max_tokens: Optional[int] = None,
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

        V26.2 返工批次二（Q-B2-1）：`max_tokens=None`（默认）= 不设平台侧输出上限，请求体里
        **不含** `max_tokens` 键。此时上述三层时间护栏 + 模型自身最大输出长度是**唯一**的输出
        边界 —— 故本批次明确禁止放宽这三层（取消 token 预算已经放宽了一个维度，两者同时放宽
        会让失控风险叠加）。anthropic 协议因该字段必填而回落，见 `_resolve_max_tokens`；回落
        原因随最终 `done` / `error` 帧的 `max_tokens_fallback` 键上报（不静默）。
        """
        effective_max_tokens, fallback_trace = _resolve_max_tokens(api_format, max_tokens)
        base_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "api_key": api_key,
            "api_base": api_base,
            "stream": True,
            "stream_options": {"include_usage": True},
            "timeout": timeout if timeout is not None else _REQUEST_TIMEOUT,
        }
        _apply_max_tokens(base_kwargs, "max_tokens", effective_max_tokens)
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
                yield {"type": "done", "usage": usage, "call_id": call_id, **fallback_trace}
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
                       "error_message": error_msg, "call_id": call_id, **fallback_trace}
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
        """Minimal connectivity test with fixed safe prompt.

        B-R20-NO-RESPONSES-CHANNEL: api_format=="responses" 走 complete_via_responses()
        （不同协议形状，不能复用 complete()）。
        """
        if api_format == "responses":
            return await self.complete_via_responses(
                model=model,
                messages=[{"role": "user", "content": "Hi"}],
                api_base=api_base,
                api_key=api_key,
                max_tokens=10,
                temperature=0.0,
            )
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
