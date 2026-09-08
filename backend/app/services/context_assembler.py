"""Context Package Assembler — C0-C6 分层 + context_recipe 解析 + SKILL.md 正文注入 (R9-5-3).

Single source of truth for context assembly (X-4-5): ALL callers must use this module.
No caller should build its own system_prompt from scratch.

Public API:
  assemble_context(project_id, stage, *, project, run, node_state, agent_type, task_type,
                   include_body) -> dict
  build_system_prompt(project_id, stage, *, project, run, node_state, agent_type, task_type,
                      user_message) -> str

Backward compatible: callers using the old assemble_context(project_id, stage, project=..., run=...,
include_skills=True) still work — response dict still contains 'skills' key.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.services.context_layers import (
    ContextLayer, DEFAULT_CONTEXT_RECIPE, LAYER_PRIORITY,
    assemble_c0, assemble_c1, assemble_c2, assemble_c3,
    assemble_c4, assemble_c5, assemble_c6,
    build_system_prompt_from_layers,
    estimate_char_budget, plan_budget_trim,
)
from app.services.scenario_loader import resolve_scenario_pack
from app.services.workspace_service import workspace_path

logger = logging.getLogger("rebuild.context_assembler")

# R17.5 P2 返工3：每阶段的【主工作流 skill】——stage-default 会广选本阶段 + common 全部 skill，
# 主 skill 若排在后面会被下游 skill_body 预算([:12000]) 截断出 prompt（skill-first 名存实亡）。
# 装配后将主 skill 置顶，保证其正文优先落入预算。名称即 SkillDefinition.name（平台稳定配置，
# 非样本实例值）；缺失则该阶段无置顶（安全降级）。
STAGE_PRIMARY_SKILL = {
    "p0": "P-codebase-onboarding",
    "p1": "full-stack-profiler",
    "p2": "P-migration-assessment",
    "p3": "P-migration-planning",
    "p4": "P-migration-execution",
    "p5": "P-migration-verification",
    "p6": "P-migration-delivery",
}


def _primary_skill_first(skills: list, stage: str) -> list:
    """把本阶段主 skill 移到列表首位（保序其余项），确保其正文优先进入 skill_body 预算。"""
    primary = STAGE_PRIMARY_SKILL.get((stage or "").lower())
    if not primary or not skills:
        return skills
    head = [s for s in skills if s.get("name") == primary]
    if not head:
        return skills
    rest = [s for s in skills if s.get("name") != primary]
    return head + rest


def assemble_context(
    project_id: str,
    current_stage: str,
    *,
    project: Optional[dict] = None,
    run: Optional[dict] = None,
    include_skills: bool = True,   # backward-compat flag
    include_body: bool = False,    # T-11: include SKILL.md body in /context response
    agent_type: str = "node_worker",
    task_type: str = "default",
    node_state: Optional[dict] = None,
    skill_disclosure: str = "full",  # R10-5 P1-C: "full" | "metadata" (progressive disclosure)
) -> dict:
    """Assemble a full C0-C6 context package for project_id/stage.

    Returns a dict with:
      project_id, current_stage, assembled_at,
      project (if provided), run (if provided),
      workspace (path + existence flags),
      skills (backward-compat list; body included only when include_body=True),
      layers (C0-C6 structured data),
      selected_agent (name/type),
      context_recipe (resolved recipe),
      assembly_trace (stats for Trace writing),
    """
    from app.services.agent_selector import select_agent, resolve_bindings
    ctx: dict = {
        "project_id": project_id,
        "current_stage": current_stage,
        "assembled_at": _now(),
    }

    # Project + run base info (backward-compat)
    if project:
        ctx["project"] = {
            "name": project.get("name", ""),
            "source_type": project.get("source_type", ""),
            "workspace_status": project.get("workspace_status", ""),
            "onboarding_done": project.get("onboarding_done", False),
            "coding_agent_ref": project.get("coding_agent_ref"),
            # R20-3: 场景值必须进白名单，否则它到不了下游（C1 场景块 / 身份句 / trace）。
            "scenario": project.get("scenario") or "",
        }
    if run:
        ctx["run"] = {
            "run_id": run.get("run_id"),
            "run_goal": run.get("run_goal", ""),
            "execution_mode": run.get("execution_mode", "plan"),
            "run_status": run.get("run_status", ""),
        }

    # Workspace state
    ws = workspace_path(project_id)
    arts = []
    art_dir = ws / "artifacts"
    if art_dir.exists():
        # D-107: 产物按 artifacts/{stage}/ 分层。除根目录扁平产物外，递归各阶段
        # 子目录（p0-p6）收集产物名，过滤 `_` 开头的清单文件（如 _stage_package.json）。
        for p in sorted(art_dir.iterdir()):
            if p.is_file() and not p.name.startswith("_"):
                arts.append(p.name)
        for stage in ("p0", "p1", "p2", "p3", "p4", "p5", "p6"):
            sub = art_dir / stage
            if sub.is_dir():
                for p in sorted(sub.iterdir()):
                    if p.is_file() and not p.name.startswith("_"):
                        arts.append(f"{stage}/{p.name}")
    workspace_info = {
        "path": str(ws),
        "source_exists": (ws / "source").exists() and any((ws / "source").iterdir()),
        "materials_exists": (ws / "materials").exists(),
        "artifacts_exists": art_dir.exists(),
        "artifacts_list": arts[:20],
    }
    ctx["workspace"] = workspace_info

    # Select agent + recipe
    agent = select_agent(current_stage, task_type)
    recipe = _resolve_recipe(agent)
    required_layers = set(recipe.get("required_context_layers", ["C0", "C1", "C2", "C3", "C4"]))
    optional_layers = set(recipe.get("optional_layers", ["C5", "C6"]))
    active_layers = required_layers | optional_layers
    max_chars_per_skill = int(recipe.get("max_chars_per_skill", 4000))

    if agent:
        ctx["selected_agent"] = {
            "agent_id": agent.get("agent_id", ""),
            "name": agent.get("name", ""),
            "agent_type": agent.get("agent_type", agent_type),
        }
    ctx["context_recipe"] = recipe

    # Resolve skills bindings (for C3 + backward-compat 'skills' key)
    bindings = resolve_bindings(agent or {}, current_stage, max_chars_per_skill) if include_skills else {"skills": [], "tools": []}
    skills_with_body = bindings.get("skills", [])

    # Backward-compat 'skills' key (keep same shape as before, body included only if include_body)
    ctx["skills"] = [
        {
            "skill_id": s.get("skill_id", ""),
            "name": s.get("name", ""),
            "category": s.get("category", ""),
            "description": s.get("description", s.get("meta", {}).get("description", "")),
            "status": s.get("capability_status", ""),
            **({"body": s.get("body", ""), "body_truncated": s.get("body_truncated", False)}
               if include_body else {}),
        }
        for s in skills_with_body
    ]
    # R17.5 P2 返工3：把本阶段【主工作流 skill】置顶，确保其正文落入下游 skill_body 预算
    # （handlers join(bodies)[:12000]），不被广选的跨阶段 skill 挤出（skill-first 真生效）。
    ctx["skills"] = _primary_skill_first(ctx["skills"], current_stage)

    # ── Assemble C0-C6 layers ──────────────────────────────────────────────
    layers: dict[str, dict] = {}

    # R20-3: 解析项目场景包（每次真读盘，无缓存 —— 用户改写后下次装配即生效）。
    # 场景值只做两件事：拼路径（loader 内已做形状 + 归属校验）与原样进 prompt 文本；
    # 此处没有、也不得有任何以场景值为条件的分支。
    scenario_pack = resolve_scenario_pack((project or {}).get("scenario"))

    if "C0" in active_layers:
        layers["C0"] = assemble_c0(agent.get("forbidden", "") if agent else "")

    if "C1" in active_layers:
        layers["C1"] = assemble_c1(scenario_pack=scenario_pack)

    if "C2" in active_layers:
        layers["C2"] = assemble_c2()

    if "C3" in active_layers:
        layers["C3"] = assemble_c3(skills_with_body, disclosure=skill_disclosure)

    # 公理3: skill load failures must surface, not stay silent. If any bound/stage
    # skill failed to load its SKILL.md (capability_status=not_connected), warn.
    _nc = [s for s in skills_with_body if s.get("capability_status") == "not_connected"]
    if _nc:
        logger.warning(
            "context_assembler[%s/%s]: %d skill(s) not_connected (SKILL.md 加载失败/缺失): %s",
            project_id, current_stage, len(_nc),
            [s.get("name") or s.get("skill_id") for s in _nc],
        )

    if "C4" in active_layers:
        layers["C4"] = assemble_c4(
            project or {},
            run,
            current_stage,
            workspace_info,
        )

    if "C5" in active_layers:
        ns = node_state or {}
        layers["C5"] = assemble_c5(
            node_task=ns.get("task") or ns.get("node_task"),
            upstream_output=ns.get("upstream_output"),
            acceptance_feedback=ns.get("acceptance_feedback"),
        )

    ctx["layers"] = layers

    # ── T4/T5: Case + Knowledge retrieval and injection (R9-5-4) ──────────
    # WP-B fix: retrieved case/knowledge bodies are injected into the C6 dynamic
    # layer so they reach the model prompt via build_system_prompt_from_layers
    # (previously ctx["cases"]/ctx["knowledge"] were computed but only used for
    # assembly_trace stats — they never entered any system_prompt).
    cases: list[dict] = []
    knowledge: list[dict] = []
    try:
        cases = _retrieve_cases(project_id, current_stage)
        ctx["cases"] = cases
    except Exception as e:
        logger.warning("case retrieval failed (公理3, C6 案例层将标空): %s", e)
        ctx["cases"] = []

    try:
        query = (node_state or {}).get("node_task") or current_stage
        knowledge = _retrieve_knowledge(current_stage, query)
        ctx["knowledge"] = knowledge
    except Exception as e:
        logger.warning("knowledge retrieval failed (公理3, C6 知识层将标空): %s", e)
        ctx["knowledge"] = []

    # Assemble C6 dynamic layer from the real retrieval results (empty → honest 标空).
    if "C6" in active_layers:
        layers["C6"] = assemble_c6(cases, knowledge)

    # ── R21: 装配清单（layers_manifest）——本次装配了哪些层/每层字符数/是否截断/
    # 是否因预算裁剪未纳入。清单只含元数据（层名/整数/布尔/skill 名单），不含任何层的
    # 正文原文（体积 + 脱敏，测试 test_assemble_context_carries_scenario_into_prompt_and_trace
    # 钉住"trace 不得携带正文原文"），故无需过 redact_secrets（无自由文本字段可能夹带 Key）。
    max_context_budget = recipe.get("max_context_budget", DEFAULT_CONTEXT_RECIPE["max_context_budget"])
    max_chars_budget = estimate_char_budget(max_context_budget)
    trim_plan = plan_budget_trim(layers, max_chars_budget)
    dropped_by_budget = {entry["layer"] for entry in trim_plan if entry["dropped_by_budget"]}
    skill_names_hit = [s.get("name") or s.get("skill_id") or "" for s in skills_with_body]

    layers_manifest: list[dict] = []
    for layer in LAYER_PRIORITY:
        key = layer.value
        entry = layers.get(key)
        item = {
            "layer": key,
            "assembled": entry is not None,
            "chars": entry.get("chars", 0) if entry else 0,
            "dropped_by_budget": key in dropped_by_budget,
        }
        if key == "C3":
            # 复用 skill_loader 已有的 body_truncated 标记（不新造截断判定）。
            item["truncated"] = any(s.get("body_truncated") for s in skills_with_body)
            item["skills_hit"] = skill_names_hit
        elif key == "C1":
            # 复用场景块已有的三份 truncated 标记（skill_body/anchors/risks）。
            item["truncated"] = any(scenario_pack.get(k) for k in
                                    ("skill_body_truncated", "anchors_truncated", "risks_truncated"))
        else:
            # 该层目前没有可复用的既有截断标记（如 C5 的 [:1000]/[:500] 是硬字符切片，
            # 未落标记）——如实标 None（未跟踪），不假称 False（公理3：不冒充精确）。
            item["truncated"] = None
        layers_manifest.append(item)

    # Assembly trace stats (for Trace writing + Evidence)
    ctx["assembly_trace"] = {
        "layers_assembled": list(layers.keys()),
        "layers_manifest": layers_manifest,
        "budget": {
            "spec": max_context_budget,
            "estimated_max_chars": max_chars_budget,
            "total_chars_before_trim": sum(v.get("chars", 0) for v in layers.values()),
            "trimmed": bool(dropped_by_budget),
            "dropped_layers": sorted(dropped_by_budget),
        },
        "skill_count": len(skills_with_body),
        "skills_with_body": sum(1 for s in skills_with_body if s.get("body")),
        "skills_not_connected": sum(1 for s in skills_with_body if s.get("capability_status") == "not_connected"),
        "selected_agent": agent.get("name", "") if agent else "",
        "recipe_layers": list(required_layers),
        "total_layer_chars": sum(v.get("chars", 0) for v in layers.values()),
        "case_count": len(cases),
        "knowledge_count": len(knowledge),
        # R20-3: 把"场景没接上"从隐性失败变成显性字段。assemble_context 有多个调用方各自构造
        # project dict；缺 scenario 键时该阶段会静默拿不到场景，症状是"场景没生效"而非报错。
        # scenario_source 让这种漏接线在 /context 响应里直接可见。
        # trace 只记标识/状态/字符数，【不写入】SKILL.md / 锚点 / 风险的正文原文（体积 + 脱敏）。
        "scenario": scenario_pack.get("scenario_id", ""),
        "scenario_status": (scenario_pack.get("fallback") or {}).get("code", "ok"),
        "scenario_source": "project_dict" if "scenario" in (project or {}) else "absent",
        "scenario_tier": scenario_pack.get("tier", ""),
        "scenario_chars": {
            "skill_body": len(scenario_pack.get("skill_body", "")),
            "anchors": len(scenario_pack.get("anchors_text", "")),
            "risks": len(scenario_pack.get("risks_text", "")),
        },
        "scenario_truncated": {
            "skill_body": bool(scenario_pack.get("skill_body_truncated")),
            "anchors": bool(scenario_pack.get("anchors_truncated")),
            "risks": bool(scenario_pack.get("risks_truncated")),
        },
        "scenario_notices": [
            {"code": n.get("code", ""), "message": n.get("message", "")}
            for n in (scenario_pack.get("notices") or [])
        ],
    }
    # 供 build_system_prompt 生成身份句附加语（不含正文，故可安全出现在 /context 响应）
    ctx["scenario_line"] = _scenario_line(scenario_pack)

    return ctx


def build_system_prompt(
    project_id: str,
    current_stage: str,
    *,
    project: Optional[dict] = None,
    run: Optional[dict] = None,
    node_state: Optional[dict] = None,
    agent_type: str = "node_worker",
    task_type: str = "default",
    user_message: str = "",
    skill_disclosure: str = "full",  # R10-5 P1-C: "full" | "metadata"
) -> str:
    """Build a complete system_prompt string from assembled C0-C6 layers.

    This is the single entry point for any code needing a system_prompt.
    agent_loop._build_prompt should be replaced with this (T-10).

    skill_disclosure="metadata" makes the C3 layer carry only Skill name+description
    (progressive disclosure) — the full SKILL.md body is loaded on demand in the
    tool loop, never dumped wholesale into the system prompt (R10-5 P1-C).
    """
    ctx = assemble_context(
        project_id, current_stage,
        project=project, run=run, node_state=node_state,
        agent_type=agent_type, task_type=task_type,
        include_skills=True, include_body=False,
        skill_disclosure=skill_disclosure,
    )
    agent_name = (ctx.get("selected_agent") or {}).get("name", "AI 助手")
    layers = ctx.get("layers", {})
    recipe = ctx.get("context_recipe", {}) or {}
    return build_system_prompt_from_layers(
        layers, current_stage, agent_name, user_message,
        scenario_line=ctx.get("scenario_line", ""),
        max_context_budget=recipe.get("max_context_budget"),
    )


def _scenario_line(pack: dict) -> str:
    """依场景 manifest 生成身份句的场景附加语（R20-3）。

    分支只看"是否发生了回落"（`fallback` 字段是否存在），**不看场景值本身** —— 故不构成
    以场景值为条件的分派面（R20-2-05）。场景名一律取自 manifest，代码内无任何场景值字面量。
    """
    if not pack:
        return "当前项目尚未选择重构场景，请勿假设目标技术栈。"
    if pack.get("fallback"):
        return "当前项目尚未选择可用的重构场景包，请勿假设目标技术栈。"
    name = pack.get("display_name") or pack.get("scenario_id") or ""
    if not name:
        return "当前项目尚未选择重构场景，请勿假设目标技术栈。"
    return f"当前协助用户完成【{name}】重构项目。"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_recipe(agent: Optional[dict]) -> dict:
    """Get the effective context_recipe, falling back to DEFAULT_CONTEXT_RECIPE."""
    if agent and agent.get("context_recipe"):
        recipe = dict(DEFAULT_CONTEXT_RECIPE)
        recipe.update(agent["context_recipe"])
        return recipe
    return dict(DEFAULT_CONTEXT_RECIPE)


def _case_relevance_score(entry, project_id: str, stage: str) -> int:
    """GAP-CASE-1: score a case entry's relevance to (project_id, stage).

    Generic (禁硬编码 MicroOA)：用 type_metadata 的 applicable_stages / project_id 显式绑定，
    再退化到 stage/project token 在 name/description/tags 的文本匹配。
    """
    meta = entry.type_metadata or {}
    score = 0
    stage_l = (stage or "").lower()
    # explicit stage binding
    stages = meta.get("applicable_stages") or meta.get("stages") or []
    if isinstance(stages, str):
        stages = [stages]
    if any(str(s).lower() == stage_l for s in stages):
        score += 3
    # explicit project binding
    if project_id and str(meta.get("project_id") or "") == project_id:
        score += 3
    # textual match on stage token
    text = f"{entry.name or ''} {entry.description or ''} {' '.join(str(t) for t in (meta.get('tags') or []))}".lower()
    if stage_l and stage_l in text:
        score += 1
    return score


def _retrieve_cases(project_id: str, stage: str, max_cases: int = 3) -> list[dict]:
    """Retrieve relevant case references for injection (T4.1 / R9-5-4 + GAP-CASE-1).

    Returns list of {resource_id, name, description, never_execute, body_snippet}.
    never_execute=True always (D-061: cases are read-only reference). Candidates are
    ranked by relevance to (project_id, stage); when any candidate is relevant, only
    relevant ones are returned (irrelevant excluded). When no candidate carries
    stage/project signals, falls back to the enabled pool (no regression).
    """
    try:
        from app.core.database import get_session
        from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
        db = get_session()
        try:
            pool = db.query(ResourceEntry).filter(
                ResourceEntry.resource_type == ResourceType.case,
                ResourceEntry.enabled == True,
                ResourceEntry.status.in_([ResourceStatus.active, ResourceStatus.read_only]),
            ).limit(50).all()
            # GAP-CASE-1: rank by relevance; keep relevant ones if any, else fall back.
            scored = [(_case_relevance_score(e, project_id, stage), e) for e in pool]
            relevant = [(s, e) for s, e in scored if s > 0]
            if relevant:
                relevant.sort(key=lambda x: -x[0])
                entries = [e for _s, e in relevant[:max_cases]]
            else:
                entries = pool[:max_cases]
            results = []
            for e in entries:
                meta = e.type_metadata or {}
                body_snippet = ""
                body_path = meta.get("body_path")
                if body_path:
                    try:
                        from pathlib import Path
                        body_snippet = Path(body_path).read_text("utf-8", errors="replace")[:300]
                    except Exception:
                        # advisory：案例正文片段仅为上下文增强，读取失败则退化为使用 description，
                        # 不影响上下文装配主流程。
                        logger.debug("读取案例正文片段失败，回退 description body_path=%s",
                                     body_path, exc_info=True)
                if not body_snippet:
                    body_snippet = (e.description or "")[:300]
                results.append({
                    "resource_id": e.resource_id,
                    "name": e.name,
                    "description": e.description or "",
                    "never_execute": True,  # D-061: cases are always read-only
                    "body_snippet": body_snippet,
                })
            return results
        finally:
            db.close()
    except Exception as e:
        logger.debug("_retrieve_cases failed: %s", e)
        return []


def _retrieve_knowledge(stage: str, query: str, max_items: int = 3) -> list[dict]:
    """Retrieve relevant knowledge snippets for context injection (T5.4 / R9-5-4).

    Returns list of {resource_id, name, snippet, retrieval_mode}.
    """
    try:
        from app.core.database import get_session
        from app.services.knowledge_search import search
        db = get_session()
        try:
            results = search(query, db, limit=max_items, scope="all")
            return [
                {
                    "resource_id": r["resource_id"],
                    "name": r["name"],
                    "snippet": r["snippet"][:500],
                    "retrieval_mode": r.get("retrieval_mode", "fulltext_like"),
                }
                for r in results
            ]
        finally:
            db.close()
    except Exception as e:
        logger.debug("_retrieve_knowledge failed: %s", e)
        return []


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
