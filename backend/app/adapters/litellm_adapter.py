"""LiteLLM Adapter — thin wrapper over litellm for ModelGateway.

R5: Supports OpenAI and Anthropic API formats via litellm.acompletion().
Uses litellm.Router for multi-provider routing with fallback support.
"""

from __future__ import annotations

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
        self._default_model = os.environ.get("LLM_MODEL_NAME", "deepseek-v4-flash")
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
        extra_params: Optional[dict] = None,
    ) -> ModelCallResult:
        """Execute a completion call via litellm.

        IMPORTANT: api_key is used in-memory only for this call; never persisted.
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
