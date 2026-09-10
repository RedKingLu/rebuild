# 01-ModelGateway与模型策略

> 文档路径：`文档/04-模型与资源/01-ModelGateway与模型策略.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/草稿/01-ModelGateway与模型策略.md`（v0.1，~840 行；去重 ~50%，主要移除字段枚举 + 上游决策复述）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R2
> 最后更新时间：2026-06-24
> 修订说明：R2：新增引用 D-073 平台助手（§6.5 模型连通性自测复用 ModelGateway + 不改默认 ModelStrategy + 脱敏）；R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`04-模型与资源/` 专题的第 1 份子文档。定义 ModelGateway 的薄抽象层设计、LiteLLM SDK Adapter 边界、Model Policy/Profile 概念、模型选择解析流程、Fusion 策略规则、token/成本预算归口、错误处理与 fallback、模型输出与 Evidence 边界。ModelGateway/Fusion/模型选择的权威定义见 `02-架构设计/00-架构总纲.md` §2.5+§7 和 `01-决策记录.md` D-035/D-036/D-039/D-064/D-065。
> 上级依据：`文档/04-模型与资源/00-模型与资源总览.md`、`文档/02-架构设计/00-架构总纲.md`、`文档/00-项目治理/01-决策记录.md`。
> 重要边界：本文是 ModelGateway 与模型策略的**操作规范**（职责边界、解析流程、fallback 规则、Evidence 边界、安全脱敏、Fusion 合入规则），不固化最终 API/DB schema（归 R4/R5），不重复架构层概念定义。

---

## 0. 编写原则

本文遵守事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

权威源引用：

| 概念 | 详述源 |
|---|---|
| ModelGateway 定位与路线 | `01-决策记录.md` D-039；`00-架构总纲.md` §2.5+§7.1 |
| 模型选择不绑定阶段 + 5 级优先级 | `01-决策记录.md` D-036；`00-架构总纲.md` §7.2 |
| Fusion 作为模型能力/策略 | `01-决策记录.md` D-035；`00-架构总纲.md` §7.3 |
| token/成本预算归口 | `01-决策记录.md` D-064 |
| 薄编排/NIH 约束 | `01-决策记录.md` D-065；`AGENTS.md` §13.0 |
| 风险分级 L0-L5 | `02-术语表.md` 第七部分 |
| 密钥安全 | `AGENTS.md` §12 |

本文必须遵守：LangGraph 是主编排底座（D-037）；当前暂定路线 rebuild ModelGateway + LiteLLM SDK Adapter（D-039）；模型输出不自动成为事实/Evidence/授权/验收结论；密钥全程脱敏。

---

## 1. ModelGateway 定位与调用链

> 权威定义见 D-039 和 `00-架构总纲.md` §7.1。本文只展开操作约束。

ModelGateway 位于：

```text
Agent / Skill 与底层模型 Provider 之间；
LangGraph node 调用链路之下；
LiteLLM SDK Adapter 之上；
Policy / Trace / Audit 约束之内。
```

调用链：

```text
LangGraph node → Agent/Skill → ModelGateway → LiteLLM SDK Adapter → Provider
```

ModelGateway 负责 7 件事：

```text
1. 统一模型调用入口       5. 统一错误处理和 fallback 边界
2. 统一模型选择解析       6. 统一密钥脱敏边界
3. 统一模型策略应用       7. 为 Fusion/Model Profile/token 预算提供归口
4. 统一调用 Trace
```

ModelGateway 不负责：编排 P0-P6、替代 LangGraph、替代 Agent/Skill/Tool/MCP、替代 Evidence/Gate/Policy/验收裁决。

---

## 2. LiteLLM SDK Adapter 边界

> 权威定义见 D-039 和 D-065。

```text
LiteLLM SDK Adapter 负责：
1. 调用具体 Provider      3. 处理 Provider 参数映射
2. 承接通用多服务商适配    4. 返回底层模型响应与错误
```

ModelGateway 对 LiteLLM 的封装必须保持薄。**不得**：

```text
1. 重复实现 LiteLLM 已覆盖的 Provider 通用适配
2. 自研一整套 Provider SDK
3. 把 ModelGateway 做成重型代理服务（除非走架构变更申请）
4. 提前引入 LiteLLM Proxy/Portkey/OpenRouter 等替代路线（除非提出申请+影响分析+验收方式）
```

---

## 3. 模型调用职责边界

这是本文的核心操作规范——谁负责什么、边界在哪。

### 3.1 Agent / Skill 负责

```text
明确任务目标 → 组装上下文引用 → 指定/引用 Model Policy
→ 调用 ModelGateway → 解释模型输出 → 生成 Artifact/Evidence 候选
→ 执行自验证 → 交给 NodeLoop/Acceptance/TaskGraph
```

### 3.2 ModelGateway 负责

```text
接收模型调用请求 → 解析模型选择优先级 → 应用 Model Policy
→ 调用 LiteLLM SDK Adapter → 记录 Trace → 处理错误/fallback/重试
→ 返回结构化结果 → 处理脱敏和调用元数据
```

### 3.3 Provider Adapter 负责

```text
与具体服务商通信 → 返回响应 → 报告底层错误
不直接决定业务流程、不直接写入 Artifact/Evidence/Gate。
```

---

## 4. Model Policy

Model Policy 是模型调用的**策略约束**。它回答：允许用哪些模型、什么条件下允许 Fusion/流式/工具调用、上下文限制、fallback 策略、重试策略、成本预算、脱敏要求、Trace 要求、风险限制。

核心规则（概念级，字段定义见 R5）：

```text
1. Model Policy 是约束，不是建议——Policy 禁止项不得由 Agent 或模型输出覆盖
2. scope 决定适用范围：system > project > agent > task
3. fallback 必须符合 Policy——不得切换到 Policy 禁止模型
4. Model Policy 不得保存明文密钥
```

> R4/R5 校准：Model Policy 具体字段（model_policy_id, allowed_models, fallback_policy, retry_policy, cost_budget_policy, redaction_policy 等 ~15 项）由 R5 模型网关施工时定义。本文不固化最终 schema。

---

## 5. Model Profile

Model Profile 是对模型**能力、用途和约束的描述**。它回答：这个模型能做什么、推荐/不推荐用途、上下文能力、成本等级、是否支持 Fusion。

核心规则：

```text
1. Model Profile 是能力说明，不是阶段绑定（D-036）
2. 推荐用途不是硬规则；禁止用途如来自 Policy 则必须执行
3. Model Profile 变更必须可追踪
4. 模型能力描述不得夸大未验证能力
```

> R4/R5 校准：Model Profile 具体字段（model_profile_id, provider, capability_tags, context_window_note, cost_tier, is_fusion_capable 等 ~13 项）由 R5 定义。本文不固化最终 schema。

---

## 6. 模型选择解析

> 5 级优先级来自 D-036。本文展开解析流程和记录要求。

### 6.1 优先级（从高到低）

```text
1. 用户临时指定        → 最高优先，覆盖所有下级
2. Task/Node override  → 任务级覆盖
3. Agent 默认模型      → Agent Definition Contract 指定
4. Project 默认模型    → Project 级设置
5. System 默认模型     → 全局 fallback
```

### 6.2 解析流程

```text
读取调用请求 → 读取用户临时指定 → 检查 Task/Node override
→ 检查 Agent 默认模型 → 检查 Project 默认模型 → 回退 System 默认模型
→ 应用 Model Policy 限制 → 校验 Provider 配置 → 记录选择依据 → 发起调用
```

### 6.3 必须记录

```text
selected_model / selection_reason / policy_ref / override_source / provider / trace_ref
```

### 6.4 禁止

```text
1. 某阶段硬编码固定模型（违反 D-036）
2. 忽略用户临时指定
3. 忽略 Policy 限制
4. 未记录模型选择原因
5. 因模型不可用而静默切换且不记录
```

### 6.5 平台助手的模型连通性自测（引用 D-073）

平台助手（AI 精灵 / 客服对话 Agent）支持在助手对话内"切换模型/服务商验证是否接入成功"。该自测**复用 ModelGateway**（同一 Provider/Profile 调用与脱敏通道），不另起调用路径。

```text
1. 自测复用 ModelGateway 的同一 Provider/Profile 调用链与脱敏边界，不自研旁路
2. 自测仅作用于助手自身会话，不改默认 ModelStrategy（不改平台/项目默认模型与选择优先级）
3. 不泄露 Key——凭据只存引用，后端为脱敏事实源（与 §10、D-032 一致）
4. 平台助手独立于 P0-P6 与 Agent/Skill 资源体系，不进 LangGraph 主编排
```

> 详见 `01-决策记录.md` D-073。本文只记本模块特有项（复用 ModelGateway + 不改默认策略 + 脱敏），不复制 D-073 全文。

---

## 7. 模型调用请求/响应规则

> 具体字段由 R4/R5 API 契约定义。本文只规定操作规则。

### 7.1 调用规则

```text
1. prompt 中不得包含明文密钥
2. input_context_refs 应保留来源引用
3. 模型输出必须标记来源为"模型输出"
4. 模型输出不得直接成为 Evidence validated
5. 模型输出如用于决策，必须经过 Acceptance/Gate/Evidence 流程
6. 模型调用失败必须记录 error_ref 和 Trace
```

### 7.2 调用必须记录

```text
调用方（project_id/run_id/stage/node_id/agent_ref/skill_ref）
模型选择（model_policy_ref/model_override/selected_model/selection_reason）
风险上下文（risk_level/trace_parent_ref）
结果（status/output_ref/usage_summary/error_ref/fallback_used/retry_count）
```

---

## 8. Fusion 策略

> 权威定义见 D-035 和 `00-架构总纲.md` §7.3。Fusion 是模型能力/策略，不是特殊流程。本文展开操作规则。

### 8.1 Fusion 是什么 / 不是什么

```text
可作为：Model Profile / Model Strategy / 多模型综合策略 / 评审辅助 / 风险分析辅助
不得作为：P 阶段 / 特殊流程 / 特殊评审引擎 / 独立运行时角色 / 授权主体 / 验收主体 / Policy 豁免机制
```

### 8.2 Fusion 调用规则

```text
1. 必须经 ModelGateway
2. 必须记录 Trace
3. 高风险用途必须可触发 Gate
4. 输出必须标记为"模型输出"
5. 不自动通过验收 / 不自动批准执行
6. 不绕过 Policy/Gate/Audit
```

### 8.3 Fusion 合入规则（R13）

```text
1. R13 可建设 Fusion 功能并合入
2. 合入前必须检查：是否污染 P0-P6 主流程 / Policy/Gate/Audit / 测试和验收依据
3. Fusion 不得反向要求重写主编排
```

---

## 9. token / 成本预算

> 权威定义见 D-064。token/成本预算是模型模块功能，非 P0-P6 主线。

```text
可记录：模型调用次数/输入输出 token/估算成本/Project-Run-Agent-Skill-Task 各级预算/超预算行为
```

核心规则：

```text
1. 不列为 P0-P6 主线默认 Gate 条件（D-064）
2. 可在 R5 或之后按需实现
3. 可为用户提供预算提示；超预算是否 Gate 需后续单独策略确认
4. 成本记录不得形成第二套模型调用事实源
```

> R5 校准：usage_record 字段由 R5 定义。

---

## 10. 密钥与配置

模型配置可包含：Provider 名称、模型名称、endpoint、region、api version、能力标签、密钥需求引用、默认参数、限流参数、可用状态。

密钥硬规则（`AGENTS.md` §12）：

```text
1. 可识别 env/配置文件/密钥文件是否存在
2. 不得在报告/日志/前端/Trace/截图/提交/交接中泄露 Key/Token/Secret/Password
3. ModelGateway 不得将密钥暴露给 Agent 提示词
4. Registry 不得保存密钥明文
5. Trace/Audit 只记录密钥引用或脱敏标识
6. 发现密钥泄露风险必须暂停 → Gate 或安全处理流程
```

> 密钥脱敏（D-032）、L5 强制 Gate（D-034）等安全红线就地保留；通用禁止与红线完整总表另见 AGENTS.md §18。

---

## 11. 错误处理与 fallback

### 11.1 失败模式（10 种）

```text
Provider 不可用 / 模型不存在 / 认证失败 / 限流 / 上下文超限
/ 输出格式不符 schema / 网络异常 / 超时 / Policy 禁止 / 预算限制
```

### 11.2 fallback 规则

```text
1. fallback 必须符合 Model Policy
2. fallback 必须记录 Trace + selected_model + selection_reason
3. fallback 不得绕过用户指定的硬约束
4. fallback 不得切换到 Policy 禁止模型
5. 高风险任务 fallback 可触发 Gate
6. fallback 失败应返回明确错误，不得伪装成功
```

### 11.3 重试规则

```text
1. 重试次数由 retry_policy 控制；重试必须记录 retry_count
2. 重试不得重复执行外部写操作
3. 输出不稳定时应进入自验证或 Acceptance
4. 多次失败应升级为 blocked 或 gate_required
```

---

## 12. 模型输出与 Evidence 边界

这是本文的关键操作规范之一。

### 12.1 模型输出可以用于

```text
分析建议 / 方案草案 / 风险提示 / 代码修改建议 / 验证报告草案 / 交付说明草案 / 辅助判断
```

### 12.2 模型输出不得直接成为

```text
accepted 决策 / 用户授权 / Policy 豁免 / Evidence validated
/ P5 验证通过 / P6 交付完成 / 高风险动作批准
```

### 12.3 模型输出进入 Evidence 的前提（5 条）

```text
1. 有可验证来源   3. 有验证方法   5. Trace 完整
2. 有对应 claim   4. 经过自验证/Acceptance/测试/用户 Gate 等适用流程
```

---

## 13. 与其他组件的关系（速查）

| 组件 | 关键约束 |
|---|---|
| Agent/Skill | 必须通过 ModelGateway 调用；不直接调用 Provider；对模型输出自验证；不将输出直接标记为事实 |
| TaskGraph/NodeLoop | Edge 可声明 model_policy_override；Node 声明模型需求；调用结果回到 NodeLoop；失败进入 failure_policy |
| Gate/Policy/Audit | Policy 优先于模型输出；模型不能批准 Policy 禁止项；低置信度/冲突必须 Gate；Gate 不得由模型自动关闭 |
| API/前端 | 前端可展示可选模型/选择依据/调用状态；必须显式标记 mock；前端状态不是模型配置事实源 |

---

## 14. 模型调用 Trace

模型调用必须可追踪。Trace 至少记录：

```text
调用标识（model_call_id）
调用方（project_id/run_id/stage/node_id/agent_ref/skill_ref）
模型选择（selected_model/provider/model_policy_ref/selection_reason）
输入输出（input_context_refs/output_ref/status/error_ref）
用量（usage_summary）
时间（created_at/completed_at）
```

Trace 不得记录：明文 Key/Token/Secret/Password、未脱敏 env、不必要的大段敏感源码、未经确认的临时推断作为事实。

> R4/R5 校准：Trace 具体字段和存储方案由 R4（工程骨架）和 R5（模型网关）定义。

---

## 15. R2/R4/R5/R13 校准项

### R2 文档校准

```text
1. 是否对齐最新决策记录（D-035/D-036/D-039/D-064/D-065）
2. Fusion 是否被误写为特殊流程
3. token/成本预算是否被误写为主线 Gate
4. 是否仍有 Mission/mission_id 残留
5. Model Policy/Profile 字段粒度是否合适
6. 密钥脱敏规则是否与安全文档一致
```

### R4 工程骨架

```text
ModelGateway 与后端服务层边界；模型配置存储；Trace 存储；API/SSE 契约；Policy/Hook 接入；密钥引用机制
```

### R5 模型网关

```text
ModelGateway 抽象实现；LiteLLM SDK Adapter 集成；Model Policy/Profile；模型选择解析器；fallback/retry；Fusion 接入方式；token/成本预算；模型调用 Trace
```

### R13 Fusion

```text
Fusion 是否仍作为模型能力；是否经 ModelGateway；是否不绕过 Policy/Gate/Audit；是否不污染 P0-P6 主流程
```

---

## 16. 规范红线

```text
1. 不得把 ModelGateway 做成重型 Provider 框架
2. 不得自研替代 LiteLLM 已覆盖的多服务商适配能力（除非走架构变更申请）
3. 不得让 Agent/Skill 绕过 ModelGateway 直接调用 Provider
4. 不得让前端直接调用 Provider
5. 不得把 Fusion 写成特殊流程/特殊阶段/特殊评审引擎/独立运行时角色
6. 不得让 Fusion 绕过 Policy/Gate/Audit
7. 不得把 token/成本预算写成 P0-P6 主线默认 Gate 条件
8. 不得用模型输出替代 Evidence
9. 不得用模型判断替代用户授权
10. 不得用模型输出批准高风险动作
11. 不得无 Trace 调用模型
12. 不得泄露 Key/Token/Secret/Password
13. 不得引入 Mission 产品层
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 17. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 ModelGateway 定位与调用链（§1）
2. 明确 LiteLLM SDK Adapter 边界与禁止事项（§2）
3. 明确三层职责边界——Agent/Skill vs ModelGateway vs Provider Adapter（§3）
4. 明确 Model Policy 概念与核心规则（§4）
5. 明确 Model Profile 概念与核心规则（§5）
6. 明确模型选择 5 级优先级 + 10 步解析流程 + 记录要求 + 禁止事项（§6）
7. 明确模型调用请求/响应的操作规则（§7）
8. 明确 Fusion 是什么/不是什么 + 调用规则 + 合入规则（§8）
9. 明确 token/成本预算归口与规则（§9）
10. 明确密钥与配置安全规则（§10）
11. 明确错误处理——10 种失败模式 + 7 条 fallback 规则 + 5 条重试规则（§11）
12. 明确模型输出与 Evidence 边界——3 类可用/7 类不可直接成为/5 条进入前提（§12）
13. 明确与其他组件的关系速查（§13）
14. 明确模型调用 Trace 要求（§14）
15. 明确 R2/R4/R5/R13 校准项（§15）
16. 明确规范红线 13 条（§16）
17. 未固化最终 API/DB schema——Model Policy/Profile/Trace 字段标记 R4/R5 定义
18. 未引入 Mission 产品层
19. 未新增产品决策
```
