"""被 fail-closed 拦下、尚未执行的动作 —— 阶段产物侧的可见性（B-ACC-HELD-ACTION-INVISIBLE）。

台账 `B-ACC-HELD-ACTION-INVISIBLE`（P1）的形态：Gate 把高风险动作拦在**执行之前**，这是
**正确的 fail-closed**（实测 `audits.jsonl` 里零 `run_safe_command` 执行记录 ⇒ 该命令确实
从未执行），且解除条件 ④ 明确禁止"改成不拦"。缺陷不在拦，而在**拦了没人说**：

  · Trace 层诚实记了（`trace_type=tool_call` / `summary="run_safe_command: awaiting_approval"`）；
  · 但对 P0 全部 10 个产物 grep `awaiting_approval|待审批|挂起|未执行|被拦|action_approval|gate-22343c`
    ⇒ **零命中**，阶段仍报 `verdict: accepted / issues: [] / risks: [] / honest_notes: ""`。
  ⇒ 读验收报告的人无从得知"有一个 L4 动作被挂起未执行"，只能去翻 Trace。这与 D-07 家族
    （诚实信号建好却无消费方）同一模式。

本模块提供**唯一一份**采集实现，由两个消费方共用（不抄第二份判据）：
  ① `graph/stage_reports.gate_brief()` → 写进 `artifacts/{stage}/{stage}_gate_brief.json`
     的 `held_actions` 段，并把摘要句并入 `honest_notes`（解除条件 ①②）；
  ② `services/validation_agent` → 写进 `artifacts/{stage}/{stage}_validation.json`
     的 `held_actions` 段 + 一条 `checks` 记录。

**刻意不做的事**（各有理由，勿"顺手"加上）：
  · 不 flip `passed` / `verdict`：动作被挂起是**设计如此**（`action_approval` Gate 不阻断
    阶段推进，公理 6），把它变成阶段失败会改变用户可见行为，超出本条解除条件；
  · 不写进 `issues`：`review_pass.StageLoop` 会拿 `review.issues` 去驱动返工重跑
    （review_pass.py:122），塞进去等于让"有个动作等审批"触发整阶段重跑。故走
    `held_actions` 独立段 + `honest_notes` + `checks` 三处可见通道；
  · 不做任何 fail-open：采集失败只降级为空列表 + 日志发声，绝不因为采集不到就宣称"无挂起"
    —— 见 `collect_held_actions` 末段。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("rebuild.held_actions")


def collect_held_actions(project_id: str, run_id: str, stage: str) -> list[dict]:
    """本 (run, stage) 上因待审批而**未执行**的动作清单（按 gate_id 有序）。

    判据复用 `gate_service._is_flow_blocking()` —— "动作类待决 Gate" 精确定义为
    "待决且**不属于**流程阻塞类"，与 `get_active()` 的分流用**同一个函数**。不另立
    gate_type 名单：日后新增任何动作审批类型（`l5_high_risk_command` /
    `community_resource_introduction` / `desensitization_release` …）自动被覆盖。

    run_id 过滤规则：`run_id` 为空的 Gate 视为"未绑定 run"，一并计入（`_create_risk_gate`
    在无 run 上下文时会写空串）；否则须与本 run 相同 —— 别的 run 的挂起动作不该出现在本
    阶段产物里（这正是 B-ACC-PROMOTE-DIRECT-RUNBLIND 那类跨 run 串位的教训）。
    stage 过滤同理，Gate 自己的 stage 为空时一并计入。
    """
    try:
        from app.dependencies import get_services
        from app.services.gate_service import _is_flow_blocking
        gates = get_services().gate_service.list_by_project(project_id)
    except Exception:
        # 发声但不抛：阶段产物写入不能因为附加信息采集失败而整体失败。
        # 返回空列表**不等于**"无挂起动作"——故此处必须留下 warning，让人能在日志里发现
        # "这一份产物的 held_actions 段不可信"。
        logger.warning("采集挂起动作失败 project=%s run=%s stage=%s —— 本次 held_actions "
                       "段为空【不代表无挂起动作】", project_id, run_id, stage, exc_info=True)
        return []

    held: list[dict] = []
    for g in gates:
        if g.gate_status != "waiting_decision":
            continue
        if _is_flow_blocking(g):
            continue                      # 流程阻塞类 Gate 不是"被挂起的动作"
        if (g.run_id or "") and run_id and (g.run_id or "") != run_id:
            continue
        if (g.stage or "") and stage and (g.stage or "").lower() != (stage or "").lower():
            continue
        held.append({
            "gate_id": g.gate_id,
            "gate_type": g.gate_type,
            "risk_level": g.risk_level or "L0",
            # reason 是短句（"高风险工具 X（风险 L4）执行前需人工审批"）；**不放 summary**
            # —— summary 里带脱敏后的入参，体量不可控且已在 Gate 自身可读，产物里放引用即可。
            "reason": g.reason or "",
            "status": "awaiting_approval_not_executed",
        })
    return held


def held_actions_note(held: list[dict]) -> str:
    """把挂起动作渲染成一句可直接并入 `honest_notes` 的中文说明。空清单返回空串。"""
    if not held:
        return ""
    ids = "、".join(h["gate_id"] for h in held)
    return (f"本阶段有 {len(held)} 个动作因等待人工审批而未执行（fail-closed：动作被拦下，"
            f"阶段未被阻塞）：{ids}。这些动作的效果不在本阶段产物中，"
            f"阅读产物时须把它们当作未发生。")


def merge_held_actions_note(honest_notes: Optional[str], held: list[dict]) -> str:
    """把挂起动作说明并入既有 honest_notes（既有内容在前，不覆盖）。"""
    note = held_actions_note(held)
    base = (honest_notes or "").strip()
    if not note:
        return base
    return f"{base} {note}".strip()
