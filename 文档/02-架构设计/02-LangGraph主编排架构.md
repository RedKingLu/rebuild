# 02-LangGraph主编排架构

> 文档路径：`文档/02-架构设计/02-LangGraph主编排架构.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/02-LangGraph主编排架构.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：在 `00-架构总纲.md` 与 `01-系统分层与技术栈基准.md` 基础上，定义 rebuild 当前版本中 LangGraph 作为主编排底座的职责、边界、图结构、状态流转、checkpoint、interrupt/resume、Gate、Run、TaskGraph、NodeLoop、事件、证据与审计关系。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-037, D-065）、`文档/02-架构设计/00-架构总纲.md`、`文档/02-架构设计/01-系统分层与技术栈基准.md`。
> 重要边界：本文不替代 LangGraph 官方文档，不固定最终代码 API，不固定具体节点函数签名，不固定数据库表结构。R1 只给出架构基准，R2/R4 需结合真实工程骨架校准为实现设计。
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守以下原则：

```text
1.  LangGraph 是 rebuild 当前版本的主编排底座（D-037）；
2.  P0-P6 主流程、阶段跳转、Gate、checkpoint、interrupt/resume 应落在 LangGraph 编排体系中；
3.  不得以自研状态机替代 LangGraph 主编排（D-065）；
4.  自研部分只能作为状态 schema、API、SSE、Adapter、Evidence/Artifact/Trace 数据层、
    Workspace Service、ExecutionProvider、Policy/Audit 等外部服务或适配层；
5.  编排层保持薄，不在图中堆积大量业务逻辑（D-065）；
6.  业务执行下沉到 Agent/Skill/Tool/MCP/Resource；
7.  模型调用下沉到 ModelGateway；
8.  安全授权下沉到 Policy/Hook/Authorization；
9.  Artifact/Evidence/Trace/Audit 必须贯穿编排；
10. 前端 UI、终端、容器、内存不得成为唯一状态源（D-053）。
```

本文不得：

```text
1.  新增 P 阶段；
2.  新增 Mission 产品层（D-070）；
3.  将 Fusion 写成特殊流程（D-035）；
4.  将 Case 写成可执行能力（D-040）；
5.  将 LangGraph 只作为形式包装，实际用自研状态机执行；
6.  将执行成功等同于验证通过（D-066）；
7.  将模型判断等同于验收结论；
8.  将 checkpoint 等同于完整审计。
```

> 本节"编写原则/本文不得"中的通用红线（LangGraph 主编排不可自研替代 D-037/D-065、不新增 Mission D-070、Fusion 非特殊流程 D-035、Case 不可执行 D-040、执行成功≠验证通过 D-066、状态源多源 D-053 等）完整总表见 AGENTS §18 + 01-决策记录；本文仅就地保留与 LangGraph 主编排职责相关的子集。

---

## 1. LangGraph 在 rebuild 中的位置

LangGraph 位于架构分层中的：

```text
Arch-L2 编排与运行时层
```

它上接：

```text
Arch-L1 产品流程层：Project、P0-P6、Run、Gate、用户流程；
Arch-L0 治理与策略层：决策、准则、变更控制、安全红线。
```

它下接：

```text
Arch-L3 Agent/Skill/资源层；
Arch-L4 ModelGateway；
Arch-L5 Workspace/Environment/Execution；
Arch-L6 Data/Event/Evidence/Trace/Audit；
Arch-L7 API/SSE/前端；
Arch-L8 测试、验证与交付。
```

LangGraph 的定位是：

```text
主流程编排器；
阶段状态推进器；
Gate 中断与恢复承载者；
节点流转与 checkpoint 承载者；
Run 运行状态的核心来源之一。
```

LangGraph 不是：

```text
业务服务全集；
数据库替代品；
文件系统替代品；
审计系统替代品；
前端状态管理器；
Agent 能力本身；
模型网关本身；
安全授权系统本身。
```

---

## 2. 主编排目标

LangGraph 主编排必须支持：

```text
1.  Project 进入 P0-P6；
2.  各 P 阶段可裁剪；
3.  被裁剪阶段不伪装 completed；
4.  被启用阶段必须有输入、输出、Artifact、Evidence、Trace/Audit、Gate 和完成条件；
5.  阶段内部可按 Manual/Plan/Auto 执行；
6.  阶段晋级必须用户 Gate（D-023）；
7.  节点内部通过 NodeLoop 组织（D-020）；
8.  复杂任务可通过 TaskGraph 表达（D-022）；
9.  高风险动作可 interrupt；
10. Gate 后可 resume；
11. 执行状态可恢复（D-053）；
12. 失败、返工、重试、跳过均可追踪；
13. P5 验证可产出可验证 Evidence（D-066）；
14. P6 交付可追溯到 Artifact/Evidence/Trace/Audit。
```

主编排成功不等于产品完成。

只有同时具备：

```text
Artifact；
Evidence；
Trace；
Audit；
Gate；
验证结论；
交付说明。
```

才可进入对应验收判断。

---

## 3. 图结构建议

R1 建议采用分层图结构，而不是单一巨型图。

```text
Project Flow Graph
  ├── P0 接入 Stage Subgraph
  ├── P1 建档 Stage Subgraph
  ├── P2 评估 Stage Subgraph
  ├── P3 规划 Stage Subgraph
  ├── P4 执行 Stage Subgraph
  ├── P5 验证 Stage Subgraph
  └── P6 交付 Stage Subgraph
```

每个 Stage Subgraph 内部可包含：

```text
stage_start；
load_context；
prepare_stage_plan；
prepare_task_plan；
execute_task_graph；
collect_artifacts；
collect_evidence；
self_check；
acceptance_check；
gate_before_next_stage；
stage_complete；
stage_failed；
stage_rework；
stage_skipped。
```

注意：以上是架构建议，不是固定函数名或固定代码实现。

R2/R4 可以根据实际代码合并或拆分节点，但不得丢失职责。

---

## 4. Project Flow Graph

### 4.1 职责

Project Flow Graph 负责：

```text
1. 管理 Project 的 P0-P6 主流程；
2. 决定下一阶段是否可进入；
3. 处理阶段裁剪；
4. 处理阶段 Gate；
5. 记录 Run 当前状态；
6. 将阶段产物、证据和状态传递给下一阶段；
7. 处理失败、返工和终止。
```

### 4.2 输入

Project Flow Graph 的输入应包括：

```text
project_id；
run_id；
current_stage；
selected_stage_scope；
execution_mode；
project_context_ref；
workspace_ref；
active_gate；
policy_context_ref；
user_decision_ref。
```

字段名称仅为建议，实际字段归 API 与数据契约文档校准。

### 4.3 输出

Project Flow Graph 的输出应包括：

```text
updated_run_state；
stage_status；
active_gate；
artifact_refs；
evidence_refs；
trace_refs；
audit_refs；
next_stage_candidate；
blocking_issues；
rework_recommendation；
completion_status。
```

### 4.4 阶段流转规则

阶段流转必须遵守：

```text
1. P 阶段晋级必须用户授权（D-023）；
2. Manual/Plan/Auto 不改变阶段晋级 Gate（D-024）；
3. 被裁剪阶段进入 skipped/not_applicable，而不是 completed；
4. 没有 Evidence 的阶段不得 completed；
5. 没有 Artifact 的交付不得 completed；
6. 高风险动作未授权时必须 paused/waiting_gate；
7. 失败可进入 rework/failed/waiting_user_decision；
8. 所有状态变化必须可追踪。
```

---

## 5. Stage Subgraph

### 5.1 职责

Stage Subgraph 负责某个 P 阶段内部流转。

通用职责：

```text
1.  读取阶段上下文；
2.  准备阶段计划；
3.  准备任务计划；
4.  调用 TaskGraph 或节点执行；
5.  收集 Artifact；
6.  收集 Evidence；
7.  执行自验证；
8.  触发 Acceptance；
9.  触发必要 Gate；
10. 给出阶段完成、返工或失败结论。
```

### 5.2 P0 接入子图

P0 接入子图应支撑：

```text
项目来源确认；
接入方式判断；
权限和材料状态确认；
初始风险登记；
接入 Evidence 生成；
进入 P1 前用户 Gate。
```

首批接入来源包括：

```text
本地目录；
Git；
ZIP；
GitHub。
```

### 5.3 P1 建档子图

P1 建档子图应支撑：

```text
源码结构识别；
文档和材料索引；
配置与依赖识别；
Environment Profile 草案；
项目档案 Artifact；
建档 Evidence；
进入 P2 前用户 Gate。
```

### 5.4 P2 评估子图

P2 评估子图应支撑：

```text
风险评估；
可行性评估；
阻塞项识别；
不确定项登记；
模型辅助分析；
评估 Evidence；
进入 P3 前用户 Gate。
```

模型输出不能自动成为事实。

### 5.5 P3 规划子图

P3 规划子图应支撑：

```text
迁移方案；
Stage Plan；
Task Plan/Task Plan Batch；
TaskGraph；
风险级别；
权限边界；
验收方式；
Gate 计划；
进入 P4 前用户 Gate。
```

### 5.6 P4 执行子图

P4 执行子图应支撑：

```text
按已确认计划执行；
调用 Node Worker Agent；
调用 Skill/Tool/MCP；
调用 Execution Session；
生成 Patch 或执行产物；
记录 Trace；
记录 Audit；
失败或越界时暂停；
进入 P5 前用户 Gate。
```

### 5.7 P5 验证子图

P5 验证子图应支撑：

```text
构建验证；
运行/启动验证；
测试保留与生成；
回归基线对比；
关键行为等价或差异显式登记；
验证 Evidence；
失败项与返工建议；
进入 P6 前用户 Gate。
```

P5 不得以"看起来完成"通过（D-066）。

### 5.8 P6 交付子图

P6 交付子图应支撑：

```text
交付包生成；
交付说明；
Evidence 包；
Artifact/Trace/Audit 索引；
遗留风险；
后续建议；
最终用户 Gate。
```

---

## 6. NodeLoop 与 LangGraph 的关系

NodeLoop 是每个 node 内部的标准小循环（D-020）。

标准小循环包括：

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

LangGraph 负责 node 之间流转。

NodeLoop 负责 node 内部执行闭环。

二者边界：

```text
LangGraph：流程、状态、中断、恢复、节点路由；
NodeLoop：节点内部任务处理、子任务拆分、执行、自验证、产物包。
```

NodeLoop 不应变成自研主编排。

如果 NodeLoop 需要产生子任务，应通过 TaskGraph 或子图表达，而不是在节点内部形成不可追踪的隐式流程。

---

## 7. TaskGraph 与 LangGraph 的关系

TaskGraph 表达任务依赖与执行路径（D-022）。

LangGraph 承载阶段和节点流转。

建议关系：

```text
Project Flow Graph 管 P0-P6；
Stage Subgraph 管阶段内部主步骤；
TaskGraph 管任务依赖和并行/串行/嵌套；
NodeLoop 管单节点内部小循环。
```

TaskGraph 边策略必须显式描述：

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

R1 不固定字段 schema。

R2/R4 需根据 API 与实现契约校准字段。

---

## 8. 状态模型建议

LangGraph 状态应以 Run 为中心，但不得替代数据库、workspace 和审计记录。

### 8.1 建议状态域

```text
project_ref；
run_ref；
current_stage；
stage_scope；
stage_status_map；
execution_mode；
active_node；
active_gate；
plan_refs；
task_graph_ref；
artifact_refs；
evidence_refs；
trace_refs；
audit_refs；
workspace_ref；
environment_profile_ref；
execution_session_refs；
policy_context_ref；
model_policy_ref；
resource_usage_refs；
blocking_issues；
rework_recommendations；
resume_token_or_checkpoint_ref。
```

以上为建议状态域，不是 API 字段或数据库 schema。

### 8.2 状态来源边界（D-053）

状态恢复依赖：

```text
数据库；
LangGraph checkpoint；
workspace 文件；
trace/audit；
event log。
```

LangGraph checkpoint 是重要状态来源，但不是唯一事实源。

### 8.3 状态不得包含

LangGraph 状态不得长期保存：

```text
明文 Key；
Token；
Secret；
Password；
完整大型源码内容；
未脱敏 env；
不可追踪的临时推断；
无需持久化的大量模型上下文；
已过期旧版本事实。
```

---

## 9. checkpoint 策略

### 9.1 checkpoint 目标

checkpoint 目标是支持：

```text
1. 任务恢复；
2. Gate 后 resume；
3. 失败后定位；
4. 后台任务状态恢复；
5. 与 event log、Trace/Audit 对齐。
```

### 9.2 checkpoint 不替代的内容

checkpoint 不替代：

```text
数据库持久化；
文件型 Artifact；
Evidence；
Trace；
Audit；
交付包；
版本化文档。
```

### 9.3 checkpoint 粒度建议

建议在以下节点前后形成可恢复点：

```text
阶段开始；
阶段计划生成；
Task Plan/TaskGraph 生成；
高风险动作前；
Gate 打开；
Gate 关闭；
执行产物生成；
Evidence 生成；
阶段完成；
阶段失败；
返工路径进入；
P6 交付前。
```

具体 checkpoint 实现由 R4/R9-R12 校准。

---

## 10. interrupt / resume 与 Gate

### 10.1 触发 interrupt 的情况

必须 interrupt 的情况：

```text
P 阶段晋级；
L5 高风险动作（D-034）；
外部系统写操作；
远程写操作；
删除或不可逆操作；
密钥相关操作；
低置信度或冲突结论；
范围变化；
用户明确要求暂停；
Policy 冲突。
```

### 10.2 Gate 类型

建议 Gate 类型包括：

```text
stage_gate：阶段晋级；
plan_gate：计划确认；
action_gate：高风险动作确认；
policy_gate：Policy 冲突确认；
verification_gate：验证结果确认；
delivery_gate：交付确认；
scope_gate：范围变更确认。
```

名称为建议，R2/R4 可校准。

### 10.3 Gate 状态

Gate 应至少表达：

```text
gate_id；
gate_type；
project_ref；
run_ref；
stage；
reason；
risk_level；
requested_action；
options；
status；
created_at；
resolved_at；
resolved_by；
decision_ref；
audit_ref。
```

具体字段归 API 契约校准。

### 10.4 resume 要求

resume 时必须：

```text
1. 读取 Gate 决策；
2. 记录 Audit；
3. 恢复 checkpoint；
4. 校验 Policy 是否仍满足；
5. 校验上下文是否过期；
6. 继续原节点或进入替代分支；
7. 向前端发出状态更新事件。
```

---

## 11. Manual / Plan / Auto 执行模式映射

### 11.1 三模式共同点

三种模式共同遵守（D-023~D-029）：

```text
1. 不改变 P0-P6；
2. 不改变阶段晋级用户 Gate；
3. 不绕过 Policy；
4. 不绕过 Trace/Audit；
5. 不允许 L5 高风险动作自动放行；
6. 不允许无 Evidence 验收通过。
```

### 11.2 Manual Mode

Manual Mode 下：

```text
用户审核 Stage Plan；
用户审核 Task Plan；
用户审核授权动作；
用户审核阶段晋级；
LangGraph 需要更频繁 interrupt。
```

### 11.3 Plan Mode

Plan Mode 下：

```text
用户审核 Stage Plan；
用户审核 Task Plan 或 Task Plan Batch；
计划内动作由 Hook + Policy 检查并放行；
越界、风险升级、不确定项回用户；
用户审核阶段晋级。
```

### 11.4 Auto Mode

Auto Mode 下：

```text
Agent 审核 Stage Plan；
Agent 审核 Task Plan；
阶段内授权由 Hook + Policy + Auto Review Agent 处理；
高风险、低置信度、冲突、超范围回用户；
用户审核阶段晋级。
```

Auto Mode 不代表无人自治。

---

## 12. 与 Agent / Skill 的协作

LangGraph 节点不应直接承担所有业务能力。

建议节点职责：

```text
1. 组装上下文引用；
2. 调用 Agent/Skill/Tool；
3. 接收执行结果；
4. 记录 Artifact/Evidence/Trace/Audit 引用；
5. 判断下一步路由；
6. 触发 Gate 或失败分支。
```

Agent/Skill 负责：

```text
1. 执行具体任务；
2. 读取必要上下文；
3. 调用模型或工具；
4. 生成产物；
5. 自验证；
6. 返回结构化结果。
```

LangGraph 不应内置大量提示词、业务知识或工具细节。

---

## 13. 与 ModelGateway 的协作

LangGraph 不直接管理模型 Provider。

模型调用路径应为：

```text
LangGraph node
  → Agent/Skill
  → ModelGateway
  → LiteLLM SDK Adapter/Provider
```

模型选择优先级（D-036）：

```text
用户临时指定；
Task/Node override；
Agent 默认模型；
Project 默认模型；
System 默认模型。
```

Fusion 只作为 ModelGateway 管理的模型能力或模型策略（D-035）。

LangGraph 不应出现：

```text
Fusion 阶段；
Fusion 特殊流程；
Fusion 自动验收；
Fusion 自动授权。
```

---

## 14. 与 Workspace / Execution 的协作

LangGraph 不直接替代 Workspace Service 或 Execution Session。

推荐调用关系：

```text
LangGraph node
  → Workspace Service：读取/写入项目文件、Artifact、Evidence 引用
  → ExecutionProvider/Execution Session：执行命令、构建、测试、Patch、Tool/MCP 调用
  → Trace/Audit Service：记录过程和关键动作
```

Execution Session 必须受：

```text
Policy；
Hook；
Gate；
Trace；
Audit。
```

约束。

退出工作区 UI 不应终止 LangGraph Run（D-052）。

但遇到 Gate、高风险动作、外部写操作或不可逆动作，Run 应暂停等待授权。

---

## 15. 与 Artifact / Evidence / Trace / Audit 的关系

每个关键节点应明确：

```text
输入 Artifact/Evidence；
输出 Artifact；
输出 Evidence；
Trace 记录；
是否需要 Audit；
是否需要 Gate。
```

### 15.1 Artifact

节点可产生 Artifact。

Artifact 需要：

```text
1. 可定位；
2. 可关联 Project/Run/Stage/Task；
3. 可被前端查看；
4. 可进入验收；
5. 不自动晋升正式文档（D-014）。
```

### 15.2 Evidence

节点结论如影响阶段完成、验证、交付，必须产生 Evidence（D-066）。

无 Evidence 时，只能输出：

```text
建议；
推断；
待验证；
失败；
待补证。
```

### 15.3 Trace

Trace 记录：

```text
执行了什么；
调用了什么资源；
使用了什么模型；
读取了哪些输入引用；
产生了哪些输出引用；
失败或重试了什么。
```

### 15.4 Audit

Audit 记录：

```text
用户 Gate 决策；
高风险动作；
Policy 冲突；
外部系统写操作；
密钥相关检查；
关键验收裁决。
```

---

## 16. 事件与前端同步

LangGraph 状态变化需要通过 API/SSE/event log 同步给前端。

前端需要看到：

```text
Run 状态；
当前 P 阶段；
当前节点；
后台任务状态；
Gate 状态；
Artifact 生成；
Evidence 生成；
Trace/Audit 更新；
错误和阻塞项；
resume 后状态变化。
```

事件流不得替代状态持久化。

事件丢失时，应能通过 API 查询当前状态恢复前端展示。

---

## 17. 失败、返工与重试

LangGraph 必须支持失败和返工路径。

常见状态包括：

```text
running；
waiting_gate；
paused；
failed；
rework_required；
retrying；
skipped；
completed；
canceled。
```

名称仅为建议，实际状态契约由 API 与数据模型校准。

失败处理要求：

```text
1. 失败原因进入 Trace；
2. 高风险失败进入 Audit；
3. 可重试任务进入 retry_policy；
4. 不可重试任务进入 failed 或 waiting_user_decision；
5. 验证失败进入 rework_required；
6. 失败不得伪装 completed。
```

---

## 18. P5 验证与 LangGraph

P5 验证是产品核心（D-066）。

LangGraph 必须在 P5 子图中支持：

```text
构建验证节点；
运行/启动验证节点；
测试保留与生成节点；
回归基线对比节点；
关键行为等价或差异登记节点；
验证 Evidence 收集节点；
验证失败返工路由；
进入 P6 前 Gate。
```

P5 子图不得只做：

```text
状态标记；
报告生成；
模型判断；
前端预览确认。
```

P5 completed 必须依赖可验证 Evidence。

---

## 19. P6 交付与 LangGraph

P6 子图必须支撑：

```text
交付包组织；
交付说明生成；
Evidence 包索引；
Artifact 索引；
Trace/Audit 索引；
风险与遗留项汇总；
用户最终 Gate。
```

P6 完成只代表当前确认范围内主流程可交付，不代表所有增强能力完成（D-059）。

---

## 20. R2 / R4 / R9-R12 校准项

### 20.1 R2 文档校准

R2 需校准：

```text
1. 本文是否对齐最新决策记录（含 D-062~D-072）；
2. 是否仍有 Mission/mission_id 残留（D-070）；
3. 状态域是否需要拆分进 API 契约；
4. Gate 类型是否需要调整；
5. Stage Subgraph 是否需要更细化；
6. 是否与测试与验收目录中的 P5 验证策略一致；
7. 架构分层编号是否使用 Arch-L0~Arch-L8（与 00-架构总纲一致）。
```

### 20.2 R4 工程骨架校准

R4 需校准：

```text
1. LangGraph 代码目录；
2. graph state schema；
3. checkpoint 存储方式；
4. FastAPI 与 graph 调用边界；
5. SSE/event log 事件格式；
6. Workspace/Execution/Policy/Audit 服务边界；
7. API 建议契约到实现契约映射（D-056）。
```

> R4 校准前应按 D-067 三步法：先立自有方案 → 深读历史版本 LangGraph 相关代码 → 完善方案后执行。

### 20.3 R9-R12 主链路校准

R9-R12 需校准：

```text
R9：P0-P1 最小真实链路；
R10：P2-P3 评估与规划链路；
R11：P4 执行链路；
R12：P5-P6 验证与交付链路。
```

每阶段校准必须检查：

```text
Gate；
Artifact；
Evidence；
Trace；
Audit；
checkpoint；
interrupt/resume；
失败和返工路径。
```

---

## 21. 架构红线

```text
1.  不得用自研状态机替代 LangGraph（D-037, D-065）；
2.  不得让 LangGraph 只作为包装壳，实际流程由自研代码推进；
3.  不得让前端 UI 状态成为运行事实源（D-053）；
4.  不得把 checkpoint 当作审计；
5.  不得无 Gate 阶段晋级（D-023）；
6.  不得无 Evidence 阶段完成（D-066）；
7.  不得无 Trace/Audit 执行高风险动作；
8.  不得把 Fusion 做成 LangGraph 特殊节点流程（D-035）；
9.  不得把 Case 直接执行（D-040）；
10. 不得默认执行在线社区资源（D-061）；
11. 不得泄露密钥（D-032）；
12. 不得引入 Mission 产品层（D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md（已在各条就地标注 D 编号）；术语与风险级别（L0-L5）详见 02-术语表.md。本文红线为 LangGraph 主编排主题特有约束，整体保留。

---

## 22. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1.  明确 LangGraph 在 rebuild 中的位置（§1）；
2.  明确主编排目标（§2）；
3.  明确 Project Flow Graph 和 Stage Subgraph 建议（§3~§5）；
4.  明确 P0-P6 子图职责（§5.2~§5.8）；
5.  明确 NodeLoop 与 LangGraph 边界（§6）；
6.  明确 TaskGraph 与 LangGraph 边界（§7）；
7.  明确状态模型建议（§8）；
8.  明确 checkpoint 策略（§9）；
9.  明确 interrupt/resume 与 Gate（§10）；
10. 明确 Manual/Plan/Auto 映射（§11）；
11. 明确 Agent/Skill/ModelGateway/Workspace/Execution 协作边界（§12~§14）；
12. 明确 Artifact/Evidence/Trace/Audit 贯穿机制（§15）；
13. 明确事件与前端同步（§16）；
14. 明确失败、返工与重试（§17）；
15. 明确 P5 验证和 P6 交付要求（§18~§19）；
16. 明确 R2/R4/R9-R12 校准项（§20）；
17. 明确架构红线 12 条（§21）；
18. 未新增产品决策；
19. 未引入独立 Mission 产品层；
20. 未以自研状态机替代 LangGraph；
21. 架构分层编号与 00-架构总纲一致（Arch-L0~Arch-L8）。
```
