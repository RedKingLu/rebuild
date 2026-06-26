"""Integration API routes — Git, Remote, OpenCode, Feishu, and aggregate summary."""

import os
import uuid
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.dependencies import get_services
from app.schemas.common import SuccessEnvelope, Meta
from app.services.git_service import GitService, run_git_command
from app.services.remote_service import RemoteService
from app.services.integration_service import IntegrationSummaryService
from app.services.opencode_adapter import is_opencode_available
from app.services.git_oauth_service import (
    GitAccountService, get_github_authorize_url,
    exchange_github_code, fetch_github_user, fetch_github_repos,
)
from app.models.git_host import GitHostStatus
from app.models.remote_host import RemoteHostStatus
from app.models.integration_config import IntegrationStatus, IntegrationType
from app.models.git_account import AccountStatus

integration_router = APIRouter(prefix="/integrations", tags=["integrations"])

# ── Helpers ──────────────────────────────────────────────

def _db(db: Session = Depends(get_db)) -> Session:
    return db

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

# ── Git Integration ──────────────────────────────────────

# Request models (inline to avoid schema file bloat)
class GitHostCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    repo_url: str = Field(..., min_length=1, max_length=1024)
    platform: str = "other"
    auth_type: str = "https_token"
    credential_ref: str | None = None
    default_branch: str = "main"

class GitHostUpdateRequest(BaseModel):
    name: str | None = None
    repo_url: str | None = None
    platform: str | None = None
    auth_type: str | None = None
    credential_ref: str | None = None
    default_branch: str | None = None

class GitCommitRequest(BaseModel):
    message: str = Field(..., min_length=1)
    files: list[str] = Field(default_factory=list)

class GitPushRequest(BaseModel):
    approval_token: str
    confirm: bool = True


@integration_router.get("/git")
async def list_git(db: Session = Depends(_db)):
    svc = GitService(db)
    hosts = svc.list_all()
    get_services().trace_writer.write("state_change", action="list_git_hosts",
                                       summary=f"Listed {len(hosts)} git hosts")
    return SuccessEnvelope(
        data=[GitService.to_response(h) for h in hosts],
        meta=Meta(source_status="real", capability_status="available"),
    )


@integration_router.post("/git")
async def create_git(req: GitHostCreateRequest, db: Session = Depends(_db)):
    svc = GitService(db)
    gh = svc.create(
        name=req.name, repo_url=req.repo_url, platform=req.platform,
        auth_type=req.auth_type, credential_ref=req.credential_ref,
        default_branch=req.default_branch,
    )
    get_services().trace_writer.write("state_change", action="create_git_host",
                                       summary=f"Created git host {gh.git_host_id}")
    return SuccessEnvelope(data=GitService.to_response(gh),
                           meta=Meta(source_status="real", capability_status="available"))


# ── Git OAuth: Connect GitHub/GitLab/Gitee accounts ──
# Must be BEFORE /git/{git_host_id} to avoid path conflict

@integration_router.get("/git/oauth/github/authorize")
async def github_authorize():
    url = get_github_authorize_url()
    if not url or "client_id=" not in url:
        raise HTTPException(503, "GitHub OAuth not configured. Set REBUILD_GITHUB_CLIENT_ID and REBUILD_GITHUB_CLIENT_SECRET.")
    return SuccessEnvelope(
        data={"authorize_url": url},
        meta=Meta(source_status="real", capability_status="available"),
    )


@integration_router.get("/git/oauth/github/callback")
async def github_callback(code: str, db: Session = Depends(_db)):
    token_data = await exchange_github_code(code)
    if token_data is None:
        raise HTTPException(400, "Failed to exchange GitHub OAuth code")
    access_token = token_data.get("access_token", "")
    if not access_token:
        raise HTTPException(400, "No access token in GitHub response")
    user = await fetch_github_user(access_token)
    if user is None:
        raise HTTPException(400, "Failed to fetch GitHub user info")
    username = user.get("login", "unknown")
    avatar = user.get("avatar_url", "")
    svc = GitAccountService(db)
    existing = [a for a in svc.list_all() if a.username == username and a.platform.value == "github"]
    if existing:
        from app.security.byok_crypto import encrypt_api_key, hash_api_key
        acct = existing[0]
        acct.encrypted_token = encrypt_api_key(access_token, "default")
        acct.token_fingerprint = hash_api_key(access_token)
        acct.status = AccountStatus.connected
        db.commit()
    else:
        svc.create_account("github", username, access_token, avatar)
    get_services().trace_writer.write("state_change", action="github_oauth",
                                       summary=f"GitHub OAuth: {username}")
    from app.services.git_oauth_service import FRONTEND_URL as frontend_url
    return RedirectResponse(url=f"{frontend_url}/integrations?oauth=github&status=connected&user={username}")


@integration_router.get("/git/accounts")
async def list_git_accounts(db: Session = Depends(_db)):
    svc = GitAccountService(db)
    accounts = svc.list_all()
    return SuccessEnvelope(
        data=[GitAccountService.to_response(a) for a in accounts],
        meta=Meta(source_status="real", capability_status="available"),
    )


@integration_router.get("/git/accounts/{account_id}/repos")
async def list_account_repos(account_id: str, db: Session = Depends(_db)):
    svc = GitAccountService(db)
    token = svc.get_token(account_id)
    if token is None:
        raise HTTPException(404, f"Account {account_id} not found")
    repos = await fetch_github_repos(token)
    return SuccessEnvelope(
        data=repos,
        meta=Meta(source_status="real", capability_status="available"),
    )


@integration_router.delete("/git/accounts/{account_id}")
async def disconnect_git_account(account_id: str, db: Session = Depends(_db)):
    svc = GitAccountService(db)
    ok = svc.delete(account_id)
    if not ok:
        raise HTTPException(404, f"Account {account_id} not found")
    return SuccessEnvelope(
        data={"disconnected": True},
        meta=Meta(source_status="real", capability_status="available"),
    )


@integration_router.get("/git/{git_host_id}")
async def get_git(git_host_id: str, db: Session = Depends(_db)):
    svc = GitService(db)
    gh = svc.get(git_host_id)
    if gh is None:
        raise HTTPException(404, f"Git host {git_host_id} not found")
    return SuccessEnvelope(data=GitService.to_response(gh),
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.patch("/git/{git_host_id}")
async def update_git(git_host_id: str, req: GitHostUpdateRequest, db: Session = Depends(_db)):
    svc = GitService(db)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    gh = svc.update(git_host_id, **updates)
    if gh is None:
        raise HTTPException(404, f"Git host {git_host_id} not found")
    return SuccessEnvelope(data=GitService.to_response(gh),
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.delete("/git/{git_host_id}")
async def delete_git(git_host_id: str, db: Session = Depends(_db)):
    svc = GitService(db)
    ok = svc.delete(git_host_id)
    if not ok:
        raise HTTPException(404, f"Git host {git_host_id} not found")
    get_services().trace_writer.write("state_change", action="delete_git_host",
                                       summary=f"Deleted git host {git_host_id}")
    return SuccessEnvelope(data={"deleted": True},
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.post("/git/{git_host_id}/clone")
async def clone_git(git_host_id: str, db: Session = Depends(_db)):
    svc = GitService(db)
    gh = svc.get(git_host_id)
    if gh is None:
        raise HTTPException(404, f"Git host {git_host_id} not found")
    clone_path = svc.get_clone_path(git_host_id)
    os.makedirs(clone_path, exist_ok=True)
    result = await run_git_command("clone", gh.repo_url, ".", cwd=clone_path, timeout=120)
    if result["success"]:
        svc.update(git_host_id, status=GitHostStatus.connected,
                   clone_path=clone_path,
                   last_connected_at=datetime.now(timezone.utc))
    else:
        svc.update(git_host_id, status=GitHostStatus.error)
    get_services().trace_writer.write("state_change", action="git_clone",
                                       summary=f"Clone {git_host_id}: {'OK' if result['success'] else 'FAIL'}")
    return SuccessEnvelope(data=result, meta=Meta(source_status="real", capability_status="available"))


@integration_router.post("/git/{git_host_id}/pull")
async def pull_git(git_host_id: str, db: Session = Depends(_db)):
    svc = GitService(db)
    gh = svc.get(git_host_id)
    if gh is None or not gh.clone_path:
        raise HTTPException(404, "Git host not found or not cloned")
    result = await run_git_command("pull", cwd=gh.clone_path, timeout=60)
    return SuccessEnvelope(data=result, meta=Meta(source_status="real", capability_status="available"))


@integration_router.get("/git/{git_host_id}/status")
async def status_git(git_host_id: str, db: Session = Depends(_db)):
    svc = GitService(db)
    gh = svc.get(git_host_id)
    if gh is None or not gh.clone_path:
        raise HTTPException(404, "Git host not found or not cloned")
    result = await run_git_command("status", "--porcelain", cwd=gh.clone_path, timeout=30)
    branch_result = await run_git_command("branch", "--show-current", cwd=gh.clone_path, timeout=10)
    return SuccessEnvelope(data={
        "branch": branch_result["stdout"].strip(),
        "changed_files": result["stdout"].strip(),
        "success": result["success"],
    }, meta=Meta(source_status="real", capability_status="available"))


@integration_router.post("/git/{git_host_id}/commit")
async def commit_git(git_host_id: str, req: GitCommitRequest, db: Session = Depends(_db)):
    svc = GitService(db)
    gh = svc.get(git_host_id)
    if gh is None or not gh.clone_path:
        raise HTTPException(404, "Git host not found or not cloned")
    # Stage files
    if req.files:
        for f in req.files:
            await run_git_command("add", f, cwd=gh.clone_path, timeout=10)
    else:
        await run_git_command("add", ".", cwd=gh.clone_path, timeout=10)
    result = await run_git_command("commit", "-m", req.message, cwd=gh.clone_path, timeout=30)
    # Write Audit
    get_services().audit_writer.write(
        audit_type="git.commit", risk_level="L3", action=f"commit:{git_host_id}",
        decision="committed", reason=f"Git commit on {git_host_id}: {req.message[:100]}",
    )
    get_services().trace_writer.write("state_change", action="git_commit",
                                       summary=f"Commit on {git_host_id}: {req.message[:80]}")
    return SuccessEnvelope(data=result, meta=Meta(source_status="real", capability_status="available"))


# Push approval tokens (in-memory, short TTL)
_push_tokens: dict[str, dict] = {}


@integration_router.post("/git/{git_host_id}/push-preview")
async def push_preview_git(git_host_id: str, db: Session = Depends(_db)):
    svc = GitService(db)
    gh = svc.get(git_host_id)
    if gh is None or not gh.clone_path:
        raise HTTPException(404, "Git host not found or not cloned")
    # Dry-run push
    dry = await run_git_command("push", "--dry-run", cwd=gh.clone_path, timeout=30)
    # Get diff stat
    diff = await run_git_command("diff", "--stat", "origin/main..HEAD", cwd=gh.clone_path, timeout=30)
    # Get commit log
    log = await run_git_command("log", "--oneline", "origin/main..HEAD", cwd=gh.clone_path, timeout=30)
    # Generate approval token
    token = uuid.uuid4().hex
    _push_tokens[token] = {"git_host_id": git_host_id, "expires_at": datetime.now(timezone.utc).timestamp() + 300}
    # Clean expired tokens
    now_ts = datetime.now(timezone.utc).timestamp()
    for k in list(_push_tokens.keys()):
        if _push_tokens[k]["expires_at"] < now_ts:
            del _push_tokens[k]
    return SuccessEnvelope(data={
        "approval_token": token,
        "expires_in": 300,
        "push_preview": {
            "dry_run": dry["stdout"].strip(),
            "commits": log["stdout"].strip().split("\n") if log["stdout"].strip() else [],
            "diff_stat": diff["stdout"].strip()[:4096],
        },
    }, meta=Meta(source_status="real", capability_status="available"))


@integration_router.post("/git/{git_host_id}/push")
async def push_git(git_host_id: str, req: GitPushRequest, db: Session = Depends(_db)):
    # Validate approval token
    token_data = _push_tokens.pop(req.approval_token, None)
    if token_data is None:
        raise HTTPException(403, "Invalid or expired approval token. Use /push-preview first.")
    if token_data["git_host_id"] != git_host_id:
        raise HTTPException(403, "Approval token does not match this git host.")
    if not req.confirm:
        return SuccessEnvelope(data={"pushed": False, "reason": "confirm=false"},
                               meta=Meta(source_status="real", capability_status="available"))
    svc = GitService(db)
    gh = svc.get(git_host_id)
    if gh is None or not gh.clone_path:
        raise HTTPException(404, "Git host not found or not cloned")
    result = await run_git_command("push", cwd=gh.clone_path, timeout=60)
    # Write L5 Audit
    get_services().audit_writer.write(
        audit_type="git.push", risk_level="L5", action=f"push:{git_host_id}",
        decision="approved" if result["success"] else "failed",
        reason=f"Git push on {git_host_id}: {'OK' if result['success'] else result['stderr'][:100]}",
    )
    get_services().trace_writer.write("state_change", action="git_push",
                                       summary=f"Push on {git_host_id}: {'OK' if result['success'] else 'FAIL'}")
    return SuccessEnvelope(data=result, meta=Meta(source_status="real", capability_status="available"))


# ── Remote Host Integration ──────────────────────────────

class RemoteHostCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    host_type: str = "virtual_machine"
    address: str = ""
    port: int = 22
    os_name: str | None = None
    credential_ref: str | None = None

class RemoteHostUpdateRequest(BaseModel):
    name: str | None = None
    host_type: str | None = None
    address: str | None = None
    port: int | None = None
    os_name: str | None = None
    credential_ref: str | None = None


@integration_router.get("/remote")
async def list_remote(db: Session = Depends(_db)):
    svc = RemoteService(db)
    hosts = svc.list_all()
    return SuccessEnvelope(
        data=[RemoteService.to_response(h) for h in hosts],
        meta=Meta(source_status="real", capability_status="available"),
    )


@integration_router.post("/remote")
async def create_remote(req: RemoteHostCreateRequest, db: Session = Depends(_db)):
    svc = RemoteService(db)
    host = svc.create(
        name=req.name, host_type=req.host_type, address=req.address,
        port=req.port, os_name=req.os_name, credential_ref=req.credential_ref,
    )
    get_services().trace_writer.write("state_change", action="create_remote_host",
                                       summary=f"Created remote host {host.remote_host_id}")
    return SuccessEnvelope(data=RemoteService.to_response(host),
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.get("/remote/{remote_host_id}")
async def get_remote(remote_host_id: str, db: Session = Depends(_db)):
    svc = RemoteService(db)
    host = svc.get(remote_host_id)
    if host is None:
        raise HTTPException(404, f"Remote host {remote_host_id} not found")
    return SuccessEnvelope(data=RemoteService.to_response(host),
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.patch("/remote/{remote_host_id}")
async def update_remote(remote_host_id: str, req: RemoteHostUpdateRequest, db: Session = Depends(_db)):
    svc = RemoteService(db)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    host = svc.update(remote_host_id, **updates)
    if host is None:
        raise HTTPException(404, f"Remote host {remote_host_id} not found")
    return SuccessEnvelope(data=RemoteService.to_response(host),
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.delete("/remote/{remote_host_id}")
async def delete_remote(remote_host_id: str, db: Session = Depends(_db)):
    svc = RemoteService(db)
    ok = svc.delete(remote_host_id)
    if not ok:
        raise HTTPException(404, f"Remote host {remote_host_id} not found")
    return SuccessEnvelope(data={"deleted": True},
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.post("/remote/{remote_host_id}/test")
async def test_remote(remote_host_id: str, db: Session = Depends(_db)):
    """Test SSH connection to remote host."""
    svc = RemoteService(db)
    host = svc.get(remote_host_id)
    if host is None:
        raise HTTPException(404, f"Remote host {remote_host_id} not found")
    svc.update(remote_host_id, status=RemoteHostStatus.testing)
    try:
        # Attempt SSH connection with timeout
        import subprocess
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
             "-p", str(host.port), f"root@{host.address}", "echo connected"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            svc.update(remote_host_id, status=RemoteHostStatus.connected,
                       last_connected_at=datetime.now(timezone.utc))
            status = "connected"
        else:
            svc.update(remote_host_id, status=RemoteHostStatus.error)
            status = "error"
        get_services().trace_writer.write("state_change", action="remote_test",
                                           summary=f"SSH test {remote_host_id}: {status}")
        return SuccessEnvelope(data={
            "status": status, "stdout": result.stdout[:1024], "stderr": result.stderr[:1024],
        }, meta=Meta(source_status="real", capability_status="available"))
    except Exception as e:
        svc.update(remote_host_id, status=RemoteHostStatus.error)
        return SuccessEnvelope(data={"status": "error", "error": str(e)},
                               meta=Meta(source_status="real", capability_status="available"))


# ── OpenCode Execution Integration ──────────────────────

class OpenCodeExecuteRequest(BaseModel):
    code: str = Field(..., min_length=1)
    language: str = "python"
    timeout: int = 30
    model: str | None = None  # Model name for opencode (e.g. 'deepseek-chat')


@integration_router.post("/execution/opencode")
async def execute_opencode(req: OpenCodeExecuteRequest):
    """Execute code via the configured ExecutionProvider (local subprocess by
    default; container sandbox when EXECUTION_MODE=container — Phase C2)."""
    from app.services.execution_provider import get_execution_provider
    provider = get_execution_provider()
    result = await provider.execute(code=req.code, language=req.language,
                                    timeout=req.timeout, model=req.model)
    get_services().trace_writer.write("state_change", action="opencode_execute",
                                       summary=f"Provider={result['provider']}, exit={result['exit_code']}")
    return SuccessEnvelope(data=result, meta=Meta(
        source_status="fallback" if result.get("fallback") else "real",
        capability_status="available",
    ))


@integration_router.get("/execution/opencode/status")
async def opencode_status():
    """Check OpenCode availability."""
    available = is_opencode_available()
    return SuccessEnvelope(data={
        "opencode_available": available,
        "fallback_available": True,
        "provider": "opencode" if available else "fallback_shell",
    }, meta=Meta(source_status="real", capability_status="available"))


# ── Feishu Integration ───────────────────────────────────

class FeishuConfigRequest(BaseModel):
    name: str = "feishu"
    webhook_url: str | None = None
    signing_secret: str | None = None
    credential_ref: str | None = None


def _mask_webhook(url: str) -> str:
    """Return a non-secret masked form of a webhook URL for display."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.netloc or "***"
        return f"{u.scheme or 'https'}://{host}/****"
    except Exception:
        return "***hook/****"


def _encrypt_webhook(url: str) -> str:
    """Encrypt a webhook URL (bearer-style secret) → base64 string for JSON storage."""
    import base64
    from app.security.byok_crypto import encrypt_api_key
    return base64.b64encode(encrypt_api_key(url, "default")).decode("ascii")


def _decrypt_webhook(config: dict) -> str:
    """Recover the plaintext webhook URL from stored config (encrypted or legacy)."""
    import base64
    from app.security.byok_crypto import decrypt_api_key
    enc = (config or {}).get("webhook_url_enc")
    if enc:
        return decrypt_api_key(base64.b64decode(enc), "default")
    return (config or {}).get("webhook_url", "")  # legacy plaintext fallback


@integration_router.get("/feishu")
async def get_feishu_config(db: Session = Depends(_db)):
    svc = IntegrationSummaryService(db)
    config = svc.get_by_type("feishu")
    if config is None:
        return SuccessEnvelope(data={"configured": False, "status": "not_configured"},
                               meta=Meta(source_status="real", capability_status="available"))
    return SuccessEnvelope(data=IntegrationSummaryService.to_response(config),
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.post("/feishu")
async def upsert_feishu_config(req: FeishuConfigRequest, db: Session = Depends(_db)):
    svc = IntegrationSummaryService(db)
    existing = svc.get_by_type("feishu")
    if existing:
        new_config = dict(existing.config or {})
        if req.webhook_url is not None:
            new_config.pop("webhook_url", None)  # drop any legacy plaintext
            new_config["webhook_url_enc"] = _encrypt_webhook(req.webhook_url)
            new_config["webhook_masked"] = _mask_webhook(req.webhook_url)
        if req.signing_secret is not None:
            new_config["signing_secret_set"] = True
        updates = {"config": new_config}
        if req.credential_ref is not None:
            updates["credential_ref"] = req.credential_ref
        ic = svc.update_config(existing.config_id, **updates)
    else:
        config = {"signing_secret_set": req.signing_secret is not None}
        if req.webhook_url is not None:
            config["webhook_url_enc"] = _encrypt_webhook(req.webhook_url)
            config["webhook_masked"] = _mask_webhook(req.webhook_url)
        ic = svc.create_config(
            integration_type="feishu", name=req.name,
            config=config, credential_ref=req.credential_ref,
        )
    return SuccessEnvelope(data=IntegrationSummaryService.to_response(ic),
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.delete("/feishu/{config_id}")
async def delete_feishu_config(config_id: str, db: Session = Depends(_db)):
    svc = IntegrationSummaryService(db)
    ok = svc.delete_config(config_id)
    if not ok:
        raise HTTPException(404, f"Feishu config {config_id} not found")
    return SuccessEnvelope(data={"deleted": True},
                           meta=Meta(source_status="real", capability_status="available"))


@integration_router.post("/feishu/test")
async def test_feishu(db: Session = Depends(_db)):
    """Send a test notification via configured Feishu webhook."""
    svc = IntegrationSummaryService(db)
    config = svc.get_by_type("feishu")
    if config is None:
        return SuccessEnvelope(
            data={"sent": False, "reason": "Feishu not configured"},
            meta=Meta(source_status="not_configured", capability_status="not_connected",
                      not_connected_reason="Feishu webhook not configured"),
        )
    webhook_url = _decrypt_webhook(config.config or {})
    if not webhook_url:
        return SuccessEnvelope(
            data={"sent": False, "reason": "Webhook URL not set"},
            meta=Meta(source_status="configured_not_verified", capability_status="not_connected"),
        )
    # Send test message
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={
                "msg_type": "text",
                "content": {"text": "🤖 rebuild 平台飞书集成测试通知 —— 如果你收到此消息，说明飞书 Webhook 配置成功。"},
            })
            success = resp.status_code == 200
            get_services().trace_writer.write("state_change", action="feishu_test",
                                               summary=f"Feishu test: {'OK' if success else 'FAIL'}")
            return SuccessEnvelope(data={"sent": success, "status_code": resp.status_code},
                                   meta=Meta(source_status="real", capability_status="available"))
    except Exception as e:
        get_services().trace_writer.write("state_change", action="feishu_test",
                                           summary=f"Feishu test error: {str(e)[:100]}")
        return SuccessEnvelope(data={"sent": False, "error": str(e)[:200]},
                               meta=Meta(source_status="error", capability_status="available"))


# ── Aggregate Summary ────────────────────────────────────

@integration_router.get("/summary")
async def integration_summary(db: Session = Depends(_db)):
    """Aggregate summary of all integration types for overview page."""
    svc = IntegrationSummaryService(db)
    summary = svc.get_summary()
    return SuccessEnvelope(data=summary, meta=Meta(source_status="real", capability_status="available"))
