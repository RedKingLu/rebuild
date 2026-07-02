"""AET service — Artifact, Evidence, Trace, Audit queries.

R9-3G P0-3: Artifacts and Evidence Gaps are now read from the real
workspace filesystem (artifacts/ directory + uncertainty_manifest.json).
Traces and Audits come from TraceWriter/AuditWriter.
"""

import json
from pathlib import Path
from typing import Optional

from app.services.workspace_service import workspace_path


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
        for f in sorted(art_dir.iterdir()):
            if f.is_file():
                try:
                    st = f.stat()
                    atype = f.suffix.lstrip(".")
                    results.append({
                        "artifact_id": f"artifact-{f.stem}",
                        "artifact_type": atype,
                        "title": f.stem.replace("_", " ").replace("-", " "),
                        "stage": stage or "",
                        "artifact_status": "generated",
                        "is_evidence_candidate": False,
                        "content_hash": "",
                        "bytes": st.st_size,
                        "path": f"artifacts/{f.name}",
                        "mock_level": "real",
                        "name": f.name,
                        "modified_at": st.st_mtime,
                    })
                except Exception:
                    pass
        return results

    def get_artifact(self, artifact_id: str):
        # Simple lookup by name match (artifact_id encodes the stem)
        # Real lookup is done via file read in routes
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
        ev_file = ev_dir / f"{evidence_id}.json"
        ev_file.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
        return ev

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
                    pass
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
                    pass
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
                    pass
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
        manifest = workspace_path(project_id) / "artifacts" / "uncertainty_manifest.json"
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
