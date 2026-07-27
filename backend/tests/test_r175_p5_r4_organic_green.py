"""R17.5-P5-R4-a 干净真阳 P5→P6 全链真跑验收（主窗口验收自建，非 mock，D-097）。

背景（R12-18 教训 + feedback_dtrack_must_be_real_run）：P5 历轮验收多为 blocked/诚实降级路径，
handler 端到端【真阳】路径长期缺真实单测——每轮修复引回归正因此路径无测拦截。本测建立一次
【干净真阳】基线并固化为回归测：自建合法 P4 产物集（output_code + patches + 真实 AET evidence
带 sha256 + p4_execution_summary + 真实 approved P4→P5 Gate + task_node_run 落行），驱动真实
RealP5Handler.execute（不 mock P5InputService/验证服务/命令服务）→ 产真实
artifacts/p5_validation_report.json（can_be_completed=True），再驱动真实 RealP6Handler.execute
读该真实报告 → 产真实 artifacts/p6_delivery_report.json。

红线：无 mock 生产路径；命令经真实 ExecutionProvider 执行（python compileall/pytest 真退出码）；
不硬编码 MicroOA；产物真实落盘后被读取核验（No Evidence No Completed）。
"""

import asyncio
import hashlib
import json

from app.core.database import get_session
from app.dependencies import get_services
from app.models.task_node_run import TaskNodeRun
from app.schemas.gate import GateDecisionRequest
from app.services import workspace_service


def _build_legal_p4_artifacts(pid: str, run_id: str) -> dict:
    """在真实工作区落一套合法 P4 产物集，返回构造出的 refs（供断言）。"""
    ws = workspace_service.workspace_path(pid)
    (ws / "output_code").mkdir(parents=True, exist_ok=True)
    (ws / "patches").mkdir(parents=True, exist_ok=True)
    (ws / "artifacts" / "p4").mkdir(parents=True, exist_ok=True)

    # 1. output_code：合法可编译 python（真阳前提：compileall/pytest 真绿）
    out_rel = "output_code/migrate_service.py"
    out_code = (
        "def add(a, b):\n"
        "    return a + b\n\n"
        "def migrate_record(row):\n"
        "    return {k: v for k, v in row.items() if v is not None}\n"
    )
    (ws / out_rel).write_text(out_code, encoding="utf-8")
    out_sha = hashlib.sha256((ws / out_rel).read_bytes()).hexdigest()
    # 构建标记文件（真实迁移产出的工程含依赖清单）→ 命令检测识别为 python 工程
    (ws / "output_code" / "requirements.txt").write_text("", encoding="utf-8")
    # 迁移产物自带通过的测试用例（真实工程含测试）→ tests_pass 真实命令真绿
    (ws / "output_code" / "test_migrate_service.py").write_text(
        "def test_add():\n    assert 1 + 1 == 2\n", encoding="utf-8")

    # 2. patch：真实存在且非空
    patch_rel = "patches/migrate_service.diff"
    (ws / patch_rel).write_text(
        "--- a/legacy.py\n+++ b/output_code/migrate_service.py\n@@\n+def add(a, b):\n+    return a + b\n",
        encoding="utf-8")

    # 3. 真实 AET Evidence（stage=p4，带 output_code_ref + output_sha256 → sha256 校验真过）
    aet = get_services().aet_service
    ev_id = "ev-p4-r4a-001"
    aet.write_evidence(
        pid, ev_id, evidence_type="build_evidence", status="validated",
        source="p4_work_agent", claim="P4 产出 migrate_service.py 且构建通过", stage="p4",
        extra={"output_code_ref": out_rel, "output_sha256": out_sha})

    # 4. p4_execution_summary.json（含硬必需字段 stage/graph_status/change_manifest）
    summary = {
        "stage": "p4", "graph_status": "completed", "node_count": 1,
        "change_manifest": [{"path": out_rel, "action": "created"}],
        "patch_index": [{"path": patch_rel, "node_id": "tn-001"}],
        "evidence_refs": [ev_id],
    }
    (ws / "artifacts" / "p4" / "p4_execution_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5. task_node_run 落行（DB 单一事实源；read_p4_input 从此取 refs）
    db = get_session()
    try:
        db.add(TaskNodeRun(
            node_id="tn-001", task_graph_run_id=run_id, project_id=pid, run_id=run_id,
            stage="p4", node_status="completed",
            artifact_refs=[out_rel, patch_rel], evidence_refs=[ev_id]))
        db.commit()
    finally:
        db.close()

    return {"out_rel": out_rel, "patch_rel": patch_rel, "ev_id": ev_id, "run_id": run_id}


def _approve_p4_to_p5_gate(pid: str, run_id: str) -> str:
    """真实创建 P4→P5 晋级 Gate 并 approve（真 DB，非伪造）。"""
    gs = get_services().gate_service
    gate = gs.create(project_id=pid, run_id=run_id, stage="p4",
                     gate_type="stage_promotion",
                     reason="P4 执行完成，晋级 P5 验证",
                     artifact_refs=["artifacts/p4/p4_execution_summary.json"])
    gs.decide(gate.gate_id, GateDecisionRequest(decision="approve", note="真跑验收批准"),
              drive_promotion=False)
    refreshed = gs.get(gate.gate_id)
    assert refreshed.gate_status == "approved", f"Gate 应 approved，实际 {refreshed.gate_status}"
    return gate.gate_id


def test_r4a_clean_true_positive_p5_to_p6_full_chain(isolated_data):
    """干净真阳：合法 P4 集 → RealP5Handler 真跑产 can_be_completed=True → RealP6Handler 真跑交付。"""
    from app.graph.stage_handlers import RealP5Handler, RealP6Handler

    pid = "r175-p5-r4a-green"
    run_id = "run-r4a"
    workspace_service.init_workspace(pid)
    refs = _build_legal_p4_artifacts(pid, run_id)
    _approve_p4_to_p5_gate(pid, run_id)

    # ── P5：真实 handler 全跑（不 mock 输入/验证/命令服务）────────────────
    p5 = RealP5Handler()
    p5_result = asyncio.run(p5.execute({"project_id": pid, "run_id": run_id}))

    ws = workspace_service.workspace_path(pid)
    report_path = ws / "artifacts" / "p5_validation_report.json"
    assert report_path.exists(), "P5 应真实落 p5_validation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # 门禁真阳：5 硬必需 validated + 4 条件槽 validated/NA → can_be_completed=True
    assert report["can_be_completed"] is True, (
        f"应真阳可完成，实际 reason={report.get('completion_reason')} / "
        f"slots={[(s['slot_id'], s['status']) for s in report['validation_plan']['slots']]}")
    assert p5_result["status"] == "completed"

    slots = {s["slot_id"]: s["status"] for s in report["validation_plan"]["slots"]}
    # 5 硬必需全 validated（真实文件/evidence sha256/summary/gate）
    for hard in ("output_code_exists", "patches_exist", "p4_evidence_real",
                 "p4_summary_readable", "p4_p5_gate_approved"):
        assert slots[hard] == "validated", f"硬必需 {hard} 应 validated，实际 {slots[hard]}"
    # 条件槽：build/static/tests 真实命令 validated，run 命令-less → not_applicable
    assert slots["run_verified"] == "not_applicable"
    for cond in ("build_verified", "static_check", "tests_pass"):
        assert slots[cond] == "validated", f"条件槽 {cond} 应 validated，实际 {slots[cond]}"

    # 反伪造：advisory 存在则必须 analysis_only，且不改 can_be_completed
    adv = p5_result.get("llm_advisory") or {}
    if adv.get("status") == "completed":
        assert adv.get("analysis_only") is True, "advisory 必须 analysis_only（不改门禁）"

    # ── P6：真实 handler 读真实 P5 报告 → 交付 ────────────────────────────
    p6 = RealP6Handler()
    p6_result = asyncio.run(p6.execute({"project_id": pid, "run_id": run_id}))
    assert p6_result["status"] == "completed", (
        f"P5 真阳后 P6 应交付，实际 {p6_result['status']} / {p6_result.get('reason')}")
    delivery_path = ws / "artifacts" / "p6_delivery_report.json"
    assert delivery_path.exists(), "P6 应真实落 p6_delivery_report.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    assert delivery["stage"] == "p6" and "delivery_manifest" in delivery
    assert p6_result["p6_final_gate_id"], "P6 应创建最终 Gate（待用户批准，D-023）"
    # 交付包内容真实反映产物
    assert p6_result["desensitization"]["ok"] is True, "干净 python 交付脱敏应通过"


def test_r4a_negative_missing_patch_blocks_p5_and_p6(isolated_data):
    """反向对照：缺 patch（硬必需 patches_exist 不过）→ P5 不得 completed → P6 诚实 blocked。"""
    from app.graph.stage_handlers import RealP5Handler, RealP6Handler

    pid = "r175-p5-r4a-neg"
    run_id = "run-r4a-neg"
    workspace_service.init_workspace(pid)
    refs = _build_legal_p4_artifacts(pid, run_id)
    # 删除 patch 文件 → patches_exist 应 validation_failed（真实缺失）
    (workspace_service.workspace_path(pid) / refs["patch_rel"]).unlink()
    _approve_p4_to_p5_gate(pid, run_id)

    p5 = RealP5Handler()
    p5_result = asyncio.run(p5.execute({"project_id": pid, "run_id": run_id}))
    report = json.loads((workspace_service.workspace_path(pid) / "artifacts"
                         / "p5_validation_report.json").read_text(encoding="utf-8"))
    assert report["can_be_completed"] is False, "缺 patch → 不得真阳（反伪造）"
    assert p5_result["status"] == "blocked"

    p6 = RealP6Handler()
    p6_result = asyncio.run(p6.execute({"project_id": pid, "run_id": run_id}))
    assert p6_result["status"] == "blocked", "P5 未过 → P6 诚实 blocked，不伪造交付"
