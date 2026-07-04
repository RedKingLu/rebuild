"""R11-3-C4 P4 平台内部 execution worker 写入闭环测试.

C4 完成标准（对照交接 §3-C4）：
  1. 至少一个 execution 节点生成 output_code 产物与 patch/diff；
  2. source/ 未被修改；
  3. Evidence 以真实落盘文件为准，不依赖 LLM 自报；
  4. Acceptance 能检查节点产物；
  5. 写 source 的负路径被拒绝并记录 Audit。
所有写盘经 WorkspaceMediator（D-099/D-104）。asyncio_mode=auto。
"""

import hashlib

from app.services.p4_execution_worker import P4ExecutionWorker
from app.services.aet_service import AETService
from app.services import workspace_service
from app.core.trace_writer import TraceWriter
from app.core.audit_writer import AuditWriter


class _StubGateway:
    """确定性 ModelGateway：返回 completed + 固定内容（不依赖 Key / 网络）。"""
    def __init__(self, content="// migrated to target stack\npublic class Migrated {}\n"):
        self.content = content
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "completed", "content": self.content, "model_id": "stub-model"}


class _BlockedGateway:
    async def call(self, **kwargs):
        return {"status": "credential_missing", "content": "", "error": "no key"}


def _mk_ws(pid: str, sources: dict[str, str]):
    ws = workspace_service.init_workspace(pid)
    for rel, content in sources.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return ws


def _worker(pid, gateway, auditor=None, tracer=None):
    return P4ExecutionWorker(pid, tracer=tracer, auditor=auditor,
                             aet=AETService(None), gateway=gateway)


# ── 1. 写入闭环：output_code + patch，且 source 未改 ────────────────────────

async def test_execute_node_writes_output_code_and_patch():
    pid = "proj-c4-1"
    ws = _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})
    stub = _StubGateway()
    node = {"node_id": "n1", "node_type": "execution", "title": "迁移 Legacy.cs",
            "risk_level": "L2", "input_refs": ["source/Legacy.cs"]}
    pkg = await _worker(pid, stub).execute_node(node, run_id="r1")

    assert pkg["node_status"] == "completed"
    assert pkg["criteria_met"] is True
    out_ref = pkg["output_code_refs"][0]
    patch_ref = pkg["patch_refs"][0]
    assert out_ref.startswith("output_code/")
    assert patch_ref == "patches/n1.diff"
    # 真实产物落盘
    out_file = ws / out_ref
    assert out_file.is_file() and out_file.read_text() == stub.content
    assert (ws / patch_ref).is_file()
    # source 未被修改（D-099①）
    assert (ws / "source/Legacy.cs").read_text() == "public class Legacy { }\n"
    # 模型被真实调用一次
    assert len(stub.calls) == 1


# ── 3. Evidence 以真实文件为准，不依赖 LLM 自报 ─────────────────────────────

async def test_evidence_derived_from_real_file():
    pid = "proj-c4-2"
    ws = _mk_ws(pid, {"source/a.cs": "int x=1;\n"})
    stub = _StubGateway(content="int x = 1; // migrated\n")
    node = {"node_id": "n2", "node_type": "execution", "title": "t",
            "input_refs": ["source/a.cs"]}
    aet = AETService(None)
    worker = P4ExecutionWorker(pid, aet=aet, gateway=stub)
    pkg = await worker.execute_node(node, run_id="r2")

    assert pkg["evidence_refs"] == ["ev-p4-n2"]
    ev = aet.get_evidence("ev-p4-n2", project_id=pid)
    # sha256 来自真实落盘文件（重算比对），非模型自报
    real_sha = hashlib.sha256((ws / pkg["output_code_refs"][0]).read_bytes()).hexdigest()
    assert ev["output_sha256"] == real_sha
    assert ev["evidence_basis"] == "real_file_on_disk"
    assert ev["output_bytes"] == len(stub.content.encode())


# ── 5. 负路径：写 source/ 被 Mediator 拒绝并记 Audit ────────────────────────

async def test_write_source_rejected_and_audited():
    pid = "proj-c4-3"
    ws = _mk_ws(pid, {"source/keep.cs": "original\n"})
    auditor = AuditWriter()
    stub = _StubGateway()
    # 误规划：节点要求写回 source/ → 必须被拒
    node = {"node_id": "n3", "node_type": "execution", "title": "bad",
            "output_target": "source/keep.cs"}
    pkg = await _worker(pid, stub, auditor=auditor).execute_node(node, run_id="r3")

    assert pkg["node_status"] == "blocked"
    assert pkg["scope_violation"] is True
    assert "D-099" in pkg["reason"]
    # source 未被改
    assert (ws / "source/keep.cs").read_text() == "original\n"
    # Audit 记录了 rejected
    rejected = [a for a in auditor.list_all() if a.get("decision") == "rejected"]
    assert any(a.get("node_id") == "n3" and a.get("risk_level") == "L4" for a in rejected)


async def test_no_model_yields_blocked_no_write():
    pid = "proj-c4-4"
    ws = _mk_ws(pid, {"source/x.cs": "x\n"})
    node = {"node_id": "n4", "node_type": "execution", "title": "t",
            "input_refs": ["source/x.cs"]}
    pkg = await _worker(pid, _BlockedGateway()).execute_node(node, run_id="r4")
    assert pkg["node_status"] == "blocked"
    assert pkg["artifacts"] == []
    # 无产物写入
    assert not (ws / "output_code/n4").exists()


# ── run(): 全部完成才 completed；混合则诚实 blocked ─────────────────────────

async def test_run_all_completed():
    pid = "proj-c4-5"
    _mk_ws(pid, {"source/a.cs": "a\n", "source/b.cs": "b\n"})
    nodes = [
        {"node_id": "m1", "node_type": "execution", "title": "A", "input_refs": ["source/a.cs"]},
        {"node_id": "m2", "node_type": "execution", "title": "B", "input_refs": ["source/b.cs"]},
    ]
    res = await _worker(pid, _StubGateway()).run(nodes, run_id="r5")
    assert res["status"] == "completed"
    assert res["completed_node_count"] == 2 and res["blocked_node_count"] == 0
    assert len(res["artifacts"]) == 4          # 2 nodes × (output + patch)
    assert len(res["patch_refs"]) == 2


async def test_run_mixed_is_blocked_not_fake_completed():
    """一个正常 + 一个写 source 被拒 → 整体 blocked（绝不因部分成功伪造 completed）。"""
    pid = "proj-c4-6"
    _mk_ws(pid, {"source/a.cs": "a\n"})
    nodes = [
        {"node_id": "ok", "node_type": "execution", "title": "A", "input_refs": ["source/a.cs"]},
        {"node_id": "bad", "node_type": "execution", "title": "B", "output_target": "source/a.cs"},
    ]
    res = await _worker(pid, _StubGateway()).run(nodes, run_id="r6")
    assert res["status"] == "blocked"
    assert res["completed_node_count"] == 1 and res["blocked_node_count"] == 1


# ── 4 + handler 集成：completed 且过 Acceptance ─────────────────────────────

async def test_handler_completed_with_acceptance():
    from app.graph.stage_handlers import RealP4Handler
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph, TaskNode

    pid = "proj-c4-h"
    ws = _mk_ws(pid, {"source/Order.cs": "public class Order { }\n"})
    db = get_session()
    try:
        tg = TaskGraph(project_id=pid, stage="p3", stage_plan_ref="sp-c4",
                       title="c4 graph", graph_status="draft", edges=[], version=1)
        db.add(tg); db.flush()
        db.add(TaskNode(task_graph_id=tg.task_graph_id, project_id=pid, stage="p3",
                        node_type="execution", title="迁移 Order",
                        input_refs=["source/Order.cs"], risk_level="L2"))
        db.commit()
        tgid = tg.task_graph_id
    finally:
        db.close()

    h = RealP4Handler(tracer=TraceWriter(), auditor=AuditWriter(), gateway=_StubGateway())
    res = await h.execute({"project_id": pid, "run_id": "rh"})

    assert res["status"] == "completed"
    assert res["task_graph_ref"] == tgid
    assert res["completed_node_count"] == 1
    assert res["artifacts"] and res["evidence_refs"]
    # Acceptance 对节点产物出结论且通过
    assert res["acceptance_results"]
    assert all(a["result"] in ("accepted", "accepted_with_warning")
               for a in res["acceptance_results"])
    assert h.review(res).passed is True
    # 真实产物落盘、source 未改
    assert (ws / res["patch_refs"][0]).is_file()
    assert (ws / "source/Order.cs").read_text() == "public class Order { }\n"
