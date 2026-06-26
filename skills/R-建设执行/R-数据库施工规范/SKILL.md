---
name: R-数据库施工规范
description: rebuild 特有 — SQLAlchemy+Alembic migration/repository/seed/fixture/测试DB/加密字段规范
metadata:
  series: R
  category: construction_skill
  status: active
  skill_source: rebuild_self
---

# R-数据库施工规范

## 使用场景
rebuild 平台自身建设过程中，任何涉及数据库的施工必须遵守本规范。

## 核心规范（引用详述源）

详见 `产物/草稿/R6-数据库编写规范.md`（11 章）。核心要点：

1. **选型**：SQLite 开发/测试 + PostgreSQL 生产路径
2. **模型**：SQLAlchemy 2.0 DeclarativeBase，所有模型继承 `app.models.base.Base`
3. **Migration**：Alembic autogenerate → 人工检查 → upgrade/downgrade 可回滚
4. **Repository**：通过 Repository 访问 DB，不直接操作 Session
5. **Seed**：仅当表为空时插入，不含真实 Key
6. **测试**：每个测试独立 SQLite :memory: 或 tmp_path
7. **加密**：Key 字段必须 AES-256-GCM 加密（`app.security.byok_crypto`）
8. **禁止明文 Key 落盘**

## 禁止事项

- 禁止在模型中直接保存明文 Key/Token/Secret/Password
- 禁止绕过 Repository 直接操作 Session
- 禁止在 API route 中直接使用 `db.execute()`
- 禁止手动修改已 applied 的 migration
- 禁止 migration 没有 downgrade 路径
- 禁止在 seed/fixture 中包含真实 Key

## 关联

- `产物/草稿/R6-数据库编写规范.md`（详述源）
- `AGENTS.md` §18（通用禁止事项）
- `rebuild-work-guidelines/SKILL.md` §4（施工安全网）
