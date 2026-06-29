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
import shutil
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


class SourceMaterializer:
    """Handles source code import into workspace/source/ for all source types."""

    def __init__(self, trace_writer=None, audit_writer=None):
        self.trace_writer = trace_writer
        self.audit_writer = audit_writer

    def materialize(self, project_id: str, source_type: str,
                    source_config: dict | None = None) -> dict:
        """Main entry point. Returns import_result dict.

        Result keys: success, source_type, file_count, dir_count,
        warnings, errors, evidence_gaps, materialization_status.
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
            "materialized_at": _now(),
        }

        try:
            if source_type == "zip":
                self._materialize_zip(project_id, source_config, ws_source, result)
            elif source_type == "git":
                self._materialize_git(project_id, source_config, ws_source, result)
            elif source_type == "github":
                self._materialize_github(project_id, source_config, ws_source, result)
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

        self._write_trace(project_id, source_type, result)
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
                         target: Path, result: dict):
        """Clone git repo into workspace/source/ via real git clone (R9-3G fix).

        Tries git clone with --depth 1 --single-branch. If an OAuth token is
        available from a linked GitAccount, uses it for authentication.
        On failure, honestly marks deferred with the specific error reason.
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
        ok, detail = False, ""
        used_url = ""
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
                used_url = url_attempt
                result["warnings"].append(f"Git clone succeeded: {url_attempt[:60]}... (branch={branch}, files={fc})")
                result["materialization_status"] = "completed"
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
                            target: Path, result: dict):
        """Clone GitHub repo — same as git; OAuth token used if available."""
        clone_url = config.get("clone_url")
        if not clone_url:
            result["errors"].append("GitHub source_config missing clone_url")
            result["evidence_gaps"].append({
                "type": "github_url_missing", "detail": "No clone_url in source_config",
            })
            result["materialization_status"] = "failed"
            return

        branch = config.get("branch", "main")
        auth_url = self._with_git_auth(clone_url)
        ok, detail = self._run_git_clone(auth_url or clone_url, branch, target)
        if ok:
            result["warnings"].append(f"GitHub clone succeeded: {clone_url[:60]}... (branch={branch})")
            result["materialization_status"] = "completed"
        else:
            result["warnings"].append(f"GitHub clone failed: {detail}")
            result["evidence_gaps"].append({
                "type": "github_clone_failed",
                "detail": f"GitHub clone of {clone_url[:80]}... (branch={branch}) "
                          f"failed: {detail}. Manual import required.",
                "branch": branch,
            })
            result["materialization_status"] = "deferred"

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
                        import shutil
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception:
                    _logger.debug("Git clone cleanup: failed to remove %s (non-fatal)", item, exc_info=True)
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

    def _write_trace(self, project_id: str, source_type: str, result: dict):
        """Write Trace (and Audit for L2+) for materialization action."""
        if self.trace_writer:
            self.trace_writer.write(
                "source_materialization",
                action="materialize",
                summary=f"Source materialization {result['materialization_status']} "
                        f"({source_type}, {result['file_count']} files, "
                        f"{len(result['errors'])} errors)",
                project_id=project_id,
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
                reason=f"Source materialized: {source_type} → "
                       f"{result['materialization_status']}",
                extras={"source_type": source_type, "status": result["materialization_status"]},
            )


def generate_source_index(project_id: str) -> dict:
    """Generate a structured source_index.json for the project workspace.

    Scans workspace/source/ and produces a machine-readable index.
    Writes to artifacts/source_index.json.
    """
    ws_source = workspace_path(project_id) / "source"
    artifacts_dir = workspace_path(project_id) / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    index = {
        "project_id": project_id,
        "generated_at": _now(),
        "source_type": "unknown",
        "materialization_status": "unknown",
        "file_count": 0,
        "directory_count": 0,
        "top_level_dirs": [],
        "key_files": [],
        "skipped_dirs": [],
        "binary_files_count": 0,
        "too_large_files_count": 0,
    }

    if not ws_source.exists():
        index["materialization_status"] = "empty"
    else:
        fc, dc = 0, 0
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
                # Track key files
                if parts[-1] in {
                    "README.md", "Makefile", "Dockerfile", "package.json",
                    "pom.xml", "build.gradle", "requirements.txt", "go.mod",
                    "Cargo.toml", "pyproject.toml", "CMakeLists.txt",
                    ".gitignore", "docker-compose.yml",
                }:
                    index["key_files"].append(rel)
                # Track large/binary
                try:
                    size = item.stat().st_size
                    if size > MAX_FILE_BYTES:
                        index["too_large_files_count"] += 1
                except Exception:
                    _logger.debug("generate_source_index: stat failed for %s (skipping)", item, exc_info=True)

        index["file_count"] = fc
        index["directory_count"] = dc
        index["materialization_status"] = "indexed"

    # Write to artifacts/
    index_path = artifacts_dir / "source_index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index
