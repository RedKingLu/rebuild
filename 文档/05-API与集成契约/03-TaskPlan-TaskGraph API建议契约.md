# 03-TaskPlan-TaskGraph API建议契约

> 文档路径：`文档/05-API与集成契约/03-TaskPlan-TaskGraph API建议契约.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.1.1
> 来源草稿：`产物/草稿/03-TaskPlan-TaskGraph API建议契约.md`（v0.1）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R1 文档正式化流程
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本中 Stage Plan、Task Plan、Task Plan Batch、TaskGraph、Node、Edge、SubTask、NodeLoop、Acceptance 相关 API 的 R1 建议契约，包括对象边界、端点归口、字段建议、状态建议、边策略、上下文/Artifact/Evidence 传递、Gate/Policy/Trace/Audit 关联、错误响应、事件建议、前端联调边界与 R2/R4/R10-R12 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-016, D-037, D-068）、`文档/05-API与集成契约/00-API与集成契约总览.md`（§11）、`文档/05-API与集成契约/01-字段规范与错误响应规范.md`（字段/错误码详述源）、`文档/05-API与集成契约/02-Project-Run-阶段API建议契约.md`（上游对象）、`文档/03-流程与运行时/02-StagePlan-TaskPlan-TaskGraph规范.md`。
> 重要边界：本文是 TaskPlan / TaskGraph API 的 R1 建议契约，不替代项目治理决策、最终 OpenAPI 文档、后端路由实现、数据库 schema、LangGraph 实现、前端页面规范、安全权限规范或运行时代码实现。本文所有端点、字段、状态和错误码均为 R1 建议契约，R2/R4/R10-R12 需按真实实现校准为实现契约。
> 修订说明：R1 正式化版基于草稿 v0.1：(1) 头部标准化；(2) §0 新增关联决策速查表 + 字段规范引用声明；(3) 关键节加注决策引用；(4) 新增 §28 待确认项（4 项）；(5) 验收标准扩展（20→22 项）。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 TaskPlan / TaskGraph API 建议契约，不重新定义上级事实。

**关联决策速查**：

| 决策编号 | 内容 | 本文相关节 |
|---|---|---|
| D-016 | API 字段 R1 输出建议契约，R2 校准为实现契约 | 全文 |
| D-037 | LangGraph 是主编排底座——TaskGraph 不替代 LangGraph | §12, §26 |
| D-068 | 文档事实源层级与引用规则（单一详述源） | §0, §21 |

**字段与错误码规范引用**：本文所有字段命名、元数据标注、状态字段、时间字段、引用字段、请求响应结构和错误码均遵守 `01-字段规范与错误响应规范.md`。上游对象（Stage/Stage Plan 关联）遵守 `02-Project-Run-阶段API建议契约.md`。

本文必须遵守：

```text
1. API 字段 R1 输出建议契约，R2 校准为实现契约（D-016）；
2. Plan Mode 采用 Stage Plan、Task Plan / To-do Plan、Plan Delta，而不是一次大计划；
3. Task Plan Batch 当前暂不设置固定数量限制；
4. Task Plan Batch 必须说明批次目标、任务范围、风险级别、权限边界、验收方式和异常升级策略；
5. 每个 node 内部必须有 NodeLoop；
6. NodeLoop 必须包含接收任务、读取上下文、生成计划、判断是否拆分子任务、执行、自验证、生成节点产物包、交给 Acceptance Agent 验收、根据验收结果流转；
7. 子任务支持 inline、serial、parallel、hybrid、nested_loop；
8. TaskGraph 边策略必须显式化；
9. TaskGraph 边必须描述 edge_type、trigger_condition、dependency、parallel_group、merge_strategy、retry_policy、failure_policy、gate_policy、model_policy_override、context_passing_policy、artifact_passing_policy、evidence_passing_policy；
10. Manual / Plan / Auto 不改变 P0-P6 产品流程，不改变 P 阶段晋级必须用户授权（D-025/D-023）；
11. Policy 优先级高于 Agent 判断（D-031）；
12. 高风险动作必须 Gate / Audit；
13. 当前版本不设独立 Mission 产品层（D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 将 TaskGraph API 设计成替代 LangGraph 主编排的自研状态机（D-037）；
2. 将 Task Plan 审核等同于 P 阶段晋级授权；
3. 将 Task Plan Batch 作为一次性大计划绕过分层计划；
4. 允许 Node 绕过 NodeLoop 隐式执行；
5. 允许 Edge 隐藏 Gate / Policy / failure 策略；
6. 允许 Artifact / Evidence 传递复制全文形成第二事实源（D-068）；
7. 将 Evidence 缺失伪装为 completed；
8. 通过 API 绕过 Policy / Gate / Audit；
9. 引入 Mission 产品层或 mission_id 长期字段（D-070）；
10. 在 API / 事件 / Trace / Audit 中泄露 Key / Token / Secret / Password（D-032）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 对象边界

本文覆盖以下对象：

```text
Stage Plan：阶段级计划；
Task Plan：任务级计划；
Task Plan Batch：任务计划批次；
Plan Delta：计划变更；
TaskGraph：任务图；
Task Node：任务节点；
Task Edge：任务边；
SubTask：节点内部子任务；
NodeLoop：节点内部标准小循环；
Acceptance Result：节点验收结果。
```

对象关系建议：

```text
Run 1 ── n Stage Plan
Stage Plan 1 ── n Task Plan
Task Plan Batch 1 ── n Task Plan
TaskGraph 1 ── n Task Node
TaskGraph 1 ── n Task Edge
Task Node 1 ── n SubTask
Task Node 1 ── 1 NodeLoop
Task Node 1 ── n Artifact / Evidence / Trace / Audit refs
```

边界规则：

```text
1. Stage Plan 不等于 Task Plan；
2. Task Plan 不等于 TaskGraph；
3. TaskGraph 不等于 LangGraph 本体（D-037）；
4. NodeLoop 是节点内部执行结构，不是独立产品阶段；
5. Task Plan / TaskGraph 不得引入 Mission 产品层。
```

---

## 2. API 路由归口建议

R1 建议路由按能力归口：

```text
/api/projects/{project_id}/runs/{run_id}/stage-plans
/api/projects/{project_id}/runs/{run_id}/stage-plans/{stage_plan_id}
/api/projects/{project_id}/runs/{run_id}/task-plans
/api/projects/{project_id}/runs/{run_id}/task-plans/{task_plan_id}
/api/projects/{project_id}/runs/{run_id}/task-plan-batches
/api/projects/{project_id}/runs/{run_id}/task-plan-batches/{task_plan_batch_id}
/api/projects/{project_id}/runs/{run_id}/plan-deltas
/api/projects/{project_id}/runs/{run_id}/task-graphs
/api/projects/{project_id}/runs/{run_id}/task-graphs/{task_graph_id}
/api/projects/{project_id}/runs/{run_id}/task-graphs/{task_graph_id}/nodes
/api/projects/{project_id}/runs/{run_id}/task-graphs/{task_graph_id}/nodes/{node_id}
/api/projects/{project_id}/runs/{run_id}/task-graphs/{task_graph_id}/edges
/api/projects/{project_id}/runs/{run_id}/task-graphs/{task_graph_id}/edges/{edge_id}
/api/projects/{project_id}/runs/{run_id}/task-graphs/{task_graph_id}/nodes/{node_id}/acceptance
```

说明：

```text
1. 以上为 R1 建议路由；
2. R4 需根据 FastAPI 实际路由结构校准；
3. 路由不应包含 V26.1；
4. 路由不应包含 Mission 产品层；
5. 如实际采用扁平路由，也必须保持对象语义一致。
```

---

## 3. Stage Plan API 总览

Stage Plan API 表达阶段目标、范围、产物、证据、权限边界、风险和计划审核状态。

建议能力：

```text
创建 Stage Plan；
查询 Stage Plan；
更新 Stage Plan；
提交 Stage Plan 审核；
审核 Stage Plan；
列出 Stage Plan；
作废 Stage Plan；
生成 Plan Delta。
```

Stage Plan API 不负责：

```text
1. 替代 P 阶段晋级 Gate；
2. 直接执行任务节点；
3. 直接验证 Evidence；
4. 绕过 Task Plan / TaskGraph；
5. 保存密钥明文。
```

---

## 4. Stage Plan 创建建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/stage-plans
```

请求字段建议：

```text
stage：string，必填，P0-P6 阶段标识；
stage_objective：string，必填，阶段目标；
scope：object，必填或可选待 R2 校准，阶段范围；
out_of_scope：object，可选，范围外事项；
execution_mode：string，可选，manual / plan / auto；
required_artifacts：array，可选，阶段产物要求；
required_evidence：array，可选，阶段证据要求；
risk_level：string，可选，风险级别；
permission_boundary：object，可选，权限边界；
acceptance_criteria：array，可选，验收标准；
context_refs：array，可选，上下文引用；
trace_reason：string，可选，创建原因。
```

响应字段建议：

```text
request_id；
status；
data.stage_plan_id；
data.project_id；
data.run_id；
data.stage；
data.stage_plan_status；
data.version；
data.review_required；
data.gate_ref；
trace_ref；
warnings；
next_actions。
```

规则：

```text
1. 创建 Stage Plan 不等于阶段完成；
2. 创建 Stage Plan 不等于阶段晋级授权；
3. 高风险计划应触发 Gate 或审核；
4. Stage Plan 必须保留 Trace；
5. Stage Plan 不得隐含范围外任务。
```

---

## 5. Stage Plan 状态建议

Stage Plan 状态建议（遵守 01-字段规范 §5）：

```text
draft：草案；
submitted：已提交；
under_review：审核中；
approved：已批准；
rejected：已拒绝；
needs_revision：需修订；
superseded：已被替代；
canceled：已取消；
archived：已归档。
```

规则：

```text
1. approved 不等于阶段晋级通过；
2. superseded 必须有关联 superseded_by；
3. rejected 必须有 reject_reason；
4. needs_revision 必须有 required_changes；
5. 状态变化必须 Trace；
6. 高风险计划审核应 Audit。
```

---

## 6. Task Plan API 总览

Task Plan API 表达具体任务目标、输入、输出、资源、模型策略、权限边界、风险和验收方式。

建议能力：

```text
创建 Task Plan；
查询 Task Plan；
更新 Task Plan；
提交 Task Plan 审核；
审核 Task Plan；
列出 Task Plan；
关联 TaskGraph；
作废 Task Plan；
生成 Plan Delta。
```

Task Plan API 不负责：

```text
1. 直接执行节点；
2. 直接批准高风险动作；
3. 替代 P 阶段晋级 Gate；
4. 替代 Acceptance；
5. 替代 P5 Evidence。
```

---

## 7. Task Plan 创建建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/task-plans
```

请求字段建议：

```text
stage：string，必填，所属阶段；
stage_plan_id：string，可选，关联 Stage Plan；
task_title：string，必填，任务标题；
task_objective：string，必填，任务目标；
input_refs：array，可选，输入引用；
expected_outputs：array，可选，期望输出；
required_artifacts：array，可选，产物要求；
required_evidence：array，可选，证据要求；
required_agent_type：string，可选，建议 Agent 类型；
required_skill_refs：array，可选，Skill 引用；
required_resource_refs：array，可选，资源引用；
model_policy_override：object，可选，模型策略覆盖；
permission_boundary：object，可选，权限边界；
risk_level：string，可选，风险级别；
acceptance_criteria：array，可选，验收标准；
failure_policy：object，可选，失败策略；
gate_policy：object，可选，Gate 策略；
trace_reason：string，可选，创建原因。
```

响应字段建议：

```text
request_id；
status；
data.task_plan_id；
data.project_id；
data.run_id；
data.stage；
data.task_plan_status；
data.version；
data.review_required；
data.gate_ref；
trace_ref；
warnings；
next_actions。
```

规则：

```text
1. Task Plan 创建不等于任务执行；
2. Task Plan 审核不等于阶段晋级授权；
3. required_resource_refs 需遵循统一资源调用流程；
4. 高风险任务必须有 gate_policy；
5. Task Plan 必须能生成或关联 TaskGraph 节点。
```

---

## 8. Task Plan 状态建议

Task Plan 状态建议（遵守 01-字段规范 §5）：

```text
draft：草案；
submitted：已提交；
under_review：审核中；
approved：已批准；
rejected：已拒绝；
ready：可进入 TaskGraph；
running：执行中；
waiting_gate：等待 Gate；
blocked：阻塞；
rework_required：需要返工；
completed：任务计划对应工作完成；
superseded：已被替代；
canceled：已取消；
archived：已归档。
```

规则：

```text
1. completed 必须有 Artifact / Evidence / Acceptance 依据；
2. waiting_gate 必须有关联 gate_ref；
3. blocked 必须有 blocking_reason；
4. rework_required 必须有 rework_reason；
5. 状态变化必须 Trace。
```

---

## 9. Task Plan Batch API 总览

Task Plan Batch API 表达一组任务计划的批次目标、范围、风险、权限、验收方式和异常升级策略。

建议能力：

```text
创建 Task Plan Batch；
查询 Task Plan Batch；
更新 Task Plan Batch；
提交批次审核；
审核批次；
列出批次内 Task Plan；
拆分批次；
作废批次；
生成 Plan Delta。
```

规则：

```text
1. Task Plan Batch 当前不设置固定数量上限；
2. 但必须说明批次目标、任务范围、风险级别、权限边界、验收方式和异常升级策略；
3. 批次审核不等于阶段晋级授权；
4. 大批次如引发上下文膨胀、越界或验收困难，应触发调整申请或 Gate；
5. 批次不得隐藏单个 Task Plan 的风险。
```

---

## 10. Task Plan Batch 创建建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/task-plan-batches
```

请求字段建议：

```text
stage：string，必填，所属阶段；
stage_plan_id：string，可选；
batch_objective：string，必填，批次目标；
task_plan_refs：array，可选，已有任务计划引用；
proposed_tasks：array，可选，待创建任务草案；
scope：object，必填或可选待 R2 校准；
risk_level：string，可选；
permission_boundary：object，可选；
acceptance_strategy：object，可选；
escalation_policy：object，可选；
context_budget_note：string，可选；
trace_reason：string，可选。
```

响应字段建议：

```text
task_plan_batch_id；
project_id；
run_id；
stage；
task_plan_batch_status；
task_plan_refs；
review_required；
gate_ref；
trace_ref；
warnings；
next_actions。
```

---

## 11. Plan Delta API 建议契约

Plan Delta 表达计划变化。

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/plan-deltas
GET /api/projects/{project_id}/runs/{run_id}/plan-deltas/{plan_delta_id}
```

请求字段建议：

```text
base_plan_ref；
change_reason；
changed_scope；
changed_tasks；
changed_risk_level；
changed_permission_boundary；
impact_summary；
requires_gate；
trace_reason。
```

规则：

```text
1. 范围变化必须通过 Plan Delta 或等价变更记录表达；
2. Plan Delta 不得静默扩大任务范围；
3. 高风险或越界变更必须 Gate；
4. Plan Delta 应关联原 Stage Plan / Task Plan / TaskGraph；
5. Plan Delta 必须 Trace。
```

---

## 12. TaskGraph API 总览

TaskGraph API 表达任务节点、任务边、执行策略、上下文传递、Artifact / Evidence 传递、失败策略、Gate 策略和模型策略覆盖。

建议能力：

```text
创建 TaskGraph；
查询 TaskGraph；
更新 TaskGraph；
提交 TaskGraph 审核；
查询节点列表；
查询边列表；
更新节点状态；
更新边策略；
查询 TaskGraph Trace / Audit；
作废 TaskGraph；
生成 Plan Delta。
```

TaskGraph API 不负责：

```text
1. 替代 LangGraph 主编排（D-037）；
2. 绕过 NodeLoop；
3. 绕过 Gate；
4. 绕过 Policy；
5. 自动验证 Evidence。
```

---

## 13. TaskGraph 创建建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/task-graphs
```

请求字段建议：

```text
stage：string，必填；
stage_plan_id：string，可选；
task_plan_refs：array，可选；
graph_objective：string，必填；
nodes：array，必填或可选待 R2 校准；
edges：array，必填或可选待 R2 校准；
default_model_policy：object，可选；
default_context_passing_policy：object，可选；
default_artifact_passing_policy：object，可选；
default_evidence_passing_policy：object，可选；
default_gate_policy：object，可选；
default_failure_policy：object，可选；
trace_reason：string，可选。
```

响应字段建议：

```text
task_graph_id；
project_id；
run_id；
stage；
task_graph_status；
version；
node_count；
edge_count；
review_required；
gate_ref；
trace_ref；
warnings；
next_actions。
```

规则：

```text
1. TaskGraph 创建不等于执行；
2. TaskGraph 不得替代 LangGraph 主编排（D-037）；
3. TaskGraph 中每个 node 必须可进入 NodeLoop；
4. TaskGraph 中每条 edge 必须显式策略；
5. 高风险图变更必须 Gate / Audit。
```

---

## 14. TaskGraph 状态建议

```text
draft：草案；
submitted：已提交；
under_review：审核中；
approved：已批准；
ready：可执行；
running：执行中；
waiting_gate：等待 Gate；
blocked：阻塞；
rework_required：需要返工；
failed：失败；
completed：完成；
superseded：被替代；
canceled：已取消；
archived：归档。
```

规则：

```text
1. completed 必须有节点完成、Artifact / Evidence、Acceptance 和 Trace 依据；
2. waiting_gate 必须有关联 gate_ref；
3. failed 必须有 error_ref；
4. superseded 必须保留替代关系；
5. 状态变化必须 Trace。
```

---

## 15. Task Node 字段建议

Task Node 建议字段（遵守 01-字段规范 §3 元数据标注）：

```text
node_id；
task_graph_id；
task_plan_id；
node_title；
node_objective；
node_type；
node_status；
stage；
input_refs；
expected_outputs；
required_agent_type；
required_skill_refs；
required_resource_refs；
model_policy_override；
context_recipe_ref；
permission_boundary；
risk_level；
subtask_strategy；
artifact_contract；
evidence_contract；
acceptance_criteria；
gate_policy；
failure_policy；
trace_refs；
audit_refs；
created_at；
updated_at。
```

规则：

```text
1. node_id 必须在 TaskGraph 内唯一；
2. node 必须有明确 objective；
3. node 不得绕过 NodeLoop；
4. 高风险 node 必须有 gate_policy；
5. node 输出不得自动成为 Evidence validated。
```

---

## 16. Task Node 状态建议

```text
pending：待执行；
ready：可执行；
running：执行中；
waiting_gate：等待 Gate；
blocked：阻塞；
retrying：重试中；
rework_required：需要返工；
accepted：Acceptance 已接受；
failed：失败；
skipped：跳过；
canceled：已取消；
completed：节点完成。
```

规则：

```text
1. accepted 与 completed 是否合并由 R2 校准；
2. failed 必须有 error_ref；
3. waiting_gate 必须有 gate_ref；
4. retrying 必须遵守 retry_policy；
5. skipped 必须有 skip_reason。
```

---

## 17. Task Edge 字段建议

Task Edge 必须显式描述策略。

建议字段：

```text
edge_id；
task_graph_id；
from_node_id；
to_node_id；
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
evidence_passing_policy；
trace_policy；
audit_policy；
created_at；
updated_at。
```

规则：

```text
1. edge_type 必须明确；
2. trigger_condition 不得为空泛；
3. failure_policy 不得缺失；
4. gate_policy 不得隐藏高风险 Gate；
5. context / artifact / evidence 传递必须使用引用和策略（遵守 01-字段规范 §7）；
6. parallel_group 和 merge_strategy 必须配套。
```

---

## 18. Edge 类型与策略建议

Edge 类型建议：

```text
sequential：顺序执行；
conditional：条件跳转；
parallel_start：并行开始；
parallel_join：并行汇合；
retry：重试；
rework：返工；
gate_wait：等待 Gate；
gate_resume：Gate 后恢复；
failure_route：失败路由；
manual_route：人工指定路由。
```

策略规则：

```text
1. conditional 必须有 trigger_condition；
2. parallel_join 必须有 merge_strategy；
3. retry 必须有 retry_policy；
4. rework 必须有 rework_reason 或 rework_policy；
5. gate_wait / gate_resume 必须有关联 gate_policy；
6. failure_route 必须有 failure_policy。
```

---

## 19. SubTask 与 NodeLoop API 建议契约

Node 内部可拆分子任务。

SubTask 类型建议：

```text
inline；
serial；
parallel；
hybrid；
nested_loop。
```

SubTask 字段建议：

```text
subtask_id；
node_id；
subtask_type；
subtask_objective；
input_refs；
expected_outputs；
required_skill_refs；
required_resource_refs；
risk_level；
status；
artifact_refs；
evidence_candidate_refs；
trace_refs；
error_ref。
```

NodeLoop 状态建议：

```text
received_task；
context_loaded；
planned；
subtasks_decided；
executing；
self_checking；
packaging_outputs；
waiting_acceptance；
accepted；
rework_required；
failed。
```

规则：

```text
1. NodeLoop 状态用于内部执行可观测性；
2. NodeLoop 不替代 Node 状态；
3. SubTask 不得绕过 Node 权限边界；
4. nested_loop 必须防止无限循环；
5. SubTask 输出必须回到 Node 输出包。
```

---

## 20. Acceptance API 建议契约

Acceptance API 用于记录节点输出验收结果。

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/task-graphs/{task_graph_id}/nodes/{node_id}/acceptance
GET /api/projects/{project_id}/runs/{run_id}/task-graphs/{task_graph_id}/nodes/{node_id}/acceptance
```

请求字段建议：

```text
acceptance_result；
accepted_artifact_refs；
accepted_evidence_refs；
evidence_gap_refs；
rework_reason；
retry_reason；
gate_reason；
trace_ref；
audit_ref。
```

Acceptance 结果建议：

```text
accepted；
rework_required；
retry_required；
gate_required；
failed；
blocked。
```

规则：

```text
1. Acceptance 不替代 P5 验证；
2. Acceptance 不替代用户阶段晋级 Gate；
3. Evidence 缺失不得 accepted；
4. 高风险验收争议应 Gate；
5. Acceptance 结果必须 Trace。
```

---

## 21. Context / Artifact / Evidence 传递策略

TaskGraph 应显式声明传递策略（遵守 D-068 单一事实源——优先传引用）。

Context 传递建议：

```text
pass_refs_only：仅传引用；
pass_summary：传摘要；
pass_selected_content：传选定内容；
retrieve_on_demand：按需检索；
blocked：禁止传递。
```

Artifact 传递建议：

```text
artifact_refs_only；
artifact_summary；
artifact_package_ref；
blocked。
```

Evidence 传递建议：

```text
evidence_refs_only；
evidence_claim_summary；
evidence_gap_summary；
blocked。
```

规则：

```text
1. 默认优先传引用；
2. Evidence 必须保留 claim 关系；
3. Artifact 不得自动变 Evidence；
4. 传递策略不得泄露密钥；
5. 传递策略不得复制形成第二事实源（D-068）。
```

---

## 22. TaskPlan / TaskGraph 错误响应

建议错误类型（遵守 01-字段规范 §12 通用错误码，以下为域特定补充）：

```text
stage_plan_not_found；
task_plan_not_found；
task_plan_batch_not_found；
task_graph_not_found；
node_not_found；
edge_not_found；
invalid_graph；
invalid_edge_policy；
missing_failure_policy；
missing_gate_policy；
context_unavailable；
artifact_missing；
evidence_missing；
acceptance_failed；
gate_required；
gate_not_resolved；
policy_blocked；
state_mismatch；
trace_missing；
audit_missing；
validation_error；
internal_error。
```

规则：

```text
1. invalid_edge_policy 应指出 edge_id 或 error_details_ref；
2. missing_gate_policy 不得自动降级为 warning；
3. evidence_missing 不得伪装 completed；
4. gate_required 必须返回 gate_ref；
5. state_mismatch 必须阻止危险推进；
6. 错误响应不得泄露密钥。
```

---

## 23. TaskPlan / TaskGraph 事件建议

建议事件：

```text
stage_plan_created；
stage_plan_submitted；
stage_plan_approved；
stage_plan_rejected；
task_plan_created；
task_plan_approved；
task_plan_batch_created；
plan_delta_created；
task_graph_created；
task_graph_approved；
task_graph_started；
task_graph_completed；
node_status_changed；
edge_traversed；
node_acceptance_recorded；
evidence_gap_detected；
gate_required；
rework_required。
```

事件字段建议（遵守 00-API与集成契约总览.md §6）：

```text
event_id；
event_type；
project_id；
run_id；
stage；
stage_plan_id；
task_plan_id；
task_graph_id；
node_id；
edge_id；
payload；
trace_ref；
audit_ref；
created_at。
```

规则：

```text
1. 事件不是唯一状态源；
2. 关键状态变化必须可追踪；
3. Gate 和高风险事件必须可审计；
4. 事件 payload 不得包含密钥；
5. 前端断线重连后必须能查询后端状态。
```

---

## 24. 前端联调边界

前端可展示：

```text
Stage Plan 列表和状态；
Task Plan 列表和状态；
Task Plan Batch 范围和风险；
TaskGraph 节点和边；
Node 状态；
Edge 路由状态；
NodeLoop 内部进度摘要；
Acceptance 结果；
Artifact / Evidence 引用；
Gate 阻塞；
Trace / Audit 引用。
```

前端必须标记：

```text
mock 数据；
未接真实服务能力；
等待 Gate；
Evidence 缺失；
Trace / Audit 缺失；
高风险节点；
高风险边策略；
需要返工；
执行失败。
```

前端不得：

```text
1. 用前端拖拽结果直接替代后端 TaskGraph；
2. 隐藏 Edge 的 Gate / failure 策略；
3. 将 Evidence 缺失显示为完成；
4. 将 Acceptance 结果伪装为 P5 验证通过；
5. 展示明文密钥。
```

---

## 25. R2 / R4 / R10-R12 校准项

### 25.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. 是否明确所有字段均为 R1 建议契约；
3. TaskGraph 边策略字段是否完整；
4. NodeLoop 是否仍为节点内部小循环；
5. 是否仍有 mission_id / Mission 产品层残留；
6. 错误码是否与 01-字段规范一致；
7. 是否重复上级事实，需要改为引用。
```

### 25.2 R4 工程骨架校准

```text
1. FastAPI 路由结构；
2. Pydantic schema；
3. Stage Plan / Task Plan / TaskGraph 数据模型；
4. LangGraph node / edge 映射；
5. checkpoint / interrupt / resume 接口；
6. Trace / Audit Writer；
7. SSE 事件实现。
```

### 25.3 R10 P2-P3 评估与规划链路校准

```text
1. Stage Plan API 是否支撑 P2 / P3；
2. Task Plan / Task Plan Batch 是否支撑计划审核；
3. TaskGraph 是否能表达评估与规划路线；
4. 风险、权限、Gate 策略是否可表示；
5. 前端是否能展示图和计划状态。
```

### 25.4 R11 P4 执行链路校准

```text
1. TaskGraph 是否能驱动 P4 Node 执行；
2. NodeLoop 状态是否可观测；
3. Tool / MCP / Execution Session 引用是否可挂到 Node；
4. 高风险节点是否 Gate；
5. 执行失败是否进入 retry / rework / failed 路由。
```

### 25.5 R12 P5-P6 验证与交付链路校准

```text
1. Evidence 传递策略是否支撑 P5；
2. Acceptance 与 P5 验证边界是否清晰；
3. TaskGraph 是否能表达返工路径；
4. P6 是否能索引 TaskGraph 产物、证据、Trace、Audit；
5. Evidence 缺失是否阻断 completed。
```

---

## 26. TaskPlan / TaskGraph API 红线

```text
1. 不得将 TaskGraph API 设计成替代 LangGraph 的自研状态机（D-037）；
2. 不得让 Node 绕过 NodeLoop；
3. 不得隐藏 Edge 的 gate_policy / failure_policy；
4. 不得将 Task Plan 审核等同于阶段晋级授权；
5. 不得将 Task Plan Batch 作为越权一次性大计划；
6. 不得无 Trace 更新 Stage Plan / Task Plan / TaskGraph 关键状态；
7. 不得无 Audit 执行高风险计划变更或 Gate 决策；
8. 不得将 Artifact 自动标记为 Evidence；
9. 不得将 Evidence 缺失伪装为 completed；
10. 不得用 Acceptance 替代 P5 验证；
11. 不得通过 API 绕过 Policy / Gate / Audit；
12. 不得引入 Mission 产品层（D-070）；
13. 不得在 API 响应、错误、事件、Trace、Audit 中泄露 Key / Token / Secret / Password（D-032）；
14. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 27. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确对象边界；
2. 明确 API 路由归口建议；
3. 明确 Stage Plan API 总览、创建和状态建议；
4. 明确 Task Plan API 总览、创建和状态建议；
5. 明确 Task Plan Batch API 总览和创建建议；
6. 明确 Plan Delta API 建议契约；
7. 明确 TaskGraph API 总览、创建和状态建议；
8. 明确 Task Node 字段与状态建议；
9. 明确 Task Edge 字段、类型与策略建议；
10. 明确 SubTask 与 NodeLoop API 建议契约；
11. 明确 Acceptance API 建议契约；
12. 明确 Context / Artifact / Evidence 传递策略；
13. 明确错误响应；
14. 明确事件建议；
15. 明确前端联调边界；
16. 明确 R2 / R4 / R10-R12 校准项；
17. 明确 TaskPlan / TaskGraph API 红线；
18. 未新增产品决策；
19. 未引入 Mission 产品层；
20. 未固定最终实现 schema；
21. 决策交叉引用完整（D-016/D-037/D-068）；
22. 待确认项显式列出。
```

---

## 28. 待确认项

```text
1. §18 Edge 类型 10 种是否需要增减——manual_route 和 gate_wait/gate_resume 的边界是否清晰（两者都涉及人工介入但场景不同）。

2. §16 Task Node 状态 accepted 与 completed 是否合并——草稿自身已标注"R2 校准是否需要合并"。

3. §19 NodeLoop 9 步循环（received_task→...→accepted/failed）是否在实际 Agent 执行中可观测到所有状态——部分中间态（如 context_loaded、planned）可能过于细粒度。

4. §15 Task Node 字段 28 个——R4 数据建模时是否需要精简（如 model_policy_override 是否应继承 TaskGraph 默认策略而非逐 Node 覆盖）。
```
