"""AET service — Artifact, Evidence, Trace, Audit queries.

R9-3G P0-3: Artifacts and Evidence Gaps are now read from the real
workspace filesystem (artifacts/ directory + uncertainty_manifest.json).
Traces and Audits come from TraceWriter/AuditWriter.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from app.services.workspace_service import workspace_path

logger = logging.getLogger("rebuild.aet_service")


class AETService:
    def __init__(self, services):
        self._svc = services

    # --- Artifact (real — reads artifacts/ directory) ---
    def list_artifacts(self, project_id: Optional[str] = None, stage: Optional[str] = None) -> list:
        if not project_id:
            return []
        art_dir = workspace_path(project_id) / "artifacts"
        if not art_dir.exists():
            return []
        results = []
        # D-107: 产物按 artifacts/{stage}/ 分层——除根目录扁平产物外，递归各阶段子文件夹。
        files = [f for f in art_dir.iterdir() if f.is_file()]
        for stage_dir in sorted(p for p in art_dir.iterdir() if p.is_dir()):
            files.extend(f for f in stage_dir.iterdir() if f.is_file())
        for f in sorted(files):
            try:
                st = f.stat()
                atype = f.suffix.lstrip(".")
                rel = str(f.relative_to(workspace_path(project_id)))
                results.append({
                    "artifact_id": f"artifact-{f.stem}",
                    "artifact_type": atype,
                    "title": f.stem.replace("_", " ").replace("-", " "),
                    "stage": stage or "",
                    "artifact_status": "generated",
                    "is_evidence_candidate": False,
                    "content_hash": "",
                    "bytes": st.st_size,
                    "path": rel,
                    "mock_level": "real",
                    "source_status": "real",
                    "name": f.name,
                    "modified_at": st.st_mtime,
                })
            except Exception:
                # 发声：artifact 文件 stat 失败会让其从清单中消失，掩盖存在的产物。
                logger.warning("list_artifacts: 读取 artifact 失败 file=%s", f, exc_info=True)
        return results

    def get_artifact(self, artifact_id: str, project_id: Optional[str] = None):
        """Resolve a single artifact by id via real file read.

        artifact_id is encoded as "artifact-<stem>" (see list_artifacts). Locates
        the matching file in workspace/{project_id}/artifacts by stem (stem may
        itself contain "-", so only the fixed "artifact-" prefix is stripped) and
        returns the same metadata structure as list_artifacts, with a real sha256
        content_hash computed from the file bytes. Returns None when project_id is
        missing, the id is malformed, or no file matches. stat/read errors are
        surfaced via logger.warning (never silently swallowed).
        """
        import hashlib
        if not project_id or not artifact_id:
            return None
        prefix = "artifact-"
        if not artifact_id.startswith(prefix):
            return None
        stem = artifact_id[len(prefix):]
        art_dir = workspace_path(project_id) / "artifacts"
        if not art_dir.exists():
            return None
        # D-107: 递归各阶段子文件夹（p0-p6）匹配 stem，兼容根目录扁平产物。
        candidates = [f for f in art_dir.iterdir() if f.is_file()]
        for stage_dir in sorted(p for p in art_dir.iterdir() if p.is_dir()):
            candidates.extend(f for f in stage_dir.iterdir() if f.is_file())
        for f in sorted(candidates):
            if f.stem == stem:
                try:
                    st = f.stat()
                    data = f.read_bytes()
                    content_hash = hashlib.sha256(data).hexdigest()
                    atype = f.suffix.lstrip(".")
                    rel = str(f.relative_to(workspace_path(project_id)))
                    return {
                        "artifact_id": f"artifact-{f.stem}",
                        "artifact_type": atype,
                        "title": f.stem.replace("_", " ").replace("-", " "),
                        "stage": "",
                        "artifact_status": "generated",
                        "is_evidence_candidate": False,
                        "content_hash": content_hash,
                        "bytes": st.st_size,
                        "path": rel,
                        "mock_level": "real",
                        "source_status": "real",
                        "name": f.name,
                        "modified_at": st.st_mtime,
                    }
                except Exception:
                    # 发声：artifact 文件存在但 stat/read 失败，静默返回 None 会让
                    # 已存在的产物"看似不存在"，掩盖读取故障。
                    logger.warning("get_artifact: 读取 artifact 失败 file=%s", f, exc_info=True)
                    return None
        return None

    # --- Evidence (WP-2: real filesystem storage under workspace/evidence/) ---
    # Evidence objects are stored as {evidence_id}.json in workspace/{project_id}/evidence/
    # This replaces the deprecated in-memory list approach.

    def _evidence_dir(self, project_id: str):
        ev_dir = workspace_path(project_id) / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        return ev_dir

    def write_evidence(self, project_id: str, evidence_id: str, evidence_type: str,
                       status: str = "candidate", source: str = "p0",
                       claim: str = "", stage: str = "p0",
                       extra: dict | None = None) -> dict:
        """Write a real Evidence object to workspace/evidence/{evidence_id}.json.

        R21 / B-R20-EVIDENCE-SHA-STALE: P4 execution is multi-node and the same
        output file path can legitimately be overwritten by a later node/run.
        When the new Evidence references the same file (output_code_ref) as an
        older, not-yet-superseded Evidence record, mark that older record with
        superseded_by=<this evidence_id> so P5's sha256 basis check (see
        p5_verification_service._verify_p4_evidence_real) can skip stale history
        instead of misreading a legitimate overwrite as basis tampering.

        Returns the evidence dict (can be serialized as API response).
        """
        from datetime import datetime, timezone
        ev = {
            "evidence_id": evidence_id,
            "project_id": project_id,
            "type": evidence_type,
            "status": status,
            "source": source,
            "claim": claim,
            "stage": stage,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            ev.update(extra)
        ev_dir = self._evidence_dir(project_id)

        out_ref = ev.get("output_code_ref")
        if out_ref:
            self._mark_superseded_by_file_ref(ev_dir, out_ref, evidence_id)

        ev_file = ev_dir / f"{evidence_id}.json"
        ev_file.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
        return ev

    def _mark_superseded_by_file_ref(self, ev_dir: Path, output_code_ref: str,
                                     new_evidence_id: str) -> None:
        """Mark older Evidence records that reference the same file as superseded.

        Scans existing Evidence JSON files in ev_dir; any record (other than the
        one about to be written) whose output_code_ref matches and that is not
        already superseded gets superseded_by=new_evidence_id persisted back to
        disk. This is what lets P5 verification skip stale sha256 basis from an
        earlier write of the same file without ever skipping the CURRENT
        (non-superseded) record's sha256 check — tampering of the latest write
        still fails verification.
        """
        for f in sorted(ev_dir.glob("*.json")):
            if f.stem == new_evidence_id:
                continue
            try:
                old = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                # 发声：历史 Evidence 文件损坏时跳过标记，不阻塞新 Evidence 写入。
                logger.warning("write_evidence: 读取历史 Evidence 失败 file=%s", f, exc_info=True)
                continue
            if old.get("output_code_ref") != output_code_ref:
                continue
            if old.get("superseded_by"):
                continue  # already marked stale by an even newer write
            old["superseded_by"] = new_evidence_id
            try:
                f.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                # 发声：标记失败不应阻塞新 Evidence 写入，但需要留痕以便排查。
                logger.warning("write_evidence: 标记 superseded_by 失败 file=%s", f, exc_info=True)

    def list_evidence(self, project_id: Optional[str] = None, stage: Optional[str] = None) -> list:
        """Read real Evidence objects from workspace/{project_id}/evidence/.

        Each file is a complete Evidence dict. Optionally filtered by stage.
        """
        if not project_id:
            return []
        ev_dir = workspace_path(project_id) / "evidence"
        if not ev_dir.exists():
            return []
        results = []
        for f in sorted(ev_dir.iterdir()):
            if f.is_file() and f.suffix == ".json":
                try:
                    ev = json.loads(f.read_text(encoding="utf-8"))
                    if stage and ev.get("stage") != stage:
                        continue
                    results.append(ev)
                except Exception:
                    # 发声：Evidence 文件损坏被静默丢弃会让证据在列表中"消失"，掩盖数据损坏。
                    logger.warning("list_evidence: 解析 Evidence 文件失败 file=%s", f, exc_info=True)
        return results

    def get_evidence(self, evidence_id: str, project_id: Optional[str] = None):
        """Get a single Evidence by id (optionally scoped to project).

        Searches workspace evidence directories. If project_id is given,
        only searches that project. Otherwise scans all known projects.
        """
        from app.core.config import settings
        import os
        ws_root = getattr(settings, 'workspace_dir', None) or os.path.join(os.getcwd(), '工作区')
        projects_dir = os.path.join(ws_root, "projects")
        if project_id:
            ev_file = workspace_path(project_id) / "evidence" / f"{evidence_id}.json"
            if ev_file.exists():
                try:
                    return json.loads(ev_file.read_text(encoding="utf-8"))
                except Exception:
                    # 发声：Evidence 文件存在但解析失败，静默返回 None 会让其"看似不存在"。
                    logger.warning("get_evidence: 解析 Evidence 失败 file=%s", ev_file, exc_info=True)
            return None
        # Scan all projects for this evidence_id
        if not os.path.isdir(projects_dir):
            return None
        for pid in os.listdir(projects_dir):
            ev_file = workspace_path(pid) / "evidence" / f"{evidence_id}.json"
            if ev_file.exists():
                try:
                    return json.loads(ev_file.read_text(encoding="utf-8"))
                except Exception:
                    # 发声：Evidence 文件存在但解析失败，静默会让扫描"看似未命中"。
                    logger.warning("get_evidence(scan): 解析 Evidence 失败 file=%s", ev_file, exc_info=True)
        return None

    def list_evidence_gaps(self, project_id: Optional[str] = None) -> list:
        """Read real evidence gaps from uncertainty_manifest.json.

        Maps gap fields to EvidenceGapResponse schema
        (gap_id / evidence_type / description / blocking / stage), which is what
        WorkspaceAggregateResponse.pending_evidence_gaps validates against. Gaps
        come from a real manifest, so source_status is "real".
        """
        if not project_id:
            return []
        # D-107: uncertainty_manifest 为 P1 产物，位于 artifacts/p1/（兼容旧扁平路径）。
        manifest = workspace_path(project_id) / "artifacts" / "p1" / "uncertainty_manifest.json"
        if not manifest.exists():
            legacy = workspace_path(project_id) / "artifacts" / "uncertainty_manifest.json"
            manifest = legacy if legacy.exists() else manifest
        if not manifest.exists():
            return []
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            gaps = data.get("evidence_gaps", [])
            return [{
                "gap_id": f"gap-{g.get('item', '')}-{i}",
                "evidence_type": g.get("type", ""),
                "description": g.get("detail", g.get("type", "")),
                "blocking": bool(g.get("blocking", False)),
                "stage": g.get("stage", ""),
                "source_status": "real",
            } for i, g in enumerate(gaps)]
        except Exception:
            return []

    # --- Trace (from trace_writer) ---
    def list_traces(self, project_id: Optional[str] = None, run_id: Optional[str] = None,
                    stage: Optional[str] = None, trace_type: Optional[str] = None, limit: int = 50) -> list:
        return self._svc.trace_writer.query(
            project_id=project_id, run_id=run_id, stage=stage,
            trace_type=trace_type, limit=limit,
        )

    def get_trace(self, trace_id: str):
        traces = self._svc.trace_writer.query(limit=10000)
        for t in traces:
            if t.get("trace_id") == trace_id:
                return t
        return None

    # --- Audit (from audit_writer) ---
    def list_audits(self, project_id: Optional[str] = None, gate_id: Optional[str] = None,
                    risk_level: Optional[str] = None, limit: int = 50) -> list:
        return self._svc.audit_writer.query(
            project_id=project_id, gate_id=gate_id,
            risk_level=risk_level, limit=limit,
        )

    def get_audit(self, audit_id: str):
        audits = self._svc.audit_writer.query(limit=10000)
        for a in audits:
            if a.get("audit_id") == audit_id:
                return a
        return None
