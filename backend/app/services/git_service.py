"""Git host service — DB-backed Git repository configuration management."""

import os
import asyncio
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.models.git_host import GitHost, GitHostStatus, GitPlatform
from app.core.config import settings


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
    """Run a git command via subprocess and return structured result."""
    cmd = ["git"] + list(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return {
            "exit_code": proc.returncode or 0,
            "stdout": stdout.decode("utf-8", errors="replace")[:65536],
            "stderr": stderr.decode("utf-8", errors="replace")[:65536],
            "success": proc.returncode == 0,
        }
    except asyncio.TimeoutError:
        return {"exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s", "success": False}
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "", "stderr": "git command not found", "success": False}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e), "success": False}
