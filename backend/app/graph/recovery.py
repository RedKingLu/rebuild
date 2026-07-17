"""Startup restart-recovery for orphaned Runs (R17.3-6 WP-8 / GAP-BG-1).

「退出 UI ≠ 停止任务」已由 R17-6 的 fire-and-forget 后台图线程成立（关闭前端不影响
后台图续跑）；本模块补齐更强的一层：**进程崩溃 / 服务重启后，仍在执行中的 run 自动从
LangGraph checkpoint 恢复**。

设计（复用既有机制，不自研状态机，D-037）：
  - 启动时（main.lifespan）扫描 DB 中 run_status == "running" 的 run。进程刚起，尚未
    服务任何请求 → 没有任何线程在执行这些 run，故凡处于 "running" 的都是被中断的孤儿。
  - 对每个孤儿读取其 LangGraph checkpoint 状态（thread_id == run_id，D-085），据此分类：
      * 无 checkpoint（created_at is None）      → 不可恢复：显式置 failed + 原因（D-097，不静默）
      * 图已到达 END（next 为空且有 checkpoint）  → 已完成：把 run_status 对账为终态
      * 停在用户 Gate interrupt（snap.interrupts）→ 真正等待用户决策：对账为 waiting_gate
                                                    （绝不伪造 approve/reject 决策）
      * next 非空且无 interrupt（work 节点未跑完）→ 可恢复：后台 ainvoke(None) 从 checkpoint
                                                    续跑（复用 routes_stages._run_graph_bg）
  - 幂等：终态 run 不在扫描集内（永不重复 resume 已完成 run）；进程内 _recovering 集合防
    「同进程内重复 startup」重复调度同一 run 的续跑（防重启风暴）。

Trace/Audit：每个恢复动作（resume / 对账 / failed）落 Trace，关键动作落 Audit。
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger("rebuild.graph.recovery")

# 进程内正在恢复（后台续跑中）的 run_id 集合 —— 幂等护栏，防同进程内重复调度同一 run。
# 后台续跑结束（终态/等待/失败）后从集合移除。真实的另一次进程重启会得到空集合，
# 从而能再次恢复（上一个进程的线程已随进程消失）——这是正确行为。
_recovering: set[str] = set()
_recovering_lock = threading.Lock()


def _list_running_runs() -> list[tuple[str, str, str]]:
    """返回 DB 中 run_status == 'running' 的 (run_id, project_id, current_stage)。"""
    from app.core.database import get_session
    from app.models.run import Run

    db = get_session()
    try:
        rows = db.query(Run).filter(Run.run_status == "running").all()
        return [(r.run_id, r.project_id, (r.current_stage or "p0")) for r in rows]
    finally:
        db.close()


def _classify(snap) -> str:
    """按 checkpoint 快照分类孤儿 run。

    返回 no_checkpoint / completed / waiting_gate / resumable。
    """
    if snap is None or getattr(snap, "created_at", None) is None:
        return "no_checkpoint"
    nxt = getattr(snap, "next", ()) or ()
    if not nxt:
        return "completed"
    # 停在用户 Gate 的 dynamic interrupt（LangGraph 顶层 interrupts 聚合，或任务级 interrupts）。
    if getattr(snap, "interrupts", None) or any(
            getattr(t, "interrupts", None) for t in (getattr(snap, "tasks", None) or ())):
        return "waiting_gate"
    return "resumable"


async def _recovery_resume_coro(run_id: str, project_id: str, stage: str) -> None:
    """后台续跑协程：复用 routes_stages._run_graph_bg(decision=None) 从 checkpoint 续跑，
    完成后（无论成功/失败）把 run_id 移出 _recovering，使进程后续状态可再判定。"""
    from app.api.routes_stages import _run_graph_bg
    try:
        await _run_graph_bg(run_id, None, project_id, stage)
    finally:
        with _recovering_lock:
            _recovering.discard(run_id)


def _schedule_resume(run_id: str, project_id: str, stage: str) -> None:
    """把一个可恢复 run 的续跑调度到后台守护线程（复用 R17-6 fire-and-forget 机制）。"""
    from app.api.routes_stages import _ensure_graph_task
    _ensure_graph_task(_recovery_resume_coro(run_id, project_id, stage))


def _trace_audit(svc, *, run_id: str, project_id: str, stage: str,
                 action: str, summary: str, audit: bool = False,
                 decision: str = "", risk_level: str = "L1") -> None:
    """恢复动作落 Trace（必写）+ 关键动作落 Audit（audit=True）。best-effort，不阻断恢复。"""
    try:
        svc.trace_writer.write("run_recovery", action=action, summary=summary,
                               project_id=project_id, run_id=run_id, stage=stage)
    except Exception:
        logger.debug("run_recovery Trace 写入失败（advisory）run=%s", run_id, exc_info=True)
    if audit:
        try:
            svc.audit_writer.write(audit_type="run_recovery", action=action,
                                   decision=decision, risk_level=risk_level,
                                   project_id=project_id, run_id=run_id, stage=stage,
                                   reason=summary)
        except Exception:
            logger.debug("run_recovery Audit 写入失败（advisory）run=%s", run_id, exc_info=True)


async def recover_interrupted_runs() -> dict:
    """启动时扫描并恢复被中断的 running run。返回动作计数摘要（供日志/测试断言）。

    非阻塞：分类读取（checkpoint 快照）在当前事件循环内快速完成；真正的续跑
    （可能含 LLM / 源码物化等耗时）通过 fire-and-forget 后台线程执行，不阻塞启动。
    任何异常都被吞掉并发声（best-effort），绝不让恢复扫描拖垮应用启动。
    """
    summary = {"scanned": 0, "resumed": 0, "reconciled_waiting_gate": 0,
               "reconciled_completed": 0, "failed_no_checkpoint": 0, "skipped": 0}

    running = _list_running_runs()
    if not running:
        return summary
    summary["scanned"] = len(running)

    from app.dependencies import get_services
    from app.graph.checkpoint import open_standalone_checkpointer, thread_config
    from app.graph.graph import build_graph

    svc = get_services()
    conn, saver = await open_standalone_checkpointer()
    try:
        g = build_graph().compile(checkpointer=saver)
        for run_id, project_id, stage in running:
            with _recovering_lock:
                if run_id in _recovering:
                    summary["skipped"] += 1
                    continue

            snap: Optional[object] = None
            try:
                snap = await g.aget_state(thread_config(run_id))
            except Exception:
                logger.warning("恢复：读取 checkpoint 状态失败 run=%s（按无 checkpoint 处理）",
                               run_id, exc_info=True)
                snap = None

            kind = _classify(snap)

            if kind == "no_checkpoint":
                # 不可恢复：显式置 failed（D-097 不静默），记录原因。
                try:
                    svc.run_service.set_run_status(run_id, "failed")
                except Exception:
                    logger.warning("恢复：置 run=%s failed 失败", run_id, exc_info=True)
                _trace_audit(svc, run_id=run_id, project_id=project_id, stage=stage,
                             action="recover_failed_no_checkpoint",
                             summary=(f"run {run_id} 处于 running 但无 LangGraph checkpoint，"
                                      f"进程重启后不可恢复 → 显式置失败态（不静默）"),
                             audit=True, decision="failed", risk_level="L2")
                summary["failed_no_checkpoint"] += 1

            elif kind == "completed":
                # 图已抵达 END：DB 状态漏同步 → 对账为终态。
                vals = getattr(snap, "values", None) or {}
                rs = vals.get("run_status")
                final = rs if rs in ("completed", "blocked") else "completed"
                try:
                    svc.run_service.set_run_status(run_id, final)
                except Exception:
                    logger.warning("恢复：对账 run=%s 终态失败", run_id, exc_info=True)
                _trace_audit(svc, run_id=run_id, project_id=project_id, stage=stage,
                             action="recover_reconcile_terminal",
                             summary=(f"run {run_id} 的图已到达终点，对账 run_status={final}"))
                summary["reconciled_completed"] += 1

            elif kind == "waiting_gate":
                # 真正在等用户 Gate 决策：对账为 waiting_gate（绝不伪造决策）。
                try:
                    svc.run_service.set_run_status(run_id, "waiting_gate")
                except Exception:
                    logger.warning("恢复：对账 run=%s waiting_gate 失败", run_id, exc_info=True)
                _trace_audit(svc, run_id=run_id, project_id=project_id, stage=stage,
                             action="recover_reconcile_waiting_gate",
                             summary=(f"run {run_id} 停在用户 Gate interrupt，对账 "
                                      f"run_status=waiting_gate（等待用户决策，不自动放行）"))
                summary["reconciled_waiting_gate"] += 1

            else:  # resumable —— work 节点未跑完，从 checkpoint 续跑
                with _recovering_lock:
                    _recovering.add(run_id)
                _trace_audit(svc, run_id=run_id, project_id=project_id, stage=stage,
                             action="recover_resume_midwork",
                             summary=(f"run {run_id} 在 {stage} 工作节点被中断（无 Gate 等待），"
                                      f"从 LangGraph checkpoint 自动续跑"),
                             audit=True, decision="resume", risk_level="L1")
                try:
                    _schedule_resume(run_id, project_id, stage)
                    summary["resumed"] += 1
                except Exception:
                    logger.error("恢复：调度 run=%s 后台续跑失败", run_id, exc_info=True)
                    with _recovering_lock:
                        _recovering.discard(run_id)
    finally:
        await conn.close()

    return summary


def reset_recovery_state_for_test() -> None:
    """测试辅助：清空进程内恢复护栏集合。"""
    with _recovering_lock:
        _recovering.clear()
