"""Git OAuth service — GitHub/GitLab/Gitee OAuth flow + repo listing."""

import os
import httpx
from app.core.config import settings
from sqlalchemy.orm import Session

from app.models.git_account import GitAccount, GitAccountPlatform, AccountStatus
from app.security.byok_crypto import encrypt_api_key, decrypt_api_key, hash_api_key


# OAuth app credentials — from pydantic-settings (REBUILD_ prefix)
GITHUB_CLIENT_ID = settings.github_client_id
GITHUB_CLIENT_SECRET = settings.github_client_secret
GITHUB_REDIRECT_URI = settings.github_redirect_uri
FRONTEND_URL = settings.frontend_url


def get_github_authorize_url() -> str:
    """Generate the GitHub OAuth authorize URL."""
    import urllib.parse
    params = urllib.parse.urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "scope": "repo,user",
        "state": "rebuild-oauth",
    })
    return f"https://github.com/login/oauth/authorize?{params}"


async def exchange_github_code(code: str) -> dict | None:
    """Exchange OAuth code for access token. Returns {access_token, token_type, scope}."""
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        return None
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if "error" in data:
            return None
        return data  # {access_token, token_type, scope}


async def fetch_github_user(access_token: str) -> dict | None:
    """Fetch GitHub user info."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            return None
        return resp.json()


async def fetch_github_repos(access_token: str, page: int = 1, per_page: int = 30) -> list[dict]:
    """Fetch repos for the authenticated GitHub user."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://api.github.com/user/repos",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"per_page": per_page, "page": page, "sort": "updated", "type": "all"},
        )
        if resp.status_code != 200:
            return []
        repos = resp.json()
        return [
            {
                "repo_id": r["id"],
                "name": r["name"],
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "private": r["private"],
                "clone_url": r["clone_url"],
                "ssh_url": r["ssh_url"],
                "default_branch": r.get("default_branch", "main"),
                "language": r.get("language"),
                "updated_at": r.get("updated_at"),
                "stargazers_count": r.get("stargazers_count", 0),
            }
            for r in repos
        ]


class GitAccountService:
    def __init__(self, db: Session):
        self.db = db

    def create_account(self, platform: str, username: str,
                       access_token: str, avatar_url: str = None) -> GitAccount:
        fingerprint = hash_api_key(access_token)
        encrypted = encrypt_api_key(access_token, "default")
        acct = GitAccount(
            platform=GitAccountPlatform(platform),
            username=username,
            encrypted_token=encrypted,
            token_fingerprint=fingerprint,
            avatar_url=avatar_url,
            status=AccountStatus.connected,
        )
        self.db.add(acct)
        self.db.commit()
        self.db.refresh(acct)
        return acct

    def get(self, account_id: str) -> GitAccount | None:
        return self.db.get(GitAccount, account_id)

    def list_all(self) -> list[GitAccount]:
        return self.db.query(GitAccount).filter(
            GitAccount.enabled == True
        ).order_by(GitAccount.created_at.desc()).all()

    def delete(self, account_id: str) -> bool:
        acct = self.get(account_id)
        if acct is None:
            return False
        self.db.delete(acct)
        self.db.commit()
        return True

    def get_token(self, account_id: str) -> str | None:
        acct = self.get(account_id)
        if acct is None:
            return None
        return decrypt_api_key(acct.encrypted_token, acct.tenant_id)

    @staticmethod
    def to_response(acct: GitAccount) -> dict:
        return {
            "account_id": acct.account_id,
            "platform": acct.platform.value if hasattr(acct.platform, 'value') else acct.platform,
            "username": acct.username,
            "avatar_url": acct.avatar_url,
            "status": acct.status.value if hasattr(acct.status, 'value') else acct.status,
            "created_at": acct.created_at.isoformat() if acct.created_at else "",
            "source_status": "real",
            "capability_status": "available",
        }
