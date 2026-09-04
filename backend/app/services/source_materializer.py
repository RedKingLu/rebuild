"""Source Materializer — materialize project source into workspace/source/.

R9-3A: Backend service/tool capability for importing source code from
ZIP, Git, GitHub, local_dir, or manual (empty) into the per-project
workspace source/ directory. NOT terminal-script-driven.

All paths go through _guard. All actions write Trace (L2+ Audit).
Errors become Evidence Gaps, not silent failures.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_logger = logging.getLogger("rebuild.source_materializer")

from app.core.config import settings
from app.services.workspace_service import workspace_path, _guard


# Directories/files to skip during import
SKIP_PATTERNS = {
    ".git", "node_modules", "vendor", "__pycache__", ".venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt",
}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10MB — mark as too_large, don't read content

# local_dir import: refuse system/sensitive roots to avoid mis-importing secrets (R9-3F).
SENSITIVE_LOCAL_ROOTS = {
    "/", "/etc", "/root", "/boot", "/sys", "/proc", "/dev",
    "/var", "/usr", "/bin", "/sbin", "/lib", "/lib64",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── R17.4-2 WP-A/WP-D: deterministic git / repository metadata helpers ───────
# The clone may carry an injected auth token in the remote URL (see _with_git_auth
# → x-access-token:...@github.com). We NEVER surface that token: remote URLs are
# credential-stripped before they enter any artifact/trace (公理3 脱敏红线).

def _sanitize_git_remote(url: str | None) -> str | None:
    """Strip embedded credentials (userinfo) from a git remote URL.

    ``https://x-access-token:<token>@github.com/o/r.git`` → ``https://github.com/o/r.git``.
    scp-like ``git@host:path`` (no secret) is returned unchanged.
    """
    if not url:
        return url
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/@]*@)?(.*)$", url)
    if m:
        return f"{m.group(1)}{m.group(3)}"
    return url


def _git_str(target: Path, *args: str, timeout: int = 15) -> str | None:
    """Run a read-only git command, return stripped stdout or None (never raises)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(target), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        _logger.debug("git %s failed for %s (non-fatal)", args, target, exc_info=True)
    return None


def _capture_repository_metadata(target: Path) -> dict | None:
    """Deterministic repository metadata for source_index / intake.

    Returns {commit, short_commit, branch, remote(sanitized), working_tree_status,
    shallow, repo_size_bytes}. Missing values are set to None (evidence honesty —
    never fabricated). Returns None when the source is not a git checkout.
    """
    if not (target / ".git").exists():
        return None
    commit = _git_str(target, "rev-parse", "HEAD")
    if not commit:
        return None
    branch = _git_str(target, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch if (branch and branch != "HEAD") else None
    remote_raw = _git_str(target, "remote", "get-url", "origin")
    remote = _sanitize_git_remote(remote_raw) if remote_raw else None
    status = _git_str(target, "status", "--short")
    working_tree_status = (
        "clean" if status == "" else ("dirty" if status is not None else None)
    )
    shallow_raw = _git_str(target, "rev-parse", "--is-shallow-repository")
    shallow = {"true": True, "false": False}.get(shallow_raw) if shallow_raw is not None else None
    repo_size_bytes = None
    try:
        du = subprocess.run(["du", "-sb", str(target)], capture_output=True,
                            text=True, timeout=20)
        if du.returncode == 0:
            repo_size_bytes = int(du.stdout.split()[0])
    except Exception:
        _logger.debug("repo size (du) failed for %s (non-fatal)", target, exc_info=True)
    return {
        "commit": commit,
        "short_commit": commit[:8],
        "branch": branch,
        "remote": remote,
        "working_tree_status": working_tree_status,
        "shallow": shallow,
        "repo_size_bytes": repo_size_bytes,
    }


# SQL dialect markers (generic, extension/pattern based — NOT hardcoded to any project).
_SQL_DIALECT_MARKERS = [
    ("SQL Server (T-SQL)", (r"\[dbo\]", r"\bIDENTITY\s*\(", r"SET\s+ANSI_NULLS",
                            r"\bGETDATE\s*\(", r"\bNVARCHAR\b", r"\bGO\b")),
    ("MySQL", (r"\bAUTO_INCREMENT\b", r"\bENGINE\s*=", r"`\w+`")),
    ("Oracle", (r"\bVARCHAR2\b", r"\bNUMBER\s*\(", r"\bNVL\s*\(")),
    ("PostgreSQL", (r"\bSERIAL\b", r"\bnextval\s*\(", r"\bBYTEA\b")),
]


def _detect_sql_encoding(raw: bytes) -> tuple[str, bytes]:
    """Detect encoding from a BOM and return (encoding_label, decode_encoding)."""
    if raw.startswith(b"\xff\xfe"):
        return "UTF-16LE (BOM)", "utf-16"
    if raw.startswith(b"\xfe\xff"):
        return "UTF-16BE (BOM)", "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "UTF-8 (BOM)", "utf-8-sig"
    return "UTF-8", "utf-8"


def _analyze_sql_file(path: Path, size_bytes: int) -> dict:
    """Deterministic SQL entry analysis: encoding / dialect / table & insert counts.

    Field-level schema profiling (per-table columns/types) is explicitly deferred
    to P1 (deep_schema_profiling). Values are measured, never guessed.
    """
    info: dict = {
        "size_bytes": size_bytes,
        "encoding": None,
        "dialect": None,
        "create_table_count": None,
        "insert_count": None,
        "deep_schema_profiling": "NOT_DONE_IN_P0 (留 P1)",
    }
    if size_bytes > MAX_FILE_BYTES:
        info["analysis_gap"] = f"file exceeds {MAX_FILE_BYTES} bytes; counts skipped"
        # still detect encoding from the head bytes only
        try:
            head = path.open("rb").read(4)
            info["encoding"], _ = _detect_sql_encoding(head)
        except Exception:
            _logger.debug("sql head read failed for %s (non-fatal)", path, exc_info=True)
        return info
    try:
        raw = path.read_bytes()
    except Exception:
        _logger.warning("SQL 文件读取失败 %s（analysis_gap）", path, exc_info=True)
        info["analysis_gap"] = "read_failed"
        return info
    enc_label, dec = _detect_sql_encoding(raw)
    info["encoding"] = enc_label
    try:
        text = raw.decode(dec, errors="replace")
    except Exception:
        text = raw.decode("utf-8", errors="replace")
    # create_table_count / insert_count — occurrence counts (not line counts; the
    # dump packs many statements per very-long line). INSERT is matched without a
    # mandatory INTO because T-SQL emits ``INSERT [dbo].[t]``.
    info["create_table_count"] = len(re.findall(r"CREATE\s+TABLE", text, re.IGNORECASE))
    info["insert_count"] = len(re.findall(r"\bINSERT\b", text, re.IGNORECASE))
    for dialect, markers in _SQL_DIALECT_MARKERS:
        if any(re.search(mk, text, re.IGNORECASE) for mk in markers):
            info["dialect"] = dialect
            break
    # R17.5 P2 补充（item 5a，确定性采集喂 P2 LLM 评估 DB 迁移方言差异，不做识别裁决）：
    # 字段级方言标记计数（IDENTITY/GETDATE/[dbo].schema/NVARCHAR/AUTO_INCREMENT）+ 外键计数
    # （0 FK 是达梦/金仓迁移常见特征）。仅计数，方言差异解读/目标库选型交 P2 LLM（AGENTS §2.3）。
    info["dialect_features"] = {
        "identity_count": len(re.findall(r"\bIDENTITY\s*\(", text, re.IGNORECASE)),
        "getdate_count": len(re.findall(r"\bGETDATE\s*\(|\bGETUTCDATE\s*\(", text, re.IGNORECASE)),
        "dbo_schema_count": len(re.findall(r"\[dbo\]\.", text, re.IGNORECASE)),
        "nvarchar_count": len(re.findall(r"\bNVARCHAR\b", text, re.IGNORECASE)),
        "auto_increment_count": len(re.findall(r"\bAUTO_INCREMENT\b", text, re.IGNORECASE)),
        "foreign_key_count": len(re.findall(r"\bFOREIGN\s+KEY\b", text, re.IGNORECASE)),
    }
    return info


# R17.5 WP-2/WP-3 (GAP-P0-10 / HC-04): entry-point IDENTIFICATION moved to the P0
# LLM (Node Worker Agent). The collection layer no longer classifies entry points via
# a hardcoded name whitelist (which false-positived on vendored index.js/main.js).
# Collection surfaces the raw candidate signals — key_files / top_level_dirs /
# code_scale.extension_counts — and the LLM judges the real entry points at P0.
# NOTE: `ENTRY_POINT_NAMES` intentionally removed (no采集层 pattern/白名单, §2.3).


class SourceMaterializer:
    """Handles source code import into workspace/source/ for all source types."""

    def __init__(self, trace_writer=None, audit_writer=None):
        self.trace_writer = trace_writer
        self.audit_writer = audit_writer

    def materialize(self, project_id: str, source_type: str,
                    source_config: dict | None = None,
                    force_refresh: bool = False, run_id: str | None = None) -> dict:
        """Main entry point. Returns import_result dict.

        Result keys: success, source_type, file_count, dir_count,
        warnings, errors, evidence_gaps, materialization_status,
        materialization_mode, git_info.

        REC-06 (R17.3-6 WP-7): for git/github, when the source was already
        materialized and the source config (url/branch) is unchanged, the
        existing source is REUSED after a real HEAD-commit integrity check
        rather than unconditionally rmtree+full-clone. ``force_refresh=True``
        forces a full re-clone. Reuse / fetch-reset / clone are each recorded
        in Trace/Audit; failures are surfaced explicitly (never silent).
        """
        source_type = source_type or "manual"
        source_config = source_config or {}
        ws_source = workspace_path(project_id) / "source"
        ws_source.mkdir(parents=True, exist_ok=True)

        result = {
            "success": False,
            "source_type": source_type,
            "file_count": 0,
            "dir_count": 0,
            "warnings": [],
            "errors": [],
            "evidence_gaps": [],
            "materialization_status": "pending",
            "materialization_mode": "clone",  # clone | reuse | fetch_reset (git/github)
            "git_info": None,
            "materialized_at": _now(),
        }

        try:
            if source_type == "zip":
                self._materialize_zip(project_id, source_config, ws_source, result)
            elif source_type == "git":
                self._materialize_git(project_id, source_config, ws_source, result,
                                      force_refresh=force_refresh)
            elif source_type == "github":
                self._materialize_github(project_id, source_config, ws_source, result,
                                         force_refresh=force_refresh)
            elif source_type == "local_dir":
                self._materialize_local(project_id, source_config, ws_source, result)
            elif source_type == "manual":
                self._materialize_manual(project_id, result)
            else:
                result["errors"].append(f"Unsupported source_type: {source_type}")
                result["materialization_status"] = "failed"
        except Exception as e:
            result["errors"].append(str(e))
            result["materialization_status"] = "failed"

        if result["errors"]:
            # Failure (or partial if some files made it in)
            result["materialization_status"] = "partial" if result["file_count"] > 0 else "failed"
        elif result["materialization_status"] in ("deferred", "empty"):
            # Honest terminal states set by the per-type materializer (git/github
            # need credentials → deferred; manual has no source → empty). These
            # MUST NOT be rewritten to "completed" (R9-3F honesty fix).
            pass
        else:
            result["success"] = True
            result["materialization_status"] = "completed"
            # Count files after import
            file_count, dir_count = self._count_files(ws_source)
            result["file_count"] = file_count
            result["dir_count"] = dir_count

        # REC-06: persist a small materialization metadata record so that
        # generate_source_index() can honestly inherit source_type + git_info,
        # and a subsequent materialize() can decide reuse vs re-clone. Contains
        # NO url/token — only a config fingerprint hash + commit/branch. Never
        # written for a hard failure (nothing was materialized).
        if result["materialization_status"] != "failed":
            self._write_materialization_meta(project_id, source_type, source_config, result)

        self._write_trace(project_id, source_type, result, run_id=run_id)
        return result

    # ── per-type materializers ──────────────────────────────────────────

    def _materialize_zip(self, project_id: str, config: dict,
                         target: Path, result: dict):
        """Copy ZIP contents from upload extraction dir to workspace/source/."""
        extracted_path = config.get("extracted_path")
        if not extracted_path or not os.path.isdir(extracted_path):
            result["errors"].append(f"ZIP extracted_path not found: {extracted_path}")
            result["evidence_gaps"].append({
                "type": "zip_extraction_missing",
                "detail": f"Upload ZIP was not extracted to expected path: {extracted_path}",
            })
            result["materialization_status"] = "failed"
            return

        try:
            self._copy_tree_safe(project_id, Path(extracted_path), target, result)
        except Exception as e:
            result["errors"].append(f"ZIP import failed: {e}")

    def _materialize_git(self, project_id: str, config: dict,
                         target: Path, result: dict, force_refresh: bool = False):
        """Clone git repo into workspace/source/ via real git clone (R9-3G fix).

        REC-06 (WP-7): if the source was already materialized with the SAME
        source config (url/branch fingerprint) and it passes a real HEAD-commit
        integrity check, the existing source is REUSED (no rmtree, no clone).
        If the config is unchanged but the on-disk source drifted from the
        recorded commit, a real ``git fetch`` + ``reset --hard`` reconciles it.
        ``force_refresh``, a changed config, or a failed reconcile fall back to
        a full clone. Errors are surfaced explicitly (never silent).
        """
        import logging
        _log = logging.getLogger("uvicorn")

        # Try urls in priority order: remote_url → clone_url
        primary = config.get("remote_url")
        fallback = config.get("clone_url")
        urls = [u for u in (primary, fallback) if u] if primary and fallback and primary != fallback else [primary or fallback]

        if not urls:
            result["errors"].append("Git source_config missing remote_url/clone_url")
            result["evidence_gaps"].append({
                "type": "git_url_missing", "detail": "No remote URL in source_config",
            })
            result["materialization_status"] = "failed"
            return

        branch = config.get("branch", "main")
        fingerprint = self._config_fingerprint("git", config)

        # ── REC-06: try reuse / fetch-reset before a full clone ──────────
        if not force_refresh:
            reuse = self._try_reuse_git_source(project_id, "git", fingerprint, branch, target, result)
            if reuse:
                return

        # ── Full clone path (force_refresh / config changed / no valid prior) ──
        ok, detail = False, ""
        for url_attempt in urls:
            _log.info(f"Git materialize: url={url_attempt[:80]}... branch={branch}")
            auth_url = self._with_git_auth(url_attempt)
            _log.info(f"Git materialize: auth_url={'***' if auth_url else 'None (no auth)'}")
            ok, detail = self._run_git_clone(auth_url or url_attempt, branch, target)
            _log.info(f"Git materialize: url={url_attempt[:60]}... ok={ok}, detail={detail[:200]}")
            if ok:
                # Verify clone actually produced source files (not just an empty repo)
                fc, _ = self._count_files(target)
                if fc == 0 and len(urls) > 1 and url_attempt != urls[-1]:
                    _log.warning(f"Git materialize: clone ok but 0 source files from {url_attempt[:60]}..., trying next url")
                    continue  # Try fallback URL — this repo might be empty
                result["warnings"].append(f"Git clone succeeded: {url_attempt[:60]}... (branch={branch}, files={fc})")
                result["materialization_status"] = "completed"
                result["materialization_mode"] = "clone"
                result["git_info"] = self._capture_git_info(target)
                self._voice_branch_mismatch(branch, result, _log)
                break
            else:
                # Non-branch errors on first URL — try fallback URL before giving up
                if "Remote branch" not in detail and "not found in upstream" not in detail:
                    break  # Real error (auth/permissions/etc.), don't waste time on fallback

        if not ok:
            result["warnings"].append(f"Git clone failed: {detail}")
            result["evidence_gaps"].append({
                "type": "git_clone_failed",
                "detail": f"Git clone failed: {detail}. Manual import required.",
                "urls_tried": urls, "branch": branch,
            })
            result["materialization_status"] = "deferred"

    def _materialize_github(self, project_id: str, config: dict,
                            target: Path, result: dict, force_refresh: bool = False):
        """Clone GitHub repo — same as git; OAuth token used if available.

        REC-06: reuse / fetch-reset / force_refresh honoured identically to git.
        """
        clone_url = config.get("clone_url")
        if not clone_url:
            result["errors"].append("GitHub source_config missing clone_url")
            result["evidence_gaps"].append({
                "type": "github_url_missing", "detail": "No clone_url in source_config",
            })
            result["materialization_status"] = "failed"
            return

        branch = config.get("branch", "main")
        fingerprint = self._config_fingerprint("github", config)

        if not force_refresh:
            reuse = self._try_reuse_git_source(project_id, "github", fingerprint, branch, target, result)
            if reuse:
                return

        auth_url = self._with_git_auth(clone_url)
        ok, detail = self._run_git_clone(auth_url or clone_url, branch, target)
        if ok:
            result["warnings"].append(f"GitHub clone succeeded: {clone_url[:60]}... (branch={branch})")
            result["materialization_status"] = "completed"
            result["materialization_mode"] = "clone"
            result["git_info"] = self._capture_git_info(target)
            self._voice_branch_mismatch(branch, result, None)
        else:
            result["warnings"].append(f"GitHub clone failed: {detail}")
            result["evidence_gaps"].append({
                "type": "github_clone_failed",
                "detail": f"GitHub clone of {clone_url[:80]}... (branch={branch}) "
                          f"failed: {detail}. Manual import required.",
                "branch": branch,
            })
            result["materialization_status"] = "deferred"

    # ── REC-06: reuse / fetch-reset / integrity helpers ─────────────────

    def _already_materialized(self, project_id: str, source_type: str,
                              config: dict) -> bool:
        """Belt-and-suspenders reuse check for callers (e.g. RealP0Handler) that
        want to skip materialize() entirely when source is already on disk.

        Returns True only when ALL of: (a) source/ has files, (b) materialization
        meta exists, (c) config fingerprint matches, (d) on-disk HEAD commit
        matches the recorded commit.  Any uncertainty → False (caller will
        materialize, which has its own safe reuse path).
        """
        try:
            if source_type not in ("git", "github"):
                return False  # only git/github have meaningful reuse semantics
            prior = self._read_materialization_meta(project_id)
            if not prior:
                return False
            if prior.get("config_fingerprint") != self._config_fingerprint(source_type, config):
                return False
            target = workspace_path(project_id) / "source"
            if not (target.exists() and (target / ".git").exists()):
                return False
            fc, _ = self._count_files(target)
            if fc == 0:
                return False
            recorded = (prior.get("git_info") or {}).get("commit")
            current = self._git_head_commit(target)
            if not recorded or not current:
                return False
            return recorded == current
        except Exception:
            return False  # any doubt → let materialize() handle it

    def _try_reuse_git_source(self, project_id: str, source_type: str,
                              fingerprint: str, branch: str, target: Path,
                              result: dict) -> bool:
        """Attempt to reuse already-materialized git source without re-cloning.

        Returns True if the source was reused OR reconciled via fetch/reset
        (result populated accordingly); False when the caller must fall back to
        a full clone. NEVER reads/outputs .git/config contents — only queries
        the HEAD commit via ``git rev-parse`` (real integrity check).
        """
        import logging
        _log = logging.getLogger("uvicorn")

        prior = self._read_materialization_meta(project_id)
        if not prior:
            return False  # never materialized → full clone
        if prior.get("config_fingerprint") != fingerprint:
            _log.info("Git materialize: source config changed → full re-clone")
            result["warnings"].append("Git 源配置已变更（url/branch），执行全量重新 clone")
            return False
        if not (target.exists() and (target / ".git").exists()):
            _log.info("Git materialize: prior meta present but source/.git missing → full clone")
            return False

        fc, _ = self._count_files(target)
        if fc == 0:
            return False  # empty working tree → full clone

        recorded = (prior.get("git_info") or {}).get("commit")
        current = self._git_head_commit(target)
        if current is None:
            _log.info("Git materialize: cannot read HEAD commit → full clone")
            return False

        if recorded and current == recorded:
            # Integrity check passed: on-disk HEAD matches the recorded commit.
            result["materialization_status"] = "completed"
            result["materialization_mode"] = "reuse"
            result["git_info"] = self._capture_git_info(target)
            result["warnings"].append(
                f"Git 源已物化且配置/commit 未变（commit={current[:8]}），复用已物化 source（未重新 clone）")
            _log.info(f"Git materialize: REUSE existing source (commit={current[:8]}, files={fc})")
            return True

        # Config unchanged but on-disk source drifted from the recorded commit
        # → reconcile via real fetch + reset --hard (uses the origin remote
        # already configured in the clone; we do not read its config).
        _log.info(f"Git materialize: source drift (recorded={str(recorded)[:8]} current={current[:8]}) → fetch/reset")
        ok, detail = self._git_fetch_reset(target, branch)
        if ok:
            result["materialization_status"] = "completed"
            result["materialization_mode"] = "fetch_reset"
            result["git_info"] = self._capture_git_info(target)
            result["warnings"].append(f"Git 源已物化，配置未变但发生漂移，已 fetch/reset 复位（{detail}）")
            return True
        # Reconcile failed → fall back to full clone (surfaced as warning).
        result["warnings"].append(f"Git fetch/reset 复位失败（{detail}），回退全量重新 clone")
        return False

    @staticmethod
    def _config_fingerprint(source_type: str, config: dict) -> str:
        """Stable hash of (source_type, canonical_url, branch). Stores NO raw url/token.

        D-107 fix: use ONLY clone_url (the canonical source URL) for fingerprinting.
        ``remote_url`` is a try-first probe URL that may differ from the actual
        materialized repo; including it makes the fingerprint unstable across
        graph resume (first run may lack remote_url, resume includes it → mismatch
        → unnecessary re-clone).  Branch IS part of the identity."""
        import hashlib
        url = config.get("clone_url") or config.get("remote_url") or config.get("path") or ""
        branch = config.get("branch", "")
        raw = f"{source_type}|{url}|{branch}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _git_head_commit(target: Path) -> str | None:
        """Return the HEAD commit SHA of the materialized source, or None.

        Reads only the commit id via ``git rev-parse HEAD`` — never touches
        .git/config (which may contain an injected token).
        """
        import subprocess
        try:
            proc = subprocess.run(
                ["git", "-C", str(target), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except Exception:
            _logger.debug("git rev-parse HEAD failed for %s (non-fatal)", target, exc_info=True)
        return None

    @staticmethod
    def _git_current_branch(target: Path) -> str | None:
        """Return the current branch name, or None (detached/unknown)."""
        import subprocess
        try:
            proc = subprocess.run(
                ["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                name = proc.stdout.strip()
                return name if name and name != "HEAD" else None
        except Exception:
            _logger.debug("git branch lookup failed for %s (non-fatal)", target, exc_info=True)
        return None

    def _capture_git_info(self, target: Path) -> dict | None:
        """Capture repository metadata for the index/meta. No token ever surfaced.

        R17.4-2 WP-A: enriched beyond {commit,short_commit,branch} to also carry
        remote(credential-stripped)/working_tree_status/shallow/repo_size_bytes.
        """
        return _capture_repository_metadata(target)

    @staticmethod
    def _voice_branch_mismatch(requested_branch: str, result: dict, log=None) -> None:
        """R17.4-2 WP-D (B-R17.4-P0-BRANCH-MISMATCH): when the configured branch
        differs from the branch actually checked out (a silent fallback to
        master/default), voice it explicitly (warning + branch_mismatch evidence_gap)
        instead of silently landing on the default branch (公理3)."""
        actual = (result.get("git_info") or {}).get("branch")
        if requested_branch and actual and requested_branch != actual:
            msg = (f"配置分支 branch='{requested_branch}' 与实际检出分支 '{actual}' 不一致"
                   f"（clone 回退到 '{actual}'）——未静默落默认分支，显式发声")
            result["warnings"].append(msg)
            result["evidence_gaps"].append({
                "type": "branch_mismatch",
                "detail": msg,
                "requested_branch": requested_branch,
                "actual_branch": actual,
            })
            if log is not None:
                log.warning("Git materialize: %s", msg)

    @staticmethod
    def _git_uncommitted_changes(target: Path) -> tuple[list[str], list[str], str | None]:
        """R19-3-02: inspect the working tree before any ``reset --hard``.

        Returns (tracked_changes, untracked_files, error). ``tracked_changes`` are the
        entries ``reset --hard`` would silently destroy (modified / staged / deleted /
        renamed / unmerged); ``untracked_files`` are reported but survive a reset.
        ``error`` is non-None when status could not be read at all — treated as
        "assume dirty" by the caller (公理3: unknown state is not a green light).
        """
        import subprocess
        try:
            proc = subprocess.run(
                ["git", "-C", str(target), "status", "--porcelain"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            return [], [], f"git status 无法读取: {str(e)[:200]}"
        if proc.returncode != 0:
            return [], [], f"git status exit {proc.returncode}: {(proc.stderr or '')[:200].strip()}"
        tracked, untracked = [], []
        for line in (proc.stdout or "").splitlines():
            if not line.strip():
                continue
            code, _, path = line[:2], line[2:3], line[3:]
            (untracked if code == "??" else tracked).append(path or line)
        return tracked, untracked, None

    @staticmethod
    def _git_fetch_reset(target: Path, branch: str) -> tuple[bool, str]:
        """Reconcile a drifted working tree via real ``git fetch`` + reset --hard.

        Uses the origin remote already stored in the clone (git resolves the
        stored URL itself; we never read .git/config). Returns (ok, detail).

        R19-3-02: ``reset --hard`` is destructive — it discards uncommitted tracked
        changes without asking. So the working tree is inspected FIRST; when there are
        uncommitted tracked modifications (or status cannot be read at all) the reset is
        REFUSED and (False, reason) is returned, which makes the caller fall back to a
        full re-clone instead of silently wiping the user's edits mid-flight.
        Untracked files are reported in the detail but do not block: ``reset --hard``
        leaves them in place, whereas forcing a full re-clone would remove them.
        """
        import subprocess
        tracked, untracked, status_err = SourceMaterializer._git_uncommitted_changes(target)
        if status_err:
            return False, f"reset 前未提交修改检查失败，拒绝 reset --hard（{status_err}）"
        if tracked:
            preview = ", ".join(tracked[:5]) + (f" 等 {len(tracked)} 项" if len(tracked) > 5 else "")
            return False, (f"检测到 {len(tracked)} 项未提交修改，拒绝 reset --hard"
                           f"（避免静默丢弃改动）：{preview}")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
        ref = branch or "HEAD"
        try:
            fetch = subprocess.run(
                ["git", "-C", str(target), "fetch", "--depth", "1", "origin", ref],
                capture_output=True, text=True, timeout=60, env=env,
            )
            if fetch.returncode != 0:
                return False, f"git fetch exit {fetch.returncode}: {(fetch.stderr or '')[:200].strip()}"
            reset = subprocess.run(
                ["git", "-C", str(target), "reset", "--hard", "FETCH_HEAD"],
                capture_output=True, text=True, timeout=30, env=env,
            )
            if reset.returncode != 0:
                return False, f"git reset exit {reset.returncode}: {(reset.stderr or '')[:200].strip()}"
            detail = "fetch+reset --hard FETCH_HEAD（工作树无未提交修改）"
            if untracked:
                detail += f"；保留 {len(untracked)} 个未跟踪文件"
            return True, detail
        except subprocess.TimeoutExpired:
            return False, "git fetch/reset timed out"
        except FileNotFoundError:
            return False, "git command not found on system PATH"
        except Exception as e:
            return False, f"git fetch/reset error: {str(e)[:200]}"

    def _materialization_meta_path(self, project_id: str) -> Path:
        return workspace_path(project_id) / ".rebuild" / "materialization.json"

    def _read_materialization_meta(self, project_id: str) -> dict | None:
        path = self._materialization_meta_path(project_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            # 发声：meta 损坏不静默——记录并当作无 meta（触发全量 clone，安全侧）。
            _logger.warning("materialization.json 损坏，按无 meta 处理 project=%s", project_id, exc_info=True)
            return None

    def _write_materialization_meta(self, project_id: str, source_type: str,
                                    config: dict, result: dict):
        """Persist materialization metadata (no url/token) for reuse + indexing."""
        path = self._materialization_meta_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "source_type": source_type,
            "config_fingerprint": self._config_fingerprint(source_type, config),
            "materialization_status": result.get("materialization_status"),
            "materialization_mode": result.get("materialization_mode"),
            "git_info": result.get("git_info"),
            "file_count": result.get("file_count", 0),
            "materialized_at": result.get("materialized_at"),
        }
        try:
            path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            # advisory：meta 写失败不阻断物化主链路，但记录以便定位。
            _logger.warning("写 materialization.json 失败 project=%s（advisory）", project_id, exc_info=True)

    # ── Git clone helpers ───────────────────────────────────────────────

    def _with_git_auth(self, url: str) -> str | None:
        """Return authenticated URL if a matching GitAccount token exists."""
        import logging
        _log = logging.getLogger("uvicorn")
        try:
            from app.services.git_oauth_service import GitAccountService
            from app.core.database import get_session
            db = get_session()
            try:
                svc = GitAccountService(db)
                accts = svc.list_all()
                _log.info(f"Git auth: found {len(accts)} account(s)")
                for acct in accts:
                    try:
                        token = svc.get_token(acct.account_id)
                    except Exception as tok_err:
                        _log.warning(f"Git auth: failed to decrypt token for {acct.username}: {tok_err}")
                        continue
                    if token:
                        if url.startswith("https://"):
                            authed = url.replace("https://", f"https://x-access-token:{token}@", 1)
                            _log.info(f"Git auth: token injected for {url[:60]}...")
                            return authed
            finally:
                db.close()
        except Exception as e:
            _log.warning(f"Git auth: exception while resolving token: {e}", exc_info=True)
        return None

    @staticmethod
    def _run_git_clone(url: str, branch: str, target: Path) -> tuple[bool, str]:
        """Execute git clone via subprocess. Returns (success, detail_message).

        Tries the requested branch first; falls back to 'master' if the branch
        doesn't exist; then tries the remote HEAD (no -b flag) as last resort.
        """
        import subprocess
        target.mkdir(parents=True, exist_ok=True)
        env = {**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}

        # Candidate branches to try, in order
        candidates = [branch] if branch == "master" else [branch, "master"]
        # Also try default (no -b) as last resort
        candidates.append(None)

        for attempt, cand in enumerate(candidates):
            # Clear target directory from previous failed attempt
            for item in list(target.iterdir()):
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception:
                    _logger.debug("Git clone cleanup: failed to remove %s (non-fatal)", item, exc_info=True)
            if cand:
                cmd = ["git", "clone", "--depth", "1", "--single-branch", "-b", cand, url, str(target)]
            else:
                cmd = ["git", "clone", "--depth", "1", url, str(target)]

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60, env=env,
                )
                if proc.returncode == 0:
                    actual_branch = cand or "default"
                    safe = url.split("@")[-1] if "@" in url else url
                    return True, f"Cloned {safe[:60]}... (branch={actual_branch}, attempt={attempt+1})"
                stderr_short = (proc.stderr or "")[:400].replace("\n", " ")
                stderr_short = stderr_short.replace(url, "<url>")
                if attempt < len(candidates) - 1:
                    # Only try next candidate if this looks like a "bad branch" error
                    if "Remote branch" in stderr_short or "not found in upstream" in stderr_short:
                        continue
                # Otherwise, this is a real error — don't retry
                return False, f"git clone exit {proc.returncode}: {stderr_short}"
            except subprocess.TimeoutExpired:
                return False, "Git clone timed out after 60 seconds"
            except FileNotFoundError:
                return False, "git command not found on system PATH"
            except Exception as e:
                return False, f"Git clone error: {str(e)[:300]}"

        return False, "Git clone failed: all branch attempts exhausted"

    def _materialize_local(self, project_id: str, config: dict,
                           target: Path, result: dict):
        """Copy or reference local directory into workspace/source/."""
        local_path = config.get("path")
        if not local_path or not os.path.isdir(local_path):
            result["errors"].append(f"local_dir path not found: {local_path}")
            result["evidence_gaps"].append({
                "type": "local_dir_not_found",
                "detail": f"Local directory does not exist: {local_path}",
            })
            result["materialization_status"] = "failed"
            return

        # Security (R9-3F): refuse to import system/sensitive roots.
        real = os.path.realpath(local_path)
        if real in SENSITIVE_LOCAL_ROOTS or any(
            real == r or real.startswith(r + os.sep) for r in SENSITIVE_LOCAL_ROOTS if r != "/"
        ):
            result["errors"].append(f"local_dir 拒绝导入敏感系统目录：{real}")
            result["evidence_gaps"].append({
                "type": "local_dir_sensitive_blocked",
                "detail": f"Refused to import sensitive/system directory: {real}",
            })
            result["materialization_status"] = "failed"
            return

        try:
            self._copy_tree_safe(project_id, Path(local_path), target, result)
        except Exception as e:
            result["errors"].append(f"local_dir import failed: {e}")

    def _materialize_manual(self, project_id: str, result: dict):
        """Manual project — source/ stays empty, honest Evidence Gap."""
        result["warnings"].append("Manual project: source/ is empty (honest).")
        result["evidence_gaps"].append({
            "type": "manual_empty_source",
            "detail": "Manual project has no source to import. User may upload later.",
        })
        result["materialization_status"] = "empty"
        result["success"] = True  # Empty is valid for manual

    # ── helpers ─────────────────────────────────────────────────────────

    def _copy_tree_safe(self, project_id: str, src: Path, dst: Path, result: dict):
        """Copy directory tree with path guard, skip/noise filtering, size limits."""
        for item in src.rglob("*"):
            # Skip filtered directories
            rel = item.relative_to(src)
            parts = rel.parts
            if any(p in SKIP_PATTERNS for p in parts):
                continue

            try:
                target_path = dst / rel
                # Path guard
                _guard(project_id, Path("source") / rel)
                if item.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    result["dir_count"] += 1
                elif item.is_file():
                    if item.stat().st_size > MAX_FILE_BYTES:
                        result["warnings"].append(f"File too large ({item.stat().st_size}B): {rel}")
                        continue
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target_path)
                    result["file_count"] += 1
            except PermissionError:
                result["warnings"].append(f"Path guard blocked: {rel}")
            except Exception as e:
                result["warnings"].append(f"Copy failed for {rel}: {e}")

    @staticmethod
    def _count_files(root: Path) -> tuple[int, int]:
        """Count files and dirs in root (excluding skipped patterns)."""
        fc, dc = 0, 0
        if not root.exists():
            return 0, 0
        for item in root.rglob("*"):
            if any(p in SKIP_PATTERNS for p in item.parts):
                continue
            if item.is_file():
                fc += 1
            elif item.is_dir():
                dc += 1
        return fc, dc

    def _write_trace(self, project_id: str, source_type: str, result: dict,
                     run_id: str | None = None):
        """Write Trace (and Audit for L2+) for materialization action.

        R17.4-2 WP-D (REC-R17.4-1): run_id is threaded through so the
        source_materialization trace/audit carry the run context (was None).
        """
        if self.trace_writer:
            self.trace_writer.write(
                "source_materialization",
                action="materialize",
                summary=f"Source materialization {result['materialization_status']} "
                        f"({source_type}, {result['file_count']} files, "
                        f"{len(result['errors'])} errors)",
                project_id=project_id,
                run_id=run_id,
                extras={
                    "source_type": source_type,
                    "status": result["materialization_status"],
                    "file_count": result["file_count"],
                    "error_count": len(result["errors"]),
                    "gap_count": len(result["evidence_gaps"]),
                },
            )
        if self.audit_writer and (
            source_type in ("git", "github") or result["errors"]
        ):
            self.audit_writer.write(
                audit_type="source_materialization",
                action="materialize",
                decision="executed",
                risk_level="L2" if source_type in ("git", "github") else "L1",
                project_id=project_id,
                run_id=run_id,
                reason=f"Source materialized: {source_type} → "
                       f"{result['materialization_status']}",
                extras={"source_type": source_type, "status": result["materialization_status"]},
            )


def generate_source_index(project_id: str, source_type: str | None = None) -> dict:
    """Generate a structured source_index.json for the project workspace.

    Scans workspace/source/ and produces a machine-readable index.
    Writes to artifacts/p0/source_index.json (D-107 分层文件夹).

    ISSUE-03 (WP-7): source_type is inherited from the real materialization
    metadata (git/zip/local_dir/…) instead of a hardcoded "unknown"; key_files
    additionally recognises .NET / SQL projects via generic endswith matching
    (.sln/.csproj/.vbproj/.fsproj/.config/packages.config/*.sql/appsettings.json)
    — NOT hardcoded to any specific project's filenames; git_info (commit/branch)
    is surfaced when the source is a git clone.

    R17.4-2 WP-A: enriched with deterministic, measured fields —
    entry_points (generic app-entry files), repository_metadata (git remote
    [credential-stripped]/status/shallow/size), database_files (per-.sql
    encoding/dialect/table & insert counts; field-level schema deferred to P1),
    and code_scale (extension count summary). All values measured, never guessed.
    """
    ws_source = workspace_path(project_id) / "source"
    # D-107: P0 采集产物写入 artifacts/p0/（分层文件夹，非扁平根）。
    artifacts_dir = workspace_path(project_id) / "artifacts" / "p0"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Inherit real source_type + git_info from materialization metadata.
    resolved_source_type = source_type or "unknown"
    git_info = None
    materialization_status = "unknown"
    meta_path = workspace_path(project_id) / ".rebuild" / "materialization.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if source_type is None and meta.get("source_type"):
                resolved_source_type = meta["source_type"]
            git_info = meta.get("git_info")
            materialization_status = meta.get("materialization_status") or "unknown"
        except Exception:
            # 发声：meta 损坏时不静默伪造 source_type，保留 unknown 并记录。
            _logger.warning("generate_source_index: materialization.json 损坏 project=%s", project_id, exc_info=True)

    # WP-A: prefer live on-disk repository metadata (fresh + full: remote/status/
    # shallow/size) when the source is a git checkout; fall back to the meta record.
    repository_metadata = _capture_repository_metadata(ws_source) or git_info

    index = {
        "project_id": project_id,
        "generated_at": _now(),
        "source_type": resolved_source_type,
        "materialization_status": materialization_status,
        "git_info": repository_metadata,
        "repository_metadata": repository_metadata,
        "file_count": 0,
        "directory_count": 0,
        "top_level_dirs": [],
        "key_files": [],
        # R17.5 WP-2/WP-3: entry-point identification is the P0 LLM's job now.
        # Collection leaves this empty (candidates = key_files/top_level_dirs/code_scale).
        "entry_points": [],
        "entry_points_note": "entry-point identification deferred to P0 LLM intake (采集不预判入口，LLM 据 key_files/结构/计数判定)",
        "database_files": [],
        "code_scale": {"extension_counts": {}, "total_code_files": 0},
        "skipped_dirs": [],
        "binary_files_count": 0,
        "too_large_files_count": 0,
    }

    if not ws_source.exists():
        index["materialization_status"] = "empty"
    else:
        fc, dc = 0, 0
        ext_counts: dict[str, int] = {}
        sql_paths: list[tuple[str, int]] = []
        for item in sorted(ws_source.rglob("*")):
            rel = str(item.relative_to(ws_source))
            parts = item.relative_to(ws_source).parts

            # Track skipped directories
            if any(p in SKIP_PATTERNS for p in parts):
                if item.is_dir() and parts[-1] in SKIP_PATTERNS:
                    index["skipped_dirs"].append(rel)
                continue

            if item.is_dir():
                dc += 1
                if len(parts) == 1:
                    index["top_level_dirs"].append(rel)
            elif item.is_file():
                fc += 1
                fname = parts[-1]
                # Track key files (generic build/manifest/config candidates for the LLM).
                if _is_key_file(fname):
                    index["key_files"].append(rel)
                # WP-A: extension-count summary (code_scale)
                ext = item.suffix.lower()
                if ext:
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
                # Track large/binary + collect .sql for DB entry analysis
                try:
                    size = item.stat().st_size
                    if size > MAX_FILE_BYTES:
                        index["too_large_files_count"] += 1
                    if ext == ".sql":
                        sql_paths.append((rel, size))
                except Exception:
                    _logger.debug("generate_source_index: stat failed for %s (skipping)", item, exc_info=True)

        index["file_count"] = fc
        index["directory_count"] = dc
        # WP-A: code_scale summary — top extensions by count (generic).
        index["code_scale"] = {
            "extension_counts": dict(sorted(ext_counts.items(), key=lambda kv: -kv[1])[:30]),
            "total_code_files": fc,
        }
        # WP-A: database_files entry-level analysis (cap at 20 .sql files).
        for rel, size in sql_paths[:20]:
            entry = {"path": rel}
            entry.update(_analyze_sql_file(ws_source / rel, size))
            index["database_files"].append(entry)
        if index["materialization_status"] == "unknown":
            index["materialization_status"] = "indexed"

    # Write to artifacts/p0/ (D-107)
    index_path = artifacts_dir / "source_index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


# ISSUE-03: key-file recognition. Exact names cover build/manifest files across
# stacks; endswith suffixes add .NET / SQL projects generically (NOT hardcoded to
# any specific project). Mirrors the P1 FullStackProfiler endswith范式.
KEY_FILE_NAMES = {
    "README.md", "Makefile", "Dockerfile", "package.json",
    "pom.xml", "build.gradle", "build.gradle.kts", "requirements.txt", "go.mod",
    "Cargo.toml", "pyproject.toml", "setup.py", "CMakeLists.txt",
    ".gitignore", "docker-compose.yml", "docker-compose.yaml",
    # .NET / config manifests (generic, extension- or exact-name-based)
    "packages.config", "appsettings.json", "web.config", "app.config",
    "global.asax", "nuget.config", "Directory.Build.props",
}
KEY_FILE_SUFFIXES = (
    ".sln", ".csproj", ".vbproj", ".fsproj",  # .NET project/solution files
    ".sql",                                     # database scripts
    ".config",                                  # .NET config (Web.Debug.config etc.)
)
# Case-insensitive lookup set (B-R17.4-P0-KEYFILE-CASE): .NET convention capitalises
# Web.config / Global.asax, which an exact-case match missed.
KEY_FILE_NAMES_LOWER = {n.lower() for n in KEY_FILE_NAMES}


def _is_key_file(filename: str) -> bool:
    """Return True for build/manifest/.NET/SQL key files (case-insensitive, generic)."""
    fl = filename.lower()
    if fl in KEY_FILE_NAMES_LOWER:
        return True
    return fl.endswith(KEY_FILE_SUFFIXES)
