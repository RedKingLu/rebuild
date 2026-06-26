---
name: P-error-handling
description: P4/P5执行 — 迁移运行期错误处理：类型化异常体系、指数退避重试、熔断、对外友好对内可诊断、不吞异常
metadata:
  series: P
  phase: P4
  category: execution_skill
  status: platform_runtime
  source: ECC skills/error-handling (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---

# P-error-handling（迁移运行期错误处理模式）

## 适用阶段与触发条件
- 阶段：P4 执行（建立错误处理）与 P5 验证（暴露并诊断运行期错误）。
- 触发：迁移后服务因国产 DB 驱动、SQL 方言、中间件配置不匹配产生运行期错误，需要可诊断、可恢复、对外可控的错误处理。
- 不适用：编译期/静态问题（属各执行 skill）。

## 输入
- 迁移后服务代码、依赖的信创 DB 驱动与中间件（东方通/宝兰德）。
- P5 暴露的错误样本、栈、日志；可重试/不可重试动作清单。

## 执行步骤
1. **类型化异常体系**：定义领域异常层次（如 `MigrationError` 基类 → `SchemaMismatchError`/`DriverError`/`DialectError`/`TransientDbError`），区分可恢复与不可恢复、可重试与不可重试，便于上层按类型处理。
2. **throw vs Result 取舍**：真正异常（不可恢复、违反不变量）用 throw；预期可失败的业务结果（校验失败、未命中）用 Result/返回值表达，避免用异常做控制流。
3. **框架级统一处理器**：在边界（ASP.NET Core 中间件、Spring `@ControllerAdvice`）集中捕获，映射到统一错误响应；业务代码不散落 try/catch 兜底。
4. **重试 + 指数退避**：仅对幂等且瞬时性错误（连接抖动、`TransientDbError`、超时）重试，退避加抖动（如 base*2^n + jitter），设最大次数与上限；非幂等/逻辑错误不重试。
5. **熔断**：对持续失败的下游（信创 DB、远程中间件）加熔断器，失败率超阈快速失败并半开探测恢复，防止雪崩与连接耗尽。
6. **对外友好 / 对内可诊断**：对外返回稳定错误码 + 安全文案，不泄露栈/SQL/连接串/内部结构；对内记录完整结构化日志（错误类型、上下文、trace id、根因），并触发 Audit。
7. **不吞异常**：捕获后必须处理、转换或带上下文重抛，禁止空 catch、禁止吞掉后返回成功。

## 输出 / 产物（Artifact / Evidence）
- 异常体系与错误码定义、统一处理器代码 — Artifact。
- 重试/熔断策略配置（阈值、退避参数）— Artifact。
- 故障注入/恢复验证结果（重试生效、熔断触发与恢复）— Evidence（实测）。
- 错误日志样本（含对外文案与对内诊断分离证据）— Evidence。

## 质量门 / 验收标准
- 错误按类型可区分处理，可重试/不可重试边界清晰。
- 重试仅作用于幂等瞬时错误，有退避+上限；熔断可触发并恢复（实测）。
- 对外响应不含栈/SQL/连接串/Key 等内部信息。
- 无空 catch、无吞异常后伪成功。

## 信创迁移要点
- 国产 DB 驱动异常类型与原生不同（SQLState/错误码差异），须建映射，避免把可重试的连接抖动误判为永久失败。
- 方言不匹配（语法/分页/NULL 语义）多在运行期暴露，应归类为 `DialectError` 并指向具体 SQL，便于回到 P-database-migrations 修正。
- 东方通/宝兰德等中间件连接池耗尽、超时配置不当易引发级联失败，熔断 + 连接池配额是关键防线。
- 中文错误信息编码（GBK/UTF-8）须统一，防止日志与对外文案乱码。

## 反例 / 禁止
- 禁止空 catch 或捕获后吞掉异常返回成功。
- 禁止对非幂等动作或逻辑错误盲目重试。
- 禁止对外响应泄露栈、SQL、连接串、Key/Token/Secret。
- 禁止用异常做正常控制流。
- 禁止无上限/无退避的重试（放大故障）。

## 与平台集成
- 错误诊断/归因若用模型，经 ModelGateway，不直连 Provider；模型分析非 Evidence，须实测复现验证。
- 日志/Audit 中严禁记录明文 Key/Token/Secret；关键错误触发 Audit 事件。
- 异常体系/策略配置/验证结果挂 Project/Run/Stage/Node，登记 Registry，产生 Trace/Audit。
- 自验证不替代 P5 验收与 D-级 Acceptance。

## 参考
- ECC skills/error-handling（MIT）— 类型化异常层次、throw vs Result、框架处理器、指数退避、熔断、对外/对内消息分离。
- P-database-migrations、P-backend-patterns；03-Agent与Skill规范 §6/§7（ModelGateway/Gate/Trace/不存密钥）。
