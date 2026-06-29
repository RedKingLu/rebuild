"""R9-5-6 tests — Execution Isolation & Remote Execution Foundation.

Covers:
  - T3: ExecutionProvider Protocol alignment + ExecutionResult structure
  - T8: _classify_risk() function
  - T2: ContainerExecutionProvider explicit failure (no docker)
  - T4/T5/T6/T9/T10: RemoteSSHExecutionProvider (mocked paramiko)
  - T7: get_execution_provider() three-mode factory
  - T11: test_remote endpoint refactor (no hardcoded root@, uses credential_ref)
  - T12: /remote/{id}/exec + /remote/{id}/transfer API contracts
  - V6: Negative paths (bad host, bad key, blocked commands, credential failure)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ══════════════════════════════════════════════════════════════════
# T8: _classify_risk
# ══════════════════════════════════════════════════════════════════

from app.services.execution_provider import _classify_risk, _check_dangerous


class TestClassifyRisk:
    def test_deny_substring_is_l5(self):
        assert _classify_risk("rm -rf /tmp", "bash") == "L5"
        assert _classify_risk("sudo apt install", "bash") == "L5"

    def test_l4_system_modification(self):
        assert _classify_risk("chmod 777 /srv/app/main.py", "bash") == "L4"
        assert _classify_risk("chown deploy:deploy /srv/app", "bash") == "L4"
        assert _classify_risk("systemctl stop nginx", "bash") == "L4"

    def test_allowed_command_l1(self):
        assert _classify_risk("echo hello", "bash") == "L1"
        assert _classify_risk("ls -la /tmp", "bash") == "L1"
        assert _classify_risk("pwd", "bash") == "L1"

    def test_unknown_shell_command_l3(self):
        assert _classify_risk("npm install", "bash") == "L3"
        assert _classify_risk("git commit -m 'test'", "bash") == "L3"
        assert _classify_risk("make build", "bash") == "L3"

    def test_python_safe_l1(self):
        assert _classify_risk("print('hello')", "python") == "L1"
        assert _classify_risk("x = 1 + 2", "python3") == "L1"

    def test_python_subprocess_l3(self):
        assert _classify_risk("import subprocess; subprocess.run(['ls'])", "python") == "L3"
        assert _classify_risk("os.system('echo hi')", "python3") == "L3"

    def test_case_insensitive(self):
        assert _classify_risk("RM -RF /tmp", "bash") == "L5"
        assert _classify_risk("SUDO apt", "bash") == "L5"


# ══════════════════════════════════════════════════════════════════
# T3: ExecutionProvider result structure
# ══════════════════════════════════════════════════════════════════

class TestLocalProviderResultStructure:
    """V1: LocalSubprocessExecutionProvider result contains all required fields."""

    def test_execute_returns_risk_level(self):
        from app.services.execution_provider import LocalSubprocessExecutionProvider
        provider = LocalSubprocessExecutionProvider()
        result = asyncio.run(provider.execute("echo hi", language="bash"))
        assert "risk_level" in result
        assert "audited" in result
        assert "execution_mode" in result
        assert result["execution_mode"] == "local"

    def test_blocked_contains_risk_l5(self):
        from app.services.execution_provider import LocalSubprocessExecutionProvider
        provider = LocalSubprocessExecutionProvider()
        result = asyncio.run(provider.execute("rm -rf /", language="bash"))
        assert result["blocked"] is True
        assert result["risk_level"] == "L5"


# ══════════════════════════════════════════════════════════════════
# T7: get_execution_provider factory
# ══════════════════════════════════════════════════════════════════

from app.services.execution_provider import get_execution_provider


class TestGetExecutionProviderFactory:
    def test_default_returns_local(self):
        import os
        os.environ.pop("EXECUTION_MODE", None)
        p = get_execution_provider()
        assert p.name == "local_subprocess"

    def test_local_mode_explicit(self):
        p = get_execution_provider(mode="local")
        assert p.name == "local_subprocess"

    def test_container_mode(self):
        p = get_execution_provider(mode="container")
        assert p.name == "container_sandbox"

    def test_remote_mode_requires_host_and_db(self):
        with pytest.raises(ValueError, match="remote_host_id"):
            get_execution_provider(mode="remote")

    def test_remote_mode_requires_db(self):
        with pytest.raises(ValueError, match="remote_host_id"):
            get_execution_provider(mode="remote", remote_host_id="host-1")

    def test_remote_mode_host_not_found_raises(self):
        mock_db = MagicMock()
        with patch("app.services.remote_executor.RemoteSSHExecutionProvider"), \
             patch("app.services.remote_service.RemoteService") as MockSvc:
            MockSvc.return_value.get.return_value = None
            with pytest.raises(ValueError, match="RemoteHost not found"):
                get_execution_provider(mode="remote", remote_host_id="bad-id", db=mock_db)

    def test_env_var_container(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_MODE", "container")
        p = get_execution_provider()
        assert p.name == "container_sandbox"
        monkeypatch.delenv("EXECUTION_MODE")


# ══════════════════════════════════════════════════════════════════
# T2: ContainerExecutionProvider — explicit failure when docker unavailable
# ══════════════════════════════════════════════════════════════════

class TestContainerProviderExplicitFailure:
    """V6-1: ContainerExecutionProvider must fail explicitly, NOT downgrade to local."""

    def test_no_docker_returns_error_not_local(self):
        from app.services.execution_provider import ContainerExecutionProvider
        with patch("docker.from_env", side_effect=Exception("docker not available")):
            result = asyncio.run(ContainerExecutionProvider().execute("print('hi')"))
        # Must NOT silently fall back to local_subprocess
        assert result["provider"] != "local_subprocess", (
            "ContainerExecutionProvider must NOT downgrade to local on docker error (D-090)"
        )
        assert result["exit_code"] == -1
        assert "docker" in result["stderr"].lower() or "container" in result["stderr"].lower()

    def test_blocked_result_structure(self):
        from app.services.execution_provider import ContainerExecutionProvider
        result = asyncio.run(ContainerExecutionProvider().execute("rm -rf /"))
        assert result["blocked"] is True
        assert result["risk_level"] == "L5"
        assert result["execution_mode"] == "container"


# ══════════════════════════════════════════════════════════════════
# T4/T5/T6: RemoteSSHExecutionProvider (mocked paramiko)
# ══════════════════════════════════════════════════════════════════

_CS_PATH = "app.services.credential_service.CredentialService"
_CS_PATH_OLD = _CS_PATH  # alias for backward compat


def _make_host(cred_ref="cred-123", fingerprint=None):
    host = MagicMock()
    host.remote_host_id = "host-abc"
    host.address = "192.168.1.100"
    host.masked_address = "192.168.***.100"
    host.port = 22
    host.credential_ref = cred_ref
    host.host_key_fingerprint = fingerprint
    return host


def _mock_cred_decrypt(plaintext: str):
    """Return a mock CredentialService that decrypts to given plaintext."""
    mock_svc = MagicMock()
    mock_svc.decrypt.return_value = plaintext
    return mock_svc


def _ssh_cred_json(user="deploy", password="s3cr3t"):
    return json.dumps({"user": user, "password": password})


class TestRemoteSSHProviderCredentials:
    def test_no_credential_ref_returns_error(self):
        from app.services.remote_executor import RemoteSSHExecutionProvider
        host = _make_host(cred_ref=None)
        provider = RemoteSSHExecutionProvider(host, MagicMock())
        result = asyncio.run(provider.execute("echo hi"))
        assert result["exit_code"] == -1
        assert "credential" in result["stderr"].lower()

    def test_decrypt_failure_returns_error(self):
        from app.services.remote_executor import RemoteSSHExecutionProvider
        mock_db = MagicMock()
        with patch(_CS_PATH) as MockCS:
            MockCS.return_value.decrypt.return_value = None
            provider = RemoteSSHExecutionProvider(_make_host(), mock_db)
            result = asyncio.run(provider.execute("echo hi"))
        assert result["exit_code"] == -1

    def test_parse_ssh_credential_password(self):
        from app.services.remote_executor import _parse_ssh_credential
        cred = _parse_ssh_credential('{"user": "admin", "password": "s3cr3t"}')
        assert cred.user == "admin"
        assert cred.password == "s3cr3t"
        assert cred.private_key is None

    def test_parse_ssh_credential_private_key(self):
        from app.services.remote_executor import _parse_ssh_credential
        cred = _parse_ssh_credential('{"user": "deploy", "private_key": "-----BEGIN RSA..."}')
        assert cred.user == "deploy"
        assert cred.private_key == "-----BEGIN RSA..."
        assert cred.password is None

    def test_parse_ssh_credential_missing_user_raises(self):
        from app.services.remote_executor import _parse_ssh_credential
        with pytest.raises(ValueError, match="user"):
            _parse_ssh_credential('{"password": "x"}')

    def test_parse_ssh_credential_no_auth_raises(self):
        from app.services.remote_executor import _parse_ssh_credential
        with pytest.raises(ValueError, match="password.*private_key"):
            _parse_ssh_credential('{"user": "x"}')


class TestRemoteSSHProviderRiskGate:
    """V6-4: High-risk commands blocked; L4+ requires confirmation."""

    def _provider_with_mock_cred(self, cred_json=None):
        from app.services.remote_executor import RemoteSSHExecutionProvider
        mock_db = MagicMock()
        with patch(_CS_PATH) as MockCS:
            MockCS.return_value.decrypt.return_value = cred_json or _ssh_cred_json()
            provider = RemoteSSHExecutionProvider(_make_host(), mock_db, mode="plan")
        return provider

    def test_deny_list_always_blocked(self):
        from app.services.remote_executor import RemoteSSHExecutionProvider
        host = _make_host()
        mock_db = MagicMock()
        with patch(_CS_PATH):
            provider = RemoteSSHExecutionProvider(host, mock_db, mode="auto")
        result = asyncio.run(provider.execute("rm -rf /tmp"))
        assert result["blocked"] is True
        assert result["risk_level"] == "L5"

    def test_l4_command_blocked_in_plan_mode(self):
        from app.services.remote_executor import RemoteSSHExecutionProvider
        host = _make_host()
        mock_db = MagicMock()
        with patch(_CS_PATH) as MockCS:
            MockCS.return_value.decrypt.return_value = _ssh_cred_json()
            provider = RemoteSSHExecutionProvider(host, mock_db, mode="plan")
        result = asyncio.run(provider.execute("chmod 777 /srv/app/main.py"))
        assert result["blocked"] is True
        assert result["risk_level"] == "L4"

    def test_echo_command_proceeds_with_mock_ssh(self):
        from app.services.remote_executor import RemoteSSHExecutionProvider
        import paramiko
        host = _make_host()
        mock_db = MagicMock()

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"hello"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

        with patch(_CS_PATH) as MockCS, \
             patch("app.services.remote_executor._get_paramiko") as mock_pm:
            MockCS.return_value.decrypt.return_value = _ssh_cred_json()
            mock_pm_module = MagicMock()
            mock_pm_module.SSHClient.return_value = mock_client
            mock_pm_module.RejectPolicy.return_value = MagicMock()
            mock_pm.return_value = mock_pm_module

            provider = RemoteSSHExecutionProvider(host, mock_db, mode="auto")
            result = asyncio.run(provider.execute("echo hello"))

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello"
        assert result["risk_level"] == "L1"
        assert result["blocked"] is False


class TestRemoteSSHHostKey:
    """T6: Host key fingerprint enforcement."""

    def test_fingerprint_check_policy_mismatch_raises(self):
        import paramiko
        from app.services.remote_executor import _FingerprintCheckPolicy, _compute_host_key_fingerprint

        key = MagicMock()
        key.asbytes.return_value = b"fake-key-bytes"
        actual_fp = _compute_host_key_fingerprint(key)

        policy = _FingerprintCheckPolicy("wrong_fingerprint_000")
        with pytest.raises(paramiko.SSHException):
            policy.missing_host_key(None, "192.168.1.1", key)

    def test_fingerprint_check_policy_match_accepts(self):
        from app.services.remote_executor import _FingerprintCheckPolicy, _compute_host_key_fingerprint

        key = MagicMock()
        key.asbytes.return_value = b"some-key-bytes"
        fp = _compute_host_key_fingerprint(key)

        policy = _FingerprintCheckPolicy(fp)
        # Should not raise
        policy.missing_host_key(None, "192.168.1.1", key)

    def test_compute_fingerprint_is_sha256(self):
        from app.services.remote_executor import _compute_host_key_fingerprint
        key = MagicMock()
        key.asbytes.return_value = b"test-bytes"
        fp = _compute_host_key_fingerprint(key)
        expected = hashlib.sha256(b"test-bytes").hexdigest()
        assert fp == expected


class TestRemoteSSHSanitizeOutput:
    """T10: sensitive data scrubbing from command output."""

    def test_sanitize_password_line(self):
        from app.services.remote_executor import _sanitize_output
        out = "hello\npassword=supersecret123\nworld"
        sanitized = _sanitize_output(out)
        assert "supersecret123" not in sanitized
        assert "REDACTED" in sanitized

    def test_clean_output_unchanged(self):
        from app.services.remote_executor import _sanitize_output
        out = "ls output:\nfoo.py\nbar.py"
        assert _sanitize_output(out) == out


class TestTransferRiskClassification:
    def test_system_path_l5(self):
        from app.services.remote_executor import _classify_transfer_risk
        assert _classify_transfer_risk("put", "/etc/passwd") == "L5"
        assert _classify_transfer_risk("put", "/proc/sys/x") == "L5"

    def test_system_bin_l4(self):
        from app.services.remote_executor import _classify_transfer_risk
        assert _classify_transfer_risk("put", "/usr/local/bin/myapp") == "L4"

    def test_put_regular_path_l3(self):
        from app.services.remote_executor import _classify_transfer_risk
        assert _classify_transfer_risk("put", "/home/deploy/app/main.py") == "L3"

    def test_get_regular_path_l2(self):
        from app.services.remote_executor import _classify_transfer_risk
        assert _classify_transfer_risk("get", "/home/deploy/app/result.txt") == "L2"


# ══════════════════════════════════════════════════════════════════
# T11: test_remote endpoint refactor V2 contract
# ══════════════════════════════════════════════════════════════════

class TestTestRemoteEndpoint:
    """V2-3: POST /api/integrations/remote/{id}/test uses credential_ref, no hardcoded root@."""

    def test_no_root_at_hardcoding_in_routes(self):
        """V2-3: grep routes_integrations.py for hardcoded root@."""
        routes_path = Path("/home/king/rebuild/backend/app/api/routes_integrations.py")
        text = routes_path.read_text(encoding="utf-8")
        assert 'f"root@' not in text, "routes_integrations.py must not hardcode root@"
        assert '"root@' not in text, "routes_integrations.py must not hardcode root@"

    def test_no_strict_host_key_checking_in_routes(self):
        """V2-3: grep routes_integrations.py for StrictHostKeyChecking=no (not in code, only in comments is ok)."""
        routes_path = Path("/home/king/rebuild/backend/app/api/routes_integrations.py")
        # Check that StrictHostKeyChecking=no is not used in executable code
        # (it may appear in docstrings/comments as a description of what we removed)
        code_lines = [
            line for line in routes_path.read_text(encoding="utf-8").splitlines()
            if "StrictHostKeyChecking=no" in line
            and not line.lstrip().startswith("#")
            and not line.lstrip().startswith('"""')
            and not line.lstrip().startswith("'''")
        ]
        assert not code_lines, (
            f"routes_integrations.py must not use StrictHostKeyChecking=no in code (D-093): {code_lines}"
        )

    def test_test_remote_uses_credential_ref(self):
        """V2-3: test_remote function uses credential_ref (checks for no cred_ref)."""
        routes_path = Path("/home/king/rebuild/backend/app/api/routes_integrations.py")
        text = routes_path.read_text(encoding="utf-8")
        assert "credential_ref" in text, "test_remote must use credential_ref (D-093)"
        assert "RemoteSSHExecutionProvider" in text, "test_remote must use RemoteSSHExecutionProvider"

    def test_endpoint_missing_credential_ref_returns_400(self, client, isolated_data):
        """V2-1: 400 when host has no credential configured."""
        # Create host without credential_ref
        resp = client.post("/api/integrations/remote", json={
            "name": "test-host",
            "address": "192.168.1.100",
            "port": 22,
        })
        assert resp.status_code == 200
        host_id = resp.json()["data"]["remote_host_id"]

        resp2 = client.post(f"/api/integrations/remote/{host_id}/test")
        assert resp2.status_code == 400
        assert "credential" in resp2.json()["detail"].lower()


# ══════════════════════════════════════════════════════════════════
# T12: /remote/{id}/exec + /remote/{id}/transfer API contracts
# ══════════════════════════════════════════════════════════════════

class TestRemoteExecEndpoint:
    def test_exec_missing_host_returns_404(self, client, isolated_data):
        resp = client.post("/api/integrations/remote/nonexistent-id/exec",
                           json={"command": "echo hi"})
        assert resp.status_code == 404

    def test_exec_no_credential_returns_400(self, client, isolated_data):
        resp = client.post("/api/integrations/remote", json={
            "name": "no-cred-host", "address": "10.0.0.1", "port": 22
        })
        host_id = resp.json()["data"]["remote_host_id"]
        resp2 = client.post(f"/api/integrations/remote/{host_id}/exec",
                            json={"command": "echo hi"})
        assert resp2.status_code == 400

    def test_exec_result_structure(self, client, isolated_data):
        """When mocked, result dict has required fields."""
        resp = client.post("/api/integrations/remote", json={
            "name": "mock-host", "address": "10.0.0.2",
            "port": 22, "credential_ref": "mock-cred-id",
        })
        host_id = resp.json()["data"]["remote_host_id"]

        mock_result = {
            "exit_code": 0, "stdout": "hi", "stderr": "", "blocked": False,
            "risk_level": "L1", "provider": "remote_ssh", "elapsed_ms": 100,
        }
        with patch("app.services.remote_executor.RemoteSSHExecutionProvider.execute",
                   new=AsyncMock(return_value=mock_result)):
            resp2 = client.post(f"/api/integrations/remote/{host_id}/exec",
                                json={"command": "echo hi"})
        assert resp2.status_code == 200
        data = resp2.json()["data"]
        assert "exit_code" in data
        assert "risk_level" in data
        assert "provider" in data


class TestRemoteTransferEndpoint:
    def test_transfer_missing_host_returns_404(self, client, isolated_data):
        resp = client.post("/api/integrations/remote/bad-id/transfer",
                           json={"action": "put", "local_path": "/workspace/f.py",
                                 "remote_path": "/home/deploy/f.py"})
        assert resp.status_code == 404

    def test_transfer_invalid_action_returns_422(self, client, isolated_data):
        resp = client.post("/api/integrations/remote/any-id/transfer",
                           json={"action": "delete", "local_path": "/workspace/f.py",
                                 "remote_path": "/home/deploy/f.py"})
        assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════
# V6: Negative paths
# ══════════════════════════════════════════════════════════════════

class TestNegativePaths:
    def test_v6_1_container_error_not_local_subprocess(self):
        """V6-1: ContainerExecutionProvider error must not degrade to local_subprocess."""
        from app.services.execution_provider import ContainerExecutionProvider
        with patch("docker.from_env", side_effect=Exception("no docker")):
            result = asyncio.run(ContainerExecutionProvider().execute("print('x')"))
        assert result["provider"] != "local_subprocess", (
            "D-090 violation: container provider silently downgraded to local"
        )

    def test_v6_2_bad_host_returns_error_no_exception(self):
        """V6-2: Unreachable host returns error dict, does not raise."""
        from app.services.remote_executor import RemoteSSHExecutionProvider
        import paramiko
        host = _make_host()
        mock_db = MagicMock()

        def mock_connect(*a, **kw):
            raise OSError("Connection refused")

        with patch(_CS_PATH) as MockCS, \
             patch("app.services.remote_executor._get_paramiko") as mock_pm:
            MockCS.return_value.decrypt.return_value = _ssh_cred_json()
            mock_client_inst = MagicMock()
            mock_client_inst.connect.side_effect = OSError("Connection refused")
            mock_pm_module = MagicMock()
            mock_pm_module.SSHClient.return_value = mock_client_inst
            mock_pm_module.RejectPolicy.return_value = MagicMock()
            mock_pm.return_value = mock_pm_module

            provider = RemoteSSHExecutionProvider(host, mock_db, mode="auto")
            result = asyncio.run(provider.execute("echo hi"))

        assert result["exit_code"] == -1
        assert result["blocked"] is False
        assert "stderr" in result

    def test_v6_3_unknown_host_key_rejected_by_default(self):
        """V6-3: Without fingerprint and trust_on_first_use=False, unknown host is rejected."""
        import paramiko
        from app.services.remote_executor import RemoteSSHExecutionProvider
        host = _make_host(fingerprint=None)
        mock_db = MagicMock()

        def mock_connect(*a, **kw):
            # Simulate paramiko RejectPolicy raising SSHException
            raise paramiko.SSHException("Server .* not found in known_hosts")

        with patch(_CS_PATH) as MockCS, \
             patch("app.services.remote_executor._get_paramiko") as mock_pm:
            MockCS.return_value.decrypt.return_value = _ssh_cred_json()
            mock_client_inst = MagicMock()
            mock_client_inst.connect.side_effect = paramiko.SSHException("not in known_hosts")
            mock_pm_module = MagicMock()
            mock_pm_module.SSHClient.return_value = mock_client_inst
            mock_pm_module.RejectPolicy.return_value = MagicMock()
            mock_pm_module.SSHException = paramiko.SSHException
            mock_pm.return_value = mock_pm_module

            provider = RemoteSSHExecutionProvider(host, mock_db, mode="auto",
                                                  trust_on_first_use=False)
            result = asyncio.run(provider.execute("echo hi"))

        assert result["exit_code"] == -1

    def test_v6_4_high_risk_remote_blocked(self):
        """V6-4: High-risk command (rm -rf) blocked before SSH connection."""
        from app.services.remote_executor import RemoteSSHExecutionProvider
        host = _make_host()
        mock_db = MagicMock()
        with patch(_CS_PATH) as MockCS:
            MockCS.return_value.decrypt.return_value = _ssh_cred_json()
            provider = RemoteSSHExecutionProvider(host, mock_db, mode="auto")
        result = asyncio.run(provider.execute("rm -rf /"))
        assert result["blocked"] is True
        assert result["risk_level"] == "L5"

    def test_v6_5_invalid_credential_json_returns_error(self):
        """V6-5: Malformed credential JSON → error, not crash."""
        from app.services.remote_executor import RemoteSSHExecutionProvider
        host = _make_host()
        mock_db = MagicMock()
        with patch(_CS_PATH) as MockCS:
            MockCS.return_value.decrypt.return_value = "not-json-at-all"
            provider = RemoteSSHExecutionProvider(host, mock_db, mode="auto")
        result = asyncio.run(provider.execute("echo hi"))
        assert result["exit_code"] == -1
        assert "stderr" in result

    def test_v6_no_key_in_audit_record(self):
        """G9: Audit record must not contain password or private_key values."""
        audit_calls = []

        from app.services.remote_executor import RemoteSSHExecutionProvider, _blocked_result
        host = _make_host()
        mock_db = MagicMock()

        with patch(_CS_PATH) as MockCS, \
             patch("app.dependencies.get_services") as mock_gs:
            MockCS.return_value.decrypt.return_value = _ssh_cred_json(password="supersecretpw")
            mock_audit = MagicMock()
            mock_audit.write = lambda **kwargs: audit_calls.append(kwargs)
            mock_gs.return_value.audit_writer = mock_audit

            provider = RemoteSSHExecutionProvider(host, mock_db, mode="auto")
            # Use a blocked result to trigger audit
            provider._write_audit("test_action", "echo hi", {"exit_code": 0, "blocked": False})

        # Check no audit_call contains the password
        for call in audit_calls:
            call_str = json.dumps(call, default=str)
            assert "supersecretpw" not in call_str, "Password leaked into audit record"
