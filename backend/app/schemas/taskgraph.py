"""TaskPlan/TaskGraph domain schemas.

R4: Mock-only — no real graph execution. TaskGraph is a data-layer DAG,
NOT the LangGraph orchestration graph (see graph/contracts.py).
"""

from typing import Optional
from pydantic import BaseModel, Field
from app.schemas.common import GraphPlaceholderFields


class StagePlanResponse(BaseModel):
    stage_plan_id: str
    project_id: str
    run_id: str
    stage: str
    plan_summary: str
    plan_detail: dict = Field(default_factory=dict)
    status: str = "draft"
    source_status: str = "mock"


class TaskPlanResponse(BaseModel):
    task_plan_id: str
    stage_plan_id: str
    title: str
    description: str = ""
    status: str = "draft"
    source_status: str = "mock"


class TaskGraphNode(BaseModel):
    node_id: str
    task_plan_id: Optional[str] = None
    title: str
    node_type: str = "task"
    status: str = "pending"
    source_status: str = "mock"


class TaskGraphEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: str = "sequential"
    trigger_condition: Optional[str] = None
    dependency: Optional[str] = None
    source_status: str = "mock"


class TaskGraphResponse(GraphPlaceholderFields):
    task_graph_id: str
    project_id: str
    run_id: str
    stage: str
    title: str = ""
    status: str = "draft"
    nodes: list[TaskGraphNode] = Field(default_factory=list)
    edges: list[TaskGraphEdge] = Field(default_factory=list)
    source_status: str = "mock"
