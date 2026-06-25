"""Pre-built fixture data for R4 mock API.

Aligns with the 3-project mental model from R3 frontend mock data
(frontend/src/mock/data.ts).
"""

from app.schemas.project import ProjectResponse
from app.schemas.run import RunResponse
from app.schemas.gate import GateResponse
from app.schemas.aet import (
    ArtifactResponse, EvidenceResponse, TraceResponse, AuditResponse, EvidenceGapResponse,
)


def seed_projects() -> list[ProjectResponse]:
    return [
        ProjectResponse(
            project_id="proj-001",
            name="MicroOA 信创迁移",
            description="将 .NET Framework 办公系统迁移至信创平台",
            project_status="ready",
            current_stage="P2",
            current_run_id="run-001",
            active_gate="gate-001",
            evidence_gap_count=3,
            source_type="local_dir",
            workspace_status="ready",
            onboarding_done=True,
            updated_at="2026-06-25T10:30:00Z",
            created_at="2026-06-24T08:00:00Z",
        ),
        ProjectResponse(
            project_id="proj-002",
            name="ERP 国产化适配",
            description="ERP 系统数据库与中间件信创迁移",
            project_status="running",
            current_stage="P4",
            current_run_id="run-002",
            active_gate=None,
            evidence_gap_count=5,
            source_type="git",
            workspace_status="ready",
            onboarding_done=True,
            updated_at="2026-06-25T09:15:00Z",
            created_at="2026-06-23T14:00:00Z",
        ),
        ProjectResponse(
            project_id="proj-003",
            name="门户网站重构",
            description="政府门户前端框架迁移至 Vue3 + 信创服务器",
            project_status="ready",
            current_stage="P6",
            current_run_id=None,
            active_gate=None,
            evidence_gap_count=1,
            source_type="zip",
            workspace_status="ready",
            onboarding_done=False,
            updated_at="2026-06-24T18:00:00Z",
            created_at="2026-06-22T10:00:00Z",
        ),
    ]


def seed_runs() -> list[RunResponse]:
    return [
        RunResponse(
            run_id="run-001",
            project_id="proj-001",
            run_goal="评估技术栈迁移风险",
            run_status="running",
            current_stage="P2",
            started_at="2026-06-25T08:00:00Z",
            updated_at="2026-06-25T10:30:00Z",
            active_gate="gate-001",
            can_pause=True,
            can_cancel=True,
            can_resume=False,
            stage_status={"P0": "completed", "P1": "completed", "P2": "in_progress", "P3": "pending", "P4": "pending", "P5": "pending", "P6": "pending"},
        ),
        RunResponse(
            run_id="run-002",
            project_id="proj-002",
            run_goal="执行数据库迁移",
            run_status="waiting_gate",
            current_stage="P4",
            started_at="2026-06-24T14:00:00Z",
            updated_at="2026-06-25T09:15:00Z",
            active_gate="gate-002",
            can_pause=False,
            can_cancel=True,
            can_resume=True,
            stage_status={"P0": "completed", "P1": "completed", "P2": "completed", "P3": "completed", "P4": "waiting_gate", "P5": "pending", "P6": "pending"},
        ),
    ]


def seed_gates() -> list[GateResponse]:
    return [
        GateResponse(
            gate_id="gate-001",
            gate_type="stage_promotion",
            gate_status="waiting_decision",
            project_id="proj-001",
            run_id="run-001",
            stage="P2",
            reason="P2→P3 评估结果确认",
            risk_level="L2",
            summary="技术栈评估已完成，发现 3 项高风险依赖需确认后进入规划阶段",
            options=["approve", "reject", "request_changes"],
            recommended_option="approve",
        ),
        GateResponse(
            gate_id="gate-002",
            gate_type="high_risk_action",
            gate_status="waiting_decision",
            project_id="proj-002",
            run_id="run-002",
            stage="P4",
            reason="数据库写操作需用户确认",
            risk_level="L3",
            summary="即将执行数据库迁移脚本（涉及 12 张表结构变更），需用户确认后继续",
            options=["approve", "reject", "pause"],
            recommended_option="approve",
        ),
    ]


def seed_artifacts() -> list[ArtifactResponse]:
    return [
        ArtifactResponse(artifact_id="art-001", artifact_type="tech_stack_report", title="技术栈分析报告", stage="P2", artifact_status="generated", is_evidence_candidate=True, content_hash="a1b2c3", bytes=12800, path="artifacts/tech-stack-report.md"),
        ArtifactResponse(artifact_id="art-002", artifact_type="risk_matrix", title="迁移风险评估矩阵", stage="P2", artifact_status="generated", is_evidence_candidate=True, content_hash="d4e5f6", bytes=6400, path="artifacts/risk-matrix.json"),
        ArtifactResponse(artifact_id="art-003", artifact_type="migration_plan", title="迁移执行计划草案", stage="P3", artifact_status="draft", is_evidence_candidate=False, content_hash="g7h8i9", bytes=25600, path="artifacts/migration-plan.md"),
    ]


def seed_evidences() -> list[EvidenceResponse]:
    return [
        EvidenceResponse(evidence_id="ev-001", evidence_type="source_accessible", evidence_status="validated", validation_status="validation_passed", stage="P0", claim="源码可访问且完整", summary="源项目目录成功读取，共 342 个文件", blocking=False),
        EvidenceResponse(evidence_id="ev-002", evidence_type="build_verification", evidence_status="candidate", validation_status="not_validated", stage="P0", claim="源码可构建", summary="构建验证待执行", gap_description="需在目标环境中执行构建验证", blocking=True),
        EvidenceResponse(evidence_id="ev-003", evidence_type="compatibility_check", evidence_status="insufficient", validation_status="validation_failed", stage="P2", claim="依赖兼容性检查", summary="3 个依赖项在信创环境中无对应版本", gap_description="需确认替代方案或接受风险", blocking=True),
    ]


def seed_traces() -> list[TraceResponse]:
    return [
        TraceResponse(trace_id="trace-001", trace_type="source_scan", run_id="run-001", stage="P0", action="scan", summary="源码扫描完成：342 文件，12 目录", created_at="2026-06-25T08:15:00Z"),
        TraceResponse(trace_id="trace-002", trace_type="state_change", run_id="run-001", stage="P2", action="evaluate", summary="模型调用：技术栈评估完成", created_at="2026-06-25T09:00:00Z"),
        TraceResponse(trace_id="trace-003", trace_type="gate_event", run_id="run-001", stage="P2", action="gate_created", summary="Gate gate-001: 等待用户决策", created_at="2026-06-25T10:30:00Z"),
    ]


def seed_audits() -> list[AuditResponse]:
    return [
        AuditResponse(audit_id="AU-a1b2c3d4e5", audit_type="gate_decision", gate_id="gate-001", risk_level="L2", action="stage_promotion", decision="pending", reason="P2→P3 阶段晋级审批：等待用户决策", project_id="proj-001", run_id="run-001", stage="P2", created_at="2026-06-25T10:30:00Z"),
    ]


def seed_evidence_gaps() -> list[EvidenceGapResponse]:
    return [
        EvidenceGapResponse(gap_id="gap-001", evidence_type="build_verification", stage="P0", description="需在目标环境中执行构建验证", blocking=True),
        EvidenceGapResponse(gap_id="gap-002", evidence_type="compatibility_check", stage="P2", description="3 个依赖项在信创环境中无对应版本", blocking=True),
    ]
