# 05-Audit审计规范

> 文档路径：`文档/07-安全与权限/05-Audit审计规范.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/05-Audit审计规范.md`
> 最后更新时间：2026-06-24
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`07-安全与权限/` 专题规范之六。定义 rebuild 当前版本 Audit 审计的操作级规范——审计目标、Audit 与 Trace/Evidence/Gate 边界、触发场景（17 项）、事件类型（20 种）、Record 字段（19 个）、状态（9 种）、9 类场景审计细节（Gate 决策/P 阶段晋级/高风险动作/Policy blocked/命令-Tool-MCP/Git-外部系统写/凭据脱敏/Evidence Gap）、前端展示、查询权限、存储不可变性、导出/报告/交接材料脱敏、API 字段/错误码/SSE 事件、状态恢复和 R2/R4/R6/R8/R11/R14/R15/R17 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-023、D-030~D-034、D-044、D-048、D-050、D-066）、`文档/00-项目治理/02-术语表.md`（第七部分：Audit 术语）、`文档/07-安全与权限/00-安全与权限总览.md`（§21 Evidence/Trace/Audit 安全 + §26 审计总览）、`文档/07-安全与权限/01-Policy-Hook-Authorization规范.md`（§15 Audit Policy P7 + §25 Gate 与 Audit 写入规范）、`文档/07-安全与权限/04-密钥与配置脱敏规范.md`（§12 Audit 脱敏规范）。
> 同级文档：`00-安全与权限总览.md` + `01-Policy-Hook-Authorization规范.md` + `02-风险分级与Gate策略.md` + `03-命令-Tool-MCP-Git-写盘授权规范.md` + `04-密钥与配置脱敏规范.md` + 本文（05）
> 重要边界：Audit 核心规则（Gate 决策必须写 Audit、高风险动作必须写 Audit、Audit 不等于授权/Evidence/Trace）已在 00 总览 §21/§26 和 01 规范 §15/§25 定义。本文在此基础上展开 Audit 的操作级规范——事件类型、Record 字段、状态管理、各场景审计细节、查询权限、存储不可变性。Audit 脱敏规则见 04-密钥与配置脱敏规范 §12（详述源），本文只列出 Audit 场景特有的脱敏约束。本文不替代 Trace 规范、Evidence 规范、Gate API、审计数据库设计、日志系统、权限系统、合规制度、前端组件或代码实现。本文为 R1 正式候选（建议契约），R2/R4/R6/R8/R11/R14/R15/R17 需按真实实现和联调结果校准。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 Audit 审计的操作级规范，不重新定义上级事实。Audit 核心规则见 `00-安全与权限总览.md` §21 + §26，Audit Policy（P7）策略定义见 `01-Policy-Hook-Authorization规范.md` §15，Gate 与 Audit 写入流程见 `01-Policy-Hook-Authorization规范.md` §25，Audit 脱敏字段约束见 `04-密钥与配置脱敏规范.md` §12。本文在此基础上展开事件类型、Record 字段、状态管理和各场景审计细节（AGENTS.md §8.2.1 单一事实源原则）。

本文必须遵守（来源标注于每条规则）：

```text
1. 安全机制采用 Hook + Policy + Security / Authorization Agent 三层（D-030）；
2. Policy 是硬约束，优先级高于 Agent 判断（D-031）；
3. Security / Authorization Agent 不能批准 Policy 禁止的动作（D-031）；
4. P 阶段晋级 Gate 必须用户授权（D-023）；
5. L5 高风险动作暂定全部强制用户 Gate（D-034）；
6. Gate 决策必须写 Audit（D-034）；
7. 高风险动作必须写 Audit（D-034）；
8. Policy blocked 高风险事件建议写 Audit；
9. Audit 不等于用户授权本身（D-034）；
10. Audit 不等于 Evidence；
11. Audit 不等于 Trace；
12. Audit 不得包含 Key / Token / Secret / Password 明文（D-032）；
13. Audit 缺失必须可见（D-050）；
14. 当前版本不设独立 Mission 产品层（D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 将 Audit 写入等同于授权通过；
2. 将 Audit 写入等同于结果可信；
3. 将 Audit 显示为 Evidence validated；
4. 将 Gate 决策不写 Audit；
5. 将高风险动作不写 Audit；
6. 隐藏 Audit 缺失；
7. 隐藏 Audit 写入失败；
8. 在 Audit 中记录 Secret 明文；
9. 将 Policy blocked 提供为可由 Audit 批准继续；
10. 用前端本地状态伪造 Audit written；
11. 用浏览器缓存或 SSE 缓存作为 Audit 事实源；
12. 引入 Mission 产品层或 mission_id 长期字段；
13. 把 R1 正式候选（建议契约）伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. Audit 审计目标

Audit 的目标是记录 rebuild 中需要可追责、可复核、可解释的关键安全和授权事件。

Audit 应回答：

```text
1. 谁做出了决策；
2. 决策对象是什么；
3. 决策发生在哪个 Project / Run / Stage / Node；
4. 决策或动作的风险级别是什么；
5. 决策依据是什么；
6. 关联 Gate 是什么；
7. 关联 Policy Check 是什么；
8. 关联 Trace 是什么；
9. 是否接受了风险；
10. 是否存在 Evidence Gap；
11. 是否存在后续动作或返工要求。
```

Audit 不负责证明结果正确。结果可信依赖 Evidence、Trace、Gate、Audit 与验证策略共同形成的链路（D-066）。

---

## 2. Audit 与 Trace / Evidence / Gate 边界

> 核心边界定义见 `00-安全与权限总览.md` §21。本文列出操作级的区分规则。

边界定义：

```text
Audit：记录决策、授权、高风险动作、Policy 冲突和安全事件；
Trace：记录过程、调用链、状态变化和执行链路；
Evidence：支撑 claim、验证和交付结论；
Gate：阻塞流程并承载决策。
```

边界规则：

```text
1. Audit 不等于 Trace；
2. Audit 不等于 Evidence；
3. Audit 不等于用户授权本身；
4. Gate 决策应写 Audit（D-034）；
5. Trace 可引用 Audit；
6. Evidence 可引用 Audit，但 Audit 不能替代 Evidence；
7. Audit 缺失应显示为可信链路缺口（D-050）。
```

---

## 3. Audit 触发场景总览

> Audit Policy（P7）策略定义见 `01-Policy-Hook-Authorization规范.md` §15。本文列出操作级的完整触发场景。

必须或建议 Audit 的场景：

```text
P 阶段晋级 Gate 决策；
Stage Plan / Task Plan / Task Plan Batch 关键授权；
L5 高风险动作；
高风险写盘；
Patch apply；
Git remote write；
外部系统写操作；
高风险 Tool / MCP 调用；
资源调用高风险事件；
Policy blocked 高风险事件；
用户接受 Evidence Gap 风险；
权限变更；
凭据状态变更；
配置敏感事件；
Audit 写入失败；
Trace 缺失相关安全事件；
交付确认。
```

规则：

```text
1. Gate 决策必须写 Audit（D-034）；
2. 高风险动作必须写 Audit（D-034）；
3. L5 必须写 Audit（D-034）；
4. 外部系统写操作必须写 Audit；
5. Audit 写入失败必须显示 pending / error；
6. Audit 事件不得包含 Secret 明文（D-032）。
```

---

## 4. Audit 事件类型

R1 建议 Audit 事件类型（20 种）：

```text
gate_decision_audit；
stage_promotion_audit；
stage_plan_audit；
task_plan_audit；
task_plan_batch_audit；
high_risk_action_audit；
write_scope_audit；
patch_apply_audit；
git_operation_audit；
external_write_audit；
resource_call_audit；
tool_call_audit；
mcp_call_audit；
policy_block_audit；
credential_status_audit；
redaction_audit；
permission_change_audit；
evidence_gap_risk_acceptance_audit；
delivery_audit；
audit_write_failure_audit。
```

规则：

```text
1. audit_type 必须明确；
2. audit_type 应能映射前端文案；
3. audit_type 不得混用 Trace 类型；
4. Evidence 事件不得伪装为 Audit；
5. Audit 事件类型 R2 可根据实现收敛。
```

---

## 5. Audit Record 字段建议

Audit Record 建议字段（19 个）：

```text
audit_id；
audit_type；
project_id；
run_id；
stage；
task_graph_id；
node_id；
action_id；
gate_id；
policy_check_ref；
trace_ref；
actor_type；
actor_ref；
decision；
risk_level；
risk_reasons；
accepted_risks；
reason；
resource_ref；
command_summary；
write_scope；
git_target_ref；
external_system_ref；
credential_refs；
redaction_status；
audit_status；
created_at；
request_id。
```

字段规则（D-032）：

```text
1. audit_id 必须唯一；
2. project_id 必须明确，平台级安全事件除外；
3. actor_ref 不得包含 Secret；
4. command_summary 必须脱敏；
5. credential_refs 只能是引用；
6. accepted_risks 应摘要化，不包含敏感明文；
7. reason 不得包含 Secret；
8. Audit Record 不得包含 Key / Token / Secret / Password。
```

---

## 6. Audit 状态

Audit 状态建议（9 种）：

```text
pending；
written；
write_failed；
redaction_required；
redaction_failed；
permission_limited；
archived；
superseded；
invalid。
```

状态规则：

```text
1. pending 不得显示为 written；
2. write_failed 必须显示 error_ref；
3. redaction_required 必须阻止明文展示；
4. redaction_failed 不得展示原文；
5. permission_limited 应显示无权限查看详情；
6. invalid 不得作为可信审计记录。
```

---

## 7. Gate 决策审计

> Gate 决策流程见 `01-Policy-Hook-Authorization规范.md` §25。本文列出 Audit 层面的记录要求。

Gate 决策审计是 Audit 的核心场景。

必须记录：

```text
gate_id / gate_type；
decision；
actor_ref；
risk_level / risk_reasons；
accepted_risks / reason；
policy_check_ref / trace_ref；
created_at。
```

规则：

```text
1. Gate approved 必须写 Audit（D-034）；
2. Gate rejected 必须写 Audit；
3. needs_more_info 必须写 Audit 或 Trace，并形成 next_actions；
4. rework 必须关联返工目标；
5. Policy blocked Gate 不得批准继续；
6. Gate 关闭不等于 Gate 决策；
7. 前端不得本地伪造 Gate 决策 Audit。
```

---

## 8. P 阶段晋级审计

> P 阶段晋级 Gate 完整定义见 `00-安全与权限总览.md` §11。本文列出 Audit 层面的记录要求。

P 阶段晋级审计用于记录从一个启用阶段进入下一阶段的用户授权（D-023）。

必须记录：

```text
当前阶段；
拟进入阶段；
阶段完成条件摘要；
Artifact 摘要；
Evidence 摘要；
Evidence Gap 摘要；
Trace 摘要；
用户决策；
风险说明；
created_at。
```

规则：

```text
1. P 阶段晋级必须用户授权（D-023）；
2. 用户授权必须写 Audit；
3. Evidence 不足不得默认晋级；
4. 被裁剪阶段不得审计为 completed；
5. Stage completed 必须以后端状态为准；
6. Audit 记录不得替代 Evidence。
```

---

## 9. 高风险动作审计

高风险动作审计覆盖 L4/L5 以及其他策略要求审计的动作。

必须记录：

```text
action_type；
risk_level / risk_reasons；
impact_scope / write_scope；
external_effect / rollback_capability；
actor_ref；
gate_id / policy_check_ref / trace_ref；
result / error_ref。
```

规则（D-034）：

```text
1. L5 必须写 Audit；
2. 高风险写盘必须写 Audit；
3. 外部系统写操作必须写 Audit；
4. 高风险动作无 Audit 不得静默通过；
5. Audit 写入失败必须显示 pending / error；
6. Audit 不得包含命令未脱敏输出。
```

---

## 10. Policy blocked 审计

Policy blocked 是终止性结果，高风险阻断应审计。

建议记录：

```text
policy_check_ref；
matched_policy_refs；
violations；
action_id / action_type；
actor_ref；
risk_level；
blocking_reasons；
trace_ref；
created_at。
```

规则（D-031）：

```text
1. blocked 不进入执行；
2. blocked 不进入批准继续流程；
3. Authorization Agent 不能批准 blocked；
4. 高风险 blocked 建议写 Audit；
5. Audit 只记录阻断摘要，不记录 Secret 明文；
6. 前端应显示"被 Policy 阻断"。
```

---

## 11. 命令 / Tool / MCP 审计

> 命令/Tool/MCP 授权规范见 `03-命令-Tool-MCP-Git-写盘授权规范.md`。本文列出 Audit 层面的记录要求。

必须审计：

```text
高风险命令；
等待 Gate 的命令；
高风险 Tool 调用；
高风险 MCP 调用；
外部写 Tool / MCP；
Policy blocked 命令 / Tool / MCP；
执行涉及凭据状态变化的动作。
```

记录建议：

```text
action_type；
command_summary；
resource_ref / mcp_ref；
risk_level；
policy_check_ref / gate_id / trace_ref；
output_ref / error_ref。
```

规则：

```text
1. command_summary 必须脱敏；
2. output_ref 不等于 Evidence；
3. Tool / MCP 输出不自动成为 Evidence validated；
4. 高风险 Tool / MCP 必须 Gate / Audit（D-034）；
5. MCP 凭据不得进入 Audit 明文（D-032）；
6. 审计详情按权限展示。
```

---

## 12. Git 与外部系统写操作审计

Git 远端写入和外部系统写操作必须审计。

Git 审计记录建议：

```text
git_operation_type；
git_repo_ref / branch / commit_ref / remote_ref；
credential_ref；
risk_level；
gate_id / trace_ref；
result / error_ref。
```

外部系统写操作审计建议：

```text
external_system_ref；
operation_type / impact_scope；
credential_ref；
risk_level；
gate_id / trace_ref；
result / error_ref。
```

规则（D-032）：

```text
1. Git token 不得进入 Audit；
2. 含凭据的 Git URL 必须脱敏；
3. 外部系统凭据只记录 credential_ref；
4. 外部系统写操作默认高风险；
5. 失败响应必须脱敏；
6. mock 外部写不得伪装为真实 Audit。
```

---

## 13. 凭据与脱敏审计

> 凭据脱敏完整规范见 `04-密钥与配置脱敏规范.md`。本文列出 Audit 层面的凭据事件记录。

凭据和脱敏相关事件建议审计：

```text
credential_status_changed；
credential_missing；
credential_invalid；
credential_permission_denied；
redaction_required；
redaction_failed；
export_blocked_by_secret；
memory_write_blocked_by_secret；
model_context_redacted。
```

规则：

```text
1. Audit 可记录 credential_ref；
2. Audit 可记录 credential_status；
3. Audit 不得记录 Secret 明文（D-032）；
4. redaction_failed 必须可见；
5. memory_write_blocked_by_secret 应记录阻断摘要（D-044）；
6. 凭据状态变更不应泄露凭据内容。
```

---

## 14. Evidence Gap 风险接受审计

当用户选择接受 Evidence Gap 风险继续推进时，必须审计。

记录建议：

```text
evidence_gap_id；
claim_refs；
stage；
risk_level；
accepted_risks / reason；
actor_ref；
gate_id / trace_ref；
created_at。
```

规则：

```text
1. 接受风险必须写 Audit；
2. 接受风险不等于 Evidence validated；
3. 接受风险不等于 P5 验证通过；
4. Evidence Gap 不得隐藏（D-050）；
5. 交付时必须能看到该审计摘要；
6. Audit 不得替代补证。
```

---

## 15. Audit 与前端展示

> 前端安全展示完整要求见 `00-安全与权限总览.md` §22。本文列出 Audit 特有的前端展示要求。

前端应展示：

```text
Audit 摘要；
Audit 类型；
风险级别；
关联 Gate / Action / Trace；
决策摘要；
actor 摘要；
created_at；
audit_status；
error_ref。
```

前端不得：

```text
1. 将 Audit 显示为用户授权本身；
2. 将 Audit 显示为 Evidence；
3. 将 Audit 缺失隐藏；
4. 将 Audit pending 显示为 written；
5. 展示 Secret 明文（D-032）；
6. 用前端本地状态伪造 Audit written。
```

---

## 16. Audit 查询与权限

Audit 查询应按权限展示摘要和详情。

查询维度建议：

```text
project_id / run_id / stage / node_id；
audit_type / risk_level；
gate_id / action_id；
actor_ref；
created_at；
audit_status。
```

权限规则：

```text
1. 默认可展示脱敏摘要；
2. 详情按权限展示；
3. 权限提升也不得展示 Secret 明文（D-032）；
4. permission_denied 不等于没有 Audit；
5. 查询结果不得返回未脱敏详情；
6. 导出查询结果必须走脱敏检查。
```

---

## 17. Audit 存储与不可变性建议

R1 不固定最终存储实现，但建议：

```text
Audit Record 应持久化；
Audit Record 应可按 project_id / run_id / gate_id / action_id 查询；
Audit Record 不应被普通流程修改；
修正 Audit 应追加 correction / superseded 记录；
删除 Audit 应视为高风险动作；
Audit 存储失败应进入错误状态并前端可见。
```

规则：

```text
1. 不得静默丢失 Audit；
2. 不得直接覆盖历史 Audit；
3. Audit 删除或归档必须受控；
4. Audit 记录不包含 Secret 明文（D-032）；
5. Audit 与 Trace 可相互引用；
6. R4/R17 根据真实存储实现校准。
```

---

## 18. Audit 与导出 / 报告 / 交接材料

> 导出脱敏完整规范见 `04-密钥与配置脱敏规范.md` §20。本文列出 Audit 特有的导出约束。

Audit 可进入报告和交接材料，但必须脱敏。

可导出内容：

```text
Audit 摘要；
Audit 类型 / 风险级别；
决策摘要；
关联 Gate / Trace / Evidence Gap；
created_at；
next_actions。
```

不得导出（D-032）：

```text
Secret 明文；
完整 .env / 完整连接串；
未脱敏命令输出；
未脱敏模型输入输出；
未脱敏外部系统响应；
可还原密钥片段。
```

规则：

```text
1. 导出前必须脱敏检查；
2. 交接材料不得包含 Secret；
3. 验收报告可引用 Audit 摘要；
4. Audit 摘要不等于 Evidence；
5. 导出失败不得输出原文；
6. 外部 Agent 交接材料需自包含但不含密钥。
```

---

## 19. Audit API 字段建议

Audit API 字段建议（R1 建议契约，R2/R4 校准）：

```text
audit_id / audit_type / audit_status；
project_id / run_id / stage / task_graph_id / node_id；
action_id / gate_id / policy_check_ref / trace_ref；
actor_type / actor_ref；
decision / risk_level / risk_reasons / accepted_risks；
summary / redaction_status / permission_scope；
error_ref；
created_at / updated_at；
request_id。
```

规则：

```text
1. R1 字段为建议契约；
2. R2/R4 按真实实现校准；
3. 默认响应不返回 Secret；
4. audit_status 必须可见；
5. redaction_status 必须可见；
6. error_ref 用于写入失败或查询失败。
```

---

## 20. Audit API 错误码建议

Audit 特有错误码：

```text
audit_required；
audit_missing；
audit_write_failed；
audit_read_failed；
audit_permission_denied；
audit_redaction_required；
audit_redaction_failed；
audit_invalid；
audit_superseded；
audit_export_blocked_by_secret；
gate_audit_missing；
high_risk_audit_missing；
policy_block_audit_failed；
internal_error。
```

规则：

```text
1. 错误响应应包含 request_id；
2. 可用时包含 trace_ref；
3. audit_write_failed 不得静默；
4. audit_permission_denied 不得返回详情；
5. audit_redaction_failed 不得返回原文；
6. internal_error 不暴露堆栈。
```

---

## 21. Audit SSE / Event 建议

Audit 相关事件：

```text
audit_required；
audit_write_started；
audit_written；
audit_write_failed；
audit_redaction_required；
audit_redaction_failed；
gate_audit_written；
high_risk_audit_written；
policy_block_audit_written；
external_write_audit_written；
evidence_gap_risk_acceptance_audit_written；
audit_permission_limited；
audit_superseded；
resync_required。
```

规则：

```text
1. 事件 payload 不得包含 Secret（D-032）；
2. 事件只作为刷新提示；
3. audit_written 不等于业务完成；
4. audit_write_failed 必须前端可见；
5. gate_audit_written 不等于 Evidence validated；
6. resync_required 必须重新查询 Audit。
```

---

## 22. Audit 状态恢复

状态恢复必须重新查询（D-048）：

```text
Audit Record；
Gate / Policy Check / Trace；
Action / Run / Stage；
Evidence Gap；
Export 状态。
```

规则：

```text
1. 浏览器缓存不能作为 Audit 事实源；
2. SSE 事件缓存不能作为 Audit written 事实源；
3. 前端本地状态不能作为审计完成事实源；
4. audit_status 必须以后端查询为准；
5. audit_write_failed 必须恢复后仍可见；
6. resync_required 必须全量刷新相关对象。
```

---

## 23. R2 / R4 / R6 / R8 / R11 / R14 / R15 / R17 校准项

各阶段校准按 `06-R阶段总计划.md` 执行。

### 23.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. Audit 与 Trace / Evidence / Gate 边界是否足够清晰；
3. Audit 事件类型（20 种）是否过多，是否需要收敛；
4. Audit 字段是否与 API 契约一致；
5. Audit 脱敏是否与 04-密钥与配置脱敏规范 一致；
6. 是否仍无 Mission 产品层；
7. 是否重复上级事实，需要改为引用。
```

### 23.2 R4 API 与工程骨架校准

```text
1. Audit Writer 是否接入；
2. Gate / Authorization API 是否产生 Audit；
3. Policy blocked 是否可写 Audit；
4. Audit API 是否可查询；
5. Audit SSE 事件是否接入；
6. Audit 写入失败是否有错误状态。
```

### 23.3 R6 Agent / Skill / 资源基础校准

```text
1. Agent Definition 是否包含 Audit 触发规则；
2. Tool / MCP 高风险调用是否写 Audit；
3. Resource Registry 是否包含 audit_policy；
4. Authorization Agent 输出是否可审计；
5. Case / Knowledge 是否只读且不触发误审计。
```

### 23.4 R8 Workspace 真实化校准

```text
1. 写盘 / Patch / 文件操作是否写 Audit；
2. Workspace 边界事件是否可审计；
3. 前端是否显示 Audit 摘要；
4. Audit 缺失是否可见；
5. Audit 详情是否脱敏。
```

### 23.5 R11 P4 执行链路校准

```text
1. 命令高风险动作是否写 Audit；
2. Tool / MCP 高风险调用是否写 Audit；
3. Git remote write 是否写 Audit；
4. 外部系统写操作是否写 Audit；
5. 执行失败是否能关联 Trace / Audit。
```

### 23.6 R14 / R15 增强资源校准

```text
R14：远程资源调用、远程 MCP、外部系统写操作审计校准；
R15：社区资源审核、发布、执行升级审计校准。
```

### 23.7 R17 全量联调校准

```text
1. Gate 决策是否全部有 Audit；
2. 高风险动作是否全部有 Audit；
3. Audit 缺失是否可见；
4. Audit 是否全链路脱敏；
5. 用户是否能理解 Audit 与 Evidence 的区别。
```

---

## 24. Audit 审计红线

以下红线在 rebuild 当前版本任何阶段不得违反（来源：D-023、D-031、D-032、D-034、D-044、D-048、D-050）：

```text
1. 不得将 Audit 写入等同于授权通过；
2. 不得将 Audit 写入等同于结果可信；
3. 不得将 Audit 显示为 Evidence validated；
4. 不得将 Gate 决策不写 Audit；
5. 不得将高风险动作不写 Audit；
6. 不得隐藏 Audit 缺失；
7. 不得隐藏 Audit 写入失败；
8. 不得在 Audit 中记录 Secret 明文；
9. 不得将 Policy blocked 提供为可由 Audit 批准继续；
10. 不得用前端本地状态伪造 Audit written；
11. 不得用浏览器缓存或 SSE 缓存作为 Audit 事实源；
12. 不得将 Audit 导出为包含密钥的报告；
13. 不得将 Audit 缺失的高风险动作显示为已完成；
14. 不得将 Audit 摘要替代 Evidence；
15. 不得引入 Mission 产品层；
16. 不得使用旧 Phase / 旧 F0-F6 / 旧 S0-S7 作为当前主流程；
17. 不得在长期 API、DB、路由、组件、状态字段中固化 V26.1；
18. 不得把 R1 正式候选（建议契约）伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 25. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 Audit 审计目标（11 项应答）；
2. 明确 Audit 与 Trace / Evidence / Gate 边界（7 条规则）；
3. 明确 Audit 触发场景（17 项）；
4. 明确 Audit 事件类型（20 种）；
5. 明确 Audit Record 字段建议（~28 个字段）；
6. 明确 Audit 状态（9 种）；
7. 明确 Gate 决策审计；
8. 明确 P 阶段晋级审计；
9. 明确高风险动作审计；
10. 明确 Policy blocked 审计；
11. 明确命令 / Tool / MCP / Git / 外部系统写操作审计；
12. 明确凭据与脱敏审计；
13. 明确 Evidence Gap 风险接受审计；
14. 明确前端展示、查询权限（7 维度）、存储与不可变性建议；
15. 明确导出 / 报告 / 交接材料审计脱敏；
16. 明确 API 字段（~25 个）、错误码（14 种）、SSE / Event（14 种）、状态恢复；
17. 明确 R2 / R4 / R6 / R8 / R11 / R14 / R15 / R17 校准项（7 阶段）；
18. 明确 Audit 审计红线（18 条）；
19. 未新增产品决策；
20. 未引入 Mission 产品层；
21. 未固定最终实现；
22. 与 00 §21/§26、01 §15/§25、04 §12 引用关系清晰。
```
