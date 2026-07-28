"""R17.5-P6-R4 有机全绿 P5→P6 全链回归测（真跑，非 mock，D-097 / feedback_dtrack_must_be_real_run）。

背景：P6 三轨（R1 skill-first LLM advisory / R2 capability-first 交付能力 / R3 许可+定级+验收档）
已分别 accepted，但缺一条【有机全绿】的端到端回归——同一条真实 P5→P6 链上同时断言 R1/R2/R3
三轨产出真实落盘、语义正确、且【均不翻转门禁】。本测复用 P5-R4 干净真阳链路助手
(_build_legal_p4_artifacts + _approve_p4_to_p5_gate)，驱动真实 RealP5Handler → RealP6Handler
（不 mock 输入/验证/命令/交付服务；LLM advisory 走 conftest mock-LLM 单次 call 路径），核验：

  真阳链：合法 P4 → P5 can_be_completed=True → P6 completed，handler 返回体 + 持久化
          artifacts/p6_delivery_report.json 同时含 R1(delivery_advisory) / R2(delivery_capabilities)
          / R3(license_notice + scope_level + acceptance_result) 三分区。
  反伪造：三分区【均不翻转门禁】——
    - R3 acceptance_result 是确定性事实档（deterministic=true），干净真阳映射 accepted_with_warning
      （PoC + evidence_gap + 许可不清，诚实降级，绝不伪 production accepted）。
    - R2 delivery_capabilities 本沙箱无一 available（evidence_gap / not_applicable），summary 诚实。
    - R1 delivery_advisory 恒 analysis_only，即便 status=completed（mock LLM parse_error）也不产出
      "通过/accepted" 确定性结论覆盖门禁。
  负向：P5 未过（删 patch）→ P6 诚实 blocked，不产 accepted 交付、不落交付报告。

红线：无 mock 生产路径；产物真实落盘后被读取核验（No Evidence No Completed）；不硬编码 MicroOA。
"""

import asyncio
import json

from app.services import workspace_service

# 复用 P5-R4 干净真阳链路助手（真实工作区产物，非 mock）
from tests.test_r175_p5_r4_organic_green import (
    _build_legal_p4_artifacts, _approve_p4_to_p5_gate,
)

ACCEPTANCE_RESULTS = ("accepted", "accepted_with_warning", "rework_required", "blocked")


def _run_green_chain(pid: str, run_id: str):
    """真跑 合法 P4 → P5 → P6，返回 (p6_result, persisted_report_dict)。"""
    from app.graph.stage_handlers import RealP5Handler, RealP6Handler
    workspace_service.init_workspace(pid)
    _build_legal_p4_artifacts(pid, run_id)
    _approve_p4_to_p5_gate(pid, run_id)

    p5_result = asyncio.run(RealP5Handler().execute({"project_id": pid, "run_id": run_id}))
    assert p5_result["status"] == "completed", f"P5 应真阳完成，实际 {p5_result['status']}"

    p6_result = asyncio.run(RealP6Handler().execute({"project_id": pid, "run_id": run_id}))
    ws = workspace_service.workspace_path(pid)
    report_path = ws / "artifacts" / "p6_delivery_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    return p6_result, report


# ── 真阳全绿：R1/R2/R3 三分区同时落盘且语义正确 ──────────────────────────────

def test_r4_organic_green_persisted_report_has_r1_r2_r3(isolated_data):
    """干净真阳 P5→P6 → 持久化 p6_delivery_report.json 同时含 R1/R2/R3 三分区。"""
    p6_result, report = _run_green_chain("r175-p6-r4-green", "run-p6r4")

    assert p6_result["status"] == "completed", (
        f"P5 真阳后 P6 应交付，实际 {p6_result['status']} / {p6_result.get('reason')}")
    assert report is not None, "P6 应真实落 p6_delivery_report.json"
    assert report["stage"] == "p6"

    # ── R3：许可 / 定级 / 验收档（确定性事实档）─────────────────────────────
    assert "license_clarity" in report["license_notice"]
    assert report["scope_level"] in ("poc", "production_candidate")
    acc = report["acceptance_result"]
    assert acc["result"] in ACCEPTANCE_RESULTS
    assert acc["deterministic"] is True, "acceptance_result 必须是确定性事实档"
    # 干净真阳（PoC + evidence_gap + 许可不清）→ 诚实 accepted_with_warning，绝不 blocked、绝不伪 production accepted
    assert acc["result"] == "accepted_with_warning", (
        f"PoC + evidence_gap + 许可不清应诚实降级 accepted_with_warning，实际 {acc['result']}")
    assert report["scope_level"] == "poc", "证据不完整（许可不清）不得伪装 production_candidate"

    # ── R2：交付能力清单（capability-first，非门禁）────────────────────────
    caps = report["delivery_capabilities"]
    assert "capabilities" in caps and "summary" in caps
    summary = caps["summary"]
    # 本沙箱无目标信创运行时 / 无外部签名设施 → 无一能力 available（反伪造）
    assert summary["environment_available"] == 0, "本沙箱不得伪造 available 交付能力"
    assert summary["evidence_gap"] + summary["not_applicable"] == summary["total"]
    for c in caps["capabilities"]:
        assert c["status"] in ("evidence_gap", "not_applicable"), (
            f"能力 {c['capability']} 应诚实降级，实际 {c['status']}")
        assert c["capability_ready"] is True, "能力已接线（capability_ready）应为真"

    # ── R1：LLM 交付叙述 advisory（analysis_only，不改门禁）─────────────────
    adv = report["delivery_advisory"]
    assert adv["status"] in ("completed", "skipped", "failed"), f"advisory status 异常：{adv['status']}"
    assert adv["analysis_only"] is True, "advisory 必须恒为 analysis_only（辅助分析，非门禁）"
    # 反伪造：advisory 不得携带确定性"通过/accepted"结论覆盖门禁——acceptance_advice 是建议非结论
    acc_advice = adv.get("acceptance_advice") or {}
    if acc_advice:
        assert "suggested_result" in acc_advice or acc_advice == {}, "advisory 只给 suggested_result 建议"


def test_r4_organic_green_handler_return_carries_three_tracks(isolated_data):
    """真阳链 handler 返回体（非仅持久化）同时带出 R1/R2/R3，且 advisory 不覆盖确定性档。"""
    p6_result, _report = _run_green_chain("r175-p6-r4-return", "run-p6r4-ret")
    assert p6_result["status"] == "completed"

    # R3 三字段在返回体
    assert p6_result["scope_level"] in ("poc", "production_candidate")
    assert p6_result["acceptance_result"]["result"] in ACCEPTANCE_RESULTS
    assert p6_result["acceptance_result"]["deterministic"] is True
    assert "license_clarity" in p6_result["license_notice"]

    # R2 能力清单在返回体
    assert "delivery_capabilities" in p6_result
    assert p6_result["delivery_capabilities"]["summary"]["environment_available"] == 0

    # R1 advisory 在返回体且 analysis_only
    assert p6_result["delivery_advisory"]["analysis_only"] is True

    # 反伪造核心：确定性 acceptance_result 独立于 LLM advisory——
    # 即便 advisory status=completed（mock LLM），确定性档仍由内核认定，不被 advisory 覆盖为 accepted。
    adv = p6_result["delivery_advisory"]
    det_result = p6_result["acceptance_result"]["result"]
    assert det_result == "accepted_with_warning", "确定性验收档不得被 LLM advisory 翻转"
    # advisory 即便"completed"也不得把确定性档粉饰成 accepted（生产可交付）
    assert not (adv.get("status") == "completed"
                and det_result == "accepted"
                and p6_result["scope_level"] == "poc"), "advisory 不得把 PoC 粉饰为 accepted 生产交付"

    # final gate 真实创建（P 阶段晋级 Gate 须用户授权，D-023）
    assert p6_result["p6_final_gate_id"], "P6 应创建最终 Gate（待用户批准）"
    # 干净 python 交付脱敏应通过
    assert p6_result["desensitization"]["ok"] is True


# ── 负向：P5 未过 → P6 诚实 blocked，三分区不翻转门禁、不产 accepted 交付 ──────

def test_r4_negative_p5_not_passed_blocks_p6_no_accepted_delivery(isolated_data):
    """删 patch → P5 not completed → P6 blocked；不落交付报告、不产 accepted、三分区不粉饰。"""
    from app.graph.stage_handlers import RealP5Handler, RealP6Handler
    pid, run_id = "r175-p6-r4-neg", "run-p6r4-neg"
    workspace_service.init_workspace(pid)
    refs = _build_legal_p4_artifacts(pid, run_id)
    # 删除 patch → 硬必需 patches_exist 不过 → P5 can_be_completed=False
    (workspace_service.workspace_path(pid) / refs["patch_rel"]).unlink()
    _approve_p4_to_p5_gate(pid, run_id)

    p5_result = asyncio.run(RealP5Handler().execute({"project_id": pid, "run_id": run_id}))
    assert p5_result["status"] == "blocked", "缺 patch → P5 不得真阳（反伪造）"

    p6_result = asyncio.run(RealP6Handler().execute({"project_id": pid, "run_id": run_id}))
    # 门禁由确定性内核认定 blocked（早于交付包生成 + advisory/能力探测），三分区不参与、不粉饰
    assert p6_result["status"] == "blocked", "P5 未过 → P6 诚实 blocked"
    assert "P5" in p6_result["reason"]
    # blocked 路径不产任何 accepted 交付事实（三分区/交付包/final gate 均未生成）
    assert "acceptance_result" not in p6_result, "blocked 不得产验收档"
    assert "delivery_advisory" not in p6_result, "blocked 不得产 LLM advisory"
    assert "delivery_capabilities" not in p6_result, "blocked 不得产交付能力分区"
    assert "p6_final_gate_id" not in p6_result, "blocked 不得创建 final gate"
    # 不落 p6_delivery_report.json（不产伪交付）
    report_path = (workspace_service.workspace_path(pid) / "artifacts" / "p6_delivery_report.json")
    assert not report_path.exists(), "P5 未过 → 不得落交付报告（No Fake Delivery）"


# ── D-074 前后端联调：/p6/package 向前端暴露 R1/R2/R3（真实 handler 产物，非 mock）────

def test_r4_p6_package_route_exposes_three_tracks_for_frontend(client):
    """StagePageP6 数据源 /p6/package 在真实 P6 跑后暴露 R3(定级/验收/许可) + R1(advisory) + R2(能力)。"""
    from app.graph.stage_handlers import RealP5Handler, RealP6Handler
    pid, run_id = "r175-p6-r4-route", "run-p6r4-route"
    workspace_service.init_workspace(pid)
    _build_legal_p4_artifacts(pid, run_id)
    _approve_p4_to_p5_gate(pid, run_id)
    asyncio.run(RealP5Handler().execute({"project_id": pid, "run_id": run_id}))
    p6_result = asyncio.run(RealP6Handler().execute({"project_id": pid, "run_id": run_id}))
    assert p6_result["status"] == "completed"

    resp = client.get(f"/api/projects/{pid}/runs/{run_id}/p6/package")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # R3 由 delivery_package_to_dict 直出
    assert data["scope_level"] in ("poc", "production_candidate")
    assert data["acceptance_result"]["result"] in ACCEPTANCE_RESULTS
    assert "license_clarity" in data["license_notice"]
    # R1 / R2 由 route 从真实持久化 p6_delivery_report.json 附加（handler 产物，非 mock）
    assert "delivery_capabilities" in data, "路由应向前端暴露 R2 交付能力清单"
    assert data["delivery_capabilities"]["summary"]["environment_available"] == 0
    assert "delivery_advisory" in data, "路由应向前端暴露 R1 交付 advisory"
    assert data["delivery_advisory"]["analysis_only"] is True


def test_r4_p6_package_route_422_when_p5_not_passed(client):
    """StagePageP6 静默空态契约：P5 未过 → /p6/package 返回 422（前端据此渲染空态、不弹错误）。"""
    pid, run_id = "r175-p6-r4-route-neg", "run-p6r4-route-neg"
    workspace_service.init_workspace(pid)
    refs = _build_legal_p4_artifacts(pid, run_id)
    (workspace_service.workspace_path(pid) / refs["patch_rel"]).unlink()
    _approve_p4_to_p5_gate(pid, run_id)
    from app.graph.stage_handlers import RealP5Handler
    asyncio.run(RealP5Handler().execute({"project_id": pid, "run_id": run_id}))

    resp = client.get(f"/api/projects/{pid}/runs/{run_id}/p6/package")
    assert resp.status_code == 422, "P5 未过 → 422（前端静默空态）"
