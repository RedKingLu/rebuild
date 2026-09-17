# 03-Project-Run-TaskGraph状态架构

> 文档路径：`文档/02-架构设计/03-Project-Run-TaskGraph状态架构.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/03-Project-Run-TaskGraph状态架构.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：在 `00-架构总纲.md`、`01-系统分层与技术栈基准.md`、`02-LangGraph主编排架构.md` 基础上，定义 Project、Run、Stage、Task Plan、TaskGraph、NodeLoop、Artifact、Evidence、Trace、Audit、Gate 与状态恢复之间的状态边界和状态流转关系。本文是 `02-LangGraph主编排架构.md` 的状态层补充——后者定义"如何流转"，本文定义"流转什么状态"。
> 上级依据：`文档/00-项目治理/01-决策记录.md`、`文档/02-架构设计/00-架构总纲.md`、`文档/02-架构设计/02-LangGraph主编排架构.md`。
> 重要边界：本文是架构层状态设计基准，不替代 API 字段契约、数据库 schema、前端状态管理规范、测试验收规范或 LangGraph 实现代码。本文所有字段均为状态域建议，R2/R4 需要校准为实现契约。
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。
> R10 修订（2026-07-01，Q-R10-5 用户裁决）：§8.3 Node Status 补入 `blocked`（原 11 态遗漏，与 §Step1/§6/§Step9 及 Stage/Run/Project 枚举对齐）。

---

## 0. 编写原则

本文遵守以下原则：

```text
1.  Project 是用户可见主对象（D-070）；
2.  当前版本不设独立 Mission 产品层；
3.  Run 是 Project 内一次执行/推进记录；
4.  P0-P6 是用户项目流程（D-009）；
5.  Task Plan/TaskGraph 表达同一 Project 内部任务拆分、依赖、执行路径和失败处理；
6.  NodeLoop 是 TaskGraph 节点内部执行小循环（D-020）；
7.  Artifact/Evidence/Trace/Audit 是状态可信度基础；
8.  Gate 是用户授权和高风险动作中断点；
9.  状态恢复依赖数据库、LangGraph checkpoint、workspace 文件、trace/audit、event log 多源共同承担（D-053）；
10. 前端 UI、终端、容器、内存不得成为唯一状态源。
```

本文不得：

```text
1.  把 Mission 作为 Project 与 Run 之间的新状态层；
2.  把前端展示状态当作系统事实源；
3.  把 checkpoint 当作完整审计；
4.  把 Artifact 自动晋升为项目文档（D-014）；
5.  把 Evidence 后补成事实；
6.  把执行成功等同于验证通过（D-066）；
7.  把模型输出等同于 accepted 结论；
8.  把状态字段建议写死为 API/数据库最终字段。
```

> 本节"编写原则/本文不得"中的通用红线（不引入 Mission D-070、Artifact 不自动晋升 D-014、Evidence 不得后补 D-066、前端非事实源 D-053、执行成功≠验证通过 D-066 等）完整总表见 AGENTS §18 + 01-决策记录；本文仅就地保留与状态架构职责相关的子集。

---

## 1. 状态架构总览

建议采用以下状态对象关系：

```text
Project
  ├── Workspace
  ├── Environment Profile
  ├── Runs
  │   ├── Stage State Map
  │   ├── Stage Plan
  │   ├── Task Plan / Task Plan Batch
  │   ├── TaskGraph
  │   │   ├── Task Nodes
  │   │   ├── Task Edges
  │   │   └── NodeLoop State
  │   ├── Gate State
  │   ├── Artifact Refs
  │   ├── Evidence Refs
  │   ├── Trace Refs
  │   └── Audit Refs
  └── Delivery Package / Final References
```

状态分层：

```text
Project State：项目级长期状态；
Run State：一次推进或执行的状态；
Stage State：P0-P6 中某阶段的状态；
Plan State：Stage Plan / Task Plan / Task Plan Batch 状态；
TaskGraph State：任务图状态；
NodeLoop State：节点内部小循环状态；
Gate State：用户授权和中断恢复状态；
Evidence State：证据状态；
Trace/Audit State：过程与审计状态。
```

---

## 2. Project State

### 2.1 定位

Project State 是项目级长期状态，用于表达用户可见项目的全局状态。

Project State 不等于某一次 Run，也不等于工作区文件本身。

### 2.2 建议状态域

```text
project_id；
project_name；
project_source_type；
project_source_ref；
project_status；
current_stage；
workspace_ref；
environment_profile_ref；
latest_run_ref；
active_run_refs；
artifact_index_ref；
evidence_index_ref；
trace_index_ref；
audit_index_ref；
created_at；
updated_at。
```

以上为建议域，不是 API/DB 最终字段。

### 2.3 Project Status 建议

```text
created：已创建但未接入；
intake_ready：可进入 P0；
in_progress：存在运行中 Run；
waiting_gate：存在项目级或阶段级 Gate；
paused：用户或策略暂停；
delivered：已完成 P6 交付；
archived：已归档；
failed：项目主线失败或中止；
canceled：用户取消。
```

状态名可在 R2/R4 校准。

### 2.4 Project State 不应包含

```text
完整源码内容；
大段模型上下文；
明文密钥；
一次性临时推断；
未确认旧版本事实；
不可追踪的 Agent 内部记忆。
```

---

## 3. Run State

### 3.1 定位

Run 是 Project 内一次执行、推进、返工、验证或交付整理记录。

Run State 是 LangGraph 主编排和后台任务管理的重要状态承载。

Run 不替代 Project。Run 不应被 Mission 产品层包裹。

### 3.2 建议状态域

```text
run_id；
project_id；
run_goal；
execution_mode；
current_stage；
current_node；
run_status；
stage_status_map；
active_gate_ref；
active_task_graph_ref；
stage_plan_refs；
task_plan_refs；
artifact_refs；
evidence_refs；
trace_refs；
audit_refs；
checkpoint_ref；
workspace_ref；
environment_profile_ref；
execution_session_refs；
blocking_issues；
rework_recommendations；
started_at；
updated_at；
completed_at。
```

说明：

```text
1. run_goal 是建议替代 mission_id 的候选表达之一（D-070）；
2. 如果现有资料中存在 mission_id，R2 必须校准为 run_goal/objective/task_group_id 或删除；
3. Run State 不应引入 Mission 产品层。
```

### 3.3 Run Status 建议

```text
created：Run 已创建；
running：Run 执行中；
waiting_gate：等待 Gate；
paused：暂停；
resuming：恢复中；
retrying：重试中；
rework_required：需要返工；
failed：失败；
completed：完成当前 Run；
canceled：取消；
archived：归档。
```

### 3.4 Run 与后台任务

Run 应支撑项目级后台任务管理。

前端可以展示 Run 状态，但前端展示不是 Run 状态源。

Run 状态至少应可由以下来源恢复：

```text
数据库；
LangGraph checkpoint；
workspace 文件；
trace/audit；
event log。
```

---

## 4. Stage State

### 4.1 定位

Stage State 表达 P0-P6 各阶段在某个 Run 中的状态。

P0-P6 是可裁剪产品流程。被裁剪阶段不得伪装为 completed。

### 4.2 建议阶段集合

```text
P0 接入；
P1 建档；
P2 评估；
P3 规划；
P4 执行；
P5 验证；
P6 交付。
```

### 4.3 Stage Status 建议

```text
not_started：未开始；
skipped：已裁剪/不适用；
ready：可开始；
running：运行中；
waiting_gate：等待 Gate；
paused：暂停；
failed：失败；
rework_required：需要返工；
completed：已完成；
blocked：阻塞。
```

### 4.4 Stage State 建议状态域

```text
stage；
status；
started_at；
updated_at；
completed_at；
stage_plan_ref；
task_plan_refs；
task_graph_ref；
artifact_refs；
evidence_refs；
trace_refs；
audit_refs；
active_gate_ref；
blocking_issues；
completion_criteria；
acceptance_result_ref；
rework_recommendation。
```

### 4.5 Stage 完成条件

Stage completed 必须满足：

```text
1. 阶段输出已生成；
2. 必要 Artifact 已存在；
3. 必要 Evidence 已存在；
4. Trace 已记录；
5. 如涉及高风险或用户授权，Audit 已记录；
6. 阶段完成条件已满足；
7. 进入下一阶段前用户 Gate 已完成（D-023）。
```

P5 completed 还必须满足验证证据要求（D-066）。

P6 completed 还必须满足交付包、交付说明和最终 Gate 要求。

---

## 5. Stage Plan State

### 5.1 定位

Stage Plan 是阶段级计划，用于表达阶段目标、范围、风险、权限、输出、验收方式和 Gate 计划。

### 5.2 建议状态域

```text
stage_plan_id；
project_id；
run_id；
stage；
plan_status；
objective；
scope；
risk_level；
permission_boundary；
expected_artifacts；
expected_evidence；
expected_trace；
expected_audit；
gate_policy；
created_by；
reviewed_by；
approved_by；
created_at；
updated_at。
```

### 5.3 Plan Status 建议

```text
draft：草案；
under_review：待审核；
approved：已批准；
rejected：已拒绝；
superseded：被替代；
executing：执行中；
completed：计划内任务完成；
failed：计划失败。
```

### 5.4 执行模式影响

```text
Manual Mode：用户审核 Stage Plan；
Plan Mode：用户审核 Stage Plan；
Auto Mode：Agent 审核 Stage Plan，但阶段晋级仍必须用户 Gate（D-028）。
```

---

## 6. Task Plan / Task Plan Batch State

### 6.1 定位

Task Plan 是任务级计划。Task Plan Batch 是一组任务计划的批次表达。

它们用于承接 Stage Plan，并为 TaskGraph 提供任务来源。

### 6.2 Task Plan 建议状态域

```text
task_plan_id；
project_id；
run_id；
stage；
stage_plan_ref；
objective；
scope；
inputs；
expected_outputs；
risk_level；
permission_boundary；
required_resources；
model_policy_override；
validation_method；
expected_artifacts；
expected_evidence；
status；
created_at；
updated_at。
```

### 6.3 Task Plan Batch 建议状态域

```text
task_plan_batch_id；
project_id；
run_id；
stage；
stage_plan_ref；
batch_objective；
task_plan_refs；
batch_risk_level；
permission_boundary；
merge_strategy；
gate_policy；
status；
created_at；
updated_at。
```

### 6.4 状态规则

```text
1. Task Plan 不得越过 Stage Plan；
2. Task Plan Batch 暂不设置固定数量上限（D-029）；
3. Task Plan Batch 必须说明批次目标、范围、风险、权限边界、验收方式和异常升级策略；
4. 高风险任务必须触发 Gate；
5. 越界任务必须暂停并登记。
```

---

## 7. TaskGraph State

### 7.1 定位

TaskGraph 表达任务依赖、执行路径、并行关系、失败处理、证据传递和上下文传递。

TaskGraph 是 Project/Run 内部任务复杂度的主要表达方式。

### 7.2 建议状态域

```text
task_graph_id；
project_id；
run_id；
stage；
stage_plan_ref；
task_plan_refs；
nodes；
edges；
graph_status；
active_node_refs；
completed_node_refs；
failed_node_refs；
skipped_node_refs；
artifact_refs；
evidence_refs；
trace_refs；
audit_refs；
created_at；
updated_at。
```

### 7.3 Graph Status 建议

```text
draft：草案；
ready：可执行；
running：运行中；
waiting_gate：等待 Gate；
paused：暂停；
completed：完成；
failed：失败；
rework_required：需要返工；
canceled：取消。
```

### 7.4 TaskGraph 不应做的事

```text
1. 不替代 LangGraph 主编排；
2. 不替代 Stage State；
3. 不隐藏失败路径；
4. 不把节点内部隐式循环藏在不可追踪状态中；
5. 不绕过 Gate；
6. 不让 Artifact/Evidence 断链。
```

---

## 8. Task Node State

### 8.1 定位

Task Node 是 TaskGraph 中的一个任务节点。Task Node 内部运行 NodeLoop。

### 8.2 建议状态域

```text
node_id；
task_graph_id；
project_id；
run_id；
stage；
node_type；
node_status；
input_refs；
context_refs；
resource_refs；
model_policy_override；
tool_policy；
permission_boundary；
risk_level；
artifact_refs；
evidence_refs；
trace_refs；
audit_refs；
active_gate_ref；
retry_count；
failure_reason；
created_at；
updated_at；
completed_at。
```

### 8.3 Node Status 建议

```text
pending：等待执行；
running：执行中；
waiting_gate：等待 Gate；
waiting_resource：等待资源；
self_checking：自验证；
acceptance_checking：验收中；
completed：完成；
failed：失败；
retrying：重试中；
skipped：跳过；
rework_required：需要返工；
blocked：阻塞，等待解除条件（R10 补入，Q-R10-5 用户裁决）。
```

### 8.4 Node 类型建议

```text
context_load：读取上下文；
planning：计划生成；
execution：执行任务；
validation：验证；
artifact_collect：产物收集；
evidence_collect：证据收集；
acceptance：验收；
gate：Gate；
delivery：交付整理。
```

类型名称可在 R2/R4 校准。

---

## 9. Task Edge State

### 9.1 定位

Task Edge 表达节点之间的依赖、触发条件、失败处理和传递策略。

### 9.2 必须显式的边策略（D-022）

TaskGraph 的边必须描述：

```text
edge_type；
trigger_condition；
dependency；
parallel_group；
merge_strategy；
retry_policy；
failure_policy；
gate_policy；
model_policy_override；
context_passing_policy；
artifact_passing_policy；
evidence_passing_policy。
```

### 9.3 Edge Type 建议

```text
sequence：顺序执行；
parallel：并行执行；
conditional：条件执行；
retry：重试路径；
failure：失败路径；
rework：返工路径；
gate：等待授权路径；
merge：合并路径。
```

### 9.4 边状态规则

```text
1. 边策略不得省略失败路径；
2. 并行边必须说明 merge_strategy；
3. 高风险边必须说明 gate_policy；
4. Artifact/Evidence 传递必须可追踪；
5. 条件边必须说明 trigger_condition。
```

---

## 10. NodeLoop State

### 10.1 定位

NodeLoop 是 node 内部标准小循环（D-020）。

NodeLoop 不替代 TaskGraph，也不替代 LangGraph。

### 10.2 标准小循环

```text
接收任务；
读取上下文；
生成计划；
判断是否拆分子任务；
执行；
自验证；
生成节点产物包；
交给 Acceptance Agent 验收；
根据验收结果流转。
```

### 10.3 建议状态域

```text
node_loop_id；
node_id；
loop_status；
current_step；
input_refs；
context_refs；
plan_ref；
subtask_refs；
execution_result_ref；
self_check_result_ref；
node_artifact_package_ref；
acceptance_result_ref；
next_route；
trace_refs；
audit_refs；
created_at；
updated_at。
```

### 10.4 Loop Status 建议

```text
received：已接收任务；
context_loaded：上下文已读取；
planned：计划已生成；
executing：执行中；
self_checking：自验证中；
packaging：产物包生成中；
acceptance_checking：验收中；
completed：完成；
failed：失败；
rework_required：需要返工。
```

---

## 11. Gate State

### 11.1 定位

Gate State 表达用户授权、策略冲突、高风险动作、阶段晋级和交付确认的中断点。

Gate 是 interrupt/resume 的关键状态。

### 11.2 Gate 类型建议（与 `02-LangGraph主编排架构.md` §10.2 一致）

```text
stage_gate：阶段晋级；
plan_gate：计划确认；
action_gate：高风险动作确认；
policy_gate：Policy 冲突确认；
verification_gate：验证结果确认；
delivery_gate：交付确认；
scope_gate：范围变更确认。
```

### 11.3 建议状态域

```text
gate_id；
gate_type；
project_id；
run_id；
stage；
node_id；
reason；
risk_level；
requested_action；
options；
status；
created_at；
resolved_at；
resolved_by；
decision；
decision_reason；
audit_ref；
resume_checkpoint_ref。
```

### 11.4 Gate Status 建议

```text
open：等待处理；
approved：已批准；
rejected：已拒绝；
modified：用户调整后批准；
expired：过期；
canceled：取消；
superseded：被新 Gate 替代。
```

### 11.5 Gate 规则

```text
1. 每个启用 P 阶段晋级必须用户 Gate（D-023）；
2. L5 高风险动作必须用户 Gate（D-034）；
3. Policy 冲突必须 Gate 或停止；
4. Gate 决策必须进入 Audit；
5. Gate 状态必须可恢复；
6. Gate 关闭后必须校验上下文是否仍有效。
```

---

## 12. Artifact State

### 12.1 定位

Artifact 是执行产物。Artifact State 表达产物的来源、状态、归属和可追溯关系。

### 12.2 建议状态域

```text
artifact_id；
project_id；
run_id；
stage；
task_graph_id；
node_id；
artifact_type；
artifact_ref；
source_action_ref；
status；
created_at；
updated_at；
validated_by；
evidence_refs；
trace_refs；
audit_refs。
```

### 12.3 Artifact Status 建议

```text
generated：已生成；
under_review：待审核；
accepted：已接受；
rejected：已拒绝；
superseded：被替代；
delivered：已纳入交付；
archived：已归档。
```

### 12.4 Artifact 规则

```text
1. Artifact 不自动晋升为项目文档（D-014）；
2. Artifact 进入交付必须有验收依据；
3. Artifact 被替代必须保留 superseded 关系；
4. Artifact 必须能关联生成它的 node/task/stage/run。
```

---

## 13. Evidence State

### 13.1 定位

Evidence 是结论、验收、验证和交付可信度的基础（D-066）。

### 13.2 建议状态域

```text
evidence_id；
project_id；
run_id；
stage；
task_graph_id；
node_id；
evidence_type；
evidence_ref；
claim_ref；
source_ref；
status；
created_at；
validated_at；
validated_by；
trace_refs；
audit_refs。
```

### 13.3 Evidence Status 建议

```text
collected：已收集；
validated：已验证；
insufficient：证据不足；
rejected：证据无效；
superseded：被替代；
archived：已归档。
```

### 13.4 Evidence 规则

```text
1. 没有 Evidence，不得标记阶段 completed；
2. 没有 Evidence，不得标记验证通过；
3. Evidence 不得后补成事实；
4. 模型输出不是 Evidence，除非有可验证来源支撑；
5. P5 验证必须有可验证 Evidence（D-066）。
```

---

## 14. Trace / Audit State

### 14.1 Trace 定位

Trace 记录执行过程。Trace 应回答：

```text
做了什么；
谁做的；
何时做的；
基于什么输入；
调用了什么资源；
产生了什么输出；
失败或重试了什么。
```

### 14.2 Audit 定位

Audit 记录关键决策、高风险动作、Gate、Policy 冲突和验收裁决。

### 14.3 Trace 建议状态域

```text
trace_id；
project_id；
run_id；
stage；
node_id；
action_type；
actor_type；
input_refs；
resource_refs；
model_call_refs；
output_refs；
status；
started_at；
ended_at；
error_ref。
```

### 14.4 Audit 建议状态域

```text
audit_id；
project_id；
run_id；
stage；
node_id；
audit_type；
risk_level；
policy_ref；
gate_ref；
decision；
decision_by；
decision_reason；
created_at；
related_trace_refs。
```

### 14.5 Trace / Audit 规则

```text
1. 执行动作必须有 Trace；
2. 高风险动作必须有 Audit；
3. Gate 决策必须有 Audit；
4. Policy 冲突必须有 Audit；
5. Trace/Audit 不得泄露密钥（D-032）；
6. Trace/Audit 是状态恢复和验收可信度的重要来源。
```

---

## 15. 状态恢复架构

### 15.1 多源恢复原则（D-053）

状态恢复依赖：

```text
数据库；
LangGraph checkpoint；
workspace 文件；
trace/audit；
event log。
```

### 15.2 各来源职责

```text
数据库：结构化状态、索引、关系；
LangGraph checkpoint：图执行恢复点；
workspace 文件：源码、材料、Artifact、Evidence 文件；
trace/audit：执行过程与关键决策；
event log：事件流回放和前端状态补偿。
```

### 15.3 不可作为唯一状态源

```text
前端 UI；
终端窗口；
容器运行状态；
内存变量；
一次性日志；
模型上下文。
```

### 15.4 恢复流程建议

```text
1. 从数据库读取 Project/Run 基础状态；
2. 读取 LangGraph checkpoint；
3. 校验 workspace 文件是否存在；
4. 校验 Artifact/Evidence 引用；
5. 校验 Trace/Audit 是否连续；
6. 根据 event log 补偿前端显示；
7. 如果发现冲突，标记为 recovery_conflict 并等待人工确认。
```

该流程是建议，R4 实现时可调整。

---

## 16. 前端状态与后端状态边界

### 16.1 前端可展示

前端可以展示：

```text
Project 状态；
Run 状态；
Stage 状态；
TaskGraph 状态；
Node 状态；
Gate 状态；
Artifact/Evidence/Trace/Audit 索引；
后台任务状态；
错误和阻塞项。
```

### 16.2 前端不可成为事实源

前端不得成为：

```text
Project 状态事实源；
Run 状态事实源；
Gate 决策唯一记录；
执行结果事实源；
验收结论事实源；
Trace/Audit 替代品。
```

事件丢失时，前端必须能通过 API 重新拉取后端状态。

---

## 17. 状态冲突与一致性

### 17.1 常见冲突

```text
数据库显示 running，但 checkpoint 不存在；
checkpoint 显示 waiting_gate，但 Gate 状态已关闭；
Artifact 引用存在，但文件缺失；
Evidence 引用存在，但证据无效；
Trace 显示执行失败，但 Stage 显示 completed；
前端显示 completed，但后端未完成；
P5 验证失败，但 P6 已进入；
mission_id 残留导致 Run 状态归属不清（D-070）。
```

### 17.2 冲突处理

```text
1. 停止自动推进；
2. 标记 recovery_conflict 或 consistency_conflict；
3. 记录 Trace/Audit；
4. 输出冲突报告；
5. 请求用户或 Copilot 确认；
6. 不得自行选择"看起来更合理"的状态继续执行。
```

---

## 18. 状态与 API / 数据库 / 事件的关系

本文只定义状态边界。

具体归口：

```text
API 字段 → 05-API与集成契约；
数据库 schema → R4 工程骨架与 API 实现契约校准；
事件字段 → 05-API与集成契约；
前端状态展示 → 06-UX与前端；
验收状态 → 08-测试与验收。
```

R1 不应把本文建议字段直接当作最终实现字段。

---

## 19. Mission / mission_id 校准要求

当前版本不设独立 Mission 产品层（D-070）。

因此：

```text
1. Project 与 Run 之间不得插入 Mission；
2. TaskGraph 不属于 Mission；
3. Stage Plan/Task Plan 不属于 Mission；
4. API/状态/前端中如出现 mission_id，必须进入 R2 校准；
5. 可选替代方向包括 run_goal、objective、task_group_id 或删除；
6. 不得把 mission_id 简单改名后继续保留同样产品层含义。
```

---

## 20. R2 / R4 / R9-R12 校准项

### 20.1 R2 文档校准

R2 需要校准：

```text
1. 本文是否对齐最新决策记录（含 D-062~D-072）；
2. 文档地图中目录归口是否已同步；
3. mission_id 是否仍残留；
4. 状态域是否过细或与 API 文档冲突；
5. Stage/Run/TaskGraph 状态是否与产品验收基准一致；
6. Trace/Audit/Evidence 是否与测试与验收目录一致。
```

### 20.2 R4 工程骨架校准

R4 需要校准：

```text
1. Project/Run 数据模型；
2. Stage State Map 实现方式；
3. Task Plan/TaskGraph 数据结构；
4. NodeLoop 状态记录方式；
5. Gate 状态持久化；
6. Artifact/Evidence/Trace/Audit 存储和索引；
7. event log 与 SSE 状态同步方式；
8. 状态恢复流程。
```

> R4 校准前应按 D-067 三步法：先立自有方案 → 深读历史版本状态管理相关代码 → 完善方案后执行。

### 20.3 R9-R12 主链路校准

```text
R9：P0-P1 状态闭环；
R10：P2-P3 评估与规划状态闭环；
R11：P4 执行状态闭环；
R12：P5-P6 验证与交付状态闭环。
```

每个阶段必须验证：

```text
Project State；
Run State；
Stage State；
TaskGraph State；
Gate State；
Artifact State；
Evidence State；
Trace/Audit State；
恢复路径。
```

---

## 21. 架构红线

```text
1.  不得引入 Mission 产品层（D-070）；
2.  不得将 mission_id 作为正式产品主字段直接保留；
3.  不得用前端状态替代后端状态（D-053）；
4.  不得用 checkpoint 替代数据库、Evidence、Trace、Audit；
5.  不得无 Evidence 标记 Stage completed（D-066）；
6.  不得无 Artifact 标记交付 completed；
7.  不得无 Trace/Audit 标记执行可信；
8.  不得把 P5 验证失败状态推进到 P6 completed；
9.  不得让 TaskGraph 隐藏失败或返工路径；
10. 不得让 NodeLoop 演变成不可追踪的自研主编排（D-065）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md（已在各条就地标注 D 编号）；术语与风险级别（L0-L5）详见 02-术语表.md。本文红线为状态架构主题特有约束，整体保留。

---

## 22. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1.  明确 Project/Run/Stage/Task Plan/TaskGraph/NodeLoop 状态边界；
2.  明确 Project State、Run State、Stage State 建议（§2~§4）；
3.  明确 Stage Plan、Task Plan、Task Plan Batch 状态建议（§5~§6）；
4.  明确 TaskGraph、Task Node、Task Edge 状态建议（§7~§9）；
5.  明确 NodeLoop State（§10）；
6.  明确 Gate State（§11）；
7.  明确 Artifact/Evidence/Trace/Audit State（§12~§14）；
8.  明确状态恢复多源原则（§15）；
9.  明确前端状态与后端状态边界（§16）；
10. 明确状态冲突处理（§17）；
11. 明确 API/数据库/事件归口（§18）；
12. 明确 Mission/mission_id 校准要求（§19）；
13. 明确 R2/R4/R9-R12 校准项（§20）；
14. 明确架构红线 10 条（§21）；
15. 未新增产品决策；
16. 未固定最终实现 schema；
17. 未引入 Mission 产品层。
```
