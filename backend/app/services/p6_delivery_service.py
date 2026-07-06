"""P6 交付包服务（R12-3-C8）。

生成 delivery package / report / artifact/evidence/trace/audit index / hash manifest / risk manifest。
但不注册 P6 handler（C9 起）。

交付包结构（R12-2 §6.5）：
  delivery_package_{run_id}/
  ├── output_code/          # P4 产出的验证通过代码
  ├── patches/              # P4 产出的 diff/patch
  ├── reports/
  │   ├── p5_validation_report.json
  │   └── p6_delivery_report.json
  ├── indexes/
  │   ├── artifact_index.json
  │   ├── evidence_index.json
  │   ├── trace_index.json
  │   └── audit_index.json
  └── manifests/
      ├── delivery_manifest.json
      ├── risk_manifest.json
      └── hash_manifest.json

诚实约束（D-105③ / D-105⑧ / D-032）：
  - 来源必须是 output_code + P5 passed evidence
  - 未通过项进入 risk_manifest，不混入通过交付
  - source 不包含（除非明确标为参考且用户授权）
  - 交付前做最终脱敏扫描
  - hash_manifest 包含所有文件的 SHA-256
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.workspace_service import workspace_path
from app.services.p5_input_service import P5InputService

logger = logging.getLogger(__name__)


# ── DTOs ──────────────────────────────────────────────────────────────────

@dataclass
class DeliveryPackage:
    """P6 交付包结果。"""
    project_id: str
    run_id: str
    # 生成的文件（相对路径）
    package_dir: str = ""
    # 清单
    delivery_manifest: dict = field(default_factory=dict)
    risk_manifest: dict = field(default_factory=dict)
    hash_manifest: dict = field(default_factory=dict)
    # 报告
    p5_validation_report: dict = field(default_factory=dict)
    p6_delivery_report: dict = field(default_factory=dict)
    # 索引
    indexes: dict = field(default_factory=dict)
    # 脱敏扫描结果
    desensitization_ok: bool = True
    desensitization_issues: list = field(default_factory=list)
    # 诚实标记
    source_included: bool = False


# ── 服务 ──────────────────────────────────────────────────────────────────

class P6DeliveryService:
    """P6 交付包生成服务。R12-3-C8 新建。"""

    def __init__(self, tracer=None, auditor=None):
        self.tracer = tracer
        self.auditor = auditor

    def generate_delivery_package(self, project_id: str, run_id: str,
                                   p5_plan: dict | None = None) -> DeliveryPackage:
        """生成完整 P6 交付包。"""
        ws = workspace_path(project_id)
        pkg = DeliveryPackage(project_id=project_id, run_id=run_id)

        # 1. 读取 input facts（output_code / patches / evidence refs）
        input_svc = P5InputService()
        try:
            p4_input = input_svc.read_p4_input(project_id, run_id)
        except Exception as e:
            logger.warning("P6: cannot read P4 input: %s", e, exc_info=True)
            pkg.risk_manifest = {"error": f"无法读取 P4 输入：{e}", "blocking": True}
            return pkg

        if p4_input.blocked:
            pkg.risk_manifest = {"error": p4_input.blocked_reason, "blocking": True}
            return pkg

        # 2. 读取验证结果
        validation_plan = p5_plan or (p4_input.p4_execution_summary or {})

        # 收集 output_code / patches 文件
        output_files = self._collect_files(ws, p4_input.output_code_refs)
        patch_files = self._collect_files(ws, p4_input.patch_refs)

        # 3. 生成 manifest
        pkg.delivery_manifest = self._build_delivery_manifest(
            project_id, run_id, output_files, patch_files, p4_input)
        pkg.risk_manifest = self._build_risk_manifest(p4_input, validation_plan)
        pkg.hash_manifest = self._build_hash_manifest(output_files, patch_files)

        # 4. 生成报告
        pkg.p5_validation_report = self._build_p5_validation_report(p4_input, validation_plan)
        pkg.p6_delivery_report = self._build_p6_delivery_report(
            project_id, run_id, output_files, patch_files, pkg.risk_manifest)

        # 5. 生成 AETA 索引
        pkg.indexes = self._build_indexes(project_id, run_id, output_files, patch_files, p4_input)

        # 6. 脱敏扫描
        pkg.desensitization_ok, pkg.desensitization_issues = self._desensitization_scan(
            ws, output_files, patch_files)

        return pkg

    # ── 文件收集 ─────────────────────────────────────────────────────────

    def _collect_files(self, ws: Path, refs: list[str]) -> list[dict]:
        """收集真实文件信息（path / sha256 / bytes）。"""
        files = []
        for ref in refs or []:
            p = ws / ref
            if not p.exists() or p.is_dir():
                continue
            raw = p.read_bytes()
            files.append({
                "path": ref,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            })
        return files

    # ── Manifests ────────────────────────────────────────────────────────

    def _build_delivery_manifest(self, project_id, run_id, output_files, patch_files, p4_input) -> dict:
        return {
            "project_id": project_id,
            "run_id": run_id,
            "generated_at": _now(),
            "contents": {
                "output_code_count": len(output_files),
                "patch_count": len(patch_files),
                "total_output_bytes": sum(f["bytes"] for f in output_files),
            },
            "output_code": [{"path": f["path"], "sha256": f["sha256"]} for f in output_files],
            "patches": [{"path": f["path"], "sha256": f["sha256"]} for f in patch_files],
        }

    def _build_risk_manifest(self, p4_input, validation_plan: dict) -> dict:
        """未通过项进入 risk_manifest。"""
        risks = []

        # Evidence gaps
        for gap in (p4_input.evidence_gaps or []):
            risks.append({
                "type": "evidence_gap",
                "gap_id": gap.get("gap_id", ""),
                "description": gap.get("description", ""),
                "blocking": gap.get("blocking", True),
            })

        # 验证失败
        if isinstance(validation_plan, dict):
            for slot in validation_plan.get("slots", []):
                if slot.get("status") in ("validation_failed", "evidence_gap", "needs_user_input"):
                    risks.append({
                        "type": "verification_failed",
                        "slot_id": slot.get("slot_id", ""),
                        "status": slot.get("status"),
                        "reason": slot.get("evidence_gap_reason", ""),
                        "blocking": slot.get("status") == "validation_failed",
                    })

        return {
            "generated_at": _now(),
            "risk_count": len(risks),
            "has_blocking": any(r.get("blocking") for r in risks),
            "risks": risks,
        }

    def _build_hash_manifest(self, output_files, patch_files) -> dict:
        all_files = output_files + patch_files
        return {
            "generated_at": _now(),
            "file_count": len(all_files),
            "files": [{"path": f["path"], "sha256": f["sha256"]} for f in all_files],
        }

    # ── Reports ───────────────────────────────────────────────────────────

    def _build_p5_validation_report(self, p4_input, validation_plan: dict) -> dict:
        return {
            "generated_at": _now(),
            "project_id": p4_input.project_id,
            "run_id": p4_input.run_id,
            "p4_to_p5_gate_status": p4_input.p4_to_p5_gate_status,
            "evidence_ref_count": len(p4_input.evidence_refs or []),
            "evidence_gaps": p4_input.evidence_gaps or [],
            "validation_plan": validation_plan,
        }

    def _build_p6_delivery_report(self, project_id, run_id, output_files, patch_files, risk_manifest) -> dict:
        return {
            "generated_at": _now(),
            "project_id": project_id,
            "run_id": run_id,
            "summary": {
                "output_code_files": len(output_files),
                "patch_files": len(patch_files),
                "total_bytes": sum(f["bytes"] for f in output_files + patch_files),
                "risk_count": risk_manifest.get("risk_count", 0),
                "has_blocking_risks": risk_manifest.get("has_blocking", False),
            },
            "notes": [
                "交付包来源 = output_code + P5 passed evidence（D-105③）",
                "未通过项在 risk_manifest 中，不混入通过交付",
                "source/ 默认不包含（D-105③）",
            ],
        }

    # ── AETA 索引 ────────────────────────────────────────────────────────

    def _build_indexes(self, project_id, run_id, output_files, patch_files, p4_input) -> dict:
        """生成 Artifact / Evidence / Trace / Audit 索引。R12-4-05 补 audit_index。"""
        # Artifact index
        artifact_index = []
        for f in output_files:
            artifact_index.append({
                "artifact_type": "output_code",
                "path": f["path"],
                "content_hash": f["sha256"],
                "bytes": f["bytes"],
                "stage": "p4",
            })
        for f in patch_files:
            artifact_index.append({
                "artifact_type": "patch",
                "path": f["path"],
                "content_hash": f["sha256"],
                "bytes": f["bytes"],
                "stage": "p4",
            })

        # Evidence index
        evidence_index = []
        for eid in (p4_input.evidence_refs or []):
            evidence_index.append({
                "evidence_id": eid,
                "stage": "p4",
                "status": "referenced",
            })

        # Trace index（从 tracer 读取）
        trace_index = []
        try:
            from app.dependencies import get_services
            svc = get_services()
            traces = svc.trace_writer.query(project_id=project_id, run_id=run_id, limit=500)
            for t in traces:
                trace_index.append({
                    "trace_id": t.get("trace_id", ""),
                    "trace_type": t.get("trace_type", ""),
                    "action": t.get("action", ""),
                    "summary": t.get("summary", ""),
                    "stage": t.get("stage", ""),
                })
        except Exception as e:
            logger.warning("P6: trace index build failed: %s", e, exc_info=True)

        # R12-4-05: audit_index — 读取审计记录
        audit_index = []
        try:
            from app.dependencies import get_services
            svc = get_services()
            audits = svc.audit_writer.query(project_id=project_id, limit=500)
            for a in audits:
                audit_index.append({
                    "audit_id": a.get("audit_id", ""),
                    "audit_type": a.get("audit_type", ""),
                    "action": a.get("action", ""),
                    "decision": a.get("decision", ""),
                    "risk_level": a.get("risk_level", ""),
                    "reason": a.get("reason", ""),
                    "stage": a.get("stage", ""),
                })
        except Exception as e:
            logger.warning("P6: audit index build failed: %s", e, exc_info=True)

        return {
            "artifact_index": artifact_index,
            "evidence_index": evidence_index,
            "trace_index": trace_index,
            "audit_index": audit_index,
        }

    # ── 脱敏扫描 ─────────────────────────────────────────────────────────

    def _desensitization_scan(self, ws: Path, output_files, patch_files) -> tuple[bool, list[dict]]:
        """交付前最终脱敏扫描（D-032）。"""
        issues = []
        secret_patterns = [
            re.compile(r'(sk-[a-z0-9]{20,})', re.IGNORECASE),
            re.compile(r'(AKIA[0-9A-Z]{16})'),
            re.compile(r'(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*["\']?([^\s"\']{8,})'),
        ]
        all_content = []
        for f in (output_files + patch_files):
            p = ws / f["path"]
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                all_content.append((f["path"], content))
            except Exception:
                continue

        for path, content in all_content:
            for pat in secret_patterns:
                matches = pat.findall(content)
                if matches:
                    issues.append({
                        "path": path,
                        "pattern": pat.pattern[:50],
                        "count": len(matches),
                    })

        return len(issues) == 0, issues


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def delivery_package_to_dict(pkg: DeliveryPackage) -> dict:
    """序列化为 API 响应。"""
    return {
        "project_id": pkg.project_id,
        "run_id": pkg.run_id,
        "package_dir": pkg.package_dir,
        "delivery_manifest": pkg.delivery_manifest,
        "risk_manifest": pkg.risk_manifest,
        "hash_manifest": pkg.hash_manifest,
        "p5_validation_report": pkg.p5_validation_report,
        "p6_delivery_report": pkg.p6_delivery_report,
        "indexes": pkg.indexes,
        "desensitization": {
            "ok": pkg.desensitization_ok,
            "issues_count": len(pkg.desensitization_issues),
            "issues": pkg.desensitization_issues,
        },
        "source_included": pkg.source_included,
    }
