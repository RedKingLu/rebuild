"""R17.3-6 WP-8 — 运行时韧性：后台任务进程重启自动恢复（GAP-BG-1）真实测试.

验证 app.graph.recovery.recover_interrupted_runs 的启动重启恢复调度器：
  1. 可恢复（work 节点被中断、无 Gate 等待）→ 从 LangGraph checkpoint 自动 resume 续跑，
     run 推进到下一个 Gate（waiting_gate），状态可查。
  2. 停在用户 Gate interrupt → 对账为 waiting_gate（绝不伪造决策）。
  3. 无 checkpoint → 显式置 failed（不静默，D-097）。
  4. 已完成（图到 END）但 DB 漏同步 → 对账为终态 completed。
  5. 幂等：重复 startup 不重复 resume 已调度的 run；已完成/终态 run 永不被重新 resume。

真实链路（非 mock）：真实 LangGraph 编译图 + 真实 AsyncSqliteSaver checkpoint（隔离
data_dir）+ 真实 DB Run 行（隔离 rebuild.db）。仅 stage handler / gate backend 用确定性
fake（与 test_graph_spine 一致），使孤儿 checkpoint 可确定性构造，不改恢复逻辑本身。

asyncio_mode=auto（conftest）。
"""

import pytest

from app.core.database import get_session
from app.graph import nodes
from app.graph.checkpoint import open_standalone_checkpointer, thread_config, close_checkpointer
from app.graph.graph import build_graph
from app.graph.recovery import recover_interrupted_runs, reset_recovery_state_for_test
from app.models.run import Run
from app.schemas.run import RunCreate
from app.services.review_pass import ReviewResult
from langgraph.types import Command


class _FakeHandler:
    goal = "fake stage"
    acceptance_criteria = ["c1"]
    planned_actions = ["a1"]

    def __init__(self):
        self.calls = 0

    async def execute(self, state):
        self.calls += 1
        pid = state.get("project_id", "")
        cur = state.get("current_stage", "p0")
        try:
            from app.services import workspace_service
            art = workspace_service.workspace_path(pid) / "artifacts"
            art.mkdir(parents=True, exist_ok=True)
            (art / f"{cur}_domain.json").write_text('{"ok": true}', encoding="utf-8")
        except Exception:
            pass
        return {"status": "completed", "artifacts": [f"{cur}_out.json"], "ok": True}

    def review(self, result):
        return ReviewResult(passed=True, issues=[])


class _FakeGate:
    def __init__(self):
        self._n = 0

    def create(self, *, project_id, run_id, stage, artifact_refs,
               gate_type="stage_promotion", metadata=None):
        self._n += 1
        return f"gate-{stage}-{self._n}"

    def decide(self, *, gate_id, decision):
        pass


@pytest.fixture
def graph_env(isolated_data):
    """确定性 handler/gate（p0/p1）+ 关闭 Agent 工作流（使 fake handler 路径生效）。"""
    _orig_stages = set(nodes._AGENT_WORKFLOW_STAGES)
    nodes.clear_handlers()
    nodes.set_gate_backend(_FakeGate())
    nodes.set_tracer_auditor(None, None)
    nodes._AGENT_WORKFLOW_STAGES = set()
    nodes.register_handler("p0", _FakeHandler())
    nodes.register_handler("p1", _FakeHandler())
    reset_recovery_state_for_test()
    yield isolated_data
    nodes._AGENT_WORKFLOW_STAGES = _orig_stages
    reset_recovery_state_for_test()


def _mk_running_run(svc, project_id: str) -> str:
    """创建一条 DB Run 并置 running（模拟后台图正在执行/被中断的孤儿）。"""
    r = svc.run_service.create(project_id, RunCreate(run_goal="g", mode="plan"))
    svc.run_service.set_run_status(r.run_id, "running")
    return r.run_id


def _db_status(run_id: str) -> str:
    db = get_session()
    try:
        return db.get(Run, run_id).run_status
    finally:
        db.close()


def _init(run_id, project_id):
    return {"run_id": run_id, "project_id": project_id, "run_goal": "g",
            "run_status": "running", "current_stage": "p0",
            "stage_status": {"p0": "in_progress"}}


async def test_recover_resumes_midwork_run_from_checkpoint(graph_env):
    """可恢复 run：work 节点被中断（无 Gate 等待）→ 恢复自动续跑到下一个 Gate。"""
    svc = graph_env
    pid = "proj-recover-midwork"
    run_id = _mk_running_run(svc, pid)

    # 构造「p1_work 被中断、尚未跑完、无 dynamic interrupt」的孤儿 checkpoint：
    # 用 interrupt_before=['p1_work'] 停在进入 p1_work 之前（等价于进程在 p1_work 执行中崩溃，
    # 最后一个持久 checkpoint 落在 gate 节点之后、work 之前）。
    conn, saver = await open_standalone_checkpointer()
    try:
        g = build_graph().compile(checkpointer=saver, interrupt_before=["p1_work"])
        await g.ainvoke(_init(run_id, pid), config=thread_config(run_id))  # → p0_gate interrupt
        await g.ainvoke(Command(resume="approve"), config=thread_config(run_id))  # → stop before p1_work
        snap = await g.aget_state(thread_config(run_id))
        assert snap.next == ("p1_work",)
        assert not snap.interrupts  # 无用户 Gate 等待 → 应被判为 resumable
    finally:
        await conn.close()

    assert _db_status(run_id) == "running"

    # 触发启动恢复扫描
    summary = await recover_interrupted_runs()
    assert summary["scanned"] >= 1
    assert summary["resumed"] == 1

    # 后台续跑完成 → run 从 checkpoint 续跑并推进到 p1 的 Gate（waiting_gate），状态可查。
    from app.api.routes_stages import drain_graph_tasks
    drain_graph_tasks(timeout=30.0)
    assert _db_status(run_id) == "waiting_gate"

    # 校验图确实推进到了 p1（p1_work 已执行）
    conn2, saver2 = await open_standalone_checkpointer()
    try:
        g2 = build_graph().compile(checkpointer=saver2)
        snap2 = await g2.aget_state(thread_config(run_id))
        assert (snap2.values.get("pending_gate") or {}).get("stage") == "p1"
    finally:
        await conn2.close()
    await close_checkpointer()


async def test_recover_reconciles_waiting_gate(graph_env):
    """停在用户 Gate interrupt 的孤儿 → 对账 waiting_gate，不伪造决策。"""
    svc = graph_env
    pid = "proj-recover-waitgate"
    run_id = _mk_running_run(svc, pid)

    conn, saver = await open_standalone_checkpointer()
    try:
        g = build_graph().compile(checkpointer=saver)
        s = await g.ainvoke(_init(run_id, pid), config=thread_config(run_id))
        assert "__interrupt__" in s  # 停在 p0_gate 用户决策 interrupt
    finally:
        await conn.close()

    summary = await recover_interrupted_runs()
    assert summary["reconciled_waiting_gate"] == 1
    assert summary["resumed"] == 0
    assert _db_status(run_id) == "waiting_gate"
    await close_checkpointer()


async def test_recover_marks_no_checkpoint_run_failed(graph_env):
    """running 但无 checkpoint（进程重启不可恢复）→ 显式失败态，不静默。"""
    svc = graph_env
    pid = "proj-recover-nockpt"
    run_id = _mk_running_run(svc, pid)  # 从不驱动图 → 无 checkpoint

    summary = await recover_interrupted_runs()
    assert summary["failed_no_checkpoint"] == 1
    assert summary["resumed"] == 0
    assert _db_status(run_id) == "failed"
    await close_checkpointer()


async def test_recover_reconciles_completed_run(graph_env):
    """图已到 END 但 DB 仍 running → 对账终态 completed。"""
    svc = graph_env
    pid = "proj-recover-completed"
    run_id = _mk_running_run(svc, pid)

    conn, saver = await open_standalone_checkpointer()
    try:
        g = build_graph().compile(checkpointer=saver)
        s = await g.ainvoke(_init(run_id, pid), config=thread_config(run_id))
        # 全程 approve 驱动到 END
        guard = 0
        while "__interrupt__" in s and guard < 20:
            s = await g.ainvoke(Command(resume="approve"), config=thread_config(run_id))
            guard += 1
        snap = await g.aget_state(thread_config(run_id))
        assert snap.next == ()  # 已完成
    finally:
        await conn.close()

    summary = await recover_interrupted_runs()
    assert summary["reconciled_completed"] == 1
    assert summary["resumed"] == 0
    assert _db_status(run_id) == "completed"
    await close_checkpointer()


async def test_recover_is_idempotent(graph_env):
    """幂等：重复 startup 不重复 resume 同一 run；已完成 run 永不被重新 resume。"""
    svc = graph_env
    pid = "proj-recover-idem"

    # 一个可恢复 mid-work run
    run_mid = _mk_running_run(svc, pid)
    conn, saver = await open_standalone_checkpointer()
    try:
        g = build_graph().compile(checkpointer=saver, interrupt_before=["p1_work"])
        await g.ainvoke(_init(run_mid, pid), config=thread_config(run_mid))
        await g.ainvoke(Command(resume="approve"), config=thread_config(run_mid))
    finally:
        await conn.close()

    # 第一次扫描 → 调度续跑
    s1 = await recover_interrupted_runs()
    assert s1["resumed"] == 1

    # 第二次扫描（未 drain，run 仍在 _recovering 且 DB 仍 running）→ 不重复调度
    s2 = await recover_interrupted_runs()
    assert s2["resumed"] == 0
    assert s2["skipped"] >= 1

    from app.api.routes_stages import drain_graph_tasks
    drain_graph_tasks(timeout=30.0)
    assert _db_status(run_mid) == "waiting_gate"

    # 续跑完成后 run 已是 waiting_gate（非 running）→ 后续扫描不再把它当孤儿处理
    s3 = await recover_interrupted_runs()
    assert s3["resumed"] == 0
    assert s3["scanned"] == 0  # 无 running 孤儿
    await close_checkpointer()
