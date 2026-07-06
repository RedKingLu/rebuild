"""R12-3-C2 P5 validation plan 与 Evidence 状态机测试。

覆盖：
  - create_p5_validation_plan() 创建完整的 10 槽位计划
  - transition_slot_status() 合法 / 非法状态转换
  - can_mark_completed() D-105① 硬约束判定
  - evidence_gap / needs_user_input 一等状态
  - 命令不可识别场景 → evidence_gap + needs_user_input（不伪造通过）
  - Gate 接受风险后 evidence_gap 视为"有条件下通过"
  - plan_to_dict() 序列化完整
"""

import pytest

from app.services.p5_validation_plan import (
    P5SlotType, P5SlotStatus, P5SlotId,
    P5ValidationSlot, P5ValidationPlan,
    HARD_REQUIRED_SLOTS, CONDITIONAL_SLOTS, SLOT_TYPE_MAP,
    VALID_TRANSITIONS,
    transition_slot_status, can_mark_completed,
    create_p5_validation_plan, slot_to_dict, plan_to_dict,
)


# ── 10 槽位完整性 ─────────────────────────────────────────────────────────

class TestP5ValidationPlanCreation:
    """create_p5_validation_plan 创建完整的 10 槽位计划。"""

    def test_10_slots_created(self):
        plan = create_p5_validation_plan("p1", "r1")
        assert len(plan.slots) == 10

    def test_all_slots_start_pending(self):
        plan = create_p5_validation_plan("p1", "r1")
        for s in plan.slots:
            assert s.status == P5SlotStatus.PENDING

    def test_hard_required_count(self):
        """硬必需 5 个（output_code/patches/evidence/summary/gate）。"""
        plan = create_p5_validation_plan("p1", "r1")
        hard = plan.get_slots_by_type(P5SlotType.HARD_REQUIRED.value)
        assert len(hard) == len(HARD_REQUIRED_SLOTS)
        assert len(hard) == 5

    def test_conditional_count(self):
        """有条件必需 4 个（build/run/test/static）。"""
        plan = create_p5_validation_plan("p1", "r1")
        cond = plan.get_slots_by_type(P5SlotType.CONDITIONAL.value)
        assert len(cond) == len(CONDITIONAL_SLOTS)
        assert len(cond) == 4

    def test_enhanced_count(self):
        """增强项 1 个（source_unmodified）。"""
        plan = create_p5_validation_plan("p1", "r1")
        enh = plan.get_slots_by_type(P5SlotType.ENHANCED.value)
        assert len(enh) == 1
        assert enh[0].slot_id == P5SlotId.SOURCE_UNMODIFIED.value

    def test_all_slot_ids_unique(self):
        plan = create_p5_validation_plan("p1", "r1")
        ids = [s.slot_id for s in plan.slots]
        assert len(ids) == len(set(ids))

    def test_slot_descriptions_populated(self):
        plan = create_p5_validation_plan("p1", "r1")
        for s in plan.slots:
            assert s.description, f"槽位 {s.slot_id} 描述为空"


# ── 状态转换 ──────────────────────────────────────────────────────────────

class TestSlotStatusTransitions:
    """transition_slot_status 合法 / 非法转换。"""

    def test_pending_to_in_progress(self):
        slot = P5ValidationSlot(slot_id="s1", slot_type=P5SlotType.HARD_REQUIRED.value)
        transition_slot_status(slot, P5SlotStatus.IN_PROGRESS)
        assert slot.status == P5SlotStatus.IN_PROGRESS

    def test_in_progress_to_validated(self):
        slot = P5ValidationSlot(slot_id="s1", slot_type=P5SlotType.HARD_REQUIRED.value,
                                status=P5SlotStatus.IN_PROGRESS)
        transition_slot_status(slot, P5SlotStatus.VALIDATED)
        assert slot.status == P5SlotStatus.VALIDATED

    def test_in_progress_to_evidence_gap(self):
        """验证动作发现证据缺失 → evidence_gap（合法）。"""
        slot = P5ValidationSlot(slot_id="s1", slot_type=P5SlotType.CONDITIONAL.value,
                                status=P5SlotStatus.IN_PROGRESS)
        transition_slot_status(slot, P5SlotStatus.EVIDENCE_GAP)
        assert slot.status == P5SlotStatus.EVIDENCE_GAP

    def test_in_progress_to_needs_user_input(self):
        """命令不可识别 → needs_user_input（合法）。"""
        slot = P5ValidationSlot(slot_id="s1", slot_type=P5SlotType.CONDITIONAL.value,
                                status=P5SlotStatus.IN_PROGRESS)
        transition_slot_status(slot, P5SlotStatus.NEEDS_USER_INPUT)
        assert slot.status == P5SlotStatus.NEEDS_USER_INPUT

    def test_validated_cannot_transition_back(self):
        """validated 是终态（除 superseded 外不可转）。"""
        slot = P5ValidationSlot(slot_id="s1", slot_type=P5SlotType.HARD_REQUIRED.value,
                                status=P5SlotStatus.VALIDATED)
        with pytest.raises(ValueError, match="非法状态转换"):
            transition_slot_status(slot, P5SlotStatus.IN_PROGRESS)

    def test_pending_cannot_jump_to_validated(self):
        """pending → validated 是非法跳跃（必须经 in_progress）。"""
        slot = P5ValidationSlot(slot_id="s1", slot_type=P5SlotType.HARD_REQUIRED.value,
                                status=P5SlotStatus.PENDING)
        with pytest.raises(ValueError, match="非法状态转换"):
            transition_slot_status(slot, P5SlotStatus.VALIDATED)

    def test_validated_to_superseded(self):
        """validated → superseded 合法（被后续验证替代）。"""
        slot = P5ValidationSlot(slot_id="s1", slot_type=P5SlotType.HARD_REQUIRED.value,
                                status=P5SlotStatus.VALIDATED)
        transition_slot_status(slot, P5SlotStatus.SUPERSEDED)
        assert slot.status == P5SlotStatus.SUPERSEDED


# ── can_mark_completed 硬约束 ──────────────────────────────────────────────

class TestCanMarkCompleted:
    """D-105① 硬约束：P5 completed 必须所有硬必需 validated + 有条件必需 validated 或 Gate 接受。"""

    def test_empty_plan_cannot_complete(self):
        plan = create_p5_validation_plan("p1", "r1")
        can, reason = can_mark_completed(plan)
        assert not can
        assert "硬必需" in reason

    def test_all_hard_required_validated_but_conditional_pending_cannot_complete(self):
        """硬必需全 pass 但 conditional 仍为 pending → 不通过。"""
        plan = create_p5_validation_plan("p1", "r1")
        for sid in HARD_REQUIRED_SLOTS:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        can, reason = can_mark_completed(plan)
        assert not can
        assert "conditional" in reason.lower() or "有条件" in reason

    def test_all_validated_can_complete(self):
        """所有 9 个硬必需+条件槽位 validated → can complete（enhanced 非必须）。"""
        plan = create_p5_validation_plan("p1", "r1")
        for sid in list(HARD_REQUIRED_SLOTS) + list(CONDITIONAL_SLOTS):
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        can, reason = can_mark_completed(plan)
        assert can, f"应通过但被拒：{reason}"

    def test_conditional_evidence_gap_without_gate_accept_cannot_complete(self):
        """conditional evidence_gap 未经 Gate 接受 → 不通过。"""
        plan = create_p5_validation_plan("p1", "r1")
        for sid in HARD_REQUIRED_SLOTS:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        # 一个 conditional 槽位为 evidence_gap（未接受风险）
        build_slot = plan.get_slot(P5SlotId.BUILD_VERIFIED.value)
        build_slot.status = P5SlotStatus.EVIDENCE_GAP
        build_slot.risk_accepted_via_gate = False
        # 其余 conditional validated
        for sid in CONDITIONAL_SLOTS - {P5SlotId.BUILD_VERIFIED}:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        can, reason = can_mark_completed(plan)
        assert not can
        assert "risk_accepted" in reason or "Gate" in reason

    def test_conditional_evidence_gap_with_gate_accept_can_complete(self):
        """conditional evidence_gap 经 Gate 接受风险 → can complete。"""
        plan = create_p5_validation_plan("p1", "r1")
        for sid in HARD_REQUIRED_SLOTS:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        build_slot = plan.get_slot(P5SlotId.BUILD_VERIFIED.value)
        build_slot.status = P5SlotStatus.EVIDENCE_GAP
        build_slot.risk_accepted_via_gate = True
        build_slot.gate_id = "gate-risk-accepted"
        for sid in CONDITIONAL_SLOTS - {P5SlotId.BUILD_VERIFIED}:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        can, reason = can_mark_completed(plan)
        assert can, f"应通过但被拒：{reason}"

    def test_needs_user_input_blocks_completion(self):
        """存在未解决的 needs_user_input → 不通过（D-105① 硬约束）。"""
        plan = create_p5_validation_plan("p1", "r1")
        for sid in HARD_REQUIRED_SLOTS:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        for sid in CONDITIONAL_SLOTS:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        # 加一个未解决的 needs_user_input
        plan.get_slot(P5SlotId.BUILD_VERIFIED.value).status = P5SlotStatus.NEEDS_USER_INPUT
        plan.get_slot(P5SlotId.BUILD_VERIFIED.value).needs_user_input_prompt = "请提供构建命令"
        can, reason = can_mark_completed(plan)
        assert not can
        # needs_user_input 被 conditional 检查捕获（status != validated/accepted）
        assert "未通过" in reason and "BUILD_VERIFIED" in reason


# ── 诚实状态：命令不可识别 ─────────────────────────────────────────────────

class TestHonestCommandNotFound:
    """命令不可识别 → evidence_gap + needs_user_input（不伪造通过）。"""

    def test_build_command_unavailable_produces_evidence_gap(self):
        """系统不能识别构建命令 → evidence_gap + needs_user_input。"""
        plan = create_p5_validation_plan("p1", "r1")
        build = plan.get_slot(P5SlotId.BUILD_VERIFIED.value)
        build.command_available = False
        build.command = None
        # 模拟 C5 发现命令不可识别
        transition_slot_status(build, P5SlotStatus.IN_PROGRESS)
        transition_slot_status(build, P5SlotStatus.NEEDS_USER_INPUT)
        build.needs_user_input_prompt = "无法自动识别项目构建命令，请提供（如 make / mvn package / go build）"
        build.evidence_gap_reason = "项目无标准构建文件（无 Makefile / pom.xml / go.mod）"
        assert build.status == P5SlotStatus.NEEDS_USER_INPUT
        assert build.needs_user_input_prompt

    def test_test_command_unavailable_does_not_fake_completed(self):
        """测试命令不可用时绝不标记 completed。"""
        plan = create_p5_validation_plan("p1", "r1")
        test_slot = plan.get_slot(P5SlotId.TESTS_PASS.value)
        transition_slot_status(test_slot, P5SlotStatus.IN_PROGRESS)
        transition_slot_status(test_slot, P5SlotStatus.EVIDENCE_GAP)
        # 即使所有其他槽位都 validated，evidence_gap 也阻止 completed
        for sid in HARD_REQUIRED_SLOTS:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        for sid in CONDITIONAL_SLOTS - {P5SlotId.TESTS_PASS}:
            plan.get_slot(sid.value).status = P5SlotStatus.VALIDATED
        can, _ = can_mark_completed(plan)
        assert not can  # evidence_gap 阻止


# ── 序列化 ────────────────────────────────────────────────────────────────

class TestSerialization:
    """plan_to_dict / slot_to_dict 序列化完整。"""

    def test_slot_to_dict_all_fields(self):
        slot = P5ValidationSlot(
            slot_id=P5SlotId.BUILD_VERIFIED.value,
            slot_type=P5SlotType.CONDITIONAL.value,
            status=P5SlotStatus.VALIDATED,
            description="真实构建",
            command="make build",
            command_available=True,
            exit_code=0,
            duration_ms=1200,
        )
        d = slot_to_dict(slot)
        assert d["slot_id"] == "build_verified"
        assert d["status"] == "validated"
        assert d["command"] == "make build"
        assert d["exit_code"] == 0

    def test_plan_to_dict_summary_counts(self):
        plan = create_p5_validation_plan("p1", "r1")
        plan.get_slot(P5SlotId.OUTPUT_CODE_EXISTS.value).status = P5SlotStatus.VALIDATED
        plan.get_slot(P5SlotId.BUILD_VERIFIED.value).status = P5SlotStatus.EVIDENCE_GAP
        d = plan_to_dict(plan)
        assert d["summary"]["hard_required_validated"] == 1
        assert d["summary"]["conditional_evidence_gap"] == 1
        assert d["summary"]["hard_required_total"] == 5
        assert d["summary"]["conditional_total"] == 4

    def test_plan_to_dict_can_be_completed_false_initially(self):
        plan = create_p5_validation_plan("p1", "r1")
        d = plan_to_dict(plan)
        assert d["can_be_completed"] is False


# ── 槽位类型映射一致性 ─────────────────────────────────────────────────────

class TestSlotTypeMapping:
    """SLOT_TYPE_MAP 与枚举定义一致。"""

    def test_all_10_slots_mapped(self):
        for sid in P5SlotId:
            assert sid in SLOT_TYPE_MAP, f"{sid} 未映射到类型"

    def test_hard_required_are_5(self):
        assert len([s for s in SLOT_TYPE_MAP.values() if s == P5SlotType.HARD_REQUIRED]) == 5

    def test_conditional_are_4(self):
        assert len([s for s in SLOT_TYPE_MAP.values() if s == P5SlotType.CONDITIONAL]) == 4

    def test_enhanced_is_1(self):
        assert len([s for s in SLOT_TYPE_MAP.values() if s == P5SlotType.ENHANCED]) == 1


# ── 转换规则完整性 ─────────────────────────────────────────────────────────

class TestTransitionRules:
    """VALID_TRANSITIONS 覆盖所有状态（防遗漏）。"""

    def test_all_statuses_have_rule(self):
        for status in P5SlotStatus:
            assert status.value in VALID_TRANSITIONS, f"{status} 无转换规则"

    def test_terminal_states_only_superseded(self):
        """终态（validated/failed/gap/needs_input/not_applicable）不可再转（除 superseded）。"""
        terminal = [
            P5SlotStatus.VALIDATED, P5SlotStatus.VALIDATION_FAILED,
            P5SlotStatus.EVIDENCE_GAP, P5SlotStatus.NEEDS_USER_INPUT,
            P5SlotStatus.NOT_APPLICABLE,
        ]
        for status in terminal:
            allowed = VALID_TRANSITIONS[status]
            assert allowed == {P5SlotStatus.SUPERSEDED} or allowed == set(), \
                f"{status} 应为终态，但允许转换：{allowed}"
