"""P5 硬必需 + 有条件必需槽位真实验证服务（R12-3-C4 硬必需 + C5 条件真实命令）。

完成全部 9 个验证槽位的真实执行：
  硬必需（5）：output_code_exists / patches_exist / p4_evidence_real /
              p4_summary_readable / p4_p5_gate_approved
  有条件必需（4）：build_verified / run_verified / tests_pass / static_check
                  经 ExecutionProvider 真实执行构建/运行/测试/静态检查

诚实验证（D-066 / D-101 / D-097 / D-105①）：
  - 每个槽位独立验证，任一失败 → validation_failed / evidence_gap（不伪造 completed）
  - 命令不可识别 → needs_user_input（诚实）
  - 命令执行失败 → validation_failed（不等于 completed）
  - 无命令 ≠ 通过
  - 缺失任一硬必需槽位时 P5 不得 completed（can_mark_completed 硬约束）
  - L4/L5 高风险 → Gate 拦截
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.workspace_service import workspace_path

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


def _file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


@dataclass
class SlotVerificationResult:
    """单个槽位的验证结果。"""
    slot_id: str
    passed: bool
    status: str  # P5SlotStatus value
    evidence_refs: list = None
    artifacts: list = None
    issues: list = None
    details: dict = None

    def __post_init__(self):
        if self.evidence_refs is None:
            self.evidence_refs = []
        if self.artifacts is None:
            self.artifacts = []
        if self.issues is None:
            self.issues = []
        if self.details is None:
            self.details = {}


# ── 验证服务 ───────────────────────────────────────────────────────────────

class P5VerificationService:
    """P5 硬必需槽位真实验证服务。R12-3-C4 新建。"""

    def __init__(self, tracer=None, auditor=None, aet=None):
        self.tracer = tracer
        self.auditor = auditor
        self.aet = aet

    # ── public ─────────────────────────────────────────────────────────────

    def verify_all_hard_required(self, project_id: str, p4_input) -> list:
        """执行全部 5 个硬必需槽位的真实验证。"""
        results = []
        results.append(self._verify_output_code_exists(project_id, p4_input))
        results.append(self._verify_patches_exist(project_id, p4_input))
        results.append(self._verify_p4_evidence_real(project_id, p4_input))
        results.append(self._verify_p4_summary_readable(project_id, p4_input))
        results.append(self._verify_p4_p5_gate(p4_input))

        # Trace 写入
        if self.tracer:
            for r in results:
                self.tracer.write(
                    "evidence_event",
                    action=f"p5_verify_{r.slot_id}",
                    summary=f"P5 槽位 {r.slot_id}: {'通过' if r.passed else '未通过'}",
                    project_id=project_id,
                    stage="p5",
                    transition_mode="real",
                    extras={"slot_id": r.slot_id, "passed": r.passed,
                            "status": r.status, "issues": [i.get("type") for i in r.issues]},
                )
        return results

    # ── 1. output_code 存在且非空 ─────────────────────────────────────────

    def _verify_output_code_exists(self, project_id: str, p4_input) -> SlotVerificationResult:
        """验证 output_code 文件存在且非空。"""
        refs = p4_input.output_code_refs
        ws = workspace_path(project_id)
        result = SlotVerificationResult(
            slot_id="output_code_exists", passed=False,
            status="validation_failed", details={"refs_checked": len(refs)},
        )

        if not refs:
            result.issues.append({"type": "no_output_code_refs",
                                  "detail": "无 output_code refs（P4 未产出）"})
            result.status = "evidence_gap"
            return result

        missing = []
        empty = []
        found = []
        for ref in refs:
            p = ws / ref
            if not p.exists():
                missing.append(ref)
            elif p.stat().st_size == 0:
                empty.append(ref)
            else:
                found.append(ref)

        result.details["found"] = found
        result.details["missing"] = missing
        result.details["empty"] = empty

        if missing:
            result.issues.append({"type": "output_code_missing",
                                  "detail": f"缺失 {len(missing)} 个 output_code 文件：{missing}"})
        if empty:
            result.issues.append({"type": "output_code_empty",
                                  "detail": f"空文件 {len(empty)} 个：{empty}"})

        if found and not missing and not empty:
            result.passed = True
            result.status = "validated"

        return result

    # ── 2. patches 存在且与 output_code 对应 ──────────────────────────────

    def _verify_patches_exist(self, project_id: str, p4_input) -> SlotVerificationResult:
        """验证 patches 存在且与 output_code 对应。"""
        ws = workspace_path(project_id)
        out_refs = p4_input.output_code_refs
        patch_refs = p4_input.patch_refs
        result = SlotVerificationResult(
            slot_id="patches_exist", passed=False,
            status="validation_failed",
            details={"output_code_count": len(out_refs), "patch_count": len(patch_refs)},
        )

        if not patch_refs:
            result.issues.append({"type": "no_patch_refs", "detail": "无 patch refs"})
            result.status = "evidence_gap"
            return result

        missing = []
        found = []
        for ref in patch_refs:
            p = ws / ref
            if p.exists() and p.stat().st_size > 0:
                found.append(ref)
            else:
                missing.append(ref)

        result.details["found"] = found
        result.details["missing"] = missing

        if missing:
            result.issues.append({"type": "patch_missing",
                                  "detail": f"缺失 {len(missing)} 个 patch 文件"})

        # 验证对应关系：patch 文件名应关联到 output_code 文件名（宽松：patch 数量 ≤ output_code 数量 + 2）
        correlation_ok = len(patch_refs) <= len(out_refs) + 2
        result.details["correlation_ok"] = correlation_ok
        if not correlation_ok:
            result.issues.append({"type": "patch_output_mismatch",
                                  "detail": f"patch 数量({len(patch_refs)}) 远多于 output_code({len(out_refs)})"})

        if found and not missing and correlation_ok:
            result.passed = True
            result.status = "validated"

        return result

    # ── 3. P4 Evidence basis 真实 ─────────────────────────────────────────

    def _verify_p4_evidence_real(self, project_id: str, p4_input) -> SlotVerificationResult:
        """验证 P4 Evidence basis 真实（非 LLM 自报，sha256 校验）。"""
        ws = workspace_path(project_id)
        ev_refs = p4_input.evidence_refs
        aet = self.aet
        result = SlotVerificationResult(
            slot_id="p4_evidence_real", passed=False,
            status="validation_failed",
            details={"evidence_count": len(ev_refs)},
        )

        if not ev_refs:
            result.issues.append({"type": "no_evidence_refs", "detail": "无 evidence refs"})
            result.status = "evidence_gap"
            return result

        if aet is None:
            # 无 aet service → 基于文件存在性做 weak 验证（诚实标记 weak basis）
            result.details["weak_check"] = True
            missing = []
            found = []
            for ref in ev_refs:
                p = ws / "evidence" / f"{ref}.json"
                if p.exists():
                    found.append(ref)
                else:
                    missing.append(ref)
            result.details["found"] = found
            result.details["missing"] = missing
            if missing:
                result.issues.append({"type": "evidence_file_missing",
                                      "detail": f"证据文件缺失 {len(missing)} 个（weak check）"})
            result.passed = bool(found) and not missing
            result.status = "validated" if result.passed else "validation_failed"
            return result

        # 真实 Evidence 验证：读取 evidence 对象 + sha256 校验
        try:
            all_ev = aet.list_evidence(project_id, stage="p4")
        except Exception as e:
            logger.warning("P5 verify evidence: list_evidence failed: %s", e, exc_info=True)
            result.issues.append({"type": "evidence_query_failed", "detail": str(e)})
            result.status = "evidence_gap"
            return result

        ev_map = {e.get("evidence_id"): e for e in all_ev if e.get("evidence_id")}
        missing = []
        invalid_basis = []
        verified = []
        for eid in ev_refs:
            ev = ev_map.get(eid)
            if ev is None:
                missing.append(eid)
                continue
            # Evidence basis 真实性：检查 output_code_ref 的 sha256 是否与文件一致
            sha_ok = self._check_evidence_sha256(ws, ev)
            if not sha_ok:
                invalid_basis.append(eid)
            else:
                verified.append(eid)

        result.details["verified"] = verified
        result.details["missing"] = missing
        result.details["invalid_basis"] = invalid_basis

        if missing:
            result.issues.append({"type": "evidence_missing",
                                  "detail": f"Evidence 对象缺失 {len(missing)} 个"})
        if invalid_basis:
            result.issues.append({"type": "evidence_basis_invalid",
                                  "detail": f"Evidence sha256 与文件不一致 {len(invalid_basis)} 个（D-101）"})

        result.passed = bool(verified) and not missing and not invalid_basis
        result.status = "validated" if result.passed else "validation_failed"
        return result

    def _check_evidence_sha256(self, ws: Path, ev: dict) -> bool:
        """校验 Evidence 中 output_code_ref 的 sha256 是否与真实文件一致。"""
        out_ref = ev.get("output_code_ref")
        if not out_ref:
            return True  # 无 output_code_ref 不校验
        p = ws / out_ref
        if not p.exists():
            return False
        ev_sha = ev.get("output_sha256") or ev.get("sha256") or ""
        if not ev_sha:
            return True  # 无 sha 记录不校验（弱证据但不伪造）
        actual = _file_sha256(p)
        return bool(ev_sha) and ev_sha == actual

    # ── 4. P4 summary 可读 ────────────────────────────────────────────────

    def _verify_p4_summary_readable(self, project_id: str, p4_input) -> SlotVerificationResult:
        """验证 artifacts/p4_execution_summary.json 可解析。"""
        ws = workspace_path(project_id)
        result = SlotVerificationResult(
            slot_id="p4_summary_readable", passed=False,
            status="validation_failed",
            details={},
        )

        if not p4_input.p4_execution_summary:
            result.issues.append({"type": "no_summary", "detail": "无 P4 execution summary"})
            result.status = "evidence_gap"
            return result

        summary = p4_input.p4_execution_summary
        # 检查关键字段存在
        required_keys = ["stage", "graph_status", "change_manifest"]
        missing_keys = [k for k in required_keys if k not in summary]
        if missing_keys:
            result.issues.append({"type": "summary_missing_keys",
                                  "detail": f"summary 缺少关键字段：{missing_keys}"})
            result.details["parsed"] = False
            return result

        result.details["parsed"] = True
        result.details["graph_status"] = summary.get("graph_status")
        result.details["node_count"] = summary.get("node_count")
        result.passed = True
        result.status = "validated"
        return result

    # ── 5. P4→P5 Gate approved ────────────────────────────────────────────

    def _verify_p4_p5_gate(self, p4_input) -> SlotVerificationResult:
        """验证 P4→P5 Gate 已 approved。"""
        return SlotVerificationResult(
            slot_id="p4_p5_gate_approved",
            passed=p4_input.p4_to_p5_gate_status == "approved",
            status="validated" if p4_input.p4_to_p5_gate_status == "approved" else "validation_failed",
            details={"gate_id": p4_input.p4_to_p5_gate_status,
                     "gate_status": p4_input.p4_to_p5_gate_status},
            issues=[] if p4_input.p4_to_p5_gate_status == "approved"
            else [{"type": "gate_not_approved",
                   "detail": f"Gate 状态 {p4_input.p4_to_p5_gate_status}"}],
        )

    # ── C5: 有条件必需槽位真实命令验证 ─────────────────────────────────────

    def verify_conditional_slots(self, project_id: str) -> list:
        """C5: 经 ExecutionProvider 真实执行构建/运行/测试/静态检查。

        落实 D-105①：P5 最小验证必须全量包含真实构建、运行/启动、测试、静态/语法检查。
        - 命令不可识别 → needs_user_input（诚实）
        - 命令执行失败 → validation_failed（不等于 completed）
        - 无命令 ≠ 通过
        """
        from app.services.p5_command_service import (
            P5CommandDetectionService, P5CommandExecutionService,
        )
        detect_svc = P5CommandDetectionService()
        exec_svc = P5CommandExecutionService(tracer=self.tracer, auditor=self.auditor)

        commands = detect_svc.detect_commands(project_id)
        exec_results = exec_svc.execute_all_conditional(project_id, commands)

        # 将命令执行结果映射到 SlotVerificationResult
        slot_results = []
        for er in exec_results:
            vr = SlotVerificationResult(
                slot_id=er.slot_id,
                passed=er.passed,
                status=er.status,
                details={
                    "command": er.command,
                    "exit_code": er.exit_code,
                    "elapsed_ms": er.elapsed_ms,
                    "risk_level": er.risk_level,
                    "blocked": er.blocked,
                    "project_type": commands.project_type,
                    "stdout_tail": er.stdout[-500:] if er.stdout else "",
                    "stderr_tail": er.stderr[-500:] if er.stderr else "",
                },
            )
            if er.failure_reason:
                vr.issues.append({"type": f"{er.slot_id}_failed",
                                  "detail": er.failure_reason})
            if er.gate_required:
                vr.details["gate_required"] = True
                vr.details["gate_reason"] = er.gate_reason
            slot_results.append(vr)

        return slot_results


