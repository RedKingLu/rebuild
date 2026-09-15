"""B-ACC-DECISION-CROSSGATE-INJECTION 回归锁：Gate 决策不得被注入到【另一个】Gate 上。

事实源（主窗口真跑坐实）：项目 b8e70a00 的 run 内，对 `action_approval`（L4）Gate
`gate-22343c` 提交【一个】`reject`（07:09:20.721137），0.5 秒后同 run 的另一个
`stage_promotion` Gate `gate-9a4201`（此前 waiting_decision）**也变成 rejected**
（07:09:21.238973）—— 从未对后者提交过任何决策。

根因：`routes_gates.decide_gate` 的图驱动条件 `(gate.checkpoint_ref or run_id) and
run_id` + `graph_thread_active(run_id)` 全是 **run 级** 判据，不校验「被提交的 Gate
是不是图正暂停在的那个 Gate」。于是 req.decision 被原样 `Command(resume=...)` 注入图，
图恢复后按 `state["pending_gate"]` 把它写到【图自己暂停的那个】Gate 上。

危害等级 P0 的决定性理由：`routes_gates.py` 的 R17-2 反空壳晋级守卫按【被提交 Gate】
的 gate_type 判断（`gate.gate_type == "stage_promotion"`），提交 action_approval 时
整道守卫被跳过，而决策最终作用在一个 stage_promotion Gate 上 ⇒ 经安全 Gate 提交
approve 即可驱动一次阶段晋级并绕过「杜绝空壳晋级」校验。

与 `B-R22-GATE-REDECIDE-REDRIVE`（D-01 幂等守卫，routes_gates.py:140）无关：那道守卫
的判据是【被提交 Gate】的 gate_status != waiting_decision，而 gate-22343c 正是
waiting_decision，按设计不拦。两者是不同缺陷。

本文件锁定四件事：
  1. 【解除条件④主项】同 run 内图暂停于一个 stage_promotion Gate 时，对同 run 的一个
     action_approval Gate 提交 approve ⇒ 那个 stage_promotion Gate 状态不变、阶段未晋级；
     并且用真实 LangGraph 线程端到端跑一遍（test_e2e_*），不是只靠假实现；
  2. 正常路径不回退：对图真正暂停在的那个 stage_promotion Gate 提交 approve 仍驱动图并晋级；
  3. 空壳晋级校验仍生效（无产物仍 422）；
  4. 同一性判据本身（pending_gate_ids_from_snapshot）在【真实图快照】上正确，且对
     "停在工作节点"/"两来源矛盾"等情形 fail closed。
"""

import os
import time
import uuid

import pytest

from app.dependencies import get_services
from app.graph.runtime import (
    graph_pending_gate_ids,
    pending_gate_ids_from_snapshot,
)


# ── 装置 ──────────────────────────────────────────────────────────────────

def _mk_project(client, name="ACC CrossGate"):
    return client.post("/api/projects", json={
        "name": name, "source_type": "manual",
    }).json()["data"]["project_id"]


def _mk_run(client, pid):
    return client.post(f"/api/projects/{pid}/runs",
                       json={"run_goal": "crossgate", "mode": "plan"}).json()["data"]["run_id"]


def _ensure_task_graph(run_id: str, stage: str):
    """R17-2：stage_promotion 晋级强绑阶段产物，注入 task_graph 满足校验。"""
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph
    db = get_session()
    try:
        tg = (db.query(TaskGraph)
              .filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage).first())
        if tg is None:
            db.add(TaskGraph(task_graph_id=f"tg-test-{uuid.uuid4().hex[:8]}", project_id="",
                             run_id=run_id, stage=stage, title=f"test gate {stage}",
                             graph_status="completed"))
            db.commit()
    finally:
        db.close()


def _mk_gate(client, pid, run_id, stage, gate_type, risk_level="L0"):
    return client.post(f"/api/projects/{pid}/gates", json={
        "run_id": run_id, "stage": stage, "gate_type": gate_type,
        "reason": "crossgate test", "summary": "crossgate test", "risk_level": risk_level,
    }).json()["data"]


def _decide(client, pid, gate_id, decision, reason="crossgate test"):
    return client.post(f"/api/projects/{pid}/gates/{gate_id}/decision",
                       json={"decision": decision, "reason": reason})


def _gate(client, pid, gate_id):
    gates = client.get(f"/api/projects/{pid}/gates").json()["data"]["gates"]
    return [g for g in gates if g["gate_id"] == gate_id][0]


@pytest.fixture
def graph_paused_on(monkeypatch):
    """把「图暂停点」替换为可控假实现，并记录图被驱动了几次。

    用法：`paused, drives = graph_paused_on`；`paused.add(gate_id)` 声明"图现在停在
    这个 Gate 上"。`drives` 每次真的驱动图就追加一条（模拟 graph resume 的可观测
    副作用：把暂停点那个 Gate 决策掉——这正是真跑现场 gate-9a4201 被改写的动作）。
    """
    paused: set[str] = set()
    drives: list[dict] = []

    async def _fake_pending(run_id):
        return frozenset(paused)

    def _fake_bg(run_id, decision, project_id, stage):
        drives.append({"run_id": run_id, "decision": decision, "stage": stage})
        # 真实图 resume 的核心副作用：把【图暂停点】那个 Gate 按本次决策写掉
        # （nodes.py gate 节点 → RealGateBackend.decide(gate_id=pending_gate.gate_id)）。
        from app.schemas.gate import GateDecisionRequest
        for gid in list(paused):
            get_services().gate_service.decide(
                gid, GateDecisionRequest(decision=decision), drive_promotion=True)
        return "fake-coro"

    def _fake_ensure(coro):
        return None  # 不起后台线程

    monkeypatch.setattr("app.graph.runtime.graph_pending_gate_ids", _fake_pending)
    monkeypatch.setattr("app.api.routes_stages._run_graph_bg", _fake_bg)
    monkeypatch.setattr("app.api.routes_stages._ensure_graph_task", _fake_ensure)
    return paused, drives


# ── ① 主缺陷锁定（假暂停点，快速版；端到端真图版见文件末尾） ─────────────

def test_action_approval_decision_does_not_leak_into_paused_promotion_gate(
        client, graph_paused_on):
    """图暂停在 stage_promotion Gate 上，对同 run 的 action_approval Gate 提交 approve
    ⇒ 那个 stage_promotion Gate 状态不变、阶段未晋级、图未被驱动（解除条件④主项）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")

    promo = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]
    paused.add(promo)                      # 图停在这个晋级 Gate 上
    approval = _mk_gate(client, pid, rid, "p2", "action_approval", risk_level="L4")["gate_id"]

    stage_before = client.get(f"/api/projects/{pid}").json()["data"]["current_stage"]

    r = _decide(client, pid, approval, "approve", reason="批准这一个高风险工具动作")
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    # 本次决策只能作用在被提交的那个 Gate 上
    assert data["gate"]["gate_id"] == approval
    assert data["gate"]["gate_status"] == "approved"
    assert data["graph_driven"] is False, "非图暂停点的 Gate 决策不得驱动阶段图 resume"
    assert drives == [], f"图被注入驱动 {len(drives)} 次（应为 0）：{drives}"

    # 另一个 Gate 必须毫发无损（真跑现场它变成了 rejected）
    other = _gate(client, pid, promo)
    assert other["gate_status"] == "waiting_decision", \
        f"未被提交决策的 stage_promotion Gate 被改写为 {other['gate_status']}（缺陷复发）"
    assert not other["decision"], f"未被提交决策的 Gate 出现决策值 {other['decision']!r}"

    # 阶段未晋级（反事实：approve 泄漏 ⇒ 凭空伪造一次 P2 通过）
    assert client.get(f"/api/projects/{pid}").json()["data"]["current_stage"] == stage_before


def test_reject_on_action_approval_does_not_reject_promotion_gate(client, graph_paused_on):
    """真跑现场的原始形态：提交的是 reject，另一个 Gate 也被 rejected。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    promo = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]
    paused.add(promo)
    approval = _mk_gate(client, pid, rid, "p2", "action_approval", risk_level="L4")["gate_id"]

    assert _decide(client, pid, approval, "reject", reason="不批这个动作").status_code == 200
    assert drives == []
    assert _gate(client, pid, promo)["gate_status"] == "waiting_decision"


def test_shell_promotion_check_cannot_be_bypassed_via_action_approval(client, graph_paused_on):
    """R17-2 绕过路径封堵：图停在一个【无产物】的 stage_promotion Gate 上，经
    action_approval Gate 提交 approve 不得让该阶段晋级（旧代码里被提交 Gate 是
    action_approval ⇒ 空壳晋级守卫整道跳过）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    # 故意不建 task_graph、不挂 artifact_refs → 该阶段无真实产物
    promo = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]
    paused.add(promo)
    approval = _mk_gate(client, pid, rid, "p2", "action_approval", risk_level="L4")["gate_id"]

    stage_before = client.get(f"/api/projects/{pid}").json()["data"]["current_stage"]
    assert _decide(client, pid, approval, "approve").status_code == 200
    assert drives == []
    assert _gate(client, pid, promo)["gate_status"] == "waiting_decision"
    assert client.get(f"/api/projects/{pid}").json()["data"]["current_stage"] == stage_before


# ── ② 正常路径不回退 ─────────────────────────────────────────────────────

def test_decision_on_the_paused_gate_still_drives_graph(client, graph_paused_on):
    """对图真正暂停在的那个 stage_promotion Gate 提交 approve ⇒ 仍驱动图（不回归）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    promo = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]
    paused.add(promo)

    r = _decide(client, pid, promo, "approve")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["graph_driven"] is True, "图暂停点自己的决策必须仍能驱动 resume"
    assert data["transition_mode"] == "real_background"
    assert drives == [{"run_id": rid, "decision": "approve", "stage": "p2"}]


def test_plan_presentation_paused_gate_still_drives_graph(client, graph_paused_on):
    """plan_presentation / plan_review 是图的真实暂停点（nodes.py 的 {stage}_gate 节点
    对这两类 gate_type 有专门分支），它们的决策必须仍能驱动图 —— 否则每个项目都会
    卡死在欢迎/计划 Gate 上。这条同时是"不得按 gate_type 一刀切封禁非晋级类 Gate"
    的反例锁。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    plan = _mk_gate(client, pid, rid, "p1", "plan_presentation")["gate_id"]
    paused.add(plan)

    r = _decide(client, pid, plan, "approve")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["graph_driven"] is True
    assert drives == [{"run_id": rid, "decision": "approve", "stage": "p1"}]


def test_direct_promotion_without_graph_not_regressed(client, graph_paused_on):
    """无图暂停点时，stage_promotion Gate 的 approve 仍走直连晋级（p2 → p3）。"""
    paused, drives = graph_paused_on   # paused 保持为空
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    promo = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]

    r = _decide(client, pid, promo, "approve")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["graph_driven"] is False
    assert r.json()["data"]["transition_mode"] == "direct"
    assert drives == []
    assert client.get(f"/api/projects/{pid}").json()["data"]["current_stage"] == "p3"


# ── ③ 空壳晋级校验仍生效 ─────────────────────────────────────────────────

def test_shell_promotion_still_422(client, graph_paused_on):
    """无产物的 stage_promotion Gate 自己被 approve ⇒ 仍 422（R17-2 未被削弱）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    promo = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]
    paused.add(promo)   # 即使图停在它上面，产物校验也必须先拦住

    r = _decide(client, pid, promo, "approve")
    assert r.status_code == 422, r.text
    assert "无真实产物" in r.text
    assert drives == [], "422 的晋级不得驱动图"
    assert _gate(client, pid, promo)["gate_status"] == "waiting_decision"


# ── ④ 同一性判据本身 ─────────────────────────────────────────────────────

class _FakeInterrupt:
    def __init__(self, value):
        self.value = value


class _FakeTask:
    def __init__(self, interrupts):
        self.interrupts = tuple(interrupts)


class _FakeSnap:
    def __init__(self, next_=(), tasks=(), values=None):
        self.next = tuple(next_)
        self.tasks = tuple(tasks)
        self.values = values or {}


def test_snapshot_parser_reads_the_paused_gate():
    snap = _FakeSnap(
        next_=("p2_gate",),
        tasks=[_FakeTask([_FakeInterrupt({"gate_id": "gate-9a4201", "stage": "p2",
                                          "type": "stage_promotion"})])],
        values={"pending_gate": {"gate_id": "gate-9a4201", "stage": "p2"}},
    )
    ids = pending_gate_ids_from_snapshot(snap)
    assert ids == frozenset({"gate-9a4201"})
    assert "gate-22343c" not in ids, "别的 Gate 绝不能被认成暂停点"


def test_snapshot_parser_returns_empty_when_thread_not_paused():
    snap = _FakeSnap(next_=(), values={"pending_gate": {"gate_id": "gate-9a4201"}})
    assert pending_gate_ids_from_snapshot(snap) == frozenset()


def test_snapshot_parser_ignores_mid_work_interrupt():
    """停在工作节点（WP-8 中断续跑）不是 Gate 暂停点，任何 Gate 决策都不得注入。"""
    snap = _FakeSnap(next_=("p1_work",),
                     values={"pending_gate": {"gate_id": "gate-old"}})
    assert pending_gate_ids_from_snapshot(snap) == frozenset()


def test_snapshot_parser_fails_closed_on_conflicting_sources():
    snap = _FakeSnap(
        next_=("p2_gate",),
        tasks=[_FakeTask([_FakeInterrupt({"gate_id": "gate-A"})])],
        values={"pending_gate": {"gate_id": "gate-B"}},
    )
    assert pending_gate_ids_from_snapshot(snap) == frozenset(), "来源矛盾必须 fail closed"


def test_snapshot_parser_falls_back_to_pending_gate_at_gate_node():
    """取不到 interrupt 载荷时，只在下一节点是 {stage}_gate 时才采信 pending_gate。"""
    snap = _FakeSnap(next_=("p2_gate",), tasks=[_FakeTask([])],
                     values={"pending_gate": {"gate_id": "gate-9a4201"}})
    assert pending_gate_ids_from_snapshot(snap) == frozenset({"gate-9a4201"})


async def test_graph_pending_gate_ids_on_a_real_graph_snapshot(tmp_path, monkeypatch):
    """真实 LangGraph 线程 + 真实 checkpoint 快照：暂停时解析出的 gate_id 必须正好是
    图创建的那个 Gate（证明判据不是只在假快照上成立）。"""
    from app.graph import nodes
    from app.graph.checkpoint import reset_checkpointer_for_test
    from app.graph.runtime import get_flow_runtime, reset_flow_runtime_for_test
    from app.services.review_pass import ReviewResult

    class _Handler:
        goal = "fake stage"
        acceptance_criteria = ["c1"]
        planned_actions = ["a1"]

        async def execute(self, state):
            return {"status": "completed", "artifacts": ["p0_out.json"], "ok": True}

        def review(self, result):
            return ReviewResult(passed=True, issues=[])

    class _Gates:
        def __init__(self):
            self.created = []

        def create(self, *, project_id, run_id, stage, artifact_refs,
                   gate_type="stage_promotion", metadata=None):
            gid = f"gate-real-{stage}-{len(self.created) + 1}"
            self.created.append(gid)
            return gid

        def decide(self, *, gate_id, decision):
            pass

    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path",
                        lambda: tmp_path / "graph_checkpoints.sqlite")
    monkeypatch.setattr("app.services.workspace_service._workspace_root", lambda: tmp_path / "ws")
    nodes.clear_handlers()
    gb = _Gates()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    try:
        await reset_checkpointer_for_test()
        reset_flow_runtime_for_test()
        nodes.register_handler("p0", _Handler())
        rt = get_flow_runtime()
        run_id = "run-crossgate-real"
        s = await rt.start(run_id, {"run_id": run_id, "project_id": "proj-crossgate",
                                    "run_goal": "g", "run_status": "running",
                                    "stage_status": {"p0": "in_progress"}})
        assert "__interrupt__" in s, "图应暂停在 p0 Gate"
        assert len(gb.created) == 1, f"应只创建一个 Gate：{gb.created}"
        paused_gate = gb.created[0]

        ids = await graph_pending_gate_ids(run_id)
        assert ids == frozenset({paused_gate}), \
            f"真实快照解析出的暂停点 {sorted(ids)} != 图创建的 Gate {paused_gate}"
        assert "gate-22343c" not in ids

        # 线程跑完（不再暂停）→ 空集
        while "__interrupt__" in s:
            s = await rt.resume(run_id, "approve")
        assert await graph_pending_gate_ids(run_id) == frozenset()
        # 从不存在的线程 → 空集（不得乐观放行）
        assert await graph_pending_gate_ids("run-never-started") == frozenset()
    finally:
        await reset_checkpointer_for_test()
        reset_flow_runtime_for_test()
        nodes.clear_handlers()
        nodes.set_gate_backend(None)


# ── ⑤ 端到端：真实图线程 + 真实 DB Gate（不用任何假实现） ─────────────────

def _wait_for_project_stage(c, pid, expected, timeout=None):
    if timeout is None:
        timeout = int(os.environ.get("R176_GRAPH_WAIT_TIMEOUT", "120"))
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = c.get(f"/api/projects/{pid}").json()["data"]["current_stage"]
        if last == expected:
            return
        time.sleep(0.5)
    raise AssertionError(f"stage never reached {expected} in {timeout}s, last={last}")


@pytest.fixture
def _reset_graph_singletons():
    """每个用例用全新编译图 + 全新 checkpointer（照 test_w9w10_graph_routes.py 的做法）。"""
    import asyncio
    from app.graph.checkpoint import reset_checkpointer_for_test
    from app.graph.runtime import reset_flow_runtime_for_test
    asyncio.run(reset_checkpointer_for_test())
    reset_flow_runtime_for_test()
    yield
    asyncio.run(reset_checkpointer_for_test())
    reset_flow_runtime_for_test()


def test_e2e_real_graph_action_approval_cannot_hijack_promotion_gate(
        tmp_path, monkeypatch, isolated_data, _reset_graph_singletons):
    """端到端真实图（无任何假 patch）：图真的暂停在 P0 stage_promotion Gate 上时，
    对同 run 的一个 action_approval Gate 提交 approve ⇒ P0 Gate 状态不变、阶段未晋级；
    随后对 P0 Gate 本人提交 approve 仍能正常驱动图晋级到 p1。

    最后这半段同时保证本用例【不是空过】：它证明本环境里图线程确实活着且可被本路由
    驱动（否则 graph_driven 不可能为 True），所以前半段的"没被驱动"是守卫起作用，
    而不是图本来就不可达。
    """
    from fastapi.testclient import TestClient
    from app.main import app
    import app.graph.stage_handlers as sh
    from app.api.routes_stages import drain_graph_tasks

    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path",
                        lambda: tmp_path / "graph_checkpoints.sqlite")
    sh._bootstrapped = False
    with TestClient(app) as c:
        pid = c.post("/api/projects", json={
            "name": "ACC CrossGate E2E", "source_type": "manual", "source_config": {},
        }).json()["data"]["project_id"]
        d = c.post(f"/api/projects/{pid}/graph/start",
                   json={"execution_mode": "auto", "source_type": "manual"}).json()["data"]
        run_id = d["run_id"]
        assert d["paused"] is True and d["pending_gate"]["stage"] == "p0"
        p0_gate = d["pending_gate"]["gate_id"]

        # 阶段执行途中 agent 停在 L3+ 工具审批 Gate 上等人批（真实场景，
        # agent_loop._create_action_gate / routes_registry 都会建这种 Gate）
        approval = _mk_gate(c, pid, run_id, "p0", "action_approval", risk_level="L4")["gate_id"]

        r = _decide(c, pid, approval, "approve", reason="批准这一个高风险动作")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["graph_driven"] is False, \
            "action_approval Gate 的决策驱动了阶段图 resume（缺陷复发）"
        # 若守卫失效，这里会有一个后台图 future 在跑；drain 后再断言即可确定性判定
        drain_graph_tasks(timeout=180.0)

        g = _gate(c, pid, p0_gate)
        assert g["gate_status"] == "waiting_decision", \
            f"P0 晋级 Gate 被 action_approval 决策改写为 {g['gate_status']}（缺陷复发）"
        assert not g["decision"]
        assert c.get(f"/api/projects/{pid}").json()["data"]["current_stage"] == "p0"

        # 正常路径：对图真正暂停在的 P0 Gate 提交 approve → 图被驱动并晋级
        r2 = _decide(c, pid, p0_gate, "approve")
        assert r2.status_code == 200, r2.text
        assert r2.json()["data"]["graph_driven"] is True, \
            "图暂停点自己的决策没能驱动图 —— 正常晋级路径被改坏"
        _wait_for_project_stage(c, pid, "p1")
        assert _gate(c, pid, p0_gate)["gate_status"] == "approved"
