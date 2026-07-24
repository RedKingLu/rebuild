"""R17.5-P4-FIX 批3 (D-109) — P1→P2 技术选型红线机制真实单测（非 mock，D-097）。

覆盖：
  ① TechSelectionService 生成（fake Key-present gateway → 结构化选型 + reasoning）+ 无 Key blocked；
  ② RealP1Handler._generate_tech_selection 写 artifacts/p1/tech_selection.json（proposed/诚实 blocked）；
  ③ GateService.decide：P1 approve → 落 project.tech_selection 红线（提案 + 用户 override 两路径）；
  ④ P4ExecutionWorker._build_gen_system 注入红线块；
  ⑤ ValidationAgent：_p4_route_summary 含红线 + P2/P3 偏离 advisory 发声。

fake gateway 范式同 test_wp2b_all_stages_agent_workflow._IntakeGateway（无 call_stream → 单次 call
兜底路径，run_stage_tool_loop 走 fallback）。
"""

import json

from app.services import workspace_service


# ── fake gateway（Key-present，无 call_stream → 走 run_stage_tool_loop 单次 call 兜底）──
class _SelectionGateway:
    def __init__(self, selection_json: str):
        self._sel = selection_json

    def stage_model_readiness(self, **kwargs):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, *, messages=None, **kw):
        return {"status": "completed", "model": "fake-kimi", "content": self._sel}


class _NoKeyGateway:
    def stage_model_readiness(self, **kwargs):
        return {"available": False, "reason": "no_model_key",
                "attempted_chain": ["m1"], "user_actions": ["配置 Key"]}

    async def call(self, **kw):  # pragma: no cover — readiness 拦截，不应到达
        raise AssertionError("blocked path must not call the model")


_MICROOA_SELECTION = json.dumps({
    "target_language": {"recommendation": "C# (.NET 8)",
                        "reasoning": "源为 .NET Framework WebForms，保留 C# 迁移成本最低",
                        "alternatives": [{"option": "Java", "reasoning": "重写成本高"}]},
    "runtime": {"recommendation": ".NET 8 (ARM64/openEuler)",
                "reasoning": ".NET 8 官方支持 ARM64 + openEuler，契合鲲鹏/openEuler 目标",
                "alternatives": []},
    "database": {"recommendation": "达梦 DM8",
                 "reasoning": "源用 SqlClient 连 SQL Server，信创目标环境替换为达梦",
                 "alternatives": [{"option": "openGauss", "reasoning": "生态兼容备选"}]},
    "web_framework": {"recommendation": "ASP.NET Core MVC",
                      "reasoning": "替代 WebForms，跨平台可在 openEuler 运行",
                      "alternatives": []},
    "middleware_replacements": [
        {"component": "IIS", "from": "IIS", "recommendation": "Kestrel + Nginx",
         "reasoning": "openEuler 无 IIS", "alternatives": []}],
    "key_arch_decisions": [
        {"decision": "会话存储", "recommendation": "外置分布式会话",
         "reasoning": "WebForms ViewState 有状态 → 无状态化", "alternatives": []}],
    "overall_rationale": "服从鲲鹏/openEuler/达梦目标环境，贴合 .NET/WebForms/SqlClient 源事实",
    "open_questions": ["是否需保留 WCF 服务契约？"],
}, ensure_ascii=False)

_IDENT = {
    "tech_stack": {"primary_language": "C#", "frameworks": [{"framework": "ASP.NET WebForms"}]},
    "dependency_draft": {"dependencies": [{"name": "System.Data.SqlClient"}], "total": 1},
    "entry_points": [{"path": "Global.asax", "kind": "aspnet_entry"}],
    "config_inventory": {"configs": [{"path": "Web.config", "config_type": "aspnet"}]},
    "infra_clues": {"infra": [{"infra_type": "database", "evidence": "SqlClient → SQL Server"}]},
}
_UPSTREAM = {"primary_language": "C#", "migration_target":
             {"cpu_arch": "kunpeng", "cpu_arch_label": "鲲鹏", "os": "openeuler", "os_label": "openEuler"}}


# ── ① TechSelectionService 生成 + blocked ────────────────────────────────
async def test_tech_selection_service_generates_structured_selection(isolated_data):
    from app.services.tech_selection_service import TechSelectionService, TECH_SELECTION_KEYS
    svc = TechSelectionService(gateway=_SelectionGateway(_MICROOA_SELECTION))
    r = await svc.select("ts-gen", identification=_IDENT, upstream=_UPSTREAM,
                         migration_target=_UPSTREAM["migration_target"], run_id="r1")
    assert r.status == "completed"
    for k in TECH_SELECTION_KEYS:
        assert k in r.selection, f"选型缺锚点键 {k}"
    # 接地 + 带理由（非空泛模板）
    assert "达梦" in r.selection["database"]["recommendation"]
    assert r.selection["database"]["reasoning"]
    assert r.selection["target_language"]["alternatives"]


async def test_tech_selection_service_blocked_without_key(isolated_data):
    from app.services.tech_selection_service import TechSelectionService
    svc = TechSelectionService(gateway=_NoKeyGateway())
    r = await svc.select("ts-blocked", identification=_IDENT, upstream=_UPSTREAM,
                         migration_target=_UPSTREAM["migration_target"])
    assert r.status == "blocked"
    assert "no_model_key" in r.reason
    assert not r.selection  # 不伪造/不降级规则选型


# ── ② RealP1Handler._generate_tech_selection 写产物 ──────────────────────
async def test_p1_handler_writes_tech_selection_artifact(isolated_data):
    from app.graph.stage_handlers import RealP1Handler
    from app.services.tech_selection_service import TechSelectionService
    pid = "ts-p1"
    workspace_service.init_workspace(pid)
    (workspace_service.workspace_path(pid) / "artifacts" / "p1").mkdir(parents=True, exist_ok=True)
    h = RealP1Handler(tech_selection_service=TechSelectionService(gateway=_SelectionGateway(_MICROOA_SELECTION)))
    await h._generate_tech_selection(pid, "r1", _IDENT, _UPSTREAM)
    doc = json.loads((workspace_service.workspace_path(pid) /
                      "artifacts" / "p1" / "tech_selection.json").read_text("utf-8"))
    assert doc["status"] == "proposed"
    assert doc["decision_status"] == "pending_p1_gate"
    assert doc["selection"]["database"]["recommendation"] == "达梦 DM8"
    assert doc["migration_target"]["os"] == "openeuler"


async def test_p1_handler_honest_blocked_selection_artifact(isolated_data):
    from app.graph.stage_handlers import RealP1Handler
    from app.services.tech_selection_service import TechSelectionService
    pid = "ts-p1-blocked"
    workspace_service.init_workspace(pid)
    (workspace_service.workspace_path(pid) / "artifacts" / "p1").mkdir(parents=True, exist_ok=True)
    h = RealP1Handler(tech_selection_service=TechSelectionService(gateway=_NoKeyGateway()))
    await h._generate_tech_selection(pid, "r1", _IDENT, _UPSTREAM)
    doc = json.loads((workspace_service.workspace_path(pid) /
                      "artifacts" / "p1" / "tech_selection.json").read_text("utf-8"))
    assert doc["status"] == "blocked"
    assert "selection" not in doc  # 诚实：未完成不落 selection


# ── ③ GateService.decide：P1 approve → 落 project.tech_selection 红线 ──────
def _make_project(client, name="D109"):
    return client.post("/api/projects", json={"name": name, "source_type": "manual"}).json()["data"]["project_id"]


def _write_proposal(pid, selection):
    d = workspace_service.workspace_path(pid) / "artifacts" / "p1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tech_selection.json").write_text(json.dumps(
        {"artifact_type": "tech_selection", "status": "proposed", "selection": selection},
        ensure_ascii=False), encoding="utf-8")


def test_gate_p1_approve_persists_tech_selection_from_proposal(client):
    from app.dependencies import get_services
    from app.schemas.gate import GateDecisionRequest
    pid = _make_project(client)
    _write_proposal(pid, json.loads(_MICROOA_SELECTION))
    svc = get_services()
    gate = svc.gate_service.create(project_id=pid, run_id="", stage="p1",
                                   gate_type="stage_promotion", artifact_refs=["artifacts/p1/tech_selection.json"])
    resp, _ = svc.gate_service.decide(gate.gate_id, GateDecisionRequest(decision="approve"),
                                      drive_promotion=False)
    assert resp.decision == "approve"
    proj = svc.project_service.get(pid)
    assert proj.tech_selection is not None
    assert proj.tech_selection["status"] == "approved"
    assert proj.tech_selection["source"] == "p1_proposal"
    assert proj.tech_selection["database"]["recommendation"] == "达梦 DM8"


def test_gate_p1_approve_persists_user_override(client):
    from app.dependencies import get_services
    from app.schemas.gate import GateDecisionRequest
    pid = _make_project(client)
    _write_proposal(pid, json.loads(_MICROOA_SELECTION))
    override = {"target_language": {"recommendation": "C# (.NET 8) [用户确认]", "reasoning": "u", "alternatives": []},
                "database": {"recommendation": "openGauss", "reasoning": "用户改选", "alternatives": []}}
    svc = get_services()
    gate = svc.gate_service.create(project_id=pid, run_id="", stage="p1", gate_type="stage_promotion")
    svc.gate_service.decide(gate.gate_id, GateDecisionRequest(decision="approve", tech_selection=override),
                            drive_promotion=False)
    proj = svc.project_service.get(pid)
    assert proj.tech_selection["source"] == "gate_override"
    assert proj.tech_selection["database"]["recommendation"] == "openGauss"


def test_gate_p2_approve_does_not_touch_tech_selection(client):
    """非 P1 gate approve 不落 tech_selection（红线只在 P1→P2 gate 裁决）。"""
    from app.dependencies import get_services
    from app.schemas.gate import GateDecisionRequest
    pid = _make_project(client)
    svc = get_services()
    gate = svc.gate_service.create(project_id=pid, run_id="", stage="p2", gate_type="stage_promotion")
    svc.gate_service.decide(gate.gate_id, GateDecisionRequest(decision="approve"), drive_promotion=False)
    proj = svc.project_service.get(pid)
    assert proj.tech_selection is None


# ── ④ P4ExecutionWorker 注入红线块 ───────────────────────────────────────
def test_p4_worker_injects_redline_block(client):
    from app.dependencies import get_services
    from app.services.p4_execution_worker import P4ExecutionWorker
    pid = _make_project(client)
    svc = get_services()
    svc.project_service.update(pid, tech_selection={
        "status": "approved",
        "target_language": {"recommendation": "C# (.NET 8)"},
        "database": {"recommendation": "达梦 DM8"}},
        migration_target={"os_label": "openEuler", "cpu_arch_label": "鲲鹏"})
    worker = P4ExecutionWorker(pid)
    sys_prompt = worker._build_gen_system()
    assert "项目红线" in sys_prompt
    assert "达梦 DM8" in sys_prompt
    assert "openEuler" in sys_prompt


def test_p4_worker_no_redline_when_absent(client):
    from app.services.p4_execution_worker import P4ExecutionWorker
    pid = _make_project(client)
    worker = P4ExecutionWorker(pid)
    assert worker._build_redline_block() == ""


# ── ⑤ ValidationAgent 红线注入 + 偏离 advisory ───────────────────────────
def test_validation_p4_route_summary_includes_redline(client):
    from app.dependencies import get_services
    from app.services.validation_agent import ValidationAgent
    pid = _make_project(client)
    get_services().project_service.update(pid, tech_selection={
        "target_language": {"recommendation": "C# (.NET 8)"},
        "database": {"recommendation": "达梦 DM8"}})
    va = ValidationAgent("p4", pid)
    summary = va._p4_route_summary()
    assert "项目红线" in summary and "达梦 DM8" in summary


def test_validation_p2_adherence_advisory_detects_deviation(client):
    from app.dependencies import get_services
    from app.services.validation_agent import ValidationAgent
    pid = _make_project(client)
    get_services().project_service.update(pid, tech_selection={
        "target_language": {"recommendation": "C# (.NET 8)"}})
    # P2 产物明显偏离：推荐迁到 Java/Spring（与选定 C# 冲突）
    d = workspace_service.workspace_path(pid) / "artifacts" / "p2"
    d.mkdir(parents=True, exist_ok=True)
    (d / "p2_assessment_report.json").write_text(json.dumps(
        {"report": {"target": "迁移到 Java + Spring Boot 重写"}}, ensure_ascii=False), encoding="utf-8")
    va = ValidationAgent("p2", pid)
    check, rec = va._tech_selection_adherence_advisory()
    assert check is not None and check["passed"] is False
    assert rec and "偏离" in rec


def test_validation_p2_adherence_advisory_ok_when_aligned(client):
    from app.dependencies import get_services
    from app.services.validation_agent import ValidationAgent
    pid = _make_project(client)
    get_services().project_service.update(pid, tech_selection={
        "target_language": {"recommendation": "C# (.NET 8)"}})
    d = workspace_service.workspace_path(pid) / "artifacts" / "p2"
    d.mkdir(parents=True, exist_ok=True)
    (d / "p2_assessment_report.json").write_text(json.dumps(
        {"report": {"target": "保留 .NET，迁移到 .NET 8 + ASP.NET Core"}}, ensure_ascii=False),
        encoding="utf-8")
    va = ValidationAgent("p2", pid)
    check, rec = va._tech_selection_adherence_advisory()
    assert check is not None and check["passed"] is True
    assert rec is None


def test_validation_adherence_none_without_redline(client):
    from app.services.validation_agent import ValidationAgent
    pid = _make_project(client)
    va = ValidationAgent("p2", pid)
    assert va._tech_selection_adherence_advisory() == (None, None)
