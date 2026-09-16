"""V26.3 · R24 回归锁：挂起动作在阶段产物中的可见性。

  R24-05  `B-ACC-HELD-ACTION-INVISIBLE`（P1）
          ① `{stage}_gate_brief.json` 与 `{stage}_validation.json` 须反映"本阶段有 N 个
             动作因待审批未执行"并列出 gate_id；
          ② 该信息须影响 `honest_notes`（本文件断言 gate_brief 侧）；
          ④ **不得**以"改为不拦"来解除 —— fail-closed 行为必须保留（本文件设正面反锁：
             断言 held_actions 不改变 passed / verdict，即动作被拦≠阶段失败）。

台账原始现象的复刻方式：对 P0 全部产物 grep
`awaiting_approval|待审批|挂起|未执行|被拦|action_approval|gate-xxxxx` ⇒ 旧代码零命中。
本文件用同一组关键词做断言，使"缺陷是否回来"可用与发现它时完全相同的手法验证。
"""

import json
import uuid

import pytest

from app.dependencies import get_services
from app.services import workspace_service

# 台账发现本缺陷时用的那组关键词（逐字沿用，便于对照）
_LEDGER_KEYWORDS = ("awaiting_approval", "待审批", "挂起", "未执行", "被拦", "action_approval")


def _mk_project(client, name="R24 HeldActions"):
    return client.post("/api/projects", json={
        "name": name, "source_type": "manual"}).json()["data"]["project_id"]


def _mk_run(client, pid):
    return client.post(f"/api/projects/{pid}/runs",
                       json={"run_goal": "held", "mode": "plan"}).json()["data"]["run_id"]


def _mk_action_gate(client, pid, run_id, stage="p0", risk="L4",
                    gate_type="action_approval"):
    return client.post(f"/api/projects/{pid}/gates", json={
        "run_id": run_id, "stage": stage, "gate_type": gate_type,
        "risk_level": risk,
        "reason": f"高风险工具 run_safe_command（风险 {risk}）执行前需人工审批",
        "summary": "Agent 拟执行高风险工具 run_safe_command（L4），请审批。",
    }).json()["data"]["gate_id"]


class TestCollectHeldActions:
    def test_pending_action_gate_is_collected(self, client):
        from app.services.held_actions import collect_held_actions
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        gid = _mk_action_gate(client, pid, rid, "p0")
        held = collect_held_actions(pid, rid, "p0")
        assert [h["gate_id"] for h in held] == [gid]
        assert held[0]["status"] == "awaiting_approval_not_executed"
        assert held[0]["risk_level"] == "L4"

    def test_flow_blocking_gates_are_not_held_actions(self, client):
        """晋级 Gate / 计划 Gate 不是"被挂起的动作"——判据与 get_active 的分流同源。"""
        from app.services.held_actions import collect_held_actions
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        _mk_action_gate(client, pid, rid, "p2", gate_type="stage_promotion", risk="L0")
        assert collect_held_actions(pid, rid, "p2") == []

    def test_decided_action_gate_is_not_held(self, client):
        from app.services.held_actions import collect_held_actions
        from app.schemas.gate import GateDecisionRequest
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        gid = _mk_action_gate(client, pid, rid, "p0")
        get_services().gate_service.decide(gid, GateDecisionRequest(decision="approve"),
                                           drive_promotion=False)
        assert collect_held_actions(pid, rid, "p0") == []

    def test_other_run_held_actions_do_not_leak_in(self, client):
        """跨 run 不串位（B-ACC-PROMOTE-DIRECT-RUNBLIND 那类教训的同款约束）。"""
        from app.services.held_actions import collect_held_actions
        pid = _mk_project(client)
        rid_a = _mk_run(client, pid)
        rid_b = _mk_run(client, pid)
        _mk_action_gate(client, pid, rid_a, "p0")
        assert collect_held_actions(pid, rid_b, "p0") == []

    def test_other_stage_held_actions_do_not_leak_in(self, client):
        from app.services.held_actions import collect_held_actions
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        _mk_action_gate(client, pid, rid, "p4")
        assert collect_held_actions(pid, rid, "p0") == []

    def test_l5_command_gate_is_also_collected(self, client):
        """判据不是 gate_type 名单：新增的动作审批类型自动被覆盖。"""
        from app.services.held_actions import collect_held_actions
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        gid = _mk_action_gate(client, pid, rid, "p4",
                             gate_type="l5_high_risk_command", risk="L5")
        assert [h["gate_id"] for h in collect_held_actions(pid, rid, "p4")] == [gid]

    def test_note_is_empty_when_nothing_held(self):
        from app.services.held_actions import held_actions_note, merge_held_actions_note
        assert held_actions_note([]) == ""
        assert merge_held_actions_note("原有说明", []) == "原有说明"

    def test_note_keeps_existing_honest_notes_first(self):
        from app.services.held_actions import merge_held_actions_note
        merged = merge_held_actions_note("原有说明", [{"gate_id": "gate-abc"}])
        assert merged.startswith("原有说明")
        assert "gate-abc" in merged


class TestGateBriefSurfacesHeldActions:
    def test_gate_brief_lists_gate_ids_and_updates_honest_notes(self, client):
        """解除条件 ①②：产物里能直接看到 gate_id，且 honest_notes 不再为空。"""
        from app.graph.stage_reports import StageReports
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        gid = _mk_action_gate(client, pid, rid, "p0")
        ref = StageReports(pid, "p0").gate_brief(
            stage="p0", what_happened="P0 接入完成", key_artifacts=[], risks=[],
            honest_notes="", validation_verdict={"verdict": "accepted", "issues_count": 0},
            run_id=rid)
        doc = json.loads((workspace_service.workspace_path(pid) / ref).read_text("utf-8"))
        assert doc["held_action_count"] == 1
        assert doc["held_actions"][0]["gate_id"] == gid
        assert gid in doc["honest_notes"]
        assert doc["honest_notes"] != ""

    def test_ledger_keywords_now_hit_the_artifact(self, client):
        """用台账发现本缺陷时的原始手法验证：那组关键词必须在产物里命中。"""
        from app.graph.stage_reports import StageReports
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        _mk_action_gate(client, pid, rid, "p0")
        ref = StageReports(pid, "p0").gate_brief(
            stage="p0", what_happened="P0 接入完成", key_artifacts=[], risks=[],
            honest_notes="", validation_verdict=None, run_id=rid)
        text = (workspace_service.workspace_path(pid) / ref).read_text("utf-8")
        hits = [k for k in _LEDGER_KEYWORDS if k in text]
        assert hits, f"台账关键词全部零命中（与缺陷发现时相同）：{_LEDGER_KEYWORDS}"

    def test_no_held_actions_leaves_honest_notes_untouched(self, client):
        """无挂起动作时不得凭空添噪（行为不变）。"""
        from app.graph.stage_reports import StageReports
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        ref = StageReports(pid, "p1").gate_brief(
            stage="p1", what_happened="P1 建档完成", key_artifacts=[], risks=[],
            honest_notes="原有说明", validation_verdict=None, run_id=rid)
        doc = json.loads((workspace_service.workspace_path(pid) / ref).read_text("utf-8"))
        assert doc["honest_notes"] == "原有说明"
        assert doc["held_actions"] == []
        assert doc["held_action_count"] == 0


class TestValidationReportSurfacesHeldActions:
    def test_validation_dict_carries_held_actions(self):
        from app.services.validation_agent import ValidationResult
        r = ValidationResult(stage="p0", passed=True, verdict="accepted",
                            held_actions=[{"gate_id": "gate-zzz"}])
        d = r.to_dict()
        assert d["held_action_count"] == 1
        assert d["held_actions"][0]["gate_id"] == "gate-zzz"

    def test_held_actions_do_not_flip_verdict(self, client):
        """解除条件 ④ 的正面反锁：动作被 fail-closed 拦下 ≠ 阶段失败。

        `action_approval` Gate 按设计不阻断阶段推进（公理 6）。若哪天有人"顺手"把
        held_actions 接进 passed/issues，本例会立刻变红。
        """
        from app.services.validation_agent import ValidationAgent
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        _mk_action_gate(client, pid, rid, "p0")
        workspace_service.init_workspace(pid)
        agent = ValidationAgent("p0", pid, rid)
        review = agent.validate({"status": "completed"})
        res = agent.last_result
        assert res is not None
        assert len(res.held_actions) == 1, res.held_actions
        # 挂起动作既不进 issues，也不改 verdict/passed
        assert all(i.get("type") != "held_action" for i in res.issues)
        assert res.passed == review.passed
        held_check = [c for c in res.checks if c["item"].startswith("挂起动作可见性")]
        assert len(held_check) == 1
        assert "因待审批未执行" in held_check[0]["reason"]

    def test_validation_report_on_disk_has_the_section(self, client):
        from app.services.validation_agent import ValidationAgent
        pid = _mk_project(client)
        rid = _mk_run(client, pid)
        gid = _mk_action_gate(client, pid, rid, "p0")
        workspace_service.init_workspace(pid)
        ValidationAgent("p0", pid, rid).validate({"status": "completed"})
        p = workspace_service.workspace_path(pid) / "artifacts/p0/p0_validation.json"
        assert p.exists(), "验收报告未落盘"
        doc = json.loads(p.read_text("utf-8"))
        assert doc["held_action_count"] == 1
        assert doc["held_actions"][0]["gate_id"] == gid
