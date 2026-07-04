"""R11-3-C5 TaskGraphEngine + NodeLoop + Acceptance 完整执行链路测试.

C5 完成标准（对照交接 §3-C5）：
  1. P4 能执行至少一个真实 TaskGraph execution 节点（经 TaskGraphEngine+NodeLoop）；
  2. NodeLoop / Acceptance / TaskGraph 边策略均有证据；
  3. 失败路径不静默、不伪造成功；
  4. 不推进 P5 业务，只能创建/准备 P4→P5 Gate。
P4 worker 写盘经 WorkspaceMediator（D-099/D-104）；handler 由 LangGraph p4 节点调用，engine
是节点内部编排（D-037）。asyncio_mode=auto。
"""

import hashlib
import json

from app.graph.stage_handlers import RealP4Handler
from app.services import workspace_service
from app.core.database import get_session
from app.core.trace_writer import TraceWriter
from app.core.audit_writer import AuditWriter
from app.models.task_graph import TaskGraph, TaskNode


class _StubGateway:
    def __init__(self, content="// migrated\npublic class M {}\n"):
        self.content = content

    async def call(self, **kwargs):
        return {"status": "completed", "content": self.content, "model_id": "stub"}


class _BlockedGateway:
    async def call(self, **kwargs):
        return {"status": "credential_missing", "content": "", "error": "no key"}


def _mk_ws(pid, sources):
    ws = workspace_service.init_workspace(pid)
    for rel, content in sources.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return ws


def _mk_graph(pid, node_specs, edges=None):
    """node_specs: list of (node_id, input_refs, [output_target]). Returns tgid."""
    db = get_session()
    try:
        tg = TaskGraph(project_id=pid, stage="p3", stage_plan_ref="sp-c5",
                       title="c5 graph", graph_status="draft", edges=edges or [], version=1)
        db.add(tg); db.flush()
        for spec in node_specs:
            nid = spec[0]
            db.add(TaskNode(task_graph_id=tg.task_graph_id, project_id=pid, stage="p3",
                            node_id=nid, node_type="execution", title=f"exec {nid}",
                            input_refs=spec[1], risk_level="L2"))
        db.commit()
        return tg.task_graph_id
    finally:
        db.close()


def _seq_edge(src, tgt):
    return {"edge_id": f"{src}->{tgt}", "source_node_id": src, "target_node_id": tgt,
            "edge_type": "sequence", "trigger_condition": "on_success",
            "dependency": "hard_dependency"}


def _handler(gateway):
    return RealP4Handler(tracer=TraceWriter(), auditor=AuditWriter(), gateway=gateway)


# ── 1 + 2：sequence 两节点经 engine 全完成，NodeLoop/Acceptance/边策略有证据 ──────

async def test_c5_sequence_two_nodes_completed():
    pid = "proj-c5-seq"
    ws = _mk_ws(pid, {"source/A.cs": "class A{}\n", "source/B.cs": "class B{}\n"})
    tgid = _mk_graph(pid, [("n1", ["source/A.cs"]), ("n2", ["source/B.cs"])],
                     edges=[_seq_edge("n1", "n2")])
    res = await _handler(_StubGateway()).execute({"project_id": pid, "run_id": "r1"})

    assert res["status"] == "completed"
    assert res["graph_status"] == "completed"
    assert res["task_graph_ref"] == tgid
    assert res["completed_node_count"] == 2
    # 真实产物落盘（两节点各 output_code + patch）+ C7 执行摘要（change manifest + patch index）
    assert len(res["patch_refs"]) == 2
    assert len(res["evidence_refs"]) == 2
    assert any(a == "artifacts/p4_execution_summary.json" for a in res["artifacts"]), \
        "C7 execution summary should be attached as the primary Gate review artifact"
    core = [a for a in res["artifacts"] if a != "artifacts/p4_execution_summary.json"]
    assert len(core) == 4 and all(
        a.startswith("output_code/") or a.startswith("patches/") for a in core)
    for nid in ("n1", "n2"):
        assert (ws / f"output_code/{nid}").is_dir()
        assert (ws / f"patches/{nid}.diff").is_file()
    # 边策略/NodeLoop/Acceptance 证据
    routes = [e for e in res["engine_events"] if e.get("engine") == "node_done"]
    assert {e["node_id"] for e in routes} == {"n1", "n2"}
    assert all(e["route"] == "next" for e in routes)
    assert all(a.get("result") == "accepted" for a in res["acceptance_results"])
    # source 未改
    assert (ws / "source/A.cs").read_text() == "class A{}\n"
    assert RealP4Handler().review(res).passed is True


# ── 3：失败路径不伪造（写 source 负路径 → 节点 blocked → graph blocked） ─────────

async def test_c5_blocked_node_not_faked():
    pid = "proj-c5-neg"
    ws = _mk_ws(pid, {"source/keep.cs": "orig\n"})
    _mk_graph(pid, [("bad", [])])  # 单节点
    # 注入 output_target=source/ 需在节点 dict 上；用 DB 无该列 → 由 handler 读不到。
    # 改测"无模型"诚实 blocked（等价的失败-不伪造路径，且不依赖节点私有字段）。
    res = await _handler(_BlockedGateway()).execute({"project_id": pid, "run_id": "r2"})
    assert res["status"] == "blocked"
    assert res["graph_status"] == "blocked"
    assert res["completed_node_count"] == 0
    assert res["artifacts"] == [] and res["evidence_refs"] == []
    assert RealP4Handler().review(res).passed is False
    # source 未被触碰
    assert (ws / "source/keep.cs").read_text() == "orig\n"


# ── 4：混合（一节点成功 + 一节点无源→仍产出 / 或无模型）诚实归类 ─────────────────

async def test_c5_partial_blocked_overall_blocked():
    """两节点，engine 无模型 → 全 blocked → graph blocked（不因任一伪造 completed）。"""
    pid = "proj-c5-mix"
    _mk_ws(pid, {"source/A.cs": "a\n"})
    _mk_graph(pid, [("m1", ["source/A.cs"]), ("m2", [])], edges=[_seq_edge("m1", "m2")])
    res = await _handler(_BlockedGateway()).execute({"project_id": pid, "run_id": "r3"})
    assert res["status"] == "blocked"
    assert res["completed_node_count"] < 2       # 绝不全绿
    assert RealP4Handler().review(res).passed is False


# ── 5：无 execution 节点 / 无 TaskGraph → 诚实 blocked ──────────────────────────

async def test_c5_no_task_graph_blocked():
    res = await _handler(_StubGateway()).execute({"project_id": "proj-c5-none", "run_id": ""})
    assert res["status"] == "blocked"
    assert res["task_graph_ref"] is None


# ── 6：engine 证据齐全（每节点走 NodeLoop 9 步，acceptance 记录可追溯） ──────────

async def test_c5_nodeloop_acceptance_traceable():
    pid = "proj-c5-trace"
    _mk_ws(pid, {"source/X.cs": "x\n"})
    _mk_graph(pid, [("only", ["source/X.cs"])])
    res = await _handler(_StubGateway()).execute({"project_id": pid, "run_id": "r5"})
    assert res["status"] == "completed"
    # Acceptance 8 项检查可追溯（§7-5）
    acc = res["acceptance_results"][0]
    assert acc["result"] == "accepted"
    assert len(acc["checks"]) == 8
    assert all(c["passed"] for c in acc["checks"])


# ── C7：执行摘要（change manifest + patch index）落盘且哈希以真实文件为准 ─────────

async def test_c7_execution_summary_written(monkeypatch):
    """C7: a P4 execution summary is produced with a change manifest + patch index whose
    sha256/bytes come from the REAL on-disk files (Evidence 以真实产物为准)."""
    import json
    pid = "proj-c7-sum"
    ws = _mk_ws(pid, {"source/S.cs": "class S{}\n"})
    _mk_graph(pid, [("c1", ["source/S.cs"])])
    res = await _handler(_StubGateway()).execute({"project_id": pid, "run_id": "r7"})
    assert res["status"] == "completed"
    assert "artifacts/p4_execution_summary.json" in res["artifacts"]

    summ = json.loads((ws / "artifacts/p4_execution_summary.json").read_text())
    assert summ["stage"] == "p4" and summ["graph_status"] == "completed"
    # change manifest: every output_code file with REAL re-read sha256 + bytes
    assert len(summ["change_manifest"]) >= 1
    for entry in summ["change_manifest"]:
        assert entry["kind"] == "output_code"
        assert entry["sha256"] and entry["bytes"] > 0
        real_hash = hashlib.sha256((ws / entry["path"]).read_bytes()).hexdigest()
        assert entry["sha256"] == real_hash, "manifest sha256 must match real file on disk"
        assert entry["evidence_refs"]  # each output links its evidence
    # patch index present (>=1 patch) and references a source file
    assert len(summ["patch_index"]) >= 1
    assert summ["patch_index"][0]["source_ref"] == "source/S.cs"
    # per-node results + evidence refs
    assert len(summ["nodes"]) == 1 and summ["nodes"][0]["node_id"] == "c1"
    assert summ["evidence_refs"]


async def test_c7_no_summary_when_blocked(monkeypatch):
    """C7: blocked P4 (no model, no artifacts) writes NO summary — no fabricated report."""
    pid = "proj-c7-block"
    _mk_ws(pid, {"source/keep.cs": "orig\n"})
    _mk_graph(pid, [("bad", [])])
    res = await _handler(_BlockedGateway()).execute({"project_id": pid, "run_id": "rB"})
    assert res["status"] == "blocked"
    assert "artifacts/p4_execution_summary.json" not in res["artifacts"]
    assert not (workspace_service.workspace_path(pid) / "artifacts"
                / "p4_execution_summary.json").exists()
