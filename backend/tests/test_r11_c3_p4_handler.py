"""R11-3 RealP4Handler 加载 / 诚实状态测试（C3 骨架不变量，C4 起接入真实 worker）.

C3 建立、C4 保持的不变量：P4 handler 已注册（不再 future stub），能加载 P3 TaskGraph，
缺 TaskGraph / 无 execution 节点 / 无可用模型时诚实 blocked，**绝不伪造 completed**
（D-097 / V10 FIND-V9-001 教训）。handler 由 LangGraph make_work_node 调用，不绕主编排
（D-037）。C4 真实写入闭环（output_code/patches/Evidence/Acceptance/负路径）见
test_r11_c4_p4_worker.py。asyncio_mode=auto，async 测试直接 await。
"""

from app.graph.stage_handlers import RealP4Handler
from app.core.database import get_session
from app.models.task_graph import TaskGraph, TaskNode


class _BlockedGateway:
    """Stub ModelGateway：恒返回未配置模型 → worker 节点 blocked（无网络、确定性）。"""
    async def call(self, **kwargs):
        return {"status": "not_configured", "content": "", "error": "no model (test)"}


def _mk_p3_task_graph(project_id: str, node_types: list[str]) -> str:
    """在测试 DB 建一个 P3 TaskGraph + 指定 node_type 的节点，返回 task_graph_id。"""
    db = get_session()
    try:
        tg = TaskGraph(project_id=project_id, stage="p3", stage_plan_ref="sp-c3-test",
                       title="C3 test graph", graph_status="draft", edges=[], version=1)
        db.add(tg)
        db.flush()
        for i, nt in enumerate(node_types):
            db.add(TaskNode(task_graph_id=tg.task_graph_id, project_id=project_id,
                            stage="p3", node_type=nt, title=f"node-{i}", risk_level="L2"))
        db.commit()
        return tg.task_graph_id
    finally:
        db.close()


# ── 加载与诚实状态 ────────────────────────────────────────────────────────

async def test_p4_blocked_when_no_task_graph():
    """无 P3 TaskGraph → blocked（诚实缺前置输入，不伪造完成）。"""
    h = RealP4Handler()
    res = await h.execute({"project_id": "proj-c3-none", "run_id": ""})
    assert res["status"] == "blocked"
    assert res["task_graph_ref"] is None
    assert res["artifacts"] == [] and res["evidence_refs"] == []
    assert h.review(res).passed is False


async def test_p4_blocked_when_no_model_available():
    """有 execution 节点但无可用模型 → 节点 blocked → 整体 blocked，绝不 completed（C4 诚实）。"""
    pid = "proj-c3-exec"
    tgid = _mk_p3_task_graph(pid, ["execution", "execution", "execution"])
    h = RealP4Handler(gateway=_BlockedGateway())
    res = await h.execute({"project_id": pid, "run_id": ""})
    assert res["status"] == "blocked"                       # 诚实：非 completed
    assert res["task_graph_ref"] == tgid
    assert res["node_count"] == 3
    assert res["execution_node_count"] == 3
    assert res["completed_node_count"] == 0                  # 无模型 → 0 节点完成
    assert res["node_type_distribution"] == {"execution": 3}
    # 无模型 → 不写 output_code / 不伪造 evidence
    assert res["artifacts"] == [] and res["evidence_refs"] == []
    assert h.review(res).passed is False


async def test_p4_no_execution_node_routes_to_review():
    """D-112 方案B：TaskGraph 只有非 execution 节点 → 不再 blocked，而是收集为待用户评审项
    （completed + pending_review），随 P4→P5 Gate 交用户裁决。绝不假通过（不进引擎默认执行器）。"""
    pid = "proj-c3-noexec"
    _mk_p3_task_graph(pid, ["decision", "verification"])
    h = RealP4Handler()
    res = await h.execute({"project_id": pid, "run_id": "r-noexec"})
    assert res["status"] == "completed"                       # 非 blocked：评审项交用户
    assert res["execution_node_count"] == 0
    assert res["review_node_count"] == 2
    assert res["pending_review_ref"]                          # 评审材料已落盘
    assert res["completed_nodes"] == [] and res["failed_nodes"] == []
    assert res["artifacts"] == [res["pending_review_ref"]]    # 无伪造代码产物
    assert "待用户评审" in res["reason"]


async def test_p4_non_execution_nodes_never_fake_pass(monkeypatch):
    """D-112：混合图（execution + 非 execution）——execution 走 worker（无模型→blocked），
    非 execution 节点收集为评审项且绝不假通过（引擎默认执行器对其不可达）。"""
    pid = "proj-c3-mixed"
    _mk_p3_task_graph(pid, ["execution", "decision", "poc"])
    h = RealP4Handler(gateway=_BlockedGateway())
    res = await h.execute({"project_id": pid, "run_id": "r-mixed"})
    assert res["execution_node_count"] == 1
    assert res["review_node_count"] == 2
    assert res["pending_review_ref"]
    # 非 execution 节点不在引擎完成集里（未被 _default_executor 假通过）
    assert res["completed_node_count"] == 0                    # 无模型 → execution 也未完成
    assert res["node_type_distribution"] == {"execution": 1, "decision": 1, "poc": 1}


async def test_p4_loads_latest_graph_and_counts_execution_only():
    """混合 node_type 时只计 execution 节点数；分布如实反映。"""
    pid = "proj-c3-mixed"
    _mk_p3_task_graph(pid, ["execution", "planning", "execution"])
    h = RealP4Handler(gateway=_BlockedGateway())
    res = await h.execute({"project_id": pid, "run_id": ""})
    assert res["status"] == "blocked"
    assert res["execution_node_count"] == 2
    assert res["node_type_distribution"] == {"execution": 2, "planning": 1}


# ── review 契约：非 completed 绝不放行；completed 须有真实产物 + Evidence ─────

def test_p4_review_never_passes_on_non_completed():
    h = RealP4Handler()
    for st in ("blocked", "waiting_input", "failed"):
        assert h.review({"status": st, "reason": "x"}).passed is False


def test_p4_review_completed_requires_artifacts_and_evidence():
    """completed 但无产物 / 无 Evidence → review 不放行（不伪造完成，D-066）。"""
    h = RealP4Handler()
    assert h.review({"status": "completed"}).passed is False
    assert h.review({"status": "completed", "artifacts": ["output_code/x"]}).passed is False
    assert h.review({"status": "completed", "artifacts": ["output_code/x"],
                     "evidence_refs": ["ev-p4-x"]}).passed is True


# ── 注册（不绕 LangGraph；由 make_work_node 调用） ──────────────────────────

def test_p4_registered_after_bootstrap():
    from app.graph import nodes
    h = nodes.get_handler("p4")
    assert h is not None and h.__class__.__name__ == "RealP4Handler"
    h5 = nodes.get_handler("p5")
    assert h5 is not None and h5.__class__.__name__ == "RealP5Handler"  # R12-3-C3: registered skeleton
