"""R20-2 scenario 一等公民 —— 断链接通 (B-R20-SCENARIO-NOT-WIRED) 判据固化.

本文件把施工计划 §10 S-9 的 T-1~T-9 落为可复跑测试。断言一律打在真实产出上（真实
assembly_trace 字段 / 真实渲染文本 / 真实库结构），不断言"函数可调用"。

覆盖范围（对齐 R20-2-04 / R20-2-05 / R20-2-06）：
  T-1  Project.scenario 列为 String，非 SAEnum/Enum
  T-2  15 处断链调用点：功能性验证 assemble_context 在 project_dict 到位时 p0~p6 全阶段
       scenario_source == "project_dict"；并用真实源码文本钉住每个调用点确已传 project=
  T-3  external_context_builder._to_dict 自动携带 scenario（防日后静默回退为显式白名单）
  T-4  :2378/:2757 的 system_prompt 恒空缺陷已消除（P5/P6 advisory 收到真实非空 system_prompt
       且含 scenario_line）
  T-5  未选场景（scenario=None）：诚实标注、C1 不含"信创"
  T-6  自定义场景（目录不存在）：回落 _generic，fallback.code = scenario-pack-not-found
  T-7  非法形状（../../etc）：fallback.code = scenario-id-invalid，不抛异常
  T-8  P1 选型：system_content 真实含场景包目标态词表片段（fake gateway 捕获 messages）
  T-9  无场景值分派面：本轮新改的 6 个文件无 `scenario ==` 分支（J-3 的范围收窄测试化）
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlalchemy as sa

BACKEND_APP = Path("/home/king/rebuild/backend/app")

R20_2_TOUCHED_FILES = [
    "graph/stage_handlers.py",
    "services/work_agent.py",
    "services/node_loop.py",
    "services/agent_loop.py",
    "api/routes_workspace.py",
    "services/project_service.py",
    "services/tech_selection_service.py",
    "services/validation_agent.py",
    "services/context_layers.py",
]


# ── T-1: scenario 列为 String，非 Enum ──────────────────────────────────────

def test_project_scenario_column_is_free_text_not_enum():
    from app.models.project import Project
    col_type = Project.__table__.c.scenario.type
    assert isinstance(col_type, sa.String)
    assert not isinstance(col_type, sa.Enum)
    assert col_type.length == 64


def test_project_scenario_schema_is_optional_str_not_literal():
    """J-2 正向断言②：schemas/project.py 的 scenario 字段须为 Optional[str]，非 Literal。"""
    from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse
    for cls in (ProjectCreate, ProjectUpdate, ProjectResponse):
        field = cls.model_fields["scenario"]
        assert field.annotation in (str, type(None)) or "str" in str(field.annotation)
        assert "Literal" not in str(field.annotation)


# ── T-2: 断链已接通 —— assemble_context 在 project_dict 到位时全阶段 project_dict ──

def test_build_project_context_dict_returns_real_six_keys(isolated_data, client):
    from app.services.project_service import ProjectService
    resp = client.post("/api/projects", json={
        "name": "R20-2-wiring-test", "source_type": "manual", "scenario": "modernization",
    })
    assert resp.status_code == 200
    pid = resp.json()["data"]["project_id"]

    d = ProjectService.build_project_context_dict(pid)
    assert d is not None
    for key in ("name", "source_type", "workspace_status", "onboarding_done",
               "coding_agent_ref", "scenario"):
        assert key in d, f"缺键 {key}"
    assert d["scenario"] == "modernization"


def test_assemble_context_scenario_source_project_dict_across_all_stages(isolated_data, client):
    """T-2 核心判据：15 处断链此前的现状是 scenario_source == 'absent'（§4.8 A/B 组）；
    接通后，只要调用方把 build_project_context_dict() 的结果传进 project=，p0~p6 全阶段
    都必须是 'project_dict'（不是只有某几个阶段通）。"""
    from app.services.context_assembler import assemble_context
    from app.services.project_service import ProjectService

    resp = client.post("/api/projects", json={
        "name": "R20-2-wiring-allstage", "source_type": "manual", "scenario": "modernization",
    })
    pid = resp.json()["data"]["project_id"]
    proj = ProjectService.build_project_context_dict(pid)

    for stage in ("p0", "p1", "p2", "p3", "p4", "p5", "p6"):
        ctx = assemble_context(pid, stage, project=proj, node_state={"node_task": "probe"})
        trace = ctx["assembly_trace"]
        assert trace["scenario_source"] == "project_dict", f"stage={stage} 未接通场景"
        assert trace["scenario"] == "modernization"
        assert trace["scenario_status"] == "ok"
        assert trace["scenario_tier"] == "typical"


def test_all_15_call_sites_pass_project_kwarg_in_real_source():
    """反证复核（非人工核对）：15 处此前不传/漏传 scenario 的调用点，源码文本中确已出现
    project= 实参（§5.1 逐点坐标）。这不是"函数可调用"式断言 —— 是钉住真实 diff 落地。"""
    sh = (BACKEND_APP / "graph" / "stage_handlers.py").read_text(encoding="utf-8")
    # 11 处 stage_handlers.py 调用点（assemble_context/build_system_prompt 各自 or 共享 project=）
    assert sh.count("project=_proj") + sh.count("project=_build_project_context(project_id)") >= 11

    wa = (BACKEND_APP / "services" / "work_agent.py").read_text(encoding="utf-8")
    assert "ProjectService.build_project_context_dict(self.project_id)" in wa

    nl = (BACKEND_APP / "services" / "node_loop.py").read_text(encoding="utf-8")
    assert "ProjectService.build_project_context_dict(spec.project_id)" in nl

    al = (BACKEND_APP / "services" / "agent_loop.py").read_text(encoding="utf-8")
    assert "ProjectService.build_project_context_dict(project_id)" in al

    rw = (BACKEND_APP / "api" / "routes_workspace.py").read_text(encoding="utf-8")
    assert '"scenario": project.scenario,' in rw


# ── T-3: external_context_builder 自动携带 scenario（钉住"自动"，防日后静默回退）──

def test_external_context_builder_to_dict_carries_scenario_automatically(isolated_data, client):
    from app.services.external_context_builder import _to_dict
    from app.core.database import get_session
    from app.models.project import Project

    resp = client.post("/api/projects", json={
        "name": "R20-2-ecb-auto", "source_type": "manual", "scenario": "porting",
    })
    pid = resp.json()["data"]["project_id"]
    db = get_session()
    try:
        p = db.get(Project, pid)
        d = _to_dict(p)
    finally:
        db.close()
    assert d is not None and "scenario" in d, (
        "_to_dict 不再自动携带 scenario —— 若已改为显式白名单，须补回 scenario 键"
    )
    assert d["scenario"] == "porting"


# ── T-4: :2378/:2757 死读法已消除，P5/P6 advisory 真收到非空 system_prompt ──────

def test_stage_handlers_no_longer_reads_dead_system_prompt_key():
    sh = (BACKEND_APP / "graph" / "stage_handlers.py").read_text(encoding="utf-8")
    assert 'pkg.get("system_prompt"' not in sh
    assert 'pkg_ctx.get("system_prompt"' not in sh
    # 修复后必须改用公开 build_system_prompt API（§7.6 方案 A），而不是绕开
    # context_assembler 自行拼接（X-4-5 单一装配入口）。
    assert sh.count("build_system_prompt(") >= 6  # 4 处既有 + P5 + P6 新增


def test_p5_p6_advisory_receives_nonempty_system_prompt_with_scenario_line(isolated_data, client):
    """this directly reproduces the fixed code path: build_system_prompt(project_id, "p5"/"p6",
    project=..., ...) must return non-empty content containing the scenario identity sentence —
    the exact defect §4.8/§5.7 proved as 'has system_prompt key? False' before this fix."""
    from app.services.context_assembler import build_system_prompt
    from app.services.project_service import ProjectService

    resp = client.post("/api/projects", json={
        "name": "R20-2-p5p6-fix", "source_type": "manual", "scenario": "xinchuang_switch",
    })
    pid = resp.json()["data"]["project_id"]
    proj = ProjectService.build_project_context_dict(pid)

    for stage in ("p5", "p6"):
        sp = build_system_prompt(pid, stage, project=proj,
                                 node_state={"node_task": "probe"}, skill_disclosure="metadata")
        assert sp, f"{stage} system_prompt 仍为空（缺陷未修复）"
        assert "信创切换" in sp, f"{stage} system_prompt 未含 scenario_line 身份句"


# ── T-5: 未选场景 —— 诚实标注，不静默套信创 ────────────────────────────────

def test_no_scenario_selected_is_honest_and_scenario_free(isolated_data, client):
    from app.services.context_assembler import assemble_context
    from app.services.project_service import ProjectService

    resp = client.post("/api/projects", json={"name": "R20-2-no-scenario", "source_type": "manual"})
    pid = resp.json()["data"]["project_id"]
    proj = ProjectService.build_project_context_dict(pid)
    assert proj["scenario"] is None

    ctx = assemble_context(pid, "p2", project=proj, node_state={"node_task": "probe"})
    trace = ctx["assembly_trace"]
    assert trace["scenario_status"] == "scenario-not-selected"
    assert trace["scenario"] == "_generic"
    c1_content = ctx["layers"]["C1"]["content"]
    assert "信创" not in c1_content
    assert "请勿假设" in c1_content or "未提供" in c1_content


# ── T-6/T-7: 自定义场景 / 非法形状 —— 诚实回落 _generic，绝不误判为典型包 ──────

def test_custom_scenario_directory_not_found_falls_back_generic():
    from app.services.scenario_loader import resolve_scenario_pack, SC_PACK_NOT_FOUND
    pack = resolve_scenario_pack("cloud_native_x_does_not_exist")
    assert pack["scenario_id"] == "_generic"
    assert pack["tier"] != "typical"
    assert pack["fallback"]["code"] == SC_PACK_NOT_FOUND


def test_invalid_scenario_shape_falls_back_without_raising():
    from app.services.scenario_loader import resolve_scenario_pack, SC_ID_INVALID
    pack = resolve_scenario_pack("../../etc")
    assert pack["scenario_id"] == "_generic"
    assert pack["fallback"]["code"] == SC_ID_INVALID


def test_create_project_with_path_traversal_scenario_does_not_500(isolated_data, client):
    """负向必做（S-4③）：恶意 scenario 值不得使创建接口 500；把门在消费侧。"""
    resp = client.post("/api/projects", json={
        "name": "R20-2-malicious", "source_type": "manual", "scenario": "../../etc",
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["scenario"] == "../../etc"


# ── T-8: P1 选型 —— system_content 真实含场景包目标态词表片段 ─────────────────

class _CapturingGateway:
    """fake gateway：readiness 放行，call() 捕获真实发送的 messages（system 段）。"""

    def __init__(self):
        self.captured_system_content: str | None = None

    def stage_model_readiness(self, **kwargs):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, *, messages=None, **kw):
        for m in messages or []:
            if m.get("role") == "system":
                self.captured_system_content = m.get("content")
        return {"status": "completed", "model": "fake",
                "content": '{"target_language":{"recommendation":"x","reasoning":"y","alternatives":[]}}'}


async def test_p1_tech_selection_system_content_includes_real_scenario_pack_text(isolated_data):
    from app.services.tech_selection_service import TechSelectionService
    gw = _CapturingGateway()
    svc = TechSelectionService(gateway=gw)
    await svc.select(
        "ts-scenario-probe",
        identification={"tech_stack": {"primary_language": "C#"}},
        upstream={}, migration_target=None, run_id="r1",
        scenario="xinchuang_switch",
    )
    assert gw.captured_system_content, "select() 未真实调用模型（system_content 未捕获到）"
    assert "信创切换" in gw.captured_system_content
    assert "达梦" in gw.captured_system_content or "人大金仓" in gw.captured_system_content


async def test_p1_tech_selection_default_scenario_none_is_backward_compatible(isolated_data):
    """既有调用零改动仍合法：不传 scenario 时走 None → _generic 诚实回落，不报错。"""
    from app.services.tech_selection_service import TechSelectionService
    gw = _CapturingGateway()
    svc = TechSelectionService(gateway=gw)
    r = await svc.select(
        "ts-no-scenario", identification={"tech_stack": {}}, upstream={}, run_id="r1",
    )
    assert r.status == "completed"
    assert "信创" not in gw.captured_system_content


# ── T-9: 无场景值分派面（本轮新改文件的范围收窄测试化，J-3 的补充）────────────

def test_no_scenario_value_branching_in_r20_2_touched_files():
    pattern = re.compile(r"if[^\n]*scenario[^\n]*==|elif[^\n]*scenario", re.IGNORECASE)
    for rel in R20_2_TOUCHED_FILES:
        text = (BACKEND_APP / rel).read_text(encoding="utf-8")
        m = pattern.search(text)
        assert not m, f"{rel} 出现以场景值为条件的分支：{m.group(0) if m else ''}"
