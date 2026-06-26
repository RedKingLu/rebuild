---
name: P-backend-patterns
description: P4执行 — 迁移后服务分层(repository/service/router)、查询优化(避N+1/信创执行计划差异)、缓存与连接池
metadata:
  series: P
  phase: P4
  category: execution_skill
  status: platform_runtime
  source: ECC skills/backend-patterns (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---

# P-backend-patterns（迁移后后端架构模式）

## 适用阶段与触发条件
- 阶段：P4 执行。迁移后服务需达到可维护、可观测、性能不退化的工程标准。
- 触发：组织迁移后服务的分层结构、设计 API、排查查询性能、配置缓存与连接池。
- 技术栈无关原则为主（.NET/ASP.NET Core、Java/Spring 等同样适用）。
- 不适用：DB schema/数据迁移（用 P-database-migrations）；.NET 语言级模式（用 P-dotnet-patterns）。

## 输入
- 迁移后的服务代码与目标信创 DB（达梦/openGauss/GaussDB）连接信息。
- 迁移前性能基线（关键接口 P95、QPS、慢查询清单）。

## 执行步骤
1. **分层与单向依赖**：router/controller（协议与校验）→ service（业务编排与事务边界）→ repository（数据访问）。依赖单向向下，禁止跨层与反向依赖；DB 细节封死在 repository 层。
2. **API 设计**：REST 资源命名一致、HTTP 语义正确（幂等性、状态码、分页/过滤约定）；如用 GraphQL 防止字段层级 N+1。契约稳定、版本化，迁移后对前端/调用方保持兼容。
3. **消除 N+1**：识别循环内查询，改为批量/`JOIN`/预加载（EF Core `Include`、Spring `@EntityGraph`）。在信创 DB 上用其执行计划工具复核（达梦/GaussDB 的 `EXPLAIN` 与 Oracle/MSSQL 不同），按真实计划而非旧库经验优化。
4. **索引与查询适配**：迁移后重建并验证索引；注意国产 DB 优化器选择、分页写法、统计信息收集差异，必要时调整 SQL 或加 hint（按目标库支持）。
5. **缓存**：对热点只读数据加缓存（进程内/Redis），明确失效策略与一致性边界，避免缓存放大旧库假设的数据形态。
6. **连接池**：使用目标 DB 驱动配置连接池（最大连接、超时、空闲回收），匹配信创 DB 的最大连接限制与隔离级别默认值，避免连接耗尽与长事务。
7. **错误与可观测**：统一错误处理（衔接 P-error-handling），关键路径加结构化日志与指标，便于回归对比基线。

## 输出 / 产物（Artifact / Evidence）
- 分层后的代码结构与改造 diff/PR — Artifact。
- 查询优化前后的执行计划与耗时对比 — Evidence（实测）。
- 缓存与连接池配置文件 — Artifact。
- 接口性能回归报告（对比迁移前基线）— Evidence。

## 质量门 / 验收标准
- 分层依赖单向、无跨层调用；DB 访问全部经 repository。
- 已知 N+1 全部消除；关键接口性能不低于迁移前基线（P95/QPS）。
- 连接池配置不超目标 DB 限制，压测无连接耗尽。
- 缓存有明确失效策略，无脏读越界。

## 信创迁移要点
- 信创 DB 优化器与索引策略和 Oracle/MSSQL 不同：旧库高效的 SQL 在达梦/GaussDB 可能走全表扫，必须以目标库真实执行计划为准重新优化。
- 国产 DB 驱动的连接池/超时/隔离级别默认值与原生不同，需显式配置并压测验证。
- 分页（`ROWNUM` vs `LIMIT/OFFSET`）、空串/NULL、大小写折叠差异会改变查询结果与计划，repository 层须适配。
- 统计信息收集（ANALYZE/收集统计）在迁移后需主动执行，否则计划失真。

## 反例 / 禁止
- 禁止在 controller/router 直接写 SQL 或访问 DB。
- 禁止照搬旧库 SQL 不验证目标库执行计划。
- 禁止无失效策略的缓存（导致脏数据）。
- 禁止连接池上限超过目标 DB 承载或留默认值不验证。

## 与平台集成
- 涉及代码生成/重构的模型调用经 ModelGateway，不直连 Provider；模型产出非 Evidence，须实测验证。
- 修改生产仓库、改连接池/缓存等运行配置、部署为外部写动作，达 L4-L5 时需用户 Gate。
- diff/执行计划/性能报告挂 Project/Run/Stage/Node，登记 Registry，产生 Trace/Audit。
- 自验证不替代 D-级 Acceptance。

## 参考
- ECC skills/backend-patterns（MIT）— 分层 repository/service、REST/GraphQL 设计、N+1、缓存、错误处理。
- P-database-migrations、P-error-handling、P-dotnet-patterns；03-Agent与Skill规范 §6/§7。
