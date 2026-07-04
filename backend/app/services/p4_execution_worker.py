"""P4 execution worker — platform-internal minimal real execution link (R11-3-C4).

Turns a P3 TaskGraph execution node into a REAL write closure:

  read source/ (read-only) → generate output → write output_code/ → write patches/
  → derive Evidence from the ACTUAL written files → Trace + Audit.

Boundaries (D-099 / D-104, enforced by WorkspaceMediator — the single write gate):
  - source/ is read only; any attempt to write it is rejected + Audit'd (negative path).
  - New / transformed code is written to output_code/ (the only new-code write target).
  - diff / patch drafts are written to patches/ (D-099③ / D-104, platform-writable L2).
  - Nothing here fakes `completed`: no model / empty output → honest `blocked`
    (D-097 / V10 FIND-V9-001). Evidence is taken from the real files on disk
    (sha256 + byte size re-read through the mediator), never from the model's
    self-report ("Evidence 以真实产物为准，不依赖 LLM 自报成功").

This worker executes one execution node at a time. It is driven by RealP4Handler,
which wires each node into TaskGraphEngine + NodeLoop edge scheduling (C5) and, when
the project opts in, delegates the node to an external coding agent (C6, see
_delegate_node). It runs from within the LangGraph p4 node via RealP4Handler, so it
does not bypass the main orchestrator (D-037).
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Optional

from app.services.workspace_service import workspace_path
from app.services.workspace_mediator import WorkspaceMediator

logger = logging.getLogger(__name__)

_MAX_SOURCE_BYTES = 200_000  # read cap for a single source reference (advisory)


def _get_services():
    from app.dependencies import get_services
    return get_services()


def _slug(text: str) -> str:
    s = re.sub(r"[^0-9A-Za-z一-鿿._-]+", "-", (text or "").strip())
    return s.strip("-")[:60] or "node"


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing markdown code fence (```lang … ```) from LLM output
    so output_code is the raw code body, not fenced markdown (B-P4-CODEFENCE).

    Only strips when the content is wrapped in a fence; returns the text unchanged
    otherwise. Handles an opening ```/```lang line and a matching closing ``` line.
    """
    s = text.strip()
    if not s.startswith("```"):
        return text
    lines = s.splitlines()
    # drop the opening fence line (``` or ```csharp etc.)
    lines = lines[1:]
    # drop the closing fence line if present
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


class P4ExecutionWorker:
    """Executes P3 execution nodes into real output_code/ + patches/ artifacts.

    All writes go through a single WorkspaceMediator instance so the source/
    read-only red line (D-099①) and the writable-dir policy (D-104) are enforced
    uniformly. Model generation goes through ModelGateway (D-098); no key → blocked.
    """

    def __init__(self, project_id: str, *, tracer=None, auditor=None, aet=None,
                 gateway=None) -> None:
        self.project_id = project_id
        self.tracer = tracer
        self.auditor = auditor
        self.aet = aet
        self.gateway = gateway
        self._ws_root = workspace_path(project_id)
        self.mediator = WorkspaceMediator(str(self._ws_root))
        # C6: cached project + resolved agent config / delegator (lazy, resolved per run).
        self._project = None
        self._agent_config = None  # resolved CodingAgentConfig | None
        self._delegator = None

    # ── writes (all routed through the mediator) ────────────────────────────

    def _write(self, rel_path: str, content: str, run_id: str, node_id: str,
               action: str) -> tuple[str, str, Optional[str]]:
        """Write content to rel_path via the mediator. Returns (rel_path, risk, audit_id).

        Raises ValueError (re-raised) when the mediator rejects the target
        (e.g. source/). The caller turns that into an honest blocked + Audit.
        """
        try:
            target, risk = self.mediator.check_write(rel_path)
        except ValueError as e:
            self._audit(run_id, node_id, action=action, decision="rejected",
                        risk_level="L4", reason=str(e))
            raise
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        audit_id = self._audit(run_id, node_id, action=action, decision="allowed",
                               risk_level=risk, reason=f"wrote {rel_path}")
        return rel_path, risk, audit_id

    def _read_source(self, rel_path: str) -> Optional[str]:
        """Read a source/ reference read-only via the mediator. None if unreadable."""
        try:
            target = self.mediator.guard_read(rel_path)
            if not target.is_file():
                return None
            data = target.read_bytes()[:_MAX_SOURCE_BYTES]
            return data.decode("utf-8", errors="replace")
        except Exception:
            logger.warning("P4 source read failed for %s (advisory)", rel_path,
                           exc_info=True)  # 公理3: surface, not silent
            return None

    def _file_evidence(self, rel_path: str) -> dict:
        """Re-read a just-written file from disk and derive real evidence facts."""
        target = self.mediator.guard_read(rel_path)
        raw = target.read_bytes()
        return {"path": rel_path, "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw)}

    @staticmethod
    def _evaluate_criteria(criteria: list, *, output_code_refs: list,
                           patch_refs: list, evidence_real: bool) -> dict:
        """Map each acceptance criterion to a real, produced-artifact-backed bool
        (B-P4-CRITERIA — no more unconditional True rubber-stamp).

        Minimal decidable mapping against the real node outputs:
          - "产出 output_code …"         ← output_code_refs 非空
          - "产出 patch/diff"            ← patch_refs 非空
          - "Evidence 来自真实落盘文件"    ← evidence 存在且 basis=real_file_on_disk
        An unrecognized criterion cannot be independently verified here, so it is
        conservatively backed by "a real output_code artifact was produced" rather
        than blindly True — never claim satisfaction without a real product.
        """
        met: dict = {}
        for c in criteria:
            cl = str(c).lower()
            if "output_code" in cl or "output code" in cl:
                met[c] = bool(output_code_refs)
            elif "patch" in cl or "diff" in cl:
                met[c] = bool(patch_refs)
            elif "evidence" in cl or "落盘" in str(c):
                met[c] = bool(evidence_real)
            else:
                met[c] = bool(output_code_refs)
        return met

    # ── C6: external coding-agent delegation decision + collection ──────────

    def _resolve_project(self) -> Optional[object]:
        if self._project is not None:
            return self._project
        try:
            svc = _get_services()
            self._project = svc.project_service.get(self.project_id)
        except Exception:
            logger.warning("P4: failed to load project %s", self.project_id, exc_info=True)
            self._project = None
        return self._project

    def _resolve_agent_config(self) -> Optional[dict]:
        """Resolve the referenced CodingAgentConfig into a delegate()-ready dict, or None.

        Returns None when no agent is referenced / not enabled / not loaded — in which
        case the delegation branch cannot run and we fall back to platform-native. The
        BYOK API key lives in Credential, never in this dict (handled inside the
        delegator's model resolver, D-077 / G9).
        """
        if self._agent_config is not None:
            return self._agent_config
        proj = self._resolve_project()
        ref = getattr(proj, "coding_agent_ref", None) if proj else None
        if not ref:
            self._agent_config = None
            return None
        try:
            from app.core.database import get_session
            from app.models.coding_agent_config import CodingAgentConfig
            db = get_session()
            try:
                cfg = db.query(CodingAgentConfig).filter(
                    CodingAgentConfig.agent_id == ref,
                    CodingAgentConfig.enabled == True).first()
            finally:
                db.close()
        except Exception:
            logger.warning("P4: coding agent config load failed for %s", ref, exc_info=True)
            self._agent_config = None
            return None
        if cfg is None:
            self._agent_config = None
            return None
        self._agent_config = {
            "agent_id": cfg.agent_id,
            "agent_type": cfg.agent_type.value if cfg.agent_type else "opencode_cli",
            "name": cfg.name,
            "strategy_id": (cfg.config or {}).get("strategy_id", "system-default"),
            "model_override": (cfg.config or {}).get("model_override"),
            "context_policy": (cfg.config or {}).get("context_policy", "full"),
            "credential_ref": cfg.credential_ref,
        }
        return self._agent_config

    def _delegator(self):
        if self._delegator is None:
            from app.services.external_platform_delegator import ExternalPlatformDelegator
            self._delegator = ExternalPlatformDelegator(
                self.project_id, str(self._ws_root))
        return self._delegator

    def _should_delegate(self, stage: str) -> bool:
        proj = self._resolve_project()
        if proj is None:
            return False
        try:
            from app.services.delegation_policy import should_delegate
            return bool(should_delegate(proj, stage))
        except ValueError as e:
            logger.warning("P4: should_delegate rejected scope for %s: %s",
                           self.project_id, e)
            return False

    # ── trace / audit ───────────────────────────────────────────────────────

    def _trace(self, run_id: str, node_id: str, action: str, summary: str, **extra):
        if not self.tracer:
            return None
        try:
            rec = self.tracer.write("node_execution", run_id=run_id, stage="p4",
                                    action=action, summary=summary,
                                    graph_status="graph_driven", transition_mode="real",
                                    project_id=self.project_id, node_id=node_id, **extra)
            return (rec or {}).get("trace_id")
        except Exception:
            logger.warning("P4 trace write failed (advisory)", exc_info=True)
            return None

    def _audit(self, run_id: str, node_id: str, *, action: str, decision: str,
               risk_level: str, reason: str):
        if not self.auditor:
            return None
        try:
            rec = self.auditor.write("workspace_write", risk_level=risk_level, action=action,
                                     decision=decision, reason=reason, project_id=self.project_id,
                                     run_id=run_id, stage="p4", transition_mode="real",
                                     node_id=node_id)
            return (rec or {}).get("audit_id")
        except Exception:
            logger.warning("P4 audit write failed (advisory)", exc_info=True)
            return None

    # ── generation ──────────────────────────────────────────────────────────

    async def _generate(self, node: dict, source_ref: Optional[str],
                        source_content: Optional[str]) -> dict:
        """Generate output code via ModelGateway. No gateway/key → blocked (honest)."""
        if self.gateway is None:
            return {"status": "blocked",
                    "reason": "P4 无可用 ModelGateway（未配置模型）", "content": ""}
        title = node.get("title") or node.get("node_id") or "execution"
        if source_content is not None:
            user = (f"迁移/改造任务：{title}\n源文件 {source_ref}：\n"
                    f"```\n{source_content}\n```\n"
                    "请输出迁移/改造后的目标代码。只输出代码本体，不要额外解释。")
        else:
            user = (f"执行任务：{title}\n请生成对应的产出代码（无源文件参考）。"
                    "只输出代码本体，不要额外解释。")
        messages = [
            {"role": "system", "content": ("你是信创迁移平台的执行器，将源代码迁移/改造为目标技术栈。"
                                            "只输出目标代码本体。")},
            {"role": "user", "content": user},
        ]
        try:
            result = await self.gateway.call(messages=messages, max_tokens=4096,
                                             temperature=0.2, source="api")
        except Exception as e:
            logger.warning("P4 model call failed", exc_info=True)  # 公理3
            return {"status": "blocked", "reason": f"模型调用失败：{e}", "content": ""}
        content = result.get("content") or ""
        # B-P4-CODEFENCE: strip LLM markdown code fences so output_code is valid code body.
        content = _strip_code_fence(content)
        if result.get("status") != "completed" or not content.strip():
            return {"status": "blocked",
                    "reason": result.get("error") or "模型未产出有效内容",
                    "content": ""}
        return {"status": "completed", "content": content,
                # B-P4-MODELUSED-NULL: ModelGateway.call returns the selected model under
                # "model"; fall back to model_id/model_used for other callers/adapters.
                "model_used": (result.get("model") or result.get("model_id")
                               or result.get("model_used"))}

    # ── per-node execution ──────────────────────────────────────────────────

    async def execute_node(self, node: dict, run_id: str = "") -> dict:
        """Execute one execution node into a real node package (§Step8 shape).

        Never fabricates completed: unreadable input / no model / rejected write
        all yield an honest blocked package.
        """
        node_id = node.get("node_id") or "node"
        title = node.get("title") or node_id
        risk_level = node.get("risk_level") or "L0"
        trace_refs: list[str] = []
        audit_refs: list[str] = []
        t = self._trace(run_id, node_id, "start", f"P4 执行节点 {node_id}：{title}")
        if t:
            trace_refs.append(t)

        # 1. read source references (read-only)
        input_refs = [r for r in (node.get("input_refs") or []) if isinstance(r, str)]
        source_ref = next((r for r in input_refs if r.startswith("source/")), None)
        source_content = self._read_source(source_ref) if source_ref else None

        # C6: external coding-agent delegation. When the project opts in (coding_agent_ref
        # + external_platform_scope) AND the policy says this stage delegates, run the
        # node through the external platform instead of platform-native generation.
        if self._should_delegate("P4"):
            agent_cfg = self._resolve_agent_config()
            if agent_cfg is not None:
                return await self._delegate_node(
                    node, run_id, title, risk_level, source_ref, source_content,
                    trace_refs, audit_refs, agent_cfg)
            t = self._trace(run_id, node_id, "delegation_unavailable",
                            "应委托但 coding agent 配置不可用，降级平台原生")
            if t:
                trace_refs.append(t)

        # 2. determine write target — default output_code/, but honor an explicit
        #    output_target so a mis-planned "write back to source/" is caught by the
        #    mediator (negative path, D-099①).
        out_target = node.get("output_target")
        if not out_target:
            if source_ref:
                out_target = f"output_code/{node_id}/{Path(source_ref).name}"
            else:
                out_target = f"output_code/{node_id}/{_slug(title)}.txt"

        # 3. generate output (honest blocked when no model / no content)
        gen = await self._generate(node, source_ref, source_content)
        if gen["status"] != "completed":
            t = self._trace(run_id, node_id, "blocked", f"节点 {node_id} 未完成：{gen['reason']}")
            if t:
                trace_refs.append(t)
            return {"node_id": node_id, "title": title, "risk_level": risk_level,
                    "node_status": "blocked", "reason": gen["reason"],
                    "criteria_met": False, "artifacts": [], "evidence_refs": [],
                    "output_code_refs": [], "patch_refs": [],
                    "trace_refs": trace_refs, "audit_refs": audit_refs}
        output_code = gen["content"]

        # 4. write output_code/ + patches/ through the mediator (negative path aware)
        try:
            out_ref, out_risk, out_audit = self._write(out_target, output_code, run_id,
                                                       node_id, action="write_output_code")
        except ValueError as e:
            # rejection already audited inside _write
            t = self._trace(run_id, node_id, "rejected", f"节点 {node_id} 写盘被拒：{e}")
            if t:
                trace_refs.append(t)
            return {"node_id": node_id, "title": title, "risk_level": risk_level,
                    "node_status": "blocked", "reason": f"写盘被拒（D-099）：{e}",
                    "criteria_met": False, "scope_violation": True,
                    "artifacts": [], "evidence_refs": [], "output_code_refs": [],
                    "patch_refs": [], "trace_refs": trace_refs, "audit_refs": audit_refs}
        if out_audit:
            audit_refs.append(out_audit)

        diff_text = "".join(difflib.unified_diff(
            (source_content or "").splitlines(keepends=True),
            output_code.splitlines(keepends=True),
            fromfile=(source_ref or "/dev/null"), tofile=out_ref))
        if not diff_text:
            diff_text = f"# no textual diff (new file {out_ref})\n"
        patch_ref, _, patch_audit = self._write(f"patches/{node_id}.diff", diff_text, run_id,
                                                node_id, action="write_patch")
        if patch_audit:
            audit_refs.append(patch_audit)

        # 5. evidence from the REAL files on disk (not the model's self-report)
        out_facts = self._file_evidence(out_ref)
        patch_facts = self._file_evidence(patch_ref)
        evidence_refs: list[str] = []
        if self.aet:
            ev_id = f"ev-p4-{node_id}"
            claim = (f"execution 节点 {node_id} 产出 {out_ref}"
                     f"（sha256={out_facts['sha256'][:12]}…, {out_facts['bytes']}B），"
                     f"diff 见 {patch_ref}")
            try:
                self.aet.write_evidence(
                    self.project_id, ev_id, "execution_output",
                    status="candidate", source="p4", stage="p4", claim=claim,
                    extra={"output_code_ref": out_ref, "output_sha256": out_facts["sha256"],
                           "output_bytes": out_facts["bytes"], "write_risk": out_risk,
                           "patch_ref": patch_ref, "patch_sha256": patch_facts["sha256"],
                           "source_ref": source_ref, "model_used": gen.get("model_used"),
                           "evidence_basis": "real_file_on_disk"})
                evidence_refs.append(ev_id)
            except Exception:
                logger.warning("P4 evidence persist failed for %s", node_id, exc_info=True)

        t = self._trace(run_id, node_id, "completed",
                        f"节点 {node_id} 完成：{out_ref} + {patch_ref}",
                        output_code_ref=out_ref, patch_ref=patch_ref,
                        output_sha256=out_facts["sha256"])
        if t:
            trace_refs.append(t)
        # criteria_met：有 acceptance_criteria 时按真实产物逐条判定（B-P4-CRITERIA，
        # 不再无条件恒真）；NodeLoop Acceptance 检查#2 需 dict 覆盖映射。无 criteria 时 True。
        crit = node.get("acceptance_criteria") or []
        criteria_met = self._evaluate_criteria(
            crit, output_code_refs=[out_ref], patch_refs=[patch_ref],
            evidence_real=bool(evidence_refs)) if crit else True
        return {"node_id": node_id, "title": title, "risk_level": risk_level,
                "node_status": "completed", "criteria_met": criteria_met,
                "artifacts": [out_ref, patch_ref], "evidence_refs": evidence_refs,
                "output_code_refs": [out_ref], "patch_refs": [patch_ref],
                "source_refs": [source_ref] if source_ref else [],
                "trace_refs": trace_refs, "audit_refs": audit_refs,
                "model_used": gen.get("model_used")}

    # ── C6: delegation of one node to the external coding agent ─────────────

    async def _delegate_node(self, node, run_id, title, risk_level, source_ref,
                             source_content, trace_refs, audit_refs, agent_cfg):
        """Run a node via ExternalPlatformDelegator and collect the real outputs.

        The external agent writes into the workspace directly (output_code/ under
        D-099). We then READ BACK the changed files through the mediator to derive
        Evidence/artifact/patch from the ACTUAL on-disk bytes — the same honest standard
        as the native path, never trusting the agent's self-report (公理 / Evidence 以真实
        产物为准). Honours criteria_met coverage when criteria are present.
        """
        node_id = node.get("node_id") or "node"
        criteria = node.get("acceptance_criteria") or []
        evidence_refs: list[str] = []

        # Build the task brief from the node + its source reference.
        crit_text = ("\n验收标准:\n- " + "\n- ".join(criteria)) if criteria else ""
        task = (f"迁移/改造任务：{title}\n"
                + (f"源文件 {source_ref}:\n```\n{source_content}\n```\n"
                   if source_content else "")
                + "请输出迁移/改造后的目标代码并写入 workspace/output_code/ 对应路径。"
                + crit_text)

        try:
            result = await self._delegator().delegate(
                task, stage="P4",
                agent_config=agent_cfg,
                project=self._resolve_project(),
            )
        except Exception as e:
            logger.warning("P4 delegation raised for %s: %s", node_id, e, exc_info=True)
            t = self._trace(run_id, node_id, "delegation_error",
                            f"委托异常降级 blocked：{e}")
            if t:
                trace_refs.append(t)
            return {"node_id": node_id, "title": title, "risk_level": risk_level,
                    "node_status": "blocked",
                    "reason": f"外部 Agent 委托异常（降级 blocked）：{e}",
                    "criteria_met": False, "delegation": {"status": "error"},
                    "artifacts": [], "evidence_refs": [], "output_code_refs": [],
                    "patch_refs": [], "trace_refs": trace_refs, "audit_refs": audit_refs}

        status = result.get("status")
        if status != "ok":
            reason = result.get("reason") or result.get("summary") or "外部 Agent 未完成"
            t = self._trace(run_id, node_id, "delegation_failed",
                            f"委托未成功({status})：{reason}")
            if t:
                trace_refs.append(t)
            return {"node_id": node_id, "title": title, "risk_level": risk_level,
                    "node_status": "blocked", "reason": f"外部 Agent 委托未完成：{reason}",
                    "criteria_met": False,
                    "delegation": {"status": status, "summary": result.get("summary", "")},
                    "artifacts": [], "evidence_refs": [], "output_code_refs": [],
                    "patch_refs": [], "trace_refs": trace_refs, "audit_refs": audit_refs}

        # Delegation succeeded — collect the REAL changed files the agent wrote.
        changed = [f for f in (result.get("changed_files") or [])
                   if isinstance(f, str)]
        output_code_refs: list[str] = []
        patch_refs: list[str] = []
        artifacts: list[str] = []
        for rel in changed:
            # Enforce via mediator: only platform-writable, in-workspace paths count.
            try:
                self.mediator.guard_read(rel)
            except Exception:
                continue
            if not rel.startswith("output_code/"):
                continue
            out_facts = self._file_evidence(rel)
            output_code_refs.append(rel)
            artifacts.append(rel)
            # Patch = diff against the source reference (real bytes on disk).
            if source_content:
                diff_text = "".join(difflib.unified_diff(
                    (source_content or "").splitlines(keepends=True),
                    (self._read_source(rel) or "").splitlines(keepends=True),
                    fromfile=(source_ref or "/dev/null"), tofile=rel))
                if diff_text:
                    pref = f"patches/{node_id}/{Path(rel).stem}.diff"
                    try:
                        pref, _, paudit = self._write(pref, diff_text, run_id, node_id,
                                                      action="write_delegation_patch")
                        if paudit:
                            audit_refs.append(paudit)
                        patch_refs.append(pref)
                        artifacts.append(pref)
                    except ValueError as e:
                        logger.warning("P4 delegation patch write rejected: %s", e)
            # Evidence from the real file on disk.
            if self.aet:
                ev_id = f"ev-p4d-{node_id}-{out_facts['sha256'][:8]}"
                try:
                    self.aet.write_evidence(
                        self.project_id, ev_id, "delegation_output",
                        status="candidate", source="external_agent", stage="p4",
                        claim=(f"委托节点 {node_id} 产出 {rel}"
                               f"（sha256={out_facts['sha256'][:12]}…, {out_facts['bytes']}B）"),
                        extra={"output_code_ref": rel, "output_sha256": out_facts["sha256"],
                               "output_bytes": out_facts["bytes"],
                               "evidence_basis": "real_file_on_disk",
                               "delegation_model": result.get("model")})
                    evidence_refs.append(ev_id)
                except Exception:
                    logger.warning("P4 delegation evidence persist failed for %s", node_id)

        if not output_code_refs:
            t = self._trace(run_id, node_id, "delegation_no_output",
                            "委托完成但未产生可验证的 output_code/ 产物 → blocked")
            if t:
                trace_refs.append(t)
            return {"node_id": node_id, "title": title, "risk_level": risk_level,
                    "node_status": "blocked",
                    "reason": "外部 Agent 委托完成但未产出可验证的 output_code/ 产物",
                    "criteria_met": False,
                    "delegation": {"status": "ok", "changed_files": changed},
                    "artifacts": [], "evidence_refs": [], "output_code_refs": [],
                    "patch_refs": [], "trace_refs": trace_refs, "audit_refs": audit_refs}

        t = self._trace(run_id, node_id, "delegation_completed",
                        f"委托完成：{len(output_code_refs)} 产物 + {len(patch_refs)} patch")
        if t:
            trace_refs.append(t)
        criteria_met = self._evaluate_criteria(
            criteria, output_code_refs=output_code_refs, patch_refs=patch_refs,
            evidence_real=bool(evidence_refs)) if criteria else True
        return {"node_id": node_id, "title": title, "risk_level": risk_level,
                "node_status": "completed", "criteria_met": criteria_met,
                "artifacts": artifacts, "evidence_refs": evidence_refs,
                "output_code_refs": output_code_refs, "patch_refs": patch_refs,
                "source_refs": [source_ref] if source_ref else [],
                "trace_refs": trace_refs, "audit_refs": audit_refs,
                "model_used": result.get("model"),
                "delegation": {"status": "ok", "summary": result.get("summary", ""),
                               "changed_files": changed}}

    # ── graph-level run ─────────────────────────────────────────────────────

    async def run(self, exec_nodes: list[dict], run_id: str = "") -> dict:
        """Execute all execution nodes. Overall completed IFF every node completed.

        Mixed / any-blocked → overall blocked with an honest reason (never fakes a
        whole-stage completed off partial success, D-097).
        """
        packages: list[dict] = []
        for node in exec_nodes:
            packages.append(await self.execute_node(node, run_id=run_id))

        completed = [p for p in packages if p.get("node_status") == "completed"]
        blocked = [p for p in packages if p.get("node_status") != "completed"]
        artifacts = [a for p in completed for a in p.get("artifacts", [])]
        evidence_refs = [e for p in completed for e in p.get("evidence_refs", [])]
        patch_refs = [r for p in completed for r in p.get("patch_refs", [])]

        if blocked or not completed:
            reason = (f"P4 execution：{len(completed)}/{len(exec_nodes)} 节点完成，"
                      f"{len(blocked)} 节点未完成（"
                      + "; ".join(f"{p['node_id']}:{p.get('reason', '')}" for p in blocked) + "）")
            status = "blocked"
        else:
            reason = f"P4 execution：全部 {len(completed)} 个 execution 节点产出真实 output_code + patch"
            status = "completed"

        return {"status": status, "reason": reason, "node_packages": packages,
                "completed_node_count": len(completed), "blocked_node_count": len(blocked),
                "artifacts": artifacts, "evidence_refs": evidence_refs,
                "patch_refs": patch_refs}
