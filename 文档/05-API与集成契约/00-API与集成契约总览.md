# 00-API与集成契约总览

> 文档路径：`文档/05-API与集成契约/00-API与集成契约总览.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/草稿/00-API与集成契约总览.md`（v0.1）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R1 文档正式化流程
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本 API 与集成契约目录的总览、契约分层、建议契约与实现契约边界、API / SSE / 事件 / 状态 / 资源调用 / 模型调用 / Gate / Trace / Audit / Project Workspace / 项目接入等接口归口，以及 R2 / R4 / R7 / R9-R12 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-016, D-037, D-039, D-054, D-058, D-068）、`文档/02-架构设计/00-架构总纲.md`、`文档/03-流程与运行时/00-流程与运行时总览.md`、`文档/04-模型与资源/00-模型与资源总览.md`。
> 重要边界：本文是 API 与集成契约专题入口，不替代项目治理决策、架构设计、具体 API schema、后端代码、前端实现、SDK 文档、安全权限规范或测试验收规范。本文所有字段、端点和事件均为 R1 建议契约，R2/R4/R7/R9-R12 需根据真实实现校准为实现契约。
> 修订说明：R1 正式化版基于草稿 v0.1：(1) 头部标准化（补来源草稿/处理人/修订说明）；(2) 新增决策交叉引用（D-016/D-037/D-039/D-054/D-058/D-068）；(3) §22 子文档标注 [规划] 状态；(4) 新增 §26 待确认项；(5) §0 补入关联决策清单。R2：新增引用 D-073 平台助手（§16A 登记 [规划] 接口面，不固化字段契约）。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 API 与集成契约总览，不重新定义上级事实。

**关联决策速查**：

| 决策编号 | 内容 | 本文相关节 |
|---|---|---|
| D-016 | API 字段 R1 输出建议契约，R2 校准为实现契约 | §3 全文 |
| D-037 | LangGraph 是主编排底座 | §9, §10, §12 |
| D-039 | ModelGateway + LiteLLM 薄适配 | §13 |
| D-054 | mission_id 为历史决策字段，R2 校准 | §4, §9 |
| D-058 | 项目接入首批全支持本地目录/Git/ZIP/GitHub | §8 |
| D-068 | 文档事实源层级与引用规则 | §0 |

本文必须遵守：

```text
1. API 字段 R1 输出建议契约，R2 校准为实现契约（D-016）；
2. R1 API 文档应尽量给出完整字段建议，不只写抽象接口；
3. 每个字段建议标注字段名、类型、必填/可选、说明、来源、是否 R2 待校准；
4. LangGraph 是主编排底座，P0-P6 主流程、阶段跳转、Gate、checkpoint、interrupt / resume 应落在 LangGraph 编排体系中（D-037）；
5. 状态恢复依赖数据库、LangGraph checkpoint、workspace 文件、trace / audit、event log；
6. 容器、终端或前端 UI 不能作为状态源；
7. 每项目独立 Project Workspace，文件、产物、Evidence、runs、任务状态按 project_id 隔离；
8. 采用 Project Workspace / Environment Profile / Execution Session 三对象；
9. 项目接入首批支持本地目录、Git、ZIP、GitHub（D-058）；
10. 模型调用归入 ModelGateway（D-039）；
11. 资源调用走统一资源调用流程；
12. Gate 决策、高风险动作、Policy 冲突必须 Trace / Audit；
13. 不得在 API、日志、Trace、截图、提交或交接材料中泄露 Key / Token / Secret / Password（D-032）；
14. 当前版本不设独立 Mission 产品层（D-054, D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 将 R1 建议字段伪装为最终实现字段；
2. 将前端 mock API 伪装为真实能力；
3. 将前端状态作为唯一事实源；
4. 将容器、终端或前端 UI 作为状态源；
5. 在 API 字段中固化 V26.1 命名；
6. 在 API 中引入 Mission 产品层（D-070）；
7. 在未校准前固定数据库 schema；
8. 将模型、资源、Gate、Execution、Workspace 的接口做成第二套事实源；
9. 通过 API 绕过 Policy / Gate / Audit；
10. 泄露 Key / Token / Secret / Password（D-032）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. API 与集成契约定位

API 与集成契约用于约束前端、后端、编排、模型网关、资源调用、工作区、执行会话、Trace / Audit、外部接入之间的交互边界。

它回答：

```text
1. 哪些能力需要 API；
2. 哪些事件需要 SSE / event log；
3. 哪些状态是后端事实源；
4. 哪些字段是 R1 建议契约；
5. 哪些字段必须 R2 校准；
6. 前端如何展示但不伪造真实能力；
7. 模型、资源、执行、Gate、Evidence、Trace、Audit 如何通过接口衔接；
8. 外部接入如 Git、ZIP、GitHub、MCP、远程资源如何归口。
```

API 与集成契约不是：

```text
1. 最终数据库 schema；
2. 最终 OpenAPI 文档；
3. 后端代码实现；
4. 前端页面规范；
5. 安全策略本体；
6. LangGraph 流程定义本体；
7. Project / Run / TaskGraph 状态事实源替代品。
```

---

## 2. 契约分层

API 与集成契约建议分为：

```text
业务 API 契约；
运行时 API 契约；
事件 / SSE 契约；
状态契约；
集成适配契约；
资源调用契约；
模型调用契约；
Gate / Audit 契约；
Artifact / Evidence / Trace 契约；
前端展示契约。
```

分层说明：

```text
业务 API：Project、P0-P6、Stage Plan、Task Plan、交付等；
运行时 API：Run、TaskGraph、Node、Execution Session、checkpoint、resume；
事件 / SSE：run_update、gate_created、trace_written 等事件；
状态契约：Project / Run / Stage / TaskGraph / Node / Gate / Execution 状态；
集成适配：本地目录、Git、ZIP、GitHub、MCP、远程资源；
资源调用：Registry、Tool、MCP、Expert Agent、Case、Knowledge、Template；
模型调用：ModelGateway、Model Profile、Model Policy、模型调用 Trace；
Gate / Audit：Gate 创建、决策、resume、Audit 写入；
AET：Artifact、Evidence、Trace、Audit 索引与引用；
前端展示：真实能力、mock 能力、只读状态和风险提示。
```

---

## 3. 建议契约与实现契约

R1 输出建议契约。

建议契约含义：

```text
1. 用于指导前端体验壳、后端骨架、Agent 施工和独立审核；
2. 尽量给出字段建议，避免抽象空泛；
3. 字段名、类型、必填性、来源、状态流转均允许 R2 校准；
4. 不得当作已实现事实；
5. 不得直接冻结为数据库或 API 最终 schema。
```

R2 校准实现契约。

实现契约含义：

```text
1. 基于真实代码、真实数据库、真实接口和前端联调结果；
2. 校准字段名、类型、必填性、错误码、状态机和事件名称；
3. 明确 mock 与真实能力边界；
4. 明确 deprecated / superseded 字段；
5. 进入后续施工和验收依据。
```

> 关联决策：D-016

---

## 4. 通用字段规范

R1 建议每个 API 字段标注：

```text
field_name；
type；
required；
description；
source；
example；
r2_calibration_required；
sensitive；
trace_required；
audit_required。
```

字段规则：

```text
1. project_id、run_id、stage、task_graph_id、node_id 等应保持跨模块一致；
2. 如出现 mission_id，R2 必须校准为 run_goal / objective / task_group_id 或删除（D-054）；
3. V26.1 不应固化进长期字段名、表名、包名或路由；
4. 密钥字段只能使用引用或脱敏标识；
5. status 字段必须明确所属对象，不得混用；
6. 时间字段必须说明语义；
7. Trace / Audit 相关字段必须保留引用。
```

---

## 5. 通用响应结构建议

成功响应建议：

```text
request_id；
status；
data；
trace_ref；
warnings；
next_actions；
created_at。
```

失败响应建议：

```text
request_id；
status；
error_code；
error_message；
error_details_ref；
trace_ref；
gate_ref；
retryable；
next_actions；
created_at。
```

规则：

```text
1. 失败不得伪装成功；
2. 高风险失败应可追踪；
3. Gate 阻塞应返回 gate_ref；
4. Policy 禁止应返回明确 error_code；
5. 错误响应不得泄露密钥或敏感内容。
```

---

## 6. 通用事件 / SSE 契约

事件建议用于：

```text
Run 状态变化；
Stage 状态变化；
TaskGraph 节点变化；
Execution Session 输出；
Gate 创建与决策；
Trace / Audit 写入；
Artifact / Evidence 更新；
模型调用状态；
资源调用状态；
项目接入进度。
```

事件建议字段：

```text
event_id；
event_type；
project_id；
run_id；
stage；
task_graph_id；
node_id；
source；
payload；
trace_ref；
audit_ref；
created_at。
```

事件规则：

```text
1. 事件不是唯一状态源；
2. 前端断线重连后必须能从后端状态恢复；
3. SSE 不得承载明文密钥；
4. 关键状态变化必须可追踪；
5. Gate 和高风险动作必须可审计。
```

---

## 7. Project API 契约归口

Project API 管理用户可见主对象 Project。

建议能力：

```text
创建 Project；
查询 Project；
更新 Project 元数据；
列出 Project；
归档 Project；
查询 Project Workspace；
查询 Project 当前 Run；
查询 Project 阶段状态；
查询 Project Artifact / Evidence / Trace / Audit 索引。
```

建议字段：

```text
project_id；
name；
description；
source_type；
source_ref；
workspace_ref；
current_stage；
status；
created_at；
updated_at；
archived_at。
```

规则：

```text
1. Project 是用户可见主对象；
2. 拆分工作范围优先创建多个 Project；
3. 同一 Project 内部复杂任务通过 Run / Stage Plan / Task Plan / TaskGraph 表达；
4. 不引入 Mission 产品层（D-070）。
```

---

## 8. 项目接入 API 契约归口

项目接入首批支持：

```text
本地目录；
Git；
ZIP；
GitHub。
```

建议能力：

```text
接入本地目录；
接入 Git URL；
上传 ZIP；
接入 GitHub；
查询接入进度；
查询接入结果；
查询接入错误；
生成 P0 接入 Artifact / Evidence 候选。
```

建议字段：

```text
import_id；
project_id；
source_type；
source_ref；
status；
progress；
workspace_ref；
artifact_refs；
evidence_candidate_refs；
trace_ref；
error_ref；
created_at；
completed_at。
```

规则：

```text
1. 接入失败不得伪装成功；
2. GitHub / Git 凭据不得明文返回；
3. ZIP 解压必须受 Workspace 边界控制；
4. 接入状态应可通过事件或轮询查看；
5. 接入结果应进入 P0 / P1 流程，而不是直接标记完成迁移。
```

> 关联决策：D-058

---

## 9. Run API 契约归口

Run 是 Project 内部一次推进过程。

建议能力：

```text
创建 Run；
查询 Run；
启动 Run；
暂停 Run；
取消 Run；
resume Run；
查询 Run 状态；
查询 active Gate；
查询 Run Trace / Audit；
查询 Run Artifact / Evidence。
```

建议字段：

```text
run_id；
project_id；
run_goal；
current_stage；
execution_mode；
status；
active_gate；
can_pause；
can_cancel；
can_resume；
started_at；
updated_at；
completed_at。
```

说明：

```text
1. 若旧字段出现 mission_id，R2 应校准为 run_goal / objective / task_group_id 或删除（D-054）；
2. Run 状态恢复不得依赖前端 UI；
3. Run 状态应与 LangGraph checkpoint、workspace、Trace / Audit、event log 对齐（D-037）。
```

---

## 10. Stage / P0-P6 API 契约归口

Stage API 表达 P0-P6 阶段状态、阶段计划、阶段产物和阶段晋级。

建议能力：

```text
查询阶段列表；
查询阶段详情；
查询阶段状态；
提交 Stage Plan；
审核 Stage Plan；
查询阶段 Artifact / Evidence；
请求阶段晋级 Gate；
查询阶段 Gate；
进入下一阶段。
```

建议字段：

```text
project_id；
run_id；
stage；
stage_name；
status；
stage_plan_ref；
artifact_refs；
evidence_refs；
gate_ref；
trace_ref；
audit_ref；
updated_at。
```

规则：

```text
1. 被裁剪阶段不得伪装 completed；
2. 启用阶段必须具备 Gate、Artifact、Evidence 和完成条件；
3. P 阶段晋级 Gate 必须用户授权；
4. 阶段状态不得由前端单独决定。
```

---

## 11. Plan / TaskGraph API 契约归口

Plan / TaskGraph API 表达 Stage Plan、Task Plan、Task Plan Batch 和 TaskGraph。

建议能力：

```text
创建 Stage Plan；
查询 Stage Plan；
创建 Task Plan；
创建 Task Plan Batch；
查询 TaskGraph；
更新 TaskGraph；
查询节点状态；
查询边策略；
查询 Plan Delta；
提交计划审核。
```

建议字段：

```text
stage_plan_id；
task_plan_id；
task_graph_id；
project_id；
run_id；
stage；
version；
status；
nodes；
edges；
model_policy_override；
context_passing_policy；
artifact_passing_policy；
evidence_passing_policy；
gate_policy；
failure_policy；
trace_ref。
```

规则：

```text
1. TaskGraph 边策略必须显式；
2. Plan Mode 采用 Stage Plan、Task Plan / To-do Plan、Plan Delta；
3. Task Plan Batch 暂不设置固定数量上限，但必须说明批次目标、范围、风险级别、权限边界、验收方式和异常升级策略；
4. 计划审核不等于阶段晋级授权。
```

---

## 12. Gate API 契约归口

Gate API 表达暂停、授权、确认、拒绝、返工和恢复。

建议能力：

```text
创建 Gate；
查询 Gate；
查询 active Gate；
提交 Gate 决策；
取消 Gate；
查询 Gate Audit；
resume Run；
查询 Gate 关联 Trace。
```

建议字段：

```text
gate_id；
gate_type；
project_id；
run_id；
stage；
node_id；
reason；
risk_level；
options；
status；
decision；
decision_by；
decision_reason；
checkpoint_ref；
resume_target；
trace_ref；
audit_ref；
created_at；
resolved_at。
```

规则：

```text
1. Gate 决策必须 Audit；
2. L5 高风险动作必须用户 Gate；
3. Gate 未决策不得 resume 阻塞动作；
4. Gate 后 resume 必须校验 checkpoint、Policy、Workspace、Artifact / Evidence、Trace / Audit；
5. Gate 状态不得只存在前端。
```

---

## 13. ModelGateway API 契约归口

ModelGateway API 表达模型配置、模型策略、模型调用和模型调用 Trace。

建议能力：

```text
查询模型配置；
查询 Model Profile；
查询 Model Policy；
设置 Project 默认模型；
设置 Agent 默认模型；
设置 Task / Node override；
测试模型连通性；
提交模型调用；
查询模型调用 Trace；
查询 token / 成本使用情况；
查询 Fusion 能力状态。
```

建议字段：

```text
model_call_id；
project_id；
run_id；
stage；
node_id；
agent_ref；
skill_ref；
model_policy_ref；
model_override；
selected_model；
provider；
status；
input_context_refs；
output_ref；
usage_summary；
trace_ref；
error_ref。
```

规则：

```text
1. 模型选择不绑定阶段；
2. 模型调用必须经 ModelGateway；
3. 模型输出不得自动成为 Evidence；
4. Fusion 是模型能力，不是特殊流程；
5. token / 成本预算属于模型模块功能，非主线 Gate 条件；
6. 密钥不得暴露给前端或 Agent 提示词。
```

> 关联决策：D-039

---

## 14. Resource / Registry API 契约归口

Resource / Registry API 表达平台资源、资源状态、资源调用前检查和资源调用记录。

建议能力：

```text
查询资源列表；
查询资源详情；
登记资源；
更新资源状态；
查询 Registry；
触发资源调用前检查；
发起资源调用；
查询资源调用记录；
查询资源 Trace / Audit；
禁用资源；
标记资源替代关系。
```

建议字段：

```text
resource_id；
resource_type；
name；
version；
status；
source_type；
source_ref；
risk_level；
permission_scope；
input_contract；
output_contract；
gate_policy；
trace_policy；
audit_policy；
review_status；
created_at；
updated_at。
```

规则：

```text
1. Case 不得直接执行；
2. 社区资源默认只读参考；
3. 未审核资源不得 active；
4. 确定性转换以 Skill / Tool / Expert Agent / MCP 形式接入；
5. 资源调用必须记录来源、风险、权限和 Trace。
```

---

## 15. Workspace / Environment / Execution API 契约归口

Workspace / Environment / Execution API 表达项目文件持久化、环境声明和执行会话。

建议能力：

```text
查询 Project Workspace；
查询文件树；
读取文件；
写入文件；
查询 Artifact / Evidence 文件；
查询 Environment Profile；
创建 Execution Session；
查询 Execution Session；
停止 Execution Session；
查询执行输出；
查询 Coding / Execution / Runtime 状态。
```

建议字段：

```text
workspace_ref；
project_id；
file_path；
material_type；
environment_profile_id；
execution_session_id；
execution_state；
runtime_state；
coding_state；
trace_ref；
audit_ref；
updated_at。
```

规则：

```text
1. 每项目独立 Project Workspace；
2. 退出工作区 UI 不等于停止任务；
3. 遇 Gate、写盘、shell、外部系统写操作或高风险动作时必须暂停等待授权；
4. Coding / Execution / Runtime 三状态分离；
5. 前端 UI 不得作为状态源。
```

---

## 16. Artifact / Evidence / Trace / Audit API 契约归口

AET API 表达产物、证据、追踪和审计。

建议能力：

```text
查询 Artifact；
创建 Artifact 记录；
查询 Evidence；
提交 Evidence 候选；
验证 Evidence；
查询 Trace；
写入 Trace；
查询 Audit；
写入 Audit；
查询 claim 与 Evidence 关系；
查询交付证据链。
```

建议字段：

```text
artifact_id；
evidence_id；
trace_id；
audit_id；
claim_id；
project_id；
run_id；
stage；
node_id；
source_ref；
status；
created_by；
created_at；
validated_at。
```

规则：

```text
1. Artifact 不自动成为项目文档；
2. Evidence 必须支撑明确 claim；
3. No Evidence / No Trace, No Trusted Result；
4. Gate 决策和高风险动作必须 Audit；
5. Trace / Audit 不得泄露密钥。
```

---

## 16A. 平台助手接口面归口（[规划]，不固化字段契约）

平台助手（全局浮动 AI 精灵 / 客服对话 Agent）是平台级内置助手，独立于 P0-P6 与 Agent / Skill 资源体系。本节仅登记其 [规划] 接口面，因用户裁决"代用户操作"为 R-future，本版本只标规划、不固化字段契约。

```text
1. 平台助手会话接口（assistant chat）        — [规划] 助手答疑 / 使用引导对话；
2. 模型连通性自测接口（model connectivity probe）— [规划] 复用 Model-Resource API / ModelGateway（D-039），
                                              验证模型 / 服务商是否接入成功，仅影响助手会话；
3. 代操作接口（assistant act-on-behalf）      — [规划 / R-future] 代用户操作平台，
                                              须经 Gate / Authorization API（§12）并纳入 Audit。
```

规则：

```text
1. 本版本不固化实现契约（无字段表、无端点冻结）；
2. 连通性自测复用 ModelGateway，不另起第二套模型调用事实源（D-039）；
3. 自测结果脱敏，不泄露 Key / Token / Secret；
4. 代操作不得绕过 Policy / Gate / Audit；
5. 平台助手不泛化为通用 AI 平台能力。
```

> 本节仅登记接口面，详述源为决策记录。本版本不固化实现契约，详见 `文档/00-项目治理/01-决策记录.md` D-073。

---

## 17. 集成适配契约归口

集成适配包括：

```text
本地目录；
Git；
ZIP；
GitHub；
MCP；
远程资源；
外部 Agent；
外部工具；
社区资源。
```

集成适配建议字段：

```text
integration_id；
integration_type；
project_id；
source_ref；
credential_ref；
permission_scope；
risk_level；
status；
last_checked_at；
trace_ref；
audit_ref。
```

规则：

```text
1. 凭据只允许引用或脱敏标识；
2. 外部系统写操作必须 Gate；
3. 社区资源默认只读参考；
4. 远程资源调用开发及合入属于 R14；
5. 社区功能建设及合入属于 R15。
```

---

## 18. 状态契约总览

状态契约应覆盖：

```text
Project Status；
Run Status；
Stage Status；
TaskGraph Status；
Node Status；
Gate Status；
Execution Session Status；
Artifact Status；
Evidence Status；
Resource Status；
Model Call Status；
Integration Status。
```

规则：

```text
1. 每类 status 必须有明确对象；
2. 不同对象状态不得混用；
3. completed 不得表示"看起来完成"；
4. blocked / waiting_gate / failed / rework_required 等状态必须可追踪；
5. 状态恢复必须依赖数据库、LangGraph checkpoint、workspace 文件、trace / audit、event log。
```

---

## 19. 权限、安全与脱敏契约

API 必须遵守：

```text
1. 允许识别 env、配置文件、密钥文件是否存在；
2. 不得在 API 响应、日志、前端、Trace、截图、提交或交接材料中泄露 Key / Token / Secret / Password（D-032）；
3. 凭据字段使用 credential_ref / secret_ref / redacted 标识；
4. Policy 优先级高于 Agent 判断（D-031）；
5. Security / Authorization Agent 不能批准 Policy 禁止的动作；
6. L5 高风险动作暂定全部强制用户 Gate（D-034）。
```

错误响应也必须脱敏。

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 20. 错误码与失败策略归口

R1 建议错误类型：

```text
validation_error；
not_found；
permission_denied；
policy_blocked；
gate_required；
gate_not_resolved；
conflict；
state_mismatch；
checkpoint_missing；
trace_missing；
audit_missing；
evidence_missing；
resource_unavailable；
model_unavailable；
integration_failed；
execution_failed；
redaction_required；
internal_error。
```

规则：

```text
1. 错误必须可追踪；
2. Policy 禁止不得返回通用失败；
3. Gate 阻塞应显式返回 gate_required 或 gate_not_resolved；
4. Evidence 缺失不得伪装 completed；
5. 错误不得泄露敏感信息。
```

---

## 21. 前端联调契约

前端可使用 API 展示：

```text
Project 列表；
Project Workspace；
P0-P6 阶段状态；
Run 状态；
后台任务状态；
Gate；
Artifact / Evidence；
Trace / Audit；
Model / Resource 状态；
Execution Session 输出；
接入进度。
```

前端必须标记：

```text
mock 能力；
未接真实服务能力；
只读资源；
高风险动作；
Gate 阻塞；
Evidence 缺口；
Trace / Audit 缺失。
```

前端不得：

```text
1. 伪装 mock 为真实能力；
2. 用前端状态替代后端状态；
3. 隐藏 Gate；
4. 隐藏失败；
5. 展示明文密钥。
```

---

## 22. 本目录后续专题文档归口

本目录实际已落位及规划中文档（按用户裁决"以后面实际更新的文档为准"，本节已更新以反映实际文档结构）：

```text
05-API与集成契约/
├── 00-API与集成契约总览.md                              [实际] ✅ 本文档（待审核）
├── 01-字段规范与错误响应规范.md                         [实际] ✅ 已生成正式候选（待审核）
├── 02-Project-Run-阶段API建议契约.md                    [实际] ✅ 已生成正式候选（待审核）
├── 03-TaskPlan-TaskGraph API建议契约.md                 [实际] ✅ 已生成正式候选（待审核）
├── 04-Artifact-Evidence-Trace-Audit API建议契约.md      [实际] ✅ 已生成正式候选（待审核）
├── 05-Gate-Authorization API建议契约.md                 [实际] 🔄 正式化中（待审核）
├── 06-Model-Resource API建议契约.md                     [实际] ✅ 已生成正式候选（待审核）
├── 07-SSE与事件契约.md                                  [实际] ✅ 已生成正式候选（待审核）
├── 08-源码接入与Git操作API建议契约.md                    [实际] ✅ 已生成正式候选（待审核，2026-06-24 补录合并）
└── 09-外部执行器与远程环境API建议契约.md                  [实际] ✅ 已生成正式候选（待审核，2026-06-24 补录）
```

> 修订说明（2026-06-23）：按用户裁决"以后面实际更新的文档为准"更新本节。原规划方案（01-Project与项目接入、02-Run-Stage-TaskGraph 等 8 份）已被 Copilot 实际输出的 6 份草稿替代。项目接入 API、Workspace-Execution-Environment API、SSE 事件流、错误码与状态等主题如后续有需求，按 D-072 证明必要性后新增。
> 修订说明（2026-06-24 补录）：08 已合并 `源码接入与Git API契约.md` 草稿的 Git 操作 API 内容（status/diff/branch/commit/push/凭据状态），文档改名 `08-源码接入与Git操作API建议契约.md`。原 08 内容完整保留。

说明：

```text
1. 不预建空文档（D-072）；
2. 新增文档必须先证明必要性；
3. 如上级已有内容，下级只引用不重复（D-068）；
4. R2 可根据实际目录和实现状态调整。
```

---

## 23. R2 / R4 / R7 / R9-R12 校准项

### 23.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. API 字段是否明确标记为 R1 建议契约；
3. 是否仍有 mission_id / Mission 产品层残留；
4. 是否重复上级事实，需要改为引用；
5. 状态契约是否与流程文档一致；
6. 安全脱敏规则是否与安全文档一致；
7. 目录归口是否需要调整。
```

### 23.2 R4 工程骨架校准

```text
1. 后端 FastAPI 路由结构；
2. 数据模型；
3. API schema；
4. SSE / event log；
5. Trace / Audit Writer；
6. Policy / Hook 接入；
7. LangGraph checkpoint / interrupt / resume 接口。
```

### 23.3 R7 集成与项目接入校准

```text
1. 本地目录接入；
2. Git 接入；
3. ZIP 接入；
4. GitHub 接入；
5. 接入进度事件；
6. 凭据脱敏；
7. 接入错误处理。
```

### 23.4 R9-R12 主链路校准

```text
R9：P0-P1 接入、建档 API 是否可支撑最小真实链路；
R10：P2-P3 评估、规划、TaskGraph API 是否可支撑评估与规划链路；
R11：P4 执行、Workspace、Execution、Tool / MCP API 是否可支撑执行链路；
R12：P5-P6 Evidence、验证、交付、Audit API 是否可支撑验证与交付链路。
```

---

## 24. API 与集成红线

```text
1. 不得把 R1 建议契约伪装为实现契约；
2. 不得在长期 API / DB / 路由中固化 V26.1；
3. 不得引入 Mission 产品层（D-070）；
4. 如出现 mission_id，R2 必须校准为 run_goal / objective / task_group_id 或删除（D-054）；
5. 不得用前端状态替代后端状态；
6. 不得用容器、终端或前端 UI 作为状态源；
7. 不得通过 API 绕过 Policy / Gate / Audit；
8. 不得无 Trace 执行关键动作；
9. 不得无 Audit 执行高风险动作；
10. 不得把 mock 能力展示为真实能力；
11. 不得把模型输出或资源输出自动标记为 Evidence validated；
12. 不得把 Evidence 缺失伪装为 completed；
13. 不得泄露 Key / Token / Secret / Password（D-032）；
14. 不得预建空文档（D-072）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 25. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 API 与集成契约定位；
2. 明确契约分层；
3. 明确建议契约与实现契约边界；
4. 明确通用字段和响应结构；
5. 明确事件 / SSE 契约；
6. 明确 Project、项目接入、Run、Stage、Plan、TaskGraph API 归口；
7. 明确 Gate、ModelGateway、Resource / Registry API 归口；
8. 明确 Workspace / Environment / Execution API 归口；
9. 明确 Artifact / Evidence / Trace / Audit API 归口；
10. 明确集成适配契约；
11. 明确状态契约；
12. 明确权限、安全与脱敏契约；
13. 明确错误码与失败策略归口；
14. 明确前端联调契约；
15. 明确后续专题文档归口；
16. 明确 R2 / R4 / R7 / R9-R12 校准项；
17. 明确 API 与集成红线；
18. 未新增产品决策；
19. 未引入 Mission 产品层；
20. 未固定最终实现 schema；
21. 决策交叉引用完整（D-016/D-037/D-039/D-054/D-058/D-068）；
22. 子文档标注 [实际]/[规划] 状态。
```

---

## 26. 待确认项

以下事项需用户或 R2 审核确认：

```text
1. §22 子文档拆分方案（8 份专题文档）是否需要增减或合并（如 03-Gate 与 04-ModelGateway 之间是否需要独立的 Policy/Hook API 文档）；
2. §20 错误码枚举是否完整，是否需要与架构设计文档中的错误处理策略统一；
3. §4 mission_id 校准为 run_goal / objective / task_group_id 的最终选择（R2 据 D-054 裁决）；
4. §13 ModelGateway API 中 token/成本使用情况的 API 粒度（仅统计摘要 vs 按 call 明细）；
5. 前端联调契约（§21）中 mock 能力的标记方式是否需要在 API 响应层面统一约定（如 `x-mock: true` header）。
```

---

## 27. 下一步建议

- 本文档作为 `05-API与集成契约/` 的首份落位文档，建议在 R2 审核时与架构设计文档（02-架构设计/）和流程文档（03-流程与运行时/）交叉审核，确保 API 契约与架构设计一致；
- R4 工程骨架启动前，应基于本文档的 §23.2 校准清单逐项落地；
- 子文档创建应在 R2 审核通过后、证明必要性后再启动（遵守 D-072 不预建空文档）。
