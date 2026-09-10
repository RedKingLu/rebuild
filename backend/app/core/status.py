"""Status enumerations — code implementation projection of authoritative document source.

THIS FILE IS A PROJECTION ONLY. It does not independently define status semantics.
If any value conflicts with the authoritative document source:
  文档/03-流程与运行时/08-状态与数据模型.md
stop construction and flag the conflict.

R4: All status enums defined here. Mock-only transitions.
"""

# === R stages ===
# 权威源：文档/00-项目治理/06-R阶段总计划.md §「R0-R22 阶段列表」（D-010 的清单已于
# 2026-09-07 Q-R20-1-7 降级为历史快照，权威定义指派给 06-R阶段总计划.md）。
# R22 批次六补齐 R18~R22（V26.2 新增阶段），此前枚举止于 R17，导致 settings.r_stage
# 推进到 R18 之后 /api/health 的 r_stage 会落在枚举外。
R_STAGES = [
    "R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8",
    "R9", "R10", "R11", "R12", "R13", "R14", "R15", "R16", "R17",
    "R18", "R19", "R20", "R21", "R22",
]

# === P stages ===
P_STAGES = ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]

# === Execution modes (D-025) ===
EXECUTION_MODES = ["manual", "plan", "auto"]

# === Risk levels (L0-L5) ===
RISK_LEVELS = ["L0", "L1", "L2", "L3", "L4", "L5"]

# === Project status ===
PROJECT_STATUSES = [
    "created", "initializing", "ready", "running",
    "waiting_gate", "blocked", "completed", "archived", "failed",
]

# === Run status ===
RUN_STATUSES = [
    "created", "ready", "running", "paused", "waiting_gate",
    "blocked", "rework_required", "failed", "canceling",
    "canceled", "completed", "archived",
]

# === Stage (P0-P6) status ===
STAGE_STATUSES = [
    "not_enabled", "not_started", "planning", "waiting_plan_review",
    "ready", "running", "waiting_gate", "blocked", "rework_required",
    "validation_required", "failed", "completed", "accepted",
]

# === Task Node status (NodeLoop runtime, 02-架构设计/03 §8.3 — 12 states) ===
# blocked was added to §8.3 per Q-R10-5 (user-decided 2026-07-01): the original
# 11-state list omitted it, though §Step1/§6/§Step9 and Stage/Run/Project enums
# all use it. Doc §8.3 now reconciled to 12 states.
NODE_STATUSES = [
    "pending", "running", "waiting_gate", "waiting_resource",
    "self_checking", "acceptance_checking", "completed",
    "failed", "retrying", "skipped", "rework_required", "blocked",
]

# === Acceptance result (NodeLoop Step 8/9, 03-流程与运行时/03 §Step9 — 8 values) ===
ACCEPTANCE_RESULTS = [
    "accepted", "accepted_with_warning", "rework_required",
    "retry_required", "gate_required", "failed", "blocked", "skipped",
]

# === Gate status ===
GATE_STATUSES = [
    "created", "waiting_decision", "under_review", "approved",
    "rejected", "needs_more_info", "expired", "canceled",
    "resolved", "failed",
]

# === Gate types ===
GATE_TYPES = [
    "stage_promotion", "high_risk_action", "policy_conflict",
    "permission_escalation", "external_write", "workspace_write",
    "shell_execution", "resource_activation", "community_resource_upgrade",
    "evidence_exception", "manual_confirmation", "recovery_conflict",
    "task_gate", "resource_gate", "secret_gate", "recovery_gate", "mode_gate",
]

# === Artifact status ===
ARTIFACT_STATUSES = [
    "draft", "generated", "under_review", "accepted", "rejected",
    "evidence_candidate", "promoted", "superseded", "archived", "invalid",
]

# === Evidence status ===
EVIDENCE_STATUSES = [
    "candidate", "submitted", "under_validation", "validated",
    "insufficient", "rejected", "superseded", "archived", "invalid",
]

# === Validation status ===
VALIDATION_STATUSES = [
    "not_validated", "validation_pending", "validation_passed",
    "validation_failed", "validation_blocked", "validation_not_applicable",
]

# === Trace types ===
TRACE_TYPES = [
    "state_change", "model_call", "resource_call", "tool_call",
    "mcp_call", "execution_action", "workspace_action", "gate_event",
    "policy_check", "artifact_event", "evidence_event",
    "acceptance_event", "error_event", "context_assembly", "plan_change",
]

# === Audit types ===
AUDIT_TYPES = [
    "gate_decision", "high_risk_action", "policy_conflict",
    "permission_escalation", "external_write", "credential_event",
    "redaction_event", "manual_override", "risk_acceptance",
    "stage_promotion", "evidence_exception", "resource_activation",
    "community_resource_upgrade",
]

# === Transition mode (R4: always "mock") ===
TRANSITION_MODES = ["mock", "langgraph", "manual"]

# === Graph capability status (R9-5-1: real probe — "live" when StateGraph builds) ===
GRAPH_CAPABILITY_STATUSES = ["live", "degraded", "not_connected", "mock_graph", "placeholder"]
