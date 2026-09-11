"""V26.2 返工修复第 8 项（Q-RW-4）：P5 判定 rework_required → 送回 P4 的完整闭环。

背景：真实规模真跑中，P5 判定 `rework_required`，但对应 Gate（`gate-401a97`）的文案却
写"p5 阶段已完成并产出三类审核报告，请审阅后决策"——与真实结论矛盾。根因三层：
  A. `gate_backend._read_gate_brief` 命中率：升级到 Gate 的分支从不把 `{stage}_gate_brief.json`
     带进 artifact_refs（那是"通过"分支才做的事），导致读不到真实 brief、落入误导性罐头话。
  B. `P5FailureRouter.route()` 算出的 `p4_rework_required`/`plan_delta_type`/`plan_delta_reason`
     写进一个后续被丢弃的局部 dict，全库零消费方——PlanDelta 表 0 行、图路由从不退回 P4。
  C. `make_router` 只会前进/原地重试/结束，从不会退回上一阶段；`gate_service._apply_promotion`
     的 approve 分支无条件推进 `current_stage`，与图路由是两条独立机制，必须同时改对。

本文件覆盖：
  ① RealP5Handler.review() 现在把这几个字段作为显式 key 塞进 issue dict（唯一能原样穿过
     ValidationAgent 聚合、活到 StageLoopResult.rounds 的通道）
  ② gate_service.write/read_p5_rework_marker（图路由与直连晋级路径共享的落盘信号源）
  ③ make_router("p5") 读到该信号时路由回 "p4_work"；不存在时（含其它阶段）行为不变
  ④ make_work_node("p5") 的升级分支：正确补挂 gate_brief ref（A 部分根因修复）+
     创建 PlanDelta（复用既有 create_plan_delta_for_failure）+ 落盘返工标记 + 传递
     metadata 供 gate_backend 合成诚实文案
  ⑤ gate_backend.RealGateBackend.create() 的第三种文案分支（诚实说明"需要返工"）
  ⑥ gate_service._apply_promotion 的直连晋级路径与图路由读同一份标记，二者不给出矛盾结论
  ⑦ 大量防回归：P0/P2/P4 等其它阶段的正常晋级路径、reject/request_changes 路径、
     plan_approved 跳过逻辑，均不受本次改动影响
"""

from __future__ import annotations

import uuid

import pytest

from app.graph import nodes
from app.graph.nodes import make_gate_node, make_router, make_work_node
from app.graph.stage_handlers import RealP5Handler
from app.services import workspace_service
from app.services.gate_service import read_p5_rework_marker, write_p5_rework_marker


# ── 共享测试 fixture / 小工具 ────────────────────────────────────────────────

def _ensure_task_graph(run_id: str, stage: str) -> None:
    """R17-2 V-R17-1B-2: gate 晋级强绑阶段产物。测试需注入 task_graph 以满足校验
    （与 tests/test_gate_promotion_p2p3.py 同一套既有约定）。"""
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph
    db = get_session()
    try:
        tg = db.query(TaskGraph).filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage).first()
        if tg is None:
            db.add(TaskGraph(
                task_graph_id=f"tg-test-{uuid.uuid4().hex[:8]}", project_id="",
                run_id=run_id, stage=stage, title=f"test gate {stage}",
                graph_status="completed",
            ))
            db.commit()
    finally:
        db.close()


@pytest.fixture
def project_run(client):
    pid = client.post("/api/projects", json={"name": "RW8 Gate", "source_type": "manual"}).json()["data"]["project_id"]
    rid = client.post(f"/api/projects/{pid}/runs", json={"run_goal": "rw8", "mode": "plan"}).json()["data"]["run_id"]
    return pid, rid


# ══════════════════════════════════════════════════════════════════════════
# ① RealP5Handler.review()：显式信号（stage_handlers.py 改动）
# ══════════════════════════════════════════════════════════════════════════

class TestP5ReviewExplicitRework:
    def test_build_failed_issue_carries_explicit_rework_fields(self):
        """构建失败（p4_rework_required=True 的四种失败类型之一）→ issue dict 显式携带
        p4_rework_required/plan_delta_type/plan_delta_reason（唯一能穿过 ValidationAgent
        聚合活下来的通道）。"""
        handler = RealP5Handler()
        result = {
            "status": "blocked", "reason": "P5 全量验证未通过",
            "conditional_results": [
                {"slot_id": "build_verified", "command": "dotnet build",
                 "status": "validation_failed", "passed": False, "stderr_tail": "NU1605"},
            ],
        }
        review = handler.review(result)
        assert review.passed is False
        issue = review.issues[0]
        assert issue.get("p4_rework_required") is True
        assert issue.get("plan_delta_type") == "blocking_adjustment"
        assert issue.get("plan_delta_reason")

    def test_l4_risk_issue_has_no_rework_fields(self):
        """防回归：不需要退回 P4 的失败类型（L4/L5 高风险 → Gate 拦截，不是代码缺陷）
        不应携带这些新字段——信号必须显式且窄，不能泛滥到所有失败类型上。"""
        handler = RealP5Handler()
        result = {
            "status": "blocked", "reason": "L4 risk",
            "conditional_results": [
                {"slot_id": "build_verified", "command": "chmod 777 /",
                 "status": "needs_user_input", "passed": False,
                 "gate_required": True, "gate_reason": "L4 risk"},
            ],
        }
        review = handler.review(result)
        assert review.passed is False
        assert "p4_rework_required" not in review.issues[0]

    def test_existing_recommendation_text_unaffected(self):
        """防回归：既有"回 P4 执行修复"建议文案不因本次改动而消失或变形。"""
        handler = RealP5Handler()
        review = handler.review({"status": "blocked", "reason": "output_code 缺失"})
        assert review.passed is False
        assert any("P4" in r for r in review.recommendations)

    def test_completed_still_passes(self):
        """防回归：completed 状态仍直接判过，不受本次改动影响。"""
        handler = RealP5Handler()
        review = handler.review({"status": "completed"})
        assert review.passed is True
        assert review.issues == []


# ══════════════════════════════════════════════════════════════════════════
# ② gate_service.write/read_p5_rework_marker
# ══════════════════════════════════════════════════════════════════════════

class TestReworkMarkerRoundTrip:
    def test_write_then_read_matching_gate_id(self, isolated_data):
        pid = "marker-1"
        workspace_service.init_workspace(pid)
        write_p5_rework_marker(pid, gate_id="gate-aaa", run_id="run-1",
                               target_stage="p4", reason="构建失败", plan_delta_id="pd-1")
        marker = read_p5_rework_marker(pid, "gate-aaa")
        assert marker is not None
        assert marker["rework_target_stage"] == "p4"
        assert marker["reason"] == "构建失败"
        assert marker["plan_delta_id"] == "pd-1"

    def test_read_with_mismatched_gate_id_returns_none(self, isolated_data):
        """防止旧一轮返工留下的标记误伤之后真正通过的同阶段 Gate。"""
        pid = "marker-2"
        workspace_service.init_workspace(pid)
        write_p5_rework_marker(pid, gate_id="gate-old", run_id="run-1",
                               target_stage="p4", reason="上一轮返工")
        assert read_p5_rework_marker(pid, "gate-new") is None

    def test_read_without_any_marker_returns_none(self, isolated_data):
        pid = "marker-3"
        workspace_service.init_workspace(pid)
        assert read_p5_rework_marker(pid, "gate-whatever") is None


# ══════════════════════════════════════════════════════════════════════════
# ③ make_router("p5")：显式信号驱动路由 + 防回归
# ══════════════════════════════════════════════════════════════════════════

class TestMakeRouterP5Rework:
    def test_approve_with_rework_target_routes_to_p4_work(self):
        route = make_router("p5")
        assert route({"last_decision": "approve", "rework_target_stage": "p4"}) == "p4_work"

    def test_approve_without_rework_target_promotes_to_p6_work(self):
        route = make_router("p5")
        assert route({"last_decision": "approve", "rework_target_stage": None}) == "p6_work"

    def test_approve_missing_rework_field_entirely_promotes_normally(self):
        """字段压根不存在（对应"从未落过标记"的正常场景）与显式 None 行为一致。"""
        route = make_router("p5")
        assert route({"last_decision": "approve"}) == "p6_work"

    def test_plan_approved_takes_priority_over_rework_signal(self):
        """plan_approved 判断在前——即便（异常情况下）rework_target_stage 恰好也非空，
        仍应先满足 plan_presentation 语义，回到本阶段 work 节点，不被返工信号抢先。"""
        route = make_router("p5")
        assert route({"last_decision": "approve", "plan_approved": True,
                      "rework_target_stage": "p4"}) == "p5_work"

    def test_reject_unaffected(self):
        route = make_router("p5")
        assert route({"last_decision": "reject", "rework_target_stage": "p4"}) == "__end__"

    def test_request_changes_unaffected(self):
        route = make_router("p5")
        assert route({"last_decision": "request_changes", "rework_target_stage": "p4"}) == "p5_work"


class TestMakeRouterOtherStagesRegression:
    """防回归：非 p5 阶段完全不读 rework_target_stage，哪怕它意外非空（防止本次改动
    泄漏到其它阶段）——逐字节保持改动前的行为。"""

    @pytest.mark.parametrize("stage,expected_next", [
        ("p0", "p1_work"), ("p1", "p2_work"), ("p2", "p3_work"),
        ("p3", "p4_work"), ("p4", "p5_work"), ("p6", "__end__"),
    ])
    def test_normal_approve_unaffected(self, stage, expected_next):
        route = make_router(stage)
        assert route({"last_decision": "approve"}) == expected_next

    @pytest.mark.parametrize("stage", ["p0", "p1", "p2", "p3", "p4", "p6"])
    def test_stray_rework_field_ignored_for_other_stages(self, stage):
        route = make_router(stage)
        state = {"last_decision": "approve", "rework_target_stage": "p4"}
        # 非 p5 阶段：这个字段必须被完全忽略，走原有"前进到下一阶段"逻辑
        from app.graph.state import next_stage
        nxt = next_stage(stage)
        expected = f"{nxt}_work" if nxt else "__end__"
        assert route(state) == expected

    @pytest.mark.parametrize("stage", ["p0", "p2", "p4"])
    def test_reject_and_request_changes_unaffected(self, stage):
        route = make_router(stage)
        assert route({"last_decision": "reject"}) == "__end__"
        assert route({"last_decision": "request_changes"}) == f"{stage}_work"

    @pytest.mark.parametrize("stage", ["p0", "p2", "p4"])
    def test_plan_approved_short_circuit_unaffected(self, stage):
        route = make_router(stage)
        assert route({"last_decision": "approve", "plan_approved": True}) == f"{stage}_work"


# ══════════════════════════════════════════════════════════════════════════
# ④ make_gate_node("p5") 节点级：approve 时依落盘标记决定 current_stage/stage_status
# ══════════════════════════════════════════════════════════════════════════

class _NoopGateBackend:
    """gate() 节点只需要 decide() 不抛异常；create() 本测试不会用到。"""

    def create(self, **kw):
        return "unused"

    def decide(self, *, gate_id, decision):
        pass


async def test_gate_node_p5_approve_with_matching_marker_routes_state_to_p4(monkeypatch, isolated_data):
    pid = "gatep5-rw"
    workspace_service.init_workspace(pid)
    write_p5_rework_marker(pid, gate_id="gate-abc", run_id="run-1",
                          target_stage="p4", reason="构建失败")

    gate_fn = make_gate_node("p5")
    monkeypatch.setattr(nodes, "interrupt", lambda payload: "approve")
    nodes.set_gate_backend(_NoopGateBackend())
    try:
        state = {"project_id": pid, "run_id": "run-1",
                 "pending_gate": {"gate_id": "gate-abc", "stage": "p5", "gate_type": "stage_promotion"}}
        upd = await gate_fn(state)
    finally:
        nodes.set_gate_backend(None)

    assert upd["rework_target_stage"] == "p4"
    assert upd["current_stage"] == "p4"
    assert upd["stage_status"] == {"p5": "rework_required", "p4": "in_progress"}
    assert upd["plan_approved"] is False


async def test_gate_node_p5_approve_without_matching_marker_promotes_normally(monkeypatch, isolated_data):
    """无标记（或标记 gate_id 不匹配）→ 逐字节走原有"晋级到下一阶段"逻辑（防回归）。"""
    pid = "gatep5-normal"
    workspace_service.init_workspace(pid)
    # 故意留一份不匹配 gate_id 的旧标记，证明它不会被误采信
    write_p5_rework_marker(pid, gate_id="gate-old-round", run_id="run-1",
                          target_stage="p4", reason="上一轮返工")

    gate_fn = make_gate_node("p5")
    monkeypatch.setattr(nodes, "interrupt", lambda payload: "approve")
    nodes.set_gate_backend(_NoopGateBackend())
    try:
        state = {"project_id": pid, "run_id": "run-1",
                 "pending_gate": {"gate_id": "gate-xyz", "stage": "p5", "gate_type": "stage_promotion"}}
        upd = await gate_fn(state)
    finally:
        nodes.set_gate_backend(None)

    assert upd["rework_target_stage"] is None
    assert upd["current_stage"] == "p6"
    assert upd["stage_status"] == {"p5": "completed", "p6": "in_progress"}
    assert upd["plan_approved"] is False


async def test_gate_node_other_stage_approve_unaffected_by_marker(monkeypatch, isolated_data):
    """防回归：非 p5 阶段的 gate() 完全不做标记检查（哪怕碰巧存在一份 p5 标记）。"""
    pid = "gatep4-normal"
    workspace_service.init_workspace(pid)
    write_p5_rework_marker(pid, gate_id="gate-p4-test", run_id="run-1",
                          target_stage="p4", reason="不应影响 p4 自己的晋级")

    gate_fn = make_gate_node("p4")
    monkeypatch.setattr(nodes, "interrupt", lambda payload: "approve")
    nodes.set_gate_backend(_NoopGateBackend())
    try:
        state = {"project_id": pid, "run_id": "run-1",
                 "pending_gate": {"gate_id": "gate-p4-test", "stage": "p4", "gate_type": "stage_promotion"}}
        upd = await gate_fn(state)
    finally:
        nodes.set_gate_backend(None)

    assert "rework_target_stage" not in upd
    assert upd["current_stage"] == "p5"
    assert upd["stage_status"] == {"p4": "completed", "p5": "in_progress"}


async def test_gate_node_reject_and_request_changes_unaffected(monkeypatch, isolated_data):
    """防回归：reject / request_changes 两条既有路径完全不受影响。"""
    pid = "gatep5-rr"
    workspace_service.init_workspace(pid)
    gate_fn = make_gate_node("p5")
    nodes.set_gate_backend(_NoopGateBackend())
    try:
        monkeypatch.setattr(nodes, "interrupt", lambda payload: "reject")
        state = {"project_id": pid, "run_id": "run-1",
                 "pending_gate": {"gate_id": "gate-r1", "stage": "p5", "gate_type": "stage_promotion"}}
        upd = await gate_fn(dict(state))
        assert upd["stage_status"] == {"p5": "blocked"}
        assert upd["run_status"] == "blocked"

        monkeypatch.setattr(nodes, "interrupt", lambda payload: "request_changes")
        upd2 = await gate_fn(dict(state))
        assert upd2["stage_status"] == {"p5": "changes_requested"}
    finally:
        nodes.set_gate_backend(None)


async def test_gate_node_plan_presentation_unaffected(monkeypatch, isolated_data):
    """防回归：plan_presentation 类型的 gate 批准逻辑完全不涉及返工标记检查
    （该检查只在 gate_type != plan_presentation 的 else 分支里）。"""
    pid = "gatep5-planpres"
    workspace_service.init_workspace(pid)
    gate_fn = make_gate_node("p5")
    monkeypatch.setattr(nodes, "interrupt", lambda payload: "approve")
    nodes.set_gate_backend(_NoopGateBackend())
    try:
        state = {"project_id": pid, "run_id": "run-1",
                 "pending_gate": {"gate_id": "gate-pp1", "stage": "p5", "gate_type": "plan_presentation"}}
        upd = await gate_fn(state)
    finally:
        nodes.set_gate_backend(None)
    assert upd["stage_status"] == {"p5": "plan_approved"}
    assert "rework_target_stage" not in upd
    assert "current_stage" not in upd


# ══════════════════════════════════════════════════════════════════════════
# ⑤ make_work_node("p5") 升级分支：A 部分 gate_brief 修复 + B 部分闭环
# ══════════════════════════════════════════════════════════════════════════

class _FakeWorkAgentResult:
    def __init__(self):
        self.work_plan_ref = "artifacts/p5/p5_work_plan.json"
        self.gate_brief_ref = "artifacts/p5/p5_gate_brief.json"
        self.gate_brief_partial = {
            "what_happened": "P5 阶段未完成（status=blocked）",
            "key_artifacts": [], "risks": [], "honest_notes": "",
        }
        self.claim_evidence_map_ref = "artifacts/p5/p5_claim_evidence_map.json"


class _FakeWorkAgent:
    """替身：绕开真实 WorkAgent 的 LLM 调用，只保留 nodes.py 需要的接口形状
    （stage/execute/last_result/set_rework_feedback），execute() 返回一个会被
    RealP5Handler.review() 判定为 build_failed 的确定性结果。"""

    def __init__(self, stage, project_id, run_id, *, tracer=None, auditor=None, handler=None):
        self.stage, self.project_id, self.run_id = stage, project_id, run_id
        self.last_result = None

    def set_rework_feedback(self, feedback):
        pass

    async def execute(self, state):
        self.last_result = _FakeWorkAgentResult()
        return {
            "status": "blocked", "reason": "P5 全量验证未通过：构建失败",
            "project_id": self.project_id, "run_id": self.run_id,
            "conditional_results": [
                {"slot_id": "build_verified", "command": "dotnet build",
                 "status": "validation_failed", "passed": False, "stderr_tail": "NU1605"},
            ],
        }


class _FakeValidationResult:
    def __init__(self, passed, verdict, issues):
        self.verdict = verdict
        self.passed = passed
        self.issues = issues
        self.agent_id = "va-test"
        self.claim_evidence_verification = {}


class _FakeValidationAgent:
    """替身：直接复用真实 handler.review()（本次改动的核心）判定 pass/fail，
    绕开 ValidationAgent 真实的磁盘重读/AcceptanceService/LLM 语义校验链路——
    那些是另一套已有测试覆盖的机制，本测试只关心 nodes.py 如何消费 review() 的输出。"""

    def __init__(self, stage, project_id, run_id, *, tracer=None, auditor=None, handler=None):
        self.stage, self.project_id, self.run_id = stage, project_id, run_id
        self._handler = handler
        self.last_result = None

    def validate(self, work_result):
        rr = self._handler.review(work_result)
        verdict = "accepted" if rr.passed else "rework_required"
        self.last_result = _FakeValidationResult(rr.passed, verdict, rr.issues)
        return rr


class _RecordingGateBackend:
    def __init__(self):
        self.created = []

    def create(self, *, project_id, run_id, stage, artifact_refs,
               gate_type="stage_promotion", metadata=None):
        gid = f"gate-test-{len(self.created)}"
        self.created.append({
            "gate_id": gid, "project_id": project_id, "run_id": run_id, "stage": stage,
            "artifact_refs": list(artifact_refs or []), "gate_type": gate_type,
            "metadata": dict(metadata or {}),
        })
        return gid

    def decide(self, *, gate_id, decision):
        pass


async def test_p5_work_node_escalation_creates_delta_marker_and_honest_metadata(monkeypatch, isolated_data):
    import app.services.work_agent as wa_mod
    import app.services.validation_agent as va_mod
    monkeypatch.setattr(wa_mod, "WorkAgent", _FakeWorkAgent)
    monkeypatch.setattr(va_mod, "ValidationAgent", _FakeValidationAgent)

    pid = "p5wn-1"
    rid = "run-p5wn-1"
    workspace_service.init_workspace(pid)

    nodes.clear_handlers()
    nodes.register_handler("p5", RealP5Handler())
    gb = _RecordingGateBackend()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    try:
        work_fn = make_work_node("p5")
        state = {"project_id": pid, "run_id": rid, "execution_mode": "auto", "plan_approved": True}
        result = await work_fn(state)
    finally:
        nodes.clear_handlers()
        nodes.set_gate_backend(None)

    assert len(gb.created) == 1, gb.created
    created = gb.created[0]

    # A 部分根因修复：升级到 Gate 的分支现在也补挂了 gate_brief ref
    assert any(r.endswith("_gate_brief.json") for r in created["artifact_refs"]), \
        f"gate_brief 未被补挂：{created['artifact_refs']}"

    # B-4：Gate 携带显式 metadata，供 gate_backend 合成"需要返工"文案
    assert created["gate_type"] == "stage_promotion"
    assert created["metadata"].get("rework_target_stage") == "p4"
    assert created["metadata"].get("rework_reason")

    # B-1：PlanDelta 记录被创建（stage=p4，复用既有 create_plan_delta_for_failure/
    # PlanDeltaService.create_delta，不新造服务）
    from app.core.database import get_session
    from app.models.plan_delta import PlanDelta
    db = get_session()
    try:
        deltas = db.query(PlanDelta).filter(PlanDelta.project_id == pid).all()
    finally:
        db.close()
    assert len(deltas) == 1, "应且只应创建一条 PlanDelta 记录"
    delta = deltas[0]
    assert delta.stage == "p4"
    assert delta.run_id == rid
    assert delta.delta_type == "blocking_adjustment"
    assert delta.reason

    # B-2：落盘的返工标记 gate_id 与刚创建的 Gate 一致，且记录了 plan_delta_id
    marker = read_p5_rework_marker(pid, created["gate_id"])
    assert marker is not None
    assert marker["rework_target_stage"] == "p4"
    assert marker["plan_delta_id"] == delta.plan_delta_id

    # 图状态：work() 自身仍诚实停在 p5（真正的阶段切换要等这个 Gate 被批准后由
    # gate()/make_router 决定，不是 work() 自己越权切换）
    assert result["current_stage"] == "p5"
    assert result["pending_gate"]["gate_id"] == created["gate_id"]


async def test_p5_work_node_passed_path_unaffected(monkeypatch, isolated_data):
    """防回归：P5 正常通过（不失败）时的"通过"分支完全不受本次改动影响——
    仍然创建 stage_promotion Gate，metadata 不携带任何返工字段，不落盘返工标记。"""
    import app.services.work_agent as wa_mod
    import app.services.validation_agent as va_mod

    class _FakePassWorkAgent(_FakeWorkAgent):
        async def execute(self, state):
            self.last_result = _FakeWorkAgentResult()
            return {"status": "completed", "project_id": self.project_id,
                   "run_id": self.run_id, "artifacts": []}

    monkeypatch.setattr(wa_mod, "WorkAgent", _FakePassWorkAgent)
    monkeypatch.setattr(va_mod, "ValidationAgent", _FakeValidationAgent)

    pid = "p5wn-pass"
    rid = "run-p5wn-pass"
    workspace_service.init_workspace(pid)

    nodes.clear_handlers()
    nodes.register_handler("p5", RealP5Handler())
    gb = _RecordingGateBackend()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    try:
        work_fn = make_work_node("p5")
        state = {"project_id": pid, "run_id": rid, "execution_mode": "auto", "plan_approved": True}
        await work_fn(state)
    finally:
        nodes.clear_handlers()
        nodes.set_gate_backend(None)

    assert len(gb.created) == 1
    created = gb.created[0]
    assert not created["metadata"].get("rework_target_stage")
    assert read_p5_rework_marker(pid, created["gate_id"]) is None

    from app.core.database import get_session
    from app.models.plan_delta import PlanDelta
    db = get_session()
    try:
        deltas = db.query(PlanDelta).filter(PlanDelta.project_id == pid).all()
    finally:
        db.close()
    assert deltas == [], "正常通过路径不应创建任何 PlanDelta"


# ══════════════════════════════════════════════════════════════════════════
# ⑥ gate_backend.RealGateBackend.create()：第三种诚实文案分支
# ══════════════════════════════════════════════════════════════════════════

class TestGateBackendReworkText:
    def test_rework_metadata_produces_honest_rework_text(self, isolated_data):
        from app.graph.gate_backend import RealGateBackend
        pid = "gbtext-1"
        workspace_service.init_workspace(pid)
        backend = RealGateBackend()
        gate_id = backend.create(
            project_id=pid, run_id="run-1", stage="p5",
            artifact_refs=[],
            gate_type="stage_promotion",
            metadata={"rework_target_stage": "p4", "rework_reason": "构建失败：NU1605"},
        )
        from app.dependencies import get_services
        gate = get_services().gate_service.get(gate_id)
        assert "返工" in gate.reason
        assert "P4" in gate.reason
        assert "构建失败" in gate.reason
        # 不得掉进误导性罐头话或"正常晋级"文案
        assert "已完成并产出三类审核报告" not in gate.reason
        assert "已完成并通过独立验收" not in gate.reason

    def test_normal_stage_promotion_with_brief_unaffected(self, isolated_data):
        """防回归：不带 rework metadata、且 artifact_refs 里有真实 brief 时，
        仍走"读 brief 合成诚实文案"这条既有分支（A 部分：不因新增分支而破坏它）。"""
        import json
        from app.graph.gate_backend import RealGateBackend
        pid = "gbtext-2"
        workspace_service.init_workspace(pid)
        brief_rel = "artifacts/p5/p5_gate_brief.json"
        brief_path = workspace_service.workspace_path(pid) / brief_rel
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(json.dumps({
            "what_happened": "P5 阶段已完成并通过验证",
            "validation_verdict": {"verdict": "accepted", "issues_count": 0},
            "honest_notes": "",
        }, ensure_ascii=False), encoding="utf-8")

        backend = RealGateBackend()
        gate_id = backend.create(
            project_id=pid, run_id="run-1", stage="p5",
            artifact_refs=[brief_rel], gate_type="stage_promotion", metadata=None,
        )
        from app.dependencies import get_services
        gate = get_services().gate_service.get(gate_id)
        assert "已完成并通过独立验收" in gate.reason
        assert "返工" not in gate.reason

    def test_normal_stage_promotion_without_brief_falls_back_to_canned_text(self, isolated_data):
        """防回归：没有 rework metadata、也没有真实 brief 时，仍落入既有兜底文案
        （该文案本身留存，只是新增了一个更早判断的返工分支，不改变它的存在）。"""
        from app.graph.gate_backend import RealGateBackend
        pid = "gbtext-3"
        workspace_service.init_workspace(pid)
        backend = RealGateBackend()
        gate_id = backend.create(
            project_id=pid, run_id="run-1", stage="p2",
            artifact_refs=[], gate_type="stage_promotion", metadata=None,
        )
        from app.dependencies import get_services
        gate = get_services().gate_service.get(gate_id)
        assert "小循环通过，请求阶段晋级" in gate.reason

    def test_replay_of_real_gate_401a97_scenario_no_longer_misleading(self, isolated_data):
        """A 部分：重放 gate-401a97 的真实场景——P5 判定 rework_required，Gate 创建时
        artifact_refs 只有三份编排报告（start_plan/construction/acceptance，不含
        gate_brief），验证：① 修复后这种输入不会被 nodes.py 产出（见 ⑤ 组测试）；
        ② 即便 gate_backend 单独收到这种"没有 brief、但带了返工 metadata"的输入，
        也不会退化回误导性罐头话——metadata 判断在前，比 brief 缺失更优先。"""
        from app.graph.gate_backend import RealGateBackend
        pid = "gbtext-401a97"
        workspace_service.init_workspace(pid)
        backend = RealGateBackend()
        gate_id = backend.create(
            project_id=pid, run_id="run-eab45b", stage="p5",
            artifact_refs=["artifacts/p5/p5_start_plan.json",
                          "artifacts/p5/p5_construction.json",
                          "artifacts/p5/p5_acceptance.json"],
            gate_type="stage_promotion",
            metadata={"rework_target_stage": "p4", "rework_reason": "P5 全量验证未通过"},
        )
        from app.dependencies import get_services
        gate = get_services().gate_service.get(gate_id)
        assert "返工" in gate.reason
        assert "已完成并产出三类审核报告" not in gate.reason
        assert "已完成并通过独立验收" not in gate.reason


# ══════════════════════════════════════════════════════════════════════════
# ⑦ gate_service._apply_promotion：直连晋级路径 + 大量防回归 + 一致性
# ══════════════════════════════════════════════════════════════════════════

class TestApplyPromotionRework:
    def test_p5_approve_with_marker_routes_db_state_to_p4(self, client, project_run):
        pid, rid = project_run
        _ensure_task_graph(rid, "p5")
        g = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-gate",
                        json={"target_stage": "P6"}).json()["data"]
        write_p5_rework_marker(pid, gate_id=g["gate_id"], run_id=rid,
                              target_stage="p4", reason="构建失败测试")
        r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-decision",
                        json={"decision": "approve", "reason": "approve rework"})
        assert r.status_code == 200, r.text

        proj = client.get(f"/api/projects/{pid}").json()["data"]
        assert proj["current_stage"] == "p4", proj
        run = client.get(f"/api/projects/{pid}/runs/{rid}").json()["data"]
        assert run["stage_status"].get("p4") == "in_progress"
        assert run["stage_status"].get("p5") == "rework_required"

    def test_p5_approve_without_marker_promotes_to_p6_normally(self, client, project_run):
        pid, rid = project_run
        _ensure_task_graph(rid, "p5")
        client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-gate",
                   json={"target_stage": "P6"})
        r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-decision",
                        json={"decision": "approve", "reason": "normal"})
        assert r.status_code == 200, r.text

        proj = client.get(f"/api/projects/{pid}").json()["data"]
        assert proj["current_stage"] == "p6", proj
        run = client.get(f"/api/projects/{pid}/runs/{rid}").json()["data"]
        assert run["stage_status"].get("p5") == "completed"
        assert run["stage_status"].get("p6") == "in_progress"

    @pytest.mark.parametrize("stage,nxt", [("p0", "p1"), ("p2", "p3"), ("p4", "p5")])
    def test_other_stages_unaffected_even_with_stray_p5_marker(self, client, project_run, stage, nxt):
        """防回归：p5 专属的返工检查完全不影响其它阶段——即便同一 project 下恰好存在
        一份不相关的 p5 返工标记文件（gate_id 不匹配），非 p5 阶段的晋级判断完全不读它。
        三个阶段跨首/中/末位置，证明这是纯加法、没碰到通用晋级路径。"""
        pid, rid = project_run
        _ensure_task_graph(rid, stage)
        write_p5_rework_marker(pid, gate_id="unrelated-gate-id", run_id=rid,
                              target_stage="p4", reason="不应影响本阶段的晋级判断")
        client.post(f"/api/projects/{pid}/runs/{rid}/stages/{stage}/promotion-gate",
                   json={"target_stage": nxt.upper()})
        r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/{stage}/promotion-decision",
                        json={"decision": "approve", "reason": "approve"})
        assert r.status_code == 200, r.text

        proj = client.get(f"/api/projects/{pid}").json()["data"]
        assert proj["current_stage"] == nxt
        run = client.get(f"/api/projects/{pid}/runs/{rid}").json()["data"]
        assert run["stage_status"].get(stage) == "completed"
        assert run["stage_status"].get(nxt) == "in_progress"

    def test_reject_and_request_changes_unaffected(self, client, project_run):
        pid, rid = project_run
        _ensure_task_graph(rid, "p5")
        client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-gate",
                   json={"target_stage": "P6"})
        r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-decision",
                        json={"decision": "reject", "reason": "拒绝"})
        assert r.json()["data"]["gate_status"] == "rejected"

        rid2 = client.post(f"/api/projects/{pid}/runs", json={"run_goal": "rw8b", "mode": "plan"}).json()["data"]["run_id"]
        _ensure_task_graph(rid2, "p5")
        client.post(f"/api/projects/{pid}/runs/{rid2}/stages/p5/promotion-gate",
                   json={"target_stage": "P6"})
        r2 = client.post(f"/api/projects/{pid}/runs/{rid2}/stages/p5/promotion-decision",
                        json={"decision": "request_changes", "reason": "补充说明"})
        assert r2.json()["data"]["gate_status"] == "changes_requested"


class TestRouterAndApplyPromotionConsistency:
    """本任务最重要的一条断言：图路由（make_router）与持久化状态（_apply_promotion）
    在同一场景下必须给出一致结论——都读同一份落盘标记，不能一个说 p4、另一个说 p6。"""

    def test_same_marker_yields_same_target_on_both_sides(self, client, project_run):
        pid, rid = project_run
        _ensure_task_graph(rid, "p5")
        g = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-gate",
                        json={"target_stage": "P6"}).json()["data"]
        write_p5_rework_marker(pid, gate_id=g["gate_id"], run_id=rid,
                              target_stage="p4", reason="一致性测试：构建失败")

        # 图路由侧：{p5}_gate 节点会调 read_p5_rework_marker 拿到这份标记，写进
        # rework_target_stage 供 make_router 消费——这里直接复用同一个真实读取结果。
        marker = read_p5_rework_marker(pid, g["gate_id"])
        assert marker is not None
        router_state = {"last_decision": "approve",
                        "rework_target_stage": marker["rework_target_stage"]}
        router_decision = make_router("p5")(router_state)

        # 持久化侧：真实走 REST 直连路径（drive_promotion=True → _apply_promotion）
        r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-decision",
                        json={"decision": "approve", "reason": "consistency"})
        assert r.status_code == 200, r.text
        proj = client.get(f"/api/projects/{pid}").json()["data"]

        # 核心断言：两条独立代码路径必须给出一致结论
        assert router_decision == f"{proj['current_stage']}_work", (
            f"图路由说 {router_decision!r}，持久化状态说 current_stage="
            f"{proj['current_stage']!r}——二者矛盾，状态会撕裂"
        )
        assert proj["current_stage"] == "p4"

    def test_same_absence_of_marker_yields_same_normal_promotion_on_both_sides(self, client, project_run):
        """反向对照：没有标记时，两条路径也必须一致地给出"正常晋级"结论。"""
        pid, rid = project_run
        _ensure_task_graph(rid, "p5")
        g = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-gate",
                        json={"target_stage": "P6"}).json()["data"]

        marker = read_p5_rework_marker(pid, g["gate_id"])
        assert marker is None
        router_state = {"last_decision": "approve",
                        "rework_target_stage": marker["rework_target_stage"] if marker else None}
        router_decision = make_router("p5")(router_state)

        r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p5/promotion-decision",
                        json={"decision": "approve", "reason": "consistency-normal"})
        assert r.status_code == 200, r.text
        proj = client.get(f"/api/projects/{pid}").json()["data"]

        assert router_decision == f"{proj['current_stage']}_work"
        assert proj["current_stage"] == "p6"
