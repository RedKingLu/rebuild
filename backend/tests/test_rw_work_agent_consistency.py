"""返工修复批次 C — WorkAgent 产物一致性与文案（D-08 / D-09 / D-10）。

事实源：`产物/草稿/V26.2-总验收真跑-问题梳理与返工修复计划.md` §3 D-08/D-09/D-10 + §4 批次 C。
用例数据取自真实规模真跑实测（项目 3df5717c-…，ASP.NET WebForms C# 系统：`.js` 256 个
第三方前端库文件压过 `.cs` 84 个）。

  D-08 工作计划 goal 把 `detected_stack[0]` 当权威主栈渲染 → 文案写成「候选栈为 JavaScript」，
       而该项目是 ASP.NET WebForms C# 系统（决策未被污染，纯文案缺陷）。
  D-09 P2 评估报告识别到问题，但四类结构化清单产物全是 `items: []` —— 空数组会被读成
       「已检查且无风险」，属虚假事实断言（公理3 / D-097）。
  D-10 `p5_gate_brief.validation_verdict` 为 null，而同阶段 `p5_validation.verdict` 是
       `rework_required`；面向用户的 gate brief 恰是空的那个。
"""

import json
from types import SimpleNamespace

import pytest

from app.services import workspace_service
from app.services.work_agent import WorkAgent


# ── 真实场景种子：ASP.NET WebForms C# 系统（.js 256 / .cs 84） ─────────────────
_JS_COUNT = 256      # layuiadmin / fullcalendar / layfly 等第三方前端库文件（真跑实测值）
_CS_COUNT = 84       # 服务端 C# 源文件（真跑实测值）


def _seed_webforms_source(pid: str) -> None:
    """按真跑实测比例铺源码：前端第三方 .js 远多于服务端 .cs，且无 .csproj（Website 形态）。"""
    workspace_service.init_workspace(pid)
    src = workspace_service.workspace_path(pid) / "source"
    js_dir = src / "Scripts" / "layuiadmin"
    js_dir.mkdir(parents=True, exist_ok=True)
    for i in range(_JS_COUNT):
        (js_dir / f"vendor_{i}.js").write_text("var a=1;\n", encoding="utf-8")
    cs_dir = src / "App_Code"
    cs_dir.mkdir(parents=True, exist_ok=True)
    for i in range(_CS_COUNT):
        (cs_dir / f"Handler_{i}.cs").write_text("public class C {}\n", encoding="utf-8")
    (src / "MicroOA.sln").write_text("Microsoft Visual Studio Solution File\n", encoding="utf-8")


def _fake_handler(*, execute_result: dict, goal: str = "P5 验证阶段主任务") -> SimpleNamespace:
    """最小 handler-Tool 桩：WorkAgent 只需 goal/planned_actions/acceptance_criteria/execute。"""
    return SimpleNamespace(
        goal=goal,
        planned_actions=["执行阶段主任务"],
        acceptance_criteria=["产出真实产物"],
        execute=lambda state: execute_result,
    )


# ══════════════════════════════════════════════════════════════════════════
# D-08：候选栈文案不得把 detected_stack[0] 当权威
# ══════════════════════════════════════════════════════════════════════════
def test_d08_p1_goal_does_not_render_top_ext_as_authoritative_stack(isolated_data):
    """P1 工作计划 goal 不得出现「候选栈为 JavaScript」式单一权威表述（D-08）。"""
    pid = "rw-d08-p1"
    _seed_webforms_source(pid)

    wa = WorkAgent("p1", pid, run_id="r1")
    plan_ref = wa.build_work_plan({"source_type": "local_dir"})
    plan = json.loads((workspace_service.workspace_path(pid) / plan_ref).read_text("utf-8"))
    goal = plan["goal"]

    # ① 不得把首位扩展名渲染成权威主栈
    assert "候选栈为 JavaScript" not in goal, f"goal 仍把 detected_stack[0] 当权威主栈：{goal}"
    assert "候选栈为" not in goal, f"goal 仍有单一权威主栈表述：{goal}"
    # ② 必须显式标注这是按文件计数的非权威候选栈
    assert "按文件计数的候选栈（非权威识别）" in goal, goal
    # ③ 真实服务端栈 C# 不得被首位的 JavaScript 掩盖（多类并列，非取首位）
    assert "C#" in goal, f"C# 被 .js 计数掩盖，读计划的人会被误导：{goal}"
    assert "JavaScript" in goal, goal
    # ④ planned_actions 的采集 rationale 同口径（同一渲染入口，不各写一套）
    rationale = " ".join(a.get("rationale", "") for a in plan["planned_actions"])
    assert "按文件计数的候选栈（非权威识别）" in rationale, rationale


def test_d08_generic_stage_goal_lists_candidates_not_first_one(isolated_data):
    """非 P1 阶段（_plan_body_generic）goal 同样不得取首位当权威（P5 复现过同一缺陷）。"""
    pid = "rw-d08-p5"
    _seed_webforms_source(pid)

    wa = WorkAgent("p5", pid, run_id="r1",
                   handler=_fake_handler(execute_result={"status": "completed"}))
    plan_ref = wa.build_work_plan({"source_type": "local_dir"})
    plan = json.loads((workspace_service.workspace_path(pid) / plan_ref).read_text("utf-8"))
    goal = plan["goal"]

    assert "候选栈 JavaScript" not in goal, f"P5 goal 仍取首位当权威：{goal}"
    assert "按文件计数的候选栈（非权威识别）" in goal, goal
    assert "C#" in goal and "JavaScript" in goal, goal


def test_d08_detected_stack_collection_and_ordering_unchanged(isolated_data):
    """防回归：`detected_stack` 的【采集与排序】逻辑不因文案修复而改动（只改渲染）。"""
    pid = "rw-d08-facts"
    _seed_webforms_source(pid)

    wa = WorkAgent("p1", pid, run_id="r1")
    facts = wa._scan_project_facts({"source_type": "local_dir"})

    # 采集：文件总数 = .js + .cs + .sln
    assert facts["file_count"] == _JS_COUNT + _CS_COUNT + 1
    # 排序：仍按【原始文件计数】降序 —— 首位仍是 JavaScript（采集口径不变，只是不再当权威渲染）
    assert facts["detected_stack"][0] == "JavaScript"
    assert "C#" in facts["detected_stack"]
    assert facts["detected_stack"].index("JavaScript") < facts["detected_stack"].index("C#")
    assert "MicroOA.sln" in facts["build_files"]

    # 文案渲染函数本身：列前 N 类 + 口径标注，且不以单一栈开头
    text = WorkAgent._candidate_stack_text(facts)
    assert text.startswith("按文件计数的候选栈（非权威识别）：")
    assert "JavaScript" in text and "C#" in text
    assert WorkAgent._candidate_stack_text({"detected_stack": []}) == ""


# ══════════════════════════════════════════════════════════════════════════
# D-09：P2 四类清单产物 —— 派生 or 诚实标注，绝不留 items: []
# ══════════════════════════════════════════════════════════════════════════
_P2_LIST_FILES = ("p2_risk_list.json", "p2_blocker_list.json",
                  "p2_validation_gaps.json", "p2_resource_needs.json")


def _seed_p2_handler_artifacts(pid: str, *, items_by_file: dict | None = None) -> list:
    """模拟 stage_handlers._write_artifacts 的落盘结果（默认四类清单皆 items: []）。"""
    art = workspace_service.workspace_path(pid) / "artifacts" / "p2"
    art.mkdir(parents=True, exist_ok=True)
    refs = []
    types = {"p2_risk_list.json": "risk_assessment", "p2_blocker_list.json": "blocker_list",
             "p2_validation_gaps.json": "validation_gap", "p2_resource_needs.json": "resource_needs"}
    for name in _P2_LIST_FILES:
        payload = {"project_id": pid, "stage": "p2", "artifact_type": types[name],
                   "items": (items_by_file or {}).get(name, [])}
        (art / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        refs.append(f"artifacts/p2/{name}")
    return refs


def _seed_p2_assessment_report(pid: str, report: dict) -> str:
    art = workspace_service.workspace_path(pid) / "artifacts" / "p2"
    art.mkdir(parents=True, exist_ok=True)
    (art / "p2_assessment_report.json").write_text(
        json.dumps({"project_id": pid, "stage": "p2", "artifact_type": "assessment_report",
                    "report": report}, ensure_ascii=False), encoding="utf-8")
    return "artifacts/p2/p2_assessment_report.json"


def _read_artifact(pid: str, rel: str) -> dict:
    return json.loads((workspace_service.workspace_path(pid) / rel).read_text("utf-8"))


@pytest.mark.asyncio
async def test_d09_empty_lists_become_honest_notes_not_empty_arrays(isolated_data):
    """LLM 未产出结构化清单（真跑实测：评估报告 parse_error）→ 四个清单产物逐个写诚实标注，
    不得留 `items: []`（空数组等于宣称"已检查且无风险"）。"""
    pid = "rw-d09-honest"
    _seed_webforms_source(pid)
    refs = _seed_p2_handler_artifacts(pid)
    # 真跑实测形态：assessment_report 解析失败（raw + parse_error），四类清单皆空
    report_ref = _seed_p2_assessment_report(
        pid, {"raw": "{\"assessment_report\": {\"compatibility_hosting\": ...", "parse_error": True})

    handler = _fake_handler(goal="P2 评估阶段主任务", execute_result={
        "status": "completed",
        "assessment_report": {"raw": "truncated", "parse_error": True},
        "risk_list": [], "blocker_list": [], "validation_gap_list": [], "resource_needs": [],
        "artifacts": refs + [report_ref],
    })
    wa = WorkAgent("p2", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1"})
    assert result["status"] == "completed"

    for name in _P2_LIST_FILES:                      # 逐个断言（硬要求）
        data = _read_artifact(pid, f"artifacts/p2/{name}")
        assert "items" not in data, f"{name} 仍留 items（空数组会被读成「无风险」）：{data}"
        assert data.get("items_status") == "not_produced", f"{name} 缺 items_status：{data}"
        note = data.get("identification_note", "")
        assert "诚实标注" in note and "未伪造" in note, f"{name} 缺诚实标注：{data}"
        assert data["derivation_attempted"]["assessment_report_parse_error"] is True, data
        assert data["reconciled_by"] == "work_agent"

    # claim-evidence map 亦不得是 entries: []（会被读成"无结论"）：绑定真实评估报告 + 如实说明
    cem = _read_artifact(pid, result["claim_evidence_map_ref"])
    assert cem["entries"], "P2 claim-evidence 不得为空数组"
    entry = cem["entries"][0]
    assert entry["bindings"]["artifact_refs"] == ["artifacts/p2/p2_assessment_report.json"]
    assert "非「无风险」" in entry["statement"], entry["statement"]


@pytest.mark.asyncio
async def test_d09_lists_are_derived_from_assessment_report_fields(isolated_data):
    """评估报告结构化字段里有清单（LLM 未写进顶层锚点键）→ 正确派生进清单产物，并标派生来源。"""
    pid = "rw-d09-derive"
    _seed_webforms_source(pid)
    refs = _seed_p2_handler_artifacts(pid)
    report = {
        "compatibility_hosting": {
            # 嵌套在评估段落里的风险清单（按【键名】结构化搬运，不做语义研判）
            "risks": [{"title": "Website 形态无 .csproj，dotnet build 无工程入口",
                       "risk_level": "L4", "evidence_refs": ["artifacts/p2/p2_assessment_report.json"]}],
        },
        "deployment_hosting": {"blockers": [{"title": "IIS 集成模式应用池不可用于 Linux 容器"}]},
        "poc_scope": {"resource_needs": ["需 SQL Server 2008 R2 兼容性验证环境"]},
    }
    report_ref = _seed_p2_assessment_report(pid, report)

    handler = _fake_handler(goal="P2 评估阶段主任务", execute_result={
        "status": "completed", "assessment_report": report,
        "risk_list": [], "blocker_list": [], "validation_gap_list": [], "resource_needs": [],
        "artifacts": refs + [report_ref],
    })
    wa = WorkAgent("p2", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1"})
    assert result["status"] == "completed"

    risk = _read_artifact(pid, "artifacts/p2/p2_risk_list.json")
    assert len(risk["items"]) == 1
    assert risk["items"][0]["title"].startswith("Website 形态无 .csproj")
    assert risk["items"][0]["derived_from"].endswith("#report.compatibility_hosting.risks")
    assert "compatibility_hosting.risks" in risk["derived_from_fields"]
    assert "派生" in risk["derivation_note"] and "未新增任何风险判断" in risk["derivation_note"]

    blocker = _read_artifact(pid, "artifacts/p2/p2_blocker_list.json")
    assert [i["title"] for i in blocker["items"]] == ["IIS 集成模式应用池不可用于 Linux 容器"]
    res = _read_artifact(pid, "artifacts/p2/p2_resource_needs.json")
    assert res["items"][0]["title"] == "需 SQL Server 2008 R2 兼容性验证环境"   # 字符串项也被搬运

    # 报告里确实没有验证缺口清单 → 仍是诚实标注，不得凭空造项，也不得留 items: []
    gaps = _read_artifact(pid, "artifacts/p2/p2_validation_gaps.json")
    assert "items" not in gaps and gaps["items_status"] == "not_produced"

    # 派生结果对下游可见：claim-evidence 以派生项立 claim（不再 entries: []）
    cem = _read_artifact(pid, result["claim_evidence_map_ref"])
    assert any(e["id"].endswith("risk-0") for e in cem["entries"]), cem["entries"]
    # 派生清单亦进入 Gate Brief 风险栏（下游按清单消费的环节能看到风险）
    assert any("Website 形态无 .csproj" in (r.get("desc") or "")
               for r in result["gate_brief_partial"]["risks"])


@pytest.mark.asyncio
async def test_d09_existing_real_items_are_not_touched(isolated_data):
    """handler 已产出真实清单项 → WorkAgent 不改写产物（精准修改：只补缺，不覆盖真实结果）。"""
    pid = "rw-d09-keep"
    _seed_webforms_source(pid)
    real = [{"title": "真实风险项", "risk_level": "L3"}]
    refs = _seed_p2_handler_artifacts(pid, items_by_file={"p2_risk_list.json": real})
    report_ref = _seed_p2_assessment_report(pid, {"compatibility_hosting": {"verdict_type": "x"}})

    handler = _fake_handler(goal="P2 评估阶段主任务", execute_result={
        "status": "completed", "assessment_report": {"compatibility_hosting": {"verdict_type": "x"}},
        "risk_list": real, "blocker_list": [], "validation_gap_list": [], "resource_needs": [],
        "artifacts": refs + [report_ref],
    })
    wa = WorkAgent("p2", pid, run_id="r1", handler=handler)
    await wa.execute({"project_id": pid, "run_id": "r1"})

    risk = _read_artifact(pid, "artifacts/p2/p2_risk_list.json")
    assert risk["items"] == real
    assert "reconciled_by" not in risk, "非空清单不应被 WorkAgent 重写"


# ══════════════════════════════════════════════════════════════════════════
# D-10：gate_brief.validation_verdict 与真实 validation 结果单一来源
# ══════════════════════════════════════════════════════════════════════════
def _seed_validation_report(pid: str, stage: str, payload: dict) -> str:
    art = workspace_service.workspace_path(pid) / "artifacts" / stage
    art.mkdir(parents=True, exist_ok=True)
    (art / f"{stage}_validation.json").write_text(json.dumps(payload, ensure_ascii=False),
                                                  encoding="utf-8")
    return f"artifacts/{stage}/{stage}_validation.json"


@pytest.mark.asyncio
async def test_d10_gate_brief_verdict_matches_validation_report(isolated_data):
    """真跑实测场景：p5_validation.verdict = rework_required 时，p5_gate_brief.validation_verdict
    必须取到同一结论（不再是 null）。"""
    pid = "rw-d10-rework"
    _seed_webforms_source(pid)
    val_ref = _seed_validation_report(pid, "p5", {
        "stage": "p5", "reviewer": "validation_agent", "passed": False,
        "verdict": "rework_required", "agent_id": "agent-abc",
        "issues": [{"type": "domain", "detail": "TESTS_PASS 槽位未通过"}],
        "validated_at": "2026-09-10T11:36:05+00:00",
    })

    handler = _fake_handler(execute_result={
        "status": "blocked",
        "reason": "有条件必需槽位 TESTS_PASS 未通过（status=validation_failed）",
        "artifacts": [],
    })
    wa = WorkAgent("p5", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1"})
    assert result["status"] == "blocked"

    brief = _read_artifact(pid, result["gate_brief_ref"])
    validation = _read_artifact(pid, val_ref)
    vv = brief["validation_verdict"]
    assert vv is not None, "gate_brief.validation_verdict 不得为 null（D-10）"
    assert vv["verdict"] == validation["verdict"] == "rework_required"
    assert vv["passed"] is False
    assert vv["issues_count"] == len(validation["issues"]) == 1
    assert vv["agent_id"] == "agent-abc"
    assert vv["source_ref"] == val_ref, "须标明取值来源（单一来源可追溯）"


@pytest.mark.asyncio
async def test_d10_no_validation_yet_is_honest_marker_not_null(isolated_data):
    """尚无落盘验收结论时写诚实标注（未假称通过/无问题），而不是 null。"""
    pid = "rw-d10-pending"
    _seed_webforms_source(pid)
    handler = _fake_handler(execute_result={"status": "completed", "artifacts": []})
    wa = WorkAgent("p5", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1"})

    vv = _read_artifact(pid, result["gate_brief_ref"])["validation_verdict"]
    assert vv is not None
    assert vv["verdict"] == "尚未取得" and vv["passed"] is None
    assert vv["verdict_scope"] == "not_yet_validated"
    assert "未假称通过或无问题" in vv["verdict_note"]


@pytest.mark.asyncio
async def test_d10_gate_backend_read_path_not_regressed(isolated_data, monkeypatch):
    """gate_backend 读 gate_brief 合成 summary/reason 的既有路径不回归：新取值下
    真实 verdict 进入 Gate 文案（期望的改善），且 pending 标注不致其报错。"""
    from app.graph import gate_backend as gb_mod

    created: list = []

    class _FakeGateService:
        def create(self, **kw):
            created.append(kw)
            return SimpleNamespace(gate_id="gate-fake-1")

    monkeypatch.setattr(gb_mod, "get_services",
                        lambda: SimpleNamespace(gate_service=_FakeGateService()))

    # ① 真实 rework_required verdict → 进入 Gate reason（gate_backend 读取路径仍工作）
    pid = "rw-d10-gb"
    _seed_webforms_source(pid)
    _seed_validation_report(pid, "p5", {"verdict": "rework_required", "passed": False,
                                        "issues": [{"type": "domain", "detail": "槽位未通过"}],
                                        "agent_id": "agent-abc"})
    handler = _fake_handler(execute_result={"status": "blocked", "reason": "槽位未通过",
                                            "artifacts": []})
    wa = WorkAgent("p5", pid, run_id="r1", handler=handler)
    result = await wa.execute({"project_id": pid, "run_id": "r1"})

    brief = gb_mod._read_gate_brief(pid, [result["gate_brief_ref"]])
    assert brief is not None and isinstance(brief.get("validation_verdict"), dict)

    gid = gb_mod.RealGateBackend().create(project_id=pid, run_id="r1", stage="p5",
                                          artifact_refs=[result["gate_brief_ref"]])
    assert gid == "gate-fake-1"
    reason = created[-1]["reason"]
    assert "rework_required" in reason and "1 项问题" in reason, reason
    assert created[-1]["summary"], "summary 仍取自 gate_brief.what_happened"

    # ② pending 诚实标注下 gate_backend 不报错、也不冒充"已通过独立验收"结论值
    pid2 = "rw-d10-gb2"
    _seed_webforms_source(pid2)
    wa2 = WorkAgent("p5", pid2, run_id="r1",
                    handler=_fake_handler(execute_result={"status": "completed", "artifacts": []}))
    result2 = await wa2.execute({"project_id": pid2, "run_id": "r1"})
    gb_mod.RealGateBackend().create(project_id=pid2, run_id="r1", stage="p5",
                                    artifact_refs=[result2["gate_brief_ref"]])
    reason2 = created[-1]["reason"]
    assert "尚未取得" in reason2, reason2
    assert "accepted" not in reason2, reason2
