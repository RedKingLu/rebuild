"""R17.3-6 WP-2 批 A — P1 样例完整闭环真实单测（非 mock，D-097）。

覆盖：
  1. WorkAgent 动态工作计划（随真实项目事实变化）+ Stage Skill 正文加载 +
     FullStackProfiler 作为确定性 Tool 被调 + fact-evidence map（绑定真实 refs + AET 登记）。
  2. 独立 ValidationAgent（独立 agent_id + read_from_disk_only）验收通过。
  3. request_changes 打回实证：ValidationAgent 判不合格（缺 p2_input_manifest）→
     StageLoop rework 轮 → WorkAgent 携反馈重跑 → 合格 → 建用户 Gate。
  4. make_work_node P1 端到端：plan_presentation 挂动态计划 + promotion Gate 挂
     Agent 侧审核材料（gate_brief/validation/claim_evidence_map），Gate Brief 真实。

不使用 mock：WorkAgent/ValidationAgent/StageLoop/AcceptanceService/FullStackProfiler/AET
全为真实组件；rework 演示用一个真实"退化 profiler"（首轮删除 p2 清单，模拟真实部分写失败）。
"""

import json

import pytest

from app.graph import nodes
from app.graph.checkpoint import reset_checkpointer_for_test
from app.graph.runtime import get_flow_runtime, reset_flow_runtime_for_test
from app.graph.stage_handlers import RealP0Handler, RealP1Handler
from app.services import workspace_service
from app.services.work_agent import WorkAgent
from app.services.validation_agent import ValidationAgent


def _seed_source(project_id: str) -> None:
    workspace_service.init_workspace(project_id)
    src = workspace_service.workspace_path(project_id) / "source"
    (src / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"react": "^18", "next": "^14"}}),
        encoding="utf-8")
    (src / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (src / "index.tsx").write_text("export default 1\n", encoding="utf-8")


def test_work_agent_dynamic_plan_reflects_real_facts(isolated_data, monkeypatch):
    """WorkAgent 动态工作计划基于真实项目事实（file_count/探测栈），非静态模板。"""
    pid = "wp2-plan"
    _seed_source(pid)
    wa = WorkAgent("p1", pid, run_id="r1")
    plan_ref = wa.build_work_plan({"source_type": "manual"})
    plan = json.loads((workspace_service.workspace_path(pid) / plan_ref).read_text("utf-8"))
    assert plan["generated_by"] == "work_agent"
    based = plan["based_on"]
    assert based["file_count"] == 3, "计划基于真实文件数"
    # detected stack reflects real extensions (.py/.tsx/.json) — non-static
    assert any("Python" in s or "TSX" in s or "JavaScript" in s for s in based["detected_stack"])
    assert "package.json" in based["build_files"]
    # goal text embeds real facts (随项目变化)
    assert "3 源文件" in plan["goal"]


async def test_work_agent_execute_produces_fact_evidence_and_skill_body(isolated_data):
    """WorkAgent.execute：调 profiler Tool + fact-evidence map + AET 登记 + Skill 正文加载。"""
    pid = "wp2-exec"
    _seed_source(pid)
    wa = WorkAgent("p1", pid, run_id="r1")
    result = await wa.execute({"source_type": "manual"})

    assert result["status"] == "completed"
    assert result["reviewer"] == "work_agent"
    # FullStackProfiler 作为确定性 Tool 被调
    assert any(tc["tool"] == "full_stack_profiler.profile" and tc["status"] == "ok"
               for tc in result["tool_calls"])
    # Stage Skill 正文加载（P1 skill body loaded） — 依赖 seed P1 skill 有 SKILL.md
    # 注：测试 seed 的 P-Skill 无真实 SKILL.md，body 可能为空；此处只断言字段存在且诚实。
    assert "body_loaded" in result["skill_loaded"]

    # fact-evidence map 落盘 + 每条 fact 经 AET 登记为可查询 Evidence
    cem_ref = result["claim_evidence_map_ref"]
    cem = json.loads((workspace_service.workspace_path(pid) / cem_ref).read_text("utf-8"))
    assert cem["map_type"] == "fact_evidence"
    assert len(cem["entries"]) >= 3
    for e in cem["entries"]:
        assert e["bindings"]["artifact_refs"], "每条 fact 绑定 artifact"
        assert e["bindings"]["sha256"], "每条 fact 绑定真实 sha256"
    # AET 可查询
    from app.services.aet_service import AETService
    evs = AETService(None).list_evidence(pid, stage="p1")
    assert any(ev.get("type") == "p1_fact" for ev in evs), "fact 已登记为可查询 Evidence"
    assert result["evidence_refs"], "返回登记的 evidence ids"


async def test_work_agent_loads_stage_skill_body(isolated_data):
    """Stage Skill 正文加载执行（include_body=True/skill_disclosure=full）：
    当 P1 有真实 SKILL.md 时，WorkAgent 上下文装配加载其正文（body_loaded=True）。"""
    import tempfile
    from pathlib import Path
    from app.core.database import get_session
    from app.models.skill_definition import SkillDefinition, SkillSeries, SkillCategory, SkillStatus
    import uuid

    pid = "wp2-skillbody"
    _seed_source(pid)
    tmpd = tempfile.mkdtemp()
    skill_dir = Path(tmpd) / "P-p1-doc"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: P-p1-doc\ndescription: P1 建档专题技能\n---\n\nP1 建档正文步骤 " * 40,
        encoding="utf-8")
    db = get_session()
    try:
        db.add(SkillDefinition(
            skill_id=str(uuid.uuid4()), name="P-p1-doc", series=SkillSeries.P,
            category=SkillCategory.p1, status=SkillStatus.active, enabled=True,
            description="P1 建档专题技能", directory_path=str(skill_dir) + "/"))
        db.commit()
    finally:
        db.close()

    wa = WorkAgent("p1", pid, run_id="r1")
    result = await wa.execute({"source_type": "manual"})
    assert result["skill_loaded"]["body_loaded"] is True, "P1 Stage Skill 正文应加载"
    assert result["skill_loaded"]["chars"] > 0


async def test_validation_agent_independent_pass(isolated_data):
    """独立 ValidationAgent 验收通过：独立 agent_id + read_from_disk_only。"""
    pid = "wp2-val"
    _seed_source(pid)
    wa = WorkAgent("p1", pid, run_id="r1")
    result = await wa.execute({"source_type": "manual"})

    va = ValidationAgent("p1", pid, run_id="r1")
    rr = va.validate(result)
    assert rr.passed, f"验收应通过, issues={rr.issues}"
    vres = va.last_result
    assert vres.read_from_disk_only is True
    assert vres.verdict in ("accepted", "accepted_with_warning")
    # 独立身份可追溯（seed acceptance agent_id）
    assert vres.agent_id, "ValidationAgent 复用独立 acceptance 身份 (Q-WP2-1)"
    # claim/fact-evidence 逐条落盘可解析校验
    cev = vres.claim_evidence_verification
    assert cev["total"] >= 3 and cev["resolved"] == cev["total"]
    # 独立验收产物落盘
    vfile = workspace_service.workspace_path(pid) / "artifacts" / "p1_validation.json"
    assert vfile.exists()
    saved = json.loads(vfile.read_text("utf-8"))
    assert saved["reviewer"] == "validation_agent"


async def test_validation_agent_rework_then_pass(isolated_data):
    """request_changes 打回实证：首轮缺 p2_input_manifest → rework → 重跑 → 通过。

    真实退化 profiler（首次 profile 后删除 p2_input_manifest.json，模拟真实部分写失败），
    第二次正常。全链路真实：StageLoop → ReviewPass → WorkAgent → ValidationAgent。
    """
    from app.graph.stage_loop import StageLoop
    pid = "wp2-rework"
    _seed_source(pid)

    class _FlakyProfiler:
        """真实 FullStackProfiler 包装：首轮删除 p2 清单（真实退化场景），第二轮完整。"""
        def __init__(self):
            from app.services.full_stack_profiler import FullStackProfiler
            self._real = FullStackProfiler()
            self.calls = 0

        def profile(self, project_id):
            r = self._real.profile(project_id)
            self.calls += 1
            if self.calls == 1:
                mani = workspace_service.workspace_path(project_id) / "artifacts" / "p2_input_manifest.json"
                if mani.exists():
                    mani.unlink()  # 真实退化：p2 清单缺失
            return r

    flaky = _FlakyProfiler()
    wa = WorkAgent("p1", pid, run_id="r1", profiler=flaky)
    va = ValidationAgent("p1", pid, run_id="r1")

    def _review(work_result):
        rr = va.validate(work_result)
        if not rr.passed:
            wa.set_rework_feedback({"issues": rr.issues, "recommendations": rr.recommendations})
        return rr

    loop = StageLoop(pid, "p1", max_rounds=2, run_id="r1")
    res = await loop.run(
        goal="P1 建档", acceptance_criteria=["占位"], planned_actions=["占位"],
        execute_fn=lambda: wa.execute({"source_type": "manual"}),
        review_fn=_review,
    )
    assert flaky.calls == 2, "profiler 被调用两轮（首轮退化 + rework 重跑）"
    assert res.passed, "rework 后第二轮应通过"
    # 施工报告记录了 rework 轮次（可追溯）
    rounds = res.rounds
    assert len(rounds) == 2
    assert rounds[0]["status"] == "retry", "首轮验收未通过（rework）"
    assert rounds[1]["status"] == "passed", "第二轮通过"
    # 首轮 issue 含缺 p2 清单
    first_issues = json.dumps(rounds[0]["issues"], ensure_ascii=False)
    assert "p2_input_manifest" in first_issues


async def test_p1_plan_presentation_carries_dynamic_plan(isolated_data):
    """make_work_node P1 plan_only 分支：plan_presentation gate 挂 WorkAgent 动态工作计划。

    直接驱动 p1 work 节点（plan_approved 未置），验证 plan_presentation 路径接线
    （多阶段自然流中 plan_approved 跨阶段沿用属既有平台行为，见施工记录 evidence_gap）。
    """
    pid = "wp2-plan-node"
    _seed_source(pid)

    class RecordingGate:
        def __init__(self):
            self.created = []
        def create(self, *, project_id, run_id, stage, artifact_refs,
                   gate_type="stage_promotion", metadata=None):
            gid = f"gate-{stage}-{gate_type}-{len(self.created)}"
            self.created.append({"gid": gid, "stage": stage, "gate_type": gate_type,
                                 "artifact_refs": list(artifact_refs)})
            return gid
        def decide(self, *, gate_id, decision):
            pass

    nodes.clear_handlers()
    nodes.register_handler("p1", RealP1Handler())
    gb = RecordingGate()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    try:
        work_node = nodes.make_work_node("p1")
        state = {"project_id": pid, "run_id": "run-plan", "source_type": "manual",
                 "execution_mode": "manual", "stage_status": {"p1": "in_progress"}}
        upd = await work_node(state)
        assert upd["stage_status"]["p1"] == "waiting_plan_presentation"
        plan_gate = gb.created[0]
        assert plan_gate["gate_type"] == "plan_presentation"
        assert any(r.endswith("p1_work_plan.json") for r in plan_gate["artifact_refs"]), \
            "plan_presentation gate 挂动态工作计划"
        plan = json.loads((workspace_service.workspace_path(pid) / "artifacts" /
                           "p1_work_plan.json").read_text("utf-8"))
        assert plan["generated_by"] == "work_agent"
        assert plan["based_on"]["file_count"] == 3
    finally:
        nodes.clear_handlers()
        nodes.set_gate_backend(None)


async def test_p1_end_to_end_agent_workflow_in_graph(isolated_data, monkeypatch):
    """make_work_node P1 端到端：执行 → 独立验收 → promotion Gate 挂 Agent 侧审核材料
    （work_plan/gate_brief/validation/claim_evidence_map），Gate Brief 内容真实。"""
    import tempfile
    from pathlib import Path
    ckpt = Path(tempfile.mkdtemp()) / "ckpt.sqlite"
    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path", lambda: ckpt)

    pid = "wp2-e2e"
    _seed_source(pid)

    class RecordingGate:
        def __init__(self):
            self.created = []
        def create(self, *, project_id, run_id, stage, artifact_refs,
                   gate_type="stage_promotion", metadata=None):
            gid = f"gate-{stage}-{gate_type}-{len(self.created)}"
            self.created.append({"gid": gid, "stage": stage, "gate_type": gate_type,
                                 "artifact_refs": list(artifact_refs)})
            return gid
        def decide(self, *, gate_id, decision):
            pass

    await reset_checkpointer_for_test()
    reset_flow_runtime_for_test()
    nodes.clear_handlers()
    nodes.register_handler("p0", RealP0Handler())
    nodes.register_handler("p1", RealP1Handler())
    gb = RecordingGate()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    try:
        rt = get_flow_runtime()
        init = {"run_id": "run-e2e", "project_id": pid, "run_goal": "e2e",
                "run_status": "running", "source_type": "manual",
                "stage_status": {"p0": "in_progress"}}
        s = await rt.start("run-e2e", init)
        assert "__interrupt__" in s
        # walk gates until we reach P1 promotion (approve each)
        p1_promo = None
        for _ in range(8):
            s = await rt.resume("run-e2e", "approve")
            for c in gb.created:
                if c["stage"] == "p1" and c["gate_type"] == "stage_promotion":
                    p1_promo = c
            if p1_promo:
                break
            if "__interrupt__" not in s:
                break

        assert p1_promo is not None, "P1 promotion gate 应创建"
        refs = p1_promo["artifact_refs"]
        assert any(r.endswith("p1_work_plan.json") for r in refs), "promotion 挂动态工作计划"
        assert any(r.endswith("p1_gate_brief.json") for r in refs), "promotion 挂 Gate Brief"
        assert any(r.endswith("p1_validation.json") for r in refs), "promotion 挂独立验收结论"
        assert any(r.endswith("p1_claim_evidence_map.json") for r in refs), "promotion 挂 fact-evidence map"

        # Gate Brief 内容真实（含 validation verdict）
        gb_ref = next(r for r in refs if r.endswith("p1_gate_brief.json"))
        brief = json.loads((workspace_service.workspace_path(pid) / gb_ref).read_text("utf-8"))
        assert brief["what_happened"], "Gate Brief what_happened 非空"
        assert brief["validation_verdict"]["passed"] is True
        assert brief["claim_evidence_summary"]["total"] >= 3
    finally:
        await reset_checkpointer_for_test()
        reset_flow_runtime_for_test()
        nodes.clear_handlers()
        nodes.set_gate_backend(None)
