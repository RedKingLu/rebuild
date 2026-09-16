"""B-ACC-PROMOTION-DECISION-NOGUARD 回归锁：`promotion-decision` 的决策必须指名对象。

台账：`B-ACC-PROMOTION-DECISION-NOGUARD`（**P0 / CRITICAL**，7 条解除条件）。
本条比已修的 `B-ACC-DECISION-CROSSGATE-INJECTION` **更宽**：那条是"守卫存在但被跳过"，
本条是"守卫在图分支上**根本不存在**"，且这是 `test_r9_chain.py` 与主窗口历次真跑驱动
脚本**实际在用**的路径，不是冷门端点。

两处站点（施工前逐行核对过原文）：
  站点①`routes_stages.py` 图驱动判据只有 run 级 `graph_thread_active(run_id)`，
    且 `PromotionDecision` **没有 gate_id 字段** ⇒ 结构上不可能做同一性判定；
    更严重的是 `if graph_driven:` 分支构造合成结果**直接返回**、根本不调
    `stage_service.promote()` ⇒ `promote()` 内的 R17-2「无真实产物 → 422」校验
    （V-R17-1B-2）**整条不执行** ⇒ 图活跃（正常情况）时可驱动一次空壳晋级。
  站点②`routes_runs.py:98` / `routes_graph.py` 的 resume 把 `req.decision` 注入图，
    **完全没有 Gate 概念**（连 gate_id 字段都不存在）。

本文件锁定（编号对应 ③ 完善方案 §3 的 T1~T8）：
  T1 图暂停于 p2 Gate 时对 /stages/p0/promotion-decision 提交 approve ⇒ 409、
     p2 Gate 状态不变、无阶段晋级、图未被驱动；
  T2 图分支下无真实产物晋级 ⇒ 422，且文案与 routes_gates 逐字一致；
  T3 传正确 gate_id 且图正暂停在它上面 ⇒ 正常驱动图（首次批准路径不受损）；
  T4 不传 gate_id、恰好 1 个待决晋级 Gate ⇒ 与修复前等价（向后兼容）；
  T5 不传 gate_id、0 个待决 Gate ⇒ 409 且不驱动图；
  T6 不传 gate_id、≥2 个待决 Gate ⇒ 409、列出候选、不驱动图；
  T7 /runs/{id}/resume 在图暂停于某 Gate 时提交 decision ⇒ 409、不注入；
  T8 /graph/resume 同上（不指名 gate_id ⇒ 409；指名暂停点则放行）。

另附三条本批次自加的边界锁（不在 T 清单内，但属同一守卫的必然推论）：
  · 传了【别的 run/stage 的】gate_id ⇒ 409（换个入口重现同一缺陷的路径也要封）；
  · 传了已决策 Gate 的 gate_id ⇒ 409（不静默改判、不重复驱动图）；
  · plan_presentation 这类"非 stage_promotion 但确实是图暂停点"的 Gate 仍可决策
    （否则项目会卡死在计划 Gate 上）—— 这条同时是"候选判据不得退化为 gate_type 名单"
    的反例锁。
"""

import uuid

import pytest

from app.dependencies import get_services


# ── 装置（与 test_acc_decision_crossgate_injection.py 同构，便于对照）────────

def _mk_project(client, name="ACC PromotionGuard"):
    return client.post("/api/projects", json={
        "name": name, "source_type": "manual",
    }).json()["data"]["project_id"]


def _mk_run(client, pid):
    return client.post(f"/api/projects/{pid}/runs",
                       json={"run_goal": "promotion guard", "mode": "plan"}).json()["data"]["run_id"]


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


def _mk_gate(client, pid, run_id, stage, gate_type="stage_promotion", risk_level="L0"):
    return client.post(f"/api/projects/{pid}/gates", json={
        "run_id": run_id, "stage": stage, "gate_type": gate_type,
        "reason": "promotion guard test", "summary": "promotion guard test",
        "risk_level": risk_level,
    }).json()["data"]["gate_id"]


def _gate(client, pid, gate_id):
    gates = client.get(f"/api/projects/{pid}/gates").json()["data"]["gates"]
    return [g for g in gates if g["gate_id"] == gate_id][0]


def _promote(client, pid, run_id, stage, decision="approve", gate_id=None, reason="锁定测试"):
    body = {"decision": decision, "reason": reason}
    if gate_id is not None:
        body["gate_id"] = gate_id
    return client.post(
        f"/api/projects/{pid}/runs/{run_id}/stages/{stage}/promotion-decision", json=body)


@pytest.fixture
def graph_paused_on(monkeypatch):
    """把「图暂停点」替换为可控假实现，并记录图被驱动了几次。

    与 `test_acc_decision_crossgate_injection.py` 的同名 fixture 完全同构（同一套
    monkeypatch 目标），这样两条同族缺陷的锁定测试可以直接对照阅读。
    `drives` 里每条 = 一次真的图注入；假实现同时复刻真实 resume 的核心副作用
    （把暂停点那个 Gate 按本次决策写掉），使"决策串到别的 Gate"可被观测到。
    """
    paused: set[str] = set()
    drives: list[dict] = []

    async def _fake_pending(run_id):
        return frozenset(paused)

    def _fake_bg(run_id, decision, project_id, stage):
        drives.append({"run_id": run_id, "decision": decision, "stage": stage})
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


# ── T1：跨阶段串位（本缺陷的主形态）──────────────────────────────────────

def test_t1_promotion_decision_on_other_stage_cannot_hijack_paused_gate(
        client, graph_paused_on):
    """T1：图暂停于 p2 Gate，对 /stages/p0/promotion-decision 提交 approve ⇒ 409，
    p2 Gate 状态不变、阶段未晋级、图未被驱动。

    旧代码：判据只有 `graph_thread_active(run_id)`（run 级），URL 里的 p0 与图暂停点 p2
    无关也照样注入 ⇒ 图恢复后把 approve 写到 p2 那个 Gate 上 ⇒ 凭空伪造一次 P2 通过。
    """
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    promo_p2 = _mk_gate(client, pid, rid, "p2")
    paused.add(promo_p2)                      # 图停在 p2 的晋级 Gate 上

    stage_before = client.get(f"/api/projects/{pid}").json()["data"]["current_stage"]

    r = _promote(client, pid, rid, "p0", "approve")

    assert r.status_code == 409, r.text
    assert "无待决晋级 Gate" in r.text
    assert drives == [], f"图被注入驱动 {len(drives)} 次（应为 0）：{drives}"
    other = _gate(client, pid, promo_p2)
    assert other["gate_status"] == "waiting_decision", \
        f"未被提交决策的 p2 Gate 被改写为 {other['gate_status']}（缺陷复发）"
    assert not other["decision"]
    assert client.get(f"/api/projects/{pid}").json()["data"]["current_stage"] == stage_before


# ── T2：R17-2 空壳晋级校验覆盖图分支（判 P0 的决定性理由）────────────────

def test_t2_shell_promotion_still_422_on_graph_branch(client, graph_paused_on):
    """T2：图正暂停在该晋级 Gate 上（走图分支）、但该阶段无真实产物 ⇒ 422，且不驱动图。

    旧代码：图分支构造合成结果直接返回、不调 promote() ⇒ R17-2 校验整条缺席 ⇒ 200 + 晋级。
    文案与 routes_gates.decide_gate 的 422 逐字同源（共享 gate_service._stage_has_real_artifact）。
    """
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    # 故意不建 task_graph、不挂 artifact_refs → 该阶段无真实产物
    promo = _mk_gate(client, pid, rid, "p2")
    paused.add(promo)

    stage_before = client.get(f"/api/projects/{pid}").json()["data"]["current_stage"]
    r = _promote(client, pid, rid, "p2", "approve", gate_id=promo)

    assert r.status_code == 422, r.text
    assert "无真实产物" in r.text
    assert "task_graph 未创建且 artifact_refs 为空" in r.text, "422 文案须与 routes_gates 一致"
    assert drives == [], "422 的晋级不得驱动图"
    assert _gate(client, pid, promo)["gate_status"] == "waiting_decision"
    assert client.get(f"/api/projects/{pid}").json()["data"]["current_stage"] == stage_before


def test_t2b_shell_promotion_422_text_matches_routes_gates(client, graph_paused_on):
    """T2 附：同一个空壳晋级，经 promotion-decision 与经 /gates/{id}/decision
    返回的 422 文案**逐字相同**（证明两条路由用的是同一份判据与同一句文案，没有第二份）。"""
    paused, _ = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    promo = _mk_gate(client, pid, rid, "p2")
    paused.add(promo)

    via_stage = _promote(client, pid, rid, "p2", "approve", gate_id=promo)
    via_gate = client.post(f"/api/projects/{pid}/gates/{promo}/decision",
                           json={"decision": "approve", "reason": "锁定测试"})

    assert via_stage.status_code == 422 and via_gate.status_code == 422
    assert via_stage.json()["detail"] == via_gate.json()["detail"], (
        "两条路由的 422 文案不一致 ⇒ 说明判据/文案被抄成了两份（本批次明确禁止）：\n"
        f"promotion-decision: {via_stage.json()['detail']!r}\n"
        f"gates/decision    : {via_gate.json()['detail']!r}")


# ── T3：正常路径不回退 ───────────────────────────────────────────────────

def test_t3_correct_gate_id_on_paused_gate_still_drives_graph(client, graph_paused_on):
    """T3：传正确 gate_id 且图正暂停在它上面 ⇒ 仍驱动图（首次批准路径不受损）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p1")
    promo = _mk_gate(client, pid, rid, "p1")
    paused.add(promo)

    r = _promote(client, pid, rid, "p1", "approve", gate_id=promo)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["graph_driven"] is True, "图暂停点自己的决策必须仍能驱动 resume"
    assert data["transition_mode"] == "real_background"
    assert data["gate_id"] == promo, "响应须诚实回填被决策对象（旧代码硬编码空串）"
    assert drives == [{"run_id": rid, "decision": "approve", "stage": "p1"}]


def test_t3b_plan_presentation_paused_gate_still_decidable(client, graph_paused_on):
    """T3 附：plan_presentation 不是 stage_promotion，但**是**图的真实暂停点
    （app/graph/nodes.py 的 {stage}_gate 节点），前端 GatePanel 对它也打本端点 ⇒
    必须仍可决策并驱动图，否则每个项目都会卡死在计划 Gate 上。

    这条锁的是"候选判据不得退化为 gate_type 名单"：候选集第二条判据是
    「gate_id ∈ 图暂停点」，与类型无关，故新增暂停点类型自动被覆盖。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    plan = _mk_gate(client, pid, rid, "p1", gate_type="plan_presentation")
    paused.add(plan)

    # 连 gate_id 都不传也应命中（它是该 run+stage 唯一的可决策待决 Gate）
    r = _promote(client, pid, rid, "p1", "approve")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["graph_driven"] is True
    assert drives == [{"run_id": rid, "decision": "approve", "stage": "p1"}]


# ── T4：向后兼容（不传 gate_id、恰好 1 个候选）───────────────────────────

def test_t4_no_gate_id_single_candidate_behaves_as_before(client, graph_paused_on):
    """T4：不传 gate_id、该 run+stage 恰好 1 个待决晋级 Gate ⇒ 行为与修复前等价。

    既有 11 个调用方（5 个后端测试文件 + 前端 + 5 个 e2e 脚本）走的正是这条路径，
    本条是它们不必同批改动的依据。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p0")
    promo = _mk_gate(client, pid, rid, "p0")
    paused.add(promo)

    r = _promote(client, pid, rid, "p0", "approve")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["graph_driven"] is True
    assert drives == [{"run_id": rid, "decision": "approve", "stage": "p0"}]


def test_t4b_no_gate_id_single_candidate_direct_path_not_regressed(client, graph_paused_on):
    """T4 附：无图暂停点（非图 run）时，不传 gate_id 仍走直连晋级 p2 → p3（不回归）。"""
    paused, drives = graph_paused_on     # paused 保持为空
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    _mk_gate(client, pid, rid, "p2")

    r = _promote(client, pid, rid, "p2", "approve")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["graph_driven"] is False
    assert r.json()["data"]["transition_mode"] == "real"
    assert drives == []
    assert client.get(f"/api/projects/{pid}").json()["data"]["current_stage"] == "p3"


# ── T5：不传 gate_id、0 个候选（用户裁决 Q-A：这个用法应当失效）──────────

def test_t5_no_gate_id_zero_candidates_409_and_no_graph_drive(client, graph_paused_on):
    """T5：不传 gate_id、0 个待决 Gate ⇒ 409 且**不驱动图**。

    用户 2026-09-15 裁决 Q-A「失效吧」：确认"未建 Gate 就直接驱动图"的用法应当失效。
    0 个正是缺陷甲的形态 —— 没有可判定的对象却把决策注入图，图会把它应用到自己暂停的
    那个 Gate 上。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p0")
    # 刻意不建任何 Gate

    r = _promote(client, pid, rid, "p0", "approve")

    assert r.status_code == 409, r.text
    assert "无待决晋级 Gate" in r.text
    assert "请传 gate_id" in r.text
    assert drives == []


# ── T6：不传 gate_id、≥2 个候选（B-R22 真实产生过 5 个重复 Gate）──────────

def test_t6_no_gate_id_multiple_candidates_409_lists_them(client, graph_paused_on):
    """T6：不传 gate_id、≥2 个待决晋级 Gate ⇒ 409、列出候选、不驱动图。

    ≥2 的情形真实存在：`B-R22-GATE-REDECIDE-REDRIVE` 实测产生过 5 个重复的 p1
    stage_promotion Gate（全部 waiting_decision）。该缺陷已修（不再产生重复），
    但历史数据里仍有这类记录 ⇒ 推断逻辑必须处理多个的情形，不能假设唯一。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p1")
    g1 = _mk_gate(client, pid, rid, "p1")
    g2 = _mk_gate(client, pid, rid, "p1")

    r = _promote(client, pid, rid, "p1", "approve")

    assert r.status_code == 409, r.text
    assert g1 in r.text and g2 in r.text, "409 须列出候选 gate_id，便于调用方指名"
    assert drives == []
    for gid in (g1, g2):
        assert _gate(client, pid, gid)["gate_status"] == "waiting_decision"


# ── 边界锁：指名了别的对象 / 已决策对象 ──────────────────────────────────

def test_explicit_gate_id_from_another_stage_is_rejected(client, graph_paused_on):
    """传【别的 stage 的】gate_id ⇒ 409。否则只是换个入口重现同一缺陷：
    URL 说 p0、gate_id 指 p2，仍然是"决策作用在与请求不一致的对象上"。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    promo_p2 = _mk_gate(client, pid, rid, "p2")
    paused.add(promo_p2)

    r = _promote(client, pid, rid, "p0", "approve", gate_id=promo_p2)

    assert r.status_code == 409, r.text
    assert "不一致" in r.text
    assert drives == []
    assert _gate(client, pid, promo_p2)["gate_status"] == "waiting_decision"


def test_explicit_gate_id_from_another_run_is_rejected(client, graph_paused_on):
    """传【别的 run 的】gate_id ⇒ 409（跨 run 注入同样封死）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid_a = _mk_run(client, pid)
    rid_b = _mk_run(client, pid)
    _ensure_task_graph(rid_b, "p1")
    gate_b = _mk_gate(client, pid, rid_b, "p1")

    r = _promote(client, pid, rid_a, "p1", "approve", gate_id=gate_b)

    assert r.status_code == 409, r.text
    assert drives == []
    assert _gate(client, pid, gate_b)["gate_status"] == "waiting_decision"


def test_explicit_gate_id_already_decided_is_rejected(client, graph_paused_on):
    """指名一个已决策 Gate ⇒ 409，不重复驱动图。

    与 `B-R22-GATE-REDECIDE-REDRIVE` 同一危害形态：对已决策 Gate 再提交一次决策会再次
    resume 同一 checkpoint 线程 → 整个阶段被重跑（真跑实测：5 次重复审批 ⇒ 5 个重复
    Gate + 产物分两批 mtime 互相覆写）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p2")
    promo = _mk_gate(client, pid, rid, "p2")
    assert _promote(client, pid, rid, "p2", "approve", gate_id=promo).status_code == 200
    drives.clear()

    again = _promote(client, pid, rid, "p2", "approve", gate_id=promo)

    assert again.status_code == 409, again.text
    assert "不是待决对象" in again.text
    assert drives == []


def test_explicit_gate_id_unknown_is_404(client, graph_paused_on):
    """指名一个不存在的 gate_id ⇒ 404（诚实报不存在，不静默退回推断）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    _ensure_task_graph(rid, "p1")
    _mk_gate(client, pid, rid, "p1")           # 存在一个可推断的候选，但请求指名了别的

    r = _promote(client, pid, rid, "p1", "approve", gate_id="gate-nope01")

    assert r.status_code == 404, r.text
    assert drives == []


def test_illegal_decision_still_400_before_any_resolution(client, graph_paused_on):
    """非法 decision 仍 400（P1-B 既有守卫在最前，不被本次改动挤到 409 之后）。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)

    r = _promote(client, pid, rid, "p0", "bogus")

    assert r.status_code == 400, r.text
    assert drives == []


# ── T7 / T8：站点② —— resume 端点不得替 Gate 做决定 ─────────────────────

def test_t7_run_resume_refuses_decision_injection_while_paused_on_gate(
        client, graph_paused_on):
    """T7：/runs/{id}/resume 在图暂停于某 Gate 时提交 decision ⇒ 409，不注入。

    「恢复一个 run」与「决策一个 Gate」是两件事，前者不该能替后者做决定 —— 该端点
    连 gate_id 字段都没有，无法指名对象。"""
    paused, drives = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    promo = _mk_gate(client, pid, rid, "p1")
    paused.add(promo)

    r = client.post(f"/api/projects/{pid}/runs/{rid}/resume", json={"decision": "approve"})

    assert r.status_code == 409, r.text
    assert promo in r.text
    assert _gate(client, pid, promo)["gate_status"] == "waiting_decision"


def test_t7b_run_resume_unaffected_when_no_gate_pause(client, graph_paused_on):
    """T7 附：无图暂停点时 /runs/{id}/resume 行为不变（非图 run 的普通状态流转不受影响）。"""
    paused, _ = graph_paused_on            # paused 保持为空
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    client.post(f"/api/projects/{pid}/runs/{rid}/start")
    client.post(f"/api/projects/{pid}/runs/{rid}/pause")

    r = client.post(f"/api/projects/{pid}/runs/{rid}/resume", json={"decision": "approve"})

    assert r.status_code == 200, r.text
    assert r.json()["data"]["run_status"] == "running"


def test_t8_graph_resume_refuses_unnamed_decision_while_paused_on_gate(
        client, graph_paused_on):
    """T8：/graph/resume 不指名 gate_id 而图正暂停在某 Gate 上 ⇒ 409，不注入。"""
    paused, _ = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    promo = _mk_gate(client, pid, rid, "p1")
    paused.add(promo)

    r = client.post(f"/api/projects/{pid}/graph/resume",
                    json={"run_id": rid, "decision": "approve"})

    assert r.status_code == 409, r.text
    assert promo in r.text
    assert _gate(client, pid, promo)["gate_status"] == "waiting_decision"


def test_t8b_graph_resume_refuses_wrong_gate_id(client, graph_paused_on):
    """T8 附：/graph/resume 指名了【另一个】Gate ⇒ 同样 409（指名必须指对）。"""
    paused, _ = graph_paused_on
    pid = _mk_project(client)
    rid = _mk_run(client, pid)
    promo = _mk_gate(client, pid, rid, "p1")
    other = _mk_gate(client, pid, rid, "p1")
    paused.add(promo)

    r = client.post(f"/api/projects/{pid}/graph/resume",
                    json={"run_id": rid, "decision": "approve", "gate_id": other})

    assert r.status_code == 409, r.text
    assert _gate(client, pid, promo)["gate_status"] == "waiting_decision"
