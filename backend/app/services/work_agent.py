"""Stage WorkAgent — 阶段主任务编排主体 (R17.3-6 WP-2 批 A, P1 样例).

WP-2 全量阶段级 Agent 工作流实现的 WorkAgent 部分。WorkAgent 是阶段主任务的
**编排层**（不是 handler 直调 service，r3 约束 2）。它复用现有资产（不推倒）：

  - context_assembler.assemble_context(include_body=True, skill_disclosure="full")
      → C0-C6 装配 + Stage Skill 正文加载（AGT-04 修复：正文加载执行，非仅元数据）
  - FullStackProfiler                → 作为确定性 Tool 调用（P1 主任务），零改写
  - AETService.write_evidence(claim=) → fact-evidence 登记为可查询 Evidence（EVI-01 折入）
  - StageReports.work_plan/gate_brief/claim_evidence_map → 独立落盘报告（Q-WP2-3）

职责链（P1 确定性阶段，Q-WP2-4：确定性合成，无需 LLM Key）：
  ① 上下文装配（C0-C6 + 相关 Case/Knowledge）
  ② 加载并使用对应 Stage Skill 正文
  ③ 调用确定性 Tool（FullStackProfiler）
  ④ 汇总产物落盘
  ⑤ 基于真实项目事实确定性合成动态工作计划（非 handler 静态类属性模板）
  ⑥ 生成 Gate Brief（做了什么/关键产物/风险）
  ⑦ 构造 fact-evidence map，每条 fact 绑定 artifact/evidence/trace/audit 引用

WorkAgent **不负责**：独立验收（→ ValidationAgent）、批准 Gate、标记阶段 completed、
绕过 Policy（承 seed node_worker forbidden）。

挂点（D-037，不新增图节点）：make_work_node 内作为 StageLoop.execute_fn。

LLM 阶段（P2/P3/P4）路径由批 B 实现（复用 AgentLoop 工具循环内核）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services import workspace_service
from app.services.full_stack_profiler import LANG_EXTENSIONS, BUILD_FILES, SKIP_DIRS

logger = logging.getLogger("rebuild.work_agent")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 各阶段 task_type 映射（Stage Skill 选取 + 上下文装配 + 上下文 recipe）。
_STAGE_TASK_TYPE = {
    "p0": "onboarding",
    "p1": "profiling",
    "p2": "assessment",
    "p3": "planning",
    "p4": "execution",
    "p5": "verification",
    "p6": "delivery",
}

# LLM 主任务阶段：claim-evidence map（LLM 内联引用）+ LLM 语义 ValidationAgent。
# R17.5 WP-1：P0/P1 纳入 LLM 执行路径——P0 接入识别 + P1 建档识别（技术栈/依赖/入口/配置/infra/
# 测试断言/盲区）由 Node Worker Agent(经 ModelGateway, D-098) 对采集事实包 + 上游产物推理产出
# （AGENTS §2.3）；确定性只做采集（materialize/index/collect_facts）与验证（原始黄金真跑捕获，
# D-106）。其余（P5/P6）为确定性主任务：fact-evidence map。
_LLM_STAGES = {"p0", "p1", "p2", "p3", "p4"}

# R17.5 WP-3 (HC-06): 语言/主语言【识别】已下放给 P0 LLM（Node Worker Agent）。work_agent
# 的 _scan_project_facts 仅做【采集】——原始扩展名计数 + 构建文件候选，用于合成"动态工作计划"
# 的叙述（非权威识别）；不再做 .NET/框架加权主语言判定（旧 _framework_primary_language 已删，
# 那是按栈枚举的样本值硬编码规则，§10-26）。数据/文档扩展不计入语言（采集口径，避免 .json 被当语言）。
_NON_LANG_EXTS = {".json", ".yaml", ".yml", ".xml", ".md", ".txt", ".lock", ".rst"}

# 各阶段上游产物（供 claim-evidence 绑定「引用了哪些上游证据」；§3.6 通用，非硬编码 MicroOA）。
# D-107: P0-P3 产物均位于 artifacts/{stage}/（R17.5 P3 轮统一分层）；P4-P6 遵循，后续轮次。
_STAGE_UPSTREAM_ARTIFACTS = {
    "p2": ["artifacts/p1/tech_stack.json", "artifacts/p1/p2_input_manifest.json",
           "artifacts/p1/dependency_draft.json"],
    "p3": ["artifacts/p2/p2_risk_list.json", "artifacts/p2/p2_assessment_report.json",
           "artifacts/p2/p2_blocker_list.json"],
    "p4": ["artifacts/p3/p3_task_graph.json", "artifacts/p3/p3_task_plans.json"],
}


# ── D-09（批次 C）：P2 结构化清单产物槽位 ────────────────────────────────────
# tool_result 键 → (产物文件名, artifact_type, 中文清单名)。真跑实测：评估报告识别到问题，
# 四类清单却全是 `items: []`——空数组会被下游与用户读成"已检查且无风险"，属虚假事实断言
# （公理3 / D-097）。WorkAgent 编排层据此对账：能派生则派生，派生不出则诚实标注。
_P2_LIST_ARTIFACTS = {
    "risk_list": ("p2_risk_list.json", "risk_assessment", "风险项"),
    "blocker_list": ("p2_blocker_list.json", "blocker_list", "阻塞项"),
    "validation_gap_list": ("p2_validation_gaps.json", "validation_gap", "验证缺口"),
    "resource_needs": ("p2_resource_needs.json", "resource_needs", "资源需求"),
}

# 派生只做【结构化搬运】：LLM 把清单写进 assessment_report 子对象而非顶层锚点键时，按【键名】
# 把同类清单搬上来。绝不做语义研判——"某段描述算不算风险"归 LLM（AGENTS §2.3 / §10-25），
# 确定性只搬运。
_P2_DERIVE_KEY_HINTS = {
    "risk_list": ("risk_list", "risks", "risk_items"),
    "blocker_list": ("blocker_list", "blockers"),
    "validation_gap_list": ("validation_gap_list", "validation_gaps", "verification_gaps"),
    "resource_needs": ("resource_needs", "resource_requirements", "resources_needed"),
}
_P2_DERIVE_MAX_DEPTH = 3


def _lift_lists_by_key(node, hints: tuple, *, path: str = "", depth: int = 0) -> list:
    """在 dict 树里按【键名】提取同类清单（结构化搬运，非语义研判）。返回 [(字段路径, 原始列表)]。"""
    out: list = []
    if depth > _P2_DERIVE_MAX_DEPTH or not isinstance(node, dict):
        return out
    for k, v in node.items():
        cur = f"{path}.{k}" if path else str(k)
        if isinstance(v, list):
            if v and str(k).lower() in hints:
                out.append((cur, v))
        elif isinstance(v, dict):
            out.extend(_lift_lists_by_key(v, hints, path=cur, depth=depth + 1))
    return out


@dataclass
class WorkAgentResult:
    """WorkAgent 执行输出契约（落盘 + 返回给 StageLoop）。

    保留 `artifacts`/`produced_artifacts` 键以兼容既有 StageLoop / make_work_node
    的领域产物汇集路径（NEW-05：反映真实写入）。
    """

    stage: str
    status: str = "completed"                         # completed | blocked | failed
    agent_id: Optional[str] = None
    work_plan_ref: Optional[str] = None
    artifacts: list = field(default_factory=list)     # 真实落盘产物 refs（含域产物）
    evidence_refs: list = field(default_factory=list) # 经 AET 登记的 Evidence id
    claim_evidence_map_ref: Optional[str] = None
    gate_brief_ref: Optional[str] = None
    gate_brief_partial: dict = field(default_factory=dict)
    skill_loaded: dict = field(default_factory=dict)
    context_trace: dict = field(default_factory=dict)
    tool_calls: list = field(default_factory=list)
    items_completed: int = 0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "status": self.status,
            "agent_id": self.agent_id,
            "work_plan_ref": self.work_plan_ref,
            "artifacts": list(self.artifacts),
            "produced_artifacts": list(self.artifacts),
            "evidence_refs": list(self.evidence_refs),
            "claim_evidence_map_ref": self.claim_evidence_map_ref,
            "gate_brief_ref": self.gate_brief_ref,
            "gate_brief_partial": self.gate_brief_partial,
            "skill_loaded": self.skill_loaded,
            "context_trace": self.context_trace,
            "tool_calls": self.tool_calls,
            "items_completed": self.items_completed,
            "reason": self.reason,
            "reviewer": "work_agent",
        }


class WorkAgent:
    """阶段主任务编排主体（handler-Tool 编排路径）。

    Args:
      stage / project_id / run_id
      tracer / auditor          — 进程单例（可 None）
      db                        — 复用会话（可 None；AET/上下文各自取会话）
      aet                       — AETService（可注入，默认 AETService(None)）
      handler                   — 阶段 handler（重定位为 WorkAgent 可调用的确定性/LLM Tool）；
                                  可注入以便测试注入 gateway；默认从图 registry 取。
    """

    def __init__(self, stage: str, project_id: str, run_id: str = "", *,
                 tracer=None, auditor=None, db=None, aet=None,
                 handler=None):
        self.stage = stage
        self.project_id = project_id
        self.run_id = run_id
        self.tracer = tracer
        self.auditor = auditor
        self.db = db
        self._aet = aet
        self._handler = handler          # 阶段编排的确定性/LLM Tool（handler 重定位）
        self._rework_feedback: Optional[dict] = None
        self.last_result: Optional[WorkAgentResult] = None

    # ── rework 反馈（ValidationAgent 打回时由 review 包装器写入） ─────────────
    def set_rework_feedback(self, feedback: Optional[dict]) -> None:
        self._rework_feedback = feedback

    # ── 依赖解析 ─────────────────────────────────────────────────────────
    def _aet_service(self):
        if self._aet is not None:
            return self._aet
        from app.services.aet_service import AETService
        return AETService(None)

    def _get_handler(self):
        """批 B：非 P1 阶段的确定性/LLM Tool（handler 重定位为 WorkAgent 可调用 Tool，
        r3 约束 2）。优先用注入的 handler，否则从图 registry 取（懒导入避免循环）。"""
        if self._handler is not None:
            return self._handler
        try:
            from app.graph.nodes import get_handler
            return get_handler(self.stage)
        except Exception:
            logger.debug("work_agent get_handler(%s) 失败（advisory）", self.stage, exc_info=True)
            return None

    def _is_llm_stage(self) -> bool:
        return self.stage in _LLM_STAGES

    def _task_type(self) -> str:
        return _STAGE_TASK_TYPE.get(self.stage, "default")

    def _artifacts_dir(self) -> Path:
        # D-107: per-stage subdirectory, not the flat artifacts/ root.
        from app.services.stage_package import stage_artifact_dir
        return stage_artifact_dir(self.project_id, self.stage)

    def _ws_root(self) -> Path:
        return workspace_service.workspace_path(self.project_id)

    def _agent_id(self) -> Optional[str]:
        """WorkAgent 复用 seed node_worker 身份（可追溯）。best-effort。"""
        try:
            from app.core.database import get_session
            from app.models.agent_definition import AgentDefinition, AgentType
            db = self.db or get_session()
            close = self.db is None
            try:
                a = (db.query(AgentDefinition)
                     .filter(AgentDefinition.agent_type == AgentType.node_worker)
                     .first())
                return a.agent_id if a else None
            finally:
                if close:
                    db.close()
        except Exception:
            logger.debug("work_agent node_worker id lookup failed (advisory)", exc_info=True)
            return None

    # ── 上下文装配 + Stage Skill 正文加载 ────────────────────────────────
    def _assemble(self, state: dict) -> dict:
        try:
            from app.services.context_assembler import assemble_context
            from app.services.project_service import ProjectService
            node_task = f"{self.stage.upper()} {('建档：全量识别项目结构/技术栈/依赖/配置' if self.stage=='p1' else '阶段主任务')}"
            return assemble_context(
                self.project_id, self.stage,
                project=ProjectService.build_project_context_dict(self.project_id),
                node_state={"node_task": node_task},
                task_type=self._task_type(),
                include_body=True, skill_disclosure="full",
            )
        except Exception:
            # 公理3：上下文装配失败发声，但不静默吞掉主任务（记录后以空上下文继续）。
            logger.warning("WorkAgent[%s] 上下文装配失败（advisory）", self.stage, exc_info=True)
            return {}

    @staticmethod
    def _skill_loaded_info(ctx: dict) -> dict:
        """从上下文提取 Stage Skill 正文加载信息（body_loaded / chars）。"""
        skills = ctx.get("skills") or []
        loaded = [s for s in skills if s.get("body")]
        if not loaded:
            return {"skill_id": None, "body_loaded": False, "chars": 0,
                    "skill_count": len(skills)}
        top = loaded[0]
        return {
            "skill_id": top.get("skill_id"),
            "name": top.get("name"),
            "body_loaded": True,
            "chars": len(top.get("body") or ""),
            "skill_count": len(skills),
            "bodies_loaded": len(loaded),
        }

    # ── 轻量确定性事实扫描（用于动态工作计划，不重跑全量 profiler） ──────────
    def _scan_project_facts(self, state: dict) -> dict:
        """基于真实 workspace 的轻量确定性【采集】：source_type / file_count / 原始扩展名
        计数 / 构建文件候选。用于合成动态工作计划的叙述（Q-WP2-4：随项目变化，非静态模板）。

        R17.5 WP-3：仅采集——`detected_stack` 是按【原始文件计数】排序的候选栈（非权威识别），
        权威技术栈/主语言【识别】由 P0 LLM（Node Worker Agent）对事实包推理产出。此处不做框架
        加权主语言判定（旧规则已删）。数据/文档扩展不计入语言（采集口径）。
        """
        src = self._ws_root() / "source"
        source_type = state.get("source_type") or "manual"
        ext_counts: Counter = Counter()      # 语言扩展原始计数（采集）
        build_files: list[str] = []
        file_count = 0
        if src.exists():
            for p in src.rglob("*"):
                parts = p.relative_to(src).parts
                if any(s in SKIP_DIRS for s in parts):
                    continue
                if not p.is_file():
                    continue
                file_count += 1
                ext = p.suffix.lower()
                # 语言计数排除数据/文档扩展（采集口径，避免 .json/.md 被当成语言）。
                if ext not in _NON_LANG_EXTS:
                    ext_counts[ext] += 1
                fname = p.name
                if fname in BUILD_FILES:
                    build_files.append(fname)
                elif ext in (".csproj", ".sln", ".fsproj", ".vbproj"):
                    build_files.append(fname)
        # 语言计数（扩展 → 语言）。
        lang_counts: dict[str, int] = {}
        for ext, cnt in ext_counts.items():
            lang = LANG_EXTENSIONS.get(ext)
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + cnt
        # 按原始计数排名的【候选】栈（非权威识别——权威识别交 P0 LLM）。
        detected_stack: list[str] = [lang for lang, _c in
                                     sorted(lang_counts.items(), key=lambda kv: -kv[1])][:6]
        return {
            "source_type": source_type,
            "file_count": file_count,
            "detected_stack": detected_stack,
            "build_files": sorted(set(build_files))[:20],
        }

    # ── D-08：候选栈文案渲染口径（单一入口，禁取首位当权威） ────────────────
    # `detected_stack` 是按【原始文件计数】排序的候选栈（见 _scan_project_facts，非权威识别）。
    # 真实规模真跑实测：256 个第三方前端 .js（layuiadmin/fullcalendar/layfly）压过 84 个 .cs，
    # 取首位会把 ASP.NET WebForms 系统的工作计划写成「候选栈为 JavaScript」，误导读计划的人。
    # 故文案一律列前 N 类 + 显式标注口径，不渲染为单一权威主栈；权威技术栈/主语言由 P0/P1
    # LLM 识别与 P2 技术选型（tech_selection）承载。采集与排序逻辑不因文案而改动。
    _STACK_TEXT_TOP_N = 3

    @classmethod
    def _candidate_stack_text(cls, facts: dict) -> str:
        """把候选栈渲染为不误导的文案（D-08）。无候选栈 → 空串（调用方决定兜底措辞）。"""
        stack = [s for s in (facts.get("detected_stack") or []) if s]
        if not stack:
            return ""
        shown = stack[:cls._STACK_TEXT_TOP_N]
        more = f" 等 {len(stack)} 类" if len(stack) > len(shown) else ""
        return f"按文件计数的候选栈（非权威识别）：{'/'.join(shown)}{more}"

    # ── 动态工作计划（确定性合成，Q-WP2-4） ─────────────────────────────
    def _plan_body_p1(self, facts: dict) -> tuple[str, list, list, list]:
        """P1 建档动态计划体（R17.5：采集→LLM 识别→原始验收基准捕获）。"""
        stack_txt = self._candidate_stack_text(facts)
        planned_actions = [
            {"action": "采集项目事实包（结构/依赖/配置/测试原文，大小写不敏感、脱敏）",
             "tool": "full_stack_profiler.collect_facts",
             "rationale": (f"采集到 {facts['file_count']} 个源文件"
                           + (f"，{stack_txt}" if stack_txt else "，源码待物化/为空"))},
            {"action": "复用 P0 接入识别（primary_language/环境/DB）+ LLM 深化建档识别",
             "tool": "p1_handler.execute→profiling_service(LLM)",
             "rationale": "识别归 LLM（AGENTS §2.3），复用 P0 结论不重算（解 P1-ARCH-1）"},
        ]
        if facts["build_files"]:
            planned_actions.append(
                {"action": "LLM 解析构建系统与依赖清单",
                 "tool": "p1_handler.execute→profiling_service(LLM)",
                 "rationale": f"采集到构建文件候选 {', '.join(facts['build_files'])}"})
        planned_actions.append(
            {"action": "捕获原始验收基准 acceptance_baseline.json（静态 LLM + 动态黄金真跑，D-106）",
             "tool": "acceptance_baseline_service",
             "rationale": "为下游 P5 行为等价/回归对比提供原始基准（不可跑栈诚实 needs_env）"})
        goal = (f"P1 建档：对该项目（{facts['file_count']} 源文件"
                + (f"；{stack_txt}" if stack_txt else "；源码待物化或为空")
                + "）复用 P0 识别深化建档 + 捕获原始验收基准")
        criteria = [
            "复用 P0 上游识别（不重算/推翻）",
            "产出建档识别产物（LLM）",
            "原始验收基准 acceptance_baseline.json 已产出",
            "盲区主动发声（uncertainty 非 0-gap）",
        ]
        risks = ["LLM 主任务：无有效模型 Key 时本阶段将诚实 blocked（不伪造，D-097）"]
        if facts["file_count"] == 0:
            risks.append("源码目录为空或未物化，识别将受限")
        return goal, planned_actions, criteria, risks

    def _plan_body_generic(self, facts: dict) -> tuple[str, list, list, list]:
        """P0/P2/P3/P4/P5/P6 动态计划体：以 handler 静态 goal/planned_actions 为骨架，
        用真实项目事实（file_count/探测栈/上游产物）确定性充实为随项目变化的动态计划
        （非 handler 静态类属性直挂，AGT-03）。上游产物 = 真实落盘的下游依赖输入。"""
        handler = self._get_handler()
        base_goal = getattr(handler, "goal", f"{self.stage.upper()} 阶段主任务") if handler else \
            f"{self.stage.upper()} 阶段主任务"
        stack_txt = self._candidate_stack_text(facts) or "结构待识别"
        goal = f"{base_goal}（当前项目：{facts['file_count']} 源文件，{stack_txt}）"
        upstream_present = [r for r in _STAGE_UPSTREAM_ARTIFACTS.get(self.stage, [])
                            if self._exists_rel(r)]
        planned_actions: list[dict] = []
        for a in (getattr(handler, "planned_actions", []) if handler else []):
            planned_actions.append({
                "action": a,
                "tool": f"{self.stage}_handler.execute",
                "rationale": (f"基于上游 {', '.join(upstream_present)}" if upstream_present
                              else f"基于 {facts['file_count']} 源文件项目上下文"),
            })
        if not planned_actions:
            planned_actions = [{"action": f"{self.stage} 阶段主任务",
                                "tool": f"{self.stage}_handler.execute",
                                "rationale": "阶段主任务编排"}]
        criteria = list(getattr(handler, "acceptance_criteria", []) if handler else [])
        risks: list[str] = []
        if self.stage in _LLM_STAGES:
            risks.append("LLM 主任务：无有效模型 Key 时本阶段将诚实 blocked（不伪造，D-097/WP-6）")
        if self.stage in _STAGE_UPSTREAM_ARTIFACTS and not upstream_present:
            risks.append(f"未检测到上游产物 {_STAGE_UPSTREAM_ARTIFACTS[self.stage]}，可能因前置阶段未完成而 blocked")
        return goal, planned_actions, criteria, risks

    def _exists_rel(self, rel: str) -> bool:
        return (self._ws_root() / rel.split("#")[0]).exists()

    # ── D-09：P2 结构化清单与评估报告对账（派生 or 诚实标注，禁 items: []） ───
    def _reconcile_p2_lists(self, tool_result: dict) -> dict:
        """让 P2 四类清单产物与评估报告一致（D-09，批次 C）。

        ① 清单非空 → 不动（handler 已落盘真实项）；
        ② 清单为空但评估报告里有【同名键】的清单 → 结构化搬运并重写产物（标 derivation_note）；
        ③ 仍为空 → 把产物重写为诚实标注（identification_note，**不留 `items: []`**）。空数组会被
           下游按清单消费的环节与用户读成"已检查且无风险"，属虚假事实断言（公理3 / D-097）；
           与本仓既有做法一致（stage_handlers 对 LLM 未产出的键写 identification_note，
           context_assembler 对未跟踪项标 None 而非 False）。

        只对账【已真实落盘】的产物：handler 未完成时不落盘，既无需对账，也不新造产物。
        返回需合并进 tool_result 的派生清单（不原地改入参，保持不可变）。
        """
        report = tool_result.get("assessment_report")
        report = report if isinstance(report, dict) else {}
        parse_error = bool(report.get("parse_error"))
        derived_out: dict = {}
        for key, (fname, art_type, cn) in _P2_LIST_ARTIFACTS.items():
            rel = f"artifacts/p2/{fname}"
            if not self._exists_rel(rel):
                continue
            if tool_result.get(key):
                continue
            lifted = _lift_lists_by_key(report, _P2_DERIVE_KEY_HINTS[key])
            derived_items: list = []
            for src_path, raw in lifted:
                for it in raw:
                    entry = dict(it) if isinstance(it, dict) else {"title": str(it)[:200]}
                    entry["derived_from"] = (
                        f"artifacts/p2/p2_assessment_report.json#report.{src_path}")
                    derived_items.append(entry)
            if derived_items:
                payload = {
                    "artifact_type": art_type,
                    "items": derived_items,
                    "derivation_note": (
                        f"{cn}清单由评估报告结构化字段派生（LLM 未在顶层锚点键 {key} 产出）；"
                        f"派生只按键名搬运，未新增任何风险判断"),
                    "derived_from_fields": [p for p, _ in lifted],
                }
                derived_out[key] = derived_items
            else:
                payload = {
                    "artifact_type": art_type,
                    # 有意不写 items 键：无 items 即表示【未取得清单】，而非"清单为空"。
                    "items_status": "not_produced",
                    "identification_note": (
                        f"LLM 未产出结构化 {key}（诚实标注，未伪造）；本产物无 items 字段即表示"
                        f"【未取得{cn}清单】，不得读作「已检查且无{cn}」"),
                    "derivation_attempted": {
                        "source_ref": "artifacts/p2/p2_assessment_report.json",
                        "matched_fields": [],
                        "assessment_report_parse_error": parse_error,
                    },
                }
            self._write_p2_list_artifact(rel, payload)
        return derived_out

    def _write_p2_list_artifact(self, rel: str, payload: dict) -> None:
        """重写 P2 清单产物（经单一写门 WorkspaceMediator，D-099⑥ + Audit）。

        复用 stage_package 的 mediated 写实现（不另造第二个写门；stage_handlers 亦为同语义）。
        写盘失败发声（公理3：不静默 except），但不影响阶段主返回。
        """
        try:
            from app.services.stage_package import _mediated_write
            body = {"project_id": self.project_id, "stage": "p2", "generated_at": _now(),
                    "reconciled_by": "work_agent", **payload}
            _mediated_write(self.project_id, rel,
                            json.dumps(body, ensure_ascii=False, indent=2),
                            auditor=self.auditor, stage="p2",
                            action="reconcile_p2_list_artifact")
        except Exception:
            logger.warning("WorkAgent[p2] 清单一致性重写失败 rel=%s（advisory，不静默）",
                           rel, exc_info=True)

    # ── D-10：独立验收结论单一来源（Gate Brief 不再硬传 None） ────────────────
    def _validation_verdict(self) -> dict:
        """取本阶段独立验收（ValidationAgent）真实落盘结论，供 Gate Brief 单一取值（D-10）。

        实测缺陷：WorkAgent 硬传 `validation_verdict=None`，导致面向用户的 `p5_gate_brief.json`
        为 null，而同阶段 `p5_validation.json` 的 verdict 是 `rework_required`——同一结论两处
        不一致，且空的那个恰是用户看的。

        取值口径（诚实）：WorkAgent 侧 Gate Brief 先于本轮 ValidationAgent 执行，故这里取到的是
        【最近一次落盘】的独立验收结论（rework 轮即上一轮结论）；本轮若通过，
        nodes._finalize_agent_gate_material 会以本轮 verdict 覆写同一份 gate_brief。读不到 →
        诚实标注"尚未取得"，不写 null。
        """
        rel = f"artifacts/{self.stage}/{self.stage}_validation.json"
        p = self._ws_root() / rel
        if p.exists():
            try:
                v = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(v, dict):
                    return {
                        "verdict": v.get("verdict"),
                        "passed": v.get("passed"),
                        "issues_count": len(v.get("issues") or []),
                        "agent_id": v.get("agent_id"),
                        "source_ref": rel,
                        "validated_at": v.get("validated_at"),
                        "verdict_scope": "last_persisted_validation",
                        "verdict_note": (
                            "取自落盘独立验收结论（单一来源，D-10）；WorkAgent 侧 Gate Brief 先于"
                            "本轮独立验收执行，本轮通过时由 nodes 侧以本轮 verdict 覆写"),
                    }
                logger.warning("WorkAgent[%s] 独立验收结论格式非对象 rel=%s（按未取得标注）",
                               self.stage, rel)
            except Exception:
                logger.warning("WorkAgent[%s] 读取独立验收结论失败 rel=%s（advisory，按未取得标注）",
                               self.stage, rel, exc_info=True)
        return {
            "verdict": "尚未取得",
            "passed": None,
            "issues_count": "未统计",
            "source_ref": None,
            "verdict_scope": "not_yet_validated",
            "verdict_note": ("独立验收尚未产出落盘结论（WorkAgent 侧 Gate Brief 先于 ValidationAgent"
                             "执行）；诚实标注，未假称通过或无问题"),
        }

    def _write_model_error_artifact(self, tool_result: dict) -> None:
        """WP-6：把模型全失败强制中断的结构化信息落盘为 {stage}_model_error.json。
        内容为执行派生事实（已脱敏错误分类/消息），供 summary 端点透传前端显式报错。
        best-effort：写盘失败不影响主返回（诚实 blocked 仍由 status 承载）。"""
        try:
            art_dir = self._artifacts_dir()
            art_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "artifact_type": "model_error",
                "interrupted_stage": self.stage,
                "failure_reason": tool_result.get("reason", ""),
                "error_category": tool_result.get("model_error_category", "model_unavailable"),
                "attempted_chain": tool_result.get("attempted_chain", []),
                "user_actions": tool_result.get("model_user_actions", []),
                "at": _now(),
            }
            (art_dir / f"{self.stage}_model_error.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.auditor:
                try:
                    self.auditor.write(
                        audit_type="model_unavailable", action="stage_model_interrupt",
                        decision="blocked", risk_level="L2",
                        project_id=self.project_id, run_id=self.run_id or None, stage=self.stage,
                        reason=(f"{self.stage} 模型全失败强制中断（{payload['error_category']}）：无静默降级，"
                                f"已尝试 {len(payload['attempted_chain'])} 个模型"))
                except Exception:
                    logger.debug("work_agent model_error audit 写入失败（advisory）", exc_info=True)
        except Exception:
            logger.warning("WorkAgent[%s] 写 model_error 产物失败（advisory）", self.stage, exc_info=True)

    def build_work_plan(self, state: dict) -> str:
        """plan_only 阶段：装配上下文（加载 Skill 正文）+ 基于真实事实确定性合成
        动态工作计划，落盘 {stage}_work_plan.json，返回相对 ref。"""
        ctx = self._assemble(state)
        skill_loaded = self._skill_loaded_info(ctx)
        facts = self._scan_project_facts(state)

        if self.stage == "p1":
            goal, planned_actions, criteria, risks = self._plan_body_p1(facts)
        else:
            goal, planned_actions, criteria, risks = self._plan_body_generic(facts)

        skill_ref = {"skill_id": skill_loaded.get("skill_id"),
                     "name": skill_loaded.get("name")} if skill_loaded.get("body_loaded") else None

        from app.graph.stage_reports import StageReports
        reports = StageReports(self.project_id, self.stage)
        ref = reports.work_plan(
            generated_by="work_agent",
            based_on=facts,
            goal=goal,
            planned_actions=planned_actions,
            acceptance_criteria=criteria,
            skill_ref=skill_ref,
            risks_foreseen=risks,
        )
        if self.tracer:
            try:
                self.tracer.write("work_agent", action="work_plan",
                                  summary=f"{self.stage} WorkAgent 动态工作计划已合成（{facts['file_count']} 源文件）",
                                  project_id=self.project_id, run_id=self.run_id or None,
                                  stage=self.stage)
            except Exception:
                logger.debug("work_agent trace(work_plan) 写入失败（advisory）", exc_info=True)
        return ref

    # ── 主执行（execute_fn） ─────────────────────────────────────────────
    async def execute(self, state: dict) -> dict:
        """StageLoop.execute_fn：阶段主任务闭环。R17.5：P0/P1 与 P2/P3/P4 一致，走
        handler-Tool 编排路径（handler 内部采集事实 + LLM 识别）；P5/P6 亦经此路径。
        返回 WorkAgentResult.to_dict()。"""
        return await self._execute_generic(state)

    # ── 批 B：P0/P1/P2/P3/P4/P5/P6 handler-Tool 编排路径 ─────────────────
    async def _execute_generic(self, state: dict) -> dict:
        """WorkAgent 编排：上下文装配（Skill 正文）→ 动态计划 → 调 handler（重定位为
        确定性/LLM Tool）→ 汇总产物 → evidence map（fact/claim）→ Gate Brief。

        诚实（D-097/公理3/WP-6）：handler Tool 返回 blocked/failed（LLM 阶段无 Key、
        前置缺失等）→ WorkAgent 如实返回 blocked/failed，不伪造 completed、不假 claim-evidence。
        """
        import inspect
        rework = self._rework_feedback
        self._rework_feedback = None

        ctx = self._assemble(state)
        skill_loaded = self._skill_loaded_info(ctx)
        assembly_trace = ctx.get("assembly_trace", {})
        tool_calls: list = []

        try:
            work_plan_ref = self.build_work_plan(state)
        except Exception:
            logger.warning("WorkAgent[%s] work_plan 合成失败（advisory）", self.stage, exc_info=True)
            work_plan_ref = None

        handler = self._get_handler()
        if handler is None:
            res = WorkAgentResult(stage=self.stage, status="failed",
                                  agent_id=self._agent_id(), work_plan_ref=work_plan_ref,
                                  skill_loaded=skill_loaded, tool_calls=tool_calls,
                                  reason=f"{self.stage} handler 未注册，WorkAgent 无可编排 Tool")
            self.last_result = res
            return res.to_dict()

        # ③ 调用 handler（重定位为 WorkAgent 可调用的确定性/LLM Tool）
        tool_name = f"{self.stage}_handler.execute"
        try:
            r = handler.execute(state)
            tool_result = await r if inspect.isawaitable(r) else r
        except Exception as e:  # 诚实：失败不伪造 completed
            tool_calls.append({"tool": tool_name, "status": "error", "error": type(e).__name__})
            res = WorkAgentResult(stage=self.stage, status="failed",
                                  agent_id=self._agent_id(), work_plan_ref=work_plan_ref,
                                  skill_loaded=skill_loaded, tool_calls=tool_calls,
                                  reason=f"{self.stage} handler 执行异常：{type(e).__name__}")
            self.last_result = res
            return res.to_dict()

        tool_result = tool_result if isinstance(tool_result, dict) else {}
        declared = tool_result.get("status", "completed")
        tool_calls.append({"tool": tool_name, "status": declared})

        # WP-6 (Q-R17.3-6-2): 模型全失败强制中断 → 落盘 {stage}_model_error.json（执行派生，
        # 非伪造），供 summary 端点/前端显式报错读取「已尝试模型链路 + 失败原因 + 用户操作」。
        if declared != "completed" and (tool_result.get("attempted_chain")
                                        or tool_result.get("model_error_category")):
            self._write_model_error_artifact(tool_result)

        # 领域产物 refs（NEW-05：反映真实写入；make_work_node 据此汇集 domain artifacts）
        refs = [str(a) for a in (tool_result.get("artifacts") or [])]

        # D-09：P2 结构化清单与评估报告对账（可派生则派生，派生不出写诚实标注；禁 items: []
        # 被读成"无风险"）。只在真正完成时对账——未完成不落盘清单产物。
        if self.stage == "p2" and declared == "completed":
            derived_lists = self._reconcile_p2_lists(tool_result)
            if derived_lists:
                tool_result = {**tool_result, **derived_lists}

        # ⑦ evidence map（LLM 阶段 claim / 确定性阶段 fact）
        llm = self._is_llm_stage()
        cem_ref, evidence_refs, entries, evidence_gap = self._build_generic_evidence_map(
            tool_result, llm, declared)

        # ⑥ Gate Brief（WorkAgent 侧）
        gate_brief_partial, gate_brief_ref = self._build_generic_gate_brief(
            tool_result, refs, entries, rework, declared, evidence_gap)

        status = "completed" if declared == "completed" else declared
        reason = tool_result.get("reason", "")
        if rework and status == "completed":
            reason = "rework 重跑：已按验收反馈重新编排 Tool 并产出产物" + (f"；{reason}" if reason else "")

        res = WorkAgentResult(
            stage=self.stage, status=status, agent_id=self._agent_id(),
            work_plan_ref=work_plan_ref, artifacts=refs, evidence_refs=evidence_refs,
            claim_evidence_map_ref=cem_ref, gate_brief_ref=gate_brief_ref,
            gate_brief_partial=gate_brief_partial, skill_loaded=skill_loaded,
            context_trace={
                "layers": assembly_trace.get("layers_assembled", []),
                "case_count": assembly_trace.get("case_count", 0),
                "knowledge_count": assembly_trace.get("knowledge_count", 0),
            },
            tool_calls=tool_calls,
            items_completed=len(entries),
            reason=reason,
        )
        result_dict = res.to_dict()
        # 透传 handler 领域字段（供 ValidationAgent 复用 handler.review 做域校验 + 前端展示）。
        # 这些是 Tool 的事实性输出（同时落盘为 domain artifacts），非 WorkAgent 进程内推理。
        for k, v in tool_result.items():
            if k not in result_dict:
                result_dict[k] = v
        result_dict["assembly_trace"] = assembly_trace
        if evidence_gap:
            result_dict["evidence_gap"] = evidence_gap
        self.last_result = res
        if self.tracer:
            try:
                self.tracer.write("work_agent", action="execute",
                                  summary=(f"{self.stage} WorkAgent 编排 {tool_name} → {status}，"
                                           f"{len(entries)} {'claim' if llm else 'fact'}→evidence"),
                                  project_id=self.project_id, run_id=self.run_id or None,
                                  stage=self.stage)
            except Exception:
                logger.debug("work_agent trace(execute-generic) 写入失败（advisory）", exc_info=True)
        return result_dict

    # ── sha256 校验（通用 evidence map / gate brief 用） ─────────────────
    def _sha256(self, rel_path: str) -> str:
        try:
            raw = (self._ws_root() / rel_path).read_bytes()
            return hashlib.sha256(raw).hexdigest()
        except Exception:
            return ""

    # ── 批 B：通用 evidence map（fact/claim）+ LLM 内联引用合成 ────────────
    def _build_generic_gate_brief(self, tool_result: dict, refs: list, entries: list,
                                  rework: Optional[dict], declared: str,
                                  evidence_gap: Optional[dict]) -> tuple[dict, str]:
        """通用 Gate Brief（WorkAgent 侧）：做了什么 / 关键产物 / 风险 / 诚实说明。"""
        llm = self._is_llm_stage()
        key_artifacts = []
        for r in refs[:12]:
            if isinstance(r, str):
                key_artifacts.append({"ref": r, "kind": self.stage,
                                      "sha256": self._sha256(r.split("#")[0])})
        risks = []
        # 从 handler Tool 输出提炼风险（P2 risk_list / P5 未通过槽位 / P6 脱敏问题）
        for r in (tool_result.get("risk_list") or [])[:8]:
            risks.append({"level": r.get("risk_level", "info"),
                          "desc": r.get("title") or str(r)[:80],
                          "source_ref": "artifacts/p2/p2_risk_list.json"})
        desens = tool_result.get("desensitization") or {}
        if desens and not desens.get("ok", True):
            risks.append({"level": "L2", "desc": f"脱敏扫描 {len(desens.get('issues', []))} 项待确认",
                          "source_ref": "artifacts/p6_delivery_report.json"})
        kind = "claim" if llm else "fact"
        if declared == "completed":
            what = (f"{self.stage.upper()} 阶段完成：WorkAgent 编排 {self.stage}_handler(Tool)，"
                    f"产出 {len(refs)} 项领域产物，登记 {len(entries)} 条 {kind}-evidence。")
        else:
            what = (f"{self.stage.upper()} 阶段未完成（status={declared}）：{tool_result.get('reason','')}"
                    f"（WorkAgent 诚实不伪造 completed）")
        if rework:
            what += "（本轮为验收打回后的重跑）"
        honest = ""
        if evidence_gap:
            honest = evidence_gap.get("detail", "")
        elif declared != "completed":
            honest = tool_result.get("reason", "")
        partial = {
            "stage": self.stage,
            "what_happened": what,
            "key_artifacts": key_artifacts,
            "risks": risks,
            "honest_notes": honest,
        }
        from app.graph.stage_reports import StageReports
        reports = StageReports(self.project_id, self.stage)
        # D-10：validation_verdict 从真实落盘的独立验收结论取值（不再硬传 None）。
        ref = reports.gate_brief(**partial, validation_verdict=self._validation_verdict(),
                                 claim_evidence_summary={"total": len(entries)})
        return partial, ref

    # ── 通用 evidence map（fact/claim）+ LLM 内联引用合成 ──────────────────
    def _stage_statements(self, tool_result: dict) -> list[dict]:
        """按阶段从 handler Tool 事实性输出提取 fact/claim 语句 + 绑定的落盘 artifact。
        每项：{key, statement, artifact_ref, detail, cited_refs}。cited_refs = 主任务 LLM
        输出内联携带的上游 evidence/artifact 引用（C1 真内联，非第二遍归因）。§3.6 通用，
        非硬编码 MicroOA。"""
        st = self.stage
        out: list[dict] = []
        if st == "p0":
            # R17.5 WP-1: P0 是 LLM 识别阶段——claim 为 LLM 产出的识别结论，内联引用其推理所据的
            # 采集产物（source_index.json 为主，intake_report.json 承载最终识别）。materialization/
            # source_type 为采集事实一并登记（引用 intake_report）。
            ident = tool_result.get("identification") or {}
            si_ref = "artifacts/p0/source_index.json"
            ir_ref = "artifacts/p0/intake_report.json"
            m = tool_result.get("materialized") or {}
            out.append({"key": "materialization",
                        "statement": f"源码物化状态={m.get('materialization_status')}，文件数={tool_result.get('file_count',0)}",
                        "artifact_ref": ir_ref,
                        "detail": {"materialization_status": m.get("materialization_status"),
                                   "file_count": tool_result.get("file_count", 0)},
                        "cited_refs": [si_ref]})
            prim = ident.get("primary_language")
            out.append({"key": "primary_language",
                        "statement": f"LLM 识别主语言/主栈：{prim or '未确定（诚实标注）'}",
                        "artifact_ref": ir_ref,
                        "detail": {"primary_language": prim,
                                   "detected_stack": ident.get("detected_stack")},
                        "cited_refs": [si_ref]})
            av = ident.get("availability_classification") or {}
            out.append({"key": "availability",
                        "statement": f"LLM 可用性研判：{av.get('class', '未判定')}（{av.get('label','')}）",
                        "artifact_ref": ir_ref,
                        "detail": {"availability_class": av.get("class")},
                        "cited_refs": [si_ref]})
            out.append({"key": "key_files",
                        "statement": f"LLM 判定关键文件 {len(ident.get('key_files') or [])} 项",
                        "artifact_ref": ir_ref,
                        "detail": {"key_files_count": len(ident.get("key_files") or [])},
                        "cited_refs": [si_ref]})
            out.append({"key": "p1_tasks",
                        "statement": f"LLM 生成 P1 建档任务 {len(ident.get('p1_intake_tasks') or [])} 项（防 P1 空转）",
                        "artifact_ref": ir_ref,
                        "detail": {"p1_task_count": len(ident.get("p1_intake_tasks") or [])},
                        "cited_refs": [si_ref]})
        elif st == "p1":
            # R17.5 P1 WP-1: P1 是 LLM 建档识别阶段——claim 为 LLM 深化建档结论，内联引用其推理所据的
            # 上游 P0 产物（intake_report.json 承载 P0 识别、source_index.json 承载采集）。样本值 LLM 生成。
            ident = tool_result.get("identification") or {}
            ir_ref = "artifacts/p0/intake_report.json"          # P0 上游识别（复用基线）
            si_ref = "artifacts/p0/source_index.json"           # P0 采集
            tech = ident.get("tech_stack") or {}
            deps = ident.get("dependency_draft") or {}
            dep_total = deps.get("total", len(deps.get("dependencies", []) or []))
            gaps = (ident.get("uncertainty_manifest") or {}).get("evidence_gaps", [])
            out.append({"key": "primary_language",
                        "statement": (f"P1 建档主语言：{tech.get('primary_language') or tool_result.get('primary_language') or '未确定'}"
                                      f"（复用 P0 识别：{tool_result.get('reused_p0_primary_language')}）"),
                        "artifact_ref": "artifacts/p1/tech_stack.json",
                        "detail": {"primary_language": tech.get("primary_language"),
                                   "reused_p0": tool_result.get("reused_p0_primary_language")},
                        "cited_refs": [ir_ref, si_ref]})
            out.append({"key": "dependencies",
                        "statement": f"LLM 建档依赖项 {dep_total} 项",
                        "artifact_ref": "artifacts/p1/dependency_draft.json",
                        "detail": {"dependency_total": dep_total},
                        "cited_refs": [si_ref]})
            out.append({"key": "entry_points",
                        "statement": f"LLM 判定应用入口 {len(ident.get('entry_points') or [])} 项",
                        "artifact_ref": "artifacts/p1/entry_points.json",
                        "detail": {"entry_point_count": len(ident.get("entry_points") or [])},
                        "cited_refs": [ir_ref]})
            out.append({"key": "acceptance_baseline",
                        "statement": (f"原始验收基准已捕获（status={tool_result.get('acceptance_baseline_status')}；"
                                      f"D-106 静态基线+动态黄金/needs_env）"),
                        "artifact_ref": "artifacts/p1/acceptance_baseline.json",
                        "detail": {"baseline_status": tool_result.get("acceptance_baseline_status")},
                        "cited_refs": [si_ref]})
            out.append({"key": "uncertainty",
                        "statement": f"LLM 主动发声识别盲区 {len(gaps)} 项（防 0-gap 掩盖，公理3）",
                        "artifact_ref": "artifacts/p1/uncertainty_manifest.json",
                        "detail": {"gap_count": len(gaps)},
                        "cited_refs": [ir_ref]})
        elif st == "p2":
            for i, r in enumerate((tool_result.get("risk_list") or [])[:8]):
                out.append({"key": f"risk-{i}",
                            "statement": f"迁移/重构风险：{r.get('title') or r.get('desc') or str(r)[:80]}",
                            "artifact_ref": f"artifacts/p2/p2_risk_list.json#item[{i}]",
                            "detail": {"risk_level": r.get("risk_level"), "source": r.get("source")},
                            "cited_refs": r.get("evidence_refs") or []})
            for i, b in enumerate((tool_result.get("blocker_list") or [])[:4]):
                out.append({"key": f"blocker-{i}",
                            "statement": f"阻塞项：{b.get('title') or str(b)[:80]}",
                            "artifact_ref": f"artifacts/p2/p2_blocker_list.json#item[{i}]", "detail": {},
                            "cited_refs": (b.get("evidence_refs") or []) if isinstance(b, dict) else []})
            for i, g in enumerate((tool_result.get("validation_gap_list") or [])[:4]):
                out.append({"key": f"vgap-{i}",
                            "statement": f"验证缺口：{g.get('title') or str(g)[:80]}",
                            "artifact_ref": f"artifacts/p2/p2_validation_gaps.json#item[{i}]", "detail": {},
                            "cited_refs": (g.get("evidence_refs") or []) if isinstance(g, dict) else []})
            if not out:
                # D-09：四类结构化清单全空（含派生后仍为空）→ 不留 entries: []（会被读成"无结论/
                # 无风险"）。绑定真实落盘的评估报告，并如实说明清单未取得（公理3）。
                _rep = tool_result.get("assessment_report")
                out.append({"key": "assessment_report",
                            "statement": ("P2 评估报告已产出，但未取得任何结构化清单项（风险/阻塞/"
                                          "验证缺口/资源需求）——诚实标注，非「无风险」"),
                            "artifact_ref": "artifacts/p2/p2_assessment_report.json",
                            "detail": {"structured_lists_present": False,
                                       "assessment_report_parse_error": bool(
                                           _rep.get("parse_error") if isinstance(_rep, dict) else False)},
                            "cited_refs": []})
        elif st == "p3":
            # C1: P3 计划类结论内联引用其所依据的上游 P2 产物（stage plan basis_refs /
            # task batch task_basis_refs），由主任务 LLM 输出携带，非二遍归因。
            plan_basis = tool_result.get("basis_refs") or []
            task_basis = tool_result.get("task_basis_refs") or plan_basis
            out.append({"key": "stage_plan",
                        "statement": f"迁移 Stage Plan 已生成（ref={tool_result.get('stage_plan_ref')}）",
                        "artifact_ref": "artifacts/p3/p3_stage_plan.json",
                        "detail": {"stage_plan_ref": tool_result.get("stage_plan_ref")},
                        "cited_refs": plan_basis})
            out.append({"key": "task_graph",
                        "statement": (f"TaskGraph 已生成（ref={tool_result.get('task_graph_ref')}，"
                                      f"degraded={tool_result.get('degraded')}）"),
                        "artifact_ref": "artifacts/p3/p3_task_graph.json",
                        "detail": {"task_graph_ref": tool_result.get("task_graph_ref"),
                                   "batch_risk_level": tool_result.get("batch_risk_level")},
                        "cited_refs": plan_basis})
            out.append({"key": "task_plans",
                        "statement": f"Task Plan(Batch) 已生成（batch={tool_result.get('batch_id')}）",
                        "artifact_ref": "artifacts/p3/p3_task_plans.json",
                        "detail": {"batch_id": tool_result.get("batch_id")},
                        "cited_refs": task_basis})
        elif st == "p4":
            # C1: 每个 output_code 结论内联引用其派生自的源文件（真实迁移溯源，主输出携带）。
            code_source_map = tool_result.get("code_source_map") or {}
            seen: set = set()
            for ref in (tool_result.get("artifacts") or []):
                if isinstance(ref, str) and ref.startswith("output_code/") and ref not in seen:
                    seen.add(ref)
                    src = code_source_map.get(ref)
                    out.append({"key": f"code-{len(out)}",
                                "statement": f"生成/修改代码文件 {ref}（真实落盘，evidence_basis=real_file_on_disk）",
                                "artifact_ref": ref, "detail": {"kind": "output_code"},
                                "cited_refs": [src] if src else []})
            if not out:
                out.append({"key": "exec_summary",
                            "statement": (f"P4 执行 {tool_result.get('completed_node_count',0)}/"
                                          f"{tool_result.get('execution_node_count',0)} execution 节点完成"),
                            "artifact_ref": "artifacts/p4/p4_execution_summary.json", "detail": {},
                            "cited_refs": [tool_result.get("task_graph_ref")]
                            if tool_result.get("task_graph_ref") else []})
        elif st == "p5":
            for vr in (tool_result.get("verify_results") or []):
                out.append({"key": f"slot-{vr.get('slot_id')}",
                            "statement": f"P5 硬必需槽位 {vr.get('slot_id')} {'通过' if vr.get('passed') else '未通过'}",
                            "artifact_ref": "artifacts/p5_validation_report.json",
                            "detail": {"slot_id": vr.get("slot_id"), "passed": vr.get("passed"),
                                       "status": vr.get("status")}})
            for cr in (tool_result.get("conditional_results") or []):
                out.append({"key": f"cond-{cr.get('slot_id')}",
                            "statement": (f"P5 条件槽位 {cr.get('slot_id')} 命令 exit_code="
                                          f"{cr.get('exit_code')}（status={cr.get('status')}）"),
                            "artifact_ref": "artifacts/p5_validation_report.json",
                            "detail": {"slot_id": cr.get("slot_id"), "exit_code": cr.get("exit_code"),
                                       "status": cr.get("status")}})
        elif st == "p6":
            pkg = tool_result.get("p6_delivery_package") or {}
            hm = pkg.get("hash_manifest") or {}
            files = (hm.get("files") if isinstance(hm, dict) else None) or []
            for i, f in enumerate(files[:12]):
                out.append({"key": f"hash-{i}",
                            "statement": f"交付文件 {f.get('path') or f.get('name') or i} sha256 已登记",
                            "artifact_ref": "artifacts/p6_delivery_report.json",
                            "detail": {"sha256": f.get("sha256")}})
            if not out:
                out.append({"key": "delivery",
                            "statement": "交付包已生成（含 hash_manifest / risk_manifest / 脱敏扫描）",
                            "artifact_ref": "artifacts/p6_delivery_report.json",
                            "detail": {"desensitization_ok": (tool_result.get("desensitization") or {}).get("ok")}})
        return out

    def _build_generic_evidence_map(self, tool_result: dict, llm: bool,
                                    declared: str) -> tuple[Optional[str], list, list, Optional[dict]]:
        """通用 evidence map：LLM 阶段=claim-evidence（要求 LLM 内联引用上游），
        确定性阶段=fact-evidence。每条经 AET 登记为可查询 Evidence。返回 evidence_gap（诚实）。"""
        aet = self._aet_service()
        pid8 = self.project_id[:8]
        entries: list[dict] = []
        evidence_refs: list[str] = []
        evidence_gap: Optional[dict] = None
        upstream = _STAGE_UPSTREAM_ARTIFACTS.get(self.stage, [])
        upstream_present = [u for u in upstream if self._exists_rel(u)]

        if declared != "completed":
            # 诚实：未完成不伪造 claim/fact；LLM 阶段无 Key → evidence_gap 明示需有效 Key。
            if llm:
                evidence_gap = {"gap_id": f"{self.stage}_llm_no_output",
                                "detail": (f"{self.stage} LLM 主任务未完成（status={declared}）：需有效模型 Key "
                                           f"端到端验证 LLM 级 claim-evidence 内联引用（D-097 不伪造）"),
                                "reason": tool_result.get("reason", "")}
            from app.graph.stage_reports import StageReports
            reports = StageReports(self.project_id, self.stage)
            cem_ref = reports.claim_evidence_map(
                map_type="claim_evidence" if llm else "fact_evidence",
                entries=[{"id": f"{self.stage}-blocked", "kind": "claim" if llm else "fact",
                          "statement": f"{self.stage} 未完成（诚实占位，无伪造 claim/fact）",
                          "produced_by": "none", "bindings": {}, "inline_citation": False,
                          "verified_on_disk": False,
                          "evidence_gap": evidence_gap["detail"] if evidence_gap else None}])
            return cem_ref, [], [], evidence_gap

        statements = self._stage_statements(tool_result)
        any_inline = False
        for s in statements:
            art_ref = s["artifact_ref"]
            base = art_ref.split("#")[0]
            # 诚实：只为真实落盘的 artifact 绑定 fact/claim（不为不存在的产物伪造条目）。
            if not self._exists_rel(base):
                continue
            sha = self._sha256(base)
            bindings = {
                "artifact_refs": [art_ref],
                "sha256": sha,
                "evidence_refs": [],
                "trace_refs": ([f"run:{self.run_id}"] if self.run_id else []),
                "audit_refs": [],
                "upstream_refs": list(upstream_present),
                **s.get("detail", {}),
            }
            # C1 真内联：主任务 LLM 输出内联携带的上游引用（cited_refs）→ 校验被引 ref 真实
            # 存在（勿杜撰），存在者置 inline_citation=True + cited_upstream_refs；不存在者
            # 记入 invalid_cited_refs 供 ValidationAgent 记 issue（不静默补全、不二遍归因）。
            inline_ok = False
            if llm:
                cited_valid: list = []
                cited_invalid: list = []
                for r in (s.get("cited_refs") or []):
                    if not isinstance(r, str) or not r:
                        continue
                    if self._exists_rel(r.split("#")[0]):
                        if r not in cited_valid:
                            cited_valid.append(r)
                    elif r not in cited_invalid:
                        cited_invalid.append(r)
                if cited_valid:
                    bindings["cited_upstream_refs"] = cited_valid
                    inline_ok = True
                    any_inline = True
                if cited_invalid:
                    bindings["invalid_cited_refs"] = cited_invalid
            ev_id = f"ev-{self.stage}-{s['key']}-{pid8}"
            claim_or_fact = "claim" if llm else "fact"
            try:
                aet.write_evidence(
                    self.project_id, ev_id,
                    evidence_type=f"{self.stage}_{claim_or_fact}",
                    status="candidate", source=f"{self.stage}_work_agent",
                    claim=s["statement"], stage=self.stage, extra={"bindings": bindings})
                bindings["evidence_refs"] = [ev_id]
                evidence_refs.append(ev_id)
            except Exception:
                logger.warning("WorkAgent %s evidence 登记失败 key=%s（advisory）",
                               self.stage, s["key"], exc_info=True)
            entries.append({
                "id": f"{claim_or_fact}-{self.stage}-{s['key']}",
                "kind": claim_or_fact,
                "statement": s["statement"],
                "produced_by": "llm" if llm else "deterministic_tool",
                "bindings": bindings,
                # LLM 阶段：inline_citation 来自【主任务 LLM 输出自带的内联引用】（C1），
                # 非第二遍 LLM 归因；确定性阶段无 LLM 内联引用（False）。
                "inline_citation": inline_ok if llm else False,
                "verified_on_disk": bool(sha),
            })

        # C1：LLM 阶段主输出必须内联携带可解析的上游引用。若无任何 claim 带内联引用 →
        # 诚实标 evidence_gap（不再第二遍 LLM 归因冒充主输出内联，AGT-05 闭合）。
        if llm and entries and not any_inline:
            evidence_gap = {
                "gap_id": f"{self.stage}_no_inline_citation_in_output",
                "detail": ("主任务 LLM 输出未内联携带任何可解析的上游 evidence 引用"
                           "（AGT-05：不做第二遍归因，诚实标 evidence_gap；需有效 Key 端到端验证主输出内联引用）"),
            }

        from app.graph.stage_reports import StageReports
        reports = StageReports(self.project_id, self.stage)
        cem_ref = reports.claim_evidence_map(
            map_type="claim_evidence" if llm else "fact_evidence", entries=entries)
        return cem_ref, evidence_refs, entries, evidence_gap


def _run_coro(coro):
    """在同步上下文安全运行 async gateway 调用：无运行中 loop → asyncio.run；
    有运行中 loop（execute 已在 loop 内）→ 新线程跑独立 loop（避免 re-entrancy）。"""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()

