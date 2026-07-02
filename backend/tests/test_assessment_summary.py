"""R10 T18 tests: P2 assessment-summary read endpoint (backs StagePageP2).

Proves GET /assessment-summary parses the p2_*.json artifacts RealP2Handler writes
(契约 §4.5 六类产出) into the 6 outputs the frontend renders, and returns an honest
empty state (available=false) when P2 has not been assessed. Fully offline: seeds
artifact files directly, never drives the graph into P2 (no real LLM call).
"""

import json

from app.services import workspace_service


def _mk_project(client) -> str:
    return client.post("/api/projects", json={
        "name": "T18 P2", "source_type": "manual",
    }).json()["data"]["project_id"]


def _seed_p2_artifacts(pid: str) -> None:
    art = workspace_service.workspace_path(pid) / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "p2_assessment_report.json").write_text(json.dumps({
        "artifact_type": "assessment_report", "analysis_only": True,
        "report": {"summary": "迁移可行，存在中高风险"},
        "uncertainty_list": [{"title": "第三方库许可证未确认"}],
    }, ensure_ascii=False), encoding="utf-8")
    (art / "p2_risk_list.json").write_text(json.dumps({
        "artifact_type": "risk_assessment",
        "items": [{"title": "内核系统调用不兼容", "risk_level": "L4",
                   "source": "profiling_summary", "basis": "glibc 依赖"}],
    }, ensure_ascii=False), encoding="utf-8")
    (art / "p2_blocker_list.json").write_text(json.dumps({
        "artifact_type": "blocker_list",
        "items": [{"title": "缺少目标平台编译器", "source": "environment.json"}],
    }, ensure_ascii=False), encoding="utf-8")
    (art / "p2_validation_gaps.json").write_text(json.dumps({
        "artifact_type": "validation_gap",
        "items": [{"title": "无集成测试覆盖", "source": "profiling_summary"}],
    }, ensure_ascii=False), encoding="utf-8")
    (art / "p2_resource_needs.json").write_text(json.dumps({
        "artifact_type": "resource_needs",
        "items": [{"title": "确定性转换器：字节序适配"}],
    }, ensure_ascii=False), encoding="utf-8")


def test_assessment_summary_empty_when_not_assessed(client):
    pid = _mk_project(client)
    r = client.get(f"/api/projects/{pid}/assessment-summary")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is False        # honest empty state, not 404 / not fabricated
    assert "reason" in data


def test_assessment_summary_returns_six_outputs(client):
    pid = _mk_project(client)
    _seed_p2_artifacts(pid)
    data = client.get(f"/api/projects/{pid}/assessment-summary").json()["data"]

    assert data["available"] is True
    assert data["analysis_only"] is True                       # §4.7-5 / STOP-4
    assert data["assessment_report"]["summary"].startswith("迁移可行")
    assert data["risk_list"][0]["risk_level"] == "L4"          # grouping + source/basis for the page
    assert data["risk_list"][0]["basis"] == "glibc 依赖"
    assert data["blocker_list"][0]["source"] == "environment.json"
    assert data["uncertainty_list"][0]["title"].startswith("第三方库")
    assert data["validation_gap_list"][0]["title"] == "无集成测试覆盖"
    assert data["resource_needs"][0]["title"].startswith("确定性转换器")
    # all five artifact refs surfaced
    assert len(data["artifacts"]) == 5


def test_assessment_summary_404_for_unknown_project(client):
    assert client.get("/api/projects/nope-xyz/assessment-summary").status_code == 404
