"""Git host service — DB-backed Git repository configuration management."""

import os
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.models.git_host import GitHost, GitHostStatus, GitPlatform
from app.core.config import settings
from app.services.subprocess_runner import run_subprocess_command


class GitService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, name: str, repo_url: str, platform: str = "other",
               auth_type: str = "https_token", credential_ref: str = None,
               default_branch: str = "main") -> GitHost:
        # Derive masked_url: strip userinfo from HTTPS URLs
        masked = repo_url
        if "@" in repo_url and "https://" in repo_url:
            # https://user:token@github.com/org/repo.git → https://github.com/org/repo.git
            proto = "https://"
            rest = repo_url[len(proto):]
            if "@" in rest:
                masked = proto + rest.split("@", 1)[1]
        gh = GitHost(
            name=name,
            repo_url=repo_url,
            masked_url=masked,
            platform=GitPlatform(platform) if platform in [e.value for e in GitPlatform] else GitPlatform.other,
            auth_type=auth_type,
            credential_ref=credential_ref,
            default_branch=default_branch or "main",
            status=GitHostStatus.not_configured,
            clone_path=_clone_path_for(repo_url, name),
        )
        self.db.add(gh)
        self.db.commit()
        self.db.refresh(gh)
        return gh

    def get(self, git_host_id: str) -> Optional[GitHost]:
        return self.db.get(GitHost, git_host_id)

    def list_all(self) -> list[GitHost]:
        return self.db.query(GitHost).order_by(GitHost.created_at.desc()).all()

    def update(self, git_host_id: str, **fields) -> Optional[GitHost]:
        gh = self.get(git_host_id)
        if gh is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(gh, k):
                setattr(gh, k, v)
        gh.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(gh)
        return gh

    def delete(self, git_host_id: str) -> bool:
        gh = self.get(git_host_id)
        if gh is None:
            return False
        self.db.delete(gh)
        self.db.commit()
        return True

    @staticmethod
    def to_response(gh: GitHost) -> dict:
        return {
            "git_host_id": gh.git_host_id,
            "name": gh.name,
            "repo_url": gh.masked_url or gh.repo_url.split("@")[-1] if "@" in gh.repo_url else gh.repo_url,
            "platform": gh.platform.value if hasattr(gh.platform, 'value') else gh.platform,
            "auth_type": gh.auth_type,
            "credential_ref": gh.credential_ref,
            "default_branch": gh.default_branch or "main",
            "status": gh.status.value if hasattr(gh.status, 'value') else gh.status,
            "last_connected_at": gh.last_connected_at.isoformat() if gh.last_connected_at else None,
            "created_at": gh.created_at.isoformat() if gh.created_at else "",
            "updated_at": gh.updated_at.isoformat() if gh.updated_at else "",
            "enabled": gh.enabled,
            "source_status": "real",
            "capability_status": "available" if gh.status == GitHostStatus.connected else "configured_not_verified",
        }

    def get_clone_path(self, git_host_id: str) -> str:
        gh = self.get(git_host_id)
        if gh and gh.clone_path:
            return gh.clone_path
        return _clone_path_for(f"repo-{git_host_id[:8]}", f"git-{git_host_id[:8]}")


def _clone_path_for(repo_url: str, name: str) -> str:
    """Determine local clone directory under data_dir/integration-repos/."""
    import hashlib
    slug = hashlib.sha256(name.encode()).hexdigest()[:12]
    base = getattr(settings, 'data_dir', os.path.join(os.getcwd(), '.data'))
    return os.path.join(base, "integration-repos", slug)


async def run_git_command(*args, cwd: str = None, timeout: int = 60,
                          env: dict = None) -> dict:
    """Run a git command via subprocess and return structured result.

    R21 实质缺陷修复：原实现只做 `asyncio.wait_for(proc.communicate(), timeout=…)`，
    超时取消的只是协程，**git 进程本身继续跑** —— 一次超时的 `git clone` 大仓库会留下
    一个持续下载的孤儿进程，占网络和磁盘，而 D-058 Git 接入流程会真实走到这条路径。
    现在改为复用 `subprocess_runner.run_subprocess_command`：超时按进程组
    SIGTERM + 宽限期 → SIGKILL 并等它确认停止（`git clone` 拉起的 `git remote-*`
    helper 等孙进程一并清掉）。**刻意不在此重写一套 SIGTERM/SIGKILL 逻辑** ——
    与 `ExecutionProvider` 共用同一份实现，避免第二份副本悄悄漂移
    （`B-R20-REDACT-THREE-IMPLS` 的教训）。

    返回契约：既有四个键的名称与语义**一字不变**（调用方众多，见
    `app/api/routes_integrations.py`）：
      `exit_code`（超时仍为 -1）/ `stdout` / `stderr` / `success`
    另**新增两个可选键**（纯新增，不改上述四键）：
      `timed_out`：是否因超时被终止 —— 此前调用方只能靠匹配 stderr 文本才能分辨
                   "超时"与"其它失败"（同 R21 循环卫生②口径）；
      `signal`：终止归因信号（从 POSIX 负 returncode 派生；未确认停止则诚实报 None，
                不编造）。

    `env=None` 表示继承宿主环境：git 需要 PATH / HOME / SSH agent 等才能工作，
    此处**不做** `_clean_env` 式清洗（与原行为一致，不顺手改语义）。
    """
    cmd = ["git"] + list(args)
    try:
        outcome = await run_subprocess_command(cmd, timeout=timeout, cwd=cwd, env=env)
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "", "stderr": "git command not found",
                "success": False, "timed_out": False, "signal": None}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e), "success": False,
                "timed_out": False, "signal": None}
    return {
        "exit_code": outcome.exit_code,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "success": outcome.exit_code == 0,
        "timed_out": outcome.timed_out,
        "signal": outcome.signal,
    }

