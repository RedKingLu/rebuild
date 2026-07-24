"""Stage-level transient auto-retry tests (R17.5-P4-FIX 批2.7).

Proves: transient infra failure (timeout / provider_unreachable / rate_limited)
re-runs the WHOLE stage with bounded backoff and succeeds; non-transient failure
(bad_request / auth / logic / honest blocked) is NOT retried (would mask a real
problem); budget is bounded and the last failed result is returned honestly.
"""

import pytest

from app.graph.stage_retry import (
    is_transient_stage_failure,
    run_stage_with_transient_retry,
    TRANSIENT_CATEGORIES,
)


# ── classifier ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cat", sorted(TRANSIENT_CATEGORIES))
def test_transient_categories_classified(cat):
    assert is_transient_stage_failure({"status": "failed", "model_error_category": cat})
    assert is_transient_stage_failure({"status": "blocked", "error_category": cat})


@pytest.mark.parametrize("cat", ["bad_request", "auth_failed", "context_exceeded",
                                 "model_call_failed"])
def test_non_transient_categories_not_retried(cat):
    assert not is_transient_stage_failure({"status": "failed", "model_error_category": cat})


def test_completed_is_never_transient():
    assert not is_transient_stage_failure(
        {"status": "completed", "model_error_category": "timeout"})


def test_reason_text_fallback():
    # no explicit category → transient inferred from reason text
    assert is_transient_stage_failure({"status": "failed", "reason": "Request timed out"})
    assert is_transient_stage_failure({"status": "blocked", "reason": "Provider endpoint unreachable"})
    # honest blocked with a non-transient reason → not retried
    assert not is_transient_stage_failure(
        {"status": "blocked", "reason": "无接地证据，诚实 blocked"})


def test_non_dict_is_not_transient():
    assert not is_transient_stage_failure(None)
    assert not is_transient_stage_failure("failed")


# ── retry driver ───────────────────────────────────────────────────────────
class _Sleeps:
    def __init__(self):
        self.delays = []

    async def __call__(self, d):
        self.delays.append(d)


@pytest.mark.asyncio
async def test_transient_then_success_retries_and_succeeds():
    calls = {"n": 0}
    sleeps = _Sleeps()

    def execute():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "failed", "model_error_category": "timeout",
                    "reason": "Request timed out"}
        return {"status": "completed", "artifacts": ["a"]}

    result = await run_stage_with_transient_retry(
        stage="p3", execute_fn=execute, max_retries=2, base_delay=1.0, sleep=sleeps)

    assert result["status"] == "completed"
    assert calls["n"] == 2                       # one retry
    assert sleeps.delays == [1.0]                # backoff applied once


@pytest.mark.asyncio
async def test_non_transient_not_retried():
    calls = {"n": 0}
    sleeps = _Sleeps()

    def execute():
        calls["n"] += 1
        return {"status": "failed", "model_error_category": "bad_request",
                "reason": "Invalid request"}

    result = await run_stage_with_transient_retry(
        stage="p3", execute_fn=execute, max_retries=2, base_delay=1.0, sleep=sleeps)

    assert result["status"] == "failed"
    assert calls["n"] == 1                        # NOT retried
    assert sleeps.delays == []


@pytest.mark.asyncio
async def test_transient_exhausts_budget_returns_last_failure():
    calls = {"n": 0}
    sleeps = _Sleeps()

    def execute():
        calls["n"] += 1
        return {"status": "failed", "model_error_category": "provider_unreachable",
                "reason": "Provider endpoint unreachable"}

    result = await run_stage_with_transient_retry(
        stage="p4", execute_fn=execute, max_retries=2, base_delay=2.0, sleep=sleeps)

    assert result["status"] == "failed"           # honest final failure
    assert result["model_error_category"] == "provider_unreachable"
    assert calls["n"] == 3                         # first + 2 retries
    assert sleeps.delays == [2.0, 4.0]             # exponential backoff


@pytest.mark.asyncio
async def test_async_execute_fn_supported():
    calls = {"n": 0}

    async def execute():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "failed", "model_error_category": "rate_limited"}
        return {"status": "completed"}

    result = await run_stage_with_transient_retry(
        stage="p1", execute_fn=execute, max_retries=1, base_delay=0.0, sleep=_Sleeps())
    assert result["status"] == "completed"
    assert calls["n"] == 2
