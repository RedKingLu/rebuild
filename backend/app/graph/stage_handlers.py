"""Real P0/P1/P2/P3 stage handlers — the actual business, run inside LangGraph nodes.

P0/P1 (R9-5-1) delegate to SourceMaterializer / FullStackProfiler. P2 (R10 T11)
delegates to AssessmentService (model-driven risk/feasibility). P3 (R10 T17)
delegates to PlanningService (Stage Plan → Task Plan(Batch) → TaskGraph 必生 →
§5.6 Artifacts + §5.7 Evidence). So each graph node carries real work — not a stub.
The three D-092 reports are produced by StageLoop; domain artifacts here.
P4 (R11-3-C5) runs a REAL platform-internal execution link: it loads the P3 TaskGraph
and drives execution nodes through TaskGraphEngine + NodeLoop (9-step, edge strategies
success/failure/retry/rework/gate). Each execution node's Node Worker is
P4ExecutionWorker.execute_node — reads source/ (read-only), writes output_code/ +
patches/ via WorkspaceMediator (D-099/D-104), derives Evidence from the real files;
NodeLoop runs the independent Acceptance. The engine's _finalize honestly classifies
failed/blocked/waiting_gate (never fakes completed). External-agent delegation (C6) and
StagePageP4 (C8) are out of scope. P5 (R12-3-C3) is a skeleton: reads P4 input via
P5InputService (C1) + creates the P5ValidationPlan (C2), in honest blocked state until
C5 runs real build/run/test/static commands. P6 stays a future stub.

DOC-2: P1 handler returns the REAL identified item list from the profiler result as the single
source of truth (replacing the hardcoded 14-item frontend/route list).

D-107 (2026-07-21): 每个 P 阶段的领域产物写入 artifacts/{stage}/（p0/p1/p2 本轮统一，P3-P6 遵循），
并产出 artifacts/{stage}/_stage_package.json 阶段完成包清单（products[{file,type,desc,key_for_next}]
+ 证据/风险摘要 + 下阶段建议）；下一阶段读前序各阶段清单后按需加载产物内容（清单驱动，非写死文件名
列表）。DOC-2 单一事实源约定随之更新为 artifacts/{stage}/*.json。见 app/services/stage_package.py。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from app.graph.state import GraphState
from app.services.review_pass import ReviewResult
from app.services import workspace_service
from app.services.stage_package import (
    stage_artifact_ref, stage_artifact_dir, write_stage_package, product_entry,
)

logger = logging.getLogger("rebuild.stage_handlers")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_file_count(project_id: str) -> int:
    src = workspace_service.workspace_path(project_id) / "source"
    if not src.exists():
        return 0
    skip = {".git", "node_modules", "__pycache__"}
    return sum(1 for p in src.rglob("*")
               if p.is_file() and not any(s in skip for s in p.parts))


def _mediated_write(project_id: str, rel_path: str, content: str, *,
                    auditor=None, stage: str, action: str) -> str:
    """Write a stage report / artifact through WorkspaceMediator (the single write
    gate, D-099⑥) + record an Audit (L2). source/ is unconditionally rejected and
    escapes raise ValueError. Returns rel_path. Every execution actor writes via the
    mediator — stage handlers no longer bypass it with raw write_text (B-P4-MEDIATOR-BYPASS).
    """
    from app.services.workspace_mediator import WorkspaceMediator
    mediator = WorkspaceMediator(str(workspace_service.workspace_path(project_id)))
    target, risk = mediator.check_write(rel_path)  # raises ValueError on source/ or escape
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if auditor:
        try:
            auditor.write("workspace_write", risk_level=risk, action=action,
                          decision="allowed", reason=f"wrote {rel_path}",
                          project_id=project_id, stage=stage, transition_mode="real")
        except Exception:
            logger.warning("stage report audit write failed (advisory)", exc_info=True)
    return rel_path


# ════════════════════════════════════════════════════════════════════════════
# R17.5 WP-2/WP-3: P0 collection helpers (采集层，只产事实不产识别结论)
# ════════════════════════════════════════════════════════════════════════════
# 采集层【只读取/测量/脱敏】原始事实，喂给 P0 LLM（Node Worker Agent）推理识别。
# 识别/解读/研判/规划/验收判定一律 LLM（AGENTS §2.3；R17.5 WP-1 IntakeService）。
# 已删除（R17.5 WP-3）的确定性识别主线（改 LLM）：
#   _TECH_KEYWORDS 关键词表(HC-01) / _extract_environment_clues 解读+Website GUID(HC-02/05)
#   / _build_availability_classification A/B/C 规则(GAP-P0-06) / _build_p1_intake_tasks 固定
#   10 类模板(HC-07) / _build_missing_information 规则研判(GAP-P0-08) / _build_database_entry
#   方言/入口解读(GAP-P0-09) / _build_intake_enrichment 确定性组装(GAP-P0-02)。
# 连接串等敏感值全程不回显（脱敏红线 D-032，intake_service.redact_config_text 二次防护）。


def _read_source_text(project_id: str, rel_path: str, max_bytes: int = 200_000) -> str:
    """Read a source file as text with a size guard, handling common BOMs.
    Returns '' on any failure (caller treats empty as 'no clue extracted')."""
    try:
        p = workspace_service.workspace_path(project_id) / "source" / rel_path
        raw = p.read_bytes()[:max_bytes]
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16", errors="replace")
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw.decode("utf-8-sig", errors="replace")
        return raw.decode("utf-8", errors="replace")
    except Exception:
        logger.debug("intake: 读取源文件失败 %s（advisory）", rel_path, exc_info=True)
        return ""


def _basename(rel: str) -> str:
    return rel.replace("\\", "/").split("/")[-1]


def _collect_key_file_contents(project_id: str, source_index: dict,
                               *, max_files: int = 18, max_bytes: int = 8000) -> list:
    """采集：读取 key_files 候选的【脱敏原文片段】喂给 LLM 识别（GAP-P0-05/HC-01/02）。

    采集层不解读、不匹配关键词表/GUID——只读取原文并脱敏（连接串/密钥值一律 [REDACTED]，
    D-032）。LLM 据原文推理 targetFramework/auth/框架/Website 等（泛化任意栈）。
    """
    from app.services.intake_service import redact_config_text
    key_files = [k for k in (source_index.get("key_files") or []) if isinstance(k, str)]
    # README 优先纳入（语义线索），其余按出现顺序；上限 max_files 防超长。
    readmes = [k for k in key_files if _basename(k).lower().startswith("readme")]
    ordered = readmes + [k for k in key_files if k not in readmes]
    out: list = []
    for rel in ordered[:max_files]:
        text = _read_source_text(project_id, rel, max_bytes=max_bytes)
        if not text:
            continue
        out.append({"path": rel, "content": redact_config_text(text)[:max_bytes]})
    return out


def _build_migration_intent(project_id: str, source_config: dict) -> dict:
    """Register migration intent (source=user_intent, unverified, pending P2).
    Text comes from source_config or project.description; when absent it is honestly
    recorded as null + not_provided_at_p0 (NEVER fabricated)."""
    text = None
    for k in ("migration_intent", "migration_goal", "goal", "intent"):
        v = source_config.get(k) if isinstance(source_config, dict) else None
        if isinstance(v, str) and v.strip():
            text = v.strip()
            break
    if not text:
        try:
            from app.core.database import get_session
            from app.models.project import Project
            db = get_session()
            try:
                proj = db.get(Project, project_id)
                if proj and proj.description and proj.description.strip():
                    text = proj.description.strip()
            finally:
                db.close()
        except Exception:
            logger.debug("intake: 读取 project.description 失败（advisory）", exc_info=True)
    if not text:
        return {"text": None, "source": "user_intent", "confidence": "unverified",
                "decision_status": "pending_p2_assessment",
                "note": "not_provided_at_p0"}
    return {"text": text[:2000], "source": "user_intent", "confidence": "unverified",
            "decision_status": "pending_p2_assessment"}


def _build_p0_migration_target(project_id: str) -> dict | None:
    """采集：读取 Project 级目标运行环境约束（用户引导点选，R17.5 WP-6）。
    用户输入采集（非识别）；缺失则 None（诚实，不编造）。"""
    try:
        from app.core.database import get_session
        from app.models.project import Project
        db = get_session()
        try:
            proj = db.get(Project, project_id)
            return getattr(proj, "migration_target", None) if proj else None
        finally:
            db.close()
    except Exception:
        logger.debug("P0 读取 project.migration_target 失败（advisory）", exc_info=True)
        return None


def _build_intake_facts(project_id: str, source_index: dict, materialized: dict,
                        *, src_type: str, source_config: dict) -> dict:
    """采集：组装 P0 【事实包】喂给 LLM 识别（R17.5 WP-2）。

    只含【采集到的事实】——不含任何识别结论（关键性判定/A-B-C/方言/入口/任务清单等一律
    由 LLM 产出，见 IntakeService）。连接串等敏感值不出（脱敏 D-032）。空源诚实标注。
    """
    source_config = source_config or {}
    si = source_index or {}
    file_count = si.get("file_count", 0)
    repository_metadata = si.get("repository_metadata") or si.get("git_info")
    # 迁移意图【原文登记】（采集读用户输入，不做归纳——归纳交 LLM）。
    raw_intent = _build_migration_intent(project_id, source_config)
    migration_target = _build_p0_migration_target(project_id)
    key_file_contents = (_collect_key_file_contents(project_id, si) if file_count > 0 else [])
    return {
        "source_type": src_type,
        "materialization_status": materialized.get("materialization_status"),
        "file_count": file_count,
        "repository_metadata": repository_metadata,
        "top_level_dirs": si.get("top_level_dirs") or [],
        "directory_count": si.get("directory_count", 0),
        # key_files 为采集候选（大小写不敏感、含 .NET/vendor），交 LLM 判哪些真正关键。
        "key_files_candidates": si.get("key_files") or [],
        "code_scale": si.get("code_scale") or {},
        # database_files：采集给路径/规模/编码/计数；方言/入口解读交 LLM。
        "database_files": si.get("database_files") or [],
        "key_file_contents_redacted": key_file_contents,
        "raw_migration_intent_text": raw_intent.get("text"),
        "migration_intent_present": raw_intent.get("text") is not None,
        "migration_target": migration_target,
        "collection_note": ("源码为空/未物化，事实包仅含基础采集字段（诚实）"
                            if file_count == 0 else
                            "事实包为确定性采集事实（大小写不敏感/依赖入口全候选/已脱敏），识别交 LLM"),
    }



class RealP0Handler:
    """P0 接入: materialize source → source_index → intake_report. Review: non-manual empty source."""

    goal = "P0 接入：导入源码、登记材料与初始风险，产出可信接入输入"
    acceptance_criteria = [
        "Workspace 已初始化", "来源已识别并尝试物化", "intake 产物已生成",
        "非手动项目源码非空（或明确阻断引导）",
    ]
    planned_actions = [
        "物化源码（从 Git/本地/ZIP/URL 导入）",
        "生成源码索引（目录结构·技术栈·文件清单）",
        "写入接入报告（intake_report.json）",
    ]

    def __init__(self, tracer=None, auditor=None, intake_service=None):
        self.tracer = tracer
        self.auditor = auditor
        self._intake_service = intake_service      # R17.5 WP-1: LLM 识别服务（可注入以测试）

    def _service(self):
        if self._intake_service is not None:
            return self._intake_service
        from app.services.intake_service import IntakeService
        return IntakeService(tracer=self.tracer, auditor=self.auditor)

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        src_type = state.get("source_type", "manual")
        source_config = state.get("source_config", {}) or {}
        run_id = state.get("run_id", "")

        # ── 采集①：物化源码（clone/zip/local，确定性采集，KEEP） ────────────
        # R17.6 D-107 fix: graph resume (plan_approved → full re-execute) must NOT
        # re-clone if source is already materialized with the same config.  The
        # materializer's _try_reuse_git_source handles this when fingerprint is
        # stable; this guard adds a belt-and-suspenders check that skips
        # materialize() entirely when source/ already has files AND the
        # materialization meta commit matches the real HEAD — preventing the
        # re-clone that temporarily empties workspace/source/.
        materialized = {"materialization_status": "skipped", "file_count": 0}
        source_index: dict = {}
        try:
            from app.services.source_materializer import SourceMaterializer, generate_source_index
            m = SourceMaterializer(trace_writer=self.tracer, audit_writer=self.auditor)
            if m._already_materialized(project_id, src_type, source_config):
                materialized = {"materialization_status": "completed",
                                "materialization_mode": "reuse_guard",
                                "file_count": _source_file_count(project_id)}
                logger.info("P0 execute: source already materialized, skipping re-clone project=%s",
                            project_id)
            else:
                materialized = m.materialize(project_id, src_type, source_config,
                                             run_id=run_id)
            if materialized.get("file_count", 0) > 0:
                source_index = generate_source_index(project_id)
        except Exception as e:  # honest: record, do not fake success (D-097/公理3)
            materialized = {"materialization_status": "error", "file_count": 0,
                            "errors": [str(e)]}

        file_count = _source_file_count(project_id)
        # D-107: P0 产物写入 artifacts/p0/（分层文件夹）。
        art_dir = stage_artifact_dir(project_id, "p0")
        art_dir.mkdir(parents=True, exist_ok=True)
        if not source_index:
            try:
                si_path = art_dir / "source_index.json"
                if si_path.exists():
                    source_index = json.loads(si_path.read_text(encoding="utf-8"))
            except Exception:
                logger.debug("P0 读取既有 source_index.json 失败（advisory）", exc_info=True)

        # ── 采集②：组装事实包（只产事实，不产识别结论） ─────────────────────
        intake_facts = _build_intake_facts(
            project_id, source_index, materialized,
            src_type=src_type, source_config=source_config)
        migration_target = intake_facts.get("migration_target")

        # ── 上下文装配（C0-C6 + P-codebase-onboarding SKILL.md 正文 + 目标环境进上下文）──
        context_package: dict = {}
        skill_body = ""
        system_prompt: str | None = None
        node_state = {"node_task": "P0 接入：采集事实包 → LLM 识别（技术栈/环境/可用性/入口/DB/P1 任务）",
                      "task": "P0 接入识别"}
        if migration_target:  # WP-6: 目标运行环境进入 P0 上下文（C5 动态层）
            node_state["upstream_output"] = {"migration_target": migration_target}
        try:
            from app.services.context_assembler import assemble_context, build_system_prompt
            context_package = assemble_context(
                project_id, "p0", node_state=node_state,
                task_type="onboarding", include_body=True, skill_disclosure="full")
            bodies = [s.get("body") for s in (context_package.get("skills") or []) if s.get("body")]
            skill_body = "\n\n".join(bodies)[:12000]
            system_prompt = build_system_prompt(
                project_id, "p0", node_state=node_state,
                task_type="onboarding", skill_disclosure="metadata")
        except Exception:
            logger.warning("P0 上下文装配失败（advisory，识别照常以事实包推理）", exc_info=True)

        # ── 识别（LLM，Node Worker Agent 经 ModelGateway；无 Key → 诚实 blocked，WP-5）──
        svc = self._service()
        intake_result = await svc.identify(
            project_id, facts=intake_facts, run_id=run_id, stage="p0",
            system_prompt=system_prompt, skill_body=skill_body,
            context_package=context_package)

        assembly_trace = context_package.get("assembly_trace", {})
        # 基础采集字段（无论识别是否完成都真实反映采集事实）
        intake = {
            "artifact_id": f"artifact-intake-{project_id[:8]}",
            "project_id": project_id, "stage": "p0", "artifact_type": "intake_report",
            "source_type": src_type, "file_count": file_count,
            "materialization_status": materialized.get("materialization_status"),
            "repository_metadata": intake_facts.get("repository_metadata"),
            "migration_target": migration_target,
            "generated_at": _now(),
            "produced_by": "llm_node_worker_agent",
            "deep_profiling": "NOT_DONE_IN_P0 (业务语义/字段级DB schema/迁移路线留 P1/P2)",
        }

        # WP-5：识别未完成（无 Key/模型失败）→ 诚实 blocked，不伪造 completed、不回退规则识别。
        if intake_result.status != "completed":
            intake["identification"] = {
                "status": intake_result.status, "reason": intake_result.reason,
                "note": "P0 识别需有效模型 Key，未降级为规则识别（D-097/公理3）"}
            _mediated_write(project_id, stage_artifact_ref("p0", "intake_report.json"),
                            json.dumps(intake, ensure_ascii=False, indent=2),
                            auditor=self.auditor, stage="p0", action="write_intake_report")
            produced = [stage_artifact_ref("p0", n) for n in ("intake_report.json", "source_index.json")
                        if (art_dir / n).exists()]
            return {
                "status": intake_result.status,
                "reason": intake_result.reason,
                "file_count": file_count, "source_type": src_type,
                "materialized": materialized, "artifacts": produced,
                "assembly_trace": assembly_trace,
                "attempted_chain": intake_result.attempted_chain,
                "model_error_category": intake_result.model_error_category,
                "model_user_actions": intake_result.model_user_actions,
            }

        # 识别完成：合并 LLM 产出的识别字段（样本值 LLM 生成，通用锚点固定）。
        identification = intake_result.identification or {}
        intake["identification"] = identification
        # 平铺常用识别字段到顶层（前端/下游沿用既有键名读取；单一事实源仍是 identification）。
        for k in ("key_files", "source_environment_clues", "database_entry",
                  "availability_classification", "migration_intent", "entry_points",
                  "p1_intake_tasks", "missing_information", "questions_for_user",
                  "uncertainty", "primary_language", "detected_stack"):
            if k in identification:
                intake[k] = identification[k]
        intake["model_used"] = intake_result.model_used
        _mediated_write(project_id, stage_artifact_ref("p0", "intake_report.json"),
                        json.dumps(intake, ensure_ascii=False, indent=2),
                        auditor=self.auditor, stage="p0", action="write_intake_report")

        produced_artifacts = [stage_artifact_ref("p0", n) for n in ("intake_report.json", "source_index.json")
                              if (art_dir / n).exists()]
        # D-107: 产出 P0 阶段完成包清单（供 P1/P2 按需加载，非写死文件名列表）。
        self._write_p0_package(project_id, produced_artifacts, identification)
        return {
            "status": "completed",
            "file_count": file_count,
            "source_type": src_type,
            "materialized": materialized,
            "identification": identification,
            "primary_language": identification.get("primary_language"),
            "availability_class": (identification.get("availability_classification") or {}).get("class"),
            "key_files_count": len(identification.get("key_files") or []),
            "model_used": intake_result.model_used,
            "artifacts": produced_artifacts,
            "assembly_trace": assembly_trace,
            "context_refs": intake_result.context_refs,
            "skill_refs": intake_result.skill_refs,
            "evidence_candidates": [
                {"evidence_id": f"ev-p0-ws-{project_id[:8]}",
                 "type": "workspace_initialized", "status": "candidate"},
                {"evidence_id": f"ev-p0-onb-{project_id[:8]}",
                 "type": "onboarding_completed", "status": "candidate"},
            ],
        }

    def _write_p0_package(self, project_id: str, produced: list, identification: dict) -> None:
        """D-107: 写 artifacts/p0/_stage_package.json（P0 完成包清单）。

        products 描述 + key_for_next（下阶段关键产物：intake_report/source_index 均为 P1 建档基线）
        + 识别摘要 + 进入 P1 建议。产物文件名从 produced（真实落盘）派生，诚实不虚报。
        """
        _types = {"intake_report.json": ("intake_report", "P0 LLM 接入识别（技术栈/环境/可用性/入口/DB/P1 任务）"),
                  "source_index.json": ("source_index", "源码索引（目录/关键文件候选/DB 文件/代码规模，采集）")}
        products = []
        for ref in produced:
            fn = ref.split("/")[-1]
            t, desc = _types.get(fn, (fn.rsplit(".", 1)[0], "P0 产物"))
            products.append(product_entry(fn, t, desc, key_for_next=True))
        av = (identification.get("availability_classification") or {}).get("class")
        try:
            write_stage_package(
                project_id, "p0", products=products,
                evidence_summary={"primary_language": identification.get("primary_language"),
                                  "availability_class": av,
                                  "key_files_count": len(identification.get("key_files") or [])},
                risks=[], next_stage_advice="进入 P1 建档：复用 P0 接入识别，深化技术栈/依赖/入口/配置/测试并捕获原始验收基准。",
                auditor=self.auditor, tracer=self.tracer)
        except Exception:
            logger.warning("P0 stage package 写入失败（advisory）", exc_info=True)

    def review(self, result: dict) -> ReviewResult:
        issues = []
        # 域规则（确定性）：非手动项目源码为空 → 应阻断引导补凭据。
        if result.get("source_type") not in ("manual",) and result.get("file_count", 0) == 0:
            issues.append({"type": "empty_source",
                           "detail": "非手动项目但源码目录为空（应阻断并引导补凭据）"})
        # 识别质量语义判定由独立 LLM Acceptance Agent 承担（WP-4，validation_agent p0=llm）；
        # 此处仅保留确定性域规则（存在性/空源），不做规则化识别质量判定。
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["补充源码凭据后重新导入"] if issues else [],
                            reviewer="p0_review_skill")


class RealP1Handler:
    """P1 建档 (R17.5): 采集事实包 (FullStackProfiler.collect_facts) + 复用 P0 上游识别
    (intake_report/source_index) → Node Worker Agent(LLM, ProfilingService) 深化建档识别
    → 原始验收基准捕获 (AcceptanceBaselineService, D-106)。采集确定性/识别 LLM (AGENTS §2.3)；
    无 Key → 诚实 blocked (承 P0，不回退规则识别)。"""

    goal = "P1 建档：复用 P0 识别、深化项目档案（技术栈/依赖/入口/配置/infra/测试）并捕获原始验收基准"
    acceptance_criteria = [
        "复用 P0 上游识别（不重算/推翻）", "产出建档识别产物（LLM）",
        "原始验收基准 acceptance_baseline.json 已产出", "盲区主动发声（uncertainty 非 0-gap）",
    ]
    planned_actions = [
        "采集项目事实包（结构/依赖/配置/测试原文，大小写不敏感、脱敏）",
        "复用 P0 接入识别（primary_language/环境/DB）作为建档基线",
        "LLM 深化建档识别 + 捕获原始验收基准（静态+动态黄金）",
    ]

    # LLM 建档识别字段 → 落盘产物 key 映射（通用锚点固定，样本值 LLM 生成）。
    _LLM_ARTIFACT_KEYS = ["tech_stack", "dependency_draft", "entry_points",
                          "config_inventory", "infra_clues", "test_inventory",
                          "module_structure", "uncertainty_manifest"]

    def __init__(self, tracer=None, auditor=None, profiling_service=None, baseline_service=None):
        self.tracer = tracer
        self.auditor = auditor
        self._profiling_service = profiling_service      # R17.5: 可注入以测试 LLM/blocked 路径
        self._baseline_service = baseline_service

    def _profiler_svc(self):
        if self._profiling_service is not None:
            return self._profiling_service
        from app.services.profiling_service import ProfilingService
        return ProfilingService(tracer=self.tracer, auditor=self.auditor)

    def _baseline_svc(self):
        if self._baseline_service is not None:
            return self._baseline_service
        from app.services.acceptance_baseline_service import AcceptanceBaselineService
        return AcceptanceBaselineService(tracer=self.tracer, auditor=self.auditor)

    def _read_upstream(self, project_id: str) -> dict:
        """WP-2: 读 P0 已 LLM 产出的接入识别结论 + source_index 摘要（复用基线，解 P1-ARCH-1）。
        D-107: P0 产物位于 artifacts/p0/。"""
        p0_dir = stage_artifact_dir(project_id, "p0")
        intake = {}
        source_index = {}
        try:
            p = p0_dir / "intake_report.json"
            if p.exists():
                intake = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("P1 读取 intake_report.json 失败（advisory）", exc_info=True)
        try:
            p = p0_dir / "source_index.json"
            if p.exists():
                si = json.loads(p.read_text(encoding="utf-8"))
                source_index = {"file_count": si.get("file_count"),
                                "top_level_dirs": si.get("top_level_dirs"),
                                "code_scale": si.get("code_scale"),
                                "database_files": si.get("database_files")}
        except Exception:
            logger.debug("P1 读取 source_index.json 失败（advisory）", exc_info=True)
        ident = intake.get("identification") or {}
        return {
            "p0_primary_language": intake.get("primary_language") or ident.get("primary_language"),
            "primary_language": intake.get("primary_language") or ident.get("primary_language"),
            "detected_stack": ident.get("detected_stack"),
            "source_environment_clues": ident.get("source_environment_clues"),
            "database_entry": ident.get("database_entry"),
            "availability_classification": ident.get("availability_classification"),
            "key_files": ident.get("key_files"),
            "p1_intake_tasks": ident.get("p1_intake_tasks"),
            "migration_target": intake.get("migration_target"),
            "source_index_summary": source_index,
            "p0_produced_by": intake.get("produced_by"),
        }

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        run_id = state.get("run_id", "")
        # D-107: P1 产物写入 artifacts/p1/（分层文件夹）。
        art_dir = stage_artifact_dir(project_id, "p1")
        art_dir.mkdir(parents=True, exist_ok=True)

        # ── 采集①：事实包 + 写盘纯采集产物（file_index/source_structure/cicd/doc） ──
        from app.services.full_stack_profiler import FullStackProfiler
        profiler = FullStackProfiler(trace_writer=self.tracer, audit_writer=self.auditor)
        facts = profiler.collect_facts(project_id)

        # ── 采集②：复用 P0 上游识别结论（WP-2，深化不重算） ──────────────────
        upstream = self._read_upstream(project_id)

        # ── 上下文装配（C0-C6 + P-profiling SKILL.md 正文；目标环境进上下文） ──
        context_package: dict = {}
        skill_body = ""
        system_prompt = None
        node_state = {"node_task": "P1 建档：复用 P0 识别 + 采集事实 → LLM 深化建档 + 捕获原始验收基准",
                      "task": "P1 建档识别"}
        if upstream.get("migration_target"):
            node_state["upstream_output"] = {"migration_target": upstream["migration_target"]}
        try:
            from app.services.context_assembler import assemble_context, build_system_prompt
            context_package = assemble_context(
                project_id, "p1", node_state=node_state, task_type="profiling",
                include_body=True, skill_disclosure="full")
            bodies = [s.get("body") for s in (context_package.get("skills") or []) if s.get("body")]
            skill_body = "\n\n".join(bodies)[:12000]
            system_prompt = build_system_prompt(
                project_id, "p1", node_state=node_state, task_type="profiling",
                skill_disclosure="metadata")
        except Exception:
            logger.warning("P1 上下文装配失败（advisory，识别照常以事实包推理）", exc_info=True)

        # ── 识别（LLM，Node Worker Agent 经 ModelGateway；无 Key → 诚实 blocked） ──
        svc = self._profiler_svc()
        prof_result = await svc.profile(
            project_id, facts=facts, upstream=upstream, run_id=run_id, stage="p1",
            system_prompt=system_prompt, skill_body=skill_body, context_package=context_package)
        assembly_trace = context_package.get("assembly_trace", {})

        if prof_result.status != "completed":
            # 诚实 blocked：不伪造 completed、不回退规则识别（D-097/公理3）。
            self._write_json(project_id, "profiling_summary.md",
                             self._blocked_summary(project_id, prof_result), raw_text=True)
            produced = [stage_artifact_ref("p1", p.name) for p in sorted(art_dir.glob("*.json"))]
            return {
                "status": prof_result.status, "reason": prof_result.reason,
                "file_count": facts.get("file_count", 0), "artifacts": produced,
                "assembly_trace": assembly_trace,
                "attempted_chain": prof_result.attempted_chain,
                "model_error_category": prof_result.model_error_category,
                "model_user_actions": prof_result.model_user_actions,
            }

        # ── 落盘 LLM 建档识别产物（通用锚点固定，样本值 LLM 生成） ────────────
        identification = prof_result.identification or {}
        for key in self._LLM_ARTIFACT_KEYS:
            data = identification.get(key)
            if data is None:
                data = {"identification_note": f"LLM 未产出 {key}（诚实标注，未伪造）"}
            self._write_json(project_id, f"{key}.json", data)

        # ── WP-B (D-106)：原始验收基准捕获（静态 LLM + 动态黄金真跑/needs_env） ──
        baseline_svc = self._baseline_svc()
        baseline_result = await baseline_svc.capture(
            project_id, facts=facts, upstream=upstream, run_id=run_id, stage="p1",
            source_path=str(workspace_service.workspace_path(project_id) / "source"))
        if baseline_result.status == "completed":
            self._write_json(project_id, "acceptance_baseline.json", baseline_result.baseline)
        else:
            # 静态基线需 LLM；若基线子步骤未完成，诚实登记（不伪造黄金）。
            self._write_json(project_id, "acceptance_baseline.json", {
                "artifact_type": "acceptance_baseline", "project_id": project_id, "stage": "p1",
                "status": baseline_result.status, "reason": baseline_result.reason,
                "note": "原始验收基准未完成（诚实标注，未伪造黄金 D-097/公理3）"})

        # ── 建档摘要 + P2 输入清单（复用 P0 + LLM 识别口径） ──────────────────
        self._write_json(project_id, "profiling_summary.md",
                         self._build_summary(project_id, identification, upstream, facts,
                                             baseline_result), raw_text=True)
        self._write_p2_manifest(project_id)

        refs: List[str] = []
        identified_items: List[str] = []
        for p in sorted(art_dir.glob("*.json")):
            if p.name.startswith("_"):   # D-107: 完成包清单等下划线文件不计入领域产物
                continue
            refs.append(stage_artifact_ref("p1", p.name))
            if p.name != "p2_input_manifest.json" and not p.name.startswith("p0_") \
                    and not p.name.startswith("p1_") and p.name != "intake_report.json" \
                    and p.name != "source_index.json":
                identified_items.append(p.stem)
        if (art_dir / "profiling_summary.md").exists():
            refs.append(stage_artifact_ref("p1", "profiling_summary.md"))

        # D-107: 产出 P1 阶段完成包清单（含 acceptance_baseline，供 P2 按需加载解 GAP-P2-1/2）。
        self._write_p1_package(project_id, refs, identification, baseline_result)

        tech = identification.get("tech_stack") or {}
        return {
            "status": "completed",
            "file_count": facts.get("file_count", 0),
            "identification": identification,
            "primary_language": tech.get("primary_language") or upstream.get("primary_language"),
            "reused_p0_primary_language": upstream.get("primary_language"),
            "acceptance_baseline_status": baseline_result.status,
            "model_used": prof_result.model_used,
            "artifacts": refs, "identified_items": identified_items,
            "assembly_trace": assembly_trace,
            "context_refs": prof_result.context_refs, "skill_refs": prof_result.skill_refs,
        }

    def _write_json(self, project_id: str, name: str, data, *, raw_text: bool = False) -> None:
        content = data if raw_text else json.dumps(data, ensure_ascii=False, indent=2)
        _mediated_write(project_id, stage_artifact_ref("p1", name), content,
                        auditor=self.auditor, stage="p1", action=f"write_{name.split('.')[0]}")

    def _write_p2_manifest(self, project_id: str) -> None:
        art_dir = stage_artifact_dir(project_id, "p1")
        manifest = {
            "project_id": project_id, "p1_completed_at": _now(), "p1_stage": "completed",
            "input_artifacts": [{"ref": stage_artifact_ref("p1", p.name), "type": p.stem}
                                for p in sorted(art_dir.glob("*.json")) if not p.name.startswith("_")],
            "environment_profile_ref": ".rebuild/environment.json",
        }
        self._write_json(project_id, "p2_input_manifest.json", manifest)

    # LLM 建档识别产物 → 完成包 products 描述（通用锚点固定）。key_for_next=True 者供 P2 按需加载。
    _P1_PRODUCT_META = {
        "tech_stack.json": ("tech_stack", "技术栈识别（主语言/框架/运行时）", True),
        "dependency_draft.json": ("dependency_draft", "依赖清单识别", True),
        "entry_points.json": ("entry_points", "应用入口识别", True),
        "config_inventory.json": ("config_inventory", "配置清单（值已脱敏）", True),
        "infra_clues.json": ("infra_clues", "基础设施线索", False),
        "test_inventory.json": ("test_inventory", "测试资产清单", False),
        "module_structure.json": ("module_structure", "模块结构识别", False),
        "uncertainty_manifest.json": ("uncertainty_manifest", "识别盲区/证据缺口（LLM 主动发声）", True),
        "acceptance_baseline.json": ("acceptance_baseline", "原始验收基准（D-106 静态基线+动态黄金/needs_env）", True),
        "profiling_summary.md": ("profiling_summary", "P1 建档摘要", True),
        "p2_input_manifest.json": ("p2_input_manifest", "P2 输入清单（产物 refs）", False),
        "file_index.json": ("file_index", "文件索引（采集）", False),
        "source_structure.json": ("source_structure", "源码结构（采集）", False),
        "cicd_inventory.json": ("cicd_inventory", "CI/CD 候选（采集）", False),
        "doc_inventory.json": ("doc_inventory", "文档候选（采集）", False),
    }

    def _write_p1_package(self, project_id: str, refs: list, identification: dict, baseline) -> None:
        """D-107: 写 artifacts/p1/_stage_package.json（P1 完成包清单）。

        key_for_next=True 的产物（含 acceptance_baseline / tech_stack / dependency / entry_points /
        config / uncertainty / profiling_summary）供 P2 _gather_inputs 按需加载内容——结构性
        根治 GAP-P2-1（P2 看不到 acceptance_baseline 而误报"P1 无验证证据"）。
        """
        products = []
        for ref in refs:
            fn = ref.split("/")[-1]
            t, desc, kfn = self._P1_PRODUCT_META.get(fn, (fn.rsplit(".", 1)[0], "P1 产物", False))
            products.append(product_entry(fn, t, desc, key_for_next=kfn))
        tech = identification.get("tech_stack") or {}
        gaps = (identification.get("uncertainty_manifest") or {}).get("evidence_gaps", [])
        golden = (getattr(baseline, "baseline", None) or {}).get("dynamic_golden", {}) if baseline else {}
        try:
            write_stage_package(
                project_id, "p1", products=products,
                evidence_summary={
                    "primary_language": tech.get("primary_language"),
                    "acceptance_baseline_status": getattr(baseline, "status", None),
                    "dynamic_golden_captured": bool(golden.get("captured")),
                    "evidence_gap_count": len(gaps)},
                risks=[], next_stage_advice=(
                    "进入 P2 评估：消费 acceptance_baseline（静态基线+动态黄金/needs_env）评估可验证性，"
                    "结合 tech_stack/dependency/entry_points/config 识别迁移风险/阻塞/验证缺口/资源需求。"),
                auditor=self.auditor, tracer=self.tracer)
        except Exception:
            logger.warning("P1 stage package 写入失败（advisory）", exc_info=True)

    @staticmethod
    def _blocked_summary(project_id: str, r) -> str:
        return "\n".join([f"# P1 建档摘要：{project_id}", "",
                          f"状态：{r.status}", f"原因：{r.reason}", "",
                          "> P1 建档识别需有效模型 Key，未降级为规则识别（D-097/公理3）。"])

    def _build_summary(self, pid: str, ident: dict, upstream: dict, facts: dict, baseline) -> str:
        tech = ident.get("tech_stack") or {}
        primary = tech.get("primary_language") or upstream.get("primary_language") or "未确定"
        deps = ident.get("dependency_draft") or {}
        dep_total = deps.get("total", len(deps.get("dependencies", []) or []))
        gaps = (ident.get("uncertainty_manifest") or {}).get("evidence_gaps", [])
        golden = (baseline.baseline or {}).get("dynamic_golden", {}) if baseline else {}
        return "\n".join([
            f"# P1 建档摘要：{pid}", f"建档时间：{_now()}", "",
            "## 技术栈（LLM 识别，复用 P0）",
            f"- 主语言：{primary}（P0 识别：{upstream.get('primary_language')}）",
            f"- 框架：{', '.join(f.get('framework','') for f in (tech.get('frameworks') or [])) or '见 tech_stack.json'}",
            f"- 依赖项：{dep_total}", "",
            "## 原始验收基准（D-106）",
            f"- 静态基线：已产出（测试断言/缺测/特征化规格，见 acceptance_baseline.json）",
            f"- 动态黄金：{'已真跑捕获' if golden.get('captured') else 'needs_env（诚实，未伪造）'}",
            "", "## 不确定性",
            f"- 识别盲区：{len(gaps)} 项（LLM 主动发声）", "",
            f"- 文件总数：{facts.get('file_count', 0)}", "",
            "> 深度业务语义/字段级 schema 归 P2。本档案在 P0 识别之上深化建档（复用不重算）。",
        ])

    def review(self, result: dict) -> ReviewResult:
        """确定性域规则（存在性）：建档识别产物 + 原始验收基准存在。识别质量语义由独立 LLM
        Acceptance Agent 判定（WP-4，validation_agent p1=llm）。r3 约束 3：从盘重读的 artifacts。"""
        issues = []
        if result.get("status") not in ("completed", None):
            issues.append({"type": "not_completed",
                           "detail": f"P1 未完成（status={result.get('status')}）"})
            return ReviewResult(passed=False, issues=issues,
                                recommendations=["配置有效模型 Key 后重跑 P1 建档识别"],
                                reviewer="p1_review_skill")
        disk_artifacts = [r for r in (result.get("artifacts") or []) if isinstance(r, str)]
        has_ident = any(any(k in r for k in ("tech_stack", "dependency_draft", "entry_points"))
                        for r in disk_artifacts)
        has_baseline = any("acceptance_baseline" in r for r in disk_artifacts)
        if not has_ident:
            issues.append({"type": "no_identification_artifact", "detail": "缺建档识别产物（LLM）"})
        if not has_baseline:
            issues.append({"type": "no_acceptance_baseline",
                           "detail": "缺 acceptance_baseline.json（D-106 原始验收基准）"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=(["重跑 P1 建档并确认识别产物 + 原始验收基准写入"]
                                             if issues else []),
                            reviewer="p1_review_skill")


_bootstrapped = False


class RealP2Handler:
    """P2 评估: AssessmentService (LLM) → 6 类评估产出 + §4.6 Artifact + §4.7 Evidence 落库.

    Runs inside the LangGraph P2 node via StageLoop (D-091). The assessment is
    genuinely model-driven (Q-R10-2): no model Key → status=blocked → review fails
    → the node escalates to a Gate honestly (never a fake completed). Model output
    is auxiliary analysis, not fact (analysis_only, §4.4-8/§4.7-5/STOP-4). P2 does
    NOT execute a TaskGraph — that is P3/P4 (no NodeLoop here).
    """

    goal = "P2 评估：识别迁移/重构风险、阻塞项、不确定项、验证缺口与资源需求，为 P3 规划提供可信依据"
    acceptance_criteria = [
        "风险清单已生成", "阻塞项与不确定项已登记", "验证缺口已登记",
        "资源需求建议已生成或明确无需求", "P2 Evidence 已落库（§4.7 五项，D-066）",
        "模型输出已标记为辅助分析而非事实（analysis_only）",
    ]
    planned_actions = [
        "调用模型执行六类风险/阻塞/缺口/资源评估",
        "写入 P2 评估产物（风险/阻塞/验证缺口/资源需求）",
        "落库 P2 Evidence（§4.7 五项，D-066）",
    ]

    def __init__(self, tracer=None, auditor=None, assessment_service=None):
        self.tracer = tracer
        self.auditor = auditor
        self._svc = assessment_service

    def _service(self):
        if self._svc is not None:
            return self._svc
        from app.services.assessment_service import AssessmentService
        return AssessmentService(tracer=self.tracer, auditor=self.auditor)

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        run_id = state.get("run_id", "")
        user_goal = state.get("user_goal") or state.get("run_goal") or ""

        # R10-5 P1-C: assemble C0-C6 context + build system prompt (C3 Skill metadata
        # only — progressive disclosure). The assessment is driven through the unified
        # context path, not an ad-hoc hardcoded prompt (S4). Advisory on failure.
        context_package: dict = {}
        system_prompt: str | None = None
        skill_body = ""
        try:
            from app.services.context_assembler import assemble_context, build_system_prompt
            node_state = {"node_task": "P2 评估：识别迁移/重构风险、阻塞项、验证缺口与资源需求",
                          "task": "评估可行性与风险"}
            # D-108: 加载 P2 评估 stage skill 正文（P-migration-assessment），评估需求随 skill 走，
            # 提示词瘦身、给 Agent 灵活度（skill-first，仿 P0/P1 include_body + skill_disclosure=full）。
            context_package = assemble_context(
                project_id, "p2", node_state=node_state,
                task_type="assessment", include_body=True, skill_disclosure="full")
            bodies = [s.get("body") for s in (context_package.get("skills") or []) if s.get("body")]
            skill_body = "\n\n".join(bodies)[:12000]
            system_prompt = build_system_prompt(
                project_id, "p2", node_state=node_state,
                task_type="assessment", skill_disclosure="metadata")
        except Exception:
            logger.warning("P2 context assembly failed (advisory, domain work proceeds)",
                           exc_info=True)  # 公理3: surface, not silent

        svc = self._service()
        result = await svc.assess(project_id, run_id=run_id, stage="p2", user_goal=user_goal,
                                  system_prompt=system_prompt, skill_body=skill_body,
                                  context_package=context_package)

        artifacts: List[str] = []
        evidence_refs: List[str] = []
        # §4.6 Artifact + §4.10 只在真正完成时落产物/证据 (blocked/failed 不伪造 completed)
        if result.status == "completed":
            artifacts = self._write_artifacts(project_id, result)
            persisted = svc.persist_evidence(project_id, result, stage="p2")
            evidence_refs = [e.get("evidence_id") for e in persisted]

        return {
            "status": result.status,
            "reason": result.reason,
            "analysis_only": result.analysis_only,
            "assessment_report": result.assessment_report,
            "risk_list": result.risk_list,
            "blocker_list": result.blocker_list,
            "uncertainty_list": result.uncertainty_list,
            "validation_gap_list": result.validation_gap_list,
            "resource_needs": result.resource_needs,
            "model_used": result.model_used,
            "artifacts": artifacts,
            "evidence_refs": evidence_refs,
            "context_refs": result.context_refs,
            "skill_refs": result.skill_refs,
            "assembly_trace": context_package.get("assembly_trace", {}),
            # WP-6 (Q-R17.3-6-2): 模型全失败中断的已尝试链路透传（WorkAgent → 前端显式报错）。
            "attempted_chain": getattr(result, "attempted_chain", []),
            "model_error_category": getattr(result, "model_error_category", ""),
            "model_user_actions": getattr(result, "model_user_actions", []),
        }

    def _write_artifacts(self, project_id: str, result) -> List[str]:
        """§4.6: 风险评估 / 阻塞项 / 验证缺口 / 资源需求 Artifact + §4.5 评估报告.
        D-107: P2 产物写入 artifacts/p2/ + 产出 P2 阶段完成包清单。"""
        art_dir = stage_artifact_dir(project_id, "p2")
        art_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "p2_assessment_report.json": {
                "artifact_type": "assessment_report", "analysis_only": result.analysis_only,
                "report": result.assessment_report, "uncertainty_list": result.uncertainty_list},
            "p2_risk_list.json": {"artifact_type": "risk_assessment", "items": result.risk_list},
            "p2_blocker_list.json": {"artifact_type": "blocker_list", "items": result.blocker_list},
            "p2_validation_gaps.json": {"artifact_type": "validation_gap", "items": result.validation_gap_list},
            "p2_resource_needs.json": {"artifact_type": "resource_needs", "items": result.resource_needs},
        }
        refs: List[str] = []
        for name, payload in files.items():
            _mediated_write(project_id, stage_artifact_ref("p2", name),
                            json.dumps({"project_id": project_id, "stage": "p2",
                                        "generated_at": _now(), **payload},
                                       ensure_ascii=False, indent=2),
                            auditor=self.auditor, stage="p2", action="write_p2_artifact")
            refs.append(stage_artifact_ref("p2", name))
        self._write_p2_package(project_id, refs, result)
        return refs

    _P2_PRODUCT_META = {
        "p2_assessment_report.json": ("assessment_report", "P2 评估报告（含 uncertainty_list）", True),
        "p2_risk_list.json": ("risk_assessment", "迁移/重构风险清单", True),
        "p2_blocker_list.json": ("blocker_list", "阻塞项清单", True),
        "p2_validation_gaps.json": ("validation_gap", "验证缺口清单", True),
        "p2_resource_needs.json": ("resource_needs", "资源需求建议", False),
    }

    def _write_p2_package(self, project_id: str, refs: list, result) -> None:
        """D-107: 写 artifacts/p2/_stage_package.json（P2 完成包清单，供 P3 按需加载）。"""
        products = []
        for ref in refs:
            fn = ref.split("/")[-1]
            t, desc, kfn = self._P2_PRODUCT_META.get(fn, (fn.rsplit(".", 1)[0], "P2 产物", False))
            products.append(product_entry(fn, t, desc, key_for_next=kfn))
        try:
            write_stage_package(
                project_id, "p2", products=products,
                evidence_summary={"risk_count": len(result.risk_list),
                                  "blocker_count": len(result.blocker_list),
                                  "validation_gap_count": len(result.validation_gap_list),
                                  "analysis_only": result.analysis_only},
                risks=[{"title": r.get("title"), "risk_level": r.get("risk_level")}
                       for r in (result.risk_list or []) if isinstance(r, dict)][:12],
                next_stage_advice=("进入 P3 规划：基于 P2 风险/阻塞/验证缺口/资源需求生成 Stage Plan / "
                                   "Task Plan(Batch) / TaskGraph。"),
                auditor=self.auditor, tracer=self.tracer)
        except Exception:
            logger.warning("P2 stage package 写入失败（advisory）", exc_info=True)

    def review(self, result: dict) -> ReviewResult:
        status = result.get("status")
        # blocked/failed → not passed; StageLoop escalates to a Gate (honest, §4.10)
        if status != "completed":
            reason = result.get("reason") or f"P2 评估未完成（status={status}）"
            rec = "配置有效模型 Key 后重试" if status == "blocked" else "检查模型调用与输入后重试"
            return ReviewResult(passed=False, issues=[{"type": "assessment_not_completed",
                                                       "detail": reason}],
                                recommendations=[rec], reviewer="p2_review_skill")
        issues = []
        # assessment_report 应为对象；真实 LLM 可返回合法 JSON 但值为字符串/散文（非 dict），
        # 此时 str 无 .get → 归一为 unparseable_output（走 ReviewPass 诚实返工），不崩溃不 500。
        report = result.get("assessment_report")
        if not isinstance(report, dict):
            report = {} if report in (None, "") else {"raw": report, "parse_error": True}
        # unparseable model output → retry structured output (ReviewPass rework)
        if report.get("parse_error"):
            issues.append({"type": "unparseable_output",
                           "detail": "模型输出 assessment_report 非结构化对象，无法解析 6 类产出"})
        # §4.9-6 / D-066: Evidence must be persisted
        if not result.get("evidence_refs"):
            issues.append({"type": "no_evidence", "detail": "P2 Evidence 未落库（D-066）"})
        # §4.7-5 / STOP-4: model output must be marked analysis_only
        if not result.get("analysis_only"):
            issues.append({"type": "model_output_not_marked",
                           "detail": "模型输出未标记为 analysis_only（§4.7-5）"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["补齐评估产出与 Evidence 后重试"] if issues else [],
                            reviewer="p2_review_skill")


class RealP3Handler:
    """P3 规划: PlanningService → Stage Plan → Task Plan(Batch) → TaskGraph(必生) →
    §5.6 Artifacts + §5.7 Evidence. Runs inside the LangGraph P3 node via StageLoop.

    execute chains T13→T14→T15→T16. Q-R10-2: no model Key → blocked → review fails
    → escalate to a Gate honestly (§5.10). Q-R10-3: a TaskGraph is ALWAYS generated.
    P4 is NOT registered (future_r11 stub); the P3→P4 Gate is created by
    make_work_node when this handler's loop passes.
    """

    goal = "P3 规划：基于 P2 评估生成 Stage Plan / Task Plan(Batch) / TaskGraph（必生）与执行/验证策略，供 P3→P4 Gate 审核"
    acceptance_criteria = [
        "Stage Plan 已生成", "Task Plan(Batch) 已生成", "TaskGraph 已生成（Q-R10-3 必生）",
        "边策略显式且通过校验", "P3 Evidence 已落库（§5.7 六项，D-066）", "高风险动作已标识",
    ]
    planned_actions = [
        "生成 Stage Plan（目标/范围/风险/策略）",
        "批量生成 Task Plan（Batch）",
        "生成 TaskGraph（节点/边/执行策略，必生）",
        "写入 P3 产物（stage_plan/task_plans/task_graph）",
        "落库 P3 Evidence（§5.7 六项，D-066）",
    ]

    def __init__(self, tracer=None, auditor=None, planning_service=None):
        self.tracer = tracer
        self.auditor = auditor
        self._svc = planning_service

    def _service(self):
        if self._svc is not None:
            return self._svc
        from app.services.planning_service import PlanningService
        return PlanningService(tracer=self.tracer, auditor=self.auditor)

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        run_id = state.get("run_id", "")
        user_goal = state.get("user_goal") or state.get("run_goal") or ""
        svc = self._service()

        # R10-5 P1-C: assemble C0-C6 context + system prompt (C3 Skill metadata only —
        # progressive disclosure) ONCE for the stage; threaded into all three planning
        # sub-calls so P3 runs through the unified context path (S4). Advisory on failure.
        context_package: dict = {}
        system_prompt: str | None = None
        try:
            from app.services.context_assembler import assemble_context, build_system_prompt
            node_state = {"node_task": "P3 规划：生成 Stage Plan / Task Plan / TaskGraph（必生）",
                          "task": "迁移方案与任务图规划"}
            context_package = assemble_context(
                project_id, "p3", node_state=node_state,
                task_type="planning", skill_disclosure="metadata")
            system_prompt = build_system_prompt(
                project_id, "p3", node_state=node_state,
                task_type="planning", skill_disclosure="metadata")
        except Exception:
            logger.warning("P3 context assembly failed (advisory, domain work proceeds)",
                           exc_info=True)  # 公理3: surface, not silent
        context_refs, skill_refs = svc.context_refs(context_package)

        # T13 → T14 → T15: each stage's blocked/failed short-circuits honestly (§5.10)
        sp = await svc.generate_stage_plan(project_id, run_id=run_id, stage="p3",
                                           user_goal=user_goal, system_prompt=system_prompt)
        if sp.status != "completed":
            return {"status": sp.status, "reason": sp.reason,
                    "stage_plan_ref": sp.stage_plan_id, "artifacts": [], "evidence_refs": [],
                    "attempted_chain": sp.attempted_chain,
                    "model_error_category": sp.model_error_category,
                    "model_user_actions": sp.model_user_actions}
        batch = await svc.generate_task_plans(project_id, sp.stage_plan_id,
                                              run_id=run_id, stage="p3", user_goal=user_goal,
                                              system_prompt=system_prompt)
        if batch.status != "completed":
            return {"status": batch.status, "reason": batch.reason,
                    "stage_plan_ref": sp.stage_plan_id, "artifacts": [], "evidence_refs": [],
                    "attempted_chain": batch.attempted_chain,
                    "model_error_category": batch.model_error_category,
                    "model_user_actions": batch.model_user_actions}
        tg = await svc.generate_task_graph(project_id, sp.stage_plan_id,
                                           run_id=run_id, stage="p3", user_goal=user_goal,
                                           system_prompt=system_prompt)
        if tg.status != "completed":
            return {"status": tg.status, "reason": tg.reason,
                    "stage_plan_ref": sp.stage_plan_id, "task_graph_ref": tg.task_graph_id,
                    "artifacts": [], "evidence_refs": [],
                    "attempted_chain": tg.attempted_chain,
                    "model_error_category": tg.model_error_category,
                    "model_user_actions": tg.model_user_actions}

        # T16 Evidence + §5.6 Artifacts (only when all three completed)
        persisted = svc.persist_p3_evidence(project_id, sp, batch, tg, stage="p3",
                                            context_refs=context_refs, skill_refs=skill_refs)
        evidence_refs = [e.get("evidence_id") for e in persisted]
        artifacts = self._write_artifacts(project_id, sp, batch, tg)
        return {
            "status": "completed", "stage_plan_ref": sp.stage_plan_id,
            "batch_id": batch.batch_id, "task_graph_ref": tg.task_graph_id,
            "task_plan_ids": batch.task_plan_ids, "degraded": tg.degraded,
            "batch_risk_level": batch.batch_risk_level, "gate_required": batch.gate_required,
            "artifacts": artifacts, "evidence_refs": evidence_refs, "model_used": sp.model_used,
            # C1: 主输出内联携带的上游引用（供 WorkAgent 构造 claim-evidence，非二遍归因）。
            "basis_refs": sp.basis_refs,
            "task_basis_refs": batch.task_basis_refs,
            "context_refs": context_refs, "skill_refs": skill_refs,
            "assembly_trace": context_package.get("assembly_trace", {}),
        }

    def _write_artifacts(self, project_id: str, sp, batch, tg) -> List[str]:
        """§5.6: 方案 / Stage Plan / Task Plan / TaskGraph / Gate 策略 / 验证策略 Artifact."""
        art_dir = workspace_service.workspace_path(project_id) / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "p3_stage_plan.json": {"artifact_type": "stage_plan", "stage_plan_ref": sp.stage_plan_id,
                                   "objective": sp.objective, "scope": sp.scope,
                                   "risk_level": sp.risk_level, "gate_policy": sp.gate_policy,
                                   "completion_criteria": sp.completion_criteria},
            "p3_task_plans.json": {"artifact_type": "task_plan", "batch_id": batch.batch_id,
                                   "task_plan_refs": batch.task_plan_ids,
                                   "batch_risk_level": batch.batch_risk_level,
                                   "gate_required": batch.gate_required},
            "p3_task_graph.json": {"artifact_type": "task_graph", "task_graph_ref": tg.task_graph_id,
                                   "node_count": tg.node_count, "edge_count": tg.edge_count,
                                   "degraded": tg.degraded},
        }
        refs: List[str] = []
        for name, payload in files.items():
            _mediated_write(project_id, f"artifacts/{name}",
                            json.dumps({"project_id": project_id, "stage": "p3",
                                        "generated_at": _now(), **payload},
                                       ensure_ascii=False, indent=2),
                            auditor=self.auditor, stage="p3", action="write_p3_artifact")
            refs.append(f"artifacts/{name}")
        return refs

    def review(self, result: dict) -> ReviewResult:
        status = result.get("status")
        if status != "completed":
            reason = result.get("reason") or f"P3 规划未完成（status={status}）"
            rec = "配置有效模型 Key 后重试" if status == "blocked" else "检查模型调用与输入后重试"
            return ReviewResult(passed=False, issues=[{"type": "planning_not_completed",
                                                       "detail": reason}],
                                recommendations=[rec], reviewer="p3_review_skill")
        issues = []
        if not result.get("stage_plan_ref"):
            issues.append({"type": "no_stage_plan", "detail": "Stage Plan 未生成（§5.9-2）"})
        if not result.get("task_graph_ref"):
            issues.append({"type": "no_task_graph", "detail": "TaskGraph 未生成（Q-R10-3 必生 / §5.9-4）"})
        if not result.get("evidence_refs"):
            issues.append({"type": "no_evidence", "detail": "P3 Evidence 未落库（D-066 / §5.9-8）"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["补齐 P3 计划产出与 Evidence 后重试"] if issues else [],
                            reviewer="p3_review_skill")


class RealP4Handler:
    """P4 执行（R11-3-C5 TaskGraph 执行链路）: 加载 P3 TaskGraph，经 TaskGraphEngine + NodeLoop
    按边策略推进 execution 节点。每个 execution 节点的 Node Worker = P4ExecutionWorker.execute_node
    （读 source/ 只读→经 WorkspaceMediator 写 output_code/+patches/(D-099/D-104)→真实文件派生
    Evidence，非 LLM 自报）；NodeLoop 9 步内含独立 Acceptance；边策略 success/failure/retry/rework/gate
    由 engine 路由，有界重试超阈诚实降级/Gate。

    诚实状态（§5.10 / 公理3 / D-097 / V10 教训，绝不伪造 completed）：
      - P3 TaskGraph 未找到或无 execution 节点 → blocked（缺前置输入）。
      - engine _finalize 诚实归类：任一节点 failed→graph failed；waiting_gate→graph waiting_gate；
        blocked→graph blocked；仅全部 completed/skipped→completed。
      - handler status：graph_status==completed→completed，其余→blocked（交 StageLoop 升级 Gate）。
    P4 handler 由 make_work_node 在 LangGraph p4_work 节点内调用，engine 是该节点的内部编排，不绕
    主编排（D-037）。C5 只准备 P4→P5 Gate（由 make_work_node 在 review passed 时建），不推进 P5 业务。
    C5 不接外部编程 Agent（C6）、不做 StagePageP4（C8）。node_type 路由依 R11-3-C2：execution。
    """

    goal = "P4 执行：经 TaskGraphEngine+NodeLoop 按边策略推进 P3 TaskGraph execution 节点，产出真实 output_code/patches 并过 Acceptance"
    acceptance_criteria = [
        "已加载 P3 TaskGraph 与 execution 节点",
        "经 TaskGraphEngine+NodeLoop 执行至少一个真实 execution 节点，产出 output_code+patch/diff",
        "NodeLoop 9 步 / Acceptance / 边策略均有证据（engine_events）",
        "Evidence 来自真实落盘文件；source/ 未被修改，写 source 负路径被拒记 Audit",
        "失败/阻塞/Gate 路径诚实归类，不静默、不伪造完成",
        "只准备 P4→P5 Gate，不推进 P5 业务",
    ]
    planned_actions = [
        "加载 P3 TaskGraph 与 execution 节点",
        "构建节点执行器（P4ExecutionWorker）",
        "驱动 TaskGraphEngine 按边策略执行",
        "NodeLoop 9 步（含 Acceptance + 边路由）",
        "节点独立验收（输出产物·sha256·Evidence 校验）",
        "汇集真实产物/patch/Evidence（D-066）",
    ]

    def __init__(self, tracer=None, auditor=None, gateway=None, aet=None):
        self.tracer = tracer
        self.auditor = auditor
        self._gateway = gateway
        self._aet = aet

    def _services(self):
        from app.dependencies import get_services
        return get_services()

    def _build_worker(self, project_id: str, reference_context: str = ""):
        from app.services.p4_execution_worker import P4ExecutionWorker
        gateway = self._gateway
        aet = self._aet
        if gateway is None or aet is None:
            svc = self._services()
            gateway = gateway if gateway is not None else svc.model_gateway
            aet = aet if aet is not None else svc.aet_service
        return P4ExecutionWorker(project_id, tracer=self.tracer, auditor=self.auditor,
                                 aet=aet, gateway=gateway,
                                 reference_context=reference_context)

    def _load_p3_task_graph(self, project_id: str) -> dict | None:
        """Load the latest P3 TaskGraph (definition) + its nodes for this project.

        Returns None when no P3 TaskGraph exists (P3 未完成 / 未生成). Read-only;
        follows the version-then-recency ordering used by the workspace aggregate.
        """
        from app.core.database import get_session
        from app.models.task_graph import TaskGraph, TaskNode
        db = get_session()
        try:
            tg = (db.query(TaskGraph)
                  .filter(TaskGraph.project_id == project_id, TaskGraph.stage == "p3")
                  .order_by(TaskGraph.version.desc(), TaskGraph.created_at.desc())
                  .first())
            if tg is None:
                return None
            tns = (db.query(TaskNode)
                   .filter(TaskNode.task_graph_id == tg.task_graph_id)
                   .order_by(TaskNode.created_at).all())
            nodes = [{"node_id": n.node_id, "node_type": n.node_type,
                      "title": n.title, "risk_level": n.risk_level,
                      "input_refs": n.input_refs or [],
                      "permission_boundary": n.permission_boundary or "workspace_read",
                      "stage": "p4"} for n in tns]
            return {"task_graph_id": tg.task_graph_id, "stage_plan_ref": tg.stage_plan_ref,
                    "graph_status": tg.graph_status, "edges": tg.edges or [],
                    "edge_count": len(tg.edges or []), "nodes": nodes}
        finally:
            db.close()

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]

        # 读取 project/run/stage context（advisory，与 P2/P3 一致；task_type=execution）
        context_package: dict = {}
        try:
            from app.services.context_assembler import assemble_context
            context_package = assemble_context(
                project_id, "p4",
                node_state={"node_task": "P4 执行：按 P3 TaskGraph 执行 execution 节点"},
                task_type="execution")
        except Exception:
            logger.warning("P4 context assembly failed (advisory, skeleton proceeds)",
                           exc_info=True)  # 公理3: surface, not silent

        assembly_trace = context_package.get("assembly_trace", {})
        tg = self._load_p3_task_graph(project_id)
        if tg is None:
            return {"status": "blocked",
                    "reason": "P3 TaskGraph 未找到（P3 未完成或未生成），P4 无法执行",
                    "task_graph_ref": None, "assembly_trace": assembly_trace,
                    "artifacts": [], "evidence_refs": []}

        node_type_dist: dict = {}
        for n in tg["nodes"]:
            node_type_dist[n["node_type"]] = node_type_dist.get(n["node_type"], 0) + 1
        exec_nodes = [n for n in tg["nodes"] if n["node_type"] == "execution"]
        if not exec_nodes:
            return {"status": "blocked",
                    "reason": f"P3 TaskGraph {tg['task_graph_id']} 无 execution 节点"
                              f"（node_type 分布 {node_type_dist}）",
                    "task_graph_ref": tg["task_graph_id"], "assembly_trace": assembly_trace,
                    "artifacts": [], "evidence_refs": []}

        # C5：把 execution 节点接入 TaskGraphEngine + NodeLoop，按 TaskGraph 边策略推进。
        # 每个 execution 节点的 Node Worker = P4ExecutionWorker.execute_node（真实写闭环）；
        # 非 execution 节点走 engine 默认执行器。NodeLoop 9 步内含 Acceptance；边策略
        # success/failure/retry/rework/gate 由 engine 路由；失败/阻塞/Gate 经 _finalize 诚实
        # 归类（绝不伪造 completed）。engine 由 handler 在 LangGraph p4_work 节点内调用（D-037）。
        run_id = state.get("run_id", "")
        # WP-B: pass C6 retrieved case/knowledge (already assembled in context_package)
        # into the worker so P4 code generation is informed by migration case/knowledge
        # reference. Empty retrieval → empty string (no reference block injected).
        p4_ref_ctx = ""
        _c6 = (context_package.get("layers") or {}).get("C6") or {}
        if _c6.get("capability_status") == "active":
            p4_ref_ctx = _c6.get("content", "")
        worker = self._build_worker(project_id, reference_context=p4_ref_ctx)

        # P3 TaskNode 无 acceptance_criteria 列 → NodeLoop step1 会因"缺 acceptance_criteria"
        # 阻塞。为 execution 节点注入默认 P4 结构化验收标准（由真实产物落盘背书），worker 据此
        # 产出 criteria_met 覆盖映射。注入到 tg["nodes"] 的同一节点对象，engine 与 worker 共享。
        _P4_CRITERIA = ["产出 output_code 产物", "产出 patch/diff", "Evidence 来自真实落盘文件"]
        for n in exec_nodes:
            if not n.get("acceptance_criteria"):
                n["acceptance_criteria"] = list(_P4_CRITERIA)

        def _make_exec(nd: dict):
            async def _fn():
                return await worker.execute_node(nd, run_id=run_id)
            return _fn
        node_executors = {n["node_id"]: _make_exec(n) for n in exec_nodes}

        from app.services.task_graph_service import TaskGraphEngine
        mode = workspace_service.get_execution_mode(project_id)
        engine = TaskGraphEngine(tracer=self.tracer, auditor=self.auditor)
        eng = await engine.execute({"nodes": tg["nodes"], "edges": tg["edges"]},
                                   node_executors=node_executors, project_id=project_id,
                                   run_id=run_id, mode=mode)

        # 证据/产物以真实落盘为准（D-066）：从 aet 汇集本 stage 的 Evidence + 其中的产物引用。
        aet = self._aet if self._aet is not None else self._services().aet_service
        try:
            p4_ev = [e for e in aet.list_evidence(project_id, stage="p4")]
        except Exception:
            logger.warning("P4 evidence 汇集失败（advisory）", exc_info=True)
            p4_ev = []
        evidence_refs = [e.get("evidence_id") for e in p4_ev if e.get("evidence_id")]
        artifacts: list[str] = []
        patch_refs: list[str] = []
        # C1: 每个 output_code 派生自哪个源文件（真实迁移溯源，来自落盘 Evidence 的 source_ref）——
        # 作为主输出内联引用供 WorkAgent 构造 claim-evidence（非第二遍 LLM 归因）。
        code_source_map: dict[str, str] = {}
        for e in p4_ev:
            if e.get("output_code_ref"):
                artifacts.append(e["output_code_ref"])
                if e.get("source_ref"):
                    code_source_map[e["output_code_ref"]] = e["source_ref"]
            if e.get("patch_ref"):
                artifacts.append(e["patch_ref"]); patch_refs.append(e["patch_ref"])
        acceptance_results = [{"node_id": nid, **((r or {}).get("acceptance") or {})}
                              for nid, r in eng.node_results.items()]

        # C7: write a structured P4 execution-summary report (change manifest + patch index +
        # per-node results) — the primary readable review material attached to the P4→P5 Gate.
        summary_ref = self._write_execution_summary(
            project_id, tg, exec_nodes, eng, p4_ev, patch_refs,
            node_type_dist, acceptance_results)

        # graph_status → handler status：completed→completed；其余（failed/waiting_gate/blocked/
        # rework_required）→ blocked（诚实非 completed，交 StageLoop 升级 Gate / 准备 P4→P5 Gate）。
        status = "completed" if eng.graph_status == "completed" else "blocked"

        # WP-6 (Q-R17.3-6-2): 若有 execution 节点因模型全失败中断 → 汇总首个模型错误链路到
        # 阶段级，供 WorkAgent/前端显式报错（不静默降级）。
        _p4_chain: list = []
        _p4_cat = ""
        _p4_actions: list = []
        for _nr in (eng.node_results.values() if hasattr(eng, "node_results") else []):
            if isinstance(_nr, dict) and _nr.get("model_error_category"):
                _p4_chain = _nr.get("attempted_chain", []) or []
                _p4_cat = _nr.get("model_error_category", "")
                _p4_actions = _nr.get("model_user_actions", []) or []
                break

        return {
            "status": status,
            "graph_status": eng.graph_status,
            "reason": eng.reason or f"P4 TaskGraph 执行结果：{eng.graph_status}",
            "task_graph_ref": tg["task_graph_id"],
            "stage_plan_ref": tg["stage_plan_ref"],
            "node_count": len(tg["nodes"]),
            "execution_node_count": len(exec_nodes),
            "completed_node_count": len(eng.completed_nodes),
            "failed_node_count": len(eng.failed_nodes),
            "gated_node_count": len(eng.gated_nodes),
            "completed_nodes": eng.completed_nodes,
            "failed_nodes": eng.failed_nodes,
            "gated_nodes": eng.gated_nodes,
            "node_type_distribution": node_type_dist,
            "edge_count": tg["edge_count"],
            "engine_events": eng.events,
            "acceptance_results": acceptance_results,
            "attempted_chain": _p4_chain,
            "model_error_category": _p4_cat,
            "model_user_actions": _p4_actions,
            "artifacts": artifacts + ([summary_ref] if summary_ref else []),
            "evidence_refs": evidence_refs,
            "patch_refs": patch_refs,
            "code_source_map": code_source_map,
            "assembly_trace": assembly_trace,
        }

    def _file_facts(self, project_id: str, rel_path: str) -> dict:
        """Re-read a real workspace file through the mediator and return sha256/bytes
        (honest evidence basis = real_file_on_disk). Empty dict if unreadable."""
        from app.services.workspace_service import workspace_path
        from app.services.workspace_mediator import WorkspaceMediator
        try:
            target = WorkspaceMediator(str(workspace_path(project_id))).guard_read(rel_path)
            raw = target.read_bytes()
            return {"path": rel_path,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw)}
        except Exception:
            return {"path": rel_path, "sha256": "", "bytes": 0}

    def _write_execution_summary(self, project_id, tg, exec_nodes, eng, p4_evidence,
                                 patch_refs, node_type_dist,
                                 acceptance_results) -> str | None:
        """C7: persist a structured P4 execution summary to artifacts/p4_execution_summary.json.

        Contains a change manifest (output_code files with real sha256/bytes), a patch index,
        and per-node execution results — all derived from real on-disk files (never the model's
        self-report). Returns the relative ref, or None if nothing to report.
        """
        evidence_refs = [e.get("evidence_id") for e in (p4_evidence or [])
                         if e.get("evidence_id")]
        artifacts = [e["output_code_ref"] for e in (p4_evidence or [])
                     if e.get("output_code_ref")]
        if not (artifacts or patch_refs or evidence_refs):
            return None
        from app.services.workspace_service import workspace_path
        from datetime import datetime, timezone

        def _now():
            return datetime.now(timezone.utc).isoformat()

        out_dir = workspace_path(project_id) / "artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Map output_code_ref → evidence_id so each output file carries its evidence links.
        ev_by_output = {e["output_code_ref"]: e.get("evidence_id")
                        for e in (p4_evidence or []) if e.get("output_code_ref")}
        change_manifest = []
        seen_out: set[str] = set()
        for rel in artifacts:
            if rel in seen_out or not rel.startswith("output_code/"):
                continue
            seen_out.add(rel)
            facts = self._file_facts(project_id, rel)
            ev = ev_by_output.get(rel)
            change_manifest.append({**facts, "kind": "output_code",
                                   "evidence_refs": [ev] if ev else []})
        # Map patch path → source_ref, taken from the real Evidence objects.
        src_by_patch = {e["patch_ref"]: e.get("source_ref")
                        for e in (p4_evidence or [])
                        if e.get("patch_ref") and e.get("source_ref")}
        patch_index = []
        for rel in dict.fromkeys(patch_refs):
            facts = self._file_facts(project_id, rel)
            patch_index.append({**facts, "kind": "patch",
                               "source_ref": src_by_patch.get(rel)})

        node_map = {n["node_id"]: n for n in tg.get("nodes", [])}
        per_node = []
        for nid in list(eng.completed_nodes) + list(eng.failed_nodes) + list(eng.gated_nodes):
            ndef = node_map.get(nid, {})
            res = (eng.node_results or {}).get(nid) or {}
            per_node.append({
                "node_id": nid,
                "title": ndef.get("title") or nid,
                "node_type": ndef.get("node_type") or "execution",
                "status": res.get("node_status") or res.get("status") or "unknown",
                "router": res.get("next_route", ""),
                "node_run_id": res.get("task_node_run_id"),
            })

        summary = {
            "stage": "p4",
            "kind": "execution_summary",
            "generated_at": _now(),
            "graph_id": tg.get("task_graph_id"),
            "graph_status": eng.graph_status,
            "run_id": getattr(eng, "run_id", ""),
            "node_count": len(tg.get("nodes", [])),
            "execution_node_count": len(exec_nodes),
            "completed_node_count": len(eng.completed_nodes),
            "failed_node_count": len(eng.failed_nodes),
            "gated_node_count": len(eng.gated_nodes),
            "blocked_node_count": (len(eng.node_results) - len(eng.completed_nodes)
                                   - len(eng.failed_nodes) - len(eng.gated_nodes)),
            "node_type_distribution": node_type_dist,
            "change_manifest": change_manifest,
            "patch_index": patch_index,
            "nodes": per_node,
            "evidence_refs": evidence_refs,
        }
        out = out_dir / "p4_execution_summary.json"
        ref = _mediated_write(project_id, f"artifacts/{out.name}",
                              json.dumps(summary, ensure_ascii=False, indent=2),
                              auditor=self.auditor, stage="p4",
                              action="write_execution_summary")
        logger.info("C7: wrote P4 execution summary %s (%d output_code, %d patches)",
                    ref, len(change_manifest), len(patch_refs))
        return ref

    def review(self, result: dict) -> ReviewResult:
        status = result.get("status")
        if status != "completed":
            reason = result.get("reason") or f"P4 未完成（status={status}）"
            rec = ("P3 未产出 TaskGraph：先完成 P3 规划并通过 P3→P4 Gate" if status == "blocked"
                   and not result.get("task_graph_ref")
                   else "检查模型可用性 / 节点产物 / Acceptance 后重试")
            return ReviewResult(passed=False,
                                issues=[{"type": "p4_execution_not_completed", "detail": reason}],
                                recommendations=[rec], reviewer="p4_review_skill")
        # completed：须有真实产物 + Evidence（D-066），否则不放行（不伪造完成）。
        issues = []
        if not result.get("artifacts"):
            issues.append({"type": "no_artifacts", "detail": "P4 未产出 output_code/patch 产物"})
        if not result.get("evidence_refs"):
            issues.append({"type": "no_evidence", "detail": "P4 Evidence 未落库（D-066）"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["补齐执行产物与 Evidence 后重试"] if issues else [],
                            reviewer="p4_review_skill")


class RealP5Handler:
    """P5 验证（R12-3-C3 骨架 + C4 硬必需槽位真实验证）。

    C4 完成 5 个硬必需槽位的真实验证：
      1. output_code 存在且非空
      2. patches 存在且与 output_code 对应
      3. P4 Evidence basis 真实（sha256 校验）
      4. P4 summary 可解析
      5. P4→P5 Gate approved

    C5 起执行有条件必需（构建/运行/测试/静态检查）真实命令。

    诚实状态（V10 教训 / D-066 / D-101 / D-105①）：
      - 缺失任一硬必需槽位 → P5 不得 completed（硬约束）
      - 失败/缺失 → blocked / evidence_gap（不伪造）
      - P5→P6 Gate 由 make_work_node 在 review passed 时创建（C7 起）
      - P6 仍保持 stub
      - 缺输入时 blocked

    P5 handler 由 make_work_node 在 LangGraph p5_work 节点内调用（D-037 不绕主编排）。
    """

    goal = "P5 验证：读取 P4 产物，执行 5 个硬必需槽位真实验证，创建 10 槽位 validation plan，诚实标记状态"
    acceptance_criteria = [
        "P5 输入事实源已读取（P4 output_code/patches/Evidence refs + summary + Gate）",
        "P5ValidationPlan 已创建（10 槽位）",
        "P4→P5 Gate = approved",
        "5 个硬必需槽位真实执行验证（不伪造）",
        "缺失任一硬必需槽位 → P5 不 completed（硬约束）",
        "有条件必需槽位标记为 evidence_gap（待 C5 真实命令验证）",
    ]
    planned_actions = [
        "读取 P4 输入事实源（output_code/patches/Evidence/summary/Gate）",
        "创建 P5ValidationPlan（10 槽位）",
        "真实验证 5 个硬必需槽位（不伪造）",
        "执行有条件必需槽位真实命令（构建/运行/测试/静态检查）",
        "诚实输出 P5 状态并持久化验证报告",
    ]

    def __init__(self, tracer=None, auditor=None, p5_input_service=None,
                 p5_verification_service=None):
        self.tracer = tracer
        self.auditor = auditor
        self._p5_input = p5_input_service
        self._p5_verify = p5_verification_service

    def _p5_input_service(self):
        if self._p5_input is not None:
            return self._p5_input
        from app.services.p5_input_service import P5InputService
        return P5InputService(self._services())

    def _p5_verification_service(self):
        if self._p5_verify is not None:
            return self._p5_verify
        from app.services.p5_verification_service import P5VerificationService
        return P5VerificationService(tracer=self.tracer, auditor=self.auditor,
                                     aet=self._services().aet_service)

    def _services(self):
        from app.dependencies import get_services
        return get_services()

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        run_id = state.get("run_id", "")

        # ① 读取 P4 输入事实源（C1 P5InputService）
        input_svc = self._p5_input_service()
        try:
            p4_input = input_svc.read_p4_input(project_id, run_id)
        except Exception as e:
            logger.warning("P5: P4 input read failed (honest blocked): %s", e, exc_info=True)
            return {"status": "blocked",
                    "reason": f"P4 输入读取异常：{type(e).__name__}",
                    "artifacts": [], "evidence_refs": []}

        # ② Gate 未 approved → 诚实 blocked
        if p4_input.blocked:
            return {"status": "blocked",
                    "reason": p4_input.blocked_reason,
                    "p4_to_p5_gate_id": p4_input.p4_to_p5_gate_id,
                    "p4_to_p5_gate_status": p4_input.p4_to_p5_gate_status,
                    "evidence_gaps": p4_input.evidence_gaps,
                    "artifacts": [], "evidence_refs": []}

        # ③ 创建 P5ValidationPlan (C2)
        from app.services.p5_validation_plan import (
            create_p5_validation_plan, P5SlotStatus, transition_slot_status,
            HARD_REQUIRED_SLOTS, CONDITIONAL_SLOTS, can_mark_completed,
            plan_to_dict,
        )
        plan = create_p5_validation_plan(project_id, run_id)

        # ④ C4: 5 个硬必需槽位真实验证（P5VerificationService）
        verify_svc = self._p5_verification_service()
        verify_results = verify_svc.verify_all_hard_required(project_id, p4_input)

        for vr in verify_results:
            slot = plan.get_slot(vr.slot_id)
            if slot is not None:
                # 状态转换：IN_PROGRESS → validated / validation_failed / evidence_gap
                transition_slot_status(slot, P5SlotStatus.IN_PROGRESS)
                transition_slot_status(slot, vr.status)
                slot.evidence_refs = vr.evidence_refs
                slot.artifacts = vr.artifacts
                slot.details.update(vr.details)
                for issue in vr.issues:
                    slot.issues.append(issue)

        # ⑤ C5: 有条件必需槽位真实命令验证（D-105① 全量包含）
        conditional_results = verify_svc.verify_conditional_slots(project_id)
        conditional_details = []
        for vr in conditional_results:
            slot = plan.get_slot(vr.slot_id)
            if slot is not None:
                if vr.status == P5SlotStatus.NOT_APPLICABLE:
                    # R12-16: 命令-less 槽位（库类 run）直接 PENDING→NA，不经 IN_PROGRESS
                    transition_slot_status(slot, P5SlotStatus.NOT_APPLICABLE)
                    slot.details.update(vr.details)
                else:
                    transition_slot_status(slot, P5SlotStatus.IN_PROGRESS)
                    if vr.status == P5SlotStatus.NEEDS_USER_INPUT:
                        # 命令不可识别 → needs_user_input（诚实，不伪造）
                        transition_slot_status(slot, P5SlotStatus.NEEDS_USER_INPUT)
                        slot.needs_user_input_prompt = vr.details.get("command", "")
                        slot.details.update(vr.details)
                    else:
                        # 真实命令执行结果
                        transition_slot_status(slot, vr.status)
                        slot.exit_code = vr.details.get("exit_code")
                        slot.command = vr.details.get("command")
                        slot.details.update(vr.details)
                        for issue in vr.issues:
                            slot.issues.append(issue)
            conditional_details.append({
                "slot_id": vr.slot_id,
                "command": vr.details.get("command"),
                "passed": vr.passed,
                "status": vr.status,
                "exit_code": vr.details.get("exit_code"),
                "elapsed_ms": vr.details.get("elapsed_ms"),
                "risk_level": vr.details.get("risk_level"),
                "issues": [i.get("type") for i in vr.issues],
                "gate_required": vr.details.get("gate_required", False),
            })

        can_complete, reason = can_mark_completed(plan)
        plan.can_be_completed = can_complete
        plan.blocked_reason = None if can_complete else reason

        # ⑥ 汇总验证结果
        hard_required_passed = sum(1 for vr in verify_results if vr.passed)
        conditional_passed = sum(1 for vr in conditional_results if vr.passed)
        conditional_gap = sum(1 for vr in conditional_results
                              if vr.status == P5SlotStatus.EVIDENCE_GAP)
        conditional_needs_input = sum(1 for vr in conditional_results
                                      if vr.status == P5SlotStatus.NEEDS_USER_INPUT)

        # ⑦ 持久化 P5 验证结果（R12-7 修复 B-P6-UNGATED-BY-P5 + R12-4-04）
        plan_dict = plan_to_dict(plan)
        self._persist_p5_validation_report(project_id, run_id, plan_dict,
                                            verify_results, conditional_details)

        # NEW-05 (R17.3-6 WP-5): artifacts 真实反映实际写入的产物。P5 handler 已把验证结果
        # 持久化到 artifacts/p5_validation_report.json（⑦），旧实现却硬编码 artifacts:[]，
        # 使 construction 报告 produced_artifacts 漏报该真实产物。按盘上存在性汇集。
        p5_report_rel = "artifacts/p5_validation_report.json"
        p5_produced = ([p5_report_rel]
                       if (workspace_service.workspace_path(project_id) / p5_report_rel).exists()
                       else [])

        # ⑧ Trace
        if self.tracer:
            self.tracer.write("stage_loop", action="p5_full_verification",
                              summary=(f"P5 全量验证 {hard_required_passed}/5 硬必需 + "
                                       f"{conditional_passed}/4 条件通过，"
                                       f"gap={conditional_gap} needs_input={conditional_needs_input}"),
                              project_id=project_id, run_id=run_id, stage="p5")
        return {
            "status": "completed" if can_complete else "blocked",
            "reason": reason if not can_complete
                      else "P5 全量验证通过（硬必需 + 条件必需真实命令）",
            "p4_to_p5_gate_id": p4_input.p4_to_p5_gate_id,
            "p4_output_code_refs": p4_input.output_code_refs,
            "p4_patch_refs": p4_input.patch_refs,
            "p4_evidence_refs": p4_input.evidence_refs,
            "p4_summary_ref": p4_input.p4_summary_ref,
            "p4_execution_summary": p4_input.p4_execution_summary,
            "validation_plan": plan_dict,
            "verify_results": [{"slot_id": vr.slot_id, "passed": vr.passed,
                                "status": vr.status, "issues": vr.issues}
                               for vr in verify_results],
            "conditional_results": conditional_details,
            "evidence_gaps": p4_input.evidence_gaps,
            "artifacts": p5_produced,
            "evidence_refs": p4_input.evidence_refs,
        }

    def review(self, result: dict) -> ReviewResult:
        """C6: review 接入 P5FailureRouter 返工路由。"""
        from app.services.p5_failure_router import P5FailureRouter, P5FailureType

        status = result.get("status")
        if status == "completed":
            return ReviewResult(passed=True, issues=[], recommendations=[],
                                reviewer="p5_review_skill")

        # C6: 失败分类 → 路由决策
        router = P5FailureRouter(max_retries=2)
        failure_type = self._classify_failure(result)
        route = router.route(failure_type, {"retry_count": 0, "result": result})

        # Evidence Gap → 标记 Gate 需求
        if route.gate_required:
            result["gate_required"] = True
            result["gate_reason"] = route.gate_reason
            result["gate_failure_type"] = failure_type

        # P4 rework 需求
        if route.p4_rework_required:
            result["p4_rework_required"] = True
            result["p4_rework_reason"] = route.plan_delta_reason

        # PlanDelta
        if route.plan_delta_type:
            result["plan_delta_type"] = route.plan_delta_type
            result["plan_delta_reason"] = route.plan_delta_reason

        # 构建 review issues（含返工建议）
        issues = [{"type": f"p5_{failure_type}", "detail": route.action}]
        recommendations = []
        if route.retry_allowed:
            recommendations.append(f"P5 有界重试（第 {route.retry_count} 次）")
        if route.p4_rework_required:
            recommendations.append("回 P4 执行修复（代码问题不在 P5 直接修改）")
        if route.gate_required:
            recommendations.append(f"创建 Gate：{route.gate_reason}")

        # 构建 evidence_gap 详情
        if failure_type in (P5FailureType.EVIDENCE_MISSING, P5FailureType.EVIDENCE_INVALID,
                            P5FailureType.NEEDS_USER_INPUT, P5FailureType.L4_L5_RISK):
            result["evidence_gap"] = {
                "gap_id": f"p5_gap_{failure_type}",
                "evidence_type": failure_type,
                "description": route.gate_reason,
                "blocking": route.blocked,
            }

        return ReviewResult(
            passed=False,
            issues=issues,
            recommendations=recommendations,
            reviewer="p5_review_skill",
        )

    def _persist_p5_validation_report(self, project_id, run_id, plan_dict,
                                       verify_results, conditional_details):
        """R12-7 持久化 P5 验证结果到工作区 artifacts/p5_validation_report.json。

        供 P6 门禁（B-P6-UNGATED-BY-P5）与 StagePageP5（R12-4-04）复用。
        单一事实源 = 工作区 JSON 文件（重启存活 + DB 持久化）。
        """
        try:
            from datetime import datetime, timezone
            report = {
                "project_id": project_id,
                "run_id": run_id,
                "stage": "p5",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "validation_plan": plan_dict,
                "verify_results": [{"slot_id": vr.slot_id, "passed": vr.passed,
                                    "status": vr.status, "issues": vr.issues}
                                   for vr in verify_results if not vr.passed],
                "conditional_results": conditional_details,
                "can_be_completed": plan_dict.get("can_be_completed", False),
            }
            ref = _mediated_write(project_id, "artifacts/p5_validation_report.json",
                                  json.dumps(report, ensure_ascii=False, indent=2),
                                  auditor=self.auditor, stage="p5",
                                  action="write_p5_validation_report")
            if self.tracer:
                self.tracer.write("evidence_event", action="p5_validation_persisted",
                                  summary=f"P5 验证报告持久化 {ref}",
                                  project_id=project_id, run_id=run_id, stage="p5")
        except Exception as e:
            logger.warning("P5: persist validation report failed: %s", e, exc_info=True)

    def _classify_failure(self, result: dict) -> str:
        """从验证结果中分类失败类型（C6）。"""
        from app.services.p5_failure_router import P5FailureType

        reason = result.get("reason", "")
        cond_results = result.get("conditional_results", [])

        # 有条件必需槽位失败
        for cr in cond_results:
            if cr.get("gate_required"):
                return P5FailureType.L4_L5_RISK
            if cr.get("status") == "needs_user_input":
                return P5FailureType.NEEDS_USER_INPUT
            cmd = cr.get("command", "")
            status = cr.get("status", "")
            sid = cr.get("slot_id", "")
            if status == "validation_failed":
                if sid == "build_verified":
                    return P5FailureType.BUILD_FAILED
                if sid == "tests_pass":
                    return P5FailureType.TEST_FAILED
                if sid == "static_check":
                    return P5FailureType.STATIC_CHECK_FAILED
                if "timeout" in str(cr.get("stderr_tail", "")).lower():
                    return P5FailureType.COMMAND_TIMEOUT
                return P5FailureType.COMMAND_FAILED

        # 硬必需槽位失败
        if "output_code" in reason.lower() or "no output_code" in reason.lower():
            return P5FailureType.OUTPUT_CODE_MISSING
        if "patch" in reason.lower():
            return P5FailureType.PATCH_MISSING
        if "evidence" in reason.lower() and "missing" in reason.lower():
            return P5FailureType.EVIDENCE_MISSING
        if "evidence" in reason.lower() and ("invalid" in reason.lower() or "不一致" in reason):
            return P5FailureType.EVIDENCE_INVALID
        if "gate" in reason.lower() and ("未" in reason or "not" in reason.lower()):
            return P5FailureType.NEEDS_USER_INPUT

        return P5FailureType.COMMAND_FAILED


class RealP6Handler:
    """P6 交付（R12-3-C9）。

    接入 P6 handler，实现交付阶段真实运行与最终 Gate。

    职责：
      - 读取 P5 passed evidence（P5InputService）+ 验证计划（P5ValidationPlan）。
      - 生成交付报告、交付索引、交付包（P6DeliveryService，C8）。
      - 创建 P6 最终 Gate / accepted-ready 状态。
      - 不绕过用户最终 Gate。

    诚实约束（D-023 / D-066 / D-105③）：
      - P6 completed 必须以交付包 + Evidence index + 用户 Gate 为前提。
      - P5 未通过时 P6 不得执行。
      - 旧验证结果保留 superseded 关系。
      - source 默认不包含在交付包中。
      - 创建最终 Gate 前做多类证据校验。

    P6 handler 由 make_work_node 在 LangGraph p6_work 节点内调用（D-037 不绕主编排）。
    """

    goal = "P6 交付：读取 P5 passed evidence，生成交付包 + 四类索引 + 交付报告，创建最终用户 Gate"
    acceptance_criteria = [
        "P5 completed 或用户接受风险",
        "交付包已生成（output_code + patches + reports + indexes + manifests）",
        "hash_manifest 完整（SHA-256）",
        "risk_manifest 记录未通过项",
        "P6 最终 Gate 真实创建（P 阶段晋级 Gate 必须用户授权）",
        "source 默认不包含",
    ]
    planned_actions = [
        "读取 P5 passed evidence 与验证报告",
        "生成交付包（output_code+patches+索引+清单）",
        "校验交付完整性（hash_manifest + 脱敏 + 风险清单）",
        "创建 P6 最终 Gate（用户授权，D-023）",
        "输出 accepted-ready 状态",
    ]

    def __init__(self, tracer=None, auditor=None, p6_delivery_service=None):
        self.tracer = tracer
        self.auditor = auditor
        self._p6_svc = p6_delivery_service

    def _services(self):
        from app.dependencies import get_services
        return get_services()

    def _p6_service(self):
        if self._p6_svc is not None:
            return self._p6_svc
        from app.services.p6_delivery_service import P6DeliveryService
        return P6DeliveryService(tracer=self.tracer, auditor=self.auditor)

    @staticmethod
    def _check_p5_validation_passed(project_id: str, run_id: str) -> bool:
        """R12-7 检查 P5 验证是否通过（读取持久化结果）。"""
        try:
            from app.services.workspace_service import workspace_path
            from app.services.workspace_mediator import WorkspaceMediator
            report_path = workspace_path(project_id) / "artifacts" / "p5_validation_report.json"
            if not report_path.exists():
                return False
            WorkspaceMediator(str(workspace_path(project_id))).guard_read(str(report_path))
            data = json.loads(report_path.read_text(encoding="utf-8"))
            return data.get("can_be_completed", False)
        except Exception as e:
            logger.warning("P6: P5 validation check failed: %s", e, exc_info=True)
            return False

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        run_id = state.get("run_id", "")

        # ① 读取 P5 passed evidence
        from app.services.p5_input_service import P5InputService
        input_svc = P5InputService()
        try:
            p4_input = input_svc.read_p4_input(project_id, run_id)
        except Exception as e:
            return {"status": "blocked",
                    "reason": f"P5 输入读取异常：{type(e).__name__}",
                    "artifacts": [], "evidence_refs": []}

        if p4_input.blocked:
            return {"status": "blocked",
                    "reason": f"P5 未通过（{p4_input.blocked_reason}），P6 不得执行",
                    "artifacts": [], "evidence_refs": []}

        # R12-7 修复 B-P6-UNGATED-BY-P5：读取 P5 验证结果（非仅 P4→P5 gate）
        p5_passed = self._check_p5_validation_passed(project_id, run_id)
        if not p5_passed:
            return {"status": "blocked",
                    "reason": "P5 验证未通过（读取 p5_validation_report.json），P6 不得执行",
                    "artifacts": [], "evidence_refs": []}

        # ② 生成交付包（P6DeliveryService，C8）
        p6_svc = self._p6_service()
        validation_plan = p4_input.p4_execution_summary or {}
        pkg = p6_svc.generate_delivery_package(project_id, run_id,
                                                p5_plan=validation_plan)

        if pkg.risk_manifest.get("blocking"):
            return {"status": "blocked",
                    "reason": f"交付包生成阻断：{pkg.risk_manifest.get('error', '')}",
                    "artifacts": [], "evidence_refs": []}

        # SEC-01（WP-4）：脱敏硬门禁。含疑似密钥/凭据的产物默认硬 blocked 交付；
        # 唯一放行路径 = 用户显式批准 desensitization_release Gate（L5）。不再仅软提示。
        if not pkg.desensitization_ok:
            from app.services.p6_delivery_service import (
                find_approved_desensitization_override, ensure_desensitization_gate,
                desensitization_risk_explanation,
            )
            override = find_approved_desensitization_override(project_id, run_id)
            if not override:
                gate_id = ensure_desensitization_gate(project_id, run_id, pkg,
                                                      tracer=self.tracer, auditor=self.auditor)
                return {
                    "status": "blocked",
                    "reason": ("SEC-01 脱敏硬门禁：交付包含疑似密钥/凭据，默认阻断交付；"
                               "须用户显式批准脱敏放行 Gate 后方可交付。"),
                    "desensitization": {
                        "ok": False,
                        "risk_explanation": desensitization_risk_explanation(pkg),
                    },
                    "desensitization_gate_id": gate_id,
                    "artifacts": [], "evidence_refs": [],
                }
            # override 已批准：用户已显式确认风险，允许继续交付（诚实标记放行来源）。

        # ③ 创建 P6 最终 Gate（用户最终授权，D-023）
        gate_id = self._create_p6_final_gate(project_id, run_id, pkg)

        # NEW-05 (R17.3-6 WP-5): 持久化交付报告为可查询产物，使 artifacts 真实反映实际写入
        # （P6DeliveryService 原仅在内存/按需重算，construction 报告 produced_artifacts 空）。
        # 仅落交付元数据（清单/索引/脱敏结论），不含 source/ 与密钥明文（D-105③/AGENTS §8）。
        delivery_report_ref = self._persist_p6_delivery_report(project_id, run_id, pkg)

        # ④ 组装输出
        return {
            "status": "completed",
            "reason": "P6 交付包已生成 + 最终 Gate 已创建（待用户批准）",
            "p6_delivery_package": {
                "delivery_manifest": pkg.delivery_manifest,
                "risk_manifest": pkg.risk_manifest,
                "hash_manifest": pkg.hash_manifest,
                "p6_delivery_report": pkg.p6_delivery_report,
            },
            "p5_validation_report": pkg.p5_validation_report,
            "indexes": pkg.indexes,
            "desensitization": {
                "ok": pkg.desensitization_ok,
                "issues": pkg.desensitization_issues,
            },
            "p6_final_gate_id": gate_id,
            "p5_evidence_refs": p4_input.evidence_refs,
            "artifacts": [delivery_report_ref] if delivery_report_ref else [],
            "evidence_refs": p4_input.evidence_refs or [],
        }

    def _persist_p6_delivery_report(self, project_id: str, run_id: str, pkg) -> str | None:
        """NEW-05 (R17.3-6 WP-5): 持久化 P6 交付报告到 artifacts/p6_delivery_report.json。

        参照 P5 `_persist_p5_validation_report` 范式，使 P6 handler 返回的 artifacts 真实
        反映实际写入（construction 报告 produced_artifacts 不欠报）。仅落交付元数据
        （delivery/risk/hash 清单 + AETA 索引 + 脱敏结论布尔），不含 source/ 与密钥明文。
        """
        try:
            report = {
                "project_id": project_id,
                "run_id": run_id,
                "stage": "p6",
                "generated_at": _now(),
                "delivery_manifest": pkg.delivery_manifest,
                "risk_manifest": pkg.risk_manifest,
                "hash_manifest": pkg.hash_manifest,
                "p6_delivery_report": pkg.p6_delivery_report,
                "indexes": pkg.indexes,
                "desensitization_ok": pkg.desensitization_ok,
            }
            ref = _mediated_write(project_id, "artifacts/p6_delivery_report.json",
                                  json.dumps(report, ensure_ascii=False, indent=2),
                                  auditor=self.auditor, stage="p6",
                                  action="write_p6_delivery_report")
            if self.tracer:
                self.tracer.write("evidence_event", action="p6_delivery_persisted",
                                  summary=f"P6 交付报告持久化 {ref}",
                                  project_id=project_id, run_id=run_id, stage="p6")
            return ref
        except Exception as e:
            logger.warning("P6: persist delivery report failed: %s", e, exc_info=True)
            return None

    def _create_p6_final_gate(self, project_id: str, run_id: str, pkg) -> str | None:
        """创建 P6 最终 Gate（用户最终授权，D-023）。"""
        try:
            svc = self._services()
            risk_count = pkg.risk_manifest.get("risk_count", 0)
            has_blocking = pkg.risk_manifest.get("has_blocking", False)
            summary_parts = [
                f"交付包已生成（{pkg.delivery_manifest.get('contents', {}).get('output_code_count', 0)} 产出 + "
                f"{pkg.delivery_manifest.get('contents', {}).get('patch_count', 0)} 补丁）",
            ]
            if risk_count > 0:
                summary_parts.append(f"{risk_count} 项风险")
            if not pkg.desensitization_ok:
                summary_parts.append(f"⚠ 脱敏扫描 {len(pkg.desensitization_issues)} 项需确认")

            gate = svc.gate_service.create(
                project_id=project_id,
                run_id=run_id,
                stage="p6",
                gate_type="stage_promotion",
                reason="P6 交付最终授权",
                summary="；".join(summary_parts),
                risk_level="L3" if has_blocking else "L1",
                options=["approve", "reject", "request_changes"],
                evidence_refs=pkg.p5_validation_report.get("evidence_refs", []),
            )
            if self.tracer:
                self.tracer.write("gate_event", action="create_p6_final_gate",
                                  summary=f"P6 最终 Gate {gate.gate_id} 创建",
                                  project_id=project_id, run_id=run_id, stage="p6")
            return gate.gate_id
        except Exception as e:
            logger.warning("P6: final gate create failed: %s", e, exc_info=True)
            return None

    def review(self, result: dict) -> ReviewResult:
        status = result.get("status")
        if status == "completed":
            gate_id = result.get("p6_final_gate_id")
            if gate_id:
                return ReviewResult(passed=True, issues=[], recommendations=[],
                                    reviewer="p6_review_skill")
            # 无 Gate 但 completed — 标记需要创建（不应发生但诚实处理）
            return ReviewResult(passed=False,
                                issues=[{"type": "no_gate_created",
                                         "detail": "P6 completed 但未创建最终 Gate（D-023 违规）"}],
                                recommendations=["创建 P6 最终 Gate 后重试"],
                                reviewer="p6_review_skill")
        reason = result.get("reason") or "P6 未完成"
        return ReviewResult(passed=False,
                            issues=[{"type": "p6_not_completed", "detail": reason}],
                            recommendations=["等待 P5 完成 + P5→P6 Gate approved 后重试"],
                            reviewer="p6_review_skill")


def bootstrap_graph_handlers(force: bool = False) -> None:
    """Register real P0/P1/P2/P3/P4/P5/P6 handlers + real Gate backend + writers.

    Idempotent. Called at app startup. Tests do NOT call this directly (conftest
    re-bootstraps with test services). P4/C5 TaskGraph execution; P5/C6 validation;
    P6/C8 delivery. Real handlers for all 6 stages now registered.

    R12-3-C9: P6 handler 不再只是 future stub，注册为 RealP6Handler。
    不绕过 LangGraph 主编排（D-037）。P6 最终 Gate 由 RealP6Handler 创建。
    """
    global _bootstrapped
    if _bootstrapped and not force:
        return
    from app.graph import nodes
    from app.graph.gate_backend import RealGateBackend
    from app.dependencies import get_services
    svc = get_services()
    nodes.set_tracer_auditor(svc.trace_writer, svc.audit_writer)
    nodes.register_handler("p0", RealP0Handler(svc.trace_writer, svc.audit_writer))
    nodes.register_handler("p1", RealP1Handler(svc.trace_writer, svc.audit_writer))
    nodes.register_handler("p2", RealP2Handler(svc.trace_writer, svc.audit_writer))
    nodes.register_handler("p3", RealP3Handler(svc.trace_writer, svc.audit_writer))
    nodes.register_handler("p4", RealP4Handler(svc.trace_writer, svc.audit_writer))
    nodes.register_handler("p5", RealP5Handler(svc.trace_writer, svc.audit_writer))
    nodes.register_handler("p6", RealP6Handler(svc.trace_writer, svc.audit_writer))
    nodes.set_gate_backend(RealGateBackend())
    _bootstrapped = True
