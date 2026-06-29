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
    ContextLayer, DEFAULT_CONTEXT_RECIPE,
    assemble_c0, assemble_c1, assemble_c2, assemble_c3,
    assemble_c4, assemble_c5, assemble_c6_empty,
    build_system_prompt_from_layers,
)
from app.services.workspace_service import workspace_path

logger = logging.getLogger("rebuild.context_assembler")


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
        arts = [p.name for p in sorted(art_dir.iterdir()) if p.is_file()]
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

    # ── Assemble C0-C6 layers ──────────────────────────────────────────────
    layers: dict[str, dict] = {}

    if "C0" in active_layers:
        layers["C0"] = assemble_c0(agent.get("forbidden", "") if agent else "")

    if "C1" in active_layers:
        layers["C1"] = assemble_c1()

    if "C2" in active_layers:
        layers["C2"] = assemble_c2()

    if "C3" in active_layers:
        layers["C3"] = assemble_c3(skills_with_body)

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

    if "C6" in active_layers:
        layers["C6"] = assemble_c6_empty()

    ctx["layers"] = layers

    # ── T4/T5: Case + Knowledge retrieval and injection (R9-5-4) ──────────
    cases: list[dict] = []
    knowledge: list[dict] = []
    try:
        cases = _retrieve_cases(project_id, current_stage)
        ctx["cases"] = cases
    except Exception as e:
        logger.debug("case retrieval skipped: %s", e)
        ctx["cases"] = []

    try:
        query = (node_state or {}).get("node_task") or current_stage
        knowledge = _retrieve_knowledge(current_stage, query)
        ctx["knowledge"] = knowledge
    except Exception as e:
        logger.debug("knowledge retrieval skipped: %s", e)
        ctx["knowledge"] = []

    # Assembly trace stats (for Trace writing + Evidence)
    ctx["assembly_trace"] = {
        "layers_assembled": list(layers.keys()),
        "skill_count": len(skills_with_body),
        "skills_with_body": sum(1 for s in skills_with_body if s.get("body")),
        "skills_not_connected": sum(1 for s in skills_with_body if s.get("capability_status") == "not_connected"),
        "selected_agent": agent.get("name", "") if agent else "",
        "recipe_layers": list(required_layers),
        "total_layer_chars": sum(v.get("chars", 0) for v in layers.values()),
        "case_count": len(cases),
        "knowledge_count": len(knowledge),
    }

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
) -> str:
    """Build a complete system_prompt string from assembled C0-C6 layers.

    This is the single entry point for any code needing a system_prompt.
    agent_loop._build_prompt should be replaced with this (T-10).
    """
    ctx = assemble_context(
        project_id, current_stage,
        project=project, run=run, node_state=node_state,
        agent_type=agent_type, task_type=task_type,
        include_skills=True, include_body=False,
    )
    agent_name = (ctx.get("selected_agent") or {}).get("name", "AI 助手")
    layers = ctx.get("layers", {})
    return build_system_prompt_from_layers(layers, current_stage, agent_name, user_message)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_recipe(agent: Optional[dict]) -> dict:
    """Get the effective context_recipe, falling back to DEFAULT_CONTEXT_RECIPE."""
    if agent and agent.get("context_recipe"):
        recipe = dict(DEFAULT_CONTEXT_RECIPE)
        recipe.update(agent["context_recipe"])
        return recipe
    return dict(DEFAULT_CONTEXT_RECIPE)


def _retrieve_cases(project_id: str, stage: str, max_cases: int = 3) -> list[dict]:
    """Retrieve relevant case references for injection (T4.1 / R9-5-4).

    Returns list of {resource_id, name, description, never_execute, body_snippet}.
    never_execute=True always (D-061: cases are read-only reference).
    """
    try:
        from app.core.database import get_session
        from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
        db = get_session()
        try:
            entries = db.query(ResourceEntry).filter(
                ResourceEntry.resource_type == ResourceType.case,
                ResourceEntry.enabled == True,
                ResourceEntry.status.in_([ResourceStatus.active, ResourceStatus.read_only]),
            ).limit(max_cases).all()
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
                        pass
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
