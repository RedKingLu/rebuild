"""B-R19-1-COMPLETE-HANG 回归测试 —— **非流式** `complete()` 的外层超时兜底。

背景（`证据/进度追踪/02-阻塞项.md` B-R18-1-STREAM-HANG 代码级根因末句）：上一批已为
`stream_complete` 补齐三层超时；**非流式 `complete()` 当时未改，无任何外层硬边界** ——
唯一边界是交给 litellm 的 `timeout` kwarg（per-request）。而同一阻塞项已实测确认该超时链
不可靠（`ss -tni` 显示 `lastrcv≈497s` 仍阻塞在 socket read），且随 provider / SDK 路径而变。
链一失效，`complete()` 即可**永久挂起**（调用方含 validation_agent / planning_service /
routes_models / p4_execution_worker / fusion 子调用）。

本文件锁定 `complete()` 的两层兜底（超时必须诚实报错，不得无限等待、不得静默返回空结果
或伪造完成 —— D-097 / 公理 3）：
  1. **单次请求超时**（phase=request）—— 用 `asyncio.wait_for` 在 adapter 自己这层兜底，
     **不依赖 litellm 内部 timeout**；
  2. **总时长硬上限**（phase=total）—— 覆盖整次调用**含全部重试**，不得被重试次数放大
     （上一批的核心教训：`deadline` 曾位于重试循环内部，上限被放大 N 倍）。

并含**反向保护**：正常但较慢的调用、以及调用方显式放宽 `timeout` 的慢域调用，都必须能
正常完成（防过度收紧误杀真实调用）。

打桩方式说明：`litellm.acompletion` 被换成「无视 timeout kwarg、直接静默 sleep」的假实现
—— 这正是缺陷现象的等价模型（litellm 内部超时链失效）。**绝不发真实 LLM 调用**（有成本
且不可复现）。夹具全部本文件自持，不改 tests/conftest.py。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import litellm  # noqa: E402
from app.adapters import litellm_adapter as la  # noqa: E402

_STALL = 3600.0  # 「永不返回」的静默时长（远大于任何测试断言窗口）


# ── 打桩响应（形状对齐 litellm 非流式响应：choices[0].message.content / usage）────────

class _Message:
    def __init__(self, content):
        self.content = content


class _RespChoice:
    def __init__(self, content):
        self.message = _Message(content)


class _Usage:
    prompt_tokens = 3
    completion_tokens = 4
    total_tokens = 7


class _Response:
    def __init__(self, content="ok", usage=None):
        self.choices = [_RespChoice(content)]
        self.usage = usage


def _install_fake_acompletion(monkeypatch, handler) -> list:
    """把 litellm.acompletion 换成打桩协程：第 n 次调用交给 handler(n) 决定行为。

    handler 是 async 函数，可 sleep（模拟挂起）或返回 _Response。**故意无视 kwargs
    里的 timeout** —— 模拟「litellm/httpx 超时链失效」这一实测缺陷现象。
    返回调用计数器 list，便于断言「重试 / 回退各发生几次」。
    """
    calls: list = []

    async def _fake_acompletion(**kwargs):
        calls.append(kwargs)
        return await handler(len(calls))

    monkeypatch.setattr(litellm, "acompletion", _fake_acompletion)
    return calls


def _adapter():
    return la.LiteLLMAdapter()


def _tighten(monkeypatch, *, request_timeout: float, total: float,
             max_retries: int = 0, base_delay: float = 0.01) -> None:
    """把阈值收紧到毫秒量级 —— 阈值来源仍是既有配置变量，测试只改其值。"""
    monkeypatch.setattr(la, "_REQUEST_TIMEOUT", request_timeout)
    monkeypatch.setattr(la, "_STREAM_TOTAL_TIMEOUT", total)
    monkeypatch.setattr(la, "_MAX_RETRIES", max_retries)
    monkeypatch.setattr(la, "_RETRY_BASE_DELAY", base_delay)


def _call_kwargs() -> dict:
    """非流式调用的最小入参（api_base/api_key 为打桩占位，绝不外发）。"""
    return {
        "model": "openai/stub-model",
        "messages": [{"role": "user", "content": "hi"}],
        "api_base": "http://stub.invalid",
        "api_key": "stub-key-not-real",
    }


async def _hang(_n):
    await asyncio.sleep(_STALL)
    return _Response("never")


# ═══════════════════════════════════════════════════════════════════════
# 第 1 层：单次请求超时（不依赖 litellm 内部 timeout）
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_complete_hang_times_out_instead_of_waiting_forever(monkeypatch):
    """litellm 挂死（内部 timeout 失效）→ `complete()` 必须自己超时并诚实失败。

    总上限故意设为 30s（远大于断言窗口）以证明生效的是「单次请求超时」这一层。
    修复前：`complete()` 无 asyncio.wait_for，会一直等打桩的 3600s ⇒ 本用例在外层
    5s 看门狗处抛 TimeoutError（真 RED）。
    """
    _tighten(monkeypatch, request_timeout=0.3, total=30.0, max_retries=0)
    _install_fake_acompletion(monkeypatch, _hang)

    result = await asyncio.wait_for(
        _adapter().complete(**_call_kwargs()), timeout=5.0)

    assert result.status == "failed", "挂起绝不能报 completed（D-097/公理3）"
    assert result.error_category == "timeout", \
        f"必须归入既有 timeout 分类（重试白名单/gateway 回退判断依赖它），实际 {result.error_category!r}"
    assert result.error_message, "必须给出可读的超时原因，不得空消息"
    assert result.content == "", "超时不得返回伪造内容"
    assert result.usage_summary == {}, "超时不得伪造 usage"


@pytest.mark.asyncio
async def test_complete_timeout_error_message_carries_no_secret(monkeypatch):
    """超时消息只含阶段与阈值，绝不回显 api_key（AGENTS §8 / §12.1）。"""
    _tighten(monkeypatch, request_timeout=0.2, total=30.0, max_retries=0)
    _install_fake_acompletion(monkeypatch, _hang)

    result = await asyncio.wait_for(
        _adapter().complete(**_call_kwargs()), timeout=5.0)

    assert "stub-key-not-real" not in result.error_message
    assert "stub-key-not-real" not in str(result.trace_data)


# ═══════════════════════════════════════════════════════════════════════
# 第 2 层：总时长上限（覆盖整次调用含全部重试，不得被重试放大）
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_complete_total_ceiling_is_call_scoped_not_per_attempt(monkeypatch):
    """总时长上限必须是「单次 complete() 调用」级别的，含全部重试 attempt。

    若上限（或唯一边界）落在单个 attempt 上，最坏耗时 = 上限 ×(_MAX_RETRIES+1) + 退避，
    再乘以 gateway 的 fallback 链长度 —— 正是上一批实测的「≥10 分钟无进展」放大机制。

    参数化说明：总上限（1.0s）必须**大于**单次请求超时（0.4s），否则会被 `max()` 抬回
    —— 上限按设计至少容得下一次完整尝试，不静默截断调用方声明的 `timeout`。退避基数
    刻意设为 0.5s，使「per-attempt 边界」与「call-scoped 边界」的耗时可区分：
      旧语义（仅 per-attempt）= 0.4×4 + (0.5+1.0+2.0) ≈ 5.1s；本语义 ≈ 1.0s。
    """
    _tighten(monkeypatch, request_timeout=0.4, total=1.0, max_retries=3, base_delay=0.5)
    calls = _install_fake_acompletion(monkeypatch, _hang)

    t0 = time.monotonic()
    result = await asyncio.wait_for(
        _adapter().complete(**_call_kwargs()), timeout=15.0)
    elapsed = time.monotonic() - t0

    assert result.status == "failed"
    assert result.error_category == "timeout"
    assert elapsed < 2.0, (
        f"总上限 1.0s 被重试放大到 {elapsed:.1f}s —— 上限必须覆盖整次调用（含重试）")
    assert len(calls) <= 3, f"预算耗尽后不得继续空转重试（实际 attempt 数 {len(calls)}）"


@pytest.mark.asyncio
async def test_complete_pre_response_timeout_still_retries_then_succeeds(monkeypatch):
    """超时属瞬时错误 → 既有「退避重试」语义不得退化（预算充足时仍应重试并成功）。"""
    async def _hang_then_ok(n):
        if n == 1:
            await asyncio.sleep(_STALL)
            return _Response("never")
        return _Response("recovered", usage=_Usage())

    _tighten(monkeypatch, request_timeout=0.3, total=20.0, max_retries=2, base_delay=0.01)
    calls = _install_fake_acompletion(monkeypatch, _hang_then_ok)

    result = await asyncio.wait_for(
        _adapter().complete(**_call_kwargs()), timeout=10.0)

    assert len(calls) == 2, "首次超时后必须允许一次重试"
    assert result.status == "completed"
    assert result.content == "recovered"
    assert result.retry_count == 1
    assert result.usage_summary["total_tokens"] == 7


# ═══════════════════════════════════════════════════════════════════════
# 反向保护：正常但较慢的调用不得被误杀
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_slow_but_successful_complete_is_not_killed(monkeypatch):
    """耗时接近但未超过阈值的正常调用必须正常完成（防过度收紧误杀真实调用）。"""
    async def _slow_ok(_n):
        await asyncio.sleep(0.4)
        return _Response("slow answer", usage=_Usage())

    _tighten(monkeypatch, request_timeout=2.0, total=20.0, max_retries=0)
    calls = _install_fake_acompletion(monkeypatch, _slow_ok)

    result = await asyncio.wait_for(
        _adapter().complete(**_call_kwargs()), timeout=10.0)

    assert len(calls) == 1
    assert result.status == "completed", f"正常慢调用被误杀：{result.error_message}"
    assert result.content == "slow answer"
    assert result.error_category == ""
    assert result.latency_ms > 0
    assert result.usage_summary["total_tokens"] == 7


@pytest.mark.asyncio
async def test_caller_timeout_override_widens_both_layers(monkeypatch):
    """慢域（P3 planning 等）显式传入更长 `timeout` 时，两层阈值都必须随之放宽。

    模块默认 `_REQUEST_TIMEOUT` 收到 0.2s（比调用耗时更紧），调用方声明 timeout=5.0
    ⇒ 必须按调用方口径成功，而不是按模块默认误杀。
    """
    async def _slow_ok(_n):
        await asyncio.sleep(0.8)
        return _Response("planning answer", usage=_Usage())

    _tighten(monkeypatch, request_timeout=0.2, total=20.0, max_retries=0)
    calls = _install_fake_acompletion(monkeypatch, _slow_ok)

    result = await asyncio.wait_for(
        _adapter().complete(timeout=5.0, **_call_kwargs()), timeout=10.0)

    assert len(calls) == 1
    assert result.status == "completed", f"调用方 timeout 未被尊重：{result.error_message}"
    assert result.content == "planning answer"


# ═══════════════════════════════════════════════════════════════════════
# Gateway 层：既有非流式运行时 fallback 语义（call()）未退化
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gateway_call_falls_back_when_primary_profile_hangs(monkeypatch):
    """主 profile 挂起超时 → `call()` 仍按策略回退到备用 profile 并正常完成。"""
    from app.services.model_gateway import ModelGateway

    gw = ModelGateway()
    profile, reason, provider = gw._registry.resolve_model(strategy_id="system-default")
    assert profile and provider, "registry 至少有一个已配置 provider"

    # 顺位尝试链：主 + 一个 fallback（用同一 profile 构造，确保测试不依赖本机凭据分布）
    monkeypatch.setattr(
        gw, "_build_profiles_to_try",
        lambda p, prov, r, sid, uo: [(profile, provider, reason, False),
                                     (profile, provider, "fallback:test", True)])
    monkeypatch.setattr(gw, "_resolve_key", lambda prov, explicit=False: ("stub-key", "env"))

    async def _hang_then_ok(n):
        if n == 1:
            await asyncio.sleep(_STALL)
            return _Response("never")
        return _Response("fine", usage=_Usage())

    _tighten(monkeypatch, request_timeout=0.3, total=20.0, max_retries=0)
    calls = _install_fake_acompletion(monkeypatch, _hang_then_ok)

    result = await asyncio.wait_for(
        gw.call(messages=[{"role": "user", "content": "hi"}], source="test-complete-hang"),
        timeout=10.0)

    assert len(calls) == 2, "主 profile 挂起超时后必须尝试 fallback profile"
    assert result["status"] == "completed", f"回退后应正常完成，实际 {result}"
    assert result["content"] == "fine"
    assert result["fallback_used"] is True
    assert len(result["attempted_chain"]) == 2


@pytest.mark.asyncio
async def test_gateway_call_surfaces_timeout_when_all_profiles_hang(monkeypatch):
    """所有 profile 都挂起超时 → 诚实 failed + timeout + attempted_chain，绝不假成功。"""
    from app.services.model_gateway import ModelGateway

    gw = ModelGateway()
    profile, reason, provider = gw._registry.resolve_model(strategy_id="system-default")
    assert profile and provider

    monkeypatch.setattr(
        gw, "_build_profiles_to_try",
        lambda p, prov, r, sid, uo: [(profile, provider, reason, False)])
    monkeypatch.setattr(gw, "_resolve_key", lambda prov, explicit=False: ("stub-key", "env"))

    _tighten(monkeypatch, request_timeout=0.3, total=20.0, max_retries=0)
    _install_fake_acompletion(monkeypatch, _hang)

    result = await asyncio.wait_for(
        gw.call(messages=[{"role": "user", "content": "hi"}], source="test-complete-hang-all"),
        timeout=10.0)

    assert result["status"] == "failed"
    assert result["error_category"] == "timeout"
    assert result["content"] == ""
    assert result["model_unavailable"] is True
    assert result["attempted_chain"], "全失败必须给出已尝试模型链路"
