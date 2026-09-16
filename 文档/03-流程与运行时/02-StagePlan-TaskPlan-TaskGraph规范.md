# 02-StagePlan-TaskPlan-TaskGraph规范

> 文档路径：`文档/03-流程与运行时/02-StagePlan-TaskPlan-TaskGraph规范.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/已完成/R1/02-StagePlan-TaskPlan-TaskGraph规范.md`（v0.1，~1059 行；去重 ~55%，主要移除 ~150 个字段建议 + 执行模式/Gate 复述）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`03-流程与运行时/` 专题的第 2 份子文档。定义 Stage Plan / Task Plan / Task Plan Batch / Plan Delta / TaskGraph / Task Node / Task Edge 的操作规范——对象层次、核心规则、边策略细化、版本替代、冲突处理。**状态字段的权威源见 `02-架构设计/03-Project-Run-TaskGraph状态架构.md`**，本文只定义操作规则不重复字段定义。
> 上级依据：`文档/03-流程与运行时/00-流程与运行时总览.md`、`文档/03-流程与运行时/01-P0-P6阶段契约.md`、`文档/02-架构设计/03-Project-Run-TaskGraph状态架构.md`。
> 重要边界：本文是计划与任务图的**操作规范**（对象层次/核心规则/边策略/版本替代/冲突处理），不重复架构层的状态字段定义、不固化 API schema、不替代 LangGraph 主编排。
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

```text
权威源引用（本文只引用，不重新定义）：
- Stage Plan / Task Plan / TaskGraph / Node / Edge 状态字段 → 02-架构设计/03-Project-Run-TaskGraph状态架构.md
- P0-P6 阶段契约（Stage Plan 必须对齐的阶段目标）        → 01-P0-P6阶段契约.md
- LangGraph 主编排、Gate 机制                             → 02-架构设计/02-LangGraph主编排架构.md
- Manual/Plan/Auto 执行模式                               → 02-架构设计/02-LangGraph主编排架构.md §11
- Artifact/Evidence/Trace/Audit 定义                       → 02-架构设计/03-Project-Run-TaskGraph状态架构.md §12-§14
- NodeLoop 标准循环                                       → 03-NodeLoop与执行模式.md
```

本文必须遵守：

```text
1. Project 是用户可见主对象，不设 Mission 产品层（D-070）；
2. Stage Plan / Task Plan / TaskGraph 是 Project 内部推进结构，不是新的产品层；
3. TaskGraph 不替代 LangGraph 主编排（D-037/D-065）；
4. TaskGraph 边策略必须显式——不得隐藏在提示词中；
5. Plan Mode 采用多层计划，不是一次大计划；
6. Task Plan Batch 不设固定数量上限，但必须说明边界/风险/验收方式/异常升级策略；
7. Artifact/Evidence/Trace/Audit 贯穿计划→执行→验收→交付（D-066）；
8. 高风险动作必须 Gate。
```

本文不得：

```text
1. 把 Stage Plan 当成产品阶段；
2. 把 Task Plan / TaskGraph 产品化为 Mission；
3. 用 TaskGraph 隐藏失败路径；
4. 用计划代替执行或验证；
5. 允许高风险任务绕过 Gate；
6. 让 Task Plan Batch 无边界膨胀；
7. 固化最终 API 字段或数据库 schema。
```

> 本节"必须遵守/本文不得"中的通用红线（不引入 Mission D-070、TaskGraph 不替代 LangGraph 主编排 D-037/D-065、高风险动作必须 Gate、Artifact/Evidence 贯穿 D-066 等）完整总表见 AGENTS §18 + 01-决策记录；本文仅就地保留与计划与任务图职责相关的子集。

---

## 1. 对象关系总览

```text
Project
  └── Run
      └── Stage (P0-P6)
          ├── Stage Plan        ← 阶段级：目标/范围/风险/权限/Gate策略
          │   ├── Task Plan     ← 任务级：单个任务的输入/输出/验证方法
          │   ├── Task Plan     ← （一个 Stage 可有多个 Task Plan）
          │   └── Task Plan Batch ← 批次：一组 Task Plan 的合并审核单元
          └── TaskGraph         ← 依赖图：节点+边+流转策略
              ├── Task Node     ← 节点内运行 NodeLoop
              ├── Task Edge     ← 边定义依赖/并行/失败/重试/合并
              └── Plan Delta    ← 变更记录：计划偏离已批准版本的差异
```

四层职责：

```text
Stage Plan：    回答一个阶段要做什么、范围是什么、风险是什么、如何验收；
Task Plan：     回答一个具体任务的输入/输出/权限/验证方法；
Task Plan Batch：回答一组任务为何可合并审核、批量风险是什么；
TaskGraph：     回答任务间如何依赖/并行/合并/失败/重试/返工/传递上下文与产物。
```

---

## 2. Stage Plan 核心规则

> 状态字段（plan_status、objective、scope、risk_level 等）的完整定义见 `02-架构设计/03-Project-Run-TaskGraph状态架构.md`。以下只定义操作规则。

### 2.1 定位

Stage Plan 是阶段级计划，约束某个 P 阶段内部的目标、范围、执行策略、风险、权限、产物、证据、Gate 和完成条件。

Stage Plan 不得替代：P 阶段本身、Task Plan、TaskGraph、Gate 决策、Evidence、验收结论。

### 2.2 操作规则

```text
1. Stage Plan 必须对齐对应 P 阶段的契约目标（见 01-P0-P6阶段契约.md）；
2. Stage Plan 必须声明 scope 和 out_of_scope——明确不做什么；
3. Stage Plan 必须声明 expected_artifacts 和 expected_evidence——让验收有据可查；
4. Stage Plan 必须声明 risk_level 和 permission_boundary——约束执行边界；
5. Stage Plan 必须声明 gate_policy——哪些情况触发 Gate；
6. Stage Plan 必须声明 completion_criteria——怎样算计划覆盖完成；
7. Stage Plan 的 completed ≠ Stage completed——后者还需 Artifact/Evidence/Gate；
8. Stage Plan 被新版本替代时，原版本保留 superseded 关系，不得删除事实链。
```

### 2.3 状态流转

```text
draft → under_review → approved → executing → completed
                                  ↘ failed → (返工后重新 draft 或 superseded)
                   ↘ rejected → (重做或取消)
```

---

## 3. Task Plan 核心规则

> 状态字段见 `02-架构设计/03-Project-Run-TaskGraph状态架构.md`。

### 3.1 定位

Task Plan 是任务级计划，承接 Stage Plan，为 TaskGraph 提供任务来源。每个 Task Plan 应明确：目标、输入、输出、执行方式、风险级别、权限边界、模型策略、资源调用、验证方法、Artifact/Evidence 期望、失败处理。

### 3.2 操作规则

```text
1. Task Plan 必须可追溯到 Stage Plan（task_plan → stage_plan_ref）；
2. Task Plan 不得扩大 Stage Plan 的 scope；
3. Task Plan 必须说明输入（input_refs）和预期输出（expected_outputs）；
4. Task Plan 必须说明风险级别（risk_level）和权限边界（permission_boundary）；
5. 高风险 Task Plan 必须配置 gate_policy；
6. 需要模型时说明模型策略（model_policy_override），否则用默认解析规则；
7. 需要 Tool/MCP/外部 Agent 时记录资源来源、风险和权限；
8. Task Plan completed 必须有 Artifact/Evidence 或明确无需产物的理由；
9. Task Plan failed 必须有失败原因和 Trace；
10. Task Plan 被替代时保留 superseded 关系。
```

### 3.3 状态流转

```text
draft → under_review → approved → ready → running → completed
                              ↘ rejected     ↘ waiting_gate → running
                                             ↘ failed → rework_required → (重新 draft)
                              ↘ skipped / canceled
```

---

## 4. Task Plan Batch 核心规则

> 状态字段见 `02-架构设计/03-Project-Run-TaskGraph状态架构.md`。

### 4.1 定位

Task Plan Batch 是一组 Task Plan 的批次表达——用于减少重复审核和组织批量执行，但不得牺牲清晰性、权限边界和验收能力。

### 4.2 操作规则

```text
1. 当前不设置固定数量上限；
2. 但必须说明：batch_objective（批次目标）、batch_scope（任务范围）、batch_risk_level（风险级别）、permission_boundary（权限边界）、validation_strategy（验收方式）、exception_policy（异常升级策略）；
3. 批次内任务风险不一致时，按最高风险处理或拆分批次；
4. 批次内出现高风险任务时，必须配置 Gate；
5. 批次内任一任务失败不得隐藏——必须体现在批次状态中；
6. partially_completed ≠ completed；
7. 批次完成必须能追踪每个 Task Plan 的独立状态；
8. 批次不得成为绕过用户审核的手段。
```

### 4.3 状态流转

```text
draft → under_review → approved → ready → running → completed
                                          ↘ waiting_gate → running
                                          ↘ partially_completed → (继续或返工)
                                          ↘ failed → rework_required
```

---

## 5. Plan Delta 规范

### 5.1 定位

Plan Delta 是计划执行过程中的**变更说明**——记录 Stage Plan / Task Plan / Task Plan Batch / TaskGraph 与已批准版本之间的差异。这是其他文档未覆盖的独特概念。

### 5.2 触发条件

以下情况必须生成 Plan Delta：

```text
1. 范围变化（scope/out_of_scope 调整）；
2. 风险级别变化（升/降级）；
3. 权限边界变化；
4. 增加或删除任务；
5. 修改 TaskGraph 边策略；
6. 改变模型策略或资源调用方式；
7. 改变验证方法或验收标准；
8. 出现阻塞项导致计划调整；
9. 用户要求变更。
```

### 5.3 核心字段

```text
plan_delta_id、source_plan_ref、target_plan_ref、delta_type、change_summary、
changed_fields、reason、risk_impact、permission_impact、artifact_impact、
evidence_impact、gate_required、audit_ref
```

> 完整字段定义见 `02-架构设计/03-Project-Run-TaskGraph状态架构.md`。

### 5.4 操作规则

```text
1. 计划变更必须可追踪——每次变更生成 Plan Delta；
2. 高风险或越界变更必须 Gate；
3. Plan Delta 不得静默覆盖原计划——原计划保留 superseded 关系；
4. Plan Delta 必须说明对 Artifact/Evidence/Gate 的影响；
5. 替代后运行中的节点需判断：继续/暂停/重跑。
```

---

## 6. TaskGraph 核心规则

> 状态字段（graph_status、nodes、edges 等）见 `02-架构设计/03-Project-Run-TaskGraph状态架构.md`。

### 6.1 定位

TaskGraph 表达任务依赖、执行路径、并行关系、失败处理、证据传递和上下文传递。**TaskGraph 不替代 LangGraph 主编排**——它是主编排内部的任务级依赖图。

### 6.2 操作规则

```text
1. TaskGraph 必须来源于 Stage Plan / Task Plan；
2. TaskGraph 不得突破 Stage Plan 的 scope；
3. TaskGraph 边策略必须显式——不得隐藏在提示词中；
4. 并行执行必须有显式 merge_strategy（见 §8.3）；
5. 失败/重试/返工必须有显式路径（见 §8.4-§8.5）；
6. Artifact/Evidence 传递必须可追踪（见 §9）；
7. TaskGraph completed ≠ Stage completed——后者还需 Artifact/Evidence/Gate；
8. TaskGraph 不得隐藏失败路径——任何失败边必须指向明确的处理节点或 Gate。
```

---

## 7. Task Node 核心规则

> 状态字段（node_type、node_status 等）见 `02-架构设计/03-Project-Run-TaskGraph状态架构.md`。

### 7.1 Node 类型

```text
context_load     — 读取上下文
planning         — 计划生成
execution        — 执行任务
validation       — 验证
artifact_collect — 产物收集
evidence_collect — 证据收集
acceptance       — 验收
gate             — Gate 等待
delivery         — 交付整理
```

### 7.2 操作规则

```text
1. 每个 Node 内部运行 NodeLoop（详见 03-NodeLoop与执行模式.md）；
2. Node 必须说明输入（input_refs）和输出（output_refs）；
3. Node 必须说明是否产生 Artifact/Evidence；
4. Node 执行必须有 Trace；
5. 高风险 Node 必须配置 Gate 和 Audit；
6. Node completed ≠ TaskGraph completed；
7. Node failed 必须走显式 failure_policy——不得静默跳过；
8. Node 内部不得形成不可追踪的隐式流程。
```

### 7.3 Node 状态流转

```text
pending → running → self_checking → acceptance_checking → completed
                  ↘ waiting_gate → running
                  ↘ waiting_resource → running
                  ↘ failed → retrying → running
                  ↘ failed → rework_required
                  ↘ skipped / canceled
```

---

## 8. Task Edge 与边策略规范

> 这是本文的**核心价值**——边策略是 TaskGraph 可执行的前提。架构层定义了状态字段，本文定义每种边的策略语义。

### 8.1 Edge Type

```text
sequence    — 顺序执行（默认边类型）
parallel    — 并行执行（需配合 parallel_group + merge_strategy）
conditional — 条件执行（必须说明 trigger_condition）
retry       — 重试路径（必须说明 retry_policy）
failure     — 失败路径（必须说明 failure_policy）
rework      — 返工路径（必须说明返工目标）
gate        — 等待授权路径（必须说明 gate_policy）
merge       — 合并路径（必须说明 merge_strategy）
skip        — 跳过路径（必须说明跳过条件）
cancel      — 取消路径（必须说明取消影响范围）
```

### 8.2 边必填策略维度

每条边必须声明以下策略（不得隐藏在提示词中）：

```text
1. edge_type          — 边类型（见 §8.1）
2. trigger_condition  — 触发条件（见 §8.3）
3. dependency         — 依赖关系类型（见 §8.4）
4. merge_strategy     — 并行合并策略（parallel 边必须，见 §8.5）
5. retry_policy       — 重试策略（retry 边必须，见 §8.6）
6. failure_policy     — 失败处理策略（failure 边必须，见 §8.7）
7. gate_policy        — Gate 触发策略（gate 边/高风险边必须，见 §8.8）
8. passing_policy     — 上下文/Artifact/Evidence 传递策略（见 §8.9）
```

### 8.3 trigger_condition（触发条件）

```text
on_success              — 上游成功时触发
on_failure              — 上游失败时触发
on_gate_approved        — Gate 批准后触发
on_gate_rejected        — Gate 拒绝后触发
on_evidence_sufficient  — Evidence 充分时触发
on_evidence_insufficient— Evidence 不足时触发
on_policy_blocked       — Policy 阻止时触发
on_retry_available      — 重试可用时触发
on_manual_decision      — 用户手动决策后触发
on_scope_changed        — 范围变更后触发
```

### 8.4 dependency（依赖关系）

```text
hard_dependency     — 必须完成才能继续
soft_dependency     — 建议完成，可带警告继续
optional_dependency — 可选依赖
evidence_dependency — 依赖 Evidence 就绪
artifact_dependency — 依赖 Artifact 就绪
gate_dependency     — 依赖 Gate 决策
```

### 8.5 merge_strategy（合并策略）

```text
all_success    — 全部并行节点成功才合并
any_success    — 任一成功即可合并
manual_merge   — 用户或 Agent 审核后合并
evidence_merge — 基于 Evidence 完整性合并
artifact_merge — 基于 Artifact 完整性合并
conflict_review— 冲突时进入评审再合并
```

### 8.6 retry_policy（重试策略）

```text
max_retries           — 最大重试次数
retry_on              — 哪些条件触发重试（如 timeout/transient_error）
retry_delay           — 重试间隔
requires_gate         — 重试是否需要 Gate
retry_scope           — 重试范围（单节点/子树/全图）
retry_evidence_required— 重试后是否需要新 Evidence
```

### 8.7 failure_policy（失败处理）

```text
fail_fast              — 立即失败，停止相关路径
continue_with_warning  — 带警告继续（仅限低风险非关键路径）
route_to_rework        — 进入返工路径
route_to_gate          — 进入 Gate 等待用户决策
route_to_manual_review — 进入人工评审
mark_blocked           — 标记阻塞，等待解除条件
```

### 8.8 gate_policy（Gate 触发策略）

```text
gate_required   — 是否必须 Gate
gate_type       — Gate 类型（见 02-LangGraph主编排架构.md §10）
risk_level      — 风险级别（L0-L5）
reason          — 触发原因
options         — 提供给用户的选项
resume_target   — Gate 通过后恢复到哪个节点
audit_required  — 是否必须 Audit
```

### 8.9 passing_policy（传递策略）

上下文、Artifact、Evidence 在边上的传递必须声明：

```text
传递什么（what）；
从哪里传递（from_node）；
传递到哪里（to_node）；
是否裁剪（trim）；
是否摘要（summarize）；
是否需要 Evidence（evidence_required）；
是否需要脱敏（sanitize）；
是否进入 Trace（trace）。
```

---

## 9. 版本与替代关系

```text
1. Stage Plan 可被新版本替代；
2. Task Plan 可被新版本替代；
3. Task Plan Batch 可被新版本替代；
4. TaskGraph 可被新版本替代；
5. 被替代对象不得删除事实链——supersedes/superseded_by 必须保留；
6. 替代原因必须进入 Plan Delta（见 §5）；
7. 高风险替代必须 Gate；
8. 替代后运行中的节点需判断：继续当前版本 / 暂停等待新版 / 重跑。
```

---

## 10. 一致性与冲突处理

### 10.1 常见冲突场景

```text
1. Stage Plan scope 与 Task Plan scope 不一致；
2. Task Plan 风险被低估（实际执行风险 > 声明风险）；
3. TaskGraph 边策略缺失或与声明不一致；
4. TaskGraph 已执行但上游 Plan 被替代（版本漂移）；
5. Artifact 引用存在但文件缺失；
6. Evidence insufficient 但节点标记 completed；
7. Gate 未关闭但 TaskGraph 继续执行；
8. Trace/Audit 断链。
```

### 10.2 冲突处理规则

```text
1. 停止自动推进；
2. 标记 consistency_conflict；
3. 记录 Trace/Audit；
4. 输出冲突说明（含涉及的 plan/node/edge/artifact 引用）；
5. 请求用户或 Copilot 确认；
6. 不得自行选择"看起来合理"的状态继续执行；
7. 必要时生成 Plan Delta 或返回上级计划重审。
```

---

## 11. 与执行模式的关系

执行模式（Manual/Plan/Auto）影响计划与任务图的**审核权**和**Gate 密度**。完整定义见 `02-架构设计/02-LangGraph主编排架构.md` §11。速查：

| 事项 | Manual | Plan | Auto |
|---|---|---|---|
| Stage Plan 审核 | 用户 | 用户 | Agent |
| Task Plan 审核 | 用户 | 用户（或 Batch） | Agent |
| 计划内动作放行 | 用户逐项 | Hook+Policy | Hook+Policy+Auto Review |
| 越界/风险升级 | 用户 | 用户 | 用户 |
| 阶段晋级 Gate | 用户 | 用户 | 用户 |

不受执行模式影响：

```text
1. TaskGraph 边策略必须显式；
2. 高风险任务必须 Gate；
3. 无 Evidence 不得标记 completed；
4. 失败/返工路径不得隐藏。
```

---

## 12. 与后续专题文档关系

```text
NodeLoop 标准步骤与子任务拆分      → 03-NodeLoop与执行模式.md
Gate 交互流程、中断/恢复、超时处理  → 04-Gate与中断恢复流程.md
P5 验证 Evidence 传递               → 08-测试与验收/02-迁移正确性与验证策略.md
API 字段契约                        → 05-API与集成契约/
```

---

## 13. R2/R4/R9-R12 校准项

### R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. 是否仍有 Mission/mission_id 残留（D-070）；
3. 边策略维度是否完整——是否遗漏关键 edge type 或策略字段；
4. Plan Delta 概念是否需与变更控制规范（04-变更控制规范.md）对齐；
5. Gate 引用是否与 04-Gate与中断恢复流程 一致。
```

### R4 工程骨架校准

```text
1. Stage Plan / Task Plan / Task Plan Batch / TaskGraph / Node / Edge / Plan Delta 数据结构落地；
2. API 与 SSE 契约——计划的 CRUD、TaskGraph 的状态推送；
3. 边策略在 LangGraph 中的实际表达方式；
4. Artifact/Evidence/Trace/Audit 存储与索引。
```

### R9-R12 主链路校准

```text
R9  (P0-P1)：是否需最小 TaskGraph（接入→建档线性流）；
R10 (P2-P3)：Stage Plan / Task Plan / TaskGraph 是否可真实生成；
R11 (P4)   ：TaskGraph 是否可驱动真实执行——边策略是否齐全；
R12 (P5-P6)：Evidence 传递是否支撑验证与交付——passing_policy 是否生效。
```

---

## 14. 规范红线

```text
1. 不得引入 Mission 产品层（D-070）；
2. 不得用 TaskGraph 替代 LangGraph 主编排（D-037/D-065）；
3. 不得把 Task Plan / TaskGraph 产品化为用户主阶段；
4. 不得让 Task Plan Batch 无边界膨胀；
5. 不得省略 TaskGraph 边策略——所有边必须显式；
6. 不得隐藏失败/重试/返工路径；
7. 不得无 Gate 执行高风险任务；
8. 不得无 Evidence 标记任务或阶段完成（D-066）；
9. 不得无 Trace 执行动作；
10. 不得无 Audit 执行高风险动作；
11. 不得把模型输出作为唯一验收依据；
12. 不得泄露 Key/Token/Secret/Password。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md（Mission 边界 D-070、TaskGraph 不替代主编排 D-037/D-065、Artifact 不自动晋升 D-014、无 Evidence 不得完成 D-066、密钥脱敏 D-032）；术语与风险级别（L0-L5）详见 02-术语表.md。本文红线为计划与任务图主题特有约束，整体保留。

---

## 15. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 Stage Plan / Task Plan / Task Plan Batch / TaskGraph / Task Node / Task Edge 的对象层次与职责；
2. 明确 Stage Plan 核心操作规则（8 条）；
3. 明确 Task Plan 核心操作规则（10 条）；
4. 明确 Task Plan Batch 核心操作规则（8 条）；
5. 明确 Plan Delta 触发条件（9 种）、核心字段和操作规则（5 条）；
6. 明确 TaskGraph 核心操作规则（8 条）；
7. 明确 Task Node 类型（9 种）、操作规则（8 条）和状态流转；
8. 明确 Task Edge 10 种类型 + 8 个必填策略维度；
9. 明确边策略 7 维细化（trigger_condition / dependency / merge_strategy / retry_policy / failure_policy / gate_policy / passing_policy）；
10. 明确版本与替代规则（9 条）；
11. 明确一致性与冲突处理（8 场景 + 7 条处理规则）；
12. 明确与执行模式的关系（引用架构层，不复述）；
13. 明确 R2/R4/R9-R12 校准项；
14. 明确规范红线（12 条）；
15. 未固化最终 API 字段或数据库 schema；
16. 架构层已有定义（状态字段/Gate 类型/执行模式）只引用不复述。
```
