"""R10 T9 tests: P2 AssessmentService (契约 §4).

Evidence for T9 (planning §5 V1/V5): 6 outputs produced; no model → blocked
(Q-R10-2, no rule fallback); model failure → failed (公理3, no fake success);
model output marked analysis_only (§4.4-8/§4.7-5); Evidence covers §4.7 five items.
Uses a fake gateway — no live LLM call.
"""

import json
from types import SimpleNamespace

import pytest

from app.services.assessment_service import (
    AssessmentService, AssessmentResult, ASSESSMENT_OUTPUTS,
)


class _FakeGateway:
    def __init__(self, overall="available", call_result=None):
        self._overall = overall
        self._call_result = call_result or {}

    def get_status(self):
        return SimpleNamespace(overall_status=self._overall)

    def stage_model_readiness(self, **kwargs):
        # WP-6: emulate ModelGateway.stage_model_readiness — availability derives from
        # the same `overall` knob; provide a minimal candidate chain for surfacing.
        available = self._overall == "available"
        chain = [] if available else [{"profile_id": "fake/model", "provider_id": "fake",
                                       "model": "fake", "is_fallback": False,
                                       "outcome": "not_configured", "error_category": "not_configured",
                                       "error_message": "模型未配置"}]
        return {"available": available, "capability_ok": available,
                "reason": "" if available else "无任一已配置且具备有效凭据的模型可用",
                "attempted_chain": chain,
                "user_actions": [{"action": "configure", "label": "配置模型 / API Key", "target": "models"}]}

    async def call(self, **kwargs):
        return self._call_result


_GOOD_CONTENT = json.dumps({
    "assessment_report": {"summary": "中等迁移风险", "feasibility": "可行"},
    "risk_list": [{"title": "依赖不兼容", "risk_level": "L3", "source": "requirements.txt",
                   "basis": "flask 2.0 与目标栈冲突"}],
    "blocker_list": [{"title": "缺少数据库凭据"}],
    "uncertainty_list": [{"title": "运行环境未知"}],
    "validation_gap_list": [{"title": "无集成测试"}],
    "resource_needs": [{"title": "需确定性转换工具 X"}],
})


def test_outputs_six():
    assert len(ASSESSMENT_OUTPUTS) == 6


async def test_no_model_blocked():
    """Q-R10-2: no available model → blocked, no rule fallback."""
    svc = AssessmentService(gateway=_FakeGateway(overall="not_configured"))
    res = await svc.assess("proj-1")
    assert res.status == "blocked"
    assert "no_model_key" in res.reason


async def test_model_failure_is_failed_not_fake_success():
    svc = AssessmentService(gateway=_FakeGateway(
        overall="available", call_result={"status": "failed", "error_message": "timeout"}))
    res = await svc.assess("proj-1")
    assert res.status == "failed"
    assert "timeout" in res.reason


async def test_completed_produces_six_outputs_analysis_only():
    svc = AssessmentService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": _GOOD_CONTENT, "model": "deepseek/chat"}))
    res = await svc.assess("proj-1", user_goal="迁移到信创栈")
    assert res.status == "completed"
    assert res.analysis_only is True            # §4.4-8: auxiliary analysis, not fact
    assert res.assessment_report.get("feasibility") == "可行"
    assert res.risk_list and res.risk_list[0]["risk_level"] == "L3"
    assert res.blocker_list and res.uncertainty_list
    assert res.validation_gap_list and res.resource_needs
    assert res.model_used == "deepseek/chat"


async def test_evidence_covers_contract_4_7():
    svc = AssessmentService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": _GOOD_CONTENT, "model": "m"}))
    res = await svc.assess("proj-1")
    types = {e["type"] for e in res.evidence}
    # §4.7 five items
    assert {"assessment_inputs", "risk_basis", "blocker_source",
            "validation_gap_source", "model_output_marker"} <= types
    marker = next(e for e in res.evidence if e["type"] == "model_output_marker")
    assert marker["analysis_only"] is True       # §4.7-5 / STOP-4


async def test_unparseable_content_keeps_raw_not_crash():
    svc = AssessmentService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": "not json at all", "model": "m"}))
    res = await svc.assess("proj-1")
    assert res.status == "completed"
    assert res.assessment_report.get("parse_error") is True
    assert res.risk_list == []                   # honest: empty, not hallucinated


async def test_json_fence_tolerated():
    fenced = "```json\n" + _GOOD_CONTENT + "\n```"
    svc = AssessmentService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": fenced, "model": "m"}))
    res = await svc.assess("proj-1")
    assert res.status == "completed"
    assert res.risk_list  # parsed through the ```json fence


# ── T10: Evidence persistence (契约 §4.7 落库可查 / D-066 / §4.10) ──────────

class _FakeAet:
    """Captures write_evidence calls without touching the filesystem."""
    def __init__(self):
        self.writes = []

    def write_evidence(self, **kw):
        self.writes.append(kw)
        return {**kw, "created_at": "t"}


async def _completed_result(user_goal="迁移到信创栈"):
    svc = AssessmentService(gateway=_FakeGateway(
        overall="available",
        call_result={"status": "completed", "content": _GOOD_CONTENT, "model": "deepseek/chat"}))
    return svc, await svc.assess("proj-1", user_goal=user_goal)


async def test_persist_evidence_writes_five_items():
    """§4.7 five Evidence items are persisted with correct types/stage/source."""
    svc, res = await _completed_result()
    aet = _FakeAet()
    persisted = svc.persist_evidence("proj-1", res, aet_service=aet)
    assert len(aet.writes) == 5 and len(persisted) == 5
    types = {w["evidence_type"] for w in aet.writes}
    assert {"assessment_inputs", "risk_basis", "blocker_source",
            "validation_gap_source", "model_output_marker"} == types
    assert all(w["stage"] == "p2" and w["source"] == "p2_assessment" for w in aet.writes)


async def test_persisted_risk_evidence_is_traceable():
    """Each risk traces to its read file / basis (T10 acceptance)."""
    svc, res = await _completed_result()
    aet = _FakeAet()
    svc.persist_evidence("proj-1", res, aet_service=aet)
    risk = next(w for w in aet.writes if w["evidence_type"] == "risk_basis")
    item = risk["extra"]["items"][0]
    assert item["source"] == "requirements.txt"           # traces to the read file
    assert "flask" in item["basis"]                        # judgment basis retained
    marker = next(w for w in aet.writes if w["evidence_type"] == "model_output_marker")
    assert marker["extra"]["analysis_only"] is True        # §4.7-5 / STOP-4


async def test_persisted_evidence_is_queryable_roundtrip(tmp_path, monkeypatch):
    """D-066 落库可查: real AETService write → list_evidence reads back 5 items."""
    from app.services import aet_service as aet_mod
    monkeypatch.setattr(aet_mod, "workspace_path", lambda pid: tmp_path / pid)
    real_aet = aet_mod.AETService(services=None)
    svc, res = await _completed_result()
    svc.persist_evidence("proj-1", res, aet_service=real_aet)
    listed = real_aet.list_evidence("proj-1", stage="p2")
    assert len(listed) == 5
    assert {e["type"] for e in listed} >= {"assessment_inputs", "risk_basis"}


async def test_blocked_persists_no_evidence():
    """§4.10: blocked/failed never fakes completed Evidence."""
    svc = AssessmentService(gateway=_FakeGateway(overall="not_configured"))
    res = await svc.assess("proj-1")
    assert res.status == "blocked"
    aet = _FakeAet()
    persisted = svc.persist_evidence("proj-1", res, aet_service=aet)
    assert persisted == [] and aet.writes == []

