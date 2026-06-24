# 05-Gate-Authorization API建议契约

> 文档路径：`文档/05-API与集成契约/05-Gate-Authorization API建议契约.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.1.1
> 来源草稿：`产物/草稿/05-Gate-Authorization API建议契约.md`（v0.1）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R1 文档正式化流程
> 最后更新时间：2026-06-24（R2 去重：§13 三模式定义收敛为引用详述源 D-025+02术语表）
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本中 Gate、Authorization、Policy Check、Interrupt/Resume、Risk Action 相关 API 的 R1 建议契约，包括对象边界、端点归口、字段建议、状态建议、授权模式、风险分级、Gate 决策、阶段晋级 Gate、执行期 Gate、Policy 阻断、Trace/Audit 关联、错误响应、事件建议、前端联调边界与 R2/R4/R9-R12 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-016, D-025, D-030-D-034, D-037）、`文档/05-API与集成契约/00-API与集成契约总览.md`（§12）、`文档/05-API与集成契约/01-字段规范与错误响应规范.md`、`文档/05-API与集成契约/02-Project-Run-阶段API建议契约.md`、`文档/05-API与集成契约/03-TaskPlan-TaskGraph API建议契约.md`、`文档/05-API与集成契约/04-Artifact-Evidence-Trace-Audit API建议契约.md`、`文档/03-流程与运行时/04-Gate与中断恢复流程.md`。
> 重要边界：本文是 Gate / Authorization API 的 R1 建议契约，不替代项目治理决策、最终 OpenAPI 文档、后端路由实现、数据库 schema、LangGraph interrupt/resume 实现、安全权限规范、前端页面规范或运行时代码实现。
> 修订说明：R1 正式化版基于草稿 v0.1：(1) 头部标准化；(2) §0 新增关联决策速查表 + 字段规范引用声明；(3) 关键节加注决策引用；(4) 新增 §22 待确认项（4 项）；(5) 验收标准扩展（17→19 项）。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

**关联决策速查**：

| 决策编号 | 内容 | 本文相关节 |
|---|---|---|
| D-016 | API 字段 R1 输出建议契约，R2 校准为实现契约 | 全文 |
| D-025 | Manual/Plan/Auto 三执行模式 | §15 |
| D-030-D-034 | 安全机制：Hook+Policy+Security/Authorization Agent 三层 | §9, §12 |
| D-037 | LangGraph 主编排——interrupt/resume 落在 LangGraph | §14 |

**字段与错误码规范引用**：本文所有字段命名、状态字段、引用字段、请求响应结构和错误码均遵守 `01-字段规范与错误响应规范.md`。AET 关联遵守 `04-Artifact-Evidence-Trace-Audit API建议契约.md`。

本文必须遵守：

```text
1. API 字段 R1 输出建议契约，R2 校准为实现契约（D-016）；
2. P 阶段晋级 Gate 必须用户授权（D-023）；
3. Manual / Plan / Auto 不改变 P0-P6 产品流程，不改变 P 阶段晋级必须用户授权（D-025）；
4. Manual / Plan / Auto 只改变阶段内部计划审核、任务审核、授权审核和异常升级策略（D-025）；
5. Plan Mode 第一版不默认加入 Authorization Agent；
6. Plan Mode 通过用户审核 Stage Plan / Task Plan 来授权任务边界，执行中由 Hook + Policy 检查是否越界；
7. Auto Mode 下 Stage Plan 和 Task Plan 均由 Agent 审核，但高风险、低置信度、冲突、超范围回用户；
8. 安全机制采用 Hook + Policy / Rule + Security / Authorization Agent 三层（D-030-D-034）；
9. Policy 是硬约束，优先级高于 Agent 判断（D-031）；
10. Security / Authorization Agent 不能批准 Policy 禁止的动作；
11. L5 高风险动作暂定全部强制用户 Gate（D-034）；
12. Gate 决策和高风险动作必须 Trace / Audit；
13. API / 响应 / 错误 / Trace / Audit / 事件不得泄露 Key / Token / Secret / Password（D-032）；
14. 当前版本不设独立 Mission 产品层（D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 用 Authorization Agent 批准 Policy 禁止项；
2. 用 Auto Mode 跳过 P 阶段晋级用户 Gate；
3. 用 Plan Mode 的计划审核替代阶段晋级授权；
4. 用 Gate 决策替代 Evidence 验证；
5. 用 Gate 决策替代 Audit；
6. 用 Audit 记录替代用户授权；
7. 将 Gate 未决策的动作继续执行；
8. 将 L5 高风险动作自动放行（D-034）；
9. 将 gate_required / policy_blocked 伪装为普通 validation_error；
10. 在 API / 事件 / Trace / Audit 中泄露 Key / Token / Secret / Password（D-032）；
11. 引入 Mission 产品层或 mission_id 长期字段（D-070）；
12. 将 R1 建议字段伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. Gate 与 Authorization 对象边界

本文覆盖以下对象：

```text
Gate：暂停并等待授权、确认或决策的流程对象；
Authorization Request：授权请求；
Authorization Decision：授权决策；
Policy Check：策略检查结果；
Risk Action：需要识别风险级别的动作；
Interrupt：运行中断；
Resume：中断后的恢复；
Gate Audit：Gate 决策审计。
```

对象边界：

```text
1. Gate 是流程暂停与决策对象；
2. Authorization Request 是授权请求，不等于授权结果；
3. Authorization Decision 是决策结果，必须可追溯；
4. Policy Check 是硬约束检查，不是建议；
5. Risk Action 是动作风险识别结果，不等于可执行授权；
6. Interrupt / Resume 是运行时机制，不等于业务完成；
7. Gate Audit 是审计记录，不等于用户决策本身。
```

---

## 2. API 路由归口建议

```text
/api/projects/{project_id}/gates
/api/projects/{project_id}/gates/{gate_id}
/api/projects/{project_id}/gates/{gate_id}/decision
/api/projects/{project_id}/gates/{gate_id}/audit
/api/projects/{project_id}/authorization-requests
/api/projects/{project_id}/authorization-requests/{authorization_request_id}
/api/projects/{project_id}/authorization-requests/{authorization_request_id}/decision
/api/projects/{project_id}/policy-checks
/api/projects/{project_id}/policy-checks/{policy_check_id}
/api/projects/{project_id}/risk-actions
/api/projects/{project_id}/risk-actions/{risk_action_id}
/api/projects/{project_id}/runs/{run_id}/active-gate
/api/projects/{project_id}/runs/{run_id}/interrupt
/api/projects/{project_id}/runs/{run_id}/resume
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/promotion-gate
/api/projects/{project_id}/runs/{run_id}/stages/{stage}/promotion-decision
```

---

## 3. Gate API 总览

建议能力：创建 Gate、查询 Gate、列出 Gate、查询 active Gate、提交 Gate 决策、取消 Gate、查询 Gate Audit、关联 Gate Trace、Gate 后 resume、查询 Gate 对应 Artifact / Evidence / Risk / Policy。

Gate API 不负责：自动验证 Evidence、自动通过 P 阶段晋级、批准 Policy 禁止项、替代用户授权、保存密钥明文。

---

## 4. Gate 创建建议契约

建议端点：`POST /api/projects/{project_id}/gates`

请求字段建议：

```text
run_id：string，可选；
stage：string，可选；
task_graph_id：string，可选；
node_id：string，可选；
gate_type：string，必填，Gate 类型；
reason：string，必填，触发原因；
risk_level：string，可选；
triggered_by：string，可选；
action_ref：string，可选；
policy_check_ref：string，可选；
artifact_refs：array，可选；
evidence_refs：array，可选；
evidence_gap_refs：array，可选；
options：array，可选，可选决策项；
recommended_option：string，可选；
checkpoint_ref：string，可选；
metadata：object，可选，不得包含密钥。
```

规则：

```text
1. Gate 创建必须 Trace；
2. 高风险 Gate 创建可 Audit；
3. Gate 创建不等于用户授权；
4. Gate 创建后阻塞动作不得继续执行；
5. Gate 响应不得包含密钥明文。
```

---

## 5. Gate 类型建议

```text
stage_promotion：P 阶段晋级 Gate；
high_risk_action：高风险动作 Gate；
policy_conflict：Policy 冲突 Gate；
permission_escalation：权限提升 Gate；
external_write：外部系统写操作 Gate；
workspace_write：写入工作区 Gate；
shell_execution：shell / 命令执行 Gate；
resource_activation：资源启用 Gate；
community_resource_upgrade：社区资源升级 Gate；
evidence_exception：证据不足例外 Gate；
manual_confirmation：用户确认 Gate；
recovery_conflict：恢复冲突 Gate。
```

规则：

```text
1. stage_promotion 必须用户授权；
2. L5 high_risk_action 必须用户 Gate；
3. policy_conflict 不得被 Agent 自动批准；
4. external_write 必须 Gate；
5. evidence_exception 不得直接 completed；
6. recovery_conflict 不得盲目 resume。
```

---

## 6. Gate 状态建议

```text
created：已创建；
waiting_decision：等待决策；
under_review：审核中；
approved：已批准；
rejected：已拒绝；
needs_more_info：需要更多信息；
expired：已过期；
canceled：已取消；
resolved：已解决；
failed：处理失败。
```

规则：

```text
1. waiting_decision 状态下不得继续阻塞动作；
2. approved 必须有 decision_by / actor_ref；
3. rejected 必须有 decision_reason；
4. needs_more_info 必须有 required_info；
5. expired 不等于自动批准；
6. resolved 必须有关联 Trace / Audit；
7. 状态变化必须 Trace，高风险 Gate 必须 Audit。
```

---

## 7. Gate 决策建议契约

建议端点：`POST /api/projects/{project_id}/gates/{gate_id}/decision`

请求字段建议：

```text
decision：string，必填，approve / reject / request_more_info / cancel / rework；
decision_reason：string，必填或可选待 R2 校准；
accepted_risks：array，可选；
selected_option：string，可选；
next_stage：string，可选，阶段晋级时使用；
resume_target：string，可选；
actor_type：string，可选，user / agent / system；
actor_ref：string，可选；
metadata：object，可选，不得包含密钥。
```

规则：

```text
1. Gate 决策必须 Audit；
2. Gate 决策必须 Trace；
3. L5 Gate 决策必须来自用户授权；
4. Policy 禁止项不得被 approve；
5. Evidence 不足的风险接受必须记录 accepted_risks；
6. Gate 决策不自动执行 resume，除非 R2 明确实现并满足安全条件。
```

---

## 8. 阶段晋级 Gate 建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/stages/{stage}/promotion-gate
POST /api/projects/{project_id}/runs/{run_id}/stages/{stage}/promotion-decision
```

规则：

```text
1. 无论 Manual / Plan / Auto，每个启用的 P 阶段晋级都必须用户授权；
2. Stage Plan / Task Plan 审核不等于阶段晋级授权；
3. Evidence 不足不得默认晋级；
4. 阶段晋级决策必须 Audit；
5. 被裁剪阶段不得通过晋级 Gate 伪装 completed。
```

---

## 9. Authorization Request API 总览

建议能力：创建授权请求、查询/列出授权请求、提交授权决策、关联 Policy Check/Risk Action/Gate、查询授权 Trace/Audit。

不负责：批准 Policy 禁止项、替代 P 阶段晋级 Gate、替代 Evidence 验证、保存密钥明文、伪造用户授权。

Authorization 状态建议：

```text
requested → policy_checking → policy_blocked | waiting_gate | approved | rejected | expired | canceled | failed
```

规则：

```text
1. policy_blocked 不得被 Authorization Agent 改为 approved；
2. waiting_gate 必须有关联 gate_ref；
3. approved 必须有 decision_by 或 actor_ref；
4. expired 不等于自动批准；
5. 状态变化必须 Trace，高风险授权必须 Audit。
```

---

## 10. Policy Check API 建议契约

建议端点：

```text
POST /api/projects/{project_id}/policy-checks
GET /api/projects/{project_id}/policy-checks/{policy_check_id}
```

响应关键字段：`allowed`, `blocked`, `policy_refs`, `violation_refs`, `requires_gate`, `risk_level`, `recommended_action`

规则：

```text
1. Policy 是硬约束（D-030-D-034）；
2. blocked=true 时不得继续执行动作；
3. allowed=true 不代表无需 Gate；
4. requires_gate=true 必须创建或关联 Gate；
5. Policy Check 结果必须 Trace；
6. Policy 冲突必须 Audit 或进入安全审计策略。
```

---

## 11. Risk Action API 建议契约

建议端点：

```text
POST /api/projects/{project_id}/risk-actions
GET /api/projects/{project_id}/risk-actions/{risk_action_id}
```

规则：

```text
1. 风险级别按动作所属风险判断，不硬编码命令清单；
2. 同一动作在不同上下文中风险可不同；
3. L5 必须用户 Gate；
4. external_write 必须 Gate / Audit；
5. 风险识别不得被前端覆盖。
```

---

## 12. Interrupt / Resume API 建议契约

建议端点：

```text
POST /api/projects/{project_id}/runs/{run_id}/interrupt
POST /api/projects/{project_id}/runs/{run_id}/resume
```

规则：

```text
1. Interrupt 应记录 checkpoint_ref；
2. Resume 前必须校验 Gate、Policy、checkpoint、Workspace、Trace / Audit；
3. Gate 未决策不得 resume 阻塞动作；
4. checkpoint_missing 不得盲目 resume；
5. Resume 高风险动作必须 Audit。
```

> 关联决策：D-037（LangGraph interrupt/resume）

---

## 13. 执行模式与授权 API 关系

Manual / Plan / Auto 三模式完整定义见详述源 `01-决策记录.md` D-025 + `02-术语表.md`（单一事实源，本文不复述）。本文只定义三模式在**授权 API** 上的体现：Manual 全程用户审核动作授权；Plan 计划内动作由 Hook+Policy 放行、越界/风险升级回用户；Auto 阶段内授权由 Hook+Policy+Auto Review Agent 处理、高风险/低置信度/冲突/超范围回用户。三模式均不改变 P 阶段晋级必须用户授权。

API 规则：

```text
1. execution_mode 只影响阶段内部授权策略（D-025）；
2. execution_mode 不改变 P 阶段晋级 Gate；
3. Auto Mode 不能自动批准 L5；
4. Plan Mode 不能用大计划覆盖所有未来风险；
5. Manual Mode 也不能批准 Policy 禁止项。
```

---

## 14. Gate / Authorization 错误响应

建议错误类型：`gate_not_found`, `gate_required`, `gate_not_resolved`, `gate_rejected`, `gate_expired`, `gate_decision_invalid`, `authorization_required`, `authorization_rejected`, `policy_blocked`, `high_risk_gate_required`, `l5_user_gate_required`, `checkpoint_missing`, `resume_not_allowed`, `permission_denied`, `state_mismatch`, `audit_missing`, `trace_missing`, `redaction_required`, `validation_error`, `internal_error`

规则：

```text
1. gate_required 必须返回 gate_ref；
2. policy_blocked 不得伪装 validation_error；
3. l5_user_gate_required 不得自动重试；
4. resume_not_allowed 必须说明阻塞原因；
5. audit_missing 对 Gate 决策必须阻断或进入处理流程。
```

---

## 15. Gate / Authorization 事件建议

关键事件：`gate_created`, `gate_decided`, `gate_approved`, `gate_rejected`, `authorization_requested`, `authorization_approved`, `authorization_rejected`, `policy_blocked`, `high_risk_gate_required`, `run_interrupted`, `run_resumed`, `resume_blocked`, `gate_audit_written`

---

## 16. 前端联调边界

前端必须标记：等待用户决策、Policy 禁止、L5 用户 Gate、证据不足、恢复冲突、高风险动作、外部系统写操作、mock/未接真实服务。

前端不得：将 gate_required 只显示为普通 toast、隐藏 Policy 阻断、隐藏 Evidence 缺口、在 Gate 未决策时继续执行阻塞动作、用前端按钮状态替代后端权限事实、展示明文密钥、将 mock Gate 决策伪装为真实授权。

---

## 17. R2 / R4 / R9-R12 校准项

R2：Gate/Authorization/Policy Check/Risk Action 边界、P 阶段晋级仍强制用户授权、L5 仍强制用户 Gate、错误码与字段规范一致。

R4：Gate FastAPI 路由、LangGraph interrupt/resume 接口、Hook+Policy 接入、Trace/Audit Writer、脱敏 middleware。

R9-R12：逐链路校准 Gate 是否可用——P0/P1 晋级 Gate、P2/P3 计划审核 Gate、P4 高风险动作 Gate、P5/P6 Evidence 不足 Gate。

---

## 18. Gate / Authorization API 红线

```text
1. 不得用 Authorization Agent 批准 Policy 禁止项；
2. 不得用 Auto Mode 跳过 P 阶段晋级用户 Gate（D-023）；
3. 不得用 Plan Mode 的计划审核替代阶段晋级授权；
4. 不得用 Gate 决策替代 Evidence 验证；
5. 不得用 Gate 决策替代 Audit；
6. 不得用 Audit 记录替代用户授权；
7. 不得让 Gate 未决策的动作继续执行；
8. 不得自动放行 L5 高风险动作（D-034）；
9. 不得将 Policy 禁止伪装为普通校验失败（D-031）；
10. 不得将 gate_required 伪装为可自动重试错误；
11. 不得无 Trace 创建或决策 Gate；
12. 不得无 Audit 记录 Gate 决策或高风险动作；
13. 不得用前端状态替代 Gate / Authorization 后端事实源；
14. 不得引入 Mission 产品层（D-070）；
15. 不得在 API 响应、错误、事件、Trace、Audit 中泄露 Key / Token / Secret / Password（D-032）；
16. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 19. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 Gate 与 Authorization 对象边界；
2. 明确 API 路由归口建议；
3. 明确 Gate API 总览、创建、类型（12种）、状态和决策建议；
4. 明确阶段晋级 Gate 建议契约；
5. 明确 Authorization Request API 总览和状态建议；
6. 明确 Policy Check API 建议契约；
7. 明确 Risk Action API 建议契约；
8. 明确 Interrupt / Resume API 建议契约；
9. 明确执行模式与授权 API 关系（D-025）；
10. 明确错误响应；
11. 明确事件建议；
12. 明确前端联调边界；
13. 明确 R2 / R4 / R9-R12 校准项；
14. 明确 Gate / Authorization API 红线（16条）；
15. 未新增产品决策；
16. 未引入 Mission 产品层；
17. 未固定最终实现 schema；
18. 决策交叉引用完整（D-016/D-025/D-030-D-034/D-037）；
19. 待确认项显式列出。
```

---

## 20. 待确认项

```text
1. §5 Gate 类型 12 种——stage_promotion 与 02-Project-Run-阶段API §20 的阶段晋级 Gate 是否需要统一端点（当前为两套路由：/gates vs /stages/{stage}/promotion-gate）。

2. §12 Interrupt/Resume 的 checkpoint_ref 由 LangGraph 管理——API 层是否需要透传 checkpoint 细节还是仅传引用（后者更安全但前端/Audit 可观测性降低）。

3. §9 Authorization Request 与 §4 Gate 的关系——当前为两个独立对象，但实际流程中高风险 Authorization Request 通常会创建 Gate。是否需要在 API 层定义"Authorization Request → Gate"的自动升级规则。

4. §13 三模式授权策略中 Plan Mode "计划内动作由 Hook+Policy 检查并放行"——Hook 和 Policy 的检查结果是否需要统一通过 Policy Check API（§10）返回，还是存在独立的 Hook 通道。
```
