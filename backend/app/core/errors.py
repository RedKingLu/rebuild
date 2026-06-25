"""Error codes — authoritative enumeration for API error responses.

R4: 19 base error codes. All error responses follow RFC 9457 Problem Details.
"""

# === Base error codes (19 types) ===
ERROR_CODES = {
    "validation_error": "请求参数/body 校验失败",
    "not_found": "资源不存在",
    "permission_denied": "权限不足",
    "policy_blocked": "策略阻止",
    "gate_required": "需用户 Gate",
    "gate_not_resolved": "Gate 未决策",
    "conflict": "资源冲突",
    "state_mismatch": "状态不兼容",
    "checkpoint_missing": "检查点缺失",
    "trace_missing": "Trace 缺失",
    "audit_missing": "Audit 缺失",
    "evidence_missing": "Evidence 缺失",
    "resource_unavailable": "资源不可用",
    "model_unavailable": "模型不可用",
    "integration_failed": "集成失败",
    "execution_failed": "执行失败",
    "redaction_required": "需脱敏",
    "internal_error": "内部错误",
}
