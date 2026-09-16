# 04-Artifact-Evidence-Trace-Audit API建议契约

> 文档路径：`文档/05-API与集成契约/04-Artifact-Evidence-Trace-Audit API建议契约.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/已完成/R1/04-Artifact-Evidence-Trace-Audit%20API建议契约.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本中 Artifact、Evidence、Trace、Audit（简称 AET）相关 API 的 R1 建议契约，包括对象边界、端点归口、字段建议、状态建议、claim 与证据关系、Trace/Audit 写入与查询、Gate/Policy/Run/Stage/TaskGraph 关联、错误响应、事件建议、前端联调边界与 R2/R4/R9-R12 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-016, D-066, D-068）、`文档/05-API与集成契约/00-API与集成契约总览.md`（§16）、`文档/05-API与集成契约/01-字段规范与错误响应规范.md`（字段/错误码详述源）、`文档/05-API与集成契约/02-Project-Run-阶段API建议契约.md`、`文档/05-API与集成契约/03-TaskPlan-TaskGraph API建议契约.md`、`文档/08-测试与验收/02-迁移正确性与验证策略.md`（D-066）。
> 重要边界：本文是 Artifact / Evidence / Trace / Audit API 的 R1 建议契约，不替代项目治理决策、最终 OpenAPI 文档、后端路由实现、数据库 schema、文件存储实现、安全权限规范、测试验收规范或运行时代码实现。本文所有端点、字段、状态和错误码均为 R1 建议契约，R2/R4/R9-R12 需按真实实现校准为实现契约。
> 修订说明：R1 正式化版基于草稿 v0.1：(1) 头部标准化；(2) §0 新增关联决策速查表 + 字段规范引用声明；(3) 关键节加注决策引用；(4) 新增 §27 待确认项（4 项）；(5) 验收标准扩展（20→22 项）。R2 校准（Q1=A/）：陈旧目录路径回写为 10 目录真值；迁移正确性统一 08-测试与验收/02-。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 Artifact / Evidence / Trace / Audit API 建议契约，不重新定义上级事实。

**关联决策速查**：

| 决策编号 | 内容 | 本文相关节 |
|---|---|---|
| D-016 | API 字段 R1 输出建议契约，R2 校准为实现契约 | 全文 |
| D-066 | 迁移正确性与验证策略——P5 验证须产出可验证证据 | §20 |
| D-068 | 文档事实源层级与引用规则（单一详述源，AET 引用优先） | §0, §18 |

**字段与错误码规范引用**：本文所有字段命名、元数据标注、状态字段、引用字段、请求响应结构和错误码均遵守 `01-字段规范与错误响应规范.md`。上游对象关联遵守 `02-Project-Run-阶段API建议契约.md` 和 `03-TaskPlan-TaskGraph API建议契约.md`。

本文必须遵守：

```text
1. API 字段 R1 输出建议契约，R2 校准为实现契约（D-016）；
2. 当前版本文档体系必须区分项目文档、交接材料、产物、证据、运行记录、参考资料；
3. Artifact 不自动晋升为项目文档，必须经过 Gate 或验收流程（D-014）；
4. Evidence 必须支撑明确 claim；
5. P5 验证必须基于可验证 Evidence（D-066）；
6. 验证不通过或证据不足时，不得标记 completed；
7. No Evidence / No Trace, No Trusted Result；
8. Gate 决策、高风险动作、Policy 冲突、外部系统写操作必须可 Trace / Audit；
9. Project Workspace 中的文件、产物、Evidence、runs、任务状态按 project_id 隔离；
10. 状态恢复依赖数据库、LangGraph checkpoint、workspace 文件、trace / audit、event log；
11. 容器、终端或前端 UI 不能作为状态源；
12. API / 响应 / 错误 / Trace / Audit / 事件不得泄露 Key / Token / Secret / Password（D-032）；
13. 当前版本不设独立 Mission 产品层（D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 将 Artifact 自动标记为 Evidence；
2. 将 Evidence 候选自动标记为 Evidence validated；
3. 将模型输出、资源输出、Case 或 Knowledge 直接作为验证通过依据；
4. 将 Evidence 缺失伪装为 completed；
5. 用 Trace 替代 Evidence；
6. 用 Audit 替代 Gate；
7. 用前端状态替代 AET 后端事实源；
8. 在 Trace / Audit / Error / SSE 中记录密钥明文（D-032）；
9. 将 R1 建议字段伪装为实现契约；
10. 引入 Mission 产品层或 mission_id 长期字段（D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. AET 对象边界

本文覆盖四个核心对象：

```text
Artifact：任务或阶段产生的产物；
Evidence：用于证明某个 claim 的证据；
Trace：记录过程发生过什么；
Audit：记录高风险、授权、策略冲突、Gate 决策等审计事件。
```

四者关系建议：

```text
Artifact 1 ── 0..n Evidence Candidate
Evidence 1 ── 1..n Claim
Trace 1 ── n Action / State Change / Resource Call / Model Call
Audit 1 ── n Gate Decision / High Risk Action / Policy Conflict
Project / Run / Stage / Node ── n Artifact / Evidence / Trace / Audit refs
```

边界说明：

```text
1. Artifact 是产物，不等于证据；
2. Evidence 是支撑 claim 的证据，不等于普通文件；
3. Trace 是过程记录，不等于验收结论；
4. Audit 是审计记录，不等于用户授权本身；
5. AET 通过引用连接 Project / Run / Stage / TaskGraph / Node / Gate（遵守 01-字段规范 §7）。
```

---

## 2. API 路由归口建议

R1 建议路由按能力归口：

```text
/api/projects/{project_id}/artifacts
/api/projects/{project_id}/artifacts/{artifact_id}
/api/projects/{project_id}/evidence
/api/projects/{project_id}/evidence/{evidence_id}
/api/projects/{project_id}/claims
/api/projects/{project_id}/claims/{claim_id}
/api/projects/{project_id}/trace
/api/projects/{project_id}/trace/{trace_id}
/api/projects/{project_id}/audit
/api/projects/{project_id}/audit/{audit_id}
/api/projects/{project_id}/runs/{run_id}/artifacts
/api/projects/{project_id}/runs/{run_id}/evidence
/api/projects/{project_id}/runs/{run_id}/trace
/api/projects/{project_id}/runs/{run_id}/audit
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/artifacts
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/evidence
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/trace
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/audit
```

说明：

```text
1. 以上为 R1 建议路由；
2. R4 需根据 FastAPI 实际路由结构校准；
3. 路由不应包含 V26.1；
4. 路由不应包含 Mission 产品层；
5. 如果实际采用扁平路由，也必须保持 AET 对象语义一致。
```

---

## 3. Artifact API 总览

Artifact API 用于记录、查询、更新、归档和关联任务产物。

建议能力：

```text
创建 Artifact 记录；
查询 Artifact；
列出 Artifact；
更新 Artifact 元数据；
标记 Artifact 状态；
关联 Artifact 到 Project / Run / Stage / Node；
提交 Artifact 为 Evidence 候选；
归档 Artifact；
查询 Artifact Trace。
```

Artifact API 不负责：

```text
1. 自动晋升项目文档；
2. 自动验证 Evidence；
3. 自动完成阶段；
4. 自动批准 Gate；
5. 保存密钥明文。
```

---

## 4. Artifact 创建建议契约

建议端点：

```text
POST /api/projects/{project_id}/artifacts
```

请求字段建议：

```text
run_id：string，可选，关联 Run；
stage：string，可选，关联 P0-P6 阶段；
task_graph_id：string，可选，关联 TaskGraph；
node_id：string，可选，关联 Node；
artifact_type：string，必填，产物类型；
material_type：string，可选，材料身份；
title：string，必填，产物标题；
description：string，可选，产物说明；
source_ref：string，可选，来源引用；
workspace_ref：string，可选，Workspace 文件引用；
content_ref：string，可选，内容引用；
claim_refs：array，可选，关联 claim；
trace_ref：string，可选，创建过程 Trace；
metadata：object，可选，不得包含密钥。
```

响应字段建议：

```text
request_id；
status；
data.artifact_id；
data.project_id；
data.run_id；
data.stage；
data.artifact_type；
data.artifact_status；
data.workspace_ref；
data.content_ref；
data.created_at；
trace_ref；
warnings；
next_actions。
```

规则：

```text
1. Artifact 创建成功不等于 Evidence validated；
2. Artifact 创建成功不等于项目文档晋升；
3. Artifact 必须保留来源引用；
4. Artifact 不得携带密钥明文；
5. Artifact 如用于 Evidence，应走 Evidence 候选或验证流程。
```

---

## 5. Artifact 状态建议

```text
draft：草案；
generated：已生成；
under_review：待审核；
accepted：已接受；
rejected：已拒绝；
evidence_candidate：证据候选；
promoted_to_document：已晋升为项目文档；
superseded：已被替代；
archived：已归档；
invalid：无效。
```

规则：

```text
1. promoted_to_document 必须经过 Gate 或验收流程；
2. evidence_candidate 不等于 Evidence validated；
3. superseded 必须保留替代关系；
4. rejected 必须有 reject_reason；
5. 状态变化必须 Trace；
6. 高风险产物晋升应 Audit。
```

---

## 6. Evidence API 总览

Evidence API 用于提交、查询、验证和关联证据。

建议能力：

```text
提交 Evidence 候选；
查询 Evidence；
列出 Evidence；
验证 Evidence；
关联 Evidence 与 claim；
查询 Evidence 缺口；
标记 Evidence 不足；
归档 Evidence；
查询 Evidence Trace / Audit。
```

Evidence API 不负责：

```text
1. 自动生成 claim；
2. 自动批准阶段完成；
3. 自动替代 P5 验证；
4. 自动替代用户 Gate；
5. 保存敏感明文。
```

---

## 7. Evidence 提交建议契约

建议端点：

```text
POST /api/projects/{project_id}/evidence
```

请求字段建议：

```text
run_id：string，可选；
stage：string，可选；
task_graph_id：string，可选；
node_id：string，可选；
claim_refs：array，必填，证据支撑的 claim；
evidence_type：string，必填，证据类型；
source_artifact_refs：array，可选，来源 Artifact；
source_trace_refs：array，可选，来源 Trace；
source_ref：string，可选，其他来源；
validation_method：string，可选，验证方法；
validation_result_ref：string，可选，验证结果引用；
confidence_note：string，可选，置信说明；
limitations：array，可选，限制说明；
metadata：object，可选，不得包含密钥。
```

响应字段建议：

```text
request_id；
status；
data.evidence_id；
data.evidence_status；
data.claim_refs；
data.validation_status；
data.created_at；
trace_ref；
warnings；
next_actions。
```

规则：

```text
1. Evidence 提交默认是候选或待验证；
2. Evidence 必须支撑明确 claim；
3. Evidence 必须保留来源；
4. Evidence 缺少验证方法时不得 validated；
5. Evidence 不得包含未脱敏敏感信息。
```

---

## 8. Evidence 状态建议

Evidence 状态建议：

```text
candidate：候选；
submitted：已提交；
under_validation：验证中；
validated：已验证；
insufficient：证据不足；
rejected：已拒绝；
superseded：已被替代；
archived：已归档；
invalid：无效。
```

验证状态建议：

```text
not_validated；
validation_pending；
validation_passed；
validation_failed；
validation_blocked；
validation_not_applicable。
```

规则：

```text
1. candidate / submitted 不得用于 completed；
2. validated 必须有验证方法和验证结果；
3. insufficient 必须有 evidence_gap_ref；
4. rejected 必须有 reject_reason；
5. validation_failed 不得伪装 validation_passed；
6. Evidence 状态变化必须 Trace。
```

---

## 9. Claim API 建议契约

Claim 是 Evidence 支撑的明确主张。

建议端点：

```text
POST /api/projects/{project_id}/claims
GET /api/projects/{project_id}/claims/{claim_id}
GET /api/projects/{project_id}/claims/{claim_id}/evidence
```

Claim 字段建议：

```text
claim_id；
project_id；
run_id；
stage；
task_graph_id；
node_id；
claim_type；
claim_text；
claim_scope；
required_evidence_types；
evidence_refs；
evidence_gap_refs；
claim_status；
created_by；
created_at；
updated_at；
trace_ref。
```

Claim 状态建议：

```text
draft；
active；
partially_supported；
supported；
unsupported；
superseded；
archived。
```

规则：

```text
1. claim_text 必须明确可验证；
2. supported 必须有 validated Evidence；
3. unsupported 不得用于完成条件；
4. claim 变更必须 Trace；
5. 关键 claim 变更可能需要 Audit。
```

---

## 10. Evidence 缺口 API 建议契约

Evidence 缺口用于标记当前证据不足。

建议端点：

```text
GET /api/projects/{project_id}/evidence-gaps
POST /api/projects/{project_id}/evidence-gaps
PATCH /api/projects/{project_id}/evidence-gaps/{evidence_gap_id}
```

字段建议：

```text
evidence_gap_id；
project_id；
run_id；
stage；
claim_id；
required_evidence_type；
gap_description；
blocking；
severity；
resolution_plan_ref；
status；
created_at；
updated_at；
trace_ref。
```

规则：

```text
1. blocking=true 时不得 completed；
2. Evidence 缺口关闭必须有补充证据或确认依据；
3. P5 Evidence 缺口不得被静默忽略（D-066）；
4. Evidence 缺口进入前端必须清晰展示；
5. 缺口状态变化必须 Trace。
```

---

## 11. Trace API 总览

Trace API 用于记录和查询过程。

建议能力：

```text
写入 Trace；
查询 Trace；
按 Project / Run / Stage / Node 查询 Trace；
查询模型调用 Trace；
查询资源调用 Trace；
查询 Gate Trace；
查询状态变化 Trace；
导出 Trace 摘要。
```

Trace API 不负责：

```text
1. 替代 Evidence；
2. 替代 Audit；
3. 替代 Gate；
4. 保存敏感明文；
5. 将过程记录自动转换为完成结论。
```

---

## 12. Trace 写入建议契约

建议端点：

```text
POST /api/projects/{project_id}/trace
```

请求字段建议：

```text
run_id；
stage；
task_graph_id；
node_id；
trace_type；
action；
actor_type；
actor_ref；
input_refs；
output_refs；
artifact_refs；
evidence_refs；
resource_call_ref；
model_call_ref；
gate_ref；
status_before；
status_after；
message；
metadata；
created_at。
```

响应字段建议：

```text
trace_id；
project_id；
run_id；
stage；
trace_type；
created_at；
warnings。
```

规则：

```text
1. Trace 写入不得包含密钥明文；
2. 关键动作必须 Trace；
3. 状态变化必须 Trace；
4. Trace 写入失败不得被吞掉；
5. Trace 不等于 Evidence validated。
```

---

## 13. Trace 类型建议

Trace 类型建议：

```text
state_change；
model_call；
resource_call；
tool_call；
mcp_call；
execution_action；
workspace_action；
gate_event；
policy_check；
artifact_event；
evidence_event；
acceptance_event；
error_event；
context_assembly；
plan_change。
```

规则：

```text
1. trace_type 必须机器可读；
2. error_event 必须有关联 error_ref 或 error_code；
3. gate_event 必须有关联 gate_ref；
4. policy_check 必须关联 policy_ref 或 policy_check_ref；
5. context_assembly 不得记录敏感明文。
```

---

## 14. Audit API 总览

Audit API 用于记录高风险、授权、策略冲突、Gate 决策和关键裁决。

建议能力：

```text
写入 Audit；
查询 Audit；
按 Project / Run / Stage / Node 查询 Audit；
查询 Gate Audit；
查询 Policy 冲突 Audit；
查询高风险动作 Audit；
导出 Audit 摘要。
```

Audit API 不负责：

```text
1. 自动批准 Gate；
2. 替代用户授权；
3. 替代 Evidence；
4. 保存密钥明文；
5. 将风险接受自动变成验证通过。
```

---

## 15. Audit 写入建议契约

建议端点：

```text
POST /api/projects/{project_id}/audit
```

请求字段建议：

```text
run_id；
stage；
task_graph_id；
node_id；
audit_type；
risk_level；
action；
actor_type；
actor_ref；
gate_ref；
policy_refs；
trace_refs；
artifact_refs；
evidence_refs；
decision；
decision_reason；
accepted_risks；
status；
metadata；
created_at。
```

响应字段建议：

```text
audit_id；
project_id；
run_id；
stage；
audit_type；
risk_level；
created_at；
warnings。
```

规则：

```text
1. Gate 决策必须 Audit；
2. 高风险动作必须 Audit；
3. Policy 冲突必须 Audit 或进入安全审计策略；
4. 外部系统写操作必须 Audit；
5. Audit 不得包含密钥明文；
6. Audit 写入失败不得被忽略。
```

---

## 16. Audit 类型建议

Audit 类型建议：

```text
gate_decision；
high_risk_action；
policy_conflict；
permission_escalation；
external_write；
credential_event；
redaction_event；
manual_override；
risk_acceptance；
stage_promotion；
evidence_exception；
resource_activation；
community_resource_upgrade。
```

规则：

```text
1. audit_type 必须机器可读；
2. high_risk_action 必须有 risk_level；
3. gate_decision 必须有 gate_ref 和 decision；
4. manual_override 必须有 decision_by 或 actor_ref；
5. credential_event 不得包含凭据明文。
```

---

## 17. AET 查询与过滤建议契约

列表查询建议参数（遵守 01-字段规范 §9）：

```text
run_id；
stage；
task_graph_id；
node_id；
artifact_type；
evidence_type；
trace_type；
audit_type；
status_filter；
risk_level_filter；
created_after；
created_before；
next_cursor；
page_size；
sort_by；
sort_order。
```

规则：

```text
1. Trace / Audit 数据量可能较大，优先 cursor 分页；
2. filter 不得支持敏感明文字段；
3. 查询结果默认返回引用和摘要；
4. 明细内容需权限校验；
5. Audit 查询可能需要更高权限。
```

---

## 18. AET 与 Project / Run / Stage / Node 的关系

AET 必须可关联（遵守 01-字段规范 §7 引用优先）：

```text
project_id；
run_id；
stage；
task_graph_id；
node_id；
gate_id；
resource_call_id；
model_call_id；
execution_session_id。
```

规则：

```text
1. project_id 必须存在；
2. run_id / stage / node_id 可按对象上下文可选；
3. P 阶段完成必须能追溯 Artifact / Evidence / Trace / Audit；
4. Run 状态恢复必须能查询相关 Trace / Audit；
5. P6 交付应能索引完整证据链。
```

---

## 19. AET 与 Gate / Policy 的关系

Gate 关联规则：

```text
1. Gate 创建应生成 Trace；
2. Gate 决策必须生成 Audit；
3. Gate resume 应引用 checkpoint_ref、trace_ref、audit_ref；
4. Gate 拒绝应进入 blocked / rework_required / failed / canceled 等状态；
5. Gate 决策不得缺少 actor_ref 或 decision_by。
```

Policy 关联规则：

```text
1. Policy 检查应生成 Trace；
2. Policy 冲突必须生成 Audit 或进入安全审计策略；
3. Policy 禁止项不得由 Agent 判断覆盖；
4. policy_blocked 错误应关联 policy_check_ref；
5. Policy 相关 Trace / Audit 不得泄露敏感信息。
```

---

## 20. AET 与 P5 验证关系

P5 验证至少需要围绕以下方向形成证据链（D-066）：

```text
构建可成功；
运行 / 启动可达；
测试保留与生成；
回归基线对比；
关键行为等价或差异显式登记。
```

P5 API 规则：

```text
1. P5 不得以"看起来完成"通过；
2. P5 completed 必须能查询 validated Evidence；
3. Evidence 缺失必须返回 evidence_missing / evidence_insufficient；
4. 验证失败必须关联 Trace；
5. 风险接受必须进入 Audit；
6. 差异显式登记应形成 Artifact / Evidence / Audit 的可追溯链路。
```

> 关联决策：D-066（迁移正确性与验证策略）

---

## 21. AET 错误响应

建议错误类型（遵守 01-字段规范 §12）：

```text
artifact_not_found；
evidence_not_found；
claim_not_found；
trace_not_found；
audit_not_found；
invalid_artifact_status；
invalid_evidence_status；
evidence_missing；
evidence_insufficient；
trace_missing；
audit_missing；
claim_unsupported；
validation_failed；
validation_blocked；
policy_blocked；
gate_required；
gate_not_resolved；
redaction_required；
permission_denied；
state_mismatch；
validation_error；
internal_error。
```

规则：

```text
1. evidence_missing 不得伪装 completed；
2. trace_missing 不得被吞掉；
3. audit_missing 对高风险动作必须阻断或进入处理流程；
4. redaction_required 不得返回原始敏感内容；
5. validation_failed 不得转成 warning 后继续 completed；
6. 错误响应不得泄露密钥。
```

---

## 22. AET 事件建议

建议事件：

```text
artifact_created；
artifact_status_changed；
artifact_promoted；
evidence_submitted；
evidence_validated；
evidence_rejected；
evidence_gap_created；
evidence_gap_resolved；
trace_written；
audit_written；
gate_audit_written；
policy_conflict_audited；
redaction_required；
claim_supported；
claim_unsupported。
```

事件字段建议（遵守 00-API与集成契约总览.md §6）：

```text
event_id；
event_type；
project_id；
run_id；
stage；
task_graph_id；
node_id；
artifact_id；
evidence_id；
claim_id；
trace_id；
audit_id；
gate_id；
payload；
created_at。
```

规则：

```text
1. 事件不是唯一状态源；
2. 事件 payload 不得包含密钥；
3. 高风险事件应关联 audit_id；
4. Evidence 事件应关联 claim_id；
5. 前端断线重连后必须能通过查询恢复状态。
```

---

## 23. 前端联调边界

前端可展示：

```text
Artifact 列表和状态；
Artifact 预览入口；
Evidence 列表和验证状态；
Evidence 缺口；
Claim 与 Evidence 关系；
Trace 时间线；
Audit 时间线；
Gate 决策审计；
P5 验证证据链；
P6 交付证据链。
```

前端必须标记：

```text
Evidence 候选；
Evidence 已验证；
Evidence 不足；
Trace 缺失；
Audit 缺失；
Gate 阻塞；
高风险 Audit；
只读历史参考；
mock / 未接真实服务能力。
```

前端不得：

```text
1. 把 Artifact 显示成 Evidence validated；
2. 隐藏 Evidence 缺口；
3. 隐藏 Trace / Audit 缺失；
4. 将模型输出直接显示为验证通过；
5. 将 mock AET 数据伪装为真实数据；
6. 展示明文密钥。
```

---

## 24. R2 / R4 / R9-R12 校准项

### 24.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. 是否明确所有字段均为 R1 建议契约；
3. Artifact / Evidence / Trace / Audit 边界是否清晰；
4. Evidence 与 claim 关系是否足够明确；
5. P5 Evidence 要求是否与测试验收文档一致（D-066）；
6. 错误码是否与 01-字段规范一致；
7. 是否仍有 mission_id / Mission 产品层残留；
8. 是否重复上级事实，需要改为引用。
```

### 24.2 R4 工程骨架校准

```text
1. AET FastAPI 路由结构；
2. AET Pydantic schema；
3. AET 数据模型；
4. Workspace 文件引用；
5. Trace / Audit Writer；
6. Gate / Policy / Error 映射；
7. SSE 事件实现；
8. 脱敏 middleware。
```

### 24.3 R9-R12 主链路校准

```text
R9：P0/P1 是否能生成 Artifact 与 Evidence 候选，Trace 是否记录接入过程；
R10：评估报告是否作为 Artifact，风险 claim 是否关联 Evidence，Gate/Audit 是否记录规划决策；
R11：Patch/转换/执行输出是否作为 Artifact，Tool/MCP 调用是否 Trace，高风险执行是否 Audit；
R12：P5 是否能提交/验证/查询 Evidence，Evidence 缺口是否阻断 completed，P6 是否能索引完整证据链。
```

---

## 25. AET API 红线

```text
1. 不得将 Artifact 自动标记为 Evidence；
2. 不得将 Evidence 候选自动标记为 validated；
3. 不得将模型输出、资源输出、Case 或 Knowledge 直接作为验证通过依据；
4. 不得将 Evidence 缺失伪装为 completed；
5. 不得用 Trace 替代 Evidence；
6. 不得用 Audit 替代 Gate；
7. 不得无 Trace 执行关键动作；
8. 不得无 Audit 执行高风险动作或 Gate 决策；
9. 不得隐藏 Trace / Audit 写入失败；
10. 不得用前端状态替代 AET 后端事实源；
11. 不得将 mock AET 数据伪装为真实数据；
12. 不得引入 Mission 产品层（D-070）；
13. 不得在 API 响应、错误、事件、Trace、Audit 中泄露 Key / Token / Secret / Password（D-032）；
14. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 26. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 AET 对象边界；
2. 明确 API 路由归口建议；
3. 明确 Artifact API 总览、创建和状态建议；
4. 明确 Evidence API 总览、提交和状态建议；
5. 明确 Claim API 建议契约；
6. 明确 Evidence 缺口 API 建议契约；
7. 明确 Trace API 总览、写入和类型建议；
8. 明确 Audit API 总览、写入和类型建议；
9. 明确 AET 查询与过滤建议契约；
10. 明确 AET 与 Project / Run / Stage / Node 的关系；
11. 明确 AET 与 Gate / Policy 的关系；
12. 明确 AET 与 P5 验证关系（D-066）；
13. 明确 AET 错误响应；
14. 明确 AET 事件建议；
15. 明确前端联调边界；
16. 明确 R2 / R4 / R9-R12 校准项；
17. 明确 AET API 红线；
18. 未新增产品决策；
19. 未引入 Mission 产品层；
20. 未固定最终实现 schema；
21. 决策交叉引用完整（D-016/D-066/D-068）；
22. 待确认项显式列出。
```

---

## 27. 待确认项

```text
1. §20 P5 验证五维度（构建/运行/测试/回归/等价）是否足够——是否需要补充"依赖兼容性"和"配置文件正确性"作为独立维度。

2. §8 Evidence 验证状态 6 种与 Evidence 主状态 9 种的关系——validation_status 是否应作为 evidence_status 的子状态而非独立字段（当前设计为两套平行枚举）。

3. §13 Trace 类型 15 种与 §16 Audit 类型 13 种——Trace 和 Audit 的写入触发边界是否需要在架构设计文档中统一（而非仅在 API 层定义）。

4. Artifact → Evidence 候选 → Evidence validated 的晋升流程是否需要独立 API 端点（当前由 Artifact 状态标记 evidence_candidate + Evidence 提交两个步骤完成），还是合并为单次调用。
```
