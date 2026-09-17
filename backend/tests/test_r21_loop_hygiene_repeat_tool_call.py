"""R21 dsh 范式吸收「循环卫生」① —— 重复工具调用检测（stage_agent_loop.run_stage_tool_loop）.

背景：dsh 有一个简单但实用的"循环卫生"设计——模型在多轮工具调用里重复完全相同的
调用（同一工具名 + 同一参数）时，提醒它换方法或收尾。这里验证 rebuild 的
`run_stage_tool_loop`（P0-P3 通用工具循环）新增的等价能力：

  ① 连续 N（默认 3）轮完全相同的 (工具名, 参数) 调用 → 第 N+1 轮发给模型的消息历史
     里出现一条通用提醒（不含任何项目/场景特定内容）。
  ② 调用不同工具、或同一工具但参数不同 → 不触发提醒。
  ③ 提醒只是"提醒"，不是"拦截"——命中阈值的那次工具调用仍照常执行。

不依赖真实工具注册表/DB：monkeypatch 掉 `_load_tools`/`_run_tool`（本文件只测循环
本身的重复检测逻辑，工具执行的语义由别处的测试覆盖）。asyncio_mode=auto（pyproject）。
"""

import json

import pytest

from app.services import stage_agent_loop as sal


# ── fake call_stream 帧构造（同 test_r11_c4_p4_worker._TCFn/_TC 范式）───────

class _TCFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, index, tid, name, arguments):
        self.index = index
        self.id = tid
        self.function = _TCFn(name, arguments)


class _ScriptedToolCallGateway:
    """按 round_specs 逐轮回放工具调用；某轮 spec 为空/None 时该轮直接给出最终文本收尾。

    round_specs: list[list[tuple[tool_name, args_dict]]]
    captured_messages: 每轮 call_stream 被调用瞬间的 messages 快照（用于断言提醒是否
    出现在"下一轮"发给模型的消息历史里）。
    """

    def __init__(self, round_specs, final_text="已基于已有信息给出结论"):
        self.round_specs = round_specs
        self.final_text = final_text
        self.round = 0
        self.captured_messages: list[list[dict]] = []

    async def call_stream(self, *, messages, tools=None, **kwargs):
        self.captured_messages.append(list(messages))
        idx = self.round
        self.round += 1
        spec = self.round_specs[idx] if idx < len(self.round_specs) else None
        if not spec:
            for ch in self.final_text:
                yield {"type": "token", "content": ch}
            yield {"type": "done", "model": "fake-model"}
            return
        tcs = [_TC(i, f"c{idx}-{i}", name, json.dumps(args, ensure_ascii=False))
               for i, (name, args) in enumerate(spec)]
        yield {"type": "tool_calls", "tool_calls": tcs}
        yield {"type": "done", "model": "fake-model"}


@pytest.fixture(autouse=True)
def _stub_tools(monkeypatch):
    """去掉真实工具注册表/DB 依赖：本文件只测循环的重复检测逻辑，不测工具执行语义。"""
    monkeypatch.setattr(sal, "_load_tools", lambda stage: [])
    calls: list[tuple[str, dict]] = []

    async def _fake_run_tool(fn_name, fn_args, project_id, stage, run_id, tracer):
        calls.append((fn_name, fn_args))
        return {"ok": True}

    monkeypatch.setattr(sal, "_run_tool", _fake_run_tool)
    return calls


def _reminder(n: int) -> str:
    return sal._REPEAT_TOOL_CALL_REMINDER_TEMPLATE.format(n=n)


def _has_reminder(messages: list[dict], n: int | None = None) -> bool:
    if n is not None:
        target = _reminder(n)
        return any(m.get("content") == target for m in messages)
    return any("连续" in (m.get("content") or "") and "完全相同的工具" in (m.get("content") or "")
               for m in messages)


# ── ① 连续 3 次完全相同的 (工具名, 参数) → 第 4 轮消息历史出现提醒 ──────────

async def test_three_consecutive_identical_calls_trigger_reminder_next_round(_stub_tools):
    gw = _ScriptedToolCallGateway([
        [("list_files", {"path": "source"})],
        [("list_files", {"path": "source"})],
        [("list_files", {"path": "source"})],
        None,  # 第 4 轮：模型收尾，不再调用工具
    ])

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-repeat")

    assert result["status"] == "completed"
    assert result["content"] == "已基于已有信息给出结论"
    assert len(gw.captured_messages) == 4, "应恰好跑 4 轮（3 次重复调用 + 1 次收尾）"

    # 前 3 轮发给模型的消息里都不应该有提醒（还没连续到阈值）
    assert not _has_reminder(gw.captured_messages[0])
    assert not _has_reminder(gw.captured_messages[1])
    assert not _has_reminder(gw.captured_messages[2])
    # 第 4 轮（= 第 N+1 轮）发给模型的消息历史里必须出现提醒，且措辞通用（无场景特定内容）
    assert _has_reminder(gw.captured_messages[3], n=3), (
        f"第 4 轮消息历史应含重复调用提醒，实际：{gw.captured_messages[3]}")

    # 提醒只是提醒，不是拦截——3 次工具调用必须全部真实执行
    assert len(_stub_tools) == 3
    assert all(c == ("list_files", {"path": "source"}) for c in _stub_tools)


async def test_reminder_keeps_firing_while_repetition_continues(_stub_tools):
    """连续调用超过阈值后仍继续重复 → 后续每一轮都应带上最新的提醒（不是只提醒一次就沉默）。"""
    gw = _ScriptedToolCallGateway([
        [("list_files", {"path": "source"})],
        [("list_files", {"path": "source"})],
        [("list_files", {"path": "source"})],
        [("list_files", {"path": "source"})],  # 第 4 次重复
        None,
    ])

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-repeat2")

    assert result["status"] == "completed"
    assert _has_reminder(gw.captured_messages[3], n=3)
    assert _has_reminder(gw.captured_messages[4], n=4)


# ── ② 每轮调用不同工具 → 不触发提醒 ─────────────────────────────────────

async def test_different_tool_each_round_no_reminder(_stub_tools):
    gw = _ScriptedToolCallGateway([
        [("list_files", {"path": "source"})],
        [("code_grep", {"pattern": "TODO"})],
        [("fs_read", {"path": "source/a.py"})],
        None,
    ])

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-diff-tool")

    assert result["status"] == "completed"
    for msgs in gw.captured_messages:
        assert not _has_reminder(msgs)
    assert len(_stub_tools) == 3


# ── ③ 同一工具但参数不同 → 不触发提醒 ───────────────────────────────────

async def test_same_tool_different_args_no_reminder(_stub_tools):
    gw = _ScriptedToolCallGateway([
        [("list_files", {"path": "a"})],
        [("list_files", {"path": "b"})],
        [("list_files", {"path": "c"})],
        None,
    ])

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-diff-args")

    assert result["status"] == "completed"
    for msgs in gw.captured_messages:
        assert not _has_reminder(msgs)
    assert len(_stub_tools) == 3


# ── ④ 交替调用打断连续性 → 不触发提醒（即便总次数达到阈值也不算"连续"）───

async def test_alternating_calls_reset_streak_no_reminder(_stub_tools):
    gw = _ScriptedToolCallGateway([
        [("list_files", {"path": "source"})],
        [("code_grep", {"pattern": "x"})],
        [("list_files", {"path": "source"})],  # 与第一轮相同，但被中间一轮打断
        [("code_grep", {"pattern": "x"})],
        None,
    ])

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-alternating")

    assert result["status"] == "completed"
    for msgs in gw.captured_messages:
        assert not _has_reminder(msgs)
