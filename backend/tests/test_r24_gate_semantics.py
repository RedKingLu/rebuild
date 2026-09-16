"""V26.3 · R24 回归锁：三条 Gate 语义类台账项。

覆盖（每个测试的编号 = 报告《R24-代码与架构优化报告》优化立项表里的编号）：

  R24-01  `B-V262-GATEREASON-HARDCODED`（P1）
          晋级 Gate 文案必须按 `validation_verdict.verdict` 的**真实取值**分派；
          `rework_required` / `blocked` 时不得出现"通过独立验收"字样；覆盖 P0~P4
          （即不依赖 `metadata["rework_target_stage"]` 是否置位）；无 brief 的兜底支
          也不得断言未验证的结论。

  R24-02  `B-V262-ACTIVEGATE-NOORDER`（P1）
          `get_active()` 按**阻塞性语义**分流：流程阻塞类（图暂停点 / stage_promotion 等）
          优先于动作审批类（action_approval 等）。早建的长期挂起 L4 Gate 不得遮蔽
          后建的晋级 Gate。

  R24-03  `B-ACC-PROMOTE-DIRECT-RUNBLIND`（P1）
          `stage_service.promote()` 必须接受调用方已解析好的目标 Gate；内部兜底查找
          必须按 `run_id` 过滤；project 级 `get_active()` 兜底已删除。
          ⇒ "R17-2 校验的对象" 与 "决策实际落到的对象" 在直连分支上恒为同一个。

纪律：本文件只断言**行为**，不断言实现细节（不 monkeypatch 被测函数自身）。
"""

import uuid

import pytest

from app.dependencies import get_services
from app.services import workspace_service


# ── 公共装置（与 test_acc_promotion_decision_noguard.py 同构，便于对照阅读）────

def _mk_project(client, name="R24 Gate Semantics"):
    return client.post("/api/projects", json={
        "name": name, "source_type": "manual",
    }).json()["data"]["project_id"]


def _mk_run(client, pid, goal="r24"):
    return client.post(f"/api/projects/{pid}/runs",
                       json={"run_goal": goal, "mode": "plan"}).json()["data"]["run_id"]


def _mk_gate(client, pid, run_id, stage, gate_type="stage_promotion", risk_level="L0"):
    return client.post(f"/api/projects/{pid}/gates", json={
        "run_id": run_id, "stage": stage, "gate_type": gate_type,
        "reason": "r24 test", "summary": "r24 test", "risk_level": risk_level,
    }).json()["data"]["gate_id"]


def _ensure_task_graph(run_id: str, stage: str):
    """R17-2：stage_promotion 晋级强绑阶段产物，注入 task_graph 满足校验。"""
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph
    db = get_session()
    try:
        tg = (db.query(TaskGraph)
              .filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage).first())
        if tg is None:
            db.add(TaskGraph(task_graph_id=f"tg-r24-{uuid.uuid4().hex[:8]}", project_id="",
                             run_id=run_id, stage=stage, title=f"r24 {stage}",
                             graph_status="completed"))
            db.commit()
    finally:
        db.close()


def _write_brief(pid: str, stage: str, verdict, issues_count=0,
                 what_happened="阶段执行结束") -> str:
    """落一份真实的 `{stage}_gate_brief.json`，返回其相对 ref。

    verdict 传 None ⇒ 写一份**不含** validation_verdict 的 brief（对应"未取得结论"）。
    """
    import json
    rel = f"artifacts/{stage}/{stage}_gate_brief.json"
    p = workspace_service.workspace_path(pid) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"what_happened": what_happened, "honest_notes": ""}
    if verdict is not None:
        body["validation_verdict"] = {"verdict": verdict, "issues_count": issues_count}
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return rel


# ══════════════════════════════════════════════════════════════════════════════
# R24-01 · B-V262-GATEREASON-HARDCODED
# ══════════════════════════════════════════════════════════════════════════════

class TestGateReasonFollowsRealVerdict:
    """解除条件 ②：对四种 verdict 各断言一次文案。"""

    @pytest.mark.parametrize("verdict,must_contain,must_not_contain", [
        ("accepted", "已完成并通过独立验收", "未通过"),
        ("accepted_with_warning", "通过但带告警", "未通过"),
        ("rework_required", "未通过独立验收", "通过独立验收，请求"),
        ("blocked", "未通过独立验收", "已完成并通过"),
    ])
    def test_verdict_dispatch(self, isolated_data, verdict, must_contain, must_not_contain):
        from app.graph.gate_backend import RealGateBackend
        pid = f"r24-verdict-{verdict}"
        workspace_service.init_workspace(pid)
        ref = _write_brief(pid, "p2", verdict, issues_count=2)
        gid = RealGateBackend().create(
            project_id=pid, run_id="run-r24", stage="p2",
            artifact_refs=[ref], gate_type="stage_promotion", metadata=None)
        reason = get_services().gate_service.get(gid).reason
        assert must_contain in reason, reason
        assert must_not_contain not in reason, reason

    def test_rework_required_reason_has_no_pass_claim(self, isolated_data):
        """解除条件 ②后半句：`rework_required` 文案中不得含"通过"（任何形式的通过断言）。"""
        from app.graph.gate_backend import RealGateBackend
        pid = "r24-verdict-nopass"
        workspace_service.init_workspace(pid)
        ref = _write_brief(pid, "p2", "rework_required", issues_count=2)
        gid = RealGateBackend().create(
            project_id=pid, run_id="run-r24", stage="p2",
            artifact_refs=[ref], gate_type="stage_promotion", metadata=None)
        g = get_services().gate_service.get(gid)
        # "未通过" 含 "通过" 两字，故按"是否出现肯定式通过断言"逐项排除，而不是简单查子串。
        for forbidden in ("已完成并通过独立验收", "小循环通过", "已通过验收"):
            assert forbidden not in g.reason, g.reason
        assert "未通过独立验收，判定需要返工" in g.reason
        assert "批准即在验收未通过/未知的情况下晋级" in g.reason

    @pytest.mark.parametrize("stage", ["p0", "p1", "p2", "p3", "p4"])
    def test_covers_p0_to_p4_without_rework_metadata(self, isolated_data, stage):
        """解除条件 ③：不依赖 `metadata["rework_target_stage"]`（该字段只有 P5 路径会写）。

        旧代码在 P0~P4 判 rework_required 时全部落进硬编码"已完成并通过独立验收"。
        """
        from app.graph.gate_backend import RealGateBackend
        pid = f"r24-p04-{stage}"
        workspace_service.init_workspace(pid)
        ref = _write_brief(pid, stage, "rework_required", issues_count=1)
        gid = RealGateBackend().create(
            project_id=pid, run_id="run-r24", stage=stage,
            artifact_refs=[ref], gate_type="stage_promotion", metadata=None)
        reason = get_services().gate_service.get(gid).reason
        assert "已完成并通过独立验收" not in reason, reason
        assert "未通过独立验收" in reason, reason

    def test_unknown_verdict_is_not_treated_as_pass(self, isolated_data):
        """未登记的 verdict 取值绝不默认成通过（fail-closed 的文案版本）。"""
        from app.graph.gate_backend import RealGateBackend
        pid = "r24-verdict-unknown"
        workspace_service.init_workspace(pid)
        ref = _write_brief(pid, "p3", "some_new_verdict_value")
        gid = RealGateBackend().create(
            project_id=pid, run_id="run-r24", stage="p3",
            artifact_refs=[ref], gate_type="stage_promotion", metadata=None)
        reason = get_services().gate_service.get(gid).reason
        assert "独立验收结论无法识别" in reason, reason
        assert "已完成并通过独立验收" not in reason

    def test_brief_without_verdict_says_no_conclusion(self, isolated_data):
        """brief 存在但没有 validation_verdict ⇒ 诚实写"未取得独立验收结论"。"""
        from app.graph.gate_backend import RealGateBackend
        pid = "r24-verdict-absent"
        workspace_service.init_workspace(pid)
        ref = _write_brief(pid, "p1", None)
        gid = RealGateBackend().create(
            project_id=pid, run_id="run-r24", stage="p1",
            artifact_refs=[ref], gate_type="stage_promotion", metadata=None)
        reason = get_services().gate_service.get(gid).reason
        assert "未取得独立验收结论" in reason, reason

    def test_no_brief_fallback_does_not_assert_pass(self, isolated_data):
        """解除条件 ④：无 brief 的兜底支不得断言"小循环通过"这类未验证结论。"""
        from app.graph.gate_backend import RealGateBackend
        pid = "r24-verdict-nobrief"
        workspace_service.init_workspace(pid)
        gid = RealGateBackend().create(
            project_id=pid, run_id="run-r24", stage="p2",
            artifact_refs=[], gate_type="stage_promotion", metadata=None)
        g = get_services().gate_service.get(gid)
        assert "小循环通过" not in g.reason
        assert "未能读取独立验收报告" in g.reason
        assert "已完成并产出三类审核报告" not in g.summary

    def test_rework_metadata_branch_still_wins(self, isolated_data):
        """防回归：P5 的 `rework_target_stage` 显式返工分支优先级不变（批次 H 的既有行为）。"""
        from app.graph.gate_backend import RealGateBackend
        pid = "r24-verdict-metafirst"
        workspace_service.init_workspace(pid)
        ref = _write_brief(pid, "p5", "accepted")
        gid = RealGateBackend().create(
            project_id=pid, run_id="run-r24", stage="p5",
            artifact_refs=[ref], gate_type="stage_promotion",
            metadata={"rework_target_stage": "p4", "rework_reason": "构建失败"})
        reason = get_services().gate_service.get(gid).reason
        assert "判定需要返工" in reason
        assert "P4" in reason


# ══════════════════════════════════════════════════════════════════════════════
# R24-02 · B-V262-ACTIVEGATE-NOORDER
# ══════════════════════════════════════════════════════════════════════════════

class TestActiveGateBlockingPriority:
    def test_stale_action_gate_does_not_mask_later_promotion_gate(self, client):
        """解除条件 ③（台账原文场景）：早建的未决 L4 action_approval + 晚建的未决晋级 Gate
        ⇒ `get_active()` 必须返回晋级 Gate。

        旧实现无 ORDER BY，SQLite 返回最早那条 ⇒ 永久返回 action_approval，
        前端 StagePageP4/P5/P6 消费本端点 ⇒ 晋级 Gate 在 UI 上永不可见。
        """
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        action_gid = _mk_gate(client, pid, rid, "p0",
                              gate_type="action_approval", risk_level="L4")
        promo_gid = _mk_gate(client, pid, rid, "p2", gate_type="stage_promotion")

        active = get_services().gate_service.get_active(pid)
        assert active is not None
        assert active.gate_id == promo_gid, (
            f"晋级 Gate {promo_gid} 被 action_approval {action_gid} 遮蔽了")

    def test_action_gate_still_returned_when_it_is_the_only_pending(self, client):
        """反向锁：只有 action_approval 待决时仍须返回它（不得因分流而"看不见"动作审批）。"""
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        action_gid = _mk_gate(client, pid, rid, "p0",
                              gate_type="action_approval", risk_level="L4")
        active = get_services().gate_service.get_active(pid)
        assert active is not None and active.gate_id == action_gid

    def test_decided_gates_are_never_active(self, client):
        """防回归：已决策 Gate 不是 active（既有"可决策"定义不变）。"""
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        gid = _mk_gate(client, pid, rid, "p0", gate_type="action_approval", risk_level="L4")
        from app.schemas.gate import GateDecisionRequest
        get_services().gate_service.decide(gid, GateDecisionRequest(decision="approve"),
                                           drive_promotion=False)
        assert get_services().gate_service.get_active(pid) is None

    def test_endpoint_reflects_the_same_priority(self, client):
        """端点级（前端真实消费面）：GET /gates/active 与 service 层结论一致。"""
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        _mk_gate(client, pid, rid, "p0", gate_type="action_approval", risk_level="L4")
        promo_gid = _mk_gate(client, pid, rid, "p3", gate_type="stage_promotion")
        body = client.get(f"/api/projects/{pid}/gates/active").json()["data"]
        assert body["gate_id"] == promo_gid


# ══════════════════════════════════════════════════════════════════════════════
# R24-03 · B-ACC-PROMOTE-DIRECT-RUNBLIND
# ══════════════════════════════════════════════════════════════════════════════

class TestPromoteDirectRunBinding:
    def test_decision_on_run_a_leaves_run_b_gate_untouched(self, client):
        """解除条件 ④：同 project 两个 run 在同一 stage 各有待决晋级 Gate ⇒
        对 run A 提交决策，run B 的 Gate **状态不变**。

        旧实现：`promote()` 按 stage 全局查找、无 run_id 过滤，`list_by_project` 按
        `gate_id`（uuid hex，与时间无关）排序 ⇒ 命中哪个取决于 uuid 字符串序 ⇒
        对 run A 的决策可能落到 run B 的 Gate 上。
        """
        pid = _mk_project(client)
        rid_a = _mk_run(client, pid, "run A")
        rid_b = _mk_run(client, pid, "run B")
        _ensure_task_graph(rid_a, "p2")
        _ensure_task_graph(rid_b, "p2")
        gate_a = _mk_gate(client, pid, rid_a, "p2")
        gate_b = _mk_gate(client, pid, rid_b, "p2")

        resp = client.post(
            f"/api/projects/{pid}/runs/{rid_a}/stages/p2/promotion-decision",
            json={"decision": "approve", "reason": "R24-03 锁定", "gate_id": gate_a})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["gate_id"] == gate_a

        gs = get_services().gate_service
        assert gs.get(gate_a).gate_status == "approved"
        assert gs.get(gate_b).gate_status == "waiting_decision", (
            "run B 的 Gate 被 run A 的决策连带改掉了")

    def test_run_b_cannot_borrow_run_a_artifacts_for_shell_promotion(self, client):
        """解除条件 ⑤：run B 无真实产物时，不得借 run A 的产物通过 R17-2 空壳晋级校验。

        run A 有 task_graph、run B 没有 ⇒ 对 run B 提交 approve 必须 422。
        旧实现下 `promote()` 可能挑中 run A 的 Gate（它有产物）从而"通过"。
        """
        pid = _mk_project(client)
        rid_a = _mk_run(client, pid, "run A")
        rid_b = _mk_run(client, pid, "run B")
        _ensure_task_graph(rid_a, "p2")          # 只有 A 有产物
        gate_a = _mk_gate(client, pid, rid_a, "p2")
        gate_b = _mk_gate(client, pid, rid_b, "p2")

        resp = client.post(
            f"/api/projects/{pid}/runs/{rid_b}/stages/p2/promotion-decision",
            json={"decision": "approve", "reason": "R24-03 空壳", "gate_id": gate_b})
        assert resp.status_code == 422, resp.text
        assert "无真实产物" in resp.text

        gs = get_services().gate_service
        assert gs.get(gate_b).gate_status == "waiting_decision"
        assert gs.get(gate_a).gate_status == "waiting_decision", "run A 的 Gate 不该被牵连"

    def test_promote_uses_the_gate_the_caller_resolved(self, client):
        """解除条件 ①：`promote()` 用调用方传入的 target_gate_id，不自己重查。"""
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        _ensure_task_graph(rid, "p2")
        gid = _mk_gate(client, pid, rid, "p2")
        from app.schemas.stage import PromotionDecision
        out = get_services().stage_service.promote(
            pid, rid, "p2", PromotionDecision(decision="approve", reason="直传"),
            drive_promotion=True, target_gate_id=gid)
        assert out["gate_id"] == gid
        assert get_services().gate_service.get(gid).gate_status == "approved"

    def test_route_passes_the_resolved_gate_down_to_promote(self, client):
        """解除条件 ①的**端点级**锁：路由必须把 `_resolve_promotion_target()` 的结果传下去。

        本例是变异验证补出来的（M3：把 `target_gate_id=target_gate_id` 改成 `None` 时，
        原有 6 条用例**全绿** —— 因为 promote() 的内部兜底已按 run_id 过滤，单个待决 Gate
        的场景下它会重新查到同一个）。要让"传不传"可被观测，必须构造一个
        **内部兜底判不出、只有调用方指名才能判出**的场景：
        同 run+stage 有两个待决晋级 Gate ⇒ 路由用显式 gate_id 指名 Gate A 后，
        promote() 若不接受该指名就只能报"有 2 个待决晋级 Gate"（400），决策落不下去。
        """
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        _ensure_task_graph(rid, "p2")
        gate_a = _mk_gate(client, pid, rid, "p2")
        gate_b = _mk_gate(client, pid, rid, "p2")
        resp = client.post(
            f"/api/projects/{pid}/runs/{rid}/stages/p2/promotion-decision",
            json={"decision": "approve", "reason": "指名 A", "gate_id": gate_a})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["gate_id"] == gate_a
        gs = get_services().gate_service
        assert gs.get(gate_a).gate_status == "approved"
        assert gs.get(gate_b).gate_status == "waiting_decision"

    def test_promote_internal_fallback_filters_by_run_id(self, client):
        """解除条件 ②：不传 target_gate_id 时，内部兜底查找必须按 run_id 过滤。"""
        pid = _mk_project(client)
        rid_a = _mk_run(client, pid, "run A")
        rid_b = _mk_run(client, pid, "run B")
        _ensure_task_graph(rid_a, "p2")
        _ensure_task_graph(rid_b, "p2")
        gate_a = _mk_gate(client, pid, rid_a, "p2")
        gate_b = _mk_gate(client, pid, rid_b, "p2")
        from app.schemas.stage import PromotionDecision
        out = get_services().stage_service.promote(
            pid, rid_b, "p2", PromotionDecision(decision="approve"), drive_promotion=True)
        assert out["gate_id"] == gate_b, "兜底查找跨 run 命中了"
        assert get_services().gate_service.get(gate_a).gate_status == "waiting_decision"

    def test_promote_no_longer_falls_back_to_project_level_active_gate(self, client):
        """解除条件 ③：已删除 `get_active(project_id)` 兜底。

        场景：run 在 p3 上**没有**待决晋级 Gate，但项目里有一个别的待决 Gate
        （另一 stage 的 action_approval）。旧实现会经 `get_active()` 抓到它并把 p3 的
        决策写到它身上；现在必须诚实报"无待决 Gate 可决策"。
        """
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        stray = _mk_gate(client, pid, rid, "p0",
                         gate_type="action_approval", risk_level="L4")
        from app.schemas.stage import PromotionDecision
        with pytest.raises(ValueError, match="无待决 Gate 可决策"):
            get_services().stage_service.promote(
                pid, rid, "p3", PromotionDecision(decision="approve"), drive_promotion=True)
        assert get_services().gate_service.get(stray).gate_status == "waiting_decision"

    def test_promote_refuses_to_guess_between_duplicates(self, client):
        """同 run+stage 多个待决晋级 Gate ⇒ 不猜，报错要求指名（与路由 409 同口径）。"""
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        _ensure_task_graph(rid, "p2")
        _mk_gate(client, pid, rid, "p2")
        _mk_gate(client, pid, rid, "p2")
        from app.schemas.stage import PromotionDecision
        with pytest.raises(ValueError, match="个待决晋级 Gate"):
            get_services().stage_service.promote(
                pid, rid, "p2", PromotionDecision(decision="approve"), drive_promotion=True)
