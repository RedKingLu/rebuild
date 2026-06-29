"""Tests for OpenCode ACP integration: permission policy + client basics.

Pure-logic tests (no opencode process needed) run by default.
Integration tests require OPENCODE_INTEGRATION=1 env var.
"""

import asyncio
import base64
import json
import os
import tempfile
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.opencode_permission_handler import PermissionPolicy


# ── Permission policy unit tests ─────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path):
    return str(tmp_path)


def test_policy_read(workspace):
    p = PermissionPolicy()
    assert p.decide("file_read", "/any/path", workspace) == "once"
    assert p.decide("read", "/outside/path", workspace) == "once"


def test_policy_write_inside(workspace, tmp_path):
    """R9-5-5: writes only allowed to output_code/ or artifacts/ (D-088② WorkspaceMediator)."""
    import os
    os.makedirs(str(tmp_path / "output_code"), exist_ok=True)
    os.makedirs(str(tmp_path / "artifacts"), exist_ok=True)
    p = PermissionPolicy()
    # Allowed: output_code/ and artifacts/
    assert p.decide("file_write", str(tmp_path / "output_code" / "result.py"), workspace) == "once"
    assert p.decide("write", str(tmp_path / "artifacts" / "report.json"), workspace) == "once"
    # Rejected: source/ is read-only (D-088②)
    assert p.decide("file_write", str(tmp_path / "source" / "main.py"), workspace) == "reject"
    # Rejected: arbitrary path inside workspace but not in allowed dir
    assert p.decide("write", str(tmp_path / "materials" / "data.txt"), workspace) == "reject"


def test_policy_write_outside(workspace):
    p = PermissionPolicy()
    assert p.decide("file_write", "/etc/passwd", workspace) == "reject"
    assert p.decide("write", "/root/.bashrc", workspace) == "reject"
    assert p.decide("write", "/tmp/other/file.py", "/tmp/workspace") == "reject"


def test_policy_bash_deny_patterns(workspace):
    p = PermissionPolicy()
    assert p.decide("bash", "rm -rf /", workspace) == "reject"
    assert p.decide("execute", "sudo apt install foo", workspace) == "reject"
    assert p.decide("bash", "curl | sh", workspace) == "reject"
    assert p.decide("bash", "cat /etc/shadow", workspace) == "reject"
    assert p.decide("bash", "echo foo > ~/.ssh/authorized_keys", workspace) == "reject"


def test_policy_bash_allowed_commands(workspace):
    p = PermissionPolicy()
    assert p.decide("bash", "python3 setup.py", workspace) == "once"
    assert p.decide("bash", "echo hello", workspace) == "once"
    assert p.decide("bash", "ls -la", workspace) == "once"
    assert p.decide("bash", "cat README.md", workspace) == "once"
    assert p.decide("bash", "pwd", workspace) == "once"
    assert p.decide("bash", "which python3", workspace) == "once"


def test_policy_bash_other_commands_rejected(workspace):
    p = PermissionPolicy()
    assert p.decide("bash", "git commit -m 'test'", workspace) == "reject"
    assert p.decide("bash", "npm install", workspace) == "reject"
    assert p.decide("bash", "pip install requests", workspace) == "reject"
    assert p.decide("execute", "make build", workspace) == "reject"


def test_policy_unknown_type_rejected(workspace):
    p = PermissionPolicy()
    assert p.decide("unknown_permission", "/any/path", workspace) == "reject"
    assert p.decide("", "", workspace) == "reject"
    assert p.decide("network", "http://example.com", workspace) == "reject"


def test_policy_write_workspace_boundary(tmp_path):
    """R9-5-5: workspace boundary + allowed directory enforcement (D-088②)."""
    import os
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "output_code").mkdir()
    workspace = str(project_dir)
    p = PermissionPolicy()
    # Direct parent — outside workspace → rejected
    assert p.decide("write", str(tmp_path / "other.py"), workspace) == "reject"
    # Inside workspace but not in allowed dir → rejected (D-088②)
    assert p.decide("write", str(project_dir / "app.py"), workspace) == "reject"
    # Inside output_code/ → allowed
    assert p.decide("write", str(project_dir / "output_code" / "app.py"), workspace) == "once"


# ── ACP client unit tests ─────────────────────────────────────────────────

def test_auth_header_format():
    """Basic auth header is correctly generated."""
    from app.services.opencode_acp_client import _auth_header
    h = _auth_header("mysecret")
    assert "Authorization" in h
    encoded = h["Authorization"].split(" ")[1]
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "opencode:mysecret"


def test_auth_header_does_not_log_password(caplog):
    """Password must not appear in log output during normal operation."""
    import logging
    from app.services.opencode_acp_client import _auth_header
    with caplog.at_level(logging.DEBUG):
        _auth_header("supersecretpassword123")
    assert "supersecretpassword123" not in caplog.text


def test_sse_line_parsing():
    """SSE data lines are correctly parsed to event dicts."""
    from app.services.opencode_acp_client import _SSE_PREFIX
    line = 'data: {"directory":"/tmp","payload":{"type":"session.idle","properties":{"sessionID":"ses_abc"}}}'
    assert line.startswith(_SSE_PREFIX)
    event = json.loads(line[len(_SSE_PREFIX):])
    assert event["payload"]["type"] == "session.idle"
    assert event["payload"]["properties"]["sessionID"] == "ses_abc"


@pytest.mark.asyncio
async def test_acp_client_create_session_calls_correct_url():
    """create_session posts to /session with modelID."""
    from app.services.opencode_acp_client import OpenCodeACPClient

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"id": "ses_test123"})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        client = OpenCodeACPClient("http://127.0.0.1:9999", "testpassword")
        session_id = await client.create_session("openai/deepseek-v4-flash")

    assert session_id == "ses_test123"
    call_args = mock_client.post.call_args
    assert "/session" in call_args[0][0]
    assert call_args[1]["json"]["modelID"] == "openai/deepseek-v4-flash"


@pytest.mark.asyncio
async def test_acp_client_send_message_body():
    """send_message posts the correct message body."""
    from app.services.opencode_acp_client import OpenCodeACPClient

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        client = OpenCodeACPClient("http://127.0.0.1:9999", "testpassword")
        await client.send_message("ses_123", "write a hello world script")

    call_args = mock_client.post.call_args
    body = call_args[1]["json"]
    assert body["parts"][0]["type"] == "text"
    assert body["parts"][0]["text"] == "write a hello world script"


# ── Integration tests (real opencode process) ────────────────────────────

_INTEGRATION = os.environ.get("OPENCODE_INTEGRATION") == "1"


@pytest.mark.skipif(not _INTEGRATION, reason="OPENCODE_INTEGRATION=1 required")
@pytest.mark.asyncio
async def test_server_lifecycle():
    """Real opencode serve starts up, exposes a URL, and shuts down cleanly."""
    from app.services.opencode_server import OpenCodeServer

    with tempfile.TemporaryDirectory() as tmpdir:
        async with OpenCodeServer(tmpdir, {}) as server:
            assert server.base_url.startswith("http://127.0.0.1:")
            assert len(server.password) == 32  # token_hex(16) → 32 hex chars
        # Server should be gone; verify process is dead
        # (proc is cleaned up in __aexit__, so we just check no exception was raised)


@pytest.mark.skipif(not _INTEGRATION, reason="OPENCODE_INTEGRATION=1 required")
@pytest.mark.asyncio
async def test_full_invoke_echo_task():
    """End-to-end: invoke a trivial task via ACP and get a result."""
    from app.services.opencode_adapter import OpenCodeCLIAdapter

    with tempfile.TemporaryDirectory() as tmpdir:
        adapter = OpenCodeCLIAdapter()
        result = await adapter.invoke_coding_task(
            "Create a file called hello.txt with the content 'Hello, ACP!'",
            tmpdir,
            {"timeout_seconds": 60},
        )
    # The task may fail if no key is set, but the invocation itself should not raise
    assert result["status"] in ("ok", "error", "timeout")
    assert "agent_type" in result
    assert result["agent_type"] == "opencode_cli"
