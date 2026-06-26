"""MCP Service — real MCP server management with stdio/SSE connectivity (Phase 12)."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.mcp_server import MCPServer

logger = logging.getLogger("rebuild.mcp_service")


class MCPService:
    def __init__(self, db: Session):
        self.db = db

    # ── CRUD ─────────────────────────────────────────────────────────

    def create(self, data: dict) -> MCPServer:
        srv = MCPServer(
            name=data["name"],
            description=data.get("description", ""),
            transport=data.get("transport", "stdio"),
            command=data.get("command"),
            args=data.get("args"),
            env_vars=data.get("env_vars"),
            sse_url=data.get("sse_url"),
            origin=data.get("origin", "user_created"),
        )
        self.db.add(srv)
        self.db.commit()
        self.db.refresh(srv)
        return srv

    def get(self, mcp_id: str) -> MCPServer | None:
        return self.db.get(MCPServer, mcp_id)

    def list_all(self, limit: int = 100, offset: int = 0) -> tuple[list[MCPServer], int]:
        q = self.db.query(MCPServer)
        total = q.count()
        records = q.order_by(MCPServer.name).offset(offset).limit(limit).all()
        return records, total

    def update(self, mcp_id: str, data: dict) -> MCPServer | None:
        srv = self.get(mcp_id)
        if not srv:
            return None
        for key in ("name", "description", "transport", "command", "args",
                     "env_vars", "sse_url", "enabled"):
            if key in data and data[key] is not None:
                setattr(srv, key, data[key])
        srv.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(srv)
        return srv

    def delete(self, mcp_id: str) -> bool:
        srv = self.get(mcp_id)
        if not srv:
            return False
        self.db.delete(srv)
        self.db.commit()
        return True

    # ── Connection test (real MCP subprocess for stdio) ─────────────

    async def test_connection(self, mcp_id: str) -> dict:
        """Test MCP connection: spawn stdio subprocess or check SSE endpoint."""
        srv = self.get(mcp_id)
        if not srv:
            return {"status": "error", "error": "MCP server not found"}

        srv.status = "connecting"
        srv.error_message = None
        self.db.commit()

        try:
            if srv.transport == "stdio":
                result = await self._test_stdio(srv)
            else:
                result = await self._test_sse(srv)

            srv.status = result.get("status", "error")
            srv.error_message = result.get("error")
            srv.last_checked_at = datetime.now(timezone.utc)
            if result.get("tools"):
                srv.tools = result["tools"]
            self.db.commit()
            return result
        except Exception as e:
            srv.status = "error"
            srv.error_message = str(e)
            srv.last_checked_at = datetime.now(timezone.utc)
            self.db.commit()
            return {"status": "error", "error": str(e)}

    async def _test_stdio(self, srv: MCPServer) -> dict:
        """Spawn MCP stdio subprocess, send initialize + tools/list via line-delimited JSON."""
        if not srv.command:
            return {"status": "error", "error": "command not configured"}

        cmd = [srv.command] + (srv.args or [])
        env = os.environ.copy()
        if srv.env_vars:
            env.update(srv.env_vars)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            # Send initialize
            init_msg = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "rebuild", "version": "V26.1.1"},
                },
            }) + "\n"

            try:
                proc.stdin.write(init_msg.encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as e:
                stderr = (await proc.stderr.read()).decode()[:500] if proc.stderr else ""
                return {"status": "error", "error": f"Process exited early: {e}. stderr: {stderr}"}

            # Read response line by line with timeout
            tools = []
            connected = False
            try:
                for _ in range(20):  # Read up to 20 lines
                    line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=20.0)
                    if not line_bytes:
                        break
                    line = line_bytes.decode().strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("result") and msg.get("id") == 1:
                        connected = True
                        # Send initialized notification
                        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
                        proc.stdin.write(notif.encode())
                        await proc.stdin.drain()
                        # Send tools/list
                        tools_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
                        proc.stdin.write(tools_req.encode())
                        await proc.stdin.drain()
                    elif msg.get("result") and msg.get("id") == 2:
                        for t in msg["result"].get("tools", []):
                            tools.append({
                                "name": t.get("name", ""),
                                "description": t.get("description", ""),
                                "inputSchema": t.get("inputSchema", {}),
                            })
                    elif msg.get("error"):
                        return {"status": "error",
                                "error": f"MCP error: {msg['error'].get('message', str(msg['error']))}"}
                    if tools:
                        break  # Got tools, done
            except asyncio.TimeoutError:
                pass  # Partial response is OK

            # Kill process gracefully
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

            if connected:
                return {"status": "connected", "tools": tools, "error": None}
            else:
                stderr_text = (await proc.stderr.read()).decode()[:500] if proc.stderr else ""
                # Check for common issues
                if "npm error" in stderr_text.lower() or "ENOENT" in stderr_text:
                    return {"status": "error", "error": stderr_text[:300]}
                if not stderr_text:
                    return {"status": "error", "error": "No JSON-RPC response from MCP server. Check command and args."}
                return {"status": "error", "error": stderr_text[:300]}

        except FileNotFoundError:
            return {"status": "error", "error": f"Command not found: {srv.command}"}
        except Exception as e:
            return {"status": "error", "error": str(e)[:300]}

    async def _test_sse(self, srv: MCPServer) -> dict:
        """Test SSE MCP endpoint."""
        if not srv.sse_url:
            return {"status": "error", "error": "SSE URL not configured"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(srv.sse_url)
                if resp.status_code == 200:
                    return {"status": "connected", "tools": [], "error": None}
                return {"status": "error", "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "error": str(e)[:300]}

    @staticmethod
    def to_response(srv: MCPServer) -> dict:
        return {
            "mcp_id": srv.mcp_id,
            "name": srv.name,
            "description": srv.description,
            "transport": srv.transport,
            "command": srv.command,
            "args": srv.args,
            "env_vars": srv.env_vars,
            "sse_url": srv.sse_url,
            "status": srv.status,
            "last_checked_at": srv.last_checked_at.isoformat() if srv.last_checked_at else None,
            "error_message": srv.error_message,
            "tools": srv.tools,
            "origin": srv.origin,
            "enabled": srv.enabled,
            "created_at": srv.created_at.isoformat() if srv.created_at else None,
            "updated_at": srv.updated_at.isoformat() if srv.updated_at else None,
        }
