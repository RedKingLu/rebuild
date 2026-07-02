"""R10-5 P1-C: P2/P3 handlers must run through the unified context_assembler +
Skill layer (S4), using progressive disclosure (C3 = Skill metadata only, NOT the
full SKILL.md body). Regression for R10-4 §7/§15 P1-C.
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.graph.stage_handlers import RealP2Handler, RealP3Handler
from app.services.assessment_service import AssessmentService
from app.services.planning_service import PlanningService
from app.services import aet_service as aet_mod


_SKILL_BODY = "P2 评估技能正文详解 SECRETBODYMARKER 全文步骤一二三四五六七八九十" * 20
_SKILL_DESC = "评估信创迁移可行性与风险的专题技能"


_P2_CONTENT = json.dumps({
    "assessment_report": {"feasibility": "可行"},
    "risk_list": [{"title": "syscall 不兼容", "risk_level": "L4", "source": "p1", "basis": "x"}],
    "blocker_list": [], "uncertainty_list": [], "validation_gap_list": [], "resource_needs": [],
})
_P3_STAGE_PLAN = json.dumps({"objective": "迁移", "scope": ["a"], "out_of_scope": ["b"],
                             "risk_level": "L2", "gate_policy": {}, "completion_criteria": ["c"]})
_P3_TASK_PLANS = json.dumps({"batch_objective": "批次", "task_plans": [
    {"title": "t1", "objective": "o1", "risk_level": "L2", "scope": ["s"]}]})
_P3_EDGES = json.dumps({"edges": []})


class _CapturingGateway:
    """Records every system prompt so the test can assert the assembled context
    (governance + Skill metadata) reached the model — and that the full SKILL.md
    body did NOT."""

    def __init__(self):
        self.system_prompts: list[str] = []

    def get_status(self):
        return SimpleNamespace(overall_status="available")

    async def call(self, *, messages, **kwargs):
        sys = messages[0]["content"] if messages else ""
        self.system_prompts.append(sys)
        if "Stage Plan" in sys and "拆解" not in sys:
            content = _P3_STAGE_PLAN
        elif "拆解出一批任务级 Task Plan" in sys:
            content = _P3_TASK_PLANS
        elif "TaskGraph 的边" in sys:
            content = _P3_EDGES
        else:
            content = _P2_CONTENT
        return {"status": "completed", "content": content, "model": "fake-model"}


def _aet():
    return aet_mod.AETService(services=None)


def _mk_project(client) -> str:
    return client.post("/api/projects", json={"name": "P1C", "source_type": "manual"}).json()["data"]["project_id"]


def _seed_real_skill(category: str, tmp: str):
    """Seed a SkillDefinition backed by a real SKILL.md (frontmatter + body) so
    resolve_bindings loads its metadata + body."""
    from app.core.database import get_session
    from app.models.skill_definition import SkillDefinition, SkillSeries, SkillCategory, SkillStatus
    import uuid

    skill_dir = Path(tmp) / f"skill-{category}"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: P-{category}-assess\ndescription: {_SKILL_DESC}\n---\n\n{_SKILL_BODY}",
        encoding="utf-8")
    db = get_session()
    try:
        db.add(SkillDefinition(
            skill_id=str(uuid.uuid4()), name=f"P-{category}-assess",
            series=SkillSeries.P, category=SkillCategory(category),
            status=SkillStatus.active, enabled=True,
            description=_SKILL_DESC, directory_path=str(skill_dir) + "/"))
        db.commit()
    finally:
        db.close()


async def test_p2_handler_uses_context_and_skill_metadata(client):
    """RealP2Handler.execute assembles context and the system prompt carries Skill
    METADATA (name+description), not the full SKILL.md body (progressive disclosure)."""
    pid = _mk_project(client)
    with tempfile.TemporaryDirectory() as tmp:
        _seed_real_skill("p2", tmp)
        gw = _CapturingGateway()
        svc = AssessmentService(gateway=gw, aet_service=_aet())
        res = await RealP2Handler(assessment_service=svc).execute(
            {"project_id": pid, "run_id": "r1", "user_goal": "迁移"})

    assert res["status"] == "completed"
    # P1-C provenance: context_refs (assembled C0-C6 layers) + skill_refs non-empty
    assert res["context_refs"], "P2 result must carry assembled context_refs"
    assert res["skill_refs"], "P2 result must carry skill_refs"

    sys = gw.system_prompts[0]
    # assembled context reached the model (C0 governance text present)
    assert "治理" in sys or "ModelGateway" in sys, "assembled C0-C6 context not in system prompt"
    # Skill METADATA present (description), full body NOT (progressive disclosure)
    assert _SKILL_DESC in sys, "Skill metadata (description) must be in the system prompt"
    assert "SECRETBODYMARKER" not in sys, "full SKILL.md body must NOT be dumped into the prompt"
    # domain output contract preserved (real products)
    assert "assessment_report" in sys


async def test_p3_handler_uses_context_and_skill_metadata(client):
    """RealP3Handler.execute threads the assembled context/system prompt into all
    three planning sub-calls; products carry context_refs/skill_refs."""
    pid = _mk_project(client)
    with tempfile.TemporaryDirectory() as tmp:
        _seed_real_skill("p3", tmp)
        gw = _CapturingGateway()
        svc = PlanningService(gateway=gw, aet_service=_aet())
        res = await RealP3Handler(planning_service=svc).execute(
            {"project_id": pid, "run_id": "r1", "user_goal": "迁移"})

    assert res["status"] == "completed"
    assert res["context_refs"] and res["skill_refs"]
    # all three sub-calls got the assembled context (metadata, not body)
    assert len(gw.system_prompts) == 3
    for sys in gw.system_prompts:
        assert _SKILL_DESC in sys
        assert "SECRETBODYMARKER" not in sys
