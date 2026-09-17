"""R12-3-C4 P5 硬必需槽位真实验证服务测试。

覆盖 P5VerificationService.verify_all_hard_required()：
  1. output_code 存在且非空 → validated
  2. patches 存在且与 output_code 对应 → validated
  3. P4 Evidence basis 真实 → validated
  4. P4 summary 可解析 → validated
  5. P4→P5 Gate approved → validated
  缺失任一 → validation_failed / evidence_gap（不伪造 completed）
  RealP5Handler 接入真实验证（硬必需槽位基于真实文件状态）。
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.p5_input_service import P4InputFacts
from app.services.p5_verification_service import (
    P5VerificationService, SlotVerificationResult,
)
from app.services.p5_validation_plan import P5SlotStatus, can_mark_completed, create_p5_validation_plan


# ── helpers ───────────────────────────────────────────────────────────────

def _make_ws(project_id: str, tmp: Path) -> Path:
    ws = tmp / "projects" / project_id
    for d in ["output_code", "patches", "artifacts", "evidence"]:
        (ws / d).mkdir(parents=True, exist_ok=True)
    return ws


@ pytest.fixture
def verify_svc():
    return P5VerificationService(tracer=MagicMock(), auditor=MagicMock(), aet=MagicMock())


# ── 1. output_code 存在且非空 ──────────────────────────────────────────────

class TestVerifyOutputCode:

    def test_output_code_exists_and_non_empty(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        (ws / "output_code" / "migrate.py").write_text("# real code", encoding="utf-8")
        p4 = P4InputFacts(project_id="p1", run_id="r",
                          output_code_refs=["output_code/migrate.py"])
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_output_code_exists("p1", p4)
        assert r.passed is True
        assert r.status == P5SlotStatus.VALIDATED

    def test_output_code_missing(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        p4 = P4InputFacts(project_id="p1", run_id="r",
                          output_code_refs=["output_code/gone.py"])
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_output_code_exists("p1", p4)
        assert r.passed is False
        assert r.status == P5SlotStatus.VALIDATION_FAILED
        assert any(i["type"] == "output_code_missing" for i in r.issues)

    def test_output_code_empty(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        (ws / "output_code" / "empty.py").write_text("", encoding="utf-8")
        p4 = P4InputFacts(project_id="p1", run_id="r",
                          output_code_refs=["output_code/empty.py"])
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_output_code_exists("p1", p4)
        assert r.passed is False


# ── 2. patches 存在且与 output_code 对应 ───────────────────────────────────

class TestVerifyPatches:

    def test_patches_exist_correlated(self, verify_svc, tmp_path):
        # R17.5-P5-R3 GAP-P5-4：patches_exist 确定性槽位只核验【存在+非空】，
        # 不再用 len(patch)<=len(output) 计数启发式（correlation_ok）当门禁——
        # 那会把合法重写式迁移误判 patch_output_mismatch。source→target 结构对应改由
        # LLM advisory 的 structure_mapping 产出（此处只留中立事实 patch_output_ratio）。
        ws = _make_ws("p1", tmp_path)
        (ws / "output_code" / "x.py").write_text("x", encoding="utf-8")
        (ws / "patches" / "tn-001.diff").write_text("diff content", encoding="utf-8")
        p4 = P4InputFacts(project_id="p1", run_id="r",
                          output_code_refs=["output_code/x.py"],
                          patch_refs=["patches/tn-001.diff"])
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_patches_exist("p1", p4)
        assert r.passed is True
        assert "correlation_ok" not in r.details  # 计数启发式已废除
        assert r.details["correspondence_assessed_by"] == "llm_structure_mapping_advisory"
        assert r.details["patch_output_ratio"] is not None

    def test_patches_missing(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        (ws / "output_code" / "x.py").write_text("x", encoding="utf-8")
        p4 = P4InputFacts(project_id="p1", run_id="r",
                          output_code_refs=["output_code/x.py"],
                          patch_refs=["patches/missing.diff"])
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_patches_exist("p1", p4)
        assert r.passed is False


# ── 3. P4 Evidence basis 真实（sha256 校验）────────────────────────────────

class TestVerifyEvidenceReal:

    def test_evidence_sha256_matches(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        code_file = ws / "output_code" / "x.py"
        code_file.write_text("real code content", encoding="utf-8")
        sha = __import__("hashlib").sha256(code_file.read_bytes()).hexdigest()
        # Write evidence JSON with matching sha256
        ev = {"evidence_id": "ev-001", "stage": "p4", "output_code_ref": "output_code/x.py",
              "output_sha256": sha, "status": "validated"}
        (ws / "evidence" / "ev-001.json").write_text(json.dumps(ev), encoding="utf-8")
        p4 = P4InputFacts(project_id="p1", run_id="r", evidence_refs=["ev-001"])
        verify_svc.aet.list_evidence.return_value = [ev]
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_p4_evidence_real("p1", p4)
        assert r.passed is True
        assert r.status == P5SlotStatus.VALIDATED

    def test_evidence_sha256_mismatch(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        code_file = ws / "output_code" / "x.py"
        code_file.write_text("real code content", encoding="utf-8")
        # Evidence claims wrong sha256
        ev = {"evidence_id": "ev-001", "stage": "p4", "output_code_ref": "output_code/x.py",
              "output_sha256": "WRONG-SHA256", "status": "validated"}
        (ws / "evidence" / "ev-001.json").write_text(json.dumps(ev), encoding="utf-8")
        p4 = P4InputFacts(project_id="p1", run_id="r", evidence_refs=["ev-001"])
        verify_svc.aet.list_evidence.return_value = [ev]
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_p4_evidence_real("p1", p4)
        assert r.passed is False
        assert any(i["type"] == "evidence_basis_invalid" for i in r.issues)

    def test_no_evidence_refs(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        p4 = P4InputFacts(project_id="p1", run_id="r", evidence_refs=[])
        verify_svc.aet = None  # no aet
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_p4_evidence_real("p1", p4)
        assert r.passed is False
        assert r.status == P5SlotStatus.EVIDENCE_GAP

    # ── R21 / B-R20-EVIDENCE-SHA-STALE ──────────────────────────────────────

    def test_evidence_superseded_by_overwrite_still_passes(self, verify_svc, tmp_path):
        """核心场景：同一文件被两次写入产生两条 Evidence（模拟多节点先后覆写）。

        较早那条的 sha256 是覆写前旧版本的 hash，跟"现在"的文件内容自然不一致——
        但它已被标记 superseded_by，应被跳过；只用最新（未被标记）的那条做校验，
        且它的 sha256 与当前文件内容一致 → 整体判通过。
        """
        ws = _make_ws("p1", tmp_path)
        code_file = ws / "output_code" / "x.py"
        code_file.write_text("version 2 content (overwritten by a later node)",
                             encoding="utf-8")
        sha_v2 = __import__("hashlib").sha256(code_file.read_bytes()).hexdigest()
        old_ev = {"evidence_id": "ev-001", "stage": "p4", "output_code_ref": "output_code/x.py",
                  "output_sha256": "sha-of-version-1-now-stale", "status": "validated",
                  "superseded_by": "ev-002"}
        new_ev = {"evidence_id": "ev-002", "stage": "p4", "output_code_ref": "output_code/x.py",
                  "output_sha256": sha_v2, "status": "validated"}
        p4 = P4InputFacts(project_id="p1", run_id="r", evidence_refs=["ev-001", "ev-002"])
        verify_svc.aet.list_evidence.return_value = [old_ev, new_ev]
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_p4_evidence_real("p1", p4)
        assert r.passed is True
        assert r.status == P5SlotStatus.VALIDATED
        assert r.details["superseded"] == ["ev-001"]
        assert r.details["verified"] == ["ev-002"]
        assert r.details["invalid_basis"] == []

    def test_latest_evidence_tampered_still_fails_despite_history(self, verify_svc, tmp_path):
        """反例（最重要）：存在被 superseded 的历史记录时，只要最新（未被标记）
        那条 Evidence 的 sha256 跟文件实际内容不一致（真实篡改），校验仍须判失败。

        "跳过历史记录" 绝不能变成 "只要有任意一条历史记录 sha256 匹配就算过"。
        """
        ws = _make_ws("p1", tmp_path)
        code_file = ws / "output_code" / "x.py"
        code_file.write_text("version 2 content, then tampered directly on disk",
                             encoding="utf-8")
        sha_v1 = __import__("hashlib").sha256(b"version 1 content").hexdigest()
        old_ev = {"evidence_id": "ev-001", "stage": "p4", "output_code_ref": "output_code/x.py",
                  "output_sha256": sha_v1, "status": "validated",
                  "superseded_by": "ev-002"}
        # 最新记录声称的 sha256 跟文件当前（被篡改后的）内容不一致。
        new_ev = {"evidence_id": "ev-002", "stage": "p4", "output_code_ref": "output_code/x.py",
                  "output_sha256": "TAMPERED-SHA-DOES-NOT-MATCH-CURRENT-FILE",
                  "status": "validated"}
        p4 = P4InputFacts(project_id="p1", run_id="r", evidence_refs=["ev-001", "ev-002"])
        verify_svc.aet.list_evidence.return_value = [old_ev, new_ev]
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_p4_evidence_real("p1", p4)
        assert r.passed is False
        assert r.status == P5SlotStatus.VALIDATION_FAILED
        assert r.details["superseded"] == ["ev-001"]
        assert r.details["invalid_basis"] == ["ev-002"]
        assert any(i["type"] == "evidence_basis_invalid" for i in r.issues)

    def test_single_evidence_no_supersede_history_unchanged(self, verify_svc, tmp_path):
        """只有一条 Evidence（没有任何覆写历史）时，行为跟改动前完全一致——
        无 superseded_by 字段的记录正常参与 sha256 校验，details["superseded"] 为空。
        """
        ws = _make_ws("p1", tmp_path)
        code_file = ws / "output_code" / "x.py"
        code_file.write_text("only version", encoding="utf-8")
        sha = __import__("hashlib").sha256(code_file.read_bytes()).hexdigest()
        ev = {"evidence_id": "ev-001", "stage": "p4", "output_code_ref": "output_code/x.py",
              "output_sha256": sha, "status": "validated"}
        p4 = P4InputFacts(project_id="p1", run_id="r", evidence_refs=["ev-001"])
        verify_svc.aet.list_evidence.return_value = [ev]
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_p4_evidence_real("p1", p4)
        assert r.passed is True
        assert r.details["superseded"] == []
        assert r.details["verified"] == ["ev-001"]


# ── 3b. aet_service.write_evidence() supersede 标记（R21 / B-R20-EVIDENCE-SHA-STALE）─

class TestWriteEvidenceSupersedeMarking:
    """AETService.write_evidence() 在同一 output_code_ref 被新 Evidence 覆盖式
    引用时，应把更早引用同一文件的、尚未被标记的 Evidence 记录打上
    superseded_by=<新 evidence_id>，供 P5 验证跳过陈旧的 sha256 基准。
    """

    def test_older_evidence_for_same_file_gets_superseded(self, tmp_path):
        from app.services.aet_service import AETService

        with patch("app.services.aet_service.workspace_path", return_value=tmp_path):
            aet = AETService(None)
            aet.write_evidence(
                "p1", "ev-001", "execution_output", stage="p4",
                extra={"output_code_ref": "output_code/x.py", "output_sha256": "sha-v1"})
            aet.write_evidence(
                "p1", "ev-002", "execution_output", stage="p4",
                extra={"output_code_ref": "output_code/x.py", "output_sha256": "sha-v2"})

            all_ev = {e["evidence_id"]: e for e in aet.list_evidence("p1", stage="p4")}
        assert all_ev["ev-001"]["superseded_by"] == "ev-002"
        assert all_ev["ev-001"]["output_sha256"] == "sha-v1"  # 历史记录内容本身不被改写
        assert "superseded_by" not in all_ev["ev-002"]

    def test_single_write_no_prior_reference_stays_unmarked(self, tmp_path):
        """回归锁：没有任何历史引用同一文件时，新写入的 Evidence 不带 superseded_by。"""
        from app.services.aet_service import AETService

        with patch("app.services.aet_service.workspace_path", return_value=tmp_path):
            aet = AETService(None)
            aet.write_evidence(
                "p1", "ev-001", "execution_output", stage="p4",
                extra={"output_code_ref": "output_code/x.py", "output_sha256": "sha-v1"})
            ev = aet.get_evidence("ev-001", "p1")
        assert "superseded_by" not in ev

    def test_unrelated_file_reference_not_marked_superseded(self, tmp_path):
        """引用不同文件路径的 Evidence 互不影响——只有同一 output_code_ref 才会被标记。"""
        from app.services.aet_service import AETService

        with patch("app.services.aet_service.workspace_path", return_value=tmp_path):
            aet = AETService(None)
            aet.write_evidence(
                "p1", "ev-001", "execution_output", stage="p4",
                extra={"output_code_ref": "output_code/a.py", "output_sha256": "sha-a"})
            aet.write_evidence(
                "p1", "ev-002", "execution_output", stage="p4",
                extra={"output_code_ref": "output_code/b.py", "output_sha256": "sha-b"})
            ev1 = aet.get_evidence("ev-001", "p1")
        assert "superseded_by" not in ev1

    def test_chained_overwrites_only_marks_the_most_recent_predecessor(self, tmp_path):
        """三次覆写：每次新写入只标记"当前尚未被标记"的旧记录，已被标记的历史记录
        不会被后续写入重新改写（防止 superseded_by 链被覆盖成最后一个之外的值）。
        """
        from app.services.aet_service import AETService

        with patch("app.services.aet_service.workspace_path", return_value=tmp_path):
            aet = AETService(None)
            aet.write_evidence("p1", "ev-001", "execution_output", stage="p4",
                               extra={"output_code_ref": "output_code/x.py",
                                      "output_sha256": "sha-v1"})
            aet.write_evidence("p1", "ev-002", "execution_output", stage="p4",
                               extra={"output_code_ref": "output_code/x.py",
                                      "output_sha256": "sha-v2"})
            aet.write_evidence("p1", "ev-003", "execution_output", stage="p4",
                               extra={"output_code_ref": "output_code/x.py",
                                      "output_sha256": "sha-v3"})
            all_ev = {e["evidence_id"]: e for e in aet.list_evidence("p1", stage="p4")}
        assert all_ev["ev-001"]["superseded_by"] == "ev-002"
        assert all_ev["ev-002"]["superseded_by"] == "ev-003"
        assert "superseded_by" not in all_ev["ev-003"]


# ── 4. P4 summary 可解析 ───────────────────────────────────────────────────

class TestVerifySummary:

    def test_summary_readable(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        summary = {"stage": "p4", "graph_status": "completed",
                   "change_manifest": [{"path": "output_code/x.py"}]}
        p4 = P4InputFacts(project_id="p1", run_id="r", p4_execution_summary=summary)
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_p4_summary_readable("p1", p4)
        assert r.passed is True
        assert r.details["parsed"] is True

    def test_no_summary(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        p4 = P4InputFacts(project_id="p1", run_id="r", p4_execution_summary=None)
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            r = verify_svc._verify_p4_summary_readable("p1", p4)
        assert r.passed is False
        assert r.status == P5SlotStatus.EVIDENCE_GAP


# ── 5. P4→P5 Gate approved ─────────────────────────────────────────────────

class TestVerifyGate:

    def test_gate_approved(self, verify_svc, tmp_path):
        p4 = P4InputFacts(project_id="p1", run_id="r", p4_to_p5_gate_status="approved")
        r = verify_svc._verify_p4_p5_gate(p4)
        assert r.passed is True

    def test_gate_not_approved(self, verify_svc, tmp_path):
        p4 = P4InputFacts(project_id="p1", run_id="r", p4_to_p5_gate_status="waiting_decision")
        r = verify_svc._verify_p4_p5_gate(p4)
        assert r.passed is False


# ── 完整 5 个硬必需槽位验证 ───────────────────────────────────────────────

class TestVerifyAllHardRequired:

    def test_all_5_passed(self, verify_svc, tmp_path):
        ws = _make_ws("p1", tmp_path)
        code_file = ws / "output_code" / "x.py"
        code_file.write_text("real", encoding="utf-8")
        sha = __import__("hashlib").sha256(code_file.read_bytes()).hexdigest()
        (ws / "patches" / "tn-001.diff").write_text("diff", encoding="utf-8")
        ev = {"evidence_id": "ev-001", "stage": "p4", "output_code_ref": "output_code/x.py",
              "output_sha256": sha, "status": "validated"}
        (ws / "evidence" / "ev-001.json").write_text(json.dumps(ev), encoding="utf-8")
        summary = {"stage": "p4", "graph_status": "completed", "change_manifest": []}

        p4 = P4InputFacts(project_id="p1", run_id="r",
                          blocked=False, p4_to_p5_gate_status="approved",
                          output_code_refs=["output_code/x.py"],
                          patch_refs=["patches/tn-001.diff"],
                          evidence_refs=["ev-001"],
                          p4_execution_summary=summary)
        verify_svc.aet.list_evidence.return_value = [ev]

        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            results = verify_svc.verify_all_hard_required("p1", p4)

        assert len(results) == 5
        passed = sum(1 for r in results if r.passed)
        assert passed == 5, f"应全通过，实际 {passed}/5: {[(r.slot_id, r.status) for r in results]}"

    def test_one_slot_fails_blocks_completion(self, verify_svc, tmp_path):
        """缺失任一硬必需槽位 → P5 不得 completed（D-105① 硬约束）。"""
        ws = _make_ws("p1", tmp_path)
        # output_code 缺失 → 失败
        (ws / "patches" / "tn-001.diff").write_text("diff", encoding="utf-8")
        summary = {"stage": "p4", "graph_status": "completed", "change_manifest": []}

        p4 = P4InputFacts(project_id="p1", run_id="r",
                          blocked=False, p4_to_p5_gate_status="approved",
                          output_code_refs=["output_code/gone.py"],  # 不存在
                          patch_refs=["patches/tn-001.diff"],
                          evidence_refs=[], p4_execution_summary=summary)
        verify_svc.aet.list_evidence.return_value = []

        with patch("app.services.p5_verification_service.workspace_path", return_value=ws):
            results = verify_svc.verify_all_hard_required("p1", p4)

        output_slot = next(r for r in results if r.slot_id == "output_code_exists")
        assert output_slot.passed is False

        # 验证 can_mark_completed = False（硬约束）
        plan = create_p5_validation_plan("p1", "r")
        for vr in results:
            slot = plan.get_slot(vr.slot_id)
            if slot:
                from app.services.p5_validation_plan import transition_slot_status
                # PENDING → IN_PROGRESS → 终态（合法路径）
                transition_slot_status(slot, P5SlotStatus.IN_PROGRESS)
                transition_slot_status(slot, vr.status)
        can, _ = can_mark_completed(plan)
        assert can is False  # output_code 不合格 → P5 不得 completed
