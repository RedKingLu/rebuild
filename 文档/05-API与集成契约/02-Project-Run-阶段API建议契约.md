# 02-Project-Run-阶段API建议契约

> 文档路径：`文档/05-API与集成契约/02-Project-Run-阶段API建议契约.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/已完成/R1/02-Project-Run-阶段API建议契约.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本中 Project、Run、P0-P6 阶段 API 的 R1 建议契约，包括对象边界、端点归口、字段建议、状态建议、错误响应、Gate / Trace / Audit / Artifact / Evidence 引用、前端联调边界与 R2/R4/R9-R12 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-016, D-054, D-058, D-070）、`文档/05-API与集成契约/00-API与集成契约总览.md`（§7-§10）、`文档/05-API与集成契约/01-字段规范与错误响应规范.md`（字段/错误码详述源）、`文档/01-产品定义/00-产品定义基准.md`、`文档/03-流程与运行时/00-流程与运行时总览.md`。
> 重要边界：本文是 Project / Run / 阶段 API 的 R1 建议契约，不替代项目治理决策、最终 OpenAPI 文档、后端路由实现、数据库 schema、前端页面规范、安全权限规范或运行时代码实现。本文所有端点、字段、状态和错误码均为 R1 建议契约，R2/R4/R9-R12 需按真实实现校准为实现契约。
> 修订说明：R1 正式化版基于草稿 v0.1：(1) 头部标准化（补来源草稿/处理人/修订说明）；(2) §0 新增关联决策速查表；(3) 关键节加注决策引用；(4) 新增 §28 待确认项（5 项，含命名/范围差异）；(5) 验收标准扩展（13→15 项）。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 Project / Run / 阶段 API 建议契约，不重新定义上级事实。

**关联决策速查**：

| 决策编号 | 内容 | 本文相关节 |
|---|---|---|
| D-016 | API 字段 R1 输出建议契约，R2 校准为实现契约 | 全文 |
| D-054 | mission_id 为历史决策字段，R2 校准 | §1, §10 |
| D-058 | 项目接入首批全支持本地目录/Git/ZIP/GitHub | §4（source_type） |
| D-070 | 不设独立 Mission 产品层 | §1, §10 |

**字段与错误码规范引用**：本文所有字段命名、元数据标注、状态字段、时间字段、引用字段、敏感字段、请求响应结构和错误码均遵守 `01-字段规范与错误响应规范.md`，本文只描述 Project/Run/Stage 域的差异。

本文必须遵守：

```text
1. API 字段 R1 输出建议契约，R2 校准为实现契约（D-016）；
2. Project 是用户可见主对象；
3. 当前版本不设独立 Mission 产品层（D-070）；
4. 同一 Project 内部复杂任务通过 Run / Stage Plan / Task Plan / TaskGraph / NodeLoop / Artifact / Evidence / Trace / Audit / Gate 表达；
5. P0-P6 是面向用户项目的可裁剪产品流程；
6. 被裁剪掉的阶段不得伪装为 completed；
7. 启用阶段必须具备 Gate、Artifact、Evidence 和完成条件；
8. P 阶段晋级 Gate 必须由用户授权（D-023）；
9. 每项目独立 Project Workspace；
10. 状态恢复依赖数据库、LangGraph checkpoint、workspace 文件、trace / audit、event log；
11. 容器、终端或前端 UI 不能作为状态源；
12. API / 响应 / 错误 / Trace / Audit 不得泄露 Key / Token / Secret / Password（D-032）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 引入 Mission 产品层（D-070）；
2. 将 mission_id 作为长期字段；
3. 将前端 UI 状态作为状态事实源；
4. 将 R1 建议字段伪装为实现契约；
5. 将 Project / Run / Stage 状态混用；
6. 将阶段 completed 解释为"看起来完成"；
7. 将 Evidence 缺失伪装为 completed；
8. 绕过 Gate 执行阶段晋级；
9. 绕过 Trace / Audit 执行关键动作；
10. 在长期 API / DB / 路由中固化 V26.1。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 对象边界

本文覆盖三个核心对象：

```text
Project：用户可见主对象；
Run：Project 内部一次推进过程；
Stage：P0-P6 中某个启用阶段的状态、计划、产物、证据和 Gate 汇总。
```

三者关系建议：

```text
Project 1 ── n Run
Run 1 ── n Stage State
Stage State 1 ── n Artifact / Evidence / Gate / Trace / Audit
Run 1 ── n Stage Plan / Task Plan / TaskGraph
```

边界说明：

```text
1. Project 不等于 Run；
2. Run 不等于 Mission（D-070）；
3. Stage 不等于 R 阶段；
4. P0-P6 是产品流程阶段；
5. R0-RN 是平台自身建设阶段，不进入用户项目 API 阶段字段。
```

---

## 2. API 路由归口建议

R1 建议路由按能力归口：

```text
/api/projects
/api/projects/{project_id}
/api/projects/{project_id}/workspace
/api/projects/{project_id}/runs
/api/projects/{project_id}/runs/{run_id}
/api/projects/{project_id}/runs/{run_id}/stages
/api/projects/{project_id}/runs/{run_id}/stages/{stage}
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/gate
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/artifacts
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/evidence
/api/projects/{project_id}/runs/{run_id}/trace
/api/projects/{project_id}/runs/{run_id}/audit
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

## 3. Project API 总览

Project API 用于管理用户可见项目。

建议能力：

```text
创建 Project；
查询 Project；
更新 Project 元数据；
列出 Project；
归档 Project；
查询 Project Workspace；
查询 Project 当前 Run；
查询 Project 阶段状态汇总；
查询 Project Artifact / Evidence / Trace / Audit 索引。
```

Project API 不负责：

```text
1. 执行 P 阶段晋级；
2. 直接执行 Tool / MCP；
3. 直接批准 Gate；
4. 直接验证 Evidence；
5. 直接保存密钥明文。
```

---

## 4. Project 创建建议契约

建议端点：

```text
POST /api/projects
```

请求字段建议：

```text
name：string，必填，项目名称，来源为用户输入，R2待校准；
description：string，可选，项目描述，来源为用户输入，R2待校准；
source_type：string，可选，项目来源类型，可取 local_dir / git / zip / github / manual，R2待校准；
source_ref：string，可选，来源引用，不得包含明文凭据，R2待校准；
initial_stage_policy：object，可选，阶段启用 / 裁剪建议，不得伪装 completed，R2待校准；
metadata：object，可选，扩展元数据，不得包含密钥，R2待校准。
```

> 关联决策：D-058（source_type 枚举）

响应字段建议：

```text
request_id；
status；
data.project_id；
data.name；
data.description；
data.project_status；
data.workspace_ref；
data.current_stage；
data.created_at；
trace_ref；
warnings；
next_actions。
```

规则：

```text
1. Project 创建成功不等于 P0 完成；
2. Project 创建成功应建立或关联 Project Workspace；
3. source_ref 不得包含凭据明文；
4. 如 source_type 需要后续接入流程，应返回 next_actions；
5. 创建失败必须返回 error_code 和 trace_ref。
```

---

## 5. Project 查询建议契约

建议端点：

```text
GET /api/projects/{project_id}
```

响应字段建议：

```text
project_id；
name；
description；
project_status；
source_type；
source_ref；
workspace_ref；
current_run_id；
current_stage；
stage_summary；
artifact_summary；
evidence_summary；
active_gate；
created_at；
updated_at；
archived_at；
trace_ref。
```

规则：

```text
1. stage_summary 应只汇总当前后端事实；
2. active_gate 应明确是否阻塞；
3. evidence_summary 不得掩盖 Evidence 缺口；
4. workspace_ref 是引用（遵守 01-字段规范 §7），不复制 workspace 内容；
5. source_ref 如敏感必须脱敏（遵守 01-字段规范 §8）。
```

---

## 6. Project 更新与归档建议契约

建议端点：

```text
PATCH /api/projects/{project_id}
POST /api/projects/{project_id}/archive
```

更新请求字段建议：

```text
name；
description；
metadata；
archive_reason；
trace_reason。
```

规则：

```text
1. 更新 Project 元数据不得改变 P 阶段状态；
2. 归档 Project 不得删除 Evidence / Trace / Audit；
3. 归档中的 active Run 需明确处理策略；
4. 高风险或不可逆归档策略需 Gate；
5. 归档响应必须返回 trace_ref。
```

---

## 7. Project 列表建议契约

建议端点：

```text
GET /api/projects
```

查询参数建议（遵守 01-字段规范 §9）：

```text
page；
page_size；
next_cursor；
status_filter；
stage_filter；
source_type_filter；
created_after；
created_before；
updated_after；
updated_before；
sort_by；
sort_order。
```

响应字段建议：

```text
items；
items[].project_id；
items[].name；
items[].project_status；
items[].current_stage；
items[].active_gate；
items[].updated_at；
page_info；
trace_ref。
```

规则：

```text
1. 列表 API 不应返回大段 Artifact / Evidence 正文；
2. active_gate 可用于前端展示阻塞提醒；
3. total_count 如性能成本高可选；
4. 列表不得返回密钥或敏感来源明文。
```

---

## 8. Project 状态建议

Project 状态建议（遵守 01-字段规范 §5）：

```text
draft：项目草案；
created：已创建；
importing：正在接入；
ready：可启动 Run；
running：存在运行中 Run；
waiting_gate：存在阻塞 Gate；
blocked：项目级阻塞；
completed：项目当前确认范围完成；
archived：已归档；
failed：项目级失败。
```

规则：

```text
1. Project completed 不等于所有增强能力完成；
2. Project completed 必须对应当前确认范围；
3. waiting_gate 必须有关联 active_gate；
4. failed 必须有 error_ref；
5. Project 状态由后端事实源计算或持久化，不由前端决定。
```

---

## 9. Run API 总览

Run API 用于管理 Project 内的一次推进过程。

建议能力：

```text
创建 Run；
查询 Run；
启动 Run；
暂停 Run；
取消 Run；
恢复 Run；
查询 Run 状态；
查询 active Gate；
查询 Run 阶段状态；
查询 Run Artifact / Evidence / Trace / Audit；
查询后台任务状态。
```

Run API 不负责：

```text
1. 复活 Mission 产品层；
2. 替代 TaskGraph；
3. 替代 Gate；
4. 替代 Evidence 验证；
5. 直接执行外部系统写操作。
```

---

## 10. Run 创建建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs
```

请求字段建议：

```text
run_goal：string，必填或可选待 R2 校准，Run 目标，不是 Mission；
objective：string，可选，目标说明；
execution_mode：string，可选，manual / plan / auto；
enabled_stages：array，可选，启用阶段；
stage_scope：object，可选，阶段裁剪和范围；
initial_context_refs：array，可选，初始上下文引用；
metadata：object，可选，不得包含密钥。
```

响应字段建议：

```text
request_id；
status；
data.run_id；
data.project_id；
data.run_goal；
data.execution_mode；
data.run_status；
data.current_stage；
data.enabled_stages；
data.created_at；
trace_ref；
next_actions。
```

规则：

```text
1. run_goal 不得被命名为 mission_id（D-054）；
2. 创建 Run 不等于启动执行；
3. enabled_stages 不得把裁剪阶段伪装 completed；
4. execution_mode 不改变 P 阶段晋级必须用户授权；
5. 创建 Run 应记录 Trace。
```

---

## 11. Run 查询建议契约

建议端点：

```text
GET /api/projects/{project_id}/runs/{run_id}
```

响应字段建议：

```text
run_id；
project_id；
run_goal；
objective；
execution_mode；
run_status；
current_stage；
stage_states；
active_gate；
can_pause；
can_cancel；
can_resume；
checkpoint_ref；
recent_trace_refs；
blocking_issues；
started_at；
updated_at；
completed_at。
```

规则：

```text
1. can_* 仅表示当前上下文下是否允许动作，不保证动作成功（遵守 01-字段规范 §10）；
2. active_gate 阻塞时不得继续执行相关动作；
3. checkpoint_ref 不得由前端伪造；
4. recent_trace_refs 只传引用（遵守 01-字段规范 §7）；
5. Run 查询不得返回密钥或 env 明文。
```

---

## 12. Run 启动建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/start
```

请求字段建议：

```text
start_stage：string，可选，默认按 Run 当前阶段；
start_reason：string，可选；
context_refs：array，可选；
requested_by：string，可选，R2 校准身份来源。
```

响应字段建议：

```text
run_id；
project_id；
run_status；
current_stage；
checkpoint_ref；
event_stream_ref；
trace_ref；
next_actions。
```

规则：

```text
1. 启动前必须检查 Run 状态；
2. 启动前必须检查 active Gate；
3. 启动前必须检查 Workspace 可用；
4. 启动后状态变化必须可通过事件或查询恢复；
5. 启动失败不得伪装 running。
```

---

## 13. Run 暂停、取消、恢复建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/pause
POST /api/projects/{project_id}/runs/{run_id}/cancel
POST /api/projects/{project_id}/runs/{run_id}/resume
```

请求字段建议：

```text
reason；
checkpoint_ref；
gate_ref；
resume_target；
force：boolean，默认 false，R2 校准是否允许；
requested_by。
```

响应字段建议：

```text
run_id；
run_status；
checkpoint_ref；
active_gate；
can_resume；
trace_ref；
audit_ref；
next_actions。
```

规则：

```text
1. pause 不等于 cancel；
2. cancel 是否高风险需按上下文判断；
3. resume 必须校验 checkpoint、Policy、Workspace、Trace / Audit；
4. Gate 未决策不得 resume 阻塞动作；
5. 强制恢复如存在，应属于高风险策略，需 Gate / Audit。
```

---

## 14. Run 状态建议

Run 状态建议（遵守 01-字段规范 §5）：

```text
created：已创建；
ready：可启动；
running：运行中；
paused：已暂停；
waiting_gate：等待 Gate；
blocked：被阻塞；
rework_required：需要返工；
failed：失败；
canceling：取消中；
canceled：已取消；
completed：当前 Run 完成；
archived：已归档。
```

规则：

```text
1. completed 必须满足 Run 完成条件；
2. waiting_gate 必须有关联 gate_ref；
3. blocked 必须有 blocking_reason；
4. failed 必须有 error_ref；
5. rework_required 必须说明返工原因；
6. Run 状态恢复依赖多源，不依赖前端 UI。
```

---

## 15. 阶段 API 总览

阶段 API 用于表达 P0-P6 阶段状态、阶段计划、阶段 Artifact / Evidence、阶段 Gate 和阶段晋级。

建议能力：

```text
查询阶段列表；
查询单阶段详情；
查询阶段状态；
提交 Stage Plan；
查询阶段 Artifact；
查询阶段 Evidence；
请求阶段晋级 Gate；
提交阶段晋级决策；
查询阶段 Trace / Audit。
```

阶段 API 不负责：

```text
1. 直接执行 TaskGraph 节点；
2. 替代 Task Plan；
3. 替代 Evidence 验证；
4. 绕过 Gate；
5. 将裁剪阶段标记 completed。
```

---

## 16. 阶段列表建议契约

建议端点：

```text
GET /api/projects/{project_id}/runs/{run_id}/stages
```

响应字段建议：

```text
project_id；
run_id；
stages；
stages[].stage；
stages[].stage_name；
stages[].enabled；
stages[].stage_status；
stages[].stage_plan_ref；
stages[].artifact_refs；
stages[].evidence_refs；
stages[].active_gate；
stages[].updated_at；
trace_ref。
```

规则：

```text
1. enabled=false 的阶段不得显示为 completed；
2. 裁剪阶段应显示 skipped / not_enabled / not_applicable 等 R2 校准状态；
3. active_gate 用于展示阻塞；
4. artifact_refs / evidence_refs 只传引用（遵守 01-字段规范 §7）；
5. 前端不得自行推断阶段完成。
```

---

## 17. 单阶段查询建议契约

建议端点：

```text
GET /api/projects/{project_id}/runs/{run_id}/stages/{stage}
```

响应字段建议：

```text
project_id；
run_id；
stage；
stage_name；
enabled；
stage_status；
stage_objective；
stage_plan_ref；
task_plan_refs；
task_graph_ref；
artifact_refs；
evidence_refs；
evidence_gap_refs；
active_gate；
completion_criteria；
acceptance_result_ref；
trace_refs；
audit_refs；
updated_at。
```

规则：

```text
1. stage 必须是 P0-P6 中的合法阶段；
2. stage_status 不得由 Artifact 数量自动推断；
3. Evidence 缺口必须显式展示；
4. P5 阶段不得以"看起来完成"通过；
5. active_gate 存在时应阻止不允许动作。
```

---

## 18. 阶段状态建议

阶段状态建议（遵守 01-字段规范 §5）：

```text
not_enabled：未启用 / 被裁剪；
not_started：未开始；
planning：规划中；
waiting_plan_review：等待计划审核；
ready：可执行；
running：执行中；
waiting_gate：等待 Gate；
blocked：阻塞；
rework_required：需要返工；
validation_required：需要验证；
failed：失败；
completed：阶段完成；
accepted：阶段已接受，R2 校准是否需要与 completed 合并。
```

规则：

```text
1. not_enabled 不得等同 completed；
2. completed 必须满足阶段完成条件；
3. P 阶段晋级必须用户 Gate；
4. blocked 必须有原因；
5. failed 必须有 error_ref；
6. validation_required 对 P5 等验证相关阶段尤其重要。
```

---

## 19. Stage Plan 提交建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/stages/{stage}/stage-plan
```

请求字段建议：

```text
stage_objective；
scope；
out_of_scope；
execution_mode；
required_artifacts；
required_evidence；
risk_level；
permission_boundary；
acceptance_criteria；
expected_task_plan_refs；
trace_reason。
```

响应字段建议：

```text
stage_plan_id；
project_id；
run_id；
stage；
status；
review_required；
gate_ref；
trace_ref；
next_actions。
```

规则：

```text
1. Stage Plan 审核不等于阶段晋级授权；
2. Plan Mode 下 Stage Plan / Task Plan 是任务边界授权基础；
3. Auto Mode 下 Stage Plan 可由 Agent 审核，但阶段晋级仍需用户授权；
4. 高风险计划需 Gate 或用户确认；
5. Stage Plan 不得隐含范围外任务。
```

---

## 20. 阶段晋级 Gate 建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/stages/{stage}/promotion-gate
POST /api/projects/{project_id}/runs/{run_id}/stages/{stage}/promotion-decision
```

Gate 创建请求字段建议：

```text
reason；
current_stage_status；
artifact_refs；
evidence_refs；
evidence_gap_refs；
risk_notes；
recommended_next_stage；
trace_ref。
```

决策请求字段建议：

```text
gate_ref；
decision；
decision_reason；
accepted_risks；
next_stage；
requested_by。
```

规则：

```text
1. P 阶段晋级 Gate 必须用户授权；
2. Gate 决策必须 Audit；
3. Evidence 不足时不得默认通过；
4. 用户接受风险也必须记录 accepted_risks；
5. Gate 被拒绝应进入 rework_required / blocked / failed / canceled 等明确状态。
```

---

## 21. Artifact / Evidence 关联建议契约

建议端点：

```text
GET /api/projects/{project_id}/runs/{run_id}/stages/{stage}/artifacts
GET /api/projects/{project_id}/runs/{run_id}/stages/{stage}/evidence
```

响应字段建议：

```text
stage；
artifact_refs；
evidence_refs；
evidence_gap_refs；
claim_refs；
validation_summary；
trace_ref。
```

规则：

```text
1. Artifact 不自动成为 Evidence；
2. Evidence 必须支撑明确 claim；
3. Evidence 缺失不得伪装 completed；
4. P5 验证必须基于可验证 Evidence；
5. API 只传引用和必要摘要，避免复制成第二事实源（D-068）。
```

---

## 22. Project / Run / 阶段错误响应

建议错误类型（遵守 01-字段规范 §12 通用错误码，以下为域特定补充）：

```text
project_not_found；
run_not_found；
stage_not_found；
invalid_stage；
stage_not_enabled；
state_mismatch；
gate_required；
gate_not_resolved；
policy_blocked；
evidence_missing；
trace_missing；
audit_missing；
checkpoint_missing；
workspace_unavailable；
permission_denied；
validation_error；
internal_error。
```

规则：

```text
1. stage_not_enabled 不得返回 completed；
2. gate_required 必须返回 gate_ref；
3. evidence_missing 必须返回 evidence_gap_refs 或 required_evidence；
4. checkpoint_missing 不得盲目 resume；
5. workspace_unavailable 不得伪装 ready；
6. 错误响应不得泄露密钥。
```

---

## 23. Project / Run / 阶段事件建议

建议事件：

```text
project_created；
project_updated；
project_archived；
run_created；
run_started；
run_paused；
run_resumed；
run_canceled；
run_completed；
stage_status_changed；
stage_plan_submitted；
stage_gate_created；
stage_gate_decided；
stage_completed；
stage_rework_required；
evidence_gap_detected。
```

事件字段建议（遵守 00-API与集成契约总览.md §6 通用事件字段）：

```text
event_id；
event_type；
project_id；
run_id；
stage；
payload；
trace_ref；
audit_ref；
created_at。
```

规则：

```text
1. 事件不是唯一状态源；
2. 前端断线重连后必须能查询后端状态；
3. Gate 事件必须可审计；
4. 事件 payload 不得包含密钥；
5. 关键状态变化必须可追踪。
```

---

## 24. 前端联调边界

前端可展示：

```text
Project 列表；
Project 当前阶段；
Project Workspace 入口；
Run 状态；
Run 后台任务状态；
P0-P6 阶段列表；
阶段完成条件；
Artifact / Evidence 引用；
Evidence 缺口；
Gate 阻塞；
Trace / Audit 引用。
```

前端必须标记：

```text
mock 数据；
未接真实服务能力；
裁剪阶段；
等待 Gate；
Evidence 缺失；
Trace / Audit 缺失；
高风险动作。
```

前端不得：

```text
1. 伪造 Project / Run / Stage 后端状态；
2. 把 not_enabled 阶段显示成 completed；
3. 隐藏 Gate；
4. 隐藏 Evidence 缺口；
5. 用 toast 替代 Gate 决策面板；
6. 展示明文密钥。
```

---

## 25. R2 / R4 / R9-R12 校准项

### 25.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. 是否明确所有字段均为 R1 建议契约；
3. 是否仍有 mission_id / Mission 产品层残留；
4. Project / Run / Stage 状态是否与流程文档一致；
5. Gate / Evidence / Trace / Audit 引用是否完整；
6. 是否重复上级事实，需要改为引用；
7. 错误码是否与 01-字段规范一致。
```

### 25.2 R4 工程骨架校准

```text
1. FastAPI 路由结构；
2. Pydantic schema；
3. Project / Run / Stage 数据模型；
4. LangGraph checkpoint / interrupt / resume 接口；
5. Trace / Audit Writer；
6. Gate 状态和错误映射；
7. SSE 事件实现。
```

### 25.3 R9 P0-P1 最小真实链路校准

```text
1. Project 创建是否可用；
2. Project Workspace 是否可查询；
3. P0 / P1 阶段状态是否可查询；
4. Project 接入结果是否能进入 Stage Artifact / Evidence 候选；
5. 前端是否不再依赖纯 mock。
```

### 25.4 R10 P2-P3 评估与规划链路校准

```text
1. P2 / P3 阶段状态；
2. Stage Plan API；
3. Task Plan / TaskGraph 引用；
4. 风险与权限边界；
5. Gate 与计划审核联动。
```

### 25.5 R11 P4 执行链路校准

```text
1. P4 阶段状态；
2. Execution Session 引用；
3. Patch / Tool / MCP Artifact 引用；
4. 高风险动作 Gate；
5. 执行失败错误响应。
```

### 25.6 R12 P5-P6 验证与交付链路校准

```text
1. P5 / P6 阶段状态；
2. Evidence 缺口；
3. 验证结果引用；
4. 交付 Artifact / Evidence / Trace / Audit 链路；
5. P5 不得缺 Evidence completed。
```

---

## 26. Project / Run / 阶段 API 红线

```text
1. 不得引入 Mission 产品层（D-070）；
2. 不得保留 mission_id，除非 R2 明确校准为非产品层语义并说明原因（D-054）；
3. 不得把 Project 创建等同于 P0 完成；
4. 不得把 Run 创建等同于执行启动；
5. 不得把裁剪阶段标记为 completed；
6. 不得绕过用户阶段晋级 Gate（D-023）；
7. 不得用前端状态替代后端状态；
8. 不得用容器、终端或前端 UI 作为状态源；
9. 不得无 Trace 更新关键状态；
10. 不得无 Audit 记录 Gate 决策或高风险动作；
11. 不得把 Evidence 缺失伪装为 completed；
12. 不得在 API 响应、错误、事件、Trace、Audit 中泄露 Key / Token / Secret / Password（D-032）；
13. 不得把 R1 建议契约伪装为实现契约；
14. 不得在长期 API / DB / 路由中固化 V26.1。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 27. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确对象边界；
2. 明确 API 路由归口建议；
3. 明确 Project API 总览、创建、查询、更新、归档、列表和状态建议；
4. 明确 Run API 总览、创建、查询、启动、暂停、取消、恢复和状态建议；
5. 明确阶段 API 总览、列表、单阶段查询、状态、Stage Plan、晋级 Gate、Artifact / Evidence 关联；
6. 明确 Project / Run / 阶段错误响应；
7. 明确 Project / Run / 阶段事件建议；
8. 明确前端联调边界；
9. 明确 R2 / R4 / R9-R12 校准项；
10. 明确 Project / Run / 阶段 API 红线；
11. 未新增产品决策；
12. 未引入 Mission 产品层；
13. 未固定最终实现 schema；
14. 决策交叉引用完整（D-016/D-054/D-058/D-070）；
15. 待确认项显式列出。
```

---

## 28. 待确认项

以下事项需用户或 R2 审核确认：

```text
1. ⚠️ 命名与范围差异：本文档覆盖 Project+Run+Stage 三合一，但 00-API与集成契约总览.md §22 规划为两份文档（01-Project+项目接入、02-Run+Stage+TaskGraph）。
   差异：① 本文不含项目接入 API（本地目录/Git/ZIP/GitHub 接入端点未展开）；② 本文不含 TaskGraph API（节点/边策略端点未展开）；③ 本文将 Project+Run+Stage 三合一而非二拆分。
   建议：本文维持三合一结构（三者深度关联，拆分会导致交叉引用断裂），项目接入和 TaskGraph 各自独立成文。R2 更新 00 总览 §22 以反映实际文档结构。

2. §8 Project 状态 accepted 是否需要与 completed 区分——当前 10 种状态是否过细。

3. §18 阶段状态 accepted 是否需要与 completed 合并——草稿自身已标注"R2 校准是否需要合并"。

4. §10 run_goal 的必填性——当前标记为"必填或可选待 R2 校准"，需 R2 根据实际业务确定。

5. §2 路由设计中 /api/projects/{project_id}/runs/{run_id}/stages/{stage}/gate 的 Gate 是阶段晋级专用，通用 Gate API（跨阶段/跨对象）是否需要独立端点。
```
