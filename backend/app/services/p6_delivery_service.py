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


# ── R17.5-P6-R3 (GAP-P6-2) 许可确定性检测词表 ────────────────────────────────
# 只做【确定性事实检测】：文件存否 / 声明片段命中的路径 + 模式名 / 分发标记。绝不做
# "是 MIT 就通过 / 无 LICENSE 就 X" 的许可类型判断（那交 acceptance_result 映射 + LLM
# advisory）。不硬编码具体许可类型；只记路径/模式名/计数，绝不写密钥或匹配到的明文内容。
_LICENSE_FILENAMES = {
    "license", "license.txt", "license.md", "licence", "licence.txt", "licence.md",
    "copying", "copying.txt", "copying.md", "notice", "notice.txt", "unlicense",
}
_LICENSE_DECL_PATTERNS = [
    ("spdx_identifier", re.compile(r"SPDX-License-Identifier", re.IGNORECASE)),
    ("licensed_under", re.compile(r"licensed\s+under", re.IGNORECASE)),
    ("copyright_notice", re.compile(r"\bcopyright\b", re.IGNORECASE)),
    ("all_rights_reserved", re.compile(r"all\s+rights\s+reserved", re.IGNORECASE)),
]
_THIRD_PARTY_PATTERNS = [
    ("spdx_identifier", re.compile(r"SPDX-License-Identifier", re.IGNORECASE)),
    ("third_party_marker", re.compile(r"third[\s_-]?party|vendored|开源组件|来自开源", re.IGNORECASE)),
]
_DISTRIBUTION_MARKERS = [
    ("external_distribution",
     re.compile(r"external\s+distribution|redistribut|对外分发|对外发布|公开发布", re.IGNORECASE)),
]


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
    # R17.5-P6-R3（GAP-P6-2/3）确定性事实：许可检测 / PoC-Production 定级 / 验收结论多档映射。
    # 均为【确定性事实与映射档】（进 p6_delivery_report，权威），与 LLM advisory 的
    # acceptance_advice（analysis_only）并存不冲突；绝不翻转任何门禁。
    license_notice: dict = field(default_factory=dict)
    scope_level: str = ""            # poc / production_candidate（由 P5 证据完整度确定性推导）
    acceptance_result: dict = field(default_factory=dict)  # accepted / accepted_with_warning / rework_required / blocked


# ── 服务 ──────────────────────────────────────────────────────────────────

class P6DeliveryService:
    """P6 交付包生成服务。R12-3-C8 新建。"""

    def __init__(self, tracer=None, auditor=None):
        self.tracer = tracer
        self.auditor = auditor

    def generate_delivery_package(self, project_id: str, run_id: str,
                                   p5_plan: dict | None = None,
                                   p5_report: dict | None = None) -> DeliveryPackage:
        """生成完整 P6 交付包。

        R17.5-P6-R3：`p5_report`（持久化的 p5_validation_report.json 内容）用于确定性
        推导 scope_level / acceptance_result（读 can_be_completed 等）；缺省时退化为从
        validation_plan 推导，绝不因缺省而伪造 accepted/Production。
        """
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

        # 2b. R17.5-P6-R3 (GAP-P6-2)：许可确定性检测（源只读 D-099）
        pkg.license_notice = self._build_license_notice(ws, output_files, p4_input)

        # 3. 生成 manifest
        pkg.delivery_manifest = self._build_delivery_manifest(
            project_id, run_id, output_files, patch_files, p4_input)
        pkg.risk_manifest = self._build_risk_manifest(
            p4_input, validation_plan, license_notice=pkg.license_notice)
        pkg.hash_manifest = self._build_hash_manifest(output_files, patch_files)

        # 3b. R17.5-P6-R3 (GAP-P6-3)：PoC/Production 定级 + ACCEPTANCE_RESULTS 多档（确定性映射）
        pkg.scope_level, pkg.acceptance_result = self._derive_scope_and_acceptance(
            p5_report, validation_plan, pkg.risk_manifest, pkg.license_notice)

        # 4. 生成报告
        pkg.p5_validation_report = self._build_p5_validation_report(p4_input, validation_plan)
        pkg.p6_delivery_report = self._build_p6_delivery_report(
            project_id, run_id, output_files, patch_files, pkg.risk_manifest,
            license_notice=pkg.license_notice, scope_level=pkg.scope_level,
            acceptance_result=pkg.acceptance_result)

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

    def _build_risk_manifest(self, p4_input, validation_plan: dict, license_notice: dict | None = None) -> dict:
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

        # R17.5-P6-R3 (GAP-P6-2)：许可不清计入 risk_manifest（Q-P6-2 口径）。
        # 默认【非阻断】（内部验证可继续）；仅当标记「对外分发」（distribution_flag=true）时升 blocking。
        if license_notice and license_notice.get("license_clarity") == "unclear":
            dist = bool(license_notice.get("distribution_flag"))
            risks.append({
                "type": "license_unclear",
                "description": ("交付范围内未发现明确 LICENSE 文件 + 许可声明（许可不清）；"
                                "默认内部验证可继续，对外分发前须核实许可（Q-P6-2）。"),
                "distribution_flag": dist,
                "blocking": dist,
            })

        return {
            "generated_at": _now(),
            "risk_count": len(risks),
            "has_blocking": any(r.get("blocking") for r in risks),
            "risks": risks,
        }

    # ── R17.5-P6-R3 (GAP-P6-2) 许可确定性检测 ──────────────────────────────

    def _build_license_notice(self, ws: Path, output_files: list[dict], p4_input) -> dict:
        """确定性许可检测——产 license_notice【事实】（非判断）。

        扫描 output_code/ 与 source/（源只读 D-099，仅读不写）里的 LICENSE/COPYING 文件存否、
        README/代码中的许可声明片段、第三方来源标记；产：
          - license_files_found：命中的 LICENSE/COPYING 文件相对路径
          - license_declarations / third_party_markers：命中片段的【路径 + 模式名】（D-032：不写内容）
          - distribution_flag：是否标记「对外分发」（默认 false——无此标记不升级）
          - license_clarity：clear/unclear（确定性推导：有明确 LICENSE 文件 + 有许可声明 → clear）
        绝不做具体许可类型判断（是否达标交 acceptance_result 映射 + LLM advisory）。
        """
        license_files_found: list[str] = []
        scan_scope: list[str] = []
        for root_name in ("output_code", "source"):
            root = ws / root_name
            if not root.exists() or not root.is_dir():
                continue
            scan_scope.append(root_name)
            try:
                for p in root.rglob("*"):
                    if len(license_files_found) >= 50:
                        break
                    if p.is_file() and p.name.lower() in _LICENSE_FILENAMES:
                        license_files_found.append(str(p.relative_to(ws)))
            except Exception:
                logger.warning("P6: license file scan failed under %s", root_name, exc_info=True)

        # 声明 / 第三方 / 分发标记：扫描交付范围文件 + output_code README/NOTICE
        scan_targets: list[str] = [f["path"] for f in (output_files or [])]
        oc = ws / "output_code"
        if oc.exists():
            for name in ("README", "README.md", "README.txt", "NOTICE", "NOTICE.md"):
                rp = oc / name
                if rp.is_file():
                    rel = str(rp.relative_to(ws))
                    if rel not in scan_targets:
                        scan_targets.append(rel)

        license_declarations: list[dict] = []
        third_party_markers: list[dict] = []
        distribution_flag = False
        for rel in scan_targets[:200]:
            p = ws / rel
            try:
                content = p.read_text(encoding="utf-8", errors="replace")[:20000]
            except Exception:
                continue
            for name, pat in _LICENSE_DECL_PATTERNS:
                if pat.search(content):
                    license_declarations.append({"path": rel, "pattern": name})
            for name, pat in _THIRD_PARTY_PATTERNS:
                if pat.search(content):
                    third_party_markers.append({"path": rel, "pattern": name})
            for _name, pat in _DISTRIBUTION_MARKERS:
                if pat.search(content):
                    distribution_flag = True

        clarity = "clear" if (license_files_found and license_declarations) else "unclear"
        return {
            "generated_at": _now(),
            "scan_scope": scan_scope,
            "license_files_found": license_files_found,
            "license_files_count": len(license_files_found),
            "license_declarations": license_declarations,   # path + pattern（无内容/无密钥）
            "third_party_markers": third_party_markers,      # path + pattern（无内容/无密钥）
            "distribution_flag": distribution_flag,
            "license_clarity": clarity,
        }

    # ── R17.5-P6-R3 (GAP-P6-3) PoC/Production 定级 + ACCEPTANCE_RESULTS 多档 ──

    def _derive_scope_and_acceptance(self, p5_report: dict | None, validation_plan: dict | None,
                                     risk_manifest: dict, license_notice: dict) -> tuple[str, dict]:
        """确定性映射——由 P5 证据完整度推导 scope_level + acceptance_result 四档【事实档】。

        这是【确定性事实映射】（进 p6_delivery_report，权威），与 LLM 的 acceptance_advice
        （advisory）并存不冲突。**绝不翻转 P5→P6 双向门禁/脱敏门禁/final gate**——status 仍由
        handler 既有逻辑认定；本映射只在已过门禁后给"建议档"事实。绝不产 "Production accepted"
        伪结论：PoC + evidence_gap + 许可不清 → 诚实 accepted_with_warning。
        """
        slots = (validation_plan or {}).get("slots", []) if isinstance(validation_plan, dict) else []
        slot_status = {s.get("slot_id"): s.get("status") for s in slots if isinstance(s, dict)}

        can_be_completed = bool((p5_report or {}).get(
            "can_be_completed", (validation_plan or {}).get("can_be_completed", True)))
        has_blocking = bool((risk_manifest or {}).get("has_blocking"))
        gap_present = (
            any(st == "evidence_gap" for st in slot_status.values())
            or any(r.get("type") == "evidence_gap" for r in (risk_manifest or {}).get("risks", []))
        )
        # build/run 证据：build 必须 validated；run 命令-less 可 not_applicable
        build_ok = slot_status.get("build_verified") == "validated"
        run_ok = slot_status.get("run_verified") in ("validated", "not_applicable")
        license_unclear = (license_notice or {}).get("license_clarity") == "unclear"
        distribution = bool((license_notice or {}).get("distribution_flag"))

        # scope_level：纯由 P5 证据完整度推导（PoC 绝不伪装 Production）
        if can_be_completed and not gap_present and build_ok and run_ok:
            scope_level = "production_candidate"
        else:
            scope_level = "poc"

        # acceptance_result 四档（此映射不覆盖门禁，只给已过门禁后的建议档事实）
        if not can_be_completed:
            result, rationale = "blocked", "P5 验证未通过（can_be_completed=false）；不可交付。"
        elif distribution and license_unclear:
            result, rationale = "blocked", ("标记对外分发（distribution_flag=true）但许可不清；"
                                            "对外分发前须核实许可（Q-P6-2 升 blocked）。")
        elif has_blocking:
            result, rationale = "rework_required", "存在阻断级风险项（validation_failed / 对外分发许可不清）；需返工后再交付。"
        elif scope_level == "production_candidate" and not license_unclear and not gap_present:
            result, rationale = "accepted", "P5 证据完整（硬必需全 validated + build/run + 无 evidence_gap）且许可清晰。"
        else:
            causes = []
            if scope_level == "poc":
                causes.append("PoC 范围（证据完整度未达 production_candidate）")
            if gap_present:
                causes.append("存在 evidence_gap")
            if license_unclear:
                causes.append("许可不清（默认内部验证可继续，对外分发前核实）")
            result = "accepted_with_warning"
            rationale = "存在非阻断但重要的缺口：" + "；".join(causes) if causes else \
                "存在非阻断缺口，接受但附警告。"

        acceptance_result = {
            "result": result,
            "rationale": rationale,
            "deterministic": True,          # 确定性事实档（权威），非 LLM 建议
            "note": "确定性映射档；与 LLM advisory acceptance_advice 并存，不翻转任何门禁。",
            "inputs": {
                "can_be_completed": can_be_completed,
                "has_blocking_risk": has_blocking,
                "evidence_gap_present": gap_present,
                "build_verified": build_ok,
                "run_verified_or_na": run_ok,
                "license_clarity": (license_notice or {}).get("license_clarity", "unclear"),
                "distribution_flag": distribution,
            },
        }
        return scope_level, acceptance_result

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

    def _build_p6_delivery_report(self, project_id, run_id, output_files, patch_files, risk_manifest,
                                  license_notice: dict | None = None, scope_level: str = "",
                                  acceptance_result: dict | None = None) -> dict:
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
            # R17.5-P6-R3（GAP-P6-2/3）确定性事实档（权威，与 LLM advisory 并存）
            "scope_level": scope_level,
            "acceptance_result": acceptance_result or {},
            "license_notice": license_notice or {},
            "notes": [
                "交付包来源 = output_code + P5 passed evidence（D-105③）",
                "未通过项在 risk_manifest 中，不混入通过交付",
                "source/ 默认不包含（D-105③）",
                "scope_level / acceptance_result / license_notice 为确定性事实档，不翻转任何门禁",
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


# ── SEC-01: P6 脱敏硬门禁（WP-4） ────────────────────────────────────────────
# desensitization_ok=False（产物含 sk-/AKIA/api_key 等疑似密钥）时：默认硬 blocked 交付；
# 唯一放行路径 = 一道强制显式用户 Gate（gate_type="desensitization_release"，L5 高风险，
# 含风险说明），用户显式 approve 后方可交付。不再仅软提示。风险说明只含路径/模式名/计数，
# 绝不含密钥明文（D-032：desensitization_issues 本身已只记 path/pattern/count）。

_DESENS_GATE_TYPE = "desensitization_release"


def desensitization_risk_explanation(pkg: "DeliveryPackage") -> dict:
    """构造用户可读的脱敏风险说明（已脱敏：仅文件路径 + 模式名 + 计数，无密钥明文）。"""
    return {
        "reason": "交付包脱敏扫描发现疑似密钥/凭据；默认硬阻断交付，须用户显式确认风险后放行（SEC-01/D-032）。",
        "issue_count": len(pkg.desensitization_issues),
        "issues": [
            {"path": i.get("path", ""), "pattern": i.get("pattern", ""), "count": i.get("count", 0)}
            for i in (pkg.desensitization_issues or [])
        ],
        "release_path": "用户显式批准 desensitization_release Gate（L5 高风险，D-034）",
    }


def find_approved_desensitization_override(project_id: str, run_id: str) -> Optional[str]:
    """查该 run 是否已有用户显式批准的脱敏放行 Gate（approved）。返回 gate_id 或 None。"""
    try:
        from app.dependencies import get_services
        gates = get_services().gate_service.list_by_project(project_id)
    except Exception:
        logger.warning("SEC-01: 查询脱敏放行 Gate 失败 project=%s", project_id, exc_info=True)
        return None
    for g in gates:
        if (g.gate_type == _DESENS_GATE_TYPE and (g.run_id or "") == (run_id or "")
                and g.gate_status == "approved"):
            return g.gate_id
    return None


def ensure_desensitization_gate(project_id: str, run_id: str, pkg: "DeliveryPackage",
                                tracer=None, auditor=None) -> Optional[str]:
    """确保存在一道强制脱敏放行 Gate（唯一放行路径）。已有 waiting/approved 则复用，
    否则创建 L5 Gate + 写安全 Audit（含脱敏后的风险说明）。返回 gate_id。"""
    try:
        from app.dependencies import get_services
        gs = get_services().gate_service
        gates = gs.list_by_project(project_id)
    except Exception:
        logger.warning("SEC-01: 无法访问 Gate 服务，脱敏放行 Gate 未创建 project=%s", project_id, exc_info=True)
        return None

    for g in gates:
        if (g.gate_type == _DESENS_GATE_TYPE and (g.run_id or "") == (run_id or "")
                and g.gate_status in ("waiting_decision", "approved")):
            return g.gate_id  # 复用（不重复建门）

    risk = desensitization_risk_explanation(pkg)
    paths = ", ".join(i["path"] for i in risk["issues"][:5]) or "(见风险清单)"
    try:
        gate = gs.create(
            project_id=project_id, run_id=run_id or "", stage="p6",
            gate_type=_DESENS_GATE_TYPE, risk_level="L5",
            reason="P6 交付包脱敏扫描发现疑似密钥/凭据，默认硬阻断交付（SEC-01/D-032）",
            summary=(f"⚠ 脱敏拦截：{risk['issue_count']} 处疑似密钥（{paths}）。"
                     f"默认不交付；如确认非敏感或已处理，请显式批准放行。"),
            options=["approve", "reject"],
        )
    except Exception:
        logger.warning("SEC-01: 创建脱敏放行 Gate 失败 project=%s", project_id, exc_info=True)
        return None

    if auditor is not None:
        try:
            auditor.write(
                audit_type="security_desensitization_block",
                risk_level="L5", action="p6_delivery_desensitization",
                decision="blocked",
                reason=(f"脱敏扫描 {risk['issue_count']} 处疑似密钥，默认硬阻断交付；"
                        f"须用户显式批准 Gate {gate.gate_id} 放行")[:300],
                project_id=project_id, run_id=run_id, stage="p6",
            )
        except Exception:
            logger.warning("SEC-01: 脱敏拦截审计写入失败", exc_info=True)
    if tracer is not None:
        try:
            tracer.write("gate_event", action="create_desensitization_gate",
                         summary=f"SEC-01 脱敏放行 Gate {gate.gate_id} 创建（默认阻断交付）",
                         project_id=project_id, run_id=run_id, stage="p6")
        except Exception:
            logger.warning("SEC-01: 脱敏拦截 trace 写入失败", exc_info=True)
    return gate.gate_id


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
        # R17.5-P6-R3（GAP-P6-2/3）确定性事实档（权威，非门禁翻转）
        "license_notice": pkg.license_notice,
        "scope_level": pkg.scope_level,
        "acceptance_result": pkg.acceptance_result,
    }
