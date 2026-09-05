---
name: P-fastapi-patterns
description: P4 执行 — 当迁移目标选 Python/FastAPI 时的项目结构、事务服务层与测试规范（目标非 Python 时仅作参考）
metadata:
  series: P
  phase: P4
  category: execution_skill
  status: platform_runtime
  source: ECC skills/fastapi-patterns (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-fastapi-patterns（FastAPI 施工模式）

## 适用阶段与触发条件
P4 执行阶段。**仅当**迁移目标技术栈明确选定 Python/FastAPI 时为强制规范；多数信创迁移目标为 .NET/Java（迁往达梦+东方通/宝兰德），此时本 skill 仅作架构参考，不强制套用。另一适用场景：平台自身后端的参考实现。重申——本 skill 面向「目标为 Python」的迁移，请先确认目标栈再触发。

## 输入
- P2/P3 选型结论中确认目标栈为 Python/FastAPI 的决策记录
- 源系统接口契约（OpenAPI/WSDL/控制器清单）、领域模型、事务边界清单
- 目标 DB 选型（达梦/openGauss/GaussDB）及其 Python 驱动（dmPython、psycopg/asyncpg 兼容层）
- 认证体系要求（JWT、统一身份对接）

## 执行步骤
1. **分层结构**：`api/`（router）→ `services/`（业务+事务）→ `repositories/`（数据访问）→ `models/`（ORM）→ `schemas/`（Pydantic v2）。router 不写业务逻辑，repository 不被 router 直接调用。
2. **Pydantic v2 全覆盖**：请求/响应/配置全部用 `BaseModel`，启用 `model_config` 严格校验；DB 模型与 API schema 分离，禁止 ORM 对象直出。
3. **依赖注入（DI）**：DB session、当前用户、权限校验均通过 `Depends` 注入；session 生命周期绑定请求，结束自动关闭。
4. **async 处理**：I/O 密集路径用 `async def` + async 驱动；达梦若无成熟 async 驱动则用线程池包裹同步驱动，避免阻塞事件循环。
5. **事务服务层**：一个业务用例对应一个 service 方法，方法内开启/提交/回滚事务，保证迁移后事务语义与源系统等价（隔离级别需对齐达梦/openGauss 默认）。
6. **JWT 认证**：token 校验放 DI 依赖；密钥从环境/密钥管理读取，绝不硬编码。
7. **测试**：用 `httpx.AsyncClient` + pytest 写接口契约测试与事务回滚测试，对照源系统接口断言行为等价。

## 输出 / 产物（Artifact / Evidence）
- FastAPI 项目骨架（分层目录、依赖配置 pyproject）
- OpenAPI schema（自动生成，供 P5 契约比对）
- 服务层事务实现与单元/集成测试代码
- pytest 运行报告（通过/失败/覆盖率）
- 与源系统接口的契约映射表

## 质量门 / 验收标准
- 分层无越界调用（router 不碰 repository，无业务逻辑泄漏到 router）
- Pydantic v2 校验覆盖全部入参出参，无裸 dict 出入
- 关键事务用例具备回滚测试且通过
- 接口契约与源系统逐项映射，差异有记录与说明
- 无明文密钥/连接串，密钥经环境或密钥管理注入
- pytest 全绿，覆盖核心业务路径

## 场景要点（按项目场景取用）
- 达梦/openGauss 与 PostgreSQL 在 SQL 方言、分页、序列、大小写、字符集上有差异，repository 层 SQL 需按目标 DB 适配，不能照搬源 MSSQL/Oracle。
- 部署目标为麒麟 on-prem，须确认目标 OS 上 Python 版本与 C 扩展（dmPython 等）可编译安装，离线环境需预置 wheel。
- 源系统事务隔离级别与锁行为须在目标 DB 复现，避免迁移后并发语义偏移。

## 反例 / 禁止
- 禁止在目标栈未确认为 Python 时强行套用本 skill
- 禁止 router 直接写 SQL 或直接操作 DB session
- 禁止 ORM 模型对象直接作为 API 响应返回（须经 schema）
- 禁止同步阻塞调用混入 async 路径而不做隔离
- 禁止硬编码 JWT 密钥、DB 密码、连接串

## 与平台集成
- 任何用模型辅助生成代码/契约的调用经 ModelGateway；模型生成的接口实现须经契约测试验证，模型输出不等于 Evidence。
- DB schema 变更、对外发布接口等外部写动作（L4-L5）须经用户 Gate。
- 项目骨架、OpenAPI schema、测试报告挂 Artifact/Evidence，施工动作记 Trace/Audit，产物登记 Registry。
- pytest 自验属开发自验，不替代 P5 验证与 D 验收。

## 参考
- ECC skills/fastapi-patterns（MIT）
- P-eval-harness（迁移正确性评估）
- P-benchmark（性能基线）
- R-数据库施工规范（平台自身 DB 规范，可参照分层与事务约束）
