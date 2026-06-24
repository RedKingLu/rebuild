# 03-NodeLoop与执行模式

> 文档路径：`文档/03-流程与运行时/03-NodeLoop与执行模式.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.1.1
> 来源草稿：`产物/草稿/03-NodeLoop与执行模式.md`（v0.1，~929 行；去重 ~55%，主要移除字段/状态枚举 + 三模式复述压缩为一节）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R2
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`03-流程与运行时/` 专题的第 3 份子文档。定义 NodeLoop 标准小循环（9 步规范 + 每步检查清单）、三种执行模式下 NodeLoop 的行为差异、模式切换规则、异常升级规则、Acceptance 与自验证的边界。
> 上级依据：`文档/03-流程与运行时/00-流程与运行时总览.md`、`文档/03-流程与运行时/02-StagePlan-TaskPlan-TaskGraph规范.md`、`文档/02-架构设计/02-LangGraph主编排架构.md`。
> 重要边界：执行模式（Manual/Plan/Auto）的完整定义见 `02-架构设计/02-LangGraph主编排架构.md` §11 和 `AGENTS.md` §15。本文只定义 NodeLoop 规范 + 各模式下 NodeLoop 的行为差异——不复述模式本身的审核规则和 Gate 策略。
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

```text
权威源引用（本文只引用，不重新定义）：
- Manual/Plan/Auto 执行模式完整定义 → 02-架构设计/02-LangGraph主编排架构.md §11
- Task Node 类型/状态字段/边策略     → 02-StagePlan-TaskPlan-TaskGraph规范.md
- TaskGraph 操作规则                 → 02-StagePlan-TaskPlan-TaskGraph规范.md
- NodeLoop 状态字段                  → 02-架构设计/03-Project-Run-TaskGraph状态架构.md
- Gate 类型/interrupt/resume          → 04-Gate与中断恢复流程.md
- P5 验证 Evidence                   → 08-测试与验收/02-迁移正确性与验证策略.md
```

本文必须遵守：

```text
1. LangGraph 是主编排底座——NodeLoop 不得替代 LangGraph（D-037/D-065）；
2. NodeLoop 是 Task Node 内部标准小循环，TaskGraph 负责任务间依赖/边策略/路由；
3. Manual/Plan/Auto 只影响阶段内部 Gate 密度——不改变 P 阶段晋级必须用户 Gate；
4. 高风险/低置信度/冲突/超范围必须回用户或 Gate；
5. Policy 优先级高于 Agent 判断；
6. 执行动作必须有 Trace，高风险动作和 Gate 决策必须有 Audit（D-066）；
7. 自验证 ≠ Acceptance ≠ 用户 Gate ≠ P5 验证——四层不可相互替代。
```

本文不得：

```text
1. 用 NodeLoop 替代 LangGraph 主编排；
2. 用 NodeLoop 隐藏 TaskGraph 边策略；
3. 用 Agent 自评替代 Acceptance；
4. 用 Auto Mode 绕过 Gate；
5. 用模型判断替代 Evidence；
6. 用执行成功替代验证通过。
```

> 本节"必须遵守/本文不得"中的通用红线（NodeLoop 不替代 LangGraph 主编排 D-037/D-065、Manual/Plan/Auto 不改 P 阶段晋级 D-024/D-025、Policy 优先 D-031、执行须 Trace 高风险须 Audit D-066 等）完整总表见 AGENTS §18 + 01-决策记录；本文仅就地保留与 NodeLoop 职责相关的子集。

---

## 1. NodeLoop 定位

NodeLoop 是每个 Task Node 内部的标准小循环。它解决 9 个问题：

```text
1. 收到任务后如何读上下文；
2. 如何生成节点内计划；
3. 如何判断是否拆分子任务；
4. 如何调用 Agent/Skill/Tool/MCP/Execution Session；
5. 如何生成 Artifact/Evidence；
6. 如何执行自验证；
7. 如何生成节点产物包；
8. 如何交给 Acceptance Agent 验收；
9. 如何根据验收结果回到 TaskGraph 路由。
```

NodeLoop 不是：主编排引擎、阶段流转引擎、TaskGraph 替代品、Gate 替代品、Acceptance 替代品。

---

## 2. NodeLoop 标准 9 步循环

```
task_received → context_loaded → node_plan_created → split_decision
  → execution_started → execution_finished
  → self_check_started → self_check_finished
  → node_package_created → acceptance_requested
  → acceptance_result_received → route_decided
```

---

## 3. NodeLoop 逐步规范

### Step 1：接收任务

确认收到的任务是否清晰、可执行、在范围内。

检查清单：

```text
□ task_plan_ref 是否存在且可读取；
□ node_id 是否属于当前 TaskGraph；
□ stage 是否匹配当前 Run 的阶段；
□ 输入引用（input_refs）是否存在且可访问；
□ 权限边界（permission_boundary）是否明确；
□ 风险级别（risk_level）是否明确；
□ acceptance_criteria 是否存在。
```

失败处理：

| 情况 | 处理 |
|---|---|
| 缺少关键输入 | → `blocked`，登记缺失项 |
| 范围/目标不清 | → `waiting_gate` 或 `waiting_user_decision` |
| 权限不清 | → `policy_gate` |
| 任务与 Stage Plan 冲突 | → `consistency_conflict`（见 02-StagePlan §10） |

### Step 2：读取上下文

按 Context Recipe 读取必要上下文，避免全量塞入。上下文来源（C0-C6 分层见 02-术语表）：

```text
Global Policy Context / Product Definition Context / Scope Baseline Context
/ Run-Stage Context / Node Task Context / Evidence-Artifact Context
/ Dynamic Retrieval Context
```

规则：

```text
1. 只读取必要上下文——不把全项目状态装入 NodeLoop；
2. 保留来源引用——每条上下文可追溯到出处；
3. 敏感信息必须脱敏；
4. 旧版本材料只读参考，不自动成为当前事实源；
5. 上下文不足必须登记为 uncertain_input，不得脑补。
```

### Step 3：生成节点内计划

将 Task Plan 转换为节点内部可执行步骤。节点内计划必须声明：

```text
执行目标 / 输入 / 输出 / 工具-模型-资源 / 风险 / 权限 / 验证方式 / 失败处理 / 是否可能触发 Gate
```

规则：

```text
1. 节点内计划不得突破 Task Plan 的 scope；
2. 若需要突破，必须先产生 Plan Delta 并 Gate（见 02-StagePlan §5）；
3. 高风险动作必须在计划中显式标记；
4. 计划不得隐藏 Tool/MCP/外部 Agent 调用——调用链必须透明。
```

### Step 4：判断是否拆分子任务

拆分策略：

| 策略 | 适用场景 |
|---|---|
| `inline` | 简单任务，无需拆分 |
| `serial` | 多步依赖任务，顺序执行 |
| `parallel` | 独立任务，无相互依赖 |
| `hybrid` | 混合依赖，部分并行部分串行 |
| `nested_loop` | 需要反复校验的任务（如"修改→构建→测试→修正"循环） |

规则：

```text
1. 拆分后必须进入 TaskGraph 或子 Task Plan——不得在 NodeLoop 内形成隐式子流程；
2. 拆分产生的子任务需有独立的 acceptance_criteria；
3. nested_loop 必须有明确的退出条件（最大迭代次数或收敛标准）。
```

### Step 5：执行

可调用的执行单元：

```text
Node Worker Agent / Skill / Tool / MCP / Expert Agent / Execution Session / ModelGateway
```

规则：

```text
1. 执行动作必须受 Policy/Hook 约束——Policy 禁止的动作不得执行；
2. 高风险执行必须 Gate；
3. 执行必须记录 Trace——每次 Tool/MCP/模型调用可追踪；
4. 高风险执行必须记录 Audit；
5. Tool/MCP/外部 Agent 调用必须记录来源、风险级别、权限要求；
6. Case 只能作为参考材料，不得直接执行；
7. 社区资源默认只读参考，执行需用户确认；
8. 输出必须写回 Workspace 或结构化状态——不得仅存在终端内存中。
```

### Step 6：自验证

节点先检查自身输出是否满足基本要求。自验证检查清单：

```text
□ 输出是否存在且格式正确；
□ Artifact 是否已生成（或明确无需产物）；
□ Evidence 是否已生成或不足原因已登记；
□ Trace 是否完整（无断链）；
□ 高风险动作 Audit 是否存在；
□ 是否满足 acceptance_criteria 的基本项；
□ 是否存在未处理错误；
□ 是否存在范围外变更。
```

自验证不能替代 Acceptance——自验证是"我检查自己"，Acceptance 是"别人检查我"。

### Step 7：生成节点产物包

节点产物包内容：

```text
node_id / task_plan_ref / 执行摘要 / 输入引用 / 输出引用
/ Artifact refs / Evidence refs / Trace refs / Audit refs
/ 自验证结果 / 风险与异常 / 返工建议 / 下一步建议
```

### Step 8：交给 Acceptance Agent 验收

Acceptance Agent 独立于执行 Agent，检查：

```text
1. 是否符合 Task Plan 的目标和范围；
2. 是否符合 acceptance_criteria；
3. 是否有必要 Artifact；
4. 是否有必要 Evidence；
5. Trace 是否完整；
6. Audit 是否完整（高风险动作）；
7. 是否存在越界（超出 permission_boundary）；
8. 是否需要返工、重试或升级 Gate。
```

### Step 9：根据验收结果流转

验收结果与流转：

| 结果 | 流转 |
|---|---|
| `accepted` | 进入 TaskGraph 下一节点（按边策略） |
| `accepted_with_warning` | 带风险继续，记录 warning 和接受理由 |
| `rework_required` | 返回 Step 3（重新计划）或 Step 5（重新执行） |
| `retry_required` | 按 retry_policy 重试（见 02-StagePlan §8.6） |
| `gate_required` | 进入 Gate（见 04-Gate与中断恢复流程） |
| `failed` | 走 TaskGraph failure_policy（见 02-StagePlan §8.7） |
| `blocked` | 登记阻塞原因，等待解除条件 |
| `skipped` | 登记跳过原因，不得伪装 completed |

**流转必须回到 TaskGraph 边策略**——NodeLoop 不得自行决定隐式跳转。

---

## 4. 三种执行模式下的 NodeLoop 行为差异

> 执行模式的完整定义（审核规则/Gate 策略/适用场景）见 `02-架构设计/02-LangGraph主编排架构.md` §11。以下只定义各模式下 NodeLoop 的行为差异。

### 4.1 Manual Mode 下的 NodeLoop

```text
1. Step 3（节点内计划）生成后，如涉及实质修改，先进入 Gate 再执行；
2. Step 5（执行）前更严格检查权限——Tool/MCP/外部 Agent 调用倾向用户确认；
3. Step 8（Acceptance）后用户可要求重试、返工或跳过；
4. Gate 密度最高——计划审核/高风险动作/阶段晋级均需 Gate。
```

### 4.2 Plan Mode 下的 NodeLoop

```text
1. Step 3 必须检查节点内计划是否在已批准 Task Plan 范围内；
2. 计划内低/中风险动作可继续——由 Hook+Policy 放行；
3. 越界动作必须生成 Plan Delta 或 Gate——不得静默扩大范围；
4. 风险升级必须暂停——不得自行降级处理；
5. 不确定项必须回用户——不得自行判断。
```

### 4.3 Auto Mode 下的 NodeLoop

```text
1. NodeLoop 可在策略允许范围内自动推进——Step 3-7 可连续执行；
2. Auto Review Agent 可参与 Step 3（计划审核）和 Step 8（Acceptance）；
3. Hook+Policy 必须先于 Step 5（执行）——Policy 禁止的动作不得由 Agent 批准；
4. 高风险动作（L4-L5）必须 Gate——Auto Mode 不放行高风险；
5. 低置信度（confidence < 阈值）或结论冲突必须回用户；
6. 自验证（Step 6）和 Acceptance（Step 8）不得省略——Auto 不意味着跳过质量关卡。
```

### 4.4 三种模式不变项

```text
1. NodeLoop 9 步循环结构不变；
2. 高风险动作（L4-L5）必须 Gate；
3. 自验证 ≠ Acceptance ≠ 用户 Gate ≠ P5 验证——四层不可替代；
4. 执行必须有 Trace，高风险必须有 Audit；
5. P 阶段晋级必须用户 Gate。
```

---

## 5. 执行模式切换

### 5.1 切换方向

```text
Manual → Plan ：计划稳定、风险可控时
Plan → Auto   ：任务重复、风险低、策略明确时
Auto → Plan   ：出现复杂计划或风险升高时
Plan → Manual ：出现不确定项、权限敏感或用户要求时
Auto → Manual ：出现高风险、冲突、低置信度、Policy 异常时
```

### 5.2 切换规则

```text
1. 模式切换必须记录 Trace（含切换原因和触发条件）；
2. 降低用户参与度的切换（Manual→Plan, Plan→Auto）必须用户确认；
3. 风险升高时必须向更保守模式降级——不得在风险升级时提升自动化；
4. 切换后必须重新检查当前 Stage Plan/Task Plan 是否仍有效；
5. 切换不得跳过阶段晋级 Gate；
6. 切换不得影响已生成 Evidence 的来源链。
```

---

## 6. 异常升级规则

以下情况必须升级至用户或 Gate（按严重度排序）：

| 级别 | 触发条件 | 升级目标 |
|---|---|---|
| 🔴 立即 | L5 风险动作、密钥相关不确定项、Policy 冲突 | `waiting_gate`（强制） |
| 🟠 高风险 | L4 风险动作、外部系统写、删除/不可逆操作 | `waiting_gate` |
| 🟡 中风险 | 范围变化、权限边界不清、Evidence 不足 | `waiting_user_decision` |
| 🟢 注意 | 低置信度结论、Artifact 缺失、Trace/Audit 断链 | `blocked` 或 `rework_required` |

升级后的处置选项：

```text
waiting_gate            — 等待用户 Gate 决策
waiting_user_decision   — 等待用户判断（不一定是完整 Gate）
blocked                 — 阻塞，等待解除条件
rework_required         — 返工，附返工建议
mode_downgrade          — 自动降级执行模式（如 Auto→Plan）
plan_delta_required     — 需要生成 Plan Delta 后继续
```

---

## 7. Acceptance 与自验证边界

四层验证不可相互替代：

```
自验证（Step 6）  — "我检查自己"：输出是否完整、格式是否正确
    ↓
Acceptance（Step 8）— "别人检查我"：是否符合 Task Plan、是否越界
    ↓
用户 Gate          — "用户决策"：是否接受、是否进入下一阶段
    ↓
P5 验证            — "系统验证"：是否正确、是否等价、是否可交付
```

边界规则：

```text
1. 自验证不能替代 Acceptance——必须由独立 Acceptance Agent 检查；
2. Acceptance 不能替代用户 Gate——Acceptance 判断"是否符合计划"，Gate 判断"用户是否接受"；
3. Acceptance 不能替代 P5 验证——Acceptance 检查单节点，P5 验证检查整体正确性；
4. Acceptance 不得批准 Policy 禁止的动作；
5. Acceptance 结论必须可追踪——记录检查项、通过/不通过、理由。
```

---

## 8. Trace/Audit 要求

### NodeLoop 必须 Trace 的环节

```text
任务接收 / 上下文读取 / 节点计划 / 子任务拆分判断 / 资源调用
/ 模型调用 / Tool-MCP 调用 / Execution Session 调用
/ 自验证 / Acceptance / 失败-重试-返工 / 最终路由
```

### 必须 Audit 的环节

```text
Gate 决策 / L4-L5 高风险动作 / Policy 冲突 / 外部系统写操作
/ 远程写操作 / 删除或不可逆操作 / 关键验收裁决 / 模式降级
```

### 脱敏

```text
Trace/Audit 不得包含 Key/Token/Secret/Password 明文；
可记录"密钥文件存在/缺失/需用户提供"，不得输出密钥内容。
```

---

## 9. 与后续专题文档关系

```text
Gate 交互流程、中断/恢复、超时处理 → 04-Gate与中断恢复流程.md
TaskGraph 边策略与 Plan Delta       → 02-StagePlan-TaskPlan-TaskGraph规范.md
P0-P6 阶段完成条件                  → 01-P0-P6阶段契约.md
P5 验证 Evidence                    → 08-测试与验收/02-迁移正确性与验证策略.md
安全风险分级 L0-L5                  → 07-安全与权限/
```

---

## 10. R2/R4/R9-R12 校准项

### R2 文档校准

```text
1. NodeLoop 9 步是否与 TaskGraph 规范（02-StagePlan）一致；
2. 三模式 NodeLoop 行为是否与 Gate 文档（04-Gate）一致；
3. Auto Mode 是否存在绕过用户 Gate 的路径——需逐条压力测试；
4. Acceptance 与 P5 验证边界是否清晰；
5. 是否仍有 Mission/mission_id 残留（D-070）。
```

### R4 工程骨架校准

```text
1. NodeLoop 状态机实现（9 步状态流转）；
2. Acceptance Agent 接口定义；
3. Policy/Hook 在 NodeLoop 各 Step 的集成点；
4. Trace/Audit Writer 接口。
```

### R9-R12 主链路校准

```text
R9  (P0-P1)：最小 NodeLoop（接入+建档场景是否需完整 9 步）；
R10 (P2-P3)：计划生成场景 NodeLoop 验证；
R11 (P4)   ：执行链路 NodeLoop 全流程——9 步是否每步都有真实代码路径；
R12 (P5-P6)：自验证→Acceptance→P5 验证→交付的四层闭环。
```

---

## 11. 规范红线

```text
1. 不得用 NodeLoop 替代 LangGraph 主编排（D-037/D-065）；
2. 不得用 NodeLoop 隐藏 TaskGraph 边策略；
3. 不得用 Auto Mode 绕过阶段晋级用户 Gate；
4. 不得用 Agent 自评替代 Acceptance；
5. 不得用 Acceptance 替代 P5 验证；
6. 不得用模型输出替代 Evidence（D-066）；
7. 不得无 Trace 执行动作；
8. 不得无 Audit 执行高风险动作；
9. 不得无 Gate 执行 L5 高风险动作；
10. 不得默认执行在线社区资源；
11. 不得直接执行 Case；
12. 不得泄露 Key/Token/Secret/Password。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md（NodeLoop 不替代主编排 D-037/D-065、模式不改晋级 D-024/D-025、Policy 优先 D-031、无 Evidence 不得完成 D-066、密钥脱敏 D-032）；术语与风险级别（L0-L5）详见 02-术语表.md。本文红线为 NodeLoop 与执行模式主题特有约束，整体保留。

---

## 12. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 NodeLoop 定位——解决 9 个问题 + 5 个"不是"；
2. 明确 NodeLoop 标准 9 步循环——每步有检查清单和失败处理；
3. 明确 Step 1-9 的逐步规范（接收任务→读取上下文→生成计划→拆分判断→执行→自验证→产物包→Acceptance→流转）；
4. 明确三种执行模式下 NodeLoop 的行为差异（每模式 3-6 条）；
5. 明确三种模式的不变项（5 条）；
6. 明确执行模式切换方向（5 种）和切换规则（6 条）；
7. 明确异常升级规则——按严重度 4 级 + 6 种处置选项；
8. 明确 Acceptance 与自验证的四层边界（自验证→Acceptance→Gate→P5）；
9. 明确 Trace 必须覆盖的环节（12 类）和 Audit 必须覆盖的环节（8 类）；
10. 明确 R2/R4/R9-R12 校准项；
11. 明确规范红线（12 条）；
12. 未固化最终 API 字段或数据库 schema；
13. 执行模式完整定义引用架构层——不复述审核规则和 Gate 策略。
```
