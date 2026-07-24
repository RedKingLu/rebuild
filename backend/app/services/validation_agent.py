"""Independent Stage ValidationAgent — 独立阶段验收主体 (R17.3-6 WP-2 批 A, P1 样例).

D-082 施工-验收分离的落地：ValidationAgent 是**独立身份 + 独立上下文**的验收主体，
不是 handler.review 改名（r3 约束 2），也不继承 WorkAgent 进程内未落盘推理（r3 约束 3）。

独立性硬边界（r3 约束 3）：
  - 独立 agent_id：复用 seed AgentType.acceptance（Q-WP2-1）；
  - 独立 DB 会话：自建 get_session()，不复用 WorkAgent 会话；
  - 输入只读落盘：validate() 只消费 WorkAgentResult 中的**落盘 ref**，并从磁盘/AET
    重新读取内容再验；不读取 WorkAgent 的进程内推理字段（read_from_disk_only=True）。

复用（不推倒）：AcceptanceService（独立身份 + 8 项结构化确定性检查族）作为结构检查基座；
在其之上叠加 P1 fact-evidence map 落盘可解析 + sha256 校验。

不合格 → passed=False → StageLoop/ReviewPass 触发 rework 轮 → WorkAgent 重跑
（这就是 request_changes 打回 WorkAgent 修改后重验，Q-WP2-5）；合格才建用户 Gate。

挂点（D-037）：make_work_node 内作为 StageLoop.review_fn。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services import workspace_service
from app.services.review_pass import ReviewResult

logger = logging.getLogger("rebuild.validation_agent")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ValidationResult:
    stage: str
    passed: bool
    verdict: str = "accepted"
    agent_id: Optional[str] = None
    reviewer: str = "validation_agent"
    checks: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    claim_evidence_verification: dict = field(default_factory=dict)
    read_from_disk_only: bool = True

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "agent_id": self.agent_id,
            "reviewer": self.reviewer,
            "passed": self.passed,
            "verdict": self.verdict,
            "checks": self.checks,
            "issues": self.issues,
            "recommendations": self.recommendations,
            "claim_evidence_verification": self.claim_evidence_verification,
            "read_from_disk_only": self.read_from_disk_only,
            "validated_at": _now(),
        }

    def to_review_result(self) -> ReviewResult:
        return ReviewResult(passed=self.passed, issues=self.issues,
                            recommendations=self.recommendations,
                            reviewer=self.reviewer)


class ValidationAgent:
    """独立阶段验收主体（P1 结构化路径）。"""

    def __init__(self, stage: str, project_id: str, run_id: str = "", *,
                 tracer=None, auditor=None, handler=None, gateway=None):
        self.stage = stage
        self.project_id = project_id
        self.run_id = run_id
        self.tracer = tracer
        self.auditor = auditor
        self._handler = handler          # 批 B：复用 handler.review 作确定性域校验 Tool（非改名冒充）
        self._gateway = gateway          # 批 B：LLM 语义验收（无 Key → 诚实 evidence_gap）
        self.last_result: Optional[ValidationResult] = None

    # ── 独立性工具 ──────────────────────────────────────────────────────
    def _ws_root(self) -> Path:
        return workspace_service.workspace_path(self.project_id)

    def _sha256(self, rel_path: str) -> str:
        try:
            return hashlib.sha256((self._ws_root() / rel_path).read_bytes()).hexdigest()
        except Exception:
            return ""

    def _exists(self, rel_path: str) -> bool:
        return (self._ws_root() / rel_path).exists()

    def _read_json(self, rel_path: str) -> Optional[dict]:
        try:
            return json.loads((self._ws_root() / rel_path).read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── main entry（review_fn） ─────────────────────────────────────────
    def validate(self, work_result: dict) -> ReviewResult:
        """StageLoop.review_fn：独立验收 WorkAgent 产出。R17.5：P0/P1 与 P2/P3/P4 一致，走
        通用 LLM 语义验收路径；P5/P6 亦经此路径。r3 约束 3：只消费落盘 ref + 磁盘重读。"""
        return self._validate_generic(work_result)
    def _validate_generic(self, work_result: dict) -> ReviewResult:
        """独立验收（通用路径）。独立性（r3 约束 3）：独立 agent_id + 独立 DB 会话 +
        磁盘重读 artifacts/evidence/claim-evidence map。

        pass/fail 判定（C4 加固——独立核验参与门控 + 域基线从盘重读）：
          - 域基线：handler.review 喂入 ValidationAgent **从盘重读**的域产物（_disk_review_input），
            不消费 WorkAgent 进程内推理 dict（read_from_disk_only 名实相符，r3 约束 3）；
          - 反伪造：declared=completed 但所声明产物/claim 均无法在磁盘解析 → 降级 rework；
          - AcceptanceService 独立结构核验 **参与 pass/fail 门控**（非仅异常记 issue，C4）：
            核验非通过(rework/gate_required/failed)或异常 → 独立验收不通过；
          - LLM 阶段：claim-evidence 内联引用校验 + LLM 语义验收（无 Key → 诚实 evidence_gap，
            不据此判失败，结构/域校验仍为准）。
        """
        work_result = work_result or {}
        declared = work_result.get("status", "completed")
        cem_ref = work_result.get("claim_evidence_map_ref")
        # R17.5 WP-4：P0/P1 改 LLM 识别 → 纳入 LLM 验收路径（独立 Acceptance Agent LLM 判识别质量，
        # 非仅存在性）；内联引用校验 + LLM 语义验收（无 Key → 诚实 evidence_gap，advisory）。
        llm = self.stage in ("p0", "p1", "p2", "p3", "p4")

        checks: list = []
        issues: list = []
        recs: list = []

        # ① 基线域校验：handler.review 作确定性域规则 Tool，喂入从盘重读的域产物（r3 约束 3）。
        #    ValidationAgent 不把 WorkAgent 进程内 work_result 直传给 handler.review。
        base_passed = True
        handler = self._handler
        if handler is None:
            try:
                from app.graph.nodes import get_handler
                handler = get_handler(self.stage)
            except Exception:
                handler = None
        disk_view = self._disk_review_input(work_result)
        if handler is not None and hasattr(handler, "review"):
            try:
                base = handler.review(disk_view)
                base_passed = bool(base.passed)
                checks.append({"item": "域规则校验（handler.review，从盘重读输入）", "passed": base_passed,
                               "reason": ("通过" if base_passed else "未通过"), "evidence_ref": None})
                if not base_passed:
                    for i in (base.issues or []):
                        issues.append(i if isinstance(i, dict) else {"type": "domain", "detail": str(i)})
                    recs += list(base.recommendations or [])
            except Exception as exc:
                base_passed = False
                checks.append({"item": "域规则校验（handler.review，从盘重读输入）", "passed": False,
                               "reason": f"域校验异常（{type(exc).__name__}）", "evidence_ref": None})
                issues.append({"type": "domain_review_error", "detail": type(exc).__name__})
        else:
            # 无 handler.review → 以 declared 完成度为域基线（诚实）
            base_passed = (declared == "completed")
            checks.append({"item": "阶段完成度（declared status）", "passed": base_passed,
                           "reason": f"status={declared}", "evidence_ref": None})

        # ② 反伪造：declared=completed 但所声明 artifact/claim 均无法磁盘解析 → 伪造嫌疑
        cem_ok, cem_verify, cem_issues = self._verify_generic_evidence_map(cem_ref, declared)
        antifake_ok = True
        if declared == "completed" and cem_verify.get("total", 0) > 0 \
                and cem_verify.get("resolved", 0) == 0:
            antifake_ok = False
            issues.append({"type": "fabricated_completion",
                           "detail": "声明 completed 但 evidence map 全部条目无法磁盘解析（反伪造 D-097）"})
            recs.append("核对领域产物是否真实落盘后重跑")
        checks.append({"item": "evidence map 磁盘可解析（反伪造）",
                       "passed": (cem_verify.get("resolved", 0) == cem_verify.get("total", 0)) if cem_verify.get("total", 0) else True,
                       "reason": f"{cem_verify.get('resolved',0)}/{cem_verify.get('total',0)} 可解析",
                       "evidence_ref": cem_ref})
        issues += cem_issues

        # ③ AcceptanceService 独立结构核验（C4：参与 pass/fail 门控，非仅异常记 issue）
        disk_refs = self._disk_artifact_refs()
        acc = self._acceptance_check(disk_refs, {})
        acc_error = acc.get("error")
        acc_result = acc.get("result")
        # 完成态下：核验异常或核验非通过 → 独立结构门控不通过（REC-1 反伪造：不静默 accepted）。
        acc_gate_ok = True
        if declared == "completed":
            if acc_error:
                acc_gate_ok = False
                issues.append({"type": "acceptance_check_error",
                               "detail": f"独立结构核验（AcceptanceService）异常（{acc_error}），已降级为不通过"})
                recs += acc.get("recommendations", [])
            elif acc_result not in ("accepted", "accepted_with_warning"):
                acc_gate_ok = False
                issues.append({"type": "acceptance_structural_fail",
                               "detail": f"独立结构核验（AcceptanceService）判定 {acc_result}，独立验收不通过"})
                recs += acc.get("recommendations", [])
            checks.append({"item": "独立结构核验（AcceptanceService 门控）", "passed": acc_gate_ok,
                           "reason": f"result={acc_result or acc_error}", "evidence_ref": None})

        # ④ LLM 阶段：内联引用校验 + LLM 语义验收。
        #    P0-P3：语义为 advisory（结构/域校验为准）。
        #    P4（T3.2）：语义内容保真结论作 pass/fail 门禁——读真实源+产出内容判「真实源的真实
        #    迁移/自洽/遵守上游裁决路线」，臆造/违反路线 → 拒绝；无 Key/无法读取 → 诚实不通过
        #    （不假 pass，去掉「结构过了就 accepted」，D-097/公理3/stage-agent-loop §7）。
        cev_verification = dict(cem_verify)
        sem_gate_ok = True
        if llm:
            inline_stats, inline_issue = self._verify_inline_citations(cem_ref, declared)
            cev_verification["inline_citation"] = inline_stats
            if inline_issue:
                issues.append(inline_issue)  # 记 issue，不静默补全（AGT-05）
            sem = self._llm_semantic_check(work_result, declared)
            if sem:
                cev_verification["llm_semantic"] = sem
                if self.stage == "p4" and declared == "completed":
                    sem_gate_ok, sem_issue = self._semantic_gate_p4(sem)
                    if sem_issue:
                        issues.append(sem_issue)
                        recs.append("依据独立验收语义结论修正/重做迁移产物后重跑"
                                    "（禁止照节点标题臆造通用样例）")

        # ④'（D-109）P2/P3 技术路线红线符合性【advisory】校验：项目已批准 project.tech_selection
        #    时，记录红线治理状态并对明显偏离发声（recommendation，不 flip passed——P0-P3 advisory；
        #    P4 由上面 _semantic_gate_p4 依红线作门禁拦截）。
        if self.stage in ("p2", "p3") and declared == "completed":
            adh_check, adh_rec = self._tech_selection_adherence_advisory()
            if adh_check:
                checks.append(adh_check)
            if adh_rec:
                recs.append(adh_rec)

        # ⑤ 综合裁决：pass = 域基线通过 且 无反伪造违规 且 独立结构核验门控通过
        #    且（P4）LLM 内容保真语义门控通过（P0-P3 语义/内联仍为 advisory）
        passed = base_passed and antifake_ok and acc_gate_ok and sem_gate_ok
        if declared != "completed":
            passed = False
            # WP-6 (Q-R17.3-6-2): 未完成且系模型全失败强制中断 → 结构化 model_unavailable issue，
            # 供 work 节点识别并建 model_unavailable 中断 Gate（携已尝试模型链路 + 用户操作）。
            if work_result.get("attempted_chain") or \
                    work_result.get("model_error_category") == "model_unavailable" or \
                    "no_model_key" in str(work_result.get("reason", "")):
                issues.append({
                    "type": "model_unavailable",
                    "detail": {
                        "interrupted_stage": self.stage,
                        "failure_reason": work_result.get("reason", ""),
                        "error_category": work_result.get("model_error_category", "model_unavailable"),
                        "attempted_chain": work_result.get("attempted_chain", []),
                        "user_actions": work_result.get("model_user_actions", []),
                    }})
        verdict = self._verdict_for(passed, acc, declared)

        result = ValidationResult(
            stage=self.stage, passed=passed, verdict=verdict,
            agent_id=acc.get("agent_id"), checks=checks, issues=issues,
            recommendations=recs, claim_evidence_verification=cev_verification,
            read_from_disk_only=True,
        )
        self.last_result = result
        self._persist(result)
        if self.tracer:
            try:
                self.tracer.write("validation_agent", action="validate",
                                  summary=f"{self.stage} 独立验收 verdict={verdict} passed={passed}",
                                  project_id=self.project_id, run_id=self.run_id or None,
                                  stage=self.stage)
            except Exception:
                logger.debug("validation_agent trace 写入失败（advisory）", exc_info=True)
        return result.to_review_result()

    def _verdict_for(self, passed: bool, acc: dict, declared: str) -> str:
        if passed:
            r = acc.get("result")
            return r if r in ("accepted", "accepted_with_warning") else "accepted"
        return "rework_required" if declared == "completed" else "rework_required"

    def _disk_artifact_refs(self) -> list:
        """从盘重读 artifact refs。D-107: 产物按 artifacts/{stage}/ 分层，故除根目录扁平
        产物外，还扫描各阶段子文件夹（p0-p6）；完成包清单 _stage_package.json 不计入产物。"""
        art_dir = self._ws_root() / "artifacts"
        refs: list = []
        if not art_dir.exists():
            return refs
        for p in sorted(art_dir.glob("*.json")):
            if not p.name.startswith("_"):
                refs.append(f"artifacts/{p.name}")
        for stage in ("p0", "p1", "p2", "p3", "p4", "p5", "p6"):
            sub = art_dir / stage
            if sub.is_dir():
                for p in sorted(sub.glob("*.json")):
                    if not p.name.startswith("_"):
                        refs.append(f"artifacts/{stage}/{p.name}")
        return refs

    # 编排/验收报告（非域产物）：StageLoop 3 报告 + WorkAgent/ValidationAgent 4 报告。
    # 域视图须排除，避免把编排报告误当作领域 artifact（否则 handler.review 的 artifact
    # 存在性判定被编排报告污染，round 检测失真）。
    _AGENT_REPORT_SUFFIXES = ("_start_plan.json", "_construction.json", "_acceptance.json",
                              "_work_plan.json", "_gate_brief.json",
                              "_claim_evidence_map.json", "_validation.json")

    def _disk_domain_artifacts(self) -> list:
        """从盘重读的【领域产物】refs（r3 约束 3）：排除 WorkAgent 编排报告；P4 追加
        AET 登记的 output_code/patch 真实产物 ref。供 handler.review 域校验使用。"""
        refs: list = []
        for r in self._disk_artifact_refs():
            name = r.split("/")[-1]
            if not any(name.endswith(sfx) for sfx in self._AGENT_REPORT_SUFFIXES):
                refs.append(r)
        if self.stage == "p4":
            try:
                from app.services.aet_service import AETService
                for e in AETService(None).list_evidence(self.project_id, stage="p4"):
                    for k in ("output_code_ref", "patch_ref"):
                        v = e.get(k)
                        if v and v not in refs:
                            refs.append(v)
            except Exception:
                logger.debug("validation_agent P4 域产物 AET 读取失败（advisory）", exc_info=True)
        return refs

    def _disk_review_input(self, work_result: dict) -> dict:
        """r3 约束 3：为 handler.review（域规则 Tool）重建输入——域内容全部从落盘产物 +
        AET 独立重读，仅从 work_result 取控制信号（status/reason）与已持久化引用/标志
        （task_graph_ref/stage_plan_ref/batch_id/p6_final_gate_id 等，均指向已落库 DB/Gate
        状态，非 WorkAgent 进程内未落盘推理）。使 read_from_disk_only 名实相符。"""
        wr = work_result or {}
        view: dict = {"status": wr.get("status", "completed"), "reason": wr.get("reason", "")}
        # 已持久化引用/标志（指向 DB/Gate 落库状态，非进程内推理）
        for k in ("task_graph_ref", "stage_plan_ref", "batch_id", "p6_final_gate_id",
                  "model_used", "degraded", "gate_required", "batch_risk_level"):
            if wr.get(k) is not None:
                view[k] = wr[k]
        # 域 Evidence：从 AET（落盘）独立重读，非 work_result
        try:
            from app.services.aet_service import AETService
            ev = AETService(None).list_evidence(self.project_id, stage=self.stage)
            view["evidence_refs"] = [e.get("evidence_id") for e in ev if e.get("evidence_id")]
        except Exception:
            logger.debug("validation_agent 域 Evidence 独立重读失败（advisory）", exc_info=True)
            view["evidence_refs"] = []
        # 域 Artifact：从盘重读（排除编排报告）
        view["artifacts"] = self._disk_domain_artifacts()
        # 逐阶段域内容从盘重读
        if self.stage == "p0":
            intake = self._read_json("artifacts/p0/intake_report.json") or {}
            view["source_type"] = intake.get("source_type")
            view["file_count"] = intake.get("file_count", 0)
        elif self.stage == "p1":
            # P1 域校验（RealP1Handler.review）判定建档识别产物 + 原始验收基准存在性；
            # 从盘重读 artifacts 已在 view["artifacts"]。status 反映是否 completed。
            view["file_count"] = (self._read_json("artifacts/p0/source_index.json") or {}).get("file_count", 0)
        elif self.stage == "p2":
            rep = self._read_json("artifacts/p2/p2_assessment_report.json") or {}
            view["assessment_report"] = rep.get("report", rep.get("assessment_report", {}))
            view["analysis_only"] = rep.get("analysis_only", True)
        return view

    def _verify_generic_evidence_map(self, cem_ref, declared) -> tuple[bool, dict, list]:
        """通用 evidence map 磁盘可解析校验（claim/fact 通用）。declared!=completed 时不苛求。"""
        if not cem_ref:
            if declared == "completed":
                return False, {"total": 0, "resolved": 0, "unresolved": ["<map 缺失>"]}, \
                    [{"type": "no_evidence_map", "detail": "completed 但缺 evidence map"}]
            return True, {"total": 0, "resolved": 0, "unresolved": []}, []
        data = self._read_json(cem_ref)
        if not data:
            return False, {"total": 0, "resolved": 0, "unresolved": ["<map 不可解析>"]}, \
                [{"type": "unparseable_evidence_map", "detail": cem_ref}]
        entries = data.get("entries") or []
        total = 0
        resolved = 0
        unresolved: list = []
        for e in entries:
            if e.get("produced_by") == "none":
                continue  # 诚实占位（未完成），不计入
            total += 1
            bindings = e.get("bindings") or {}
            art_refs = bindings.get("artifact_refs") or []
            recorded_sha = bindings.get("sha256") or ""
            ok = bool(art_refs) and all(self._exists(r.split("#")[0]) for r in art_refs)
            if ok and recorded_sha:
                ok = self._sha256(art_refs[0].split("#")[0]) == recorded_sha
            if ok:
                resolved += 1
            else:
                unresolved.append(e.get("id"))
        verify = {"total": total, "resolved": resolved, "unresolved": unresolved}
        issues = ([] if resolved == total else
                  [{"type": "evidence_partially_unresolved", "detail": f"未解析: {unresolved}"}])
        return (total > 0 and resolved == total), verify, issues

    def _verify_inline_citations(self, cem_ref, declared) -> tuple[dict, Optional[dict]]:
        """AGT-05：校验 LLM 阶段 claim 是否带【主输出级】内联引用（C1，非二遍归因），
        且被引上游 ref 真实存在。无引用或引用不存在 → 记 issue（不静默补全）。"""
        if declared != "completed" or not cem_ref:
            return {"total": 0, "with_inline_citation": 0}, None
        data = self._read_json(cem_ref) or {}
        entries = [e for e in (data.get("entries") or []) if e.get("kind") == "claim"
                   and e.get("produced_by") == "llm"]
        total = len(entries)
        cited = sum(1 for e in entries if e.get("inline_citation"))
        # C1: 被引上游 ref 真实存在性校验——WorkAgent 已把杜撰/不存在的 ref 记入
        # invalid_cited_refs；ValidationAgent 独立记 issue（不静默）。
        invalid: list = []
        for e in entries:
            for r in ((e.get("bindings") or {}).get("invalid_cited_refs") or []):
                if r not in invalid:
                    invalid.append(r)
        stats = {"total": total, "with_inline_citation": cited, "invalid_cited_refs": invalid}
        if total > 0 and cited == 0:
            return stats, {"type": "no_inline_citation",
                           "detail": ("LLM claim 无输出级内联引用上游证据（AGT-05）：需有效模型 Key "
                                      "端到端验证 LLM 内联引用（evidence_gap，不静默补全）")}
        if invalid:
            return stats, {"type": "invalid_inline_citation",
                           "detail": f"主输出内联引用了不存在的上游 ref（勿杜撰，已剔除）：{invalid}"}
        return stats, None

    _MAX_SEMANTIC_BYTES = 12_000  # per-file read cap for semantic acceptance (advisory)

    def _llm_semantic_check(self, work_result: dict, declared: str) -> Optional[dict]:
        """LLM 语义验收（独立上下文）。

        P4 执行阶段（T3.1 内容保真）：给源路径 + 迁移产物路径，读**真实源与真实产出内容**
        （不再只喂 claim_evidence_map 声明语句），判三点——①是否真实源的真实迁移（产物可追溯
        source/ 真实文件、非通用臆造样例）②是否内部自洽 ③是否遵守上游裁决的目标技术栈/库路线；
        产出结构化 verdict 供 _semantic_gate_p4 作 pass/fail 门禁（T3.2）。
        其余 LLM 阶段（P0-P3）：沿用 claim 结构合理性 advisory 检查（结构/域校验为准）。
        无 Key/失败/无法读取 → 诚实 evidence_gap（P4 作门禁不假 pass，D-097）。
        """
        if declared != "completed":
            return None
        gw = self._gateway
        if gw is None:
            try:
                from app.dependencies import get_services
                gw = get_services().model_gateway
            except Exception:
                gw = None
        if gw is None:
            return {"status": "evidence_gap",
                    "detail": "模型网关不可用，LLM 语义验收未执行（需有效 Key 端到端验证）"}
        if self.stage == "p4":
            return self._llm_semantic_check_p4(gw)
        # P0-P3：claim 结构合理性 advisory（不作门禁）
        cem = self._read_json(work_result.get("claim_evidence_map_ref")) or {}
        claims = [e.get("statement", "") for e in (cem.get("entries") or [])][:12]
        prompt = ("你是独立验收 Agent。请判断以下阶段结论是否结构合理、无自相矛盾，"
                  "仅输出 JSON：{\"verdict\":\"accepted|rework\",\"reason\":\"...\"}\n结论：\n"
                  + "\n".join(f"- {c}" for c in claims))
        return self._run_semantic_call(gw, prompt)

    def _llm_semantic_check_p4(self, gw) -> dict:
        """T3.1：P4 内容保真语义验收——给源根路径 + 迁移产物路径，读真实产出内容与其引用的真实
        源片段，判「真实源的真实迁移 / 内部自洽 / 遵守上游裁决路线」。该 Agent 无工具循环范式
        → 走「给路径 + 读关键文件」（读关键产物 + 绑定源片段 + 上游裁决路线摘要，不预装配无关
        内容）。无产物 → rework（No Evidence No Completed）；无法读取/无 Key → evidence_gap。"""
        products = self._p4_product_files()
        if not products:
            return {"status": "completed", "verdict": "rework", "grounded": False,
                    "reason": ("P4 声明完成但 output_code/ 无任何迁移产物落盘，无法确认真实迁移"
                               "（疑空壳/臆造，No Evidence No Completed）")}
        prod_blocks, src_blocks = self._p4_read_products_and_sources(products)
        route = self._p4_route_summary()
        prompt = (
            "你是 rebuild 信创迁移平台 P4 执行阶段的【独立验收 Agent】。只依据下面提供的真实内容"
            "判定，不得脑补或臆测未提供的信息。判定迁移产物是否达标，须同时判三点：\n"
            "① 真实源的真实迁移：产物是否可追溯到 source/ 的真实文件与真实结构，而非与源无关的"
            "通用臆造样例（例如凭空的通用 EMPLOYEE 表、与源栈无关的 Vue3/Java 样板）；\n"
            "② 内部自洽：产物自身是否一致、无自相矛盾；\n"
            "③ 遵守上游裁决路线：是否迁移到上游裁决的目标技术栈/库（见【上游裁决路线】）。\n"
            "任一不满足（脱离真实源臆造 / 违反目标路线 / 不自洽）→ verdict=rework，且 grounded=false。\n"
            "仅输出 JSON：{\"verdict\":\"accepted|rework\",\"grounded\":true|false,\"reason\":\"...\"}\n\n"
            f"【源根路径】source/（真实文件样本清单）：\n{self._p4_source_manifest()}\n\n"
            f"【上游裁决路线（目标栈/库）】\n{route}\n\n"
            f"【迁移产物内容（output_code/ 与 patches/，路径已标注）】\n{prod_blocks}\n\n"
            f"【产物引用的真实源片段】\n{src_blocks or '（产物未绑定可读取的 source/ 文件——若产物脱离真实源应判 rework）'}")
        return self._run_semantic_call(gw, prompt, parse_verdict=True)

    def _run_semantic_call(self, gw, prompt: str, *, parse_verdict: bool = False) -> dict:
        """经 ModelGateway 发起一次语义验收调用（D-098）。无 Key/失败 → evidence_gap。
        parse_verdict=True 时解析结构化 verdict/grounded/reason（P4 门禁用）。"""
        try:
            from app.services.work_agent import _run_coro
            resp = _run_coro(gw.call(messages=[{"role": "user", "content": prompt}],
                                     source="api", max_tokens=512))
        except Exception:
            return {"status": "evidence_gap", "detail": "LLM 语义验收调用异常（需有效 Key 复验）"}
        if not resp or resp.get("status") != "completed":
            cat = (resp or {}).get("error_category", "unknown")
            return {"status": "evidence_gap",
                    "detail": f"LLM 语义验收未完成（{cat}）：需有效模型 Key 端到端验证"}
        content = resp.get("content", "") or ""
        out = {"status": "completed", "raw": content[:200]}
        if parse_verdict:
            verdict, grounded, reason = self._parse_verdict(content)
            out.update({"verdict": verdict, "grounded": grounded, "reason": reason})
        return out

    def _semantic_gate_p4(self, sem: dict) -> tuple[bool, Optional[dict]]:
        """T3.2：把 P4 内容保真语义结论转为 pass/fail 门禁（不再 advisory）。
          - verdict=accepted 且未标 grounded=false → 通过；
          - verdict=rework/reject（脱离真实源臆造 / 违反上游裁决路线 / 不自洽）→ 拒绝；
          - status=evidence_gap 或无法解析 verdict（无 Key / 无法读取产物内容）→ 诚实不通过
            （不假 pass，D-097；去掉「结构过了就 accepted」）。"""
        status = sem.get("status")
        verdict = (sem.get("verdict") or "").lower()
        reason = str(sem.get("reason") or sem.get("detail") or "")[:200]
        if status == "completed" and verdict in ("accepted", "pass", "approved"):
            if sem.get("grounded") is False:
                return False, {"type": "semantic_not_grounded",
                               "detail": f"语义判定通过但标注 grounded=false（产物未接地真实源）：{reason}"}
            return True, None
        if status == "completed" and verdict in ("rework", "reject", "rejected", "fail", "failed"):
            return False, {"type": "semantic_fidelity_fail",
                           "detail": f"P4 内容保真语义验收判定 {verdict}（脱离真实源臆造/违反裁决路线/不自洽）：{reason}"}
        # evidence_gap / verdict 不可解析 → 诚实不通过（不假 pass）
        return False, {"type": "semantic_evidence_gap",
                       "detail": (f"P4 内容保真语义验收未能给出有效结论（status={status}, verdict={verdict or 'unknown'}）："
                                  f"{reason}；无法确认迁移产物真实接地 → 诚实不通过（需有效模型 Key 端到端复验，不假 pass）")}

    # ── P4 内容保真：给路径 + 读关键文件（真实源 + 迁移产物 + 上游裁决路线）────────
    def _read_text_capped(self, rel_path: str) -> Optional[str]:
        try:
            data = (self._ws_root() / rel_path).read_bytes()[:self._MAX_SEMANTIC_BYTES]
            return data.decode("utf-8", errors="replace")
        except Exception:
            return None

    def _p4_product_files(self) -> list:
        """从盘扫描 P4 真实迁移产物（output_code/ + patches/）。"""
        ws = self._ws_root()
        refs: list = []
        for top in ("output_code", "patches"):
            d = ws / top
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    refs.append(str(p.relative_to(ws)))
                if len(refs) >= 60:
                    break
        return refs

    def _p4_source_refs(self) -> list:
        """P4 产物引用的真实源路径（从 AET 落盘证据的 source_ref 收集，供读取源片段对照）。"""
        refs: list = []
        try:
            from app.services.aet_service import AETService
            for e in AETService(None).list_evidence(self.project_id, stage="p4"):
                sr = e.get("source_ref") or (e.get("extra") or {}).get("source_ref")
                if sr and sr not in refs:
                    refs.append(sr)
        except Exception:
            logger.debug("validation_agent P4 source_ref 读取失败（advisory）", exc_info=True)
        return refs

    def _p4_read_products_and_sources(self, products: list) -> tuple[str, str]:
        """读关键产物内容（优先 output_code/）+ 其引用的真实源片段，拼为可判读文本块。"""
        prod_sel = [r for r in products if r.startswith("output_code/")][:6] or products[:6]
        prod_blocks: list = []
        for r in prod_sel:
            txt = self._read_text_capped(r)
            if txt is not None:
                prod_blocks.append(f"### {r}\n```\n{txt}\n```")
        src_blocks: list = []
        for r in self._p4_source_refs()[:6]:
            txt = self._read_text_capped(r)
            if txt is not None:
                src_blocks.append(f"### {r}\n```\n{txt}\n```")
        return "\n\n".join(prod_blocks), "\n\n".join(src_blocks)

    def _p4_source_manifest(self) -> str:
        """source/ 真实文件样本清单（给源根路径 + 真实文件名，供判定可追溯性）。"""
        src = self._ws_root() / "source"
        if not src.is_dir():
            return "（未发现 source/ 目录）"
        names: list = []
        for p in sorted(src.rglob("*")):
            if p.is_file():
                names.append(str(p.relative_to(src)))
            if len(names) >= 40:
                names.append("…（更多略）")
                break
        return "\n".join(f"- {n}" for n in names) or "（source/ 为空）"

    def _p4_route_summary(self) -> str:
        """上游裁决路线摘要（给路径 + 读关键：项目红线 project.tech_selection > P3 stage plan 目标 +
        P2 评估目标库/栈候选）。D-109：project.tech_selection（用户经 P1→P2 gate 批准的技术路线红线）
        为【权威】选定路线，置顶——P4 产物偏离红线 → 语义门禁 rework。"""
        parts: list = []
        ts = self._project_tech_selection()
        if isinstance(ts, dict) and ts:
            route_bits = []
            for key, label in (("target_language", "目标语言"), ("runtime", "运行时"),
                               ("database", "数据库"), ("web_framework", "Web 框架")):
                item = ts.get(key)
                if isinstance(item, dict) and item.get("recommendation"):
                    route_bits.append(f"{label}={item.get('recommendation')}")
            mws = ts.get("middleware_replacements")
            if isinstance(mws, list) and mws:
                names = [f"{m.get('component','')}→{m.get('recommendation','')}"
                         for m in mws if isinstance(m, dict) and m.get("recommendation")]
                if names:
                    route_bits.append("中间件替换=" + "; ".join(names))
            if route_bits:
                parts.append("【项目红线·选定技术路线（权威，用户已裁决，偏离即 rework）】："
                             + "；".join(route_bits))
        sp = (self._read_json("artifacts/p3/p3_stage_plan.json")
              or self._read_json("artifacts/p3/stage_plan.json") or {})
        plan = sp.get("stage_plan", sp) if isinstance(sp, dict) else {}
        obj = plan.get("objective") if isinstance(plan, dict) else None
        tgt = (plan.get("target_stack") or plan.get("target") or plan.get("scope")) \
            if isinstance(plan, dict) else None
        if obj:
            parts.append(f"P3 迁移目标：{str(obj)[:400]}")
        if tgt:
            parts.append(f"P3 目标范围/栈：{str(tgt)[:400]}")
        p2 = self._read_json("artifacts/p2/p2_assessment_report.json") or {}
        rep = (p2.get("report") or p2.get("assessment_report") or p2) if isinstance(p2, dict) else {}
        adr = rep.get("adr_candidates") if isinstance(rep, dict) else None
        if adr:
            parts.append(f"P2 目标库/栈候选（裁决路线依据）：{json.dumps(adr, ensure_ascii=False)[:600]}")
        return "\n".join(parts) or ("（未从项目红线/上游 P2/P3 产物解析到明确目标栈/库；"
                                    "以「产物是否接地真实源、是否内部自洽」为主判据）")

    def _project_tech_selection(self) -> Optional[dict]:
        """D-109：读取 project.tech_selection（P1→P2 gate 批准的技术路线红线）。缺失 → None。"""
        try:
            from app.core.database import get_session
            from app.models.project import Project
            db = get_session()
            try:
                proj = db.get(Project, self.project_id)
                ts = getattr(proj, "tech_selection", None) if proj else None
                return ts if isinstance(ts, dict) and ts else None
            finally:
                db.close()
        except Exception:
            logger.debug("validation_agent 读取 project.tech_selection 失败（advisory）", exc_info=True)
            return None

    def _tech_selection_adherence_advisory(self) -> tuple[Optional[dict], Optional[str]]:
        """D-109（P2/P3 advisory）：项目红线 project.tech_selection 存在时，检测本阶段产物是否明显
        偏离选定目标语言。返回 (check_dict, rec_str|None)。advisory——不 flip passed（P0-P3 语义为
        advisory），仅记 check + recommendation 发声；P4 由 _semantic_gate_p4 依红线作门禁。
        无红线（未到 P1 或未批准）→ (None, None) 不参与。"""
        ts = self._project_tech_selection()
        if not isinstance(ts, dict) or not ts:
            return None, None
        expected: dict[str, str] = {}
        for key in ("target_language", "runtime", "database", "web_framework"):
            item = ts.get(key)
            if isinstance(item, dict) and item.get("recommendation"):
                expected[key] = str(item["recommendation"]).lower()
        if not expected:
            return None, None
        if self.stage == "p2":
            blob = json.dumps(self._read_json("artifacts/p2/p2_assessment_report.json") or {},
                              ensure_ascii=False).lower()
        else:  # p3
            blob = json.dumps(self._read_json("artifacts/p3/p3_stage_plan.json")
                              or self._read_json("artifacts/p3/stage_plan.json") or {},
                              ensure_ascii=False).lower()
        lang_families = {"c#": ["c#", ".net", "csharp"], "java": ["java", "spring"],
                         "python": ["python", "django", "fastapi", "flask"],
                         "go": ["golang", "gin gonic"], "rust": ["rust", "cargo"],
                         "node": ["node.js", "express", "typescript", "javascript"]}
        sel_lang = expected.get("target_language", "")
        deviations: list[str] = []
        if sel_lang and blob:
            sel_fam = next((fam for fam, kws in lang_families.items()
                            if any(k in sel_lang for k in kws)), None)
            for fam, kws in lang_families.items():
                if fam == sel_fam:
                    continue
                if any(k in blob for k in kws) and (
                        not sel_fam or not any(k in blob for k in lang_families[sel_fam])):
                    deviations.append(fam)
        adhered = not deviations
        check = {"item": "技术路线红线符合性（D-109 advisory）", "passed": adhered,
                 "reason": ("产物未见与选定目标语言冲突的其他语言族" if adhered else
                            f"产物出现与选定路线（{sel_lang}）不一致的语言族信号：{deviations}"),
                 "evidence_ref": None, "advisory": True}
        rec = (None if adhered else
               f"{self.stage.upper()} 产物疑似偏离项目选定技术路线（目标语言={sel_lang}）：出现 "
               f"{deviations} 语言族信号，请核对是否偏离已批准红线（advisory；P4 执行将由验收门禁强制）")
        return check, rec

    @staticmethod
    def _parse_verdict(content: str) -> tuple[Optional[str], Optional[bool], str]:
        """从 LLM 回答中解析结构化 verdict/grounded/reason（容错：优先 JSON，退化关键词扫描）。"""
        import re as _re
        text = content or ""
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                v = obj.get("verdict")
                g = obj.get("grounded")
                return (str(v).lower() if v else None,
                        (bool(g) if isinstance(g, bool) else None),
                        str(obj.get("reason", "")))
            except Exception:
                pass
        low = text.lower()
        if "rework" in low or "reject" in low:
            return "rework", None, text[:200]
        if "accepted" in low or "accept" in low:
            return "accepted", None, text[:200]
        return None, None, text[:200]

    # ── AcceptanceService 结构核验（独立身份 + 独立 DB 会话） ────────────
    def _acceptance_check(self, disk_refs: list, criteria_met: dict) -> dict:
        try:
            from app.core.database import get_session
            from app.services.acceptance_service import AcceptanceService
            db = get_session()  # 独立会话（不复用 WorkAgent）
            try:
                aet_evidence = []
                try:
                    from app.services.aet_service import AETService
                    aet_evidence = AETService(None).list_evidence(self.project_id, stage=self.stage)
                except Exception:
                    logger.debug("validation_agent AET 独立读取失败（advisory）", exc_info=True)
                node_package = {
                    "artifacts": disk_refs,
                    "evidence": aet_evidence,
                    "criteria_met": criteria_met,
                    "risk_level": "L0",
                    # fact-evidence 的 run trace 作为可观测性信号（若有 run_id）
                    "trace_refs": ([f"run:{self.run_id}"] if self.run_id else []),
                }
                svc = AcceptanceService(db=db, tracer=self.tracer, auditor=self.auditor)
                acc = svc.accept(node_package, task_plan={},
                                 acceptance_criteria=list(criteria_met.keys()),
                                 project_id=self.project_id, run_id=self.run_id,
                                 stage=self.stage)
                return acc.to_dict()
            finally:
                db.close()
        except Exception as exc:
            # REC-1（D-097/公理3 反伪造）：核验异常不得静默按 accepted 处理——那是失败伪装
            # 通过。改为显式降级为不通过并保留（脱敏的）异常原因，让失败发声。仅记录异常类型
            # 名（不含消息正文），避免路径/凭据等敏感内容进入验收产物。
            reason = type(exc).__name__
            logger.warning(
                "validation_agent AcceptanceService 核验异常（%s），显式降级为不通过（不再静默 accepted）",
                reason, exc_info=True)
            return {"result": "error",
                    "recommendations": ["排查独立结构核验（AcceptanceService）异常后重跑独立验收"],
                    "agent_id": None, "error": reason}

    # ── 独立验收产物落盘 ────────────────────────────────────────────────
    def _persist(self, result: ValidationResult) -> None:
        try:
            from app.graph.stage_reports import StageReports
            StageReports(self.project_id, self.stage).validation(result.to_dict())
        except Exception:
            logger.warning("validation_agent 验收结论落盘失败（advisory）", exc_info=True)
