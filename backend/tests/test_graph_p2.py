"""R10 T11 tests: RealP2Handler — P2 assessment runs as a real StageHandler.

Proves the P2 node carries real business (AssessmentService LLM analysis →
§4.6 Artifacts + §4.7 Evidence 落库), not a future_r10 stub. blocked/failed →
review fails → StageLoop escalates to a Gate honestly (§4.10, no fake completed).
Uses a fake gateway + tmp workspace — no live LLM call.
"""

import json
from types import SimpleNamespace

import pytest

from app.graph.stage_handlers import RealP2Handler
from app.services.assessment_service import AssessmentService
from app.services import aet_service as aet_mod
from app.services import workspace_service


class _FakeGateway:
    def __init__(self, overall="available", call_result=None):
        self._overall = overall
        self._call_result = call_result or {}

    def get_status(self):
        return SimpleNamespace(overall_status=self._overall)

    def stage_model_readiness(self, **kwargs):
        ok = self._overall == "available"
        return {"available": ok, "capability_ok": ok, "reason": "" if ok else "无可用模型",
                "attempted_chain": [] if ok else [{"profile_id": "fake/m", "provider_id": "fake",
                    "model": "m", "is_fallback": False, "outcome": "not_configured",
                    "error_category": "not_configured", "error_message": "未配置"}],
                "user_actions": [{"action": "configure", "label": "配置模型 / API Key", "target": "models"}]}

    async def call(self, **kwargs):
        return self._call_result


_GOOD = json.dumps({
    "assessment_report": {"feasibility": "可行"},
    "risk_list": [{"title": "依赖不兼容", "risk_level": "L3",
                   "source": "requirements.txt", "basis": "flask 2.0 与目标栈冲突"}],
    "blocker_list": [{"title": "缺少数据库凭据"}],
    "uncertainty_list": [{"title": "运行环境未知"}],
    "validation_gap_list": [{"title": "无集成测试"}],
    "resource_needs": [{"title": "需确定性转换工具 X"}],
})


def _handler_with(gateway, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service, "workspace_path", lambda pid: tmp_path / pid)
    monkeypatch.setattr(aet_mod, "workspace_path", lambda pid: tmp_path / pid)
    aet = aet_mod.AETService(services=None)
    svc = AssessmentService(gateway=gateway, aet_service=aet)
    return RealP2Handler(assessment_service=svc), aet


async def test_p2_handler_completed_produces_artifacts_and_evidence(tmp_path, monkeypatch):
    h, aet = _handler_with(
        _FakeGateway(call_result={"status": "completed", "content": _GOOD, "model": "m"}),
        tmp_path, monkeypatch)
    res = await h.execute({"project_id": "proj-1", "run_id": "r1", "user_goal": "迁移到信创栈"})
    assert res["status"] == "completed"
    assert res["analysis_only"] is True
    # §4.6 five artifacts on disk
    assert len(res["artifacts"]) == 5
    art = tmp_path / "proj-1" / "artifacts" / "p2"  # D-107: P2 产物分层
    assert (art / "p2_risk_list.json").exists()
    assert (art / "p2_assessment_report.json").exists()
    # §4.7 five evidence persisted + queryable
    assert len(res["evidence_refs"]) == 5
    assert len(aet.list_evidence("proj-1", stage="p2")) == 5
    # review passes (完成条件满足)
    assert h.review(res).passed is True


async def test_p2_handler_blocked_escalates_no_artifacts(tmp_path, monkeypatch):
    h, aet = _handler_with(_FakeGateway(overall="not_configured"), tmp_path, monkeypatch)
    res = await h.execute({"project_id": "proj-1"})
    assert res["status"] == "blocked"
    assert res["artifacts"] == [] and res["evidence_refs"] == []
    rev = h.review(res)
    assert rev.passed is False   # StageLoop escalates to a Gate (§4.10 honest)
    assert any(i["type"] == "assessment_not_completed" for i in rev.issues)
    assert aet.list_evidence("proj-1") == []   # nothing faked


async def test_p2_handler_unparseable_fails_review_for_retry(tmp_path, monkeypatch):
    h, aet = _handler_with(
        _FakeGateway(call_result={"status": "completed", "content": "not json", "model": "m"}),
        tmp_path, monkeypatch)
    res = await h.execute({"project_id": "proj-1"})
    assert res["status"] == "completed"
    rev = h.review(res)
    assert rev.passed is False   # parse_error → ReviewPass retries structured output
    assert any(i["type"] == "unparseable_output" for i in rev.issues)


def test_p2_registered_after_bootstrap():
    """P2 is a registered real handler (not a future_r10 stub) after app bootstrap."""
    from app.graph import nodes
    h = nodes.get_handler("p2")
    assert h is not None and h.__class__.__name__ == "RealP2Handler"
