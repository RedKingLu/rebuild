"""Stage-level transient auto-retry (R17.5-P4-FIX 批2.7).

A P-stage's real work (handler.execute / WorkAgent.execute) drives one or more
streaming LLM calls through ModelGateway. Batch 2.6 already bounds a stalling
provider with a wall-clock ceiling (`litellm_adapter._STREAM_TOTAL_TIMEOUT`) and
does *committed-safe* pre-token retry inside a single call. But once a stream has
committed tokens and then stalls, the call fails honestly with a `timeout`
category — the whole stage returns status=failed. That is a provider **transient
stall**, not a logic defect, yet a linear driver aborts the entire P0→P4 chain on
the first such failure.

This module adds a bounded, backoff'd **stage-level** auto-retry that re-runs the
WHOLE stage when — and only when — it failed for a transient infra reason
(timeout / provider_unreachable / rate_limited). Non-transient failures
(bad_request / auth_failed / context_exceeded / logic / honest blocked) are
returned immediately: retrying them would only mask a real problem (§AGENTS 3.7).

It is a thin wrapper injected as `execute_fn`, so it composes with — and does not
replace — the existing ReviewPass rework loop (which handles *review* failures)
and the P4 NodeLoop per-node edge scheduling. The production `work` node and the
clean-run driver both go through this same helper.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("rebuild.stage_retry")

# error_category values produced by litellm_adapter._classify_litellm_error that
# represent a provider-side *transient* fault worth re-running the stage for.
# Mirrors the adapter's own per-call retry set (litellm_adapter.py:159/296).
TRANSIENT_CATEGORIES = frozenset({"timeout", "provider_unreachable", "rate_limited"})

# 结构化输出未解析（模型输出非确定性）——同一输入重跑常成功（真跑实证：P3 task_plans
# attempt1 parse_error、attempt2 completed）。非基础设施瞬态，但同属"重跑可自愈"，故纳入
# 可重试类别。空产出（no_task_plans）不在此列，不会误重试。
RETRYABLE_CATEGORIES = TRANSIENT_CATEGORIES | frozenset({"output_contract_parse_error"})

# Bounded stage retries + exponential backoff. Kept small: each retry re-runs the
# whole stage (potentially minutes of LLM work), so this is a last-resort safety
# net for infra blips, not a substitute for a healthy provider.
_MAX_TRANSIENT_RETRIES = int(os.environ.get("STAGE_TRANSIENT_MAX_RETRIES", "2"))
_RETRY_BASE_DELAY = float(os.environ.get("STAGE_TRANSIENT_RETRY_BASE_DELAY", "3.0"))

# reason-text fallbacks: when a handler surfaces only a human reason (no explicit
# error_category), these substrings still identify a transient stall honestly.
_TRANSIENT_REASON_HINTS = (
    "timed out", "timeout", "unreachable", "connection", "rate limit", "rate_limited",
)


def _extract_category(result: dict) -> str:
    """Return the transient-classification category a stage result carries, if any."""
    for key in ("model_error_category", "error_category"):
        cat = result.get(key)
        if cat:
            return str(cat)
    return ""


def is_transient_stage_failure(result) -> bool:
    """True iff `result` is a failed stage caused by a transient infra fault.

    A stage is considered failed when status is falsy or ∈ {failed, blocked} AND it
    carries a transient error_category (preferred, explicit) or — lacking one — a
    reason whose text names a transient network/timeout condition.
    """
    if not isinstance(result, dict):
        return False
    status = (result.get("status") or "").lower()
    # `completed` is success; anything else is a candidate. We only ever retry when
    # a transient signal is present, so `blocked`/`failed`/`""` are all admissible.
    if status == "completed":
        return False
    cat = _extract_category(result)
    if cat:
        return cat in RETRYABLE_CATEGORIES
    # No explicit category → fall back to reason text (honest best-effort).
    reason = (result.get("reason") or "").lower()
    return any(hint in reason for hint in _TRANSIENT_REASON_HINTS)


async def run_stage_with_transient_retry(
    *,
    stage: str,
    execute_fn: Callable[[], "dict | Awaitable[dict]"],
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    tracer=None,
    auditor=None,
    project_id: str = "",
    run_id: str = "",
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict:
    """Run one stage; auto-retry the WHOLE stage on transient infra failure.

    Args:
        stage: stage id (p0..p6), for logging/trace scoping.
        execute_fn: sync or async callable returning the stage result dict
            (typically ``lambda: handler.execute(state)``). Re-invoked per retry.
        max_retries: bounded extra attempts after the first (default env/2).
        base_delay: exponential backoff base seconds (default env/3.0).
        tracer/auditor: optional observability sinks (advisory; never fatal).
        sleep: injectable for tests (default asyncio.sleep).

    Returns:
        The last stage result dict. On success or non-transient failure it is
        returned immediately; on exhausted transient retries the final (still
        failed) result is returned so the caller escalates honestly.
    """
    _max = _MAX_TRANSIENT_RETRIES if max_retries is None else max_retries
    _base = _RETRY_BASE_DELAY if base_delay is None else base_delay

    attempt = 0
    result: dict = {}
    while True:
        r = execute_fn()
        result = await r if asyncio.iscoroutine(r) or hasattr(r, "__await__") else r

        if not is_transient_stage_failure(result):
            # success, non-transient failure, or honest blocked → do not retry.
            return result

        if attempt >= _max:
            # transient but budget exhausted → return last failed result honestly.
            logger.warning(
                "stage %s transient failure persisted after %d retries (category=%s)",
                stage, _max, _extract_category(result) or "reason-inferred")
            _trace(tracer, stage, project_id, run_id, action="transient_retry_exhausted",
                   attempt=attempt, category=_extract_category(result))
            _audit(auditor, stage, project_id, run_id, attempt=attempt,
                   category=_extract_category(result), exhausted=True)
            return result

        delay = _base * (2 ** attempt)
        attempt += 1
        cat = _extract_category(result) or "reason-inferred"
        logger.warning(
            "stage %s transient failure (category=%s) → auto-retry %d/%d after %.1fs",
            stage, cat, attempt, _max, delay)
        _trace(tracer, stage, project_id, run_id, action="transient_retry",
               attempt=attempt, max_retries=_max, delay=delay, category=cat)
        _audit(auditor, stage, project_id, run_id, attempt=attempt,
               category=cat, exhausted=False)
        await sleep(delay)


def _trace(tracer, stage, project_id, run_id, *, action, **extra):
    if tracer is None:
        return
    try:
        tracer.write(
            "stage_retry", action=action,
            summary=f"{stage} 瞬断自动重试 {action} (attempt={extra.get('attempt')})",
            project_id=project_id or None, run_id=run_id or None, stage=stage, **extra)
    except Exception:
        logger.debug("stage_retry trace 写入失败（advisory）", exc_info=True)


def _audit(auditor, stage, project_id, run_id, *, attempt, category, exhausted):
    if auditor is None:
        return
    try:
        auditor.write(
            audit_type="stage_transient_retry",
            action="retry_exhausted" if exhausted else "auto_retry",
            decision="retry", risk_level="L1",
            project_id=project_id or None, run_id=run_id or None, stage=stage,
            reason=(f"{stage} 瞬断类失败（{category}）"
                    + (f"，已达重试上限 {attempt} 次仍失败" if exhausted
                       else f"，自动重跑整阶段第 {attempt} 次")))
    except Exception:
        logger.debug("stage_retry audit 写入失败（advisory）", exc_info=True)
