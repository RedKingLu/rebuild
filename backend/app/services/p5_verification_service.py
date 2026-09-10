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

        # R22-4 代码量观察指标（**纯旁路**）：源码/产出体量对比只落观察产物
        # artifacts/p5/_code_volume.json，【不并入任何 SlotVerificationResult、不影响
        # results、不参与任何判定分支、不进 Gate、不参与 P4→P5 晋级判定】。
        # 整段包 try/except 且只告警：连度量/落盘失败也不得阻断（承 _persist_build_log
        # 的"写失败只告警不阻断"范式，公理 3 发声但非阻断）—— 观察者不得弄坏被观察对象。
        # 边界详述源：产物/草稿/R22-③完善方案.md §1.1；机器化锁定见
        # backend/tests/test_r22_code_volume.py 的非阻断锁定断言。
        try:
            from app.services.code_volume_service import measure_and_persist
            measure_and_persist(project_id)
        except Exception:
            logger.warning("P5 代码量观察指标失败（非阻断，验证结论逐字不受影响）",
                           exc_info=True)  # 公理 3

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
        """验证 patches 存在且非空（确定性事实）。

        GAP-P5-4（R17.5-P5-R3）：本槽位【只产确定性事实】——patch 文件真实存在且非空。
        旧实现用启发式 `len(patch) <= len(output)+2` 当 PASS/FAIL 门禁（correlation_ok），
        会把合法【重写式迁移】误判为 patch_output_mismatch：如 WebForms→Razor 整体重写，
        一个源产出多个目标文件 + 转换脚本，patch 非行级 diff、数量与 output_code 不成 1:1，
        真跑 8666035c（73 patch > 72）即因此假阴。数量对应/重写式 source→target 结构对应
        是【语义判断】，归 LLM advisory 层的 `structure_mapping`（diff 等价证据）产出，
        不由确定性计数产"对应/不对应"判断结论（红线：确定性只产事实——存在/非空/计数）。
        因此本槽位判定 = patch 文件真实存在且非空即 validated；缺文件/空文件 →
        validation_failed（真实缺失，非启发式误判）；无 patch refs → evidence_gap。
        """
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

        # patch 与 output_code 的【数量关系】只作中立事实登记，不再当 PASS/FAIL 门禁。
        # source→target 结构对应（含重写式非行级 diff）由 LLM advisory 的 structure_mapping 评估。
        result.details["patch_output_ratio"] = (
            round(len(patch_refs) / len(out_refs), 3) if out_refs else None)
        result.details["correspondence_assessed_by"] = "llm_structure_mapping_advisory"

        # 确定性判定：patch 文件真实存在且非空 → validated（存在性铁证，不依赖计数启发式）。
        if found and not missing:
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
        superseded = []
        for eid in ev_refs:
            ev = ev_map.get(eid)
            if ev is None:
                missing.append(eid)
                continue
            # R21 / B-R20-EVIDENCE-SHA-STALE: 一个文件在 P4 多节点执行中被后续
            # 节点合法覆写是正常行为——早先写入的 Evidence 记录的 sha256 只反映
            # 它写入当时的文件版本，文件被覆写后自然与"现在"的内容不一致。这不是
            # 证据基准被篡改，是 aet_service.write_evidence() 在检测到同一
            # output_code_ref 被新 Evidence 覆盖式引用时打上的 superseded_by 标记。
            # 跳过这些历史记录，只用未被标记（即最新）的记录做 sha256 校验——
            # 真正的篡改（最新记录 sha256 与文件不一致）仍然会被下面的校验判失败，
            # 不会因为"存在历史记录"而被绕过。
            if ev.get("superseded_by"):
                superseded.append(eid)
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
        result.details["superseded"] = superseded

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
        """验证 artifacts/p4/p4_execution_summary.json 可解析。"""
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

    # ── 增强项（非门禁）：source 未被修改（GAP-P5-5，R17.5-P5-R3）──────────

    def verify_source_unmodified(self, project_id: str) -> SlotVerificationResult:
        """source/ 未被修改的真实证明（D-099 源只读的正向证据）。

        GAP-P5-5：source_unmodified 原为【死槽】（从不产真实证据，恒 PENDING）。本轮按用户
        裁决（先不硬约束、优先真 verifier、不可用则诚实软证据、绝不用恒 True/占位假证据填死槽）
        接入真实确定性 verifier：
          - source/ 是 git 检出（有 .git）→ 读 `git status --porcelain`（只读，不改源）：
            工作树干净 = 未改（validated，D-099 正向铁证）；非空 = 被改（validation_failed，
            列出改动条目）。这是【确定性事实】，非 LLM 判断。
          - source/ 非 git（zip/手工导入，无 byte 级基线快照）→ 诚实 evidence_gap
            （capability_ready=True）：无基线可正向比对，D-099 由 WorkspaceMediator 单一写闸
            + READONLY_DIRS 保障；【绝不伪造恒 True/占位通过】（反伪造红线，R12 多轮教训）。
          - source/ 不存在 → 诚实 evidence_gap。
        本槽为【增强项】(ENHANCED)，不在 can_mark_completed 门禁内，
        任何状态都不翻转 can_be_completed（门禁只读硬必需 + 有条件必需）。
        """
        ws = workspace_path(project_id)
        src = ws / "source"
        result = SlotVerificationResult(
            slot_id="source_unmodified", passed=False, status="evidence_gap", details={})

        if not src.exists():
            result.details["source_present"] = False
            result.issues.append({"type": "no_source",
                                  "detail": "source/ 不存在，无法做未改证明（诚实 evidence_gap，非伪造）"})
            return result

        result.details["source_present"] = True
        if (src / ".git").exists():
            porcelain = self._git_status_porcelain(src)
            result.details["method"] = "git_status_porcelain"
            if porcelain is None:
                result.issues.append({"type": "git_query_failed",
                                      "detail": "git 状态读取失败，诚实 evidence_gap（非伪造）"})
                return result
            changed = [ln for ln in porcelain.splitlines() if ln.strip()]
            result.details["changed_count"] = len(changed)
            result.details["changed_entries"] = changed[:50]
            if changed:
                result.status = "validation_failed"
                result.issues.append({"type": "source_modified",
                                      "detail": f"source/ 检出到 {len(changed)} 处改动（D-099 源只读被破坏）"})
                return result
            result.passed = True
            result.status = "validated"
            result.details["note"] = "git 工作树干净：source/ 未被修改（D-099 正向证据）"
            return result

        # 非 git → 诚实软证据（不伪造）：能力已接线，待基线快照能力真验。
        result.details["method"] = "no_baseline"
        result.details["capability_ready"] = True
        result.issues.append({
            "type": "no_source_baseline",
            "detail": ("source/ 非 git 检出，无 byte 级基线快照，无法做未改的正向证明；"
                       "D-099 源只读由 WorkspaceMediator 单一写闸 + READONLY_DIRS 保障。"
                       "诚实标 evidence_gap（能力已接线、待基线快照真验），非伪造通过。")})
        return result

    @staticmethod
    def _git_status_porcelain(target: Path) -> Optional[str]:
        """只读 git 状态（不改 source/，D-099）。失败返回 None，不抛（非阻断）。"""
        import subprocess
        try:
            out = subprocess.run(
                ["git", "-C", str(target), "status", "--porcelain"],
                capture_output=True, text=True, timeout=15)
            if out.returncode != 0:
                return None
            return out.stdout
        except Exception:
            logger.warning("P5 source_unmodified: git status failed (non-blocking)",
                           exc_info=True)  # 公理3
            return None

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
                    # R19-1（修 B4）：尾巴上限自 500 提到 4000（原 500 字符导致 NU1101/CS
                    # 文本根本到不了报告），并附结构化诊断 + 执行环境 + 阶段明细。
                    "stdout_tail": er.stdout,
                    "stderr_tail": er.stderr,
                    "diagnostics": er.diagnostics,
                    "diagnostics_summary": er.diagnostics_summary,
                    "stages": er.stages,
                    "execution": er.execution,
                    "execution_channel": commands.execution_channel,
                    "toolchain_unavailable": er.toolchain_unavailable,
                },
            )
            # R19-1：完整构建日志落盘，报告里只放摘要+诊断，全文由 evidence_refs 引用。
            log_ref = self._persist_build_log(project_id, er)
            if log_ref:
                vr.evidence_refs.append(log_ref)
                vr.details["log_ref"] = log_ref
            if er.failure_reason:
                vr.issues.append({"type": f"{er.slot_id}_failed",
                                  "detail": er.failure_reason})
            if er.gate_required:
                vr.details["gate_required"] = True
                vr.details["gate_reason"] = er.gate_reason
            slot_results.append(vr)

        return slot_results

    @staticmethod
    def _persist_build_log(project_id: str, er) -> Optional[str]:
        """把命令的完整 stdout/stderr 落盘（过 D-032 脱敏），返回工作区相对路径。

        经 WorkspaceMediator 单一写闸（D-099⑥），不绕过。写失败只告警不阻断（公理 3：
        发声；日志是补充证据，不能因为写日志失败就把真实构建结论丢掉）。
        """
        if not er.executed and not er.stderr:
            return None
        try:
            from datetime import datetime, timezone
            from app.services.workspace_mediator import WorkspaceMediator
            from app.services.workspace_service import workspace_path
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            slot_slug = str(er.slot_id).replace(":", "__").replace("/", "__")
            rel = f"artifacts/p5/build/{slot_slug}-{ts}.log"
            body = [f"# slot: {er.slot_id}", f"# command: {er.command}",
                    f"# exit_code: {er.exit_code}", f"# elapsed_ms: {er.elapsed_ms}",
                    f"# execution: {er.execution}", ""]
            for st in (er.stages or []):
                body += [f"===== stage {st.get('stage')} (exit={st.get('exit_code')}) =====",
                         f"$ {st.get('command')}", st.get("stdout_tail") or "",
                         st.get("stderr_tail") or ""]
            if not er.stages:
                body += ["===== stdout =====", er.stdout, "===== stderr =====", er.stderr]
            mediator = WorkspaceMediator(str(workspace_path(project_id)))
            target, _risk = mediator.check_write(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(body), encoding="utf-8")
            return rel
        except Exception as e:
            logger.warning("P5 构建日志落盘失败（非阻断，构建结论不受影响）：%s", e, exc_info=True)
            return None


