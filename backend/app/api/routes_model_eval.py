"""AgentModelEvalResult API (R15-4-C10): imported eval results — display only.

Endpoints:
  GET  /api/model-evaluations          query by model_id / task_type / scenario
  POST /api/model-evaluations/import   import eval results (no auto-eval engine)

Red line #9/#10/#11: this is a catalog of IMPORTED results. The page must show
source/eval_method/limitations/sample_count for every row and must never present
rankings as the model's global capability.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.agent_model_eval import AgentModelEvalResult
from app.schemas.common import SuccessEnvelope, Meta

eval_router = APIRouter(prefix="/model-evaluations", tags=["model-evaluations"])


def _to_dict(e: AgentModelEvalResult) -> dict:
    return {
        "eval_id": e.eval_id,
        "model_id": e.model_id,
        "catalog_id": e.catalog_id,
        "agent_type": e.agent_type,
        "task_type": e.task_type,
        "scenario": e.scenario,
        "metric": e.metric,
        "score": e.score,
        "success_rate": e.success_rate,
        "cost_level": e.cost_level,
        "latency_level": e.latency_level,
        "sample_count": e.sample_count,
        "eval_method": e.eval_method,
        "eval_version": e.eval_version,
        "source": e.source,
        "published_at": e.published_at,
        "limitations": e.limitations,
    }


@eval_router.get("")
def list_evaluations(
    model_id: str | None = None,
    task_type: str | None = None,
    scenario: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(AgentModelEvalResult)
    if model_id:
        stmt = stmt.where(AgentModelEvalResult.model_id == model_id)
    if task_type:
        stmt = stmt.where(AgentModelEvalResult.task_type == task_type)
    if scenario:
        stmt = stmt.where(AgentModelEvalResult.scenario == scenario)
    items = db.execute(stmt.order_by(AgentModelEvalResult.score.desc())).scalars().all()
    return SuccessEnvelope(
        data={"total": len(items), "evaluations": [_to_dict(e) for e in items]},
        meta=Meta(),
    ).model_dump()


class EvalImportItem(BaseModel):
    eval_id: str
    model_id: str
    catalog_id: str | None = None
    agent_type: str | None = None
    task_type: str | None = None
    scenario: str | None = None
    metric: str | None = None
    score: float | None = None
    success_rate: float | None = None
    cost_level: str | None = None
    latency_level: str | None = None
    sample_count: int | None = None
    eval_method: str | None = None
    eval_version: str | None = None
    source: str | None = None
    published_at: str | None = None          # ISO datetime (optional)
    limitations: str | None = None


class EvalImportRequest(BaseModel):
    evaluations: list[EvalImportItem]


@eval_router.post("/import")
def import_evaluations(body: EvalImportRequest, db: Session = Depends(get_db)) -> dict:
    """Import evaluation results. No automatic evaluation is performed (red line #9)."""
    from datetime import datetime, timezone
    imported = 0
    for m in body.evaluations:
        existing = db.get(AgentModelEvalResult, m.eval_id)
        row = existing or AgentModelEvalResult(eval_id=m.eval_id)
        row.model_id = m.model_id
        row.catalog_id = m.catalog_id
        row.agent_type = m.agent_type
        row.task_type = m.task_type
        row.scenario = m.scenario
        row.metric = m.metric
        row.score = m.score
        row.success_rate = m.success_rate
        row.cost_level = m.cost_level
        row.latency_level = m.latency_level
        row.sample_count = m.sample_count
        row.eval_method = m.eval_method
        row.eval_version = m.eval_version
        row.source = m.source
        row.limitations = m.limitations
        if m.published_at:
            try:
                row.published_at = datetime.fromisoformat(m.published_at)
            except Exception:
                row.published_at = None
        if existing is None:
            db.add(row)
        imported += 1
    db.commit()
    return SuccessEnvelope(
        data={"imported": imported, "total": db.query(AgentModelEvalResult).count()},
        meta=Meta(),
    ).model_dump()


def seed_exemplar_evaluations(db: Session) -> int:
    """Seed a couple of imported exemplar evaluations (idempotent, display-only)."""
    from datetime import datetime, timezone
    if db.query(AgentModelEvalResult).count() > 0:
        return 0
    now = datetime.now(timezone.utc)
    exemplars = [
        AgentModelEvalResult(
            eval_id="eval-seed-glm52-migration",
            model_id="glm-5.2",
            agent_type="node_worker",
            task_type="code_migration",
            scenario="Oracle PL/SQL → 达梦 代码迁移",
            metric="pass_rate", score=0.82, success_rate=0.82,
            cost_level="medium", latency_level="medium", sample_count=50,
            eval_method="社区/第三方对 50 个迁移片段的人工评审通过率（非平台自动评测）",
            eval_version="seed-2026Q2", source="社区导入",
            published_at=now,
            limitations="样本仅覆盖 PL/SQL 常见语法，不代表模型全局能力；未覆盖复杂触发器与存储过程。",
        ),
        AgentModelEvalResult(
            eval_id="eval-seed-dsv4-planning",
            model_id="deepseek-v4-pro",
            agent_type="planner",
            task_type="migration_planning",
            scenario="信创迁移分阶段规划",
            metric="rubric_score", score=4.3,
            cost_level="high", latency_level="medium", sample_count=30,
            eval_method="导入的第三方 rubric 打分（1-5），非平台自动评测",
            eval_version="seed-2026Q2", source="导入数据",
            published_at=now,
            limitations="rubric 主观性较高；样本 30 例，不代表模型全局规划能力。",
        ),
    ]
    for e in exemplars:
        db.add(e)
    db.commit()
    return len(exemplars)
