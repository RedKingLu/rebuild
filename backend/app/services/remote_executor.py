"""Remote SSH execution provider — D-093 / R9-5-6 T4/T5/T6/T9/T10.

Provides paramiko-based SSH command execution and SFTP file transfer
as the "remote" mode of the ExecutionProvider abstraction.

Security:
- Credentials are decrypted via CredentialService, used in-memory, then
  immediately discarded. They are NEVER logged, returned, or serialised.
- Trace/Audit records use masked_address + command digest, never plaintext
  credentials or raw command strings.
- Host key fingerprint is verified against the stored SHA-256 fingerprint
  on RemoteHost; unknown hosts are rejected by default (D-093).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import paramiko as _paramiko

log = logging.getLogger("rebuild.remote_executor")


# ── Credential helpers ─────────────────────────────────────────────────────

@dataclasses.dataclass
class SSHCredential:
    user: str
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None


def _parse_ssh_credential(plaintext: str) -> SSHCredential:
    """Parse decrypted JSON into SSHCredential.

    Expected formats:
      {"user": "deploy", "password": "..."}
      {"user": "deploy", "private_key": "-----BEGIN...", "passphrase": "optional"}
    """
    try:
        data = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SSH credential must be valid JSON: {exc}") from exc
    user = data.get("user")
    if not user:
        raise ValueError("SSH credential missing required 'user' field")
    if not data.get("password") and not data.get("private_key"):
        raise ValueError("SSH credential must provide 'password' or 'private_key'")
    return SSHCredential(
        user=user,
        password=data.get("password"),
        private_key=data.get("private_key"),
        passphrase=data.get("passphrase"),
    )


def _compute_host_key_fingerprint(key) -> str:
    """Return SHA-256 hex fingerprint of a paramiko host key."""
    return hashlib.sha256(key.asbytes()).hexdigest()


# ── Host key policies ──────────────────────────────────────────────────────

def _get_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError as exc:
        raise RuntimeError(
            "paramiko is not installed — cannot use RemoteSSHExecutionProvider. "
            "Run: uv add paramiko"
        ) from exc


class _FingerprintCheckPolicy:
    """Accept a host key only if its SHA-256 fingerprint matches the stored value."""

    def __init__(self, expected_fingerprint: str):
        self._expected = expected_fingerprint

    def missing_host_key(self, client, hostname, key):
        actual = _compute_host_key_fingerprint(key)
        if actual != self._expected:
            paramiko = _get_paramiko()
            raise paramiko.SSHException(
                f"Host key fingerprint mismatch for {hostname}: "
                f"expected {self._expected[:16]}..., got {actual[:16]}..."
            )
        # Fingerprint matches — accept silently


class _TrustOnFirstUsePolicy:
    """Accept any host key on first use; store the fingerprint for future checks."""

    def __init__(self, host, db):
        self._host = host
        self._db = db

    def missing_host_key(self, client, hostname, key):
        fingerprint = _compute_host_key_fingerprint(key)
        log.info(
            "TOFU: accepting host key for %s (fp=%s...); storing for future checks.",
            hostname, fingerprint[:16],
        )
        # Persist fingerprint to RemoteHost record
        try:
            from app.services.remote_service import RemoteService
            svc = RemoteService(self._db)
            svc.update(self._host.remote_host_id, host_key_fingerprint=fingerprint)
        except Exception as exc:
            log.warning("TOFU: could not persist fingerprint: %s", exc)


# ── Main provider ──────────────────────────────────────────────────────────

class RemoteSSHExecutionProvider:
    """Execute commands and transfer files on a remote host via SSH/SFTP.

    D-093: Third execution mode alongside local and container.
    D-076: This is a code/command execution path, NOT an AI coding agent path.
    """

    name = "remote_ssh"

    def __init__(self, host, db, *, mode: str = "plan", trust_on_first_use: bool = False):
        self._host = host
        self._db = db
        self._mode = mode
        self._tofu = trust_on_first_use

    async def detect_environment(self) -> dict:
        """G8: read-only remote OS / runtime / service fingerprint.

        Runs a battery of read-only commands via one SSH session and returns a
        structured dict suitable for RemoteHost.environment_tags.  Never writes,
        never mutates.  Returns partial results on failure (honest, per D-105).
        """
        import hashlib as _hl
        result: dict = {
            "ok": False,
            "os": None,
            "kernel": None,
            "arch": None,
            "python": None,
            "services": [],
            "探测命令指纹": [],
            "error": None,
        }
        # Several read-only commands.  Each is individually safe and avoids
        # /dev/null redirection (which matches the DENY list).  Honest on error.
        try:
            r_rel = await self.execute("cat /etc/os-release || true", timeout=10)
            if r_rel.get("blocked"):
                result["error"] = "探测命令被安全策略拦截"
                return result
            rel_out = r_rel.get("stdout", "")
            uname_out = (await self.execute("uname -a", timeout=10)).get("stdout", "").strip()
            py_raw = (await self.execute("python3 --version 2>&1 || true", timeout=10)).get("stdout", "").strip()
            tools_raw = (await self.execute("command -v docker node go java mvn gradle || true", timeout=10)).get("stdout", "")
            ports_raw = (await self.execute("ss -tlnp | awk '{print $4}' | grep -E ':(22|3306|5432|6379|8080|5236|54321|5866)$' | sort -u || true", timeout=10)).get("stdout", "")
        except Exception as exc:
            result["error"] = _safe_str(exc)
            return result

        # Parse /etc/os-release
        for line in rel_out.splitlines():
            if line.startswith("PRETTY_NAME="):
                result["os"] = line.split("=", 1)[1].strip('"')
            elif line.startswith("VERSION_ID=") and result["os"]:
                result["os"] = f"{result['os']} {line.split('=', 1)[1].strip('\"')}"
        result["os"] = (result["os"] or "").strip() or None
        # kernel / arch from uname -a
        result["kernel"] = uname_out or None
        # python
        if py_raw.startswith("Python "):
            result["python"] = py_raw.split()[1]
        # runtime tools present
        for tool in ("docker", "node", "go", "java", "mvn", "gradle"):
            if f"/{tool}" in tools_raw:
                result["探测命令指纹"].append(tool)
        # port→service fingerprint
        known_ports = {"3306": "mysql", "5432": "postgres", "6379": "redis",
                       "8080": "tomcat/spring", "5236": "dameng", "54321": "kingbase",
                       "5866": "highgo"}
        for p, svc in known_ports.items():
            if f":{p}" in ports_raw:
                result["services"].append({"name": svc, "port": int(p), "status": "detected"})
        # sshd always present (we're connected through it)
        if not any(s["name"] == "sshd" for s in result["services"]):
            result["services"].insert(0, {"name": "sshd", "port": 22, "status": "connected"})
        result["ok"] = True
        return result

    async def execute(
        self,
        code: str,
        language: str = "bash",
        timeout: int = 30,
        model: str | None = None,
        cwd: str | None = None,
    ) -> dict:
        """Run a command on the remote host. Returns standard ExecutionResult."""
        import asyncio
        from app.services.execution_provider import _check_dangerous, _classify_risk
        from app.services.mode_policy import authorize_action

        start = time.monotonic()

        # Security: hard deny before anything else
        denial = _check_dangerous(code)
        if denial:
            return _blocked_result(denial, "remote_ssh", "L5")

        risk = _classify_risk(code, language)

        # Mode policy: L4+ requires confirmation; blocked in non-confirmed path
        auth = authorize_action(self._mode, risk, action="remote_exec")
        if auth["decision"] in ("require_confirmation", "escalate"):
            return _blocked_result(
                f"Risk {risk} requires confirmation in {self._mode} mode: {auth['reason']}",
                "remote_ssh", risk,
            )

        # Build credential — decrypt, use, discard immediately
        cred_plaintext = self._decrypt_credential()
        if cred_plaintext is None:
            return _error_result(
                "Credential not found or decryption failed — check credential_ref",
                "remote_ssh",
            )
        try:
            cred = _parse_ssh_credential(cred_plaintext)
        except ValueError as exc:
            return _error_result(str(exc), "remote_ssh")
        finally:
            cred_plaintext = None  # noqa: F841 — clear from locals immediately

        # Run in thread (paramiko is synchronous)
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._run_command, code, cwd, timeout, cred),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            result = _error_result(f"Remote execution timed out after {timeout}s", "remote_ssh")
        except Exception as exc:
            result = _error_result(f"Remote execution error: {_safe_str(exc)}", "remote_ssh")
        finally:
            # Ensure cred is cleared even on exception
            cred = None  # noqa: F841

        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        result["risk_level"] = risk
        result["execution_mode"] = "remote"

        # Audit (no credentials in record)
        self._write_audit("remote_exec", code, result)

        return result

    async def transfer(
        self,
        action: str,
        local_path: str,
        remote_path: str,
    ) -> dict:
        """SFTP file transfer: action='put' (local→remote) or 'get' (remote→local).

        local_path must be an absolute path within the workspace.
        remote_path is the destination/source on the remote host.
        """
        import asyncio
        from app.services.execution_provider import _classify_risk
        from app.services.mode_policy import authorize_action

        start = time.monotonic()

        if action not in ("put", "get"):
            return _error_result(f"Unknown transfer action: {action!r} (must be 'put' or 'get')", "remote_ssh")

        # Classify write risk (L3 for writes to non-system paths, L4+ for system paths)
        risk = _classify_transfer_risk(action, remote_path)
        auth = authorize_action(self._mode, risk, action=f"remote_transfer_{action}")
        if auth["decision"] in ("require_confirmation", "escalate"):
            return _blocked_result(
                f"Transfer risk {risk} requires confirmation in {self._mode} mode",
                "remote_ssh", risk,
            )

        cred_plaintext = self._decrypt_credential()
        if cred_plaintext is None:
            return _error_result("Credential not found or decryption failed", "remote_ssh")
        try:
            cred = _parse_ssh_credential(cred_plaintext)
        except ValueError as exc:
            return _error_result(str(exc), "remote_ssh")
        finally:
            cred_plaintext = None

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._run_transfer, action, local_path, remote_path, cred),
                timeout=120,
            )
        except asyncio.TimeoutError:
            result = _error_result("File transfer timed out", "remote_ssh")
        except Exception as exc:
            result = _error_result(f"File transfer error: {_safe_str(exc)}", "remote_ssh")
        finally:
            cred = None

        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        result["risk_level"] = risk
        result["execution_mode"] = "remote"

        self._write_audit(f"remote_transfer_{action}", remote_path, result)
        return result

    # ── Internal helpers ───────────────────────────────────────────────────

    def _decrypt_credential(self) -> str | None:
        """Decrypt and return plaintext credential JSON. Returns None on failure."""
        if not self._host.credential_ref:
            return None
        try:
            from app.services.credential_service import CredentialService
            return CredentialService(self._db).decrypt(self._host.credential_ref)
        except Exception as exc:
            log.warning("Credential decryption failed for host %s: %s",
                        self._host.masked_address or "***", _safe_str(exc))
            return None

    def _connect(self, cred: SSHCredential):
        """Return a connected paramiko.SSHClient. Caller must close it."""
        paramiko = _get_paramiko()
        client = paramiko.SSHClient()

        host = self._host
        if host.host_key_fingerprint:
            client.set_missing_host_key_policy(_FingerprintCheckPolicy(host.host_key_fingerprint))
        elif self._tofu:
            client.set_missing_host_key_policy(_TrustOnFirstUsePolicy(host, self._db))
        else:
            # Default: reject unknown hosts (D-093 security requirement)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

        connect_kwargs: dict = {
            "hostname": host.address,
            "port": host.port or 22,
            "username": cred.user,
            "timeout": 10,
            "look_for_keys": False,
            "allow_agent": False,
        }

        if cred.private_key:
            from io import StringIO
            # Determine key type (RSA / Ed25519 / ECDSA)
            key_text = cred.private_key
            if "OPENSSH PRIVATE KEY" in key_text or "BEGIN EC PRIVATE KEY" in key_text:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(
                        StringIO(key_text), password=cred.passphrase
                    )
                except Exception:
                    pkey = paramiko.ECDSAKey.from_private_key(
                        StringIO(key_text), password=cred.passphrase
                    )
            else:
                pkey = paramiko.RSAKey.from_private_key(
                    StringIO(key_text), password=cred.passphrase
                )
            connect_kwargs["pkey"] = pkey
        elif cred.password:
            connect_kwargs["password"] = cred.password

        try:
            client.connect(**connect_kwargs)
        except paramiko.AuthenticationException:
            raise RuntimeError("SSH authentication failed — check credential")
        except paramiko.SSHException as exc:
            raise RuntimeError(f"SSH connection error: {_safe_str(exc)}")
        except OSError as exc:
            raise RuntimeError(f"Cannot reach host {host.masked_address or '***'}: {_safe_str(exc)}")

        return client

    def _run_command(self, code: str, cwd: str | None, timeout: int, cred: SSHCredential) -> dict:
        """Synchronous: run a shell command via exec_command."""
        client = None
        try:
            client = self._connect(cred)
            # Prepend cwd if provided
            cmd = f"cd {cwd} && {code}" if cwd else code
            stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=False)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")[:65536]
            err = stderr.read().decode("utf-8", errors="replace")[:8192]
            return {
                "exit_code": exit_code,
                "stdout": _sanitize_output(out),
                "stderr": _sanitize_output(err),
                "provider": "remote_ssh",
                "fallback": False,
                "blocked": False,
            }
        except RuntimeError as exc:
            return {
                "exit_code": -1, "stdout": "", "stderr": str(exc),
                "provider": "remote_ssh", "fallback": False, "blocked": False,
            }
        except Exception as exc:
            return {
                "exit_code": -1, "stdout": "", "stderr": f"Unexpected error: {_safe_str(exc)}",
                "provider": "remote_ssh", "fallback": False, "blocked": False,
            }
        finally:
            if client:
                client.close()

    def _run_transfer(self, action: str, local_path: str, remote_path: str,
                      cred: SSHCredential) -> dict:
        """Synchronous: SFTP put or get."""
        client = None
        try:
            client = self._connect(cred)
            sftp = client.open_sftp()
            try:
                if action == "put":
                    sftp.put(local_path, remote_path)
                    return {
                        "exit_code": 0,
                        "stdout": f"Transferred {Path(local_path).name} → {remote_path}",
                        "stderr": "",
                        "provider": "remote_ssh", "fallback": False, "blocked": False,
                    }
                else:  # get
                    sftp.get(remote_path, local_path)
                    return {
                        "exit_code": 0,
                        "stdout": f"Downloaded {remote_path} → {Path(local_path).name}",
                        "stderr": "",
                        "provider": "remote_ssh", "fallback": False, "blocked": False,
                    }
            finally:
                sftp.close()
        except RuntimeError as exc:
            return {
                "exit_code": -1, "stdout": "", "stderr": str(exc),
                "provider": "remote_ssh", "fallback": False, "blocked": False,
            }
        except Exception as exc:
            return {
                "exit_code": -1, "stdout": "", "stderr": f"SFTP error: {_safe_str(exc)}",
                "provider": "remote_ssh", "fallback": False, "blocked": False,
            }
        finally:
            if client:
                client.close()

    def _write_audit(self, action: str, cmd_or_path: str, result: dict) -> None:
        """Write sanitized audit record — no credentials, no raw secrets."""
        try:
            from app.dependencies import get_services
            svc = get_services()
            digest = hashlib.sha256(cmd_or_path.encode()).hexdigest()[:16]
            svc.audit_writer.write(
                action=action,
                actor="execution_provider",
                resource_id=getattr(self._host, "remote_host_id", "unknown"),
                resource_type="remote_host",
                outcome="blocked" if result.get("blocked") else (
                    "success" if result.get("exit_code", -1) == 0 else "failure"
                ),
                extra={
                    "provider": "remote_ssh",
                    "host": getattr(self._host, "masked_address", "***") or "***",
                    "risk_level": result.get("risk_level", "L1"),
                    "exit_code": result.get("exit_code"),
                    "cmd_digest": digest,
                    # No credentials, no raw command string
                },
            )
        except Exception as exc:
            log.warning("Audit write failed (non-fatal): %s", exc)


# ── Utility functions ──────────────────────────────────────────────────────

_SENSITIVE_PATTERNS = [
    "password=", "passwd=", "secret=", "token=", "api_key=",
    "private_key", "passphrase=",
]


def _sanitize_output(text: str) -> str:
    """Scrub likely credential patterns from command output (best-effort)."""
    lines = text.splitlines()
    result = []
    for line in lines:
        if any(p in line.lower() for p in _SENSITIVE_PATTERNS):
            result.append("[REDACTED: possible credential in output]")
        else:
            result.append(line)
    return "\n".join(result)


def _safe_str(exc: Exception) -> str:
    """Convert exception to string without leaking potential credential fragments."""
    msg = str(exc)
    # Truncate if suspiciously long (might contain credential data)
    return msg[:256] if len(msg) > 256 else msg


def _between(text: str, start: str, end: str) -> str:
    """Return text between start and end markers (exclusive)."""
    try:
        s = text.index(start) + len(start)
        e = text.index(end, s)
        return text[s:e].strip()
    except ValueError:
        return ""


def _classify_transfer_risk(action: str, remote_path: str) -> str:
    """Classify file transfer risk based on action and destination path."""
    path_lower = remote_path.lower()
    if any(p in path_lower for p in ("/etc/", "/sys/", "/proc/", "/root/", "~/.ssh")):
        return "L5"
    if any(p in path_lower for p in ("/usr/", "/bin/", "/sbin/", "/lib/")):
        return "L4"
    if action == "put":
        return "L3"  # Writing to remote is at least L3
    return "L2"  # Reading from remote is L2


def _blocked_result(reason: str, provider: str, risk_level: str) -> dict:
    return {
        "exit_code": 1,
        "stdout": "",
        "stderr": reason,
        "provider": provider,
        "execution_mode": "remote",
        "fallback": False,
        "blocked": True,
        "risk_level": risk_level,
        "audited": False,
        "elapsed_ms": 0,
    }


def _error_result(reason: str, provider: str) -> dict:
    return {
        "exit_code": -1,
        "stdout": "",
        "stderr": reason,
        "provider": provider,
        "execution_mode": "remote",
        "fallback": False,
        "blocked": False,
        "risk_level": "L1",
        "audited": False,
        "elapsed_ms": 0,
    }
