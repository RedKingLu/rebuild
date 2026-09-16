"""Gate service — DB-persisted CRUD with mandatory Audit writing (R9 P1-2).

Replaces the in-memory _gates dict with SQLAlchemy Gate model.
"""

import logging as _logging
import uuid
_logger = _logging.getLogger("rebuild.gate_service")

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.gate import Gate
from app.schemas.gate import (
    GateResponse, GateDecisionRequest,
    PolicyCheckRequest, PolicyCheckResponse,
    RiskAssessmentRequest, RiskAssessmentResponse,
)

VALID_DECISIONS = {"approve", "reject", "request_changes"}
STAGE_ORDER = ["p0", "p1", "p2", "p3", "p4", "p5", "p6"]
_DECISION_TO_STATUS = {
    "approve": "approved",
    "reject": "rejected",
    "request_changes": "changes_requested",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── P5 返工信号（V26.2 返工修复第 8 项，Q-RW-4）───────────────────────────
# 背景：P5 判定 rework_required 时，用户批准其 stage_promotion Gate 的真实语义是
# "接受返工、退回 P4 重跑"，不是"晋级到 P6"。这个决策必须同时被两处独立消费：
#   ① 图路由 make_router("p5")（app/graph/nodes.py）——决定图走到哪个节点；
#   ② 本文件 _apply_promotion——图未在跑时（drive_promotion=True 的直连路径）
#     独立推进 project.current_stage / run.stage_status。
# 这两处是两套完全独立的代码路径（一个走 GraphState，一个只有 Gate 行 g 可用，
# Gate 表没有自由格式的 metadata 列可持久化"这是一次返工批准"），若各自从
# verdict/文案等模糊信号反推，分支逻辑一旦漂移（比如日后新增一种失败类型），两处
# 就可能给出矛盾结论——图已经在跑 p4_work，但 DB 里 project.current_stage 却被
# 写成了 p6，状态撕裂。
#
# 解法：显式落盘一份"这个 gate_id 一旦被批准 = 退回某阶段"的标记文件，由判定
# rework 的一方（nodes.py 的 make_work_node，在 P5FailureRouter.route() 明确算出
# p4_rework_required=True 时）写入；两个消费方都读同一份文件，且都用 gate_id
# 精确匹配才采信——不匹配（比如文件是上一轮返工留下的旧标记，这一轮 P5 已经真正
# 通过）就当没有信号，走原有的"正常晋级"逻辑。文件路径落在 P5 的产物目录下，是
# 因为这个机制目前只服务 P5→P4 这一条路径（Q-RW-4 裁决的范围），不做成任意阶段
# 通用的返工机制。
_P5_REWORK_MARKER_REL = "artifacts/p5/p5_rework_decision.json"


def write_p5_rework_marker(project_id: str, *, gate_id: str, run_id: str,
                            target_stage: str, reason: str,
                            plan_delta_id: Optional[str] = None) -> None:
    """P5 判定需要返工时落盘"这个 gate_id 批准 = 退回 target_stage"的显式标记。

    由 nodes.py 的 make_work_node 在创建该 Gate 之后调用（此时 gate_id 已知）。
    失败只降级发声（不抛）：标记写入失败不该让 Gate 创建整体失败——退回路由会走不
    动，但至少不会误判为"正常晋级"（read 侧找不到匹配文件时同样保守地判无信号）。
    """
    import json
    from app.services import workspace_service
    try:
        p = workspace_service.workspace_path(project_id) / _P5_REWORK_MARKER_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "gate_id": gate_id,
            "run_id": run_id or "",
            "rework_target_stage": target_stage,
            "reason": reason,
            "plan_delta_id": plan_delta_id,
            "created_at": _now(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        _logger.warning("写入 P5 返工标记失败 project=%s gate=%s target=%s",
                        project_id, gate_id, target_stage, exc_info=True)


def read_p5_rework_marker(project_id: str, gate_id: str) -> Optional[dict]:
    """读取（若存在且 gate_id 精确匹配）本次批准是否携带"退回上一阶段"的标记。

    gate_id 不匹配（标记来自另一个 Gate，通常是上一轮返工留下的旧文件）→ 视为无
    信号，返回 None——这是防止旧标记误伤之后真正通过的同阶段 Gate 的唯一手段
    （文件本身不会在消费后删除，见模块顶部说明；匹配失败即安全，无需再显式清理）。
    """
    import json
    from app.services import workspace_service
    try:
        p = workspace_service.workspace_path(project_id) / _P5_REWORK_MARKER_REL
        if not p.exists():
            return None
        marker = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        _logger.warning("读取 P5 返工标记失败 project=%s gate=%s", project_id, gate_id,
                        exc_info=True)
        return None
    if not gate_id or marker.get("gate_id") != gate_id:
        return None
    return marker


# ── 共享守卫：晋级前的「阶段有无真实产物」判据（R17-2 V-R17-1B-2）────────────
#
# B-ACC-PROMOTION-DECISION-NOGUARD 解除条件③：该判据原本是 `app/api/routes_gates.py`
# 的模块私有函数，只有 `/gates/{gate_id}/decision` 一条路由能用；`/stages/{stage}/
# promotion-decision` 的图分支因此【完全没有】空壳晋级校验（该端点在图活跃时构造合成结果
# 直接返回，根本不调 stage_service.promote()，promote() 内的 422 校验整条不执行）。
#
# 处置（用户 2026-09-15 裁决 Q-B：放本文件的模块级函数，不新增 _gate_guards.py）：
# 提取到此处，`routes_gates` 与 `routes_stages` **import 同一份**。抄第二份等于制造
# 第三处需要同步的判据 —— 本批次两条缺陷的共同教训正是"同一件事有两份判据必然漂移"。
#
# 【勿改函数体】提取时函数体逐字未变（含下面 `logging.getLogger("rebuild.routes_gates")`
# 这个日志器名 —— 保留它是为了让"提取是纯移动、无行为变化"这一点可被 `git show` 逐行核对，
# 也让既有日志过滤规则不因本次移动而失效）。422 语义与错误文案同样逐字保留在两个调用点。
#
# 注意与本文件内 `GateService._stage_has_real_artifact`（方法，2 参数）的区别：那是
# `_apply_promotion` 内部用的既有判据（只看 task_graph，不看 artifact_refs，错误文案也不同）。
# 两者的合并【不在本批次范围】—— 合并会改动 R17-2 的既有 422 文案与语义，属独立议题。
def _stage_has_real_artifact(svc, run_id: str, stage: str, artifact_refs: list | None) -> bool:
    """校验某 run 在某阶段是否有真实产物（task_graph 存在 OR artifact_refs 非空）。"""
    # 1) artifact_refs 非空（gate 自身携带的产物引用）
    if artifact_refs:
        return True
    # 2) task_graph 表存在该 run+stage
    try:
        db = svc.run_service._db()
        try:
            from app.models.task_graph import TaskGraph
            return (
                db.query(TaskGraph)
                .filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage)
                .limit(1)
                .count()
                > 0
            )
        finally:
            db.close()
    except Exception as exc:
        import logging
        logging.getLogger("rebuild.routes_gates").warning("_stage_has_real_artifact 降级放行: %s", exc)
        return True


def _gate_to_response(g: Gate) -> GateResponse:
    # P2-C: graph capability must be a REAL probe, never the schema default
    # "not_connected" (state.py 红线：不得写死 not_connected). "live" when the
    # StateGraph compiles, else "degraded" (公理4, no hardcode).
    try:
        from app.graph.runtime import graph_capability_probe
        graph_cap = graph_capability_probe()
    except Exception:
        graph_cap = "degraded"
    return GateResponse(
        gate_id=g.gate_id,
        gate_type=g.gate_type,
        gate_status=g.gate_status,
        project_id=g.project_id,
        run_id=g.run_id or "",
        stage=g.stage or "",
        reason=g.reason or "",
        risk_level=g.risk_level or "L0",
        summary=g.summary or "",
        options=g.options or ["approve", "reject"],
        recommended_option=None,
        decision=g.decision,
        decided_by=None,
        decided_at=g.decided_at.isoformat() if g.decided_at else None,
        artifact_refs=g.artifact_refs or [],
        evidence_refs=g.evidence_refs or [],
        trace_refs=g.trace_refs or [],
        audit_ref=g.audit_ref,
        action_fingerprint=g.action_fingerprint,
        source_status="real",
        transition_mode=g.transition_mode or "real",
        graph_capability_status=graph_cap,
        checkpoint_ref=g.checkpoint_ref,
        interrupt_ref=g.interrupt_ref,
    )


class GateService:
    def __init__(self, services):
        self._svc = services

    def _db(self) -> Session:
        from app.core.database import get_session
        return get_session()

    def list_by_project(self, project_id: str) -> list[GateResponse]:
        db = self._db()
        try:
            gates = db.query(Gate).filter(Gate.project_id == project_id).order_by(Gate.gate_id).all()
            return [_gate_to_response(g) for g in gates]
        finally:
            db.close()

    def get(self, gate_id: str) -> Optional[GateResponse]:
        db = self._db()
        try:
            g = db.get(Gate, gate_id)
            return _gate_to_response(g) if g else None
        finally:
            db.close()

    def get_active(self, project_id: str) -> Optional[GateResponse]:
        db = self._db()
        try:
            g = db.query(Gate).filter(
                Gate.project_id == project_id,
                Gate.gate_status == "waiting_decision",
            ).first()
            return _gate_to_response(g) if g else None
        finally:
            db.close()

    def create(self, project_id: str, run_id: str | None, stage: str,
               gate_type: str, reason: str = "", risk_level: str = "L0",
               summary: str = "", options: list[str] | None = None,
               artifact_refs: list[str] | None = None,
               evidence_refs: list[str] | None = None,
               checkpoint_ref: str | None = None,
               interrupt_ref: str | None = None,
               action_fingerprint: str | None = None) -> GateResponse:
        """创建 Gate。

        action_fingerprint（B-ACC-GATE-APPROVAL-NOT-BOUND）：动作审批类 Gate 须传入
        「被审阅的那一份入参」的指纹，使批准可绑定到具体内容而不是仅绑定工具名。
        由调用方用 `tool_registry.action_args_fingerprint()` 计算（**只有一份实现**，
        与 Gate 展示用的入参规范化同源）。非动作审批类 Gate 传 None。
        """
        db = self._db()
        try:
            g = Gate(
                project_id=project_id,
                run_id=run_id or "",
                stage=stage,
                gate_type=gate_type,
                gate_status="waiting_decision",
                reason=reason,
                risk_level=risk_level,
                summary=summary,
                options=options or ["approve", "reject", "request_changes"],
                artifact_refs=artifact_refs or [],
                evidence_refs=evidence_refs or [],
                checkpoint_ref=checkpoint_ref,
                interrupt_ref=interrupt_ref,
                action_fingerprint=action_fingerprint,
            )
            db.add(g)
            db.commit()
            db.refresh(g)
            return _gate_to_response(g)
        finally:
            db.close()

    def decide(self, gate_id: str, req: GateDecisionRequest,
               drive_promotion: bool = True) -> tuple[Optional[GateResponse], Optional[dict]]:
        """Submit a Gate decision with full state advancement (R9 P1-2).

        drive_promotion: when False (LangGraph orchestration, R9-5-1), record the
        decision + Audit but DO NOT advance project/run stage here — the graph drives
        stage transitions via conditional edges (avoids double-advancement).
        """
        db = self._db()
        try:
            g = db.get(Gate, gate_id)
            if g is None:
                return None, None

            decision = (req.decision or "").strip().lower()
            if decision not in VALID_DECISIONS:
                raise ValueError(
                    f"非法 Gate 决策：{req.decision!r}（允许 {sorted(VALID_DECISIONS)}）"
                )

            # NEW-03: idempotency guard — one decision = one authoritative gate_decision
            # Audit. A graph-driven gate is decided TWICE for a single user action: the
            # REST route records it synchronously (drive_promotion=False), then the
            # background LangGraph gate node re-applies the SAME decision on resume via
            # _gate_backend.decide(). Without this guard both writes emit a gate_decision
            # Audit (double-write). When the gate is already in the target decided status
            # for this same decision, skip the re-write (no duplicate Audit, no state
            # churn) and return the existing gate. Non-graph gates hit this only on a
            # genuine double-submit, where skipping the duplicate is equally correct.
            if g.decision == decision and g.gate_status == _DECISION_TO_STATUS[decision]:
                return _gate_to_response(g), None

            g.gate_status = _DECISION_TO_STATUS[decision]
            g.decision = decision
            g.decided_at = datetime.now(timezone.utc)
            g.transition_mode = "real"
            db.commit()
            db.refresh(g)

            # D-07 / B-R22-GATE-REDECIDE-REDRIVE 次级项：Gate 一旦被决策，
            # project.active_gate 必须立刻不再指向它。该字段语义是「当前待决 Gate」，
            # 客户端（前端 GatePanel、轮询脚本）据此判断"是否还需要提交决策"。
            # 此前只有 stage_promotion 的 _apply_promotion 分支清理它，而：
            #   ① plan_presentation / plan_review / action_approval 等 gate_type 从不清理；
            #   ② 图驱动路径要等后台图整段跑完才由 routes_stages._run_graph_bg 更新，
            #      阶段执行期内（真实规模项目可达数分钟）该字段仍指向已决 Gate。
            # ⇒ 轮询客户端在这段窗口里会反复提交决策，正是 D-01 的诱因。故在决策落库后
            # 立即同步清理（此处是所有决策路径的唯一必经点：REST 路由与图内部重放都经 decide）。
            self._clear_active_gate_if_current(g)

            # Drive stage transition
            if g.gate_type == "stage_promotion" and drive_promotion:
                self._apply_promotion(g, decision)

            # Audit
            audit = self._svc.audit_writer.write(
                audit_type="gate_decision", gate_id=gate_id, risk_level=g.risk_level,
                action="gate_decision", decision=decision, reason=req.reason,
                project_id=g.project_id, run_id=g.run_id, stage=g.stage,
            )
            if audit and isinstance(audit, dict):
                g.audit_ref = audit.get("audit_id")
                db.commit()

            # R17.5 P2（item 4）：P2→P3 路线风险接受由 Audit `risk_acceptance` 承载（非新
            # gate_type，禁 route_decision_gate）。用户 approve P2→P3 stage_promotion Gate =
            # 接受 P2 评估的迁移路线风险 → 补记 risk_acceptance 审计（可追溯"谁在何时接受了何种
            # 路线风险"），不改 Gate 语义。仅在权威决策写一次（承 gate_decision 幂等保护之后）。
            if g.gate_type == "stage_promotion" and decision == "approve" and g.stage == "p2":
                try:
                    self._svc.audit_writer.write(
                        audit_type="risk_acceptance", gate_id=gate_id, risk_level=g.risk_level,
                        action="p2_to_p3_route_risk_accepted",
                        decision="accepted",
                        reason=(req.reason or "用户批准 P2→P3 晋级，接受 P2 评估的迁移路线风险"),
                        project_id=g.project_id, run_id=g.run_id, stage=g.stage,
                    )
                except Exception:
                    _logger.warning("P2→P3 risk_acceptance 审计写入失败（advisory）", exc_info=True)

            # D-109：用户批准 P1→P2 stage_promotion Gate = 批准技术路线选型 → 落 project.tech_selection
            # 成为项目红线（贯穿注入 P2/P4 + 验收校验）。裁决携带 req.tech_selection 则用其覆盖 LLM 建议，
            # 否则采用 P1 产出的 tech_selection 提案（artifacts/p1/tech_selection.json）。仅在权威决策写一次。
            if g.gate_type == "stage_promotion" and decision == "approve" and g.stage == "p1":
                try:
                    self._persist_tech_selection(g, getattr(req, "tech_selection", None))
                except Exception:
                    _logger.warning("P1→P2 技术选型红线落库失败（advisory）", exc_info=True)

            return _gate_to_response(g), audit
        finally:
            db.close()

    def _clear_active_gate_if_current(self, g: Gate) -> None:
        """决策生效后把 project.active_gate 从本 Gate 上摘掉（D-07）。

        只在它确实指向本 Gate 时清理 —— 否则会误清另一个真实待决 Gate（例如后台图已
        创建下一阶段 Gate 并把 active_gate 指向了它）。
        比较用的 project 走本服务自己的新 session 读取（`_db()`），不用
        `svc.project_service` 那个长生命周期单例 session —— 后者的 identity map 可能持有
        别的 session 早前写入前加载的旧 Project，据此比较会错判。写入仍复用
        ProjectService.update（保持与 _apply_promotion 同一条写路径，不另开第二条）。
        置空值用 `""` 而非 `None`：ProjectService.update 会过滤 None（那是"本次不更新该
        字段"的通用语义，不得为了本需求放宽它，否则影响所有字段的更新行为），`""` 是本文件
        _apply_promotion 既有的置空写法（同一约定，不另立第二种）。
        失败只降级发声（不抛）：清理失败不该让一个已成功落库的决策变成 4xx/5xx；
        客户端仍可用 gate_status 判断待决状态。
        """
        try:
            from app.models.project import Project
            _db = self._db()
            try:
                project = _db.get(Project, g.project_id)
                points_at_this_gate = (project is not None
                                       and (project.active_gate or "") == g.gate_id)
            finally:
                _db.close()
            if points_at_this_gate:
                self._svc.project_service.update(g.project_id, active_gate="")
        except Exception:
            _logger.warning("Gate %s 决策后清理 project.active_gate 失败（project=%s）——"
                            "该字段可能仍指向已决 Gate，客户端须以 gate_status 判断待决状态",
                            g.gate_id, g.project_id, exc_info=True)

    def _persist_tech_selection(self, g: Gate, override: dict | None) -> None:
        """D-109：把批准的技术路线选型落 project.tech_selection（项目红线）。

        override（用户 gate 裁决时修改的选型）优先；否则读 P1 产出的选型提案
        artifacts/p1/tech_selection.json 的 selection 段。补 status=approved + 裁决元数据。
        无可用选型（提案缺失/未完成且无 override）→ 不写（诚实，不伪造空红线），仅记日志。
        """
        selection_body: dict | None = None
        source = "gate_override" if override else "p1_proposal"
        if isinstance(override, dict) and override:
            selection_body = override
        else:
            try:
                from app.services import workspace_service
                p = (workspace_service.workspace_path(g.project_id)
                     / "artifacts" / "p1" / "tech_selection.json")
                if p.exists():
                    import json as _json
                    doc = _json.loads(p.read_text(encoding="utf-8"))
                    if doc.get("status") == "proposed" and isinstance(doc.get("selection"), dict):
                        selection_body = doc.get("selection")
            except Exception:
                _logger.warning("读取 P1 tech_selection 提案失败（advisory）", exc_info=True)
        if not selection_body:
            _logger.warning("P1→P2 approve：无可用技术选型提案（未完成/缺失且无 override），"
                            "不写 project.tech_selection（诚实，不伪造空红线） project=%s", g.project_id)
            return
        redline = dict(selection_body)
        redline.update({
            "status": "approved",
            "source": source,
            "decided_at": _now(),
            "decided_gate_id": g.gate_id,
        })
        self._svc.project_service.update(g.project_id, tech_selection=redline)
        try:
            self._svc.audit_writer.write(
                audit_type="tech_selection", gate_id=g.gate_id, risk_level=g.risk_level,
                action="p1_to_p2_tech_selection_approved", decision="approved",
                reason=f"用户批准 P1→P2 技术路线选型红线（来源={source}）",
                project_id=g.project_id, run_id=g.run_id, stage=g.stage)
        except Exception:
            _logger.warning("tech_selection 审计写入失败（advisory）", exc_info=True)

    def mark_consumed(self, gate_id: str) -> bool:
        """One-time consumption of an action_approval Gate (R18→R17.2).

        Sets gate_status to "consumed" so a single approval cannot be reused for a
        second high-risk tool execution. gate_status is a free-form String(32) column
        (no schema Literal / migration needed). _resolve_action_gate only matches
        "approved"/"waiting_decision", so a consumed gate no longer authorizes a
        re-dispatch — the next call opens a fresh pending gate.

        Writes a gate_decision Audit (action="gate_consumed", decision="consumed") so
        consumption is visible, never silent. Returns True on success, False if the gate
        does not exist.
        """
        db = self._db()
        try:
            g = db.get(Gate, gate_id)
            if g is None:
                _logger.warning("mark_consumed: gate %s 不存在，无法消费", gate_id)
                return False
            g.gate_status = "consumed"
            db.commit()
            db.refresh(g)
            try:
                self._svc.audit_writer.write(
                    audit_type="gate_decision", gate_id=gate_id, risk_level=g.risk_level,
                    action="gate_consumed", decision="consumed",
                    reason="高风险工具审批通过并成功执行后，一次性消费该 action_approval Gate",
                    project_id=g.project_id, run_id=g.run_id, stage=g.stage,
                )
            except Exception:
                # 发声：消费审计写入失败必须可见（审计链完整性），但不回滚已生效的消费。
                _logger.warning("mark_consumed: 消费审计写入失败 gate=%s", gate_id, exc_info=True)
            return True
        finally:
            db.close()

    def _apply_promotion(self, g: Gate, decision: str) -> None:
        """Advance project/run state per a stage-promotion decision."""
        ps = self._svc.project_service
        try:
            project = ps.get(g.project_id)
        except Exception:
            _logger.warning(f"_apply_promotion: failed to get project {g.project_id}", exc_info=True)
            project = None
        cur = (g.stage or (getattr(project, "current_stage", None) if project else None) or "p0").lower()

        if decision == "approve":
            # R17-2 V-R17-1B-2/P1: gate 晋级强绑阶段产物。stage_promotion 前校验该
            # run+stage 有真实产物（task_graph 存在），杜绝空壳晋级。plan_review 豁免。
            if g.gate_type == "stage_promotion" and g.run_id:
                if not self._stage_has_real_artifact(g.run_id, cur):
                    raise ValueError(
                        f"阶段 {cur} 晋级被拒绝：run {g.run_id} 在该阶段无真实产物"
                        f"（task_graph 未创建）。请先完成阶段执行再申请晋级。"
                    )
            # V26.2 返工修复第 8 项（Q-RW-4）：这条是"直连"晋级路径（drive_promotion=
            # True，图未在跑或图驱动检测失败时的兜底）。cur=="p5" 时必须检查这个 Gate
            # 是否携带落盘的返工标记——检查方式、判据来源与图路由 make_router("p5")
            # 完全相同（同一份文件、同一个 gate_id 精确匹配规则，见
            # read_p5_rework_marker 顶部注释），这是保证两条独立代码路径不给出矛盾
            # 结论的唯一手段。非 p5 阶段完全不做这个检查，不影响其它阶段既有行为。
            rework_target: Optional[str] = None
            if cur == "p5":
                _marker = read_p5_rework_marker(g.project_id, g.gate_id)
                rework_target = _marker.get("rework_target_stage") if _marker else None
            if rework_target:
                if g.run_id:
                    self._svc.run_service.set_stage_status(g.run_id, cur, "rework_required")
                    self._svc.run_service.set_stage_status(g.run_id, rework_target, "in_progress")
                if project is not None:
                    try:
                        ps.update(g.project_id, current_stage=rework_target, active_gate="")
                    except Exception:
                        _logger.warning(f"_apply_promotion: rework update failed for project {g.project_id}", exc_info=True)
                return
            try:
                nxt = STAGE_ORDER[STAGE_ORDER.index(cur) + 1]
            except (ValueError, IndexError):
                nxt = cur
            if g.run_id:
                self._svc.run_service.set_stage_status(g.run_id, cur, "completed")
                if nxt != cur:
                    self._svc.run_service.set_stage_status(g.run_id, nxt, "in_progress")
            if project is not None:
                try:
                    ps.update(g.project_id, current_stage=nxt, active_gate="")
                except Exception:
                    _logger.warning(f"_apply_promotion: approve update failed for project {g.project_id}", exc_info=True)
        elif decision == "reject":
            if g.run_id:
                self._svc.run_service.set_stage_status(g.run_id, cur, "blocked")
            if project is not None:
                try:
                    ps.update(g.project_id, active_gate="")
                except Exception:
                    _logger.warning(f"_apply_promotion: reject update failed for project {g.project_id}", exc_info=True)
        elif decision == "request_changes":
            if g.run_id:
                self._svc.run_service.set_stage_status(g.run_id, cur, "changes_requested")
            if project is not None:
                try:
                    ps.update(g.project_id, active_gate="")
                except Exception:
                    _logger.warning(f"_apply_promotion: request_changes update failed for project {g.project_id}", exc_info=True)

    def _stage_has_real_artifact(self, run_id: str, stage: str) -> bool:
        """校验某 run 在某阶段是否有真实产物（task_graph 存在）。

        最小阈值：task_graph 表有 run_id+stage 记录。未来可加 artifact/output_code。
        """
        db = self._db()
        try:
            from app.models.task_graph import TaskGraph
            return (
                db.query(TaskGraph)
                .filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage)
                .limit(1)
                .count()
                > 0
            )
        except Exception as exc:
            # task_graph 表不存在时（旧环境）降级放行，避免阻断
            _logger.warning("_stage_has_real_artifact 查询失败，降级放行: %s", exc)
            return True
        finally:
            db.close()

    def policy_check(self, req: PolicyCheckRequest) -> PolicyCheckResponse:
        from app.services.mode_policy import authorize_action, risk_for_action
        ctx = req.context or {}
        # Risk: explicit request value wins; else derive from action type (Q-5 single source).
        risk = req.risk_level if (req.risk_level and req.risk_level != "L0") else risk_for_action(req.action_type)
        result = authorize_action(
            mode=ctx.get("mode", "plan"),
            risk_level=risk,
            action=req.action_type or "policy_check",
            in_plan=bool(ctx.get("in_plan", False)),
        )
        return PolicyCheckResponse(
            check_id=f"pc-{uuid.uuid4().hex[:6]}",
            allowed=result["decision"] != "require_confirmation",
            reason=result["reason"],
            required_gate=result["decision"] == "require_confirmation",
            source_status="real",
        )

    def risk_assess(self, req: RiskAssessmentRequest) -> RiskAssessmentResponse:
        from app.services.mode_policy import authorize_action, risk_for_action
        ctx = req.context or {}
        # R9-5-7 T14: derive mode + risk from request context, not hardcoded plan/L1.
        mode = ctx.get("mode", "plan")
        risk = ctx.get("risk_level") or risk_for_action(req.action_type)
        result = authorize_action(
            mode=mode, risk_level=risk,
            action=req.action_type or "risk_assessment",
            in_plan=bool(ctx.get("in_plan", False)),
        )
        return RiskAssessmentResponse(
            assessment_id=f"ra-{uuid.uuid4().hex[:6]}",
            risk_level=result["risk_level"],
            summary=result["reason"],
            source_status="real",
        )
