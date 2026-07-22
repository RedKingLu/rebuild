"""R11-3 B-P0-FAKE-1 regression: P0 Gate materials must be REAL, not fabricated.

Root cause (坐实): /onboarding/complete hand-wrote p0_execution_record.json (7 steps
sharing one timestamp), p0_construction_report.md (a template ending "本报告由 R9-3G
Gate 附审材料系统自动生成"), and p0_review_pass.json (an always-pass review). The
frontend GatePanel hardcoded those filenames, so the user saw fabricated materials.

Fix: the P0 review materials are produced by the REAL LangGraph P0 node
(RealP0Handler → StageLoop → StageReports: p0_start_plan / p0_construction /
p0_acceptance) and the Gate's artifact_refs point at them. This route no longer
fabricates. D-101: fabricated materials never count as passed validation.

These tests assert on the real produced files / gate refs — no mock, no fabricated pass.
"""

import json

from app.services import workspace_service


def _make_manual_project(client, name="MicroOA-real-mat"):
    r = client.post("/api/projects", json={"name": name, "source_type": "manual",
                                           "source_config": {}})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["project_id"]


def _artifacts_dir(pid):
    # D-107: stage artifacts live in artifacts/{stage}/ subdirectory.
    return workspace_service.workspace_path(pid) / "artifacts" / "p0"


def test_fabricated_p0_files_are_not_produced(client):
    """The three template/hardcoded/always-pass files must no longer be written."""
    pid = _make_manual_project(client)
    resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
    assert resp.status_code == 200, resp.text
    # R17-X 联调修复: complete 不再自动驱动 P0 图。P0 图执行（含真实材料产出）由用户点
    # 「开始」触发的 POST /onboarding/execute 完成 — 故先驱动 execute，再断言产物。
    exec_resp = client.post(f"/api/projects/{pid}/onboarding/execute")
    assert exec_resp.status_code == 200, exec_resp.text

    art = _artifacts_dir(pid)
    for fabricated in ("p0_execution_record.json", "p0_construction_report.md", "p0_review_pass.json"):
        assert not (art / fabricated).exists(), \
            f"Fabricated file {fabricated} was produced — B-P0-FAKE-1 not fixed (D-101)."


def test_real_stage_reports_are_produced(client):
    """The real StageReports (start_plan / construction / acceptance) must exist and be real."""
    pid = _make_manual_project(client)
    resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
    assert resp.status_code == 200, resp.text
    # R17-X: 真实 StageReports 由 execute 驱动的图 P0 节点产出（complete 不再驱动图）
    exec_resp = client.post(f"/api/projects/{pid}/onboarding/execute")
    assert exec_resp.status_code == 200, exec_resp.text

    art = _artifacts_dir(pid)
    for real in ("p0_start_plan.json", "p0_construction.json", "p0_acceptance.json"):
        p = art / real
        assert p.exists(), f"Real StageReport {real} was not produced by the P0 node."
        payload = json.loads(p.read_text(encoding="utf-8"))
        # every StageReport carries a real generated_at and its own kind
        assert payload.get("generated_at"), f"{real} missing generated_at"
        # no template fabrication marker anywhere in the file
        assert "自动生成" not in p.read_text(encoding="utf-8"), \
            f"{real} contains a template auto-generation marker (fabrication smell)."

    # acceptance report is a genuine review verdict, not a hardcoded always-pass template
    acc = json.loads((art / "p0_acceptance.json").read_text(encoding="utf-8"))
    assert acc.get("kind") == "acceptance"
    assert "passed" in acc and isinstance(acc["passed"], bool)
    assert "issues" in acc and isinstance(acc["issues"], list)
    assert acc.get("reviewer")


def test_gate_artifact_refs_point_to_real_reports(client):
    """The P0 Gate's artifact_refs must reference the real StageReports, not fabricated files."""
    pid = _make_manual_project(client)
    resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
    assert resp.status_code == 200, resp.text
    # R17-X: P0 Gate 由 execute 驱动的图创建（complete 不再创建 Gate）
    exec_resp = client.post(f"/api/projects/{pid}/onboarding/execute")
    assert exec_resp.status_code == 200, exec_resp.text

    # the aggregate exposes the active gate the frontend GatePanel renders from
    agg = client.get(f"/api/projects/{pid}/workspace").json()["data"]
    gate = agg.get("active_gate")
    assert gate is not None, "No active P0 gate after onboarding/execute"
    refs = gate.get("artifact_refs") or []
    assert refs, "Gate has no artifact_refs — panel would fall back to hardcoded names"
    joined = " ".join(refs)
    # real reports referenced
    assert "p0_acceptance.json" in joined, f"Gate refs missing real acceptance report: {refs}"
    # fabricated names must NOT be referenced
    for fabricated in ("p0_execution_record.json", "p0_construction_report.md", "p0_review_pass.json"):
        assert fabricated not in joined, f"Gate still references fabricated {fabricated}: {refs}"


def test_stage_artifacts_p0_lists_real_names(client):
    """The /stage-artifacts/p0 core list must be the real StageReport names."""
    pid = _make_manual_project(client)
    client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
    # R17-X: 真实 P0 产物由 execute 驱动的图产出（complete 不再驱动图）
    client.post(f"/api/projects/{pid}/onboarding/execute")
    d = client.get(f"/api/projects/{pid}/stage-artifacts/p0").json()["data"]
    names = {a["name"] for a in d["artifacts"]}
    assert {"p0_start_plan.json", "p0_construction.json", "p0_acceptance.json"} <= names, \
        f"stage-artifacts/p0 does not list the real StageReports: {names}"
    assert "p0_execution_record.json" not in names
    assert "p0_review_pass.json" not in names
