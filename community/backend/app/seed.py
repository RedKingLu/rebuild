"""Community service seed data (R15-4-C3).

Builds REAL resource packages (zip) on disk, computes their sha256, and inserts
DB rows so the /download + checksum flow is genuinely verifiable (not mocked).
Idempotent: only seeds when the resource table is empty.

This is the "发布侧" seed (D-061 修订: 合格性由发布侧保证). Content is 信创迁移
oriented placeholder data for a locally-runnable closed loop, NOT real official
community operations (Q-R15-10).
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db import PACKAGE_DIR, SessionLocal
from app.models import (
    CommunityResource,
    CommunityDoc,
    CommunityNews,
    CommunityModelEntry,
    CommunityEvaluation,
)

VERSION = "R16-community-0.2.0"  # R16-E1: + community docs


def _build_package(resource_id: str, files: dict[str, str], meta: dict | None = None) -> tuple[str, str, list[dict]]:
    """Write a real zip package + manifest.json to PACKAGE_DIR.

    Returns (package_path, checksum_sha256, file_entries).
    manifest 自描述（R15-R16 返工）：含 resource_type/name/display_name/description/version，
    供主平台粘贴链接导入时还原资源真实身份（否则落为默认 tool/Imported Resource）。
    """
    meta = meta or {}
    manifest = {
        "resource_id": resource_id,
        "resource_type": meta.get("resource_type"),
        "name": meta.get("name"),
        "display_name": meta.get("display_name"),
        "description": meta.get("description"),
        "version": meta.get("version"),
        "files": [{"name": n, "size": len(c.encode("utf-8"))} for n, c in files.items()],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, content in files.items():
            zf.writestr(name, content)
    data = buf.getvalue()
    checksum = hashlib.sha256(data).hexdigest()
    path = PACKAGE_DIR / f"{resource_id}.zip"
    path.write_bytes(data)

    file_entries = [
        {"name": "manifest.json", "size": len(json.dumps(manifest).encode("utf-8")),
         "download_url": f"/resources/{resource_id}/download"},
    ] + [
        {"name": n, "size": len(c.encode("utf-8")),
         "download_url": f"/resources/{resource_id}/download"}
        for n, c in files.items()
    ]
    return str(path), checksum, file_entries


_RESOURCE_DEFS = [
    dict(
        id="case-oracle-to-dm-migration",
        resource_type="case",
        name="oracle-to-dm-migration",
        display_name="Oracle → 达梦 迁移案例",
        description="金融核心系统从 Oracle 迁移到达梦 DM8 的完整案例记录。",
        namespace="rebuild-official", publisher="rebuild 官方",
        version="1.2.0", versions=["1.0.0", "1.1.0", "1.2.0"],
        tags=["信创", "数据库", "Oracle", "达梦"], categories=["案例"],
        license="Apache-2.0", source="official", verified=True,
        download_count=1280,
        readme="# Oracle → 达梦 迁移案例\n\n## 背景\n某金融机构核心系统迁移。\n\n## 步骤\n\n1. 盘点 PL/SQL 存储过程\n2. 语法适配（`ROWNUM` → `LIMIT` 等）\n3. 数据一致性校验\n\n| 阶段 | 工时 |\n|---|---|\n| 评估 | 5d |\n| 改造 | 20d |\n",
        files={"case.md": "# 迁移案例正文\nPL/SQL 适配清单...", "checklist.md": "- [x] 盘点\n- [ ] 改造"},
    ),
    dict(
        id="tool-dm-sql-linter",
        resource_type="tool",
        name="dm-sql-linter",
        display_name="达梦 SQL 兼容性检查工具",
        description="扫描 Oracle SQL 并标记达梦不兼容语法。",
        namespace="rebuild-community", publisher="社区贡献者",
        version="0.3.1", versions=["0.3.1"],
        tags=["工具", "SQL", "达梦"], categories=["工具"],
        license="MIT", source="community", verified=False,
        download_count=342,
        readme="# 达梦 SQL 兼容性检查工具\n\n```bash\ndm-sql-linter scan ./sql\n```\n\n检测 `ROWNUM`、`(+)` 外连接、`DECODE` 等。",
        files={"linter.py": "# placeholder linter\nprint('scan')"},
    ),
    dict(
        id="skill-gaussdb-migration-planner",
        resource_type="skill",
        name="gaussdb-migration-planner",
        display_name="GaussDB 迁移规划 Skill",
        description="为 MySQL/PostgreSQL → GaussDB 生成迁移规划的 Skill。",
        namespace="rebuild-community", publisher="社区贡献者",
        version="1.0.0", versions=["1.0.0"],
        tags=["Skill", "GaussDB", "规划"], categories=["Agent 与 Skill"],
        license="MIT", source="community", verified=False,
        download_count=210,
        readme="# GaussDB 迁移规划 Skill\n\n输入源库信息，输出分阶段迁移计划。",
        files={"SKILL.md": "# GaussDB Migration Planner\n信创迁移规划技能。"},
    ),
    dict(
        id="knowledge-xinchuang-baseline",
        resource_type="knowledge",
        name="xinchuang-migration-baseline",
        display_name="信创迁移基线知识",
        description="信创迁移常见技术栈映射与风险基线。",
        namespace="rebuild-official", publisher="rebuild 官方",
        version="2.0.0", versions=["1.0.0", "2.0.0"],
        tags=["知识", "信创", "基线"], categories=["知识"],
        license="CC-BY-4.0", source="official", verified=True,
        download_count=890,
        readme="# 信创迁移基线\n\n## 技术栈映射\n\n| 原栈 | 目标栈 |\n|---|---|\n| Oracle | 达梦 / GaussDB |\n| WebLogic | TongWeb / 东方通 |\n| RedHat | 麒麟 / 统信 UOS |\n",
        files={"baseline.md": "# 迁移基线正文\n技术栈映射表..."},
    ),
    dict(
        id="template-migration-report",
        resource_type="template",
        name="migration-report-template",
        display_name="迁移评估报告模板",
        description="标准化的信创迁移评估报告模板。",
        namespace="rebuild-official", publisher="rebuild 官方",
        version="1.1.0", versions=["1.0.0", "1.1.0"],
        tags=["模板", "报告"], categories=["模板"],
        license="Apache-2.0", source="official", verified=True,
        download_count=455,
        readme="# 迁移评估报告模板\n\n## 章节\n1. 项目概况\n2. 技术栈盘点\n3. 风险评估\n4. 迁移方案\n",
        files={"report-template.md": "# {{项目名}} 迁移评估报告\n..."},
    ),
]

_MODEL_DEFS = [
    dict(model_id="deepseek/deepseek-v4-pro", display_name="DeepSeek V4 Pro",
         provider_id="deepseek-official", family="DeepSeek", model_version="v4-pro",
         context_window=1_000_000, max_output_tokens=8192,
         input_modalities=["text"], output_modalities=["text"],
         capability_tags=["reasoning", "code", "long-context"], task_tags=["code", "planning"],
         license="proprietary", official_url="https://deepseek.com",
         availability_status="available", source="official"),
    dict(model_id="zhipu/glm-5.2", display_name="GLM-5.2",
         provider_id="maas-icompify", family="GLM", model_version="5.2",
         context_window=128_000, max_output_tokens=8192,
         input_modalities=["text"], output_modalities=["text"],
         capability_tags=["reasoning", "code"], task_tags=["general"],
         license="proprietary", availability_status="available", source="official"),
]

_EVAL_DEFS = [
    dict(eval_id="eval-glm52-migration-code", model_id="zhipu/glm-5.2",
         agent_type="node_worker", task_type="code_migration",
         scenario="Oracle PL/SQL → 达梦", metric="pass_rate", score=0.82,
         success_rate=0.82, cost_level="medium", latency_level="medium", sample_count=50,
         eval_method="社区人工评审 50 个迁移片段的正确率（非平台自动评测）",
         eval_version="community-2026Q2", source="社区贡献",
         limitations="样本仅覆盖 PL/SQL 常见语法，不代表模型全局能力；未覆盖复杂触发器。"),
    dict(eval_id="eval-dsv4-planning", model_id="deepseek/deepseek-v4-pro",
         agent_type="planner", task_type="migration_planning",
         scenario="信创迁移分阶段规划", metric="rubric_score", score=4.3,
         success_rate=None, cost_level="high", latency_level="medium", sample_count=30,
         eval_method="导入的第三方 rubric 打分（1-5），非平台自动评测",
         eval_version="community-2026Q2", source="导入数据",
         limitations="rubric 主观性较高；样本 30 例，不代表全局能力。"),
]

_NEWS_DEFS = [
    dict(id="news-welcome", title="rebuild 社区上线（R15）",
         summary="信创迁移资源社区本地骨架上线，支持资源检索、下载、导入。",
         body="# 社区上线\n\n本社区服务为 rebuild R15 交付的本地可运行骨架。",
         pinned=True),
    dict(id="news-dm-case", title="新增 Oracle→达梦 迁移案例",
         summary="金融核心系统迁移案例已发布。", body="详见资源列表。", pinned=False),
]

# R16-E1: community online documentation seed (trusted 发布侧 content).
_DOC_DEFS = [
    dict(
        slug="xinchuang-overview",
        title="信创迁移概述",
        summary="信创（信息技术应用创新）迁移的背景、目标与总体路径。",
        category="信创基础",
        tags=["信创", "概述", "迁移"],
        version="1.0.0", source="official",
        body_markdown=(
            "# 信创迁移概述\n\n"
            "## 什么是信创\n\n"
            "信创（信息技术应用创新）是指在核心基础设施领域采用自主可控的技术栈，"
            "覆盖芯片、操作系统、数据库、中间件与应用软件。\n\n"
            "## 迁移路径\n\n"
            "1. **盘点**：梳理现有技术栈与依赖\n"
            "2. **评估**：确定替换方案与风险\n"
            "3. **改造**：语法适配与数据迁移\n"
            "4. **验证**：一致性校验与回归测试\n\n"
            "## 典型技术栈映射\n\n"
            "| 原栈 | 目标栈 |\n"
            "|---|---|\n"
            "| Oracle | 达梦 DM8 / GaussDB |\n"
            "| MySQL | GaussDB / 海量 |\n"
            "| WebLogic | TongWeb |\n"
            "| RedHat | 麒麟 Kylin / 统信 UOS |\n"
        ),
    ),
    dict(
        slug="dm-migration-guide",
        title="达梦 DM8 迁移实战指南",
        summary="从 Oracle 迁移到达梦 DM8 的常见问题与语法适配清单。",
        category="数据库迁移",
        tags=["达梦", "Oracle", "数据库", "迁移"],
        version="1.0.0", source="community",
        body_markdown=(
            "# 达梦 DM8 迁移实战指南\n\n"
            "## 语法差异清单\n\n"
            "- `ROWNUM` → 使用 `LIMIT` / `ROWNUM`（达梦兼容但语义略异）\n"
            "- `(+)` 外连接 → 改用 `LEFT/RIGHT JOIN`\n"
            "- `DECODE` → 使用 `CASE WHEN`（推荐）\n"
            "- `NVL` → `IFNULL` / `COALESCE`\n\n"
            "## 数据类型\n\n"
            "| Oracle | 达梦 |\n"
            "|---|---|\n"
            "| NUMBER | NUMERIC / DECIMAL |\n"
            "| VARCHAR2 | VARCHAR |\n"
            "| CLOB | TEXT |\n\n"
            "## 数据校验\n\n"
            "迁移完成后务必进行行数对比与关键业务查询的回归验证。\n"
        ),
    ),
    dict(
        slug="rebuild-platform-quickstart",
        title="rebuild 平台快速上手",
        summary="本地起栈、导入知识包与基础工作流。",
        category="平台使用",
        tags=["rebuild", "快速入门", "平台"],
        version="1.0.0", source="official",
        body_markdown=(
            "# rebuild 平台快速上手\n\n"
            "## 本地起栈\n\n"
            "```bash\n"
            "docker compose up -d            # 主平台（后端 8000 / 前端 8080）\n"
            "docker compose --profile community up -d   # 社区门户（8001 / 8081）\n"
            "```\n\n"
            "## 基础工作流\n\n"
            "1. 创建项目 → 2. 导入知识包 → 3. 启动迁移规划 → 4. 查看评测对比\n\n"
            "## 知识包导入\n\n"
            "平台支持导入 `.zip` 知识包（含 manifest.json + 多 .md 文档），"
            "导入后可在知识库中按标题、摘要与正文全文检索。\n"
        ),
    ),
    dict(
        slug="eval-methodology",
        title="评测方法学与局限性说明",
        summary="社区评测结果的来源、方法与局限性，不代表模型全局能力。",
        category="评测",
        tags=["评测", "方法学", "局限性"],
        version="1.0.0", source="official",
        body_markdown=(
            "# 评测方法学与局限性说明\n\n"
            "## 数据来源\n\n"
            "所有评测结果均为**导入数据**，由社区贡献或第三方评测机构提供，"
            "**非平台自动评测**，也**不代表模型的全局能力**。\n\n"
            "## 使用方法\n\n"
            "- 每个评测均附带方法学（eval_method）与局限性（limitations）\n"
            "- 分数仅在相同任务与场景下可比\n"
            "- 样本量（sample_count）影响统计可信度\n\n"
            "## 注意事项\n\n"
            "请勿把评测排名写成模型全局能力结论；请结合具体场景判断。\n"
        ),
    ),
]


def seed_all(db: Session) -> dict:
    if db.query(CommunityResource).count() > 0:
        return {"seeded": False, "reason": "already_seeded"}

    now = datetime.now(timezone.utc)
    for d in _RESOURCE_DEFS:
        d = dict(d)
        files = d.pop("files")
        pkg_path, checksum, file_entries = _build_package(d["id"], files, meta=d)
        db.add(CommunityResource(
            **d, files=file_entries, checksum_sha256=checksum,
            package_path=pkg_path, updated_at=now,
        ))
    for d in _MODEL_DEFS:
        db.add(CommunityModelEntry(**d, updated_at=now))
    for d in _EVAL_DEFS:
        db.add(CommunityEvaluation(**d, published_at=now))
    for d in _NEWS_DEFS:
        db.add(CommunityNews(**d, published_at=now))
    for d in _DOC_DEFS:
        db.add(CommunityDoc(**d, updated_at=now))
    db.commit()
    return {"seeded": True, "resources": len(_RESOURCE_DEFS), "models": len(_MODEL_DEFS),
            "docs": len(_DOC_DEFS)}


def seed_on_startup() -> None:
    db = SessionLocal()
    try:
        seed_all(db)
    finally:
        db.close()
