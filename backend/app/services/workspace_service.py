"""Workspace service — real file-system backed workspace operations.

R8: Replaces mock file_index with real directory scanning.
Provides file read/write with path-boundary protection (_guard).

Read-only: source/, patches/
User-writable: materials/, reports/, .rebuild/user-notes/
Platform-writable: artifacts/, evidence/, logs/, runs/, .rebuild/sessions/...
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.schemas.workspace import WorkspaceAggregateResponse, GraphStatus, FileIndex
from app.schemas.project import ProjectResponse
from app.schemas.common import Meta

# Subdirectories created on project workspace init (architecture §3.2)
WORKSPACE_SUBDIRS = [
    "source",
    "materials",
    "artifacts",
    "evidence",
    "patches",
    "runs",
    "logs",
    "reports",
    "indexes",
    "output_code",   # R9-5-5 D-088②: external platform write target (WorkspaceMediator)
    ".rebuild/sessions",
    ".rebuild/user-notes",
]

# Writable by user (file editing via frontend)
USER_WRITABLE_DIRS = {"materials", "reports", ".rebuild/user-notes"}

# Writable by platform (agent/system)
PLATFORM_WRITABLE_DIRS = {
    "artifacts", "evidence", "patches", "logs", "runs",
    ".rebuild", ".rebuild/sessions",
    "output_code",   # R9-5-5: external platform output goes here
}

# Read-only for users (source code, patches viewing)
READONLY_DIRS = {"source", "patches"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_root() -> Path:
    return Path(settings.workspace_dir).resolve()


def _graph_cap() -> str:
    """Real orchestration capability probe (R9-5-1 阶段D); 'degraded' if graph unavailable."""
    try:
        from app.graph.runtime import graph_capability_probe
        return graph_capability_probe()
    except Exception:
        return "degraded"


def workspace_path(project_id: str) -> Path:
    """Resolve the absolute workspace directory for a project."""
    return _workspace_root() / "projects" / project_id


def init_workspace(project_id: str) -> Path:
    """Create per-project workspace directory structure. Idempotent."""
    ws = workspace_path(project_id)
    for sub in WORKSPACE_SUBDIRS:
        (ws / sub).mkdir(parents=True, exist_ok=True)
    # Write workspace metadata
    meta_dir = ws / ".rebuild"
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_file = meta_dir / "workspace.json"
    if not meta_file.exists():
        meta_file.write_text(json.dumps({
            "project_id": project_id,
            "created_at": _now(),
            "updated_at": _now(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    # R8: Environment Profile (D-051 三对象之一) — declarative env metadata.
    init_environment(project_id)
    return ws


# ── Execution mode single source (R9-5-7 T6/T7, D-086④ / 公理6) ───────────
# workspace.json execution_mode is the ONE control source. The top-bar
# ExecModeSwitch (PUT /mode) and the onboarding wizard finalize both write
# through here so "选了即生效" with no second source (no Project.execution_mode).

VALID_EXECUTION_MODES = {"manual", "plan", "auto"}


def set_execution_mode(project_id: str, mode: str) -> str:
    """Persist execution_mode into workspace.json. Returns the stored mode.

    Idempotent; creates .rebuild/workspace.json if missing. Invalid modes fall
    back to the existing/default value rather than corrupting the source.
    """
    ws = workspace_path(project_id)
    meta_dir = ws / ".rebuild"
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_file = meta_dir / "workspace.json"
    meta: dict = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    new_mode = mode if mode in VALID_EXECUTION_MODES else meta.get("execution_mode", "plan")
    meta["execution_mode"] = new_mode
    meta["execution_mode_updated_at"] = _now()
    meta.setdefault("project_id", project_id)
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_mode


def get_execution_mode(project_id: str) -> str:
    """Read execution_mode from workspace.json (the single control source)."""
    meta_file = workspace_path(project_id) / ".rebuild" / "workspace.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8")).get("execution_mode", "plan")
        except Exception:
            pass
    return "plan"


# ── Environment Profile (D-051) ─────────────────────────────────────────
# Declarative environment for the project workspace, distinct from workspace.json
# (which is bare workspace bookkeeping). Backs onboarding wizard step 1 (R9, §25).

def _environment_file(project_id: str) -> Path:
    return workspace_path(project_id) / ".rebuild" / "environment.json"


def init_environment(project_id: str) -> dict:
    """Create a default Environment Profile if absent. Idempotent. Returns the profile."""
    f = _environment_file(project_id)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    profile = {
        "project_id": project_id,
        "status": "unknown",          # unknown | declared | ready
        "env_kind": "local",          # local | remote
        "language_hint": None,
        "framework_hint": None,
        "source": "default",          # default | user | auto
        "created_at": _now(),
        "updated_at": _now(),
    }
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def read_environment(project_id: str) -> dict:
    """Read the Environment Profile, creating a default one if missing."""
    f = _environment_file(project_id)
    if not f.exists():
        return init_environment(project_id)
    return json.loads(f.read_text(encoding="utf-8"))


ENV_WRITABLE_FIELDS = {"status", "env_kind", "language_hint", "framework_hint", "source"}
ENV_STATUS_VALUES = {"unknown", "declared", "ready"}
ENV_KIND_VALUES = {"local", "remote"}


def update_environment(project_id: str, updates: dict) -> dict:
    """Update declarative fields of the Environment Profile. Validates enums."""
    profile = read_environment(project_id)
    if "status" in updates and updates["status"] not in ENV_STATUS_VALUES:
        raise ValueError(f"无效 status：{updates['status']}（允许 {sorted(ENV_STATUS_VALUES)}）")
    if "env_kind" in updates and updates["env_kind"] not in ENV_KIND_VALUES:
        raise ValueError(f"无效 env_kind：{updates['env_kind']}（允许 {sorted(ENV_KIND_VALUES)}）")
    for k, v in updates.items():
        if k in ENV_WRITABLE_FIELDS:
            profile[k] = v
    profile["updated_at"] = _now()
    _environment_file(project_id).write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def _guard(project_id: str, target: Path) -> Path:
    """Resolve target path and enforce it stays within project workspace.

    Returns resolved Path on success, raises PermissionError on boundary violation.
    R9-3A: Uses os.path.commonpath for robust prefix matching (fixes D-P2-3:
    projects/p1 was a prefix match for projects/p10).
    """
    import os
    ws = workspace_path(project_id).resolve()
    resolved = (ws / target).resolve()
    try:
        common = os.path.commonpath([str(ws), str(resolved)])
    except ValueError:
        raise PermissionError(f"路径越界：无法解析路径关系")
    if common != str(ws):
        raise PermissionError(f"路径越界：不允许访问 workspace 之外的文件")
    return resolved


def _is_writable_by_user(rel_path: str) -> bool:
    """Check if a relative path falls within user-writable directories."""
    parts = Path(rel_path).parts
    if not parts:
        return False
    first = parts[0]
    if first in USER_WRITABLE_DIRS:
        return True
    # Check .rebuild/user-notes/ prefix
    if first == ".rebuild" and len(parts) > 1:
        return f".rebuild/{parts[1]}" in USER_WRITABLE_DIRS
    return False


def _is_writable_by_platform(rel_path: str) -> bool:
    """Check if a relative path falls within platform-writable directories."""
    parts = Path(rel_path).parts
    if not parts:
        return False
    first = parts[0]
    if first in PLATFORM_WRITABLE_DIRS:
        return True
    if first == ".rebuild":
        return True  # platform can write to .rebuild/*
    return False


def _is_readonly(rel_path: str) -> bool:
    """Check if a relative path is in a read-only directory."""
    parts = Path(rel_path).parts
    if not parts:
        return False
    return parts[0] in READONLY_DIRS


# ── File tree scanning (replaces _mock_file_index) ──────────────────────

def scan_file_tree(project_id: str) -> list[FileIndex]:
    """Scan the real workspace directory and return a FileIndex tree.

    Code view: source/, artifacts/, patches/ (three-root model).
    """
    ws = workspace_path(project_id)

    def walk(base: Path, editable: bool) -> list[dict]:
        nodes: list[dict] = []
        if not base.exists():
            return nodes
        try:
            items = sorted(base.iterdir())
        except PermissionError:
            return nodes
        for item in items:
            rel = str(item.relative_to(ws))
            if item.name.startswith('.') and item.name != '.rebuild':
                continue  # skip hidden files except .rebuild
            if item.is_dir():
                children = walk(item, editable)
                nodes.append({
                    "name": item.name, "path": rel, "type": "dir",
                    "editable": editable, "children": children,
                })
            else:
                nodes.append({
                    "name": item.name, "path": rel, "type": "file",
                    "editable": editable,
                    "bytes": item.stat().st_size if item.exists() else 0,
                })
        return nodes

    roots = [
        FileIndex(key="source", label="源代码（只读）", editable=False,
                   children=walk(ws / "source", False)),
        FileIndex(key="artifacts", label="产出物（只读）", editable=False,
                   children=walk(ws / "artifacts", False)),
        FileIndex(key="patches", label="变更补丁（只读）", editable=False,
                   children=walk(ws / "patches", False)),
        # R9-5-8 T9: 产出代码 root (D-088② external-platform/P4 write target).
        # Platform-writable; user-editable view deferred to R11 (avoid P4 write race).
        FileIndex(key="output_code", label="产出代码", editable=False,
                   children=walk(ws / "output_code", False)),
    ]
    return roots


def scan_material_tree(project_id: str) -> list[FileIndex]:
    """Scan material-view directories and return FileIndex tree.

    Material view: materials/, artifacts/, evidence/, reports/, runs/, logs/.
    """
    ws = workspace_path(project_id)
    material_dirs = [
        ("materials", "材料", False),
        ("artifacts", "产物", False),
        ("evidence", "证据", False),
        ("reports", "报告", True),   # writable by user
        ("runs", "运行记录", False),
        ("logs", "执行日志", False),
    ]

    def walk(base: Path, editable: bool) -> list[dict]:
        nodes: list[dict] = []
        if not base.exists():
            return nodes
        try:
            items = sorted(base.iterdir())
        except PermissionError:
            return nodes
        for item in items:
            rel = str(item.relative_to(ws))
            if item.name.startswith('.'):
                continue
            if item.is_dir():
                children = walk(item, editable)
                nodes.append({
                    "name": item.name, "path": rel, "type": "dir",
                    "editable": editable, "children": children,
                })
            else:
                nodes.append({
                    "name": item.name, "path": rel, "type": "file",
                    "editable": editable,
                    "bytes": item.stat().st_size if item.exists() else 0,
                })
        return nodes

    roots = []
    for key, label, editable in material_dirs:
        roots.append(FileIndex(
            key=key, label=label, editable=editable,
            children=walk(ws / key, editable),
        ))
    return roots


def read_file(project_id: str, rel_path: str) -> dict:
    """Read a file within the project workspace. Returns {path, content, editable, readonly_reason}."""
    target = _guard(project_id, Path(rel_path))
    if not target.exists():
        raise FileNotFoundError(f"文件不存在：{rel_path}")
    if target.is_dir():
        raise IsADirectoryError(f"路径是目录：{rel_path}")
    content = target.read_text(encoding="utf-8", errors="replace")
    return {
        "path": rel_path,
        "content": content,
        "bytes": len(content.encode("utf-8")),
        "editable": _is_writable_by_user(rel_path),
        "readonly": _is_readonly(rel_path),
        "readonly_reason": "源码目录始终只读；P4 产出写入 output_code/（D-099）" if _is_readonly(rel_path) else None,
    }


def write_file(project_id: str, rel_path: str, content: str) -> dict:
    """Write a file within the project workspace. Enforces write-boundary policy.

    Returns {path, bytes, saved_at}.
    Raises PermissionError if path is not user-writable or out of bounds.
    """
    target = _guard(project_id, Path(rel_path))

    # Enforce write policy
    if not _is_writable_by_user(rel_path):
        if _is_readonly(rel_path):
            raise PermissionError(
                f"源码目录为只读：{rel_path}。源码目录始终只读；P4 产出写入 output_code/（D-099）。"
            )
        raise PermissionError(
            f"不允许写入此路径：{rel_path}。仅 materials/、reports/、.rebuild/user-notes/ 支持用户编辑。"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "path": rel_path,
        "bytes": len(content.encode("utf-8")),
        "saved_at": _now(),
    }


def append_rework_notes(project_id: str, stage: str, decision: str, reason: str) -> str:
    """UX-5: append a rejection / request_changes reason to artifacts/{stage}_rework_notes.json.

    Platform-only write (artifacts/ is platform-writable, D-099/D-104). Stores an append-only
    list of {round, decision, reason, at} so the rework (Acceptance) agent can see exactly why
    the prior attempt was rejected and what to fix — reasons never get lost. Returns the rel path.
    """
    rel = f"artifacts/{stage}_rework_notes.json"
    target = _guard(project_id, Path(rel))
    if not _is_writable_by_platform(rel):
        raise PermissionError(f"artifacts/ 不可写：{rel}")
    notes: list[dict] = []
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, list):
                notes = data
        except Exception:
            notes = []
    notes.append({
        "round": len(notes) + 1,
        "decision": decision,
        "reason": reason,
        "at": _now(),
    })
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return rel


# ── Workspace aggregate (updated for real data) ─────────────────────────

class WorkspaceService:
    def __init__(self, services):
        self._svc = services

    def aggregate(self, project_id: str, include: Optional[str] = None) -> WorkspaceAggregateResponse:
        svc = self._svc

        project = svc.project_service.get(project_id)
        active_run = None
        stage_statuses = {}
        active_gate = None
        pending_gates = []
        recent_artifacts = []
        pending_evidence_gaps = []
        recent_traces = []
        recent_audits = []

        if project and project.current_run_id:
            active_run = svc.run_service.get(project.current_run_id)
            if active_run:
                stage_statuses = active_run.stage_status

        active_gate = svc.gate_service.get_active(project_id)
        pending_gates = svc.gate_service.list_by_project(project_id)

        recent_artifacts = svc.aet_service.list_artifacts(project_id=project_id)[:10]
        pending_evidence_gaps = svc.aet_service.list_evidence_gaps(project_id=project_id)
        recent_traces = svc.aet_service.list_traces(project_id=project_id, limit=20)
        recent_audits = svc.aet_service.list_audits(project_id=project_id, limit=20)

        # R8: Real file tree scanning (replaces _mock_file_index)
        try:
            file_index = scan_file_tree(project_id)
        except Exception:
            file_index = []

        try:
            material_index = scan_material_tree(project_id)
        except Exception:
            material_index = []

        # R9-5-8 T1/T11/T12: real state probes — no hardcoded "real"/"available".
        from app.services import capability_probe
        _src_type = project.source_type.value if hasattr(project.source_type, 'value') else str(project.source_type)
        _ws_status = project.workspace_status or "ready"
        _source_status = capability_probe.probe_source_status(project.project_id, _src_type, _ws_status)
        _capability_status = capability_probe.probe_capability_status()

        # Convert project to dict for response (avoid circular import from ProjectService)
        project_dict = {
            "project_id": project.project_id,
            "name": project.name,
            "description": project.description or "",
            "project_status": project.project_status.value if hasattr(project.project_status, 'value') else str(project.project_status),
            "source_type": _src_type,
            "source_config": project.source_config,
            "current_stage": project.current_stage,
            "current_run_id": project.current_run_id,
            "active_gate": project.active_gate,
            "evidence_gap_count": project.evidence_gap_count,
            "workspace_status": _ws_status,
            "onboarding_done": project.onboarding_done,
            "created_at": project.created_at.isoformat() if project.created_at else "",
            "updated_at": project.updated_at.isoformat() if project.updated_at else "",
            "source_status": _source_status,
            "capability_status": _capability_status,
        }

        return WorkspaceAggregateResponse(
            project=ProjectResponse(**project_dict) if project else None,
            active_run=active_run,
            stage_statuses=stage_statuses,
            active_gate=active_gate,
            pending_gates=pending_gates,
            recent_artifacts=recent_artifacts,
            pending_evidence_gaps=pending_evidence_gaps,
            recent_traces=recent_traces,
            recent_audits=recent_audits,
            file_index=file_index,
            material_index=material_index,
            graph_status=GraphStatus(
                graph_capability_status=_graph_cap(),
                transition_mode="langgraph",
            ),
            meta=Meta(),
        )
