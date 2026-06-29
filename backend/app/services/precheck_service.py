"""Precheck service — pre-create validation for project onboarding (R9-5-8 T10).

Field-level checks run BEFORE a project is created (nothing is persisted). The
guide calls this so the user gets actionable feedback up front instead of a
silent deferred/blocked materialization later. Per Q-B: advisory by default, but
a Git source with no usable credential is a hard block (passed=False) — aligning
with "git failures should be blocked at create/precheck, not faked as success".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    field: str
    status: str   # ok | warn | error
    message: str


@dataclass
class PrecheckResult:
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [{"field": c.field, "status": c.status, "message": c.message}
                       for c in self.checks],
        }


_VALID_SOURCE_TYPES = {"local_dir", "git", "zip", "github", "manual"}


def run_precheck(name: str | None, source_type: str | None,
                 source_config: dict | None, *, services=None) -> PrecheckResult:
    """Validate create parameters. Returns field-level checks; passed=False only
    on a hard error (empty name / invalid source_type / Git without credential)."""
    checks: list[CheckResult] = []
    source_config = source_config or {}
    st = (source_type or "").lower()

    # name non-empty
    if not (name or "").strip():
        checks.append(CheckResult("name", "error", "项目名称不能为空"))
    else:
        checks.append(CheckResult("name", "ok", "名称有效"))

    # source_type valid
    if st not in _VALID_SOURCE_TYPES:
        checks.append(CheckResult("source_type", "error", f"未知来源类型：{source_type!r}"))
    else:
        checks.append(CheckResult("source_type", "ok", f"来源类型：{st}"))

    # Git/GitHub: credential usability is a HARD block (Q-B) — fail fast at create
    if st in ("git", "github"):
        remote = source_config.get("remote_url") or source_config.get("clone_url")
        if not remote:
            checks.append(CheckResult("source_config", "error",
                                      "远端 Git 缺少仓库地址（remote_url/clone_url）"))
        elif not _git_credential_available(source_config, services):
            checks.append(CheckResult("source_config", "error",
                                      "远端 Git 未配置可用凭据，无法克隆——请先在集成页绑定 Git 账号"))
        else:
            checks.append(CheckResult("source_config", "ok", "Git 来源与凭据可用"))

    # local_dir: warn on sensitive roots (materializer blocks them anyway)
    if st == "local_dir":
        path = (source_config.get("path") or "").rstrip("/")
        if path in ("", "/", "/etc", "/root", "/usr", "/bin", "/sys", "/proc"):
            checks.append(CheckResult("source_config", "error",
                                      f"本地路径不可用或为敏感根目录：{path or '(空)'}"))
        else:
            checks.append(CheckResult("source_config", "ok", "本地路径有效"))

    passed = not any(c.status == "error" for c in checks)
    return PrecheckResult(passed=passed, checks=checks)


def _git_credential_available(source_config: dict, services) -> bool:
    """Best-effort credential probe. A local path / file remote needs none.
    A bound git_host_id or an existing connected Git account counts as available."""
    remote = (source_config.get("remote_url") or source_config.get("clone_url") or "")
    # local path remote (e.g. test repos) needs no credential
    if remote and not remote.startswith(("http://", "https://", "git@", "ssh://")):
        return True
    if source_config.get("git_host_id") or source_config.get("credential_ref"):
        return True
    # any connected Git account makes remote clone feasible
    try:
        from app.services.git_oauth_service import GitOAuthService  # type: ignore
        accounts = GitOAuthService().list_accounts()  # may raise if unavailable
        return bool(accounts)
    except Exception:
        return False
