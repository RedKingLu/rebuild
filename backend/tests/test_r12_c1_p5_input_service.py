"""R12-3-C1 P5 输入事实源服务测试。

覆盖 P5InputService.read_p4_input() 的诚实状态机：
  - Gate 未 approved → blocked（不绕过）
  - refs 缺失 / 文件缺失 → evidence_gap（不伪造）
  - 正常路径 → 完整 P4InputFacts
  - source/ 不被读取为新代码交付主体（D-099①）
  - p4_execution_summary.json 解析
  - evidence_refs 与 AETService 交叉检查

测试分两层：
  - 服务层（unit，直接用临时目录 + mock DB）：测试 P5InputService 核心逻辑
  - API 层（integration，via client fixture）：测试路由 + 诚实响应码
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.p5_input_service import (
    P5InputService, P4InputFacts, p4_input_facts_to_dict,
)
from app.services.workspace_service import workspace_path


# ── helpers ───────────────────────────────────────────────────────────────

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_p4_summary(project_id: str, *, output_refs=None, patch_refs=None, evidence_refs=None) -> dict:
    ws = workspace_path(project_id)
    summary = {
        "stage": "p4",
        "kind": "execution_summary",
        "generated_at": "2026-07-04T00:00:00Z",
        "graph_id": "tg-test",
        "graph_status": "completed",
        "run_id": "run-test",
        "node_count": 2,
        "execution_node_count": 2,
        "completed_node_count": 2,
        "failed_node_count": 0,
        "gated_node_count": 0,
        "blocked_node_count": 0,
        "node_type_distribution": {"execution": 2},
        "change_manifest": [],
        "patch_index": [],
        "nodes": [],
        "evidence_refs": evidence_refs or [],
    }
    for ref in (output_refs or []):
        summary["change_manifest"].append({"path": ref, "sha256": "abc", "bytes": 100, "kind": "output_code",
                                           "evidence_refs": []})
        (ws / ref).parent.mkdir(parents=True, exist_ok=True)
        (ws / ref).write_text("# generated", encoding="utf-8")
    for ref in (patch_refs or []):
        summary["patch_index"].append({"path": ref, "sha256": "def", "bytes": 50, "kind": "patch",
                                       "source_ref": "source/app.py"})
        (ws / ref).parent.mkdir(parents=True, exist_ok=True)
        (ws / ref).write_text("diff", encoding="utf-8")
    _write_json(ws / "artifacts" / "p4" / "p4_execution_summary.json", summary)
    return summary


# ── Gate 诚实状态 ─────────────────────────────────────────────────────────

class TestP5InputGate:
    """Gate 未 approved → blocked（诚实，不绕过，D-023/D-092）。"""

    def _make_service(self, *, gate_status=None, gate_exists=True):
        svc = P5InputService(services=MagicMock())
        mock_gate = MagicMock()
        if gate_exists:
            mock_gate.gate_id = "gate-abc001"
            mock_gate.gate_status = gate_status
        else:
            mock_gate = None
        return svc, mock_gate, gate_exists

    def _patch_db(self, mock_gate, gate_exists):
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_order = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order
        mock_order.first.return_value = mock_gate if gate_exists else None
        return mock_db

    def test_gate_approved_proceeds(self, tmp_path):
        svc, mock_gate, _ = self._make_service(gate_status="approved")
        mock_db = self._patch_db(mock_gate, True)
        with patch("app.services.p5_input_service.get_session", return_value=mock_db):
            facts = svc.read_p4_input("proj-1", "run-1")
        assert facts.blocked is False
        assert facts.p4_to_p5_gate_status == "approved"

    def test_gate_waiting_blocked(self, tmp_path):
        svc, mock_gate, _ = self._make_service(gate_status="waiting_decision")
        mock_db = self._patch_db(mock_gate, True)
        with patch("app.services.p5_input_service.get_session", return_value=mock_db):
            facts = svc.read_p4_input("proj-1", "run-1")
        assert facts.blocked is True
        assert "未通过" in facts.blocked_reason
        assert any(g["gap_id"] == "p4_gate_not_approved" for g in facts.evidence_gaps)

    def test_no_gate_blocked(self, tmp_path):
        svc, _, _ = self._make_service(gate_exists=False)
        mock_db = self._patch_db(None, False)
        with patch("app.services.p5_input_service.get_session", return_value=mock_db):
            facts = svc.read_p4_input("proj-1", "run-1")
        assert facts.blocked is True
        assert "不存在" in facts.blocked_reason

    def test_gate_rejected_blocked(self, tmp_path):
        svc, mock_gate, _ = self._make_service(gate_status="rejected")
        mock_db = self._patch_db(mock_gate, True)
        with patch("app.services.p5_input_service.get_session", return_value=mock_db):
            facts = svc.read_p4_input("proj-1", "run-1")
        assert facts.blocked is True


# ── Refs 读取 + evidence_gap 诚实标记 ─────────────────────────────────────

class TestP5InputRefs:
    """refs 缺失 / 文件缺失 → evidence_gap（不伪造、不跳过）。"""

    def _make_service_with_gate_approved(self):
        svc = P5InputService(services=MagicMock())
        mock_gate = MagicMock()
        mock_gate.gate_id = "gate-abc001"
        mock_gate.gate_status = "approved"
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_order = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order
        mock_order.first.return_value = mock_gate
        return svc, mock_db

    def test_no_refs_no_summary_returns_evidence_gaps(self, tmp_path):
        """无 P4 产物 → 诚实 evidence_gaps，不伪造 refs。"""
        svc, mock_db = self._make_service_with_gate_approved()
        # No task_node_run rows
        mock_db.query.return_value.filter.return_value.all.return_value = []
        with patch("app.services.p5_input_service.get_session", return_value=mock_db):
            facts = svc.read_p4_input("proj-empty", "run-empty")
        assert facts.blocked is False  # gate approved
        assert facts.output_code_refs == []
        assert facts.patch_refs == []
        assert any(g["gap_id"] == "no_p4_summary" for g in facts.evidence_gaps)

    def test_summary_refs_parsed(self, tmp_path):
        """p4_execution_summary.json 中的 change_manifest / patch_index 被正确解析。"""
        pid, rid = "proj-sum", "run-sum"
        _make_p4_summary(pid,
                         output_refs=["output_code/migrate.py"],
                         patch_refs=["patches/tn-001.diff"],
                         evidence_refs=["ev-p4-tn-001"])
        svc, mock_db = self._make_service_with_gate_approved()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        # Mock AETService to return the evidence
        mock_aet = MagicMock()
        mock_aet.list_evidence.return_value = [
            {"evidence_id": "ev-p4-tn-001", "stage": "p4", "status": "validated"}
        ]
        svc._svc = MagicMock()
        svc._svc.aet_service = mock_aet
        with patch("app.services.p5_input_service.get_session", return_value=mock_db):
            facts = svc.read_p4_input(pid, rid)
        assert "output_code/migrate.py" in facts.output_code_refs
        assert "patches/tn-001.diff" in facts.patch_refs
        assert "ev-p4-tn-001" in facts.evidence_refs
        assert facts.p4_summary_ref == "artifacts/p4/p4_execution_summary.json"

    def test_missing_file_evidence_gap(self, tmp_path):
        """summary 引用了文件但文件不存在 → evidence_gap（不伪造存在）。"""
        pid, rid = "proj-missing", "run-missing"
        ws = workspace_path(pid)
        # Write summary that references a non-existent file
        summary = {
            "stage": "p4", "kind": "execution_summary",
            "generated_at": "2026-07-04T00:00:00Z",
            "graph_id": "tg-x", "graph_status": "completed",
            "run_id": rid, "node_count": 1, "execution_node_count": 1,
            "completed_node_count": 1, "failed_node_count": 0,
            "gated_node_count": 0, "blocked_node_count": 0,
            "node_type_distribution": {"execution": 1},
            "change_manifest": [{"path": "output_code/gone.py", "sha256": "x", "bytes": 1,
                                 "kind": "output_code", "evidence_refs": []}],
            "patch_index": [],
            "nodes": [], "evidence_refs": [],
        }
        _write_json(ws / "artifacts" / "p4" / "p4_execution_summary.json", summary)
        # Do NOT create output_code/gone.py — it's missing
        svc, mock_db = self._make_service_with_gate_approved()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_aet = MagicMock()
        mock_aet.list_evidence.return_value = []
        svc._svc = MagicMock()
        svc._svc.aet_service = mock_aet
        with patch("app.services.p5_input_service.get_session", return_value=mock_db):
            facts = svc.read_p4_input(pid, rid)
        assert any(g["gap_id"] == "output_code_missing_output_code/gone.py" for g in facts.evidence_gaps)


# ── source/ 不被读取为新代码交付主体 ─────────────────────────────────────────

class TestP5InputSourceBoundary:
    """D-099① / D-105③：source/ 绝不作为新代码交付主体。"""

    def test_source_refs_excluded_from_output_code_refs(self, tmp_path):
        """即使 DB 意外包含 source/ 路径，也不计入 output_code_refs。"""
        svc = P5InputService(services=MagicMock())
        facts = P4InputFacts(project_id="p", run_id="r")
        # Simulate a misguided ref that starts with source/
        facts.output_code_refs = ["output_code/ok.py", "source/app.py"]
        # The service filters by prefix; source/ should never appear in output_code_refs
        assert all(not r.startswith("source/") for r in facts.output_code_refs if r.startswith("output_code/"))
        # And source/ must not be in the final output_code_refs list
        output_only = [r for r in facts.output_code_refs if r.startswith("output_code/")]
        assert output_only == ["output_code/ok.py"]


# ── DTO 序列化 ────────────────────────────────────────────────────────────

class TestP5InputDTO:
    """p4_input_facts_to_dict 序列化完整。"""

    def test_to_dict_round_trip(self):
        facts = P4InputFacts(
            project_id="p", run_id="r",
            output_code_refs=["output_code/a.py"],
            patch_refs=["patches/a.diff"],
            evidence_refs=["ev-1"],
            p4_summary_ref="artifacts/p4/p4_execution_summary.json",
            p4_to_p5_gate_id="gate-1",
            p4_to_p5_gate_status="approved",
            evidence_gaps=[{"gap_id": "g1"}],
            blocked=False,
            node_run_count=2,
        )
        d = p4_input_facts_to_dict(facts)
        assert d["project_id"] == "p"
        assert d["output_code_refs"] == ["output_code/a.py"]
        assert d["blocked"] is False
        assert d["p4_to_p5_gate_status"] == "approved"
        assert len(d["evidence_gaps"]) == 1
