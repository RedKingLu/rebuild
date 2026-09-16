"""Stage/P0-P6 domain schemas."""

from typing import Optional
from pydantic import BaseModel, Field
from app.schemas.common import GraphPlaceholderFields


class StageResponse(GraphPlaceholderFields):
    stage_code: str  # P0-P6
    stage_name: str
    stage_status: str
    entry_gate_ref: Optional[str] = None
    exit_gate_ref: Optional[str] = None
    stage_plan_ref: Optional[str] = None
    task_plan_refs: list[str] = Field(default_factory=list)
    task_graph_ref: Optional[str] = None
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_gap_refs: list[str] = Field(default_factory=list)
    trace_refs: list[str] = Field(default_factory=list)
    audit_refs: list[str] = Field(default_factory=list)
    blocked_reason: Optional[str] = None

    source_status: str = "not_connected"


class StagePlanRequest(BaseModel):
    plan_summary: str = ""
    plan_detail: dict = Field(default_factory=dict)


class PromotionRequest(BaseModel):
    target_stage: str = Field(..., description="Target P stage, e.g. P3")


class PromotionDecision(BaseModel):
    decision: str = Field(..., description="approve/reject/request_changes")
    reason: str = ""
    # B-ACC-PROMOTION-DECISION-NOGUARD：决策必须指名被决策的 Gate。
    # 这不是新增约束 —— V10 legacy RunEngine._gate() 原本就让 gate_id 随暂停点
    # 全程传播（DB + SSE + 客户端），V26.2 在本端点上把它丢了，本字段是把它捡回来。
    # 【勿删】删掉它就退回"按 run_id 猜测要决策哪个 Gate"的跨 Gate 注入缺陷。
    #
    # Optional 而非必填：既有调用方（后端测试 / 前端 / e2e 脚本）不必同批改动。
    # 但"不传"绝不等于"不判定" —— 不传时由 routes_stages.decide_promotion 按
    # (run_id, stage) 推断唯一待决对象，0 个或 ≥2 个一律 409 且不驱动图。
    gate_id: Optional[str] = None
