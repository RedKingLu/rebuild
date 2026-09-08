"""R21 施工批次②：轮次上限可覆盖 + "这一轮为什么能继续"写入 Trace.

背景（dsh 范式吸收）：一个"目标"的持久状态（这一轮是否还在进行）和"谁/为何有权推进
下一轮"应是两个独立的记录点。本文件验证两处改动：

  1. `run_stage_tool_loop`（stage_agent_loop.py）的 `max_rounds` 与 `NodeLoop`
     （node_loop.py，经其驱动的 ReviewPass 内核）的轮次上限，都是「调用方可显式覆盖、
     不传则维持既有默认值」的可选参数——不是全局配置文件，也不是新的调度组件。
  2. 两个循环在每次进入新一轮之前，把真实存在的推进依据（读代码确认，非臆造）写入既有
     Trace 机制的 `advancement_basis` 字段，取值只有两种：
       - within_round_budget：仍在轮次预算内，正常进入
       - round_budget_exhausted：轮次预算耗尽，强制中止/收尾

不依赖真实工具注册表/DB（stage_agent_loop 部分 monkeypatch `_load_tools`/`_run_tool`，
同 test_r21_loop_hygiene_repeat_tool_call.py 的范式）；NodeLoop 部分不传 db，走
TaskNodeRun 持久化的降级路径（advisory，同 test_node_loop.py 里未传 db 的用例）。
asyncio_mode=auto（pyproject）。
"""

import inspect
import json
import uuid

import pytest

from app.services import stage_agent_loop as sal
from app.services.node_loop import NodeLoop, NodeSpec
from app.services.review_pass import ReviewPass
from app.core.trace_writer import TraceWriter


# ══════════════════════════════════════════════════════════════════════════
# 1. max_rounds 默认值不变 + 可被调用方显式覆盖
# ══════════════════════════════════════════════════════════════════════════

def test_run_stage_tool_loop_default_max_rounds_unchanged():
    """签名默认值仍是既有的 6（_MAX_TOOL_ROUNDS），不传参调用方行为不变。"""
    sig = inspect.signature(sal.run_stage_tool_loop)
    assert sig.parameters["max_rounds"].default == 6 == sal._MAX_TOOL_ROUNDS


def test_node_loop_default_max_rounds_unchanged():
    """NodeLoop 构造函数默认值仍是既有的 2，不传参调用方行为不变。"""
    sig = inspect.signature(NodeLoop.__init__)
    assert sig.parameters["max_rounds"].default == 2
    loop = NodeLoop()
    assert loop.max_rounds == 2


# ── fake call_stream 帧构造（同 test_r21_loop_hygiene_repeat_tool_call.py 范式）──

class _TCFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, index, tid, name, arguments):
        self.index = index
        self.id = tid
        self.function = _TCFn(name, arguments)


class _AlwaysToolCallGateway:
    """每一轮都发起一次工具调用，从不自行收尾——用来把循环真正跑到轮次预算耗尽。
    forced-final 阶段 `tools=None`（工具被禁用）时改为直接吐出最终文本，与
    run_stage_tool_loop 的强制收尾调用契约一致。"""

    def __init__(self):
        self.round = 0
        self.calls = []

    async def call_stream(self, *, messages, tools=None, **kwargs):
        self.calls.append(list(messages))
        if tools is None:
            for ch in "forced-final-text":
                yield {"type": "token", "content": ch}
            yield {"type": "done", "model": "fake-model"}
            return
        idx = self.round
        self.round += 1
        tcs = [_TC(0, f"c{idx}", "list_files", json.dumps({"path": f"p{idx}"}))]
        yield {"type": "tool_calls", "tool_calls": tcs}
        yield {"type": "done", "model": "fake-model"}


class _ImmediateFinishGateway:
    """第 1 轮就不再调用工具，直接给出最终文本收尾。"""

    def __init__(self, text="已给出结论"):
        self.text = text
        self.calls = []

    async def call_stream(self, *, messages, tools=None, **kwargs):
        self.calls.append(list(messages))
        for ch in self.text:
            yield {"type": "token", "content": ch}
        yield {"type": "done", "model": "fake-model"}


@pytest.fixture(autouse=True)
def _stub_tools(monkeypatch):
    """去掉真实工具注册表/DB 依赖（本文件只测轮次预算 + Trace 字段，不测工具语义）。"""
    monkeypatch.setattr(sal, "_load_tools", lambda stage: [])

    async def _fake_run_tool(fn_name, fn_args, project_id, stage, run_id, tracer):
        return {"ok": True}

    monkeypatch.setattr(sal, "_run_tool", _fake_run_tool)


async def test_run_stage_tool_loop_max_rounds_override_takes_effect():
    """调用方传入非默认值（max_rounds=2）→ 真的按新值生效：跑满 2 轮工具调用后强制
    收尾合成，而不是默认的 6 轮。"""
    gw = _AlwaysToolCallGateway()

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-override",
        max_rounds=2)

    assert result["tool_rounds"] == 2, "应恰好跑 2 轮工具调用（覆盖值），而非默认的 6"
    assert result["status"] == "completed"
    assert result["content"] == "forced-final-text", "第 3 次调用是强制收尾（tools=None）"
    assert gw.round == 2, "只应发起 2 轮真实工具调用"


async def test_run_stage_tool_loop_default_max_rounds_behavior_unchanged():
    """不传 max_rounds → 仍是默认的 6 轮上限（覆盖能力不改变既有默认行为）。"""
    gw = _AlwaysToolCallGateway()

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-default")

    assert result["tool_rounds"] == 6
    assert gw.round == 6


# ══════════════════════════════════════════════════════════════════════════
# 2. run_stage_tool_loop：advancement_basis 写入 Trace
# ══════════════════════════════════════════════════════════════════════════

async def test_stage_tool_loop_writes_within_round_budget_when_finishing_early():
    """第 1 轮模型直接收尾（不调用工具）→ 应写入恰好 1 条 within_round_budget 记录，
    不应有 round_budget_exhausted 记录。"""
    tracer = TraceWriter()
    gw = _ImmediateFinishGateway()

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-early",
        run_id="run-early", stage="p1", tracer=tracer)

    assert result["status"] == "completed"
    entries = [t for t in tracer._traces if t["trace_type"] == "stage_tool_loop"]
    assert len(entries) == 1
    e = entries[0]
    assert e["advancement_basis"] == sal.ADVANCE_WITHIN_ROUND_BUDGET == "within_round_budget"
    assert e["action"] == "within_round_budget"
    assert e["round"] == 1 and e["max_rounds"] == 6
    assert e["project_id"] == "p-early" and e["run_id"] == "run-early" and e["stage"] == "p1"


async def test_stage_tool_loop_writes_round_budget_exhausted_when_budget_spent():
    """max_rounds=3 且模型每轮都调用工具（从不自行收尾）→ 3 条 within_round_budget
    （round 1/2/3）+ 恰好 1 条 round_budget_exhausted（强制收尾前写入）。"""
    tracer = TraceWriter()
    gw = _AlwaysToolCallGateway()

    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-exhausted",
        run_id="run-exhausted", stage="p2", tracer=tracer, max_rounds=3)

    assert result["tool_rounds"] == 3
    entries = [t for t in tracer._traces if t["trace_type"] == "stage_tool_loop"]
    within = [e for e in entries if e["advancement_basis"] == "within_round_budget"]
    exhausted = [e for e in entries if e["advancement_basis"] == "round_budget_exhausted"]
    assert [e["round"] for e in within] == [1, 2, 3]
    assert all(e["max_rounds"] == 3 for e in within)
    assert len(exhausted) == 1
    assert exhausted[0]["round"] == 3 and exhausted[0]["max_rounds"] == 3
    assert exhausted[0]["action"] == "round_budget_exhausted"
    # 写入顺序：本轮的 round_budget_exhausted 记录必须在强制收尾调用之前（即紧跟在
    # 第 3 条 within_round_budget 之后），不是事后补记。
    assert entries.index(exhausted[0]) == len(entries) - 1


async def test_stage_tool_loop_no_tracer_does_not_raise():
    """tracer=None（未注入）→ 不应抛异常，行为与改动前一致。"""
    gw = _ImmediateFinishGateway()
    result = await sal.run_stage_tool_loop(
        gw, system_content="sys", user_content="usr", project_id="p-no-tracer")
    assert result["status"] == "completed"


# ══════════════════════════════════════════════════════════════════════════
# 3. NodeLoop / ReviewPass：max_rounds 覆盖 + advancement_basis 写入 Trace
# ══════════════════════════════════════════════════════════════════════════

def _node_spec(**over) -> NodeSpec:
    base = dict(
        node_id="tn-adv", stage="p3", project_id="proj-adv",
        task_plan={"task_plan_id": "tp-adv", "objective": "验证推进依据",
                   "expected_artifacts": ["plan"], "expected_evidence": ["ev"]},
        acceptance_criteria=["crit-a"],
        permission_boundary="workspace_write",
        risk_level="L1", mode="plan",
    )
    base.update(over)
    return NodeSpec(**base)


async def _always_bad_execute():
    """execute_fn 本身成功产出结果，但 self_check 会一直判定失败——用来把 ReviewPass
    真正跑到轮次预算耗尽（而不是执行异常触发的另一条路径）。"""
    return {
        "summary": "done", "artifacts": ["a"], "evidence": [{"evidence_id": "ev-1"}],
        "trace_refs": ["t"], "criteria_met": {"crit-a": True},
    }


def _always_fail_self_check(_result: dict) -> list[str]:
    return ["尚未满足验收标准"]


async def test_node_loop_max_rounds_override_takes_effect():
    """NodeLoop(max_rounds=1)（覆盖默认的 2）→ ReviewPass 只跑 1 轮就 escalate。"""
    tracer = TraceWriter()
    loop = NodeLoop(tracer=tracer, max_rounds=1)
    pid = f"proj-adv-{uuid.uuid4().hex[:8]}"
    await loop.run(_node_spec(project_id=pid), execute_fn=_always_bad_execute,
                  self_check_fn=_always_fail_self_check)

    entries = [t for t in tracer._traces if t["trace_type"] == "review_pass"
              and t["project_id"] == pid]
    within = [e for e in entries if e.get("advancement_basis") == "within_round_budget"]
    exhausted = [e for e in entries if e.get("advancement_basis") == "round_budget_exhausted"]
    assert len(within) == 1, "覆盖为 1 轮 → 只应有 1 条 round_start 记录"
    assert len(exhausted) == 1


async def test_node_loop_default_max_rounds_behavior_unchanged():
    """不传 max_rounds → NodeLoop 仍走既有默认值 2，ReviewPass 跑满 2 轮再 escalate。"""
    tracer = TraceWriter()
    loop = NodeLoop(tracer=tracer)
    assert loop.max_rounds == 2
    pid = f"proj-adv-{uuid.uuid4().hex[:8]}"
    await loop.run(_node_spec(project_id=pid), execute_fn=_always_bad_execute,
                  self_check_fn=_always_fail_self_check)

    entries = [t for t in tracer._traces if t["trace_type"] == "review_pass"
              and t["project_id"] == pid]
    within = [e for e in entries if e.get("advancement_basis") == "within_round_budget"]
    exhausted = [e for e in entries if e.get("advancement_basis") == "round_budget_exhausted"]
    assert len(within) == 2, "默认 2 轮 → 应有 2 条 round_start 记录"
    assert len(exhausted) == 1


async def test_review_pass_writes_within_round_budget_on_pass():
    """哪怕 review 首轮就通过（不走 escalate），round_start 也应带 advancement_basis
    （字段不是只在失败路径才出现）。"""
    tracer = TraceWriter()
    rp = ReviewPass(max_rounds=2, tracer=tracer)

    from app.services.review_pass import ReviewResult

    async def _execute():
        return {"ok": True}

    def _review_pass_fn(_result):
        return ReviewResult(passed=True)

    outcome = await rp.run(execute_fn=_execute, review_fn=_review_pass_fn,
                           project_id="p-pass", stage="p1")

    assert outcome["passed"] is True
    entries = [t for t in tracer._traces if t["trace_type"] == "review_pass"]
    round_starts = [e for e in entries if e["action"] == "round_start"]
    assert len(round_starts) == 1, "首轮即通过 → 只应有 1 条 round_start（round_end 另计）"
    assert round_starts[0].get("advancement_basis") == "within_round_budget"


async def test_review_pass_escalate_writes_round_budget_exhausted_and_audit():
    """轮次预算耗尽 → Trace 记 round_budget_exhausted，且既有的 Audit escalate 记录
    （审计维度）不受影响，两者并存。"""
    from app.core.audit_writer import AuditWriter
    from app.services.review_pass import ReviewResult

    tracer = TraceWriter()
    auditor = AuditWriter()
    rp = ReviewPass(max_rounds=1, tracer=tracer, auditor=auditor)

    async def _execute():
        return {"ok": False}

    def _always_fail(_result):
        return ReviewResult(passed=False, issues=[{"detail": "still failing"}])

    outcome = await rp.run(execute_fn=_execute, review_fn=_always_fail,
                           project_id="p-escalate", stage="p1")

    assert outcome["escalated_to_gate"] is True
    trace_entries = [t for t in tracer._traces if t["trace_type"] == "review_pass"]
    exhausted = [e for e in trace_entries if e.get("advancement_basis") == "round_budget_exhausted"]
    assert len(exhausted) == 1
    assert exhausted[0]["action"] == "round_budget_exhausted"

    audit_entries = [a for a in auditor._audits
                     if a.get("audit_type") == "review_escalation"]
    assert len(audit_entries) == 1, "既有 Audit escalate 记录未被本次改动破坏"
