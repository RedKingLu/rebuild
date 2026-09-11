"""D-01 / D-07 回归锁：Gate 重复决策的幂等性 + active_gate 语义。

事实源：`证据/进度追踪/02-阻塞项.md` 的 `B-R22-GATE-REDECIDE-REDRIVE`（V26.2 总验收
真实规模真跑实测）。当时对同一个【已 approved】的 p1 plan_presentation Gate 每 20 秒
重复提交一次批准、共 5 次，结果：
  ① 产生 5 个重复的 p1 stage_promotion Gate（全部 waiting_decision）；
  ② P1 产物 mtime 分两批 → 阶段工作被多次执行并互相覆写（同阶段两次结果 18 项 vs 23 项）；
  ③ 对 1018 文件的真实 LLM 调用被重复执行，真实消耗额度。
根因：`routes_gates.decide_gate` 的图驱动分支只判 `graph_thread_active(run_id)`，
不判 `gate.gate_status` / `gate.decision`；`gate_service.decide()` 的幂等守卫在其后。

本文件锁定四件事：
  1. 已决策 Gate 再次提交同一决策 → 不再驱动图、不产生第二个晋级 Gate（D-01 主缺陷）；
  2. 首次批准的图恢复路径、非图直连晋级路径、reject 路径均不回归；
  3. 图节点 resume 时"重新应用同一决策"这一既有幂等场景（gate_service.py 的
     decision/gate_status 守卫）行为不变 —— 它走 RealGateBackend，不经 HTTP 路由；
  4. active_gate 语义 = 当前待决 Gate：决策后即被清空（D-07），且不误清指向别的 Gate 的值；
     同时 ProjectService.update 的通用 None 过滤行为未被削弱。

测试策略说明：真实图驱动需要真实 LLM 与 checkpoint 线程，不适合单测。这里把
`graph_thread_active` 与 `_run_graph_bg` 替换为可计数的假实现，且让假实现【真的建一个
下一阶段 stage_promotion Gate】——精确模拟真实图 `make_work_node` 跑完阶段后
`promotion_gate_created` 的可观测副作用。于是"图被驱动几次"可由"多出几个晋级 Gate"
坐实，与真跑现场的证据形态一致（5 次驱动 = 5 个重复晋级 Gate）。
"""

import uuid

import pytest

from app.dependencies import get_services
from app.schemas.gate import GateDecisionRequest


# ── 基础装置 ──────────────────────────────────────────────────────────────

def _mk_project(client, name="RW Gate Redecide"):
    return client.post("/api/projects", json={
        "name": name, "source_type": "manual",
    }).json()["data"]["project_id"]


def _mk_run(client, pid):
    return client.post(f"/api/projects/{pid}/runs",
                       json={"run_goal": "rw-gate", "mode": "plan"}).json()["data"]["run_id"]


def _ensure_task_graph(run_id: str, stage: str):
    """R17-2 V-R17-1B-2：stage_promotion 晋级强绑阶段产物，注入 task_graph 满足校验。"""
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


def _mk_gate(client, pid, run_id, stage, gate_type):
    return client.post(f"/api/projects/{pid}/gates", json={
        "run_id": run_id, "stage": stage, "gate_type": gate_type,
        "reason": "rw test", "summary": "rw test",
    }).json()["data"]


def _decide(client, pid, gate_id, decision, reason="rw test"):
    return client.post(f"/api/projects/{pid}/gates/{gate_id}/decision",
                       json={"decision": decision, "reason": reason})


def _gates(client, pid):
    return client.get(f"/api/projects/{pid}/gates").json()["data"]["gates"]


def _promotion_gates(client, pid, stage):
    return [g for g in _gates(client, pid)
            if g["gate_type"] == "stage_promotion" and g["stage"] == stage
            and g["gate_status"] == "waiting_decision"]


@pytest.fixture
def fake_graph(monkeypatch):
    """把「图线程活跃」+「后台驱动图」替换为可计数假实现（不跑真图、不调 LLM）。

    假实现同时模拟真实图的可观测副作用：每被驱动一次，就为下一阶段建一个
    stage_promotion Gate（对应 nodes.make_work_node 的 promotion_gate_created）。
    返回 drives 列表，每次驱动追加一条 {run_id, decision, stage}。
    """
    drives: list[dict] = []
    _next = {"p0": "p1", "p1": "p2", "p2": "p3", "p3": "p4", "p4": "p5", "p5": "p6"}

    async def _fake_active(run_id):
        return True

    def _fake_bg(run_id, decision, project_id, stage):
        drives.append({"run_id": run_id, "decision": decision, "stage": stage})
        if decision == "approve":
            nxt = _next.get(stage, stage)
            _ensure_task_graph(run_id, nxt)
            get_services().gate_service.create(
                project_id=project_id, run_id=run_id, stage=nxt,
                gate_type="stage_promotion", reason="fake graph promotion",
                summary=f"{nxt} 阶段已完成（假图）")
        return f"fake-coro-{len(drives)}"

    def _fake_ensure(coro):
        return None  # 不真的起后台线程

    monkeypatch.setattr("app.graph.runtime.graph_thread_active", _fake_active)
    monkeypatch.setattr("app.api.routes_stages._run_graph_bg", _fake_bg)
    monkeypatch.setattr("app.api.routes_stages._ensure_graph_task", _fake_ensure)
    return drives


@pytest.fixture
def no_graph(monkeypatch):
    """无活跃图线程 → 走直连（direct）决策路径。"""
    async def _fake_inactive(run_id):
        return False
    monkeypatch.setattr("app.graph.runtime.graph_thread_active", _fake_inactive)


# ── D-01 核心：已决策 Gate 重复提交不得重复驱动阶段 ───────────────────────

def test_repeat_approve_on_decided_stage_promotion_gate_does_not_redrive(client, fake_graph):
    """已 approved 的 stage_promotion Gate 再次 approve → 不触发第二次阶段执行、
    不产生第二个晋级 Gate（真跑现场：5 次重复提交 → 5 个重复晋级 Gate）。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    gid = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]

    first = _decide(client, pid, gid, "approve")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["graph_driven"] is True
    assert first.json()["data"]["gate"]["gate_status"] == "approved"
    assert len(fake_graph) == 1
    assert len(_promotion_gates(client, pid, "p3")) == 1

    # 模拟真跑现场的轮询客户端：再提交 4 次同一决策
    for _ in range(4):
        r = _decide(client, pid, gid, "approve")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["graph_driven"] is False
        assert data["transition_mode"] == "noop_already_decided"
        assert data["audit"] is None, "重放不得再写一条 gate_decision 审计"
        assert data["gate"]["gate_status"] == "approved"

    assert len(fake_graph) == 1, f"图被重复驱动 {len(fake_graph)} 次（应为 1）"
    assert len(_promotion_gates(client, pid, "p3")) == 1, "出现重复晋级 Gate（D-01 复发）"


def test_repeat_approve_on_decided_plan_presentation_gate_does_not_redrive(client, fake_graph):
    """已 approved 的 plan_presentation Gate 再次 approve → 同上（真跑现场正是这种 Gate）。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    gid = _mk_gate(client, pid, rid, "p1", "plan_presentation")["gate_id"]

    first = _decide(client, pid, gid, "approve")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["graph_driven"] is True
    assert len(fake_graph) == 1
    assert len(_promotion_gates(client, pid, "p2")) == 1

    for _ in range(4):
        r = _decide(client, pid, gid, "approve")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["graph_driven"] is False
        assert r.json()["data"]["transition_mode"] == "noop_already_decided"

    assert len(fake_graph) == 1, f"plan_presentation 图被重复驱动 {len(fake_graph)} 次"
    assert len(_promotion_gates(client, pid, "p2")) == 1, "出现重复晋级 Gate（D-01 复发）"


def test_repeat_decision_writes_no_second_audit(client, fake_graph):
    """重复提交不得产生第二条 gate_decision 审计（一次决策 = 一条权威审计）。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    gid = _mk_gate(client, pid, rid, "p1", "plan_presentation")["gate_id"]

    _decide(client, pid, gid, "approve")
    for _ in range(3):
        _decide(client, pid, gid, "approve")

    audits = client.get(f"/api/projects/{pid}/audit?gate_id={gid}").json()["data"]["audits"]
    decisions = [a for a in audits if a["audit_type"] == "gate_decision"]
    assert len(decisions) == 1, f"gate_decision 审计 {len(decisions)} 条（应为 1）"


def test_conflicting_decision_on_decided_gate_is_409(client, fake_graph):
    """对已决策 Gate 提交【不同】决策 → 409 且不驱动图、不改写既有决策（不静默改判）。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    gid = _mk_gate(client, pid, rid, "p1", "plan_presentation")["gate_id"]

    assert _decide(client, pid, gid, "approve").status_code == 200
    assert len(fake_graph) == 1

    r = _decide(client, pid, gid, "reject", reason="改主意了")
    assert r.status_code == 409, r.text
    assert len(fake_graph) == 1, "冲突决策不得驱动图"
    gate = client.get(f"/api/projects/{pid}/gates").json()["data"]["gates"]
    same = [g for g in gate if g["gate_id"] == gid][0]
    assert same["gate_status"] == "approved" and same["decision"] == "approve"


def test_illegal_decision_on_decided_gate_is_400(client, fake_graph):
    """已决策 Gate 上的非法决策值仍是 400（与 waiting 态语义一致），且不驱动图。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    gid = _mk_gate(client, pid, rid, "p1", "plan_presentation")["gate_id"]
    _decide(client, pid, gid, "approve")

    r = _decide(client, pid, gid, "frobnicate")
    assert r.status_code == 400, r.text
    assert len(fake_graph) == 1


# ── 既有路径不回归 ────────────────────────────────────────────────────────

def test_first_approve_still_drives_graph_resume(client, fake_graph):
    """R17-6 首次批准的图恢复路径不回归：waiting_decision 的 Gate 首次 approve
    仍 fire-and-forget 恢复图（graph_driven=True，且带上用户决策）。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    gid = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]

    r = _decide(client, pid, gid, "approve")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["graph_driven"] is True
    assert data["transition_mode"] == "real_background"
    assert data["audit"] is not None and data["audit"]["audit_type"] == "gate_decision"
    assert fake_graph == [{"run_id": rid, "decision": "approve", "stage": "p2"}]


def test_first_approve_without_graph_still_promotes_directly(client, no_graph):
    """无活跃图线程时首次 approve 仍走直连晋级（_apply_promotion 真实推进阶段）。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    gid = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]

    r = _decide(client, pid, gid, "approve")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["graph_driven"] is False
    assert r.json()["data"]["transition_mode"] == "direct"
    proj = client.get(f"/api/projects/{pid}").json()["data"]
    assert proj["current_stage"] == "p3", "直连晋级路径被破坏（应从 p2 推进到 p3）"


def test_reject_path_not_regressed(client, fake_graph):
    """reject 路径不回归：首次 reject 正常驱动图并记 rejected；重复 reject 幂等无操作。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    gid = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]

    r = _decide(client, pid, gid, "reject", reason="计划不可接受")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["gate"]["gate_status"] == "rejected"
    assert r.json()["data"]["graph_driven"] is True
    assert fake_graph == [{"run_id": rid, "decision": "reject", "stage": "p2"}]

    r2 = _decide(client, pid, gid, "reject", reason="再点一次")
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["graph_driven"] is False
    assert r2.json()["data"]["transition_mode"] == "noop_already_decided"
    assert len(fake_graph) == 1


def test_request_changes_path_not_regressed(client, fake_graph):
    """request_changes 路径不回归（返工语义）+ 重复提交幂等。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    gid = _mk_gate(client, pid, rid, "p2", "stage_promotion")["gate_id"]

    r = _decide(client, pid, gid, "request_changes", reason="补充说明")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["gate"]["gate_status"] == "changes_requested"
    assert len(fake_graph) == 1

    r2 = _decide(client, pid, gid, "request_changes", reason="补充说明")
    assert r2.status_code == 200
    assert r2.json()["data"]["transition_mode"] == "noop_already_decided"
    assert len(fake_graph) == 1


def test_graph_internal_replay_of_same_decision_still_idempotent(client, fake_graph):
    """图内部重放不回归：图节点 resume 时经 RealGateBackend.decide() 重新应用同一决策
    （app/graph/nodes.py → gate_backend.py，不经 HTTP 路由），必须仍被 GateService
    的既有幂等守卫吸收 —— 不报错、不改状态、不写第二条审计。"""
    from app.graph.gate_backend import RealGateBackend

    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    gid = _mk_gate(client, pid, rid, "p1", "plan_presentation")["gate_id"]

    # 1) REST 路由同步记录决策（drive_promotion=False，图驱动语义）
    assert _decide(client, pid, gid, "approve").status_code == 200

    # 2) 后台图节点 resume 时重新应用同一决策 —— 既有场景，必须无副作用
    backend = RealGateBackend()
    backend.decide(gate_id=gid, decision="approve")
    backend.decide(gate_id=gid, decision="approve")  # 再来一次也仍无副作用

    g = get_services().gate_service.get(gid)
    assert g.gate_status == "approved" and g.decision == "approve"
    audits = client.get(f"/api/projects/{pid}/audit?gate_id={gid}").json()["data"]["audits"]
    assert len([a for a in audits if a["audit_type"] == "gate_decision"]) == 1

    # 直接调 service 层同样保持"同一决策重复 = 无操作（audit=None）"
    resp, audit = get_services().gate_service.decide(
        gid, GateDecisionRequest(decision="approve"), drive_promotion=False)
    assert resp is not None and resp.gate_status == "approved"
    assert audit is None


# ── D-07：active_gate 语义 = 当前待决 Gate ────────────────────────────────

def test_active_gate_cleared_after_decision(client, no_graph):
    """D-07：Gate 决策后 project.active_gate 立即不再指向它（该字段语义=当前待决 Gate）。
    真跑现场：gate-ce9095 已 approved，active_gate 仍指向它 → 轮询客户端反复提交（D-01 诱因）。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    gid = _mk_gate(client, pid, rid, "p1", "plan_presentation")["gate_id"]
    get_services().project_service.update(pid, active_gate=gid)
    assert client.get(f"/api/projects/{pid}").json()["data"]["active_gate"] == gid

    assert _decide(client, pid, gid, "approve").status_code == 200

    proj = client.get(f"/api/projects/{pid}").json()["data"]
    assert not proj["active_gate"], \
        f"决策后 active_gate 仍指向已决 Gate：{proj['active_gate']}（D-07 复发）"


def test_active_gate_pointing_at_other_gate_is_not_cleared(client, no_graph):
    """只清"指向本 Gate"的 active_gate，不得误清指向别的真实待决 Gate 的值。"""
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    decided = _mk_gate(client, pid, rid, "p1", "plan_presentation")["gate_id"]
    other = _mk_gate(client, pid, rid, "p2", "plan_presentation")["gate_id"]
    get_services().project_service.update(pid, active_gate=other)

    assert _decide(client, pid, decided, "approve").status_code == 200

    proj = client.get(f"/api/projects/{pid}").json()["data"]
    assert proj["active_gate"] == other, "误清了指向另一个待决 Gate 的 active_gate"


def test_project_update_none_filter_not_weakened(client):
    """通用契约未被削弱：ProjectService.update 仍把 None 当作"本次不更新该字段"，
    置空须显式传空值（active_gate=""）。放宽这条过滤会让所有字段被意外清空。"""
    pid = _mk_project(client)
    ps = get_services().project_service
    ps.update(pid, active_gate="gate-keepme", description="保留我")

    ps.update(pid, active_gate=None, description=None)
    p = ps.get(pid)
    assert p.active_gate == "gate-keepme", "None 被当成置空 —— 通用过滤行为被削弱"
    assert p.description == "保留我"

    ps.update(pid, active_gate="")
    assert not ps.get(pid).active_gate, "显式传空值应能置空"
