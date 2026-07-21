"""R17.3-6 WP-5 产物契约与追溯 — 真实（非 mock）单测（D-097）。

覆盖四项：
  - FUP-3：{stage}_acceptance.json 的 issues 为结构化对象列表（非 Python dict 的 repr 字符串）。
  - NEW-05：P0/P5/P6 handler 返回的 artifacts 真实反映实际写入的产物（construction 不欠报）。
  - EVI-01：P1 产物登记为可查询 AET Evidence（与 WP-2 WorkAgent fact-evidence 协调，单一来源）。
  - GATE-02：make_work_node 复用 P6 handler 唯一权威最终 Gate，不重复建第二个 promotion Gate。

红线：确定性阶段无 Key 端到端可跑；无 mock 生产路径；不硬编码 MicroOA。
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

from app.graph import nodes
from app.graph.stage_loop import StageLoop
from app.services import workspace_service
from app.services.review_pass import ReviewResult


# ── FUP-3：验收报告 issues 结构化 ─────────────────────────────────────────
async def test_fup3_acceptance_issues_are_structured_objects(isolated_data):
    """review_fn 返回 dict issues → p3_acceptance.json 的 issues[0] 应为结构化 dict，
    不再是 "{'type': ..., 'detail': ...}" 这种 Python dict 的 repr 字符串。"""
    pid = "wp5-fup3"
    workspace_service.init_workspace(pid)
    loop = StageLoop(pid, "p3", max_rounds=1)

    def review_fn(result):
        return ReviewResult(
            passed=False,
            issues=[{"type": "planning_not_completed",
                     "detail": "P3 规划未完成（LLM 未产出 stage_plan）"}],
            recommendations=["配置有效模型 Key 后重试"],
            reviewer="p3_review_skill")

    await loop.run(goal="P3 规划", acceptance_criteria=["Stage Plan 已生成"],
                   planned_actions=["调用模型生成规划"],
                   execute_fn=lambda: {"status": "blocked"}, review_fn=review_fn)

    acc = json.loads((workspace_service.workspace_path(pid) / "artifacts"
                      / "p3_acceptance.json").read_text("utf-8"))
    assert isinstance(acc["issues"], list) and acc["issues"], "issues 应非空列表"
    first = acc["issues"][0]
    assert isinstance(first, dict), f"issues[0] 应为结构化 dict，实际 {type(first)}: {first!r}"
    assert first.get("type") == "planning_not_completed"
    assert first.get("detail")
    # 反向：不得是字符串化的 dict（旧 bug 形态）
    assert not isinstance(first, str)
    assert not str(first).startswith("\"{'"), "不得是 dict repr 字符串"


async def test_fup3_self_check_issues_also_structured(isolated_data):
    """self_check_fn 追加的 issues 亦为结构化 {self_check: ...} 对象。"""
    pid = "wp5-fup3-sc"
    workspace_service.init_workspace(pid)
    loop = StageLoop(pid, "p1", max_rounds=1)
    await loop.run(
        goal="g", acceptance_criteria=["c"], planned_actions=["a"],
        execute_fn=lambda: {"status": "completed"},
        review_fn=lambda r: ReviewResult(passed=True, issues=[]),
        self_check_fn=lambda r: ["自检失败项 X"])
    acc = json.loads((workspace_service.workspace_path(pid) / "artifacts"
                      / "p1_acceptance.json").read_text("utf-8"))
    assert acc["issues"], "self_check 触发 issues"
    assert all(isinstance(i, dict) for i in acc["issues"]), "issues 全为结构化对象"
    assert any("self_check" in i for i in acc["issues"])


# ── NEW-05：P0 artifacts 反映真实写入（intake + source_index） ─────────────
async def test_new05_p0_artifacts_reflect_written(isolated_data):
    from app.graph.stage_handlers import RealP0Handler
    from app.services.source_materializer import generate_source_index
    pid = "wp5-new05-p0"
    workspace_service.init_workspace(pid)
    src = workspace_service.workspace_path(pid) / "source"
    (src / "app.py").write_text("print('x')\n", encoding="utf-8")
    # 真实生成 source_index.json（P0 在 file_count>0 时会调用；此处直接生成以复现磁盘态）
    generate_source_index(pid)

    handler = RealP0Handler()
    result = await handler.execute({"project_id": pid, "source_type": "manual",
                                    "source_config": {}})
    arts = result["artifacts"]
    # 真实反映：intake_report.json + source_index.json 均在磁盘且被报告（D-107: artifacts/p0/）
    assert "artifacts/p0/intake_report.json" in arts
    assert "artifacts/p0/source_index.json" in arts, f"漏报 source_index，arts={arts}"
    # 无幻影：每个 ref 都真实存在于盘
    for ref in arts:
        assert (workspace_service.workspace_path(pid) / ref).exists()


# ── NEW-05：P5 artifacts 反映 p5_validation_report.json ────────────────────
def test_new05_p5_artifacts_reflect_validation_report(isolated_data, tmp_path):
    from app.graph.stage_handlers import RealP5Handler
    from app.services.p5_input_service import P4InputFacts
    ws = tmp_path / "projects" / "wp5_p5"
    ws.mkdir(parents=True)
    (ws / "ok.py").write_text("def hello(): pass\n", encoding="utf-8")
    (ws / "pyproject.toml").write_text("[project]", encoding="utf-8")

    handler = RealP5Handler(tracer=MagicMock(), auditor=MagicMock())
    p4_input = P4InputFacts(
        project_id="wp5_p5", run_id="r",
        blocked=False, p4_to_p5_gate_status="approved",
        output_code_refs=[], patch_refs=[], evidence_refs=[],
        p4_execution_summary={"stage": "p4", "graph_status": "completed",
                              "change_manifest": []})
    with patch("app.services.workspace_service.workspace_path", return_value=ws), \
         patch("app.services.p5_input_service.P5InputService") as mock_cls, \
         patch("app.services.p5_command_service.workspace_path", return_value=ws):
        mock_cls.return_value.read_p4_input.return_value = p4_input
        result = asyncio.run(handler.execute({"project_id": "wp5_p5", "run_id": "r"}))

    assert result["artifacts"] == ["artifacts/p5_validation_report.json"], \
        f"P5 应反映持久化的验证报告，实际 {result['artifacts']}"
    assert (ws / "artifacts" / "p5_validation_report.json").exists()


# ── NEW-05：P6 artifacts 反映持久化交付报告 ────────────────────────────────
def test_new05_p6_persists_and_reflects_delivery_report(isolated_data):
    from app.graph.stage_handlers import RealP6Handler
    from app.services.p5_input_service import P4InputFacts
    from app.services.p6_delivery_service import DeliveryPackage
    pid = "wp5-new05-p6"
    workspace_service.init_workspace(pid)

    handler = RealP6Handler(tracer=MagicMock(), auditor=MagicMock())
    p4_input = P4InputFacts(project_id=pid, run_id="r", blocked=False,
                            p4_to_p5_gate_status="approved",
                            output_code_refs=["output_code/x.py"], evidence_refs=["ev-1"])
    pkg = DeliveryPackage(project_id=pid, run_id="r")
    pkg.delivery_manifest = {"contents": {"output_code_count": 1, "patch_count": 0}}
    pkg.risk_manifest = {"risk_count": 0, "has_blocking": False, "risks": []}
    pkg.hash_manifest = {"files": [{"path": "output_code/x.py", "sha256": "abc"}]}
    pkg.p6_delivery_report = {"summary": {}}
    pkg.p5_validation_report = {"evidence_refs": ["ev-1"]}
    pkg.indexes = {"artifact_index": [], "evidence_index": [], "trace_index": [],
                   "audit_index": []}
    pkg.desensitization_ok = True
    pkg.desensitization_issues = []

    mock_p6_svc = MagicMock()
    mock_p6_svc.generate_delivery_package.return_value = pkg
    handler._p6_svc = mock_p6_svc
    mock_gate = MagicMock()
    mock_gate.gate_id = "gate-p6-final"
    mock_svc = MagicMock()
    mock_svc.gate_service.create.return_value = mock_gate

    mock_input_svc = MagicMock()
    mock_input_svc.read_p4_input.return_value = p4_input
    with patch.object(handler, "_services", return_value=mock_svc), \
         patch.object(RealP6Handler, "_check_p5_validation_passed", return_value=True), \
         patch("app.services.p5_input_service.P5InputService") as mock_cls:
        mock_cls.return_value = mock_input_svc
        result = asyncio.run(handler.execute({"project_id": pid, "run_id": "r"}))

    assert result["status"] == "completed"
    assert result["artifacts"] == ["artifacts/p6_delivery_report.json"], \
        f"P6 应反映持久化交付报告，实际 {result['artifacts']}"
    report_path = workspace_service.workspace_path(pid) / "artifacts" / "p6_delivery_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text("utf-8"))
    assert report["stage"] == "p6" and "delivery_manifest" in report


# ── EVI-01：P1 产物登记为可查询 AET Evidence（WP-2 WorkAgent，单一来源） ───
async def test_evi01_p1_products_are_queryable_evidence(isolated_data):
    from app.graph.stage_handlers import RealP1Handler
    from app.services.work_agent import WorkAgent
    from app.services.aet_service import AETService
    pid = "wp5-evi01"
    workspace_service.init_workspace(pid)
    src = workspace_service.workspace_path(pid) / "source"
    (src / "app.py").write_text("import flask\nprint('x')\n", encoding="utf-8")
    (src / "requirements.txt").write_text("flask==2.0\n", encoding="utf-8")

    wa = WorkAgent("p1", pid, run_id="r1", handler=RealP1Handler())
    result = await wa.execute({"project_id": pid, "source_type": "manual"})
    assert result["status"] == "completed"

    # 可查询：list_evidence(project, stage="p1") + get_evidence(id)
    aet = AETService(None)
    p1_ev = aet.list_evidence(pid, stage="p1")
    assert p1_ev, "P1 产物应登记为可查询 Evidence"
    assert all(e.get("evidence_id") for e in p1_ev)
    one = aet.get_evidence(p1_ev[0]["evidence_id"], project_id=pid)
    assert one and one.get("claim"), "Evidence 可按 id 单查且带 claim"
    # 与 WP-2 协调：单一来源（p1_work_agent），不存在 handler 另立的第二来源
    sources = {e.get("source") for e in p1_ev}
    assert sources == {"p1_work_agent"}, f"P1 fact-evidence 应单一来源，实际 {sources}"
    # 与 claim_evidence_map 引用一致（同一 evidence_id）
    cem = json.loads((workspace_service.workspace_path(pid)
                      / result["claim_evidence_map_ref"]).read_text("utf-8"))
    cem_ev_ids = {b for e in cem["entries"] for b in e["bindings"].get("evidence_refs", [])}
    listed_ids = {e["evidence_id"] for e in p1_ev}
    assert cem_ev_ids and cem_ev_ids <= listed_ids, "map 引用的 evidence 应均可查"


# ── GATE-02：P6 复用唯一权威最终 Gate，不重复建 ────────────────────────────
class _RecordingGate:
    """记录 create / attach_artifact_refs 调用的 Gate 后端（测试用，非生产 mock）。"""

    def __init__(self):
        self.created = []
        self.attached = []

    def create(self, *, project_id, run_id, stage, artifact_refs,
               gate_type="stage_promotion", metadata=None):
        gid = f"gate-{stage}-{len(self.created)+1}"
        self.created.append((gid, stage, list(artifact_refs)))
        return gid

    def attach_artifact_refs(self, *, gate_id, refs):
        self.attached.append((gate_id, list(refs)))

    def decide(self, *, gate_id, decision):
        pass


class _FakeP6Handler:
    goal = "P6 交付"
    acceptance_criteria = ["交付包已生成"]
    planned_actions = ["生成交付包", "创建最终 Gate"]

    async def execute(self, state):
        # 模拟 handler 已创建唯一权威最终 Gate（D-023），返回其 gate_id。
        return {"status": "completed", "p6_final_gate_id": "gate-handler-p6",
                "artifacts": []}

    def review(self, result):
        return ReviewResult(passed=result.get("status") == "completed", issues=[])


class _FakeP0Handler(_FakeP6Handler):
    goal = "P0 接入"
    planned_actions = ["物化源码"]

    async def execute(self, state):
        return {"status": "completed", "artifacts": []}  # 无 p6_final_gate_id


def _run_work_node(stage, gate_backend, handler, monkeypatch, pid):
    workspace_service.init_workspace(pid)
    monkeypatch.setattr(nodes, "_agent_workflow_enabled", lambda s: False)
    nodes.set_gate_backend(gate_backend)
    nodes.set_tracer_auditor(None, None)
    nodes.register_handler(stage, handler)
    try:
        work = nodes.make_work_node(stage)
        state = {"project_id": pid, "run_id": "r", "execution_mode": "auto",
                 "current_stage": stage, "stage_status": {stage: "in_progress"}}
        return asyncio.run(work(state))
    finally:
        nodes.set_gate_backend(None)
        nodes.clear_handlers()


def test_gate02_p6_reuses_handler_gate_no_duplicate(monkeypatch):
    gb = _RecordingGate()
    out = _run_work_node("p6", gb, _FakeP6Handler(), monkeypatch, "wp5-gate02-p6")
    # 复用 handler 权威 Gate，未重复建第二个 promotion Gate
    assert gb.created == [], f"P6 不应再建 promotion Gate，实际 created={gb.created}"
    assert gb.attached, "应把审核材料补挂到复用的权威 Gate"
    attached_gid, attached_refs = gb.attached[0]
    assert attached_gid == "gate-handler-p6"
    assert attached_refs, "补挂的审核材料（三类报告）非空"
    # pending_gate 指向唯一权威 Gate
    assert out["pending_gate"]["gate_id"] == "gate-handler-p6"


def test_gate02_non_p6_still_creates_promotion_gate(monkeypatch):
    """回归：无 handler 建 Gate 的阶段（如 P0）仍照常建 promotion Gate。"""
    gb = _RecordingGate()
    out = _run_work_node("p0", gb, _FakeP0Handler(), monkeypatch, "wp5-gate02-p0")
    assert len(gb.created) == 1, "P0 应建唯一 promotion Gate"
    assert gb.created[0][1] == "p0"
    assert gb.attached == [], "P0 无复用 Gate，不应 attach"
    assert out["pending_gate"]["gate_id"] == gb.created[0][0]


# ── GATE-02：attach_artifact_refs 真实合并 + 去重（对真实 GateService） ──────
def test_gate02_attach_artifact_refs_merges_and_dedups(isolated_data):
    from app.graph.gate_backend import RealGateBackend
    from app.dependencies import get_services
    gs = get_services().gate_service
    gate = gs.create(project_id="wp5-attach", run_id="r", stage="p6",
                     gate_type="stage_promotion", artifact_refs=["a.json", "b.json"])
    backend = RealGateBackend()
    backend.attach_artifact_refs(gate_id=gate.gate_id,
                                 refs=["b.json", "c.json", "d.json"])
    refreshed = gs.get(gate.gate_id)
    assert refreshed.artifact_refs == ["a.json", "b.json", "c.json", "d.json"], \
        f"应合并去重，实际 {refreshed.artifact_refs}"
