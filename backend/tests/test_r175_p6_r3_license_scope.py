"""R17.5-P6-R3 (GAP-P6-2/3) — 许可确定性检测 + PoC/Production 定级 + ACCEPTANCE_RESULTS 多档。

覆盖（确定性事实/映射，非门禁翻转、反伪造、脱敏 D-032、源只读 D-099）：
  ① 许可不清默认 accepted_with_warning，不 blocked（Q-P6-2 内部验证可继续口径）。
  ② distribution_flag=true + 许可不清 才升 blocked（对外分发前须核实）。
  ③ PoC 不被误判 production_candidate（证据不完整时诚实降级）。
  ④ 全绿 + 许可 clear + production_candidate → accepted（唯一可给 accepted 的路径）。
  ⑤ license_notice / scope_level / acceptance_result 均落进 persisted p6_delivery_report.json。
  ⑥ 这些字段不翻转任何门禁：P5 未过仍 blocked（确定性内核认定，字段不粉饰）。
"""

import asyncio
import json

from app.services.p6_delivery_service import P6DeliveryService
from app.services.p5_input_service import P4InputFacts

# 复用 P5-R4 干净真阳链路助手（真实工作区产物，非 mock）
from tests.test_r175_p5_r4_organic_green import (
    _build_legal_p4_artifacts, _approve_p4_to_p5_gate,
)
from app.services import workspace_service


# ── 许可确定性检测 _build_license_notice ─────────────────────────────────────

def _make_ws(tmp_path, *, license_file=False, declaration=False, distribution=False):
    """在临时工作区落 output_code；可选 LICENSE 文件 / 许可声明 / 对外分发标记。"""
    oc = tmp_path / "output_code"
    oc.mkdir(parents=True, exist_ok=True)
    code = "def f():\n    return 1\n"
    if declaration:
        code = "# SPDX-License-Identifier: MIT\n" + code
    (oc / "mod.py").write_text(code, encoding="utf-8")
    if license_file:
        (oc / "LICENSE").write_text("MIT License\nCopyright (c) 2026 x\n", encoding="utf-8")
    if distribution:
        (oc / "README.md").write_text("本迁移组件用于对外分发发布。\n", encoding="utf-8")
    return tmp_path


class TestLicenseNoticeDetection:

    def test_no_license_file_is_unclear(self, tmp_path):
        svc = P6DeliveryService()
        ws = _make_ws(tmp_path, license_file=False, declaration=False)
        notice = svc._build_license_notice(ws, [{"path": "output_code/mod.py"}], None)
        assert notice["license_clarity"] == "unclear"
        assert notice["license_files_found"] == []
        assert notice["distribution_flag"] is False

    def test_license_file_plus_declaration_is_clear(self, tmp_path):
        svc = P6DeliveryService()
        ws = _make_ws(tmp_path, license_file=True, declaration=True)
        notice = svc._build_license_notice(ws, [{"path": "output_code/mod.py"}], None)
        assert notice["license_clarity"] == "clear"
        assert any("LICENSE" in p for p in notice["license_files_found"])
        # 声明/第三方标记只记 路径 + 模式名（D-032：无内容、无密钥）
        assert all(set(d.keys()) == {"path", "pattern"} for d in notice["license_declarations"])
        assert all(set(m.keys()) == {"path", "pattern"} for m in notice["third_party_markers"])

    def test_distribution_marker_sets_flag(self, tmp_path):
        svc = P6DeliveryService()
        ws = _make_ws(tmp_path, license_file=False, distribution=True)
        notice = svc._build_license_notice(ws, [{"path": "output_code/mod.py"}], None)
        assert notice["distribution_flag"] is True
        assert notice["license_clarity"] == "unclear"


# ── PoC/Production 定级 + ACCEPTANCE_RESULTS 映射 _derive_scope_and_acceptance ─

def _risk_manifest(svc, validation_plan, license_notice):
    return svc._build_risk_manifest(
        P4InputFacts(project_id="p", run_id="r"), validation_plan, license_notice=license_notice)


class TestScopeAndAcceptanceMapping:

    def test_unclear_license_defaults_accepted_with_warning_not_blocked(self):
        """① 许可不清（非对外分发）→ accepted_with_warning，绝不 blocked。"""
        svc = P6DeliveryService()
        license_notice = {"license_clarity": "unclear", "distribution_flag": False}
        vp = {"slots": [{"slot_id": "build_verified", "status": "validated"},
                        {"slot_id": "run_verified", "status": "not_applicable"}]}
        rm = _risk_manifest(svc, vp, license_notice)
        # 许可不清风险计入且【非阻断】
        lic_risks = [r for r in rm["risks"] if r["type"] == "license_unclear"]
        assert lic_risks and lic_risks[0]["blocking"] is False
        _scope, acc = svc._derive_scope_and_acceptance({"can_be_completed": True}, vp, rm, license_notice)
        assert acc["result"] == "accepted_with_warning"
        assert acc["result"] != "blocked"

    def test_distribution_plus_unclear_license_escalates_blocked(self):
        """② distribution_flag=true + 许可不清 → blocked（对外分发前须核实）。"""
        svc = P6DeliveryService()
        license_notice = {"license_clarity": "unclear", "distribution_flag": True}
        vp = {"slots": [{"slot_id": "build_verified", "status": "validated"},
                        {"slot_id": "run_verified", "status": "not_applicable"}]}
        rm = _risk_manifest(svc, vp, license_notice)
        lic_risks = [r for r in rm["risks"] if r["type"] == "license_unclear"]
        assert lic_risks and lic_risks[0]["blocking"] is True   # 对外分发 → 升阻断
        _scope, acc = svc._derive_scope_and_acceptance({"can_be_completed": True}, vp, rm, license_notice)
        assert acc["result"] == "blocked"

    def test_poc_not_misjudged_as_production(self):
        """③ 证据不完整（evidence_gap / build 未 validated）→ scope=poc，绝不 production_candidate。"""
        svc = P6DeliveryService()
        license_notice = {"license_clarity": "clear", "distribution_flag": False}
        vp = {"slots": [{"slot_id": "build_verified", "status": "evidence_gap"},
                        {"slot_id": "run_verified", "status": "not_applicable"}]}
        rm = _risk_manifest(svc, vp, license_notice)
        scope, acc = svc._derive_scope_and_acceptance({"can_be_completed": True}, vp, rm, license_notice)
        assert scope == "poc"
        assert acc["result"] == "accepted_with_warning"   # PoC + evidence_gap → 诚实警告
        assert acc["inputs"]["evidence_gap_present"] is True

    def test_all_green_clear_license_accepted(self):
        """④ 全绿（硬必需 + build/run validated + 无 gap）+ 许可 clear → accepted。"""
        svc = P6DeliveryService()
        license_notice = {"license_clarity": "clear", "distribution_flag": False}
        vp = {"slots": [{"slot_id": "build_verified", "status": "validated"},
                        {"slot_id": "run_verified", "status": "validated"}]}
        rm = _risk_manifest(svc, vp, license_notice)
        assert rm["has_blocking"] is False
        scope, acc = svc._derive_scope_and_acceptance({"can_be_completed": True}, vp, rm, license_notice)
        assert scope == "production_candidate"
        assert acc["result"] == "accepted"
        assert acc["deterministic"] is True

    def test_p5_not_completed_maps_blocked(self):
        """P5 未过（can_be_completed=false）→ 映射 blocked（不粉饰）。"""
        svc = P6DeliveryService()
        license_notice = {"license_clarity": "clear", "distribution_flag": False}
        vp = {"slots": []}
        rm = _risk_manifest(svc, vp, license_notice)
        _scope, acc = svc._derive_scope_and_acceptance({"can_be_completed": False}, vp, rm, license_notice)
        assert acc["result"] == "blocked"


# ── 端到端：真实 handler 落盘 + 门禁不被翻转 ─────────────────────────────────

class TestPersistAndGateNotFlipped:

    def test_fields_persisted_in_delivery_report(self, isolated_data):
        """⑤ 干净真阳 P5→P6 → license_notice/scope_level/acceptance_result 落进 p6_delivery_report.json。"""
        from app.graph.stage_handlers import RealP5Handler, RealP6Handler
        pid, run_id = "r175-p6-r3-persist", "run-p6r3"
        workspace_service.init_workspace(pid)
        _build_legal_p4_artifacts(pid, run_id)
        _approve_p4_to_p5_gate(pid, run_id)

        asyncio.run(RealP5Handler().execute({"project_id": pid, "run_id": run_id}))
        p6_result = asyncio.run(RealP6Handler().execute({"project_id": pid, "run_id": run_id}))
        assert p6_result["status"] == "completed"
        # handler 返回体带出三字段
        assert "scope_level" in p6_result and "acceptance_result" in p6_result
        assert "license_notice" in p6_result

        ws = workspace_service.workspace_path(pid)
        report = json.loads((ws / "artifacts" / "p6_delivery_report.json").read_text(encoding="utf-8"))
        # ⑤ 三字段落进持久化产物（作分区，与 delivery_advisory/delivery_capabilities 并列）
        assert report["scope_level"] in ("poc", "production_candidate")
        assert report["acceptance_result"]["result"] in (
            "accepted", "accepted_with_warning", "rework_required", "blocked")
        assert report["acceptance_result"]["deterministic"] is True
        assert "license_clarity" in report["license_notice"]
        # 反伪造：acceptance_result 是确定性事实档；LLM advisory（若有）独立并存
        assert report["acceptance_result"]["result"] != "blocked"  # 干净真阳不应 blocked

    def test_p5_not_passed_still_blocked_fields_do_not_flip_gate(self, isolated_data):
        """⑥ P5 未过 → P6 诚实 blocked；新字段不翻转门禁、不写伪 accepted。"""
        from app.graph.stage_handlers import RealP5Handler, RealP6Handler
        pid, run_id = "r175-p6-r3-neg", "run-p6r3-neg"
        workspace_service.init_workspace(pid)
        refs = _build_legal_p4_artifacts(pid, run_id)
        # 删除 patch → 硬必需 patches_exist 不过 → P5 can_be_completed=False
        (workspace_service.workspace_path(pid) / refs["patch_rel"]).unlink()
        _approve_p4_to_p5_gate(pid, run_id)

        asyncio.run(RealP5Handler().execute({"project_id": pid, "run_id": run_id}))
        p6_result = asyncio.run(RealP6Handler().execute({"project_id": pid, "run_id": run_id}))
        # 门禁由确定性内核认定 blocked（早于交付包生成），新字段不参与、不粉饰
        assert p6_result["status"] == "blocked"
        assert "P5" in p6_result["reason"]
        # blocked 路径不落交付报告 → 不产伪 accepted
        assert "acceptance_result" not in p6_result
