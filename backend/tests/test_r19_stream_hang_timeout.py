"""B-R19-1-STREAM-HANG 回归测试 —— 流式模型调用的挂起保护（三层超时）。

背景（`证据/进度追踪/02-阻塞项.md` B-R18-1-STREAM-HANG）：真实 LLM 模式下 pytest 停在
P0/P1 真实图用例 ≥10 分钟无进展，`ss -tni` 显示到模型 API 的连接处于 CLOSE-WAIT、
`lastrcv≈497s`，进程 sleeping / CPU 0% ⇒ 阻塞在 socket read。

本文件锁定三层保护（超时必须诚实报错，不得无限等待、不得静默返回空结果 —— D-097/公理3）：
  1. **chunk 间读超时**（inter_chunk）—— 两个 chunk 之间静默超阈值即判定挂起（本缺陷核心）；
  2. **首字节超时**（first_token）—— 首个 chunk 迟迟不来；
  3. **总时长上限**（total）—— 单次流式调用的硬上限，且**不得被重试次数放大**。

全部用打桩的「慢流 / 不返回的流」验证，**绝不发真实 LLM 调用**（有成本且不可复现）。
夹具全部本文件自持，不改 tests/conftest.py。
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


# ── 打桩 chunk（形状对齐 litellm streaming chunk：choices[0].delta / usage）──────────

class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, content=None, tool_calls=None, usage=None, no_choices=False):
        self.choices = [] if no_choices else [_Choice(_Delta(content, tool_calls))]
        self.usage = usage


class _Usage:
    prompt_tokens = 3
    completion_tokens = 4
    total_tokens = 7


def _install_fake_acompletion(monkeypatch, stream_factory) -> list:
    """把 litellm.acompletion 换成打桩：返回 stream_factory() 产出的异步迭代器。

    返回一个 list（调用计数器），便于断言"重试/回退各发生几次"。
    """
    calls: list = []

    async def _fake_acompletion(**kwargs):
        calls.append(kwargs)
        return stream_factory(len(calls))

    monkeypatch.setattr(litellm, "acompletion", _fake_acompletion)
    return calls


async def _collect(agen) -> list:
    return [f async for f in agen]


def _adapter():
    return la.LiteLLMAdapter()


def _tighten(monkeypatch, *, request_timeout: float, total: float,
             max_retries: int = 0, base_delay: float = 0.01) -> None:
    """把阈值收紧到毫秒量级 —— 阈值来源仍是既有配置变量，测试只改其值。"""
    monkeypatch.setattr(la, "_REQUEST_TIMEOUT", request_timeout)
    monkeypatch.setattr(la, "_STREAM_TOTAL_TIMEOUT", total)
    monkeypatch.setattr(la, "_MAX_RETRIES", max_retries)
    monkeypatch.setattr(la, "_RETRY_BASE_DELAY", base_delay)


def _stream_kwargs() -> dict:
    """流式调用的最小入参（api_base/api_key 为打桩占位，绝不外发）。"""
    return {
        "model": "openai/stub-model",
        "messages": [{"role": "user", "content": "hi"}],
        "api_base": "http://stub.invalid",
        "api_key": "stub-key-not-real",
    }


# ═══════════════════════════════════════════════════════════════════════
# 第 1 层：chunk 间读超时（本缺陷核心）
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_inter_chunk_stall_yields_timeout_error_frame(monkeypatch):
    """流中途静默 → 必须在 chunk 间读超时处诚实报错，而不是等到总上限（或永远挂死）。

    总时长上限故意设为 30s（远大于断言窗口）以证明生效的是「chunk 间读超时」这一层。
    """
    async def _mid_stream_stall(_n):
        yield _Chunk(content="He")
        yield _Chunk(content="llo")
        await asyncio.sleep(_STALL)   # 服务端已静默 / 已关流，客户端仍在等读
        yield _Chunk(content="never")

    _tighten(monkeypatch, request_timeout=0.3, total=30.0, max_retries=0)
    _install_fake_acompletion(monkeypatch, _mid_stream_stall)

    frames = await asyncio.wait_for(
        _collect(_adapter().stream_complete(**_stream_kwargs())), timeout=5.0)

    types = [f["type"] for f in frames]
    assert "done" not in types, "静默挂起绝不能伪造完成（D-097/公理3）"
    assert types[-1] == "error", f"末帧必须是 error 帧，实际 {types}"
    assert frames[-1]["error_category"] == "timeout"
    assert "".join(f["content"] for f in frames if f["type"] == "token") == "Hello", \
        "超时前已产出的 token 必须原样透传，不得丢弃"


@pytest.mark.asyncio
async def test_committed_stall_never_dirty_retries(monkeypatch):
    """已 emit 过 token 后再挂起 → 只报错，绝不重试（重试会重复输出）。"""
    async def _mid_stream_stall(_n):
        yield _Chunk(content="partial")
        await asyncio.sleep(_STALL)

    _tighten(monkeypatch, request_timeout=0.3, total=30.0, max_retries=3)
    calls = _install_fake_acompletion(monkeypatch, _mid_stream_stall)

    frames = await asyncio.wait_for(
        _collect(_adapter().stream_complete(**_stream_kwargs())), timeout=5.0)

    assert len(calls) == 1, "已提交输出后不得脏重试"
    assert [f["type"] for f in frames].count("error") == 1
    assert frames[-1]["error_category"] == "timeout"


# ═══════════════════════════════════════════════════════════════════════
# 第 2 层：首字节超时
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_first_token_stall_yields_timeout_error_frame(monkeypatch):
    """流建立后首个 chunk 迟迟不来 → 首字节超时诚实报错（不等总上限）。"""
    async def _never_first_chunk(_n):
        await asyncio.sleep(_STALL)
        yield _Chunk(content="never")

    _tighten(monkeypatch, request_timeout=0.3, total=30.0, max_retries=0)
    _install_fake_acompletion(monkeypatch, _never_first_chunk)

    frames = await asyncio.wait_for(
        _collect(_adapter().stream_complete(**_stream_kwargs())), timeout=5.0)

    assert [f["type"] for f in frames] == ["error"]
    assert frames[0]["error_category"] == "timeout"
    assert frames[0]["error_message"], "必须给出可读的超时原因，不得空消息"


# ═══════════════════════════════════════════════════════════════════════
# 第 3 层：总时长上限（且不得被重试放大）
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_total_ceiling_is_call_scoped_not_per_attempt(monkeypatch):
    """总时长上限必须是「单次流式调用」级别的。

    旧实现把 deadline 放在重试循环**内部**，每次 attempt 各拿一份完整上限 ⇒ 实际最坏
    耗时 = 上限 ×(_MAX_RETRIES+1)（再乘以 gateway 的 fallback 链长度），这正是「≥10 分钟
    无进展」的放大机制。
    """
    async def _never_first_chunk(_n):
        await asyncio.sleep(_STALL)
        yield _Chunk(content="never")

    _tighten(monkeypatch, request_timeout=5.0, total=1.0, max_retries=3, base_delay=0.01)
    calls = _install_fake_acompletion(monkeypatch, _never_first_chunk)

    t0 = time.monotonic()
    frames = await asyncio.wait_for(
        _collect(_adapter().stream_complete(**_stream_kwargs())), timeout=15.0)
    elapsed = time.monotonic() - t0

    assert frames[-1]["type"] == "error"
    assert frames[-1]["error_category"] == "timeout"
    assert elapsed < 2.0, (
        f"总时长上限 1.0s 被重试放大到 {elapsed:.1f}s —— 上限必须覆盖整次调用（含重试）")
    assert len(calls) <= 2, f"预算耗尽后不得继续空转重试（实际 attempt 数 {len(calls)}）"


# ═══════════════════════════════════════════════════════════════════════
# 反向保护：正常（慢但持续推进）的流不得被误杀
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_slow_but_progressing_stream_completes_normally(monkeypatch):
    """每个 chunk 间隔都在阈值内、但总时长远超单个 chunk 阈值的正常慢流必须正常完成。"""
    async def _slow_ok(_n):
        for i in range(8):
            await asyncio.sleep(0.08)
            yield _Chunk(content=str(i))
        yield _Chunk(no_choices=True, usage=_Usage())

    _tighten(monkeypatch, request_timeout=0.5, total=20.0, max_retries=0)
    _install_fake_acompletion(monkeypatch, _slow_ok)

    frames = await asyncio.wait_for(
        _collect(_adapter().stream_complete(**_stream_kwargs())), timeout=10.0)

    assert frames[-1]["type"] == "done"
    assert not [f for f in frames if f["type"] == "error"]
    assert "".join(f["content"] for f in frames if f["type"] == "token") == "01234567"
    assert frames[-1]["usage"]["total_tokens"] == 7


@pytest.mark.asyncio
async def test_pre_token_stall_still_retries_then_succeeds(monkeypatch):
    """首字节前超时属瞬时错误 → 既有「未提交则退避重试」语义不得退化。"""
    async def _stall_then_ok(n):
        if n == 1:
            await asyncio.sleep(_STALL)
            yield _Chunk(content="never")
        else:
            yield _Chunk(content="ok")
            yield _Chunk(no_choices=True, usage=_Usage())

    _tighten(monkeypatch, request_timeout=0.3, total=20.0, max_retries=2, base_delay=0.01)
    calls = _install_fake_acompletion(monkeypatch, _stall_then_ok)

    frames = await asyncio.wait_for(
        _collect(_adapter().stream_complete(**_stream_kwargs())), timeout=10.0)

    assert len(calls) == 2, "首字节前超时必须允许一次干净重试"
    assert frames[-1]["type"] == "done"
    assert "".join(f["content"] for f in frames if f["type"] == "token") == "ok"


# ═══════════════════════════════════════════════════════════════════════
# Gateway 层：既有 fallback 语义（首 token 前 error → 按 strategy 换备用 profile）未退化
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gateway_falls_back_when_primary_profile_stalls_pre_token(monkeypatch):
    """主 profile 因流式挂起超时（首 token 前）→ 仍按策略回退到备用 profile 并正常完成。"""
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

    async def _stall_then_ok(n):
        if n == 1:
            await asyncio.sleep(_STALL)
            yield _Chunk(content="never")
        else:
            yield _Chunk(content="fine")
            yield _Chunk(no_choices=True, usage=_Usage())

    _tighten(monkeypatch, request_timeout=0.3, total=20.0, max_retries=0)
    calls = _install_fake_acompletion(monkeypatch, _stall_then_ok)

    frames = await asyncio.wait_for(
        _collect(gw.call_stream(messages=[{"role": "user", "content": "hi"}],
                                source="test-stream-hang")), timeout=10.0)

    assert len(calls) == 2, "主 profile 挂起超时后必须尝试 fallback profile"
    types = [f["type"] for f in frames]
    assert types[-1] == "done", f"回退后应正常完成，实际 {types}"
    assert "".join(f["content"] for f in frames if f["type"] == "token") == "fine"
    assert not [f for f in frames if f["type"] == "error"]


@pytest.mark.asyncio
async def test_gateway_surfaces_error_when_all_profiles_stall(monkeypatch):
    """所有 profile 都挂起超时 → 诚实 error 帧 + attempted_chain，绝不静默返回空完成。"""
    from app.services.model_gateway import ModelGateway

    gw = ModelGateway()
    profile, reason, provider = gw._registry.resolve_model(strategy_id="system-default")
    assert profile and provider

    monkeypatch.setattr(
        gw, "_build_profiles_to_try",
        lambda p, prov, r, sid, uo: [(profile, provider, reason, False)])
    monkeypatch.setattr(gw, "_resolve_key", lambda prov, explicit=False: ("stub-key", "env"))

    async def _always_stall(_n):
        await asyncio.sleep(_STALL)
        yield _Chunk(content="never")

    _tighten(monkeypatch, request_timeout=0.3, total=20.0, max_retries=0)
    _install_fake_acompletion(monkeypatch, _always_stall)

    frames = await asyncio.wait_for(
        _collect(gw.call_stream(messages=[{"role": "user", "content": "hi"}],
                                source="test-stream-hang-all")), timeout=10.0)

    assert [f["type"] for f in frames] == ["error"]
    assert frames[0]["error_category"] == "timeout"
    assert frames[0].get("model_unavailable") is True
    assert frames[0].get("attempted_chain"), "全失败必须给出已尝试模型链路"
