"""R10 T17 tests: RealP3Handler — P3 planning runs as a real StageHandler.

Proves the P3 node carries real business (PlanningService: Stage Plan → Task Plan
Batch → TaskGraph 必生 → §5.6 Artifacts + §5.7 Evidence), not a stub. blocked/failed
→ review fails → StageLoop escalates to a Gate honestly (§5.10). P4 stays unregistered
(future_r11 stub, Q-R10-3). Uses a sequenced fake gateway + tmp workspace — no live LLM.
"""

import json
from types import SimpleNamespace

import pytest

from app.graph.stage_handlers import RealP3Handler
from app.services.planning_service import PlanningService
from app.services import aet_service as aet_mod
from app.services import workspace_service


class _SeqGateway:
    """Returns queued call results in order (P3 execute makes 3 model calls)."""
    def __init__(self, overall="available", results=None):
        self._overall = overall
        self._results = list(results or [])
        self._i = 0

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
        r = self._results[min(self._i, len(self._results) - 1)] if self._results else {}
        self._i += 1
        return r


_SP = json.dumps({
    "objective": "迁移到信创栈", "scope": ["Web 层"], "out_of_scope": ["前端"],
    "risk_level": "L3", "permission_boundary": "source/",
    "expected_artifacts": ["plan.md"], "expected_evidence": ["对照表"],
    "gate_policy": {"high_risk": "require_gate"}, "completion_criteria": ["计划完成"],
    "validation_strategy": "回归测试",
})
_BATCH = json.dumps({
    "batch_objective": "迁移任务", "batch_scope": ["路由"], "permission_boundary": "source/",
    "validation_strategy": "逐任务回归", "exception_policy": "升级 Gate",
    "task_plans": [
        {"objective": "替换路由", "risk_level": "L2", "validation_method": "回归", "title": "路由"},
        {"objective": "替换依赖", "risk_level": "L4", "validation_method": "构建", "title": "依赖"},
    ],
})
_EDGES = '{"edges": [{"source_index": 0, "target_index": 1, "edge_type": "sequence"}]}'


def _handler(overall, results, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service, "workspace_path", lambda pid: tmp_path / pid)
    monkeypatch.setattr(aet_mod, "workspace_path", lambda pid: tmp_path / pid)
    aet = aet_mod.AETService(services=None)
    gw = _SeqGateway(overall=overall, results=results)
    svc = PlanningService(gateway=gw, aet_service=aet)
    return RealP3Handler(planning_service=svc), aet


def _ok(content, model="m"):
    return {"status": "completed", "content": content, "model": model}


async def test_p3_handler_completed_full_chain(tmp_path, monkeypatch):
    h, aet = _handler("available", [_ok(_SP), _ok(_BATCH), _ok(_EDGES)], tmp_path, monkeypatch)
    res = await h.execute({"project_id": "proj-1", "run_id": "r1", "user_goal": "迁移"})
    assert res["status"] == "completed"
    assert res["stage_plan_ref"] and res["task_graph_ref"]        # Q-R10-3 必生
    assert len(res["artifacts"]) == 3                              # §5.6
    assert len(res["evidence_refs"]) == 6                          # §5.7 six items
    art = tmp_path / "proj-1" / "artifacts"
    # D-107 (R17.5 P3): 领域 3 产物迁入 artifacts/p3/ 子目录（约定变更，非 bug）。
    assert (art / "p3" / "p3_task_graph.json").exists()
    assert len(aet.list_evidence("proj-1", stage="p3")) == 6
    assert h.review(res).passed is True


async def test_p3_handler_blocked_escalates(tmp_path, monkeypatch):
    h, aet = _handler("not_configured", [], tmp_path, monkeypatch)
    res = await h.execute({"project_id": "proj-1"})
    assert res["status"] == "blocked"
    assert res["artifacts"] == [] and res["evidence_refs"] == []
    rev = h.review(res)
    assert rev.passed is False                                    # escalate (§5.10)
    assert any(i["type"] == "planning_not_completed" for i in rev.issues)
    assert aet.list_evidence("proj-1") == []                      # nothing faked


def test_p3_registered_p4_skeleton(tmp_path):
    """P3 is a real handler after bootstrap; P4 is now a registered C3 skeleton
    handler (R11-3-C3) — loads P3 TaskGraph, honest blocked/waiting_input, never
    fakes completed. P5-P6 stay unregistered stubs."""
    from app.graph import nodes
    h = nodes.get_handler("p3")
    assert h is not None and h.__class__.__name__ == "RealP3Handler"
    h4 = nodes.get_handler("p4")
    assert h4 is not None and h4.__class__.__name__ == "RealP4Handler"  # C3: registered
    h5 = nodes.get_handler("p5")
    assert h5 is not None and h5.__class__.__name__ == "RealP5Handler"  # R12-3-C3: registered skeleton
