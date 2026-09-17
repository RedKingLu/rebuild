"""FlowRuntime — thin wrapper over the compiled orchestration graph (R9-5-1, T6).

Lazily compiles the StateGraph with the async SqliteSaver checkpointer on first use
(checkpointer must exist before compile, and creating it needs a running loop).
thread_id == run_id throughout (checkpoint.thread_config).

Exposes:
  start(run_id, init_state)        — begin a run; returns state (pauses at first Gate interrupt)
  resume(run_id, decision)         — resume after a Gate decision (approve/reject/request_changes)
  get_state(run_id)                — current checkpointed state snapshot
  astream_events(run_id, ...)      — real LangGraph event stream (backs SSE, R9-5-1 T13)
  capability_status()              — "live" once compiled, else "degraded" (公理4, no hardcode)
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from langgraph.types import Command

from app.graph.checkpoint import get_checkpointer, thread_config
from app.graph.graph import build_graph

logger = logging.getLogger("rebuild.graph.runtime")


class FlowRuntime:
    def __init__(self):
        self._compiled = None
        self._saver = None
        self._lock = asyncio.Lock()

    async def _graph(self):
        # R19-4：编译图把 checkpointer 编了进去并长期缓存。get_checkpointer() 现在会在
        # 健康检查失败时重建单例（例如 checkpoints 表被删、连接已死），此时旧编译图仍握着
        # 那条已弃用的 saver —— checkpoint 层"恢复"了而生产调用方仍不可用，即假恢复。
        # 故每次取图都比对 saver 身份，换了就重新编译（健康时 get_checkpointer 只多一次
        # 亚毫秒级探针查询，返回的是同一对象，不触发重编译）。
        saver = await get_checkpointer()
        if self._compiled is None or self._saver is not saver:
            async with self._lock:
                if self._compiled is None or self._saver is not saver:
                    self._compiled = build_graph().compile(checkpointer=saver)
                    self._saver = saver
        return self._compiled

    def capability_status(self) -> str:
        # "live" only when a real graph has been compiled; never hardcoded optimistic.
        return "live" if self._compiled is not None else "degraded"

    async def start(self, run_id: str, init_state: dict) -> dict:
        g = await self._graph()
        return await g.ainvoke(init_state, config=thread_config(run_id))

    async def resume(self, run_id: str, decision: str) -> dict:
        g = await self._graph()
        return await g.ainvoke(Command(resume=decision), config=thread_config(run_id))

    async def get_state(self, run_id: str):
        g = await self._graph()
        return await g.aget_state(thread_config(run_id))

    async def astream_events(self, run_id: str, init_state: Optional[dict] = None,
                             resume_decision: Optional[str] = None) -> AsyncIterator[dict]:
        """Stream real LangGraph events for a start or resume drive (backs SSE).

        Pass init_state to drive a fresh start, or resume_decision to drive a resume.
        """
        g = await self._graph()
        payload = (Command(resume=resume_decision)
                   if resume_decision is not None else init_state)
        async for ev in g.astream_events(payload, config=thread_config(run_id),
                                         version="v2"):
            yield ev


# process singleton accessor (also mounted on Services in dependencies.py)
_runtime: Optional[FlowRuntime] = None


def get_flow_runtime() -> FlowRuntime:
    global _runtime
    if _runtime is None:
        _runtime = FlowRuntime()
    return _runtime


async def graph_thread_active(run_id: str) -> bool:
    """True iff a checkpoint thread for `run_id` exists AND is paused at an interrupt.

    Used by the legacy run-lifecycle / promotion-decision routes (W9/W10) to decide
    whether a decision should drive the graph via resume (single thread, no double
    advancement) or fall back to direct state advancement for non-graph runs.
    Any error (no checkpointer, never-started thread) → False (legacy path).

    ⚠ 这是「run 级」判据：它只回答"这个 run 的线程停着吗"，**不回答**"停在哪个 Gate 上"。
    任何"把某个 Gate 的决策注入图 resume"的调用点都不能只用它做前置条件——否则决策会被
    应用到图自己暂停的那个 Gate 上（B-ACC-DECISION-CROSSGATE-INJECTION）。
    Gate 决策请用 graph_pending_gate_ids() 做同一性校验。
    """
    try:
        snap = await get_flow_runtime().get_state(run_id)
        return bool(getattr(snap, "next", ()))
    except Exception:
        return False


_GATE_NODE_SUFFIX = "_gate"


def pending_gate_ids_from_snapshot(snap) -> frozenset[str]:
    """从 LangGraph 状态快照解析「图此刻正暂停在哪个/哪些 Gate 上」的 gate_id 集合。

    为什么这个集合能判定同一性（B-ACC-DECISION-CROSSGATE-INJECTION 的核心）：
      · 全图只有一个 interrupt() 调用点——app/graph/nodes.py 的 {stage}_gate 节点，
        `interrupt({"gate_id": gate_id, "stage": stage, "type": gate_type})`。
        所以「图暂停点」必然携带一个 gate_id，就是恢复后 `_gate_backend.decide(gate_id=...)`
        会真正写入的那个 Gate（nodes.py 的 gate 节点先从 state["pending_gate"] 取
        gate_id，再用它调 decide）。
      · 两个独立来源必须一致才认账：
          ① snap.tasks[*].interrupts[*].value["gate_id"] —— interrupt() 当时传出的载荷；
          ② snap.values["pending_gate"]["gate_id"]        —— resume 重跑该节点时会读到的值。
        二者本应恒等（同一次节点执行里由同一个变量派生）。不一致说明快照语义已漂移，
        此时返回空集（fail closed：不驱动图），并发声告警——绝不猜。
      · DB 上的 checkpoint_ref / interrupt_ref **不能**用来判同一性：
        RealGateBackend.create() 对图创建的每个 Gate 都写 checkpoint_ref=run_id
        （gate_backend.py），同 run 的所有 Gate 取值完全相同，无法区分"是哪一个"。
        真正的暂停点只存在于 checkpoint 快照里，故只认快照。

    返回空集的含义统一为「没有可判定的 Gate 暂停点」：线程没停、停在工作节点（WP-8 中断
    续跑场景）、快照读不出 gate_id、或两个来源互相矛盾。调用方必须把空集当作"不得驱动图"。
    """
    if not getattr(snap, "next", ()):
        return frozenset()  # 线程未暂停（已跑完或从未启动）→ 没有暂停点

    payload_ids: set[str] = set()
    for task in (getattr(snap, "tasks", ()) or ()):
        for itr in (getattr(task, "interrupts", ()) or ()):
            val = getattr(itr, "value", None)
            gid = val.get("gate_id") if isinstance(val, dict) else None
            if gid:
                payload_ids.add(str(gid))

    values = getattr(snap, "values", None)
    pg = values.get("pending_gate") if isinstance(values, dict) else None
    state_id = str(pg.get("gate_id")) if isinstance(pg, dict) and pg.get("gate_id") else ""

    if payload_ids:
        if not state_id:
            return frozenset(payload_ids)
        agreed = payload_ids & {state_id}
        if not agreed:
            logger.warning(
                "图暂停点两个来源不一致：interrupt 载荷 gate_id=%s，state.pending_gate "
                "gate_id=%s；按 fail-closed 处理（本次不驱动图）", sorted(payload_ids), state_id)
        return frozenset(agreed)

    # 兜底：某些 checkpoint/库版本下 tasks[].interrupts 可能取不到载荷。此时只在
    # 「下一个待执行节点就是某个 {stage}_gate 节点」时，才把 state 里的 pending_gate
    # 当作暂停点——停在工作节点上的中断（WP-8 续跑）绝不能被当成 Gate 暂停点。
    at_gate_node = any(str(n).endswith(_GATE_NODE_SUFFIX)
                       for n in (getattr(snap, "next", ()) or ()))
    if state_id and at_gate_node:
        return frozenset({state_id})
    return frozenset()


async def graph_pending_gate_ids(run_id: str) -> frozenset[str]:
    """图线程 `run_id` 此刻暂停在的 Gate 集合（空集 = 没有可判定的 Gate 暂停点）。

    Gate 决策路由用它校验「被提交的 Gate 就是图暂停点」，再决定是否把决策注入
    graph resume。任何异常（无 checkpointer / 线程从未启动 / 读盘失败）→ 空集，
    调用方退回直连路径。
    """
    if not run_id:
        return frozenset()
    try:
        snap = await get_flow_runtime().get_state(run_id)
    except Exception:
        logger.debug("读取图状态快照失败 run=%s（视为无图暂停点）", run_id, exc_info=True)
        return frozenset()
    return pending_gate_ids_from_snapshot(snap)


_cap_probe: Optional[str] = None


def graph_capability_probe() -> str:
    """Real probe of orchestration capability: 'live' if the StateGraph builds, else 'degraded'.

    Replaces the hardcoded 'not_connected'/'future_r10' optimism/pessimism (公理4/G7).
    Cached after first probe (graph topology is static).
    """
    global _cap_probe
    if _cap_probe is None:
        try:
            build_graph()
            _cap_probe = "live"
        except Exception:
            _cap_probe = "degraded"
    return _cap_probe


def reset_flow_runtime_for_test() -> None:
    global _runtime, _cap_probe
    _runtime = None
    _cap_probe = None
