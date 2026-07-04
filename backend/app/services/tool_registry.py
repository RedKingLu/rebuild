"""Tool Registry — loads tool resources from Registry → generates OpenAI tool schemas
and routes execution via ExecutionProvider (T2.1 + T2.2 / R9-5-4).

Public API:
  load_schemas(tool_refs, stage, db) -> list[dict]   # OpenAI-format tool schemas
  execute_tool(tool_name, args, project_id, db) -> dict

Execution routing by write_scope / binds_via (type_metadata):
  write_scope=none  → local read-only direct execution
  write_scope=workspace → workspace-confined write
  write_scope=system / execute_scope → ExecutionProvider.execute()
  risk_level≥L3    → TODO: R9-5-7 HITL gate接线占位（当前返回 risk_flagged）

Built-in 3 tools (get_project_info/read_artifact/run_profiling) remain available
as seed-driven tools so capability is not lost when Registry is empty.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("rebuild.tool_registry")

# ── Built-in tool fallback (seed-equivalent schemas) ────────────────────────
# These match the hard-coded AGENT_TOOLS in agent_loop.py so the agent always
# has at minimum these three regardless of whether DB seed is populated.
_BUILTIN_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_project_info",
            "description": "获取当前项目的基本信息：阶段、文件数、产物列表",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        "_source": "builtin",
        "_tool_id": "builtin:get_project_info",
        "_risk_level": "L0",
        "_write_scope": "none",
    },
    {
        "type": "function",
        "function": {
            "name": "read_artifact",
            "description": "读取指定阶段产物的内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_path": {
                        "type": "string",
                        "description": "产物文件名，如 intake_report.json",
                    },
                },
                "required": ["artifact_path"],
            },
        },
        "_source": "builtin",
        "_tool_id": "builtin:read_artifact",
        "_risk_level": "L0",
        "_write_scope": "none",
    },
    {
        "type": "function",
        "function": {
            "name": "run_profiling",
            "description": "触发 P1 全量识别（14 项技术栈分析），仅在项目已进入 P1 阶段时可用",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        "_source": "builtin",
        "_tool_id": "builtin:run_profiling",
        "_risk_level": "L1",
        "_write_scope": "workspace",
    },
]

# Risk levels that require gate review (T2.3 / S3 note: full HITL接线→R9-5-7)
_GATE_RISK_THRESHOLD = "L3"
_RISK_ORDER = ["L0", "L1", "L2", "L3", "L4", "L5"]


def load_schemas(
    tool_refs: Optional[list] = None,
    stage: str = "p0",
    db: Optional[Session] = None,
    include_mcp: bool = False,
) -> list[dict]:
    """Return OpenAI-format tool schemas for the agent.

    Resolution order:
    1. If tool_refs provided → load only those specific tool resource_ids
    2. Else → load all enabled active tools from Registry
    3. Merge with built-in schemas (built-ins fill in if Registry has nothing)

    include_mcp: also include tools discovered from connected MCP servers.
    """
    registry_schemas: list[dict] = []

    if db is not None:
        try:
            from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
            q = db.query(ResourceEntry).filter(
                ResourceEntry.resource_type == ResourceType.tool,
                ResourceEntry.enabled == True,
                ResourceEntry.status.in_([ResourceStatus.active, ResourceStatus.read_only]),
            )
            if tool_refs:
                tool_ids = [r if isinstance(r, str) else r.get("tool_id", "") for r in tool_refs]
                tool_ids = [t for t in tool_ids if t and not t.startswith("builtin:")]
                if tool_ids:
                    q = q.filter(ResourceEntry.resource_id.in_(tool_ids))
            tools = q.all()
            for t in tools:
                schema = _build_schema(t)
                if schema:
                    registry_schemas.append(schema)
        except Exception as e:
            logger.warning("tool_registry.load_schemas DB query failed: %s", e)

    # MCP tools injection (T3.4)
    mcp_schemas: list[dict] = []
    if include_mcp and db is not None:
        mcp_schemas = _load_mcp_schemas(db)

    # Merge: registry + MCP + built-ins
    # Avoid duplicates by function name (registry/MCP takes precedence)
    all_schemas = registry_schemas + mcp_schemas
    seen_names = {s["function"]["name"] for s in all_schemas}
    for builtin in _BUILTIN_SCHEMAS:
        if builtin["function"]["name"] not in seen_names:
            all_schemas.append(builtin)

    return all_schemas


def _build_schema(entry) -> Optional[dict]:
    """Build an OpenAI-format tool schema from a ResourceEntry."""
    meta = entry.type_metadata or {}
    input_contract = entry.input_contract or ""

    # Generate parameters from input_contract (JSON schema string) or type_metadata
    params = meta.get("parameters") or meta.get("input_schema")
    if not params and input_contract:
        try:
            import json
            params = json.loads(input_contract)
        except Exception:
            params = None
    if not params:
        params = {"type": "object", "properties": {}, "required": []}

    tool_name = meta.get("tool_name") or entry.name.lower().replace(" ", "_").replace("-", "_")

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": entry.description or entry.name,
            "parameters": params,
        },
        "_source": "registry",
        "_tool_id": entry.resource_id,
        "_risk_level": entry.risk_level.value if hasattr(entry.risk_level, "value") else "L0",
        "_write_scope": meta.get("write_scope", "none"),
        "_binds_via": meta.get("binds_via", ""),
    }


def _load_mcp_schemas(db: Session) -> list[dict]:
    """Load tool schemas from connected MCP servers."""
    try:
        from app.services.mcp_service import MCPService
        svc = MCPService(db)
        servers, _total = svc.list_all()
        schemas = []
        for srv in servers:
            if srv.status != "connected" or not srv.tools:
                continue
            for tool in srv.tools:
                tool_name = f"mcp__{srv.name.lower().replace('-', '_')}__{tool.get('name', '')}"
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"[MCP:{srv.name}] {tool.get('description', '')}",
                        "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}, "required": []},
                    },
                    "_source": "mcp",
                    "_mcp_id": srv.mcp_id,
                    "_mcp_tool_name": tool.get("name", ""),
                    "_risk_level": "L1",
                    "_write_scope": "mcp",
                })
        return schemas
    except Exception as e:
        logger.warning("_load_mcp_schemas failed: %s", e)
        return []


async def execute_tool(
    tool_name: str,
    args: dict,
    project_id: str,
    stage: str = "p0",
    db: Optional[Session] = None,
    tracer=None,
) -> dict:
    """Execute a tool by name, routing via write_scope / binds_via.

    Routing:
    - builtin:*     → internal dispatch (get_project_info / read_artifact / run_profiling)
    - write_scope=mcp → MCP call_tool()
    - write_scope=none → local read (workspace/source)
    - write_scope=workspace → workspace-confined write
    - write_scope=execute → ExecutionProvider.execute()
    - risk_level≥L3 → risk_flagged (TODO: R9-5-7 HITL接线)

    Writes Trace on every call (公理3 / T2.3).
    """
    # ── Resolve tool entry ────────────────────────────────────────────────
    schema = None
    tool_entry = None
    if db is not None:
        try:
            # Match by tool_name in type_metadata OR by name slug
            from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
            tools = db.query(ResourceEntry).filter(
                ResourceEntry.resource_type == ResourceType.tool,
                ResourceEntry.enabled == True,
            ).all()
            for t in tools:
                meta = t.type_metadata or {}
                slug = meta.get("tool_name") or t.name.lower().replace(" ", "_").replace("-", "_")
                if slug == tool_name:
                    tool_entry = t
                    break
        except Exception:
            pass

    # MCP dispatch
    if tool_name.startswith("mcp__"):
        return await _execute_mcp_tool(tool_name, args, db)

    # Built-in dispatch
    if tool_entry is None:
        return await _execute_builtin(tool_name, args, project_id, stage)

    # Risk gate check (T2.3 / S3: full HITL→R9-5-7, current: flag+trace)
    risk = tool_entry.risk_level.value if hasattr(tool_entry.risk_level, "value") else "L0"
    if _RISK_ORDER.index(risk) >= _RISK_ORDER.index(_GATE_RISK_THRESHOLD):
        result = {
            "status": "risk_flagged",
            "risk_level": risk,
            "tool_name": tool_name,
            "message": f"工具风险等级 {risk} ≥ {_GATE_RISK_THRESHOLD}，需人工审核（TODO: R9-5-7 HITL接线）",
        }
        _write_trace(tracer, project_id, tool_name, args, result)
        return result

    meta = tool_entry.type_metadata or {}
    write_scope = meta.get("write_scope", "none")

    # Execute by scope
    if write_scope in ("none",):
        result = await _execute_read(tool_name, args, project_id)
    elif write_scope in ("workspace",):
        result = await _execute_workspace_write(tool_name, args, project_id, tool_entry)
    elif write_scope in ("execute", "system"):
        result = await _execute_via_provider(tool_name, args, project_id, tool_entry)
    else:
        result = await _execute_builtin(tool_name, args, project_id, stage)

    _write_trace(tracer, project_id, tool_name, args, result)
    return result


# ── Internal dispatch helpers ─────────────────────────────────────────────────

async def _execute_builtin(tool_name: str, args: dict, project_id: str, stage: str) -> dict:
    """Handle the three built-in tool implementations."""
    if tool_name == "get_project_info":
        from app.services import workspace_service
        ws = workspace_service.workspace_path(project_id)
        art_dir = ws / "artifacts"
        artifacts = [p.name for p in sorted(art_dir.iterdir()) if p.is_file()] if art_dir.exists() else []
        return {"project_id": project_id, "current_stage": stage,
                "artifacts": artifacts, "source": "tool_registry_builtin"}
    elif tool_name == "read_artifact":
        path = args.get("artifact_path", "")
        try:
            from app.services.workspace_service import workspace_path
            full = workspace_path(project_id) / "artifacts" / path
            if full.exists():
                return {"artifact_path": path, "content": full.read_text("utf-8", errors="replace")[:4000]}
            return {"artifact_path": path, "error": "文件不存在"}
        except Exception as e:
            return {"artifact_path": path, "error": str(e)}
    elif tool_name == "run_profiling":
        if stage not in ("p1", "p2", "p3", "p4", "p5", "p6"):
            return {"error": f"P1 全量识别仅在 P1 及之后阶段可用，当前: {stage}"}
        try:
            from app.dependencies import get_services
            from app.services.full_stack_profiler import FullStackProfiler
            svc = get_services()
            profiler = FullStackProfiler(trace_writer=svc.trace_writer, audit_writer=svc.audit_writer)
            result = profiler.profile(project_id)
            return {"status": "completed", "items_completed": result.get("items_completed", 0)}
        except Exception as e:
            return {"error": f"全量识别执行失败: {e}"}
    return {"error": f"未知内置工具: {tool_name}"}


async def _execute_mcp_tool(tool_name: str, args: dict, db: Optional[Session]) -> dict:
    """Dispatch an mcp__server__tool call."""
    # tool_name format: mcp__{server_slug}__{tool}
    parts = tool_name.split("__", 2)
    if len(parts) < 3:
        return {"error": f"无法解析 MCP 工具名: {tool_name}"}
    _prefix, server_slug, mcp_tool_name = parts
    if db is None:
        return {"error": "DB not available for MCP dispatch"}
    try:
        from app.services.mcp_service import MCPService
        svc = MCPService(db)
        servers, _total = svc.list_all()
        for srv in servers:
            slug = srv.name.lower().replace("-", "_")
            if slug == server_slug and srv.status == "connected":
                result = await svc.call_tool(srv.mcp_id, mcp_tool_name, args)
                return result
        return {"error": f"MCP server '{server_slug}' 未找到或未连接"}
    except Exception as e:
        return {"error": f"MCP dispatch failed: {e}"}


async def _execute_read(tool_name: str, args: dict, project_id: str) -> dict:
    """Local read-only tool execution within workspace (write_scope=none).

    B-TOOL-SCHEMA-1 (R11-3): dispatch by tool so the L1 read tools are genuinely
    usable with their real params — fs_read reads a file, list_files lists a dir,
    code_grep searches. All confined to the project workspace.
    """
    from app.services.workspace_service import workspace_path
    ws = workspace_path(project_id)
    ws_root = ws.resolve()

    def _confine(rel: str):
        full = (ws / rel).resolve()
        if not str(full).startswith(str(ws_root)):
            return None
        return full

    # ── list_files: list a directory ──
    if tool_name == "list_files":
        rel = args.get("path") or args.get("dir") or "source"
        full = _confine(rel)
        if full is None:
            return {"error": "路径越界: 仅允许访问项目 workspace 内目录"}
        if not full.exists() or not full.is_dir():
            return {"error": f"目录不存在: {rel}"}
        entries = []
        for p in sorted(full.iterdir())[:500]:
            entries.append({"name": p.name, "type": "dir" if p.is_dir() else "file"})
        return {"path": rel, "count": len(entries), "entries": entries}

    # ── code_grep: search a pattern under a directory ──
    if tool_name == "code_grep":
        import re
        pattern = args.get("pattern") or args.get("query", "")
        if not pattern:
            return {"error": "需要 pattern 参数"}
        rel = args.get("path") or "source"
        base = _confine(rel)
        if base is None:
            return {"error": "路径越界: 仅允许检索 workspace 内目录"}
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return {"error": f"无效正则: {e}"}
        skip = {".git", "node_modules", "__pycache__", ".venv"}
        matches = []
        scanned = 0
        for p in base.rglob("*"):
            if not p.is_file() or any(s in p.parts for s in skip):
                continue
            scanned += 1
            if scanned > 5000:
                break
            try:
                for i, line in enumerate(p.read_text("utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        matches.append({"file": str(p.relative_to(ws_root)), "line": i, "text": line.strip()[:200]})
                        if len(matches) >= 100:
                            break
            except Exception:
                continue
            if len(matches) >= 100:
                break
        return {"pattern": pattern, "path": rel, "match_count": len(matches),
                "matches": matches, "truncated": len(matches) >= 100}

    # ── fs_read (default read-only): read a file's content ──
    target = args.get("path") or args.get("file") or args.get("artifact_path", "")
    if not target:
        return {"error": "需要 path/file/artifact_path 参数"}
    full = _confine(target)
    if full is None:
        return {"error": "路径越界: 仅允许访问项目 workspace 内文件"}
    if full.exists() and full.is_file():
        return {"content": full.read_text("utf-8", errors="replace")[:4000], "path": target}
    return {"error": f"文件不存在: {target}"}


async def _execute_workspace_write(tool_name: str, args: dict, project_id: str, entry) -> dict:
    """Workspace-confined write operation."""
    return {
        "status": "workspace_write_stub",
        "tool_name": tool_name,
        "note": "R9-5-4: workspace write routing stub — real execution via ExecutionProvider in R9-5-6",
    }


async def _execute_via_provider(tool_name: str, args: dict, project_id: str, entry) -> dict:
    """Route through ExecutionProvider for execute-scope tools."""
    code = args.get("code") or args.get("command", "")
    if not code:
        return {"error": "execute 类工具需要 code 或 command 参数"}
    try:
        from app.dependencies import get_services
        svc = get_services()
        from app.services.workspace_service import workspace_path
        ws = str(workspace_path(project_id))
        result = await svc.execution_provider.execute(code, language="bash", timeout=30, cwd=ws)
        return result
    except Exception as e:
        return {"error": f"ExecutionProvider failed: {e}"}


def _write_trace(tracer, project_id: str, tool_name: str, args: dict, result: dict) -> None:
    if tracer is None:
        return
    try:
        summary = result.get("status") or result.get("error") or "ok"
        tracer.write(
            project_id=project_id,
            trace_type="tool_call",
            summary=f"{tool_name}: {str(summary)[:100]}",
            detail={"tool_name": tool_name, "args_keys": list(args.keys()), "result_keys": list(result.keys())},
        )
    except Exception:
        pass
