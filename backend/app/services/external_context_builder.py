"""External context builder — assembles project context for injection into OpenCode.

D-088① / D-077 / R9-5-5 T5: Produces an injection-ready context text from the
platform's C0-C6 context package (R9-5-3 assemble_context), cropped to the
requested context_policy:

  full    — full project fact-source + current stage materials
  summary — fact-source summary + materials table of contents
  minimal — task description + key fact pointers only

The output is a plain-text string (no credentials, G9) suitable for injection
as the first ACP session message.

Depends on R9-5-3 assemble_context (context_assembler.py) — if that is not
available, raises ExternalContextBuildError explicitly (G8, no silent fallback).
"""

from __future__ import annotations

import logging

log = logging.getLogger("rebuild.external_context_builder")

POLICY_FULL = "full"
POLICY_SUMMARY = "summary"
POLICY_MINIMAL = "minimal"
_VALID_POLICIES = {POLICY_FULL, POLICY_SUMMARY, POLICY_MINIMAL}


class ExternalContextBuildError(Exception):
    """Raised when context assembly fails and caller must not proceed."""


def build_external_context(
    project_id: str,
    stage: str,
    *,
    context_policy: str = POLICY_FULL,
    project: object | None = None,
    run: object | None = None,
    task: str = "",
) -> dict:
    """Build the context text + material file list for an external platform session.

    Args:
        project_id: Platform project UUID.
        stage: Current P-stage string (e.g. "P4").
        context_policy: "full" / "summary" / "minimal".
        project: ORM/schema object for project context (optional, speeds up assembly).
        run: Current run dict/object (optional).
        task: The task description to prepend in all policies.

    Returns:
        {
          "context_text": str,         # inject as first ACP message
          "material_files": list[str], # workspace paths to expose as read-only
          "policy": str,               # echo back the applied policy
          "assembly_trace": dict,      # stats for Trace writing
        }

    Raises:
        ExternalContextBuildError: If the underlying context assembly fails.
        ValueError: If context_policy is not a recognised value.
    """
    if context_policy not in _VALID_POLICIES:
        raise ValueError(
            f"Unknown context_policy {context_policy!r}. "
            f"Expected one of: {sorted(_VALID_POLICIES)}"
        )

    try:
        from app.services.context_assembler import assemble_context
    except ImportError as exc:
        raise ExternalContextBuildError(
            f"context_assembler unavailable (R9-5-3 dependency): {exc}"
        ) from exc

    project_dict = _to_dict(project)
    run_dict = _to_dict(run)

    try:
        ctx = assemble_context(
            project_id,
            stage,
            project=project_dict,
            run=run_dict,
            include_body=True,
            agent_type="external_platform",
            task_type="delegation",
        )
    except Exception as exc:
        raise ExternalContextBuildError(
            f"assemble_context failed for project {project_id} stage {stage}: {exc}"
        ) from exc

    layers = ctx.get("layers", {})
    skills = ctx.get("skills", [])
    assembly_trace = ctx.get("assembly_trace", {})

    context_text, material_files = _build_text_and_files(
        ctx, layers, skills, stage, task, context_policy, project_dict
    )

    return {
        "context_text": context_text,
        "material_files": material_files,
        "policy": context_policy,
        "assembly_trace": assembly_trace,
    }


def _build_text_and_files(
    ctx: dict,
    layers: dict,
    skills: list,
    stage: str,
    task: str,
    policy: str,
    project: dict | None,
) -> tuple[str, list[str]]:
    """Return (context_text, material_files) for the given policy."""
    lines: list[str] = []
    material_files: list[str] = []

    # ── Task header (all policies) ────────────────────────────────────────
    if task:
        lines.append(f"# Task\n{task}\n")

    # ── Stage header ─────────────────────────────────────────────────────
    lines.append(f"# Current Stage: {stage}\n")

    # ── Project identity (minimal and above) ──────────────────────────────
    if project:
        pname = project.get("name", project.get("project_id", ""))
        if pname:
            lines.append(f"# Project: {pname}\n")

    if policy == POLICY_MINIMAL:
        # minimal: only task + stage + key facts pointer
        c0 = layers.get("C0") or {}
        platform = c0.get("platform_identity", "")
        if platform:
            lines.append(f"## Platform\n{str(platform)[:500]}\n")
        lines.append(
            "## Context\n"
            "Full project context is available in the workspace materials/ directory. "
            "Check materials/ for detailed specifications before making changes.\n"
        )
        return "\n".join(lines), material_files

    # ── Summary / Full: include C0-C6 layers ─────────────────────────────
    for layer_key in ("C0", "C1", "C2", "C3", "C4", "C5", "C6"):
        layer_data = layers.get(layer_key)
        if not layer_data:
            continue
        if policy == POLICY_SUMMARY:
            # Summary: layer header + brief description only (no full text)
            desc = layer_data.get("description") or layer_data.get("summary") or ""
            if isinstance(layer_data, dict) and not desc:
                desc = str(layer_data)[:300]
            lines.append(f"## {layer_key}\n{str(desc)[:400]}\n")
        else:
            # Full: include everything (cap at 4000 chars per layer to avoid bloat)
            if isinstance(layer_data, dict):
                for k, v in layer_data.items():
                    if v:
                        snippet = str(v)[:2000]
                        lines.append(f"### {layer_key}.{k}\n{snippet}\n")
            else:
                lines.append(f"## {layer_key}\n{str(layer_data)[:4000]}\n")

    # ── Skills (full only) ────────────────────────────────────────────────
    if policy == POLICY_FULL:
        for skill in skills[:10]:  # cap at 10 skills to keep context size reasonable
            name = skill.get("name", "")
            body = skill.get("body") or skill.get("description") or ""
            if name:
                lines.append(f"## Skill: {name}\n{str(body)[:1000]}\n")

    # ── Material files (full + summary) ──────────────────────────────────
    if policy in (POLICY_FULL, POLICY_SUMMARY):
        workspace = ctx.get("workspace", {})
        ws_path = workspace.get("path", "")
        if ws_path:
            import os
            materials_dir = os.path.join(ws_path, "materials")
            if os.path.isdir(materials_dir):
                for f in os.listdir(materials_dir):
                    fpath = os.path.join(materials_dir, f)
                    if os.path.isfile(fpath):
                        material_files.append(fpath)

    return "\n".join(lines), material_files


def _to_dict(obj: object | None) -> dict | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    # ORM model or Pydantic schema
    try:
        return obj.model_dump()
    except AttributeError:
        # 预期分支：obj 非 Pydantic 模型（无 model_dump），落到下方 __dict__ 分支。
        # 属类型分派的正常控制流，非错误吞噬，无需发声。
        pass
    try:
        return dict(obj.__dict__)
    except Exception:
        return None
