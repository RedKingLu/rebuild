"""Project service — DB-backed CRUD operations on projects."""

import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectStatus, SourceType
from app.schemas.project import ProjectCreate

logger = logging.getLogger("rebuild.project_service")


class ProjectService:
    def __init__(self, db: Session):
        self.db = db

    def list(self, status: Optional[str] = None, sort: str = "updated_at",
             order: str = "desc", limit: Optional[int] = None,
             offset: int = 0, include_archived: bool = False) -> list[Project]:
        q = self.db.query(Project)
        if status:
            # Explicit status filter takes precedence (e.g. status=archived must be
            # listable); do NOT also apply the default "exclude archived" filter,
            # which would make status=archived logically impossible (always empty).
            q = q.filter(Project.project_status == status)
        elif not include_archived:
            q = q.filter(Project.project_status != ProjectStatus.archived)
        # sort
        col = getattr(Project, sort, Project.updated_at)
        if order == "asc":
            q = q.order_by(col.asc())
        else:
            q = q.order_by(col.desc())
        if limit is not None:
            q = q.limit(limit)
        q = q.offset(offset)
        # R17-2 V-R17-1B-1/P0：source_type 枚举容错。DB 历史非法值（如 'local'）
        # 触发 SQLAlchemy LookupError 时，降级跳过该行并记 warning，而非整表 500。
        # SourceType._missing_ 已返 None；但 SAEnum 反序列化早于 Python Enum，故仍需此兜底。
        rows = []
        try:
            rows = q.all()
        except LookupError as exc:
            logger.warning("ProjectService.list: 枚举反序列化失败，降级逐行加载: %s", exc)
            for row in q.yield_per(50):
                try:
                    _ = row.source_type  # 触发列加载
                    rows.append(row)
                except LookupError:
                    logger.warning("ProjectService.list: 跳过非法 source_type 行 id=%s", row.project_id)
        return rows

    def get(self, project_id: str) -> Optional[Project]:
        return self.db.get(Project, project_id)

    def create(self, req: ProjectCreate) -> Project:
        source_config = None
        if req.source_type in ("git", "github") and req.source_config:
            source_config = req.source_config
        elif req.source_type in ("local_dir", "zip") and req.source_config:
            source_config = req.source_config
        elif req.source_type == "manual" and req.source_config:
            source_config = req.source_config

        # Map "zip" to enum member zip_source (value is "zip")
        st = req.source_type
        if st == "zip":
            st = "zip"  # SourceType("zip") looks up by value "zip" -> zip_source

        p = Project(
            name=req.name,
            description=req.description or "",
            source_type=SourceType(st),
            source_config=source_config,
            project_status=ProjectStatus.created,
            # R20-2-01: 空串归一为 None，使"未选场景"在库里只有一种表示（NULL）。
            scenario=(getattr(req, "scenario", None) or None),
        )
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        return p

    def update(self, project_id: str, **fields) -> Optional[Project]:
        p = self.get(project_id)
        if p is None:
            return None
        for k, v in fields.items():
            # 约定（勿放宽）：v is None 表示「本次不更新该字段」，这是所有调用方共享的
            # 通用语义。需要把某字段【置空】的调用方请传空值本身（如 active_gate=""，
            # 见 GateService._clear_active_gate_if_current），不要改这里的过滤条件——
            # 放宽它会让所有未显式传参/传 None 的字段被意外清空。
            if v is not None and hasattr(p, k):
                setattr(p, k, v)
        p.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(p)
        return p

    def update_source(self, project_id: str, source_type: str,
                      source_config: Optional[dict] = None) -> Optional[Project]:
        """Update project source configuration (used by Git/ZIP/GitHub integration endpoints)."""
        p = self.get(project_id)
        if p is None:
            return None
        # Map "zip" to value "zip" for enum lookup
        st = source_type
        if st == "zip":
            st = "zip"
        p.source_type = SourceType(st)
        if source_config is not None:
            p.source_config = source_config
        p.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(p)
        return p

    def delete(self, project_id: str) -> bool:
        """Soft-delete: set status to archived."""
        p = self.get(project_id)
        if p is None:
            return False
        p.project_status = ProjectStatus.archived
        p.archived_at = datetime.now(timezone.utc)
        p.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    @staticmethod
    def build_project_context_dict(project_id: str) -> Optional[dict]:
        """R20-2-04：读取 Project 记录，转为上下文装配器白名单认识的 6 键 dict
        （name / source_type / workspace_status / onboarding_done / coding_agent_ref / scenario）。

        供 stage_handlers.py / work_agent.py / node_loop.py 等此前【不传 project=】的断链调用点
        复用（B-R20-SCENARIO-NOT-WIRED）——统一"怎么把 Project 转成上下文装配器能读的
        project= dict"这一件事，避免各处各写一份（DRY，仿 _build_p0_migration_target 采集范式）。
        放在 project_service（services 层）而非 stage_handlers（graph 层），是为了让 services 层的
        work_agent.py / node_loop.py 也能直接复用而不产生 services→graph 反向依赖。

        缺失/异常 → None（诚实采集失败，advisory）。调用方在 None 时按"不传 project="处理，
        行为不劣于此前现状（此前就是完全不传）。
        """
        try:
            from app.core.database import get_session
            from app.models.project import Project as _Project
            db = get_session()
            try:
                p = db.get(_Project, project_id)
                if p is None:
                    return None
                return {
                    "name": p.name,
                    "source_type": (p.source_type.value if hasattr(p.source_type, "value")
                                    else str(p.source_type)),
                    "workspace_status": p.workspace_status,
                    "onboarding_done": p.onboarding_done,
                    "coding_agent_ref": p.coding_agent_ref,
                    "scenario": getattr(p, "scenario", None),
                }
            finally:
                db.close()
        except Exception:
            logger.debug("build_project_context_dict 读取 Project 失败（advisory）", exc_info=True)
            return None

    @staticmethod
    def to_response(p: Project) -> dict:
        return {
            "project_id": p.project_id,
            "name": p.name,
            "description": p.description or "",
            "project_status": p.project_status.value if hasattr(p.project_status, 'value') else str(p.project_status),
            "source_type": p.source_type.value if hasattr(p.source_type, 'value') else str(p.source_type),
            "source_config": p.source_config,
            "current_stage": p.current_stage,
            "current_run_id": p.current_run_id,
            # active_gate 语义（D-07）：**当前待决 Gate 的 id**。Gate 一经决策即由
            # GateService._clear_active_gate_if_current 置为 ""；后台图产出下一个待决 Gate 时
            # 由 routes_stages._run_graph_bg 指向新 Gate。空串/None = 当前无待决 Gate。
            # 客户端仍应以 Gate 自身的 gate_status 为权威判据（该字段是便捷索引，非唯一真相）。
            "active_gate": p.active_gate,
            "evidence_gap_count": p.evidence_gap_count,
            "workspace_status": p.workspace_status or "ready",
            "onboarding_done": p.onboarding_done,
            "coding_agent_ref": p.coding_agent_ref,
            # D-088 / R9-5-5 — must be surfaced so the B-6 PATCH response round-trips the new scope
            # and the Workspace toolbar switch reflects exactly what should_delegate() will read.
            "external_platform_scope": getattr(p, "external_platform_scope", None) or "none",
            "model_strategy_mode": getattr(p, "model_strategy_mode", "global_unified") or "global_unified",
            "global_model_ref": getattr(p, "global_model_ref", None),
            # R17.5 WP-6 (Q-R17.4-3-2): 目标运行环境约束（引导点选），前端展示 + 喂 P 阶段目标。
            "migration_target": getattr(p, "migration_target", None),
            # R20-2-01: 项目重构场景 id（自由文本，NULL=未选择，不给任何默认值 —— R20-2-06）。
            "scenario": getattr(p, "scenario", None),
            "created_at": p.created_at.isoformat() if p.created_at else "",
            "updated_at": p.updated_at.isoformat() if p.updated_at else "",
            "source_status": "real",
            "capability_status": "available",
        }
