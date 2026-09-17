# 01-Policy-Hook-Authorization规范

> 文档路径：`文档/07-安全与权限/01-Policy-Hook-Authorization规范.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/01-Policy-Hook-Authorization规范.md`
> 最后更新时间：2026-06-24
> 修订说明：R2：新增引用 D-073 平台助手（§23A 代操作[规划/R-future]纳入 Policy/Hook/Authorization 与风险分级 L3+/强制 Gate、对话与代操作纳入 Audit、自测复用 ModelGateway 不泄露密钥）；R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`07-安全与权限/` 专题规范之一。定义 rebuild 当前版本 Policy、Hook、Security/Authorization Agent 与 Gate 的协作规范，覆盖动作拦截、策略判断、授权辅助、阻断、风险升级、Manual/Plan/Auto 模式差异、命令/Tool/MCP/写盘/Git/外部系统写操作、凭据脱敏、Trace/Audit、安全事件、API 错误和前端展示要求。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-023~D-027、D-030~D-034、D-036、D-040、D-043/D-044、D-048、D-050、D-061、D-063、D-065、D-066）、`文档/00-项目治理/02-术语表.md`（第七部分）、`文档/07-安全与权限/00-安全与权限总览.md`（三层机制总览 + 安全对象边界）。
> 同级文档：本文（01）+ `00-安全与权限总览.md`（已落位）+ `02-风险分级与Gate策略.md`（待正式化）+ `03-命令-Tool-MCP-Git-写盘授权规范.md`（待正式化）
> 重要边界：本文是 Policy/Hook/Authorization 专题规范，不替代 Policy DSL、后端 middleware、权限数据库设计、身份认证实现、审计存储、Agent Definition Contract、API 契约或代码实现。本文为 R1 正式候选（建议契约），R2/R4/R6/R8/R11/R14/R15/R17 需按真实实现校准。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 Policy/Hook/Authorization 协作规范，不重新定义上级事实。三层机制总览、安全对象边界、执行模式权限关系已在 `00-安全与权限总览.md` 定义，本文引用而不复述（AGENTS.md §8.2.1 单一事实源原则）。

本文必须遵守（来源标注于每条规则）：

```text
1. 安全机制采用 Hook + Policy + Security / Authorization Agent 三层（D-030）；
2. Policy 是硬约束，优先级高于 Agent 判断（D-031）；
3. Security / Authorization Agent 不能批准 Policy 禁止的动作（D-031）；
4. Manual / Plan / Auto 不改变 P0-P6 产品流程，不改变 P 阶段晋级必须用户授权的规则（D-023~D-027）；
5. 命令、Tool、MCP、写盘、Git 操作等必须先映射到风险级别，再按执行模式策略处理（D-033）；
6. L5 高风险动作暂定全部强制用户 Gate（D-034）；
7. Task Plan Batch 必须说明批次目标、任务范围、风险级别、权限边界、验收方式和异常升级策略（D-027）；
8. 资源调用必须记录来源、风险、权限和使用 Trace（D-040）；
9. 确定性转换资源不享有特殊流程特权（D-063）；
10. Fusion 不绕过 Policy / Gate / Audit（D-036）；
11. 允许识别 env、配置文件、密钥文件是否存在，但不得泄露 Key/Token/Secret/Password（D-032）；
12. 当前版本不设独立 Mission 产品层（D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 允许 Agent 绕过 Hook；
2. 允许 Agent 或前端绕过 Policy；
3. 允许 Authorization Agent 批准 Policy 禁止项；
4. 允许 policy_blocked 进入"批准继续"流程；
5. 将 unknown / error 默认按 allowed 处理；
6. 将 Gate 写成普通提醒后继续执行；
7. 将 Audit 写入等同于授权通过；
8. 将 Trace 写入等同于结果可信；
9. 将 Auto Mode 解释为所有动作自动放行；
10. 将静态命令清单当作唯一风险依据；
11. 在日志、前端、Trace、截图或交接材料中泄露密钥；
12. 引入 Mission 产品层或 mission_id 长期字段；
13. 把 R1 正式候选（建议契约）伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 规范目标

Policy/Hook/Authorization 的目标是建立一条可执行、可追溯、不可被 Agent 任意绕过的安全决策链路。

这条链路必须回答：

```text
1. 动作是什么；
2. 动作由谁发起；
3. 动作作用于哪个 Project / Run / Stage / Node；
4. 动作涉及哪些资源、文件、命令、外部系统或凭据；
5. 动作风险级别是什么；
6. 是否被 Policy 禁止；
7. 是否需要 Gate；
8. 是否需要 Audit；
9. 用户或授权主体做出了什么决策；
10. 动作执行后是否产生 Trace / Audit / Evidence / Artifact。
```

本文不试图提前固定最终代码实现，但必须为 R4 API、R6 Agent/Skill/资源、R8 Workspace、R11 执行链路、R14/R15 资源增强提供共同安全契约。

---

## 2. 三层职责边界

> 三层机制的完整定义和安全对象边界见 `00-安全与权限总览.md` §2~§6。本文只展开协作规范，不重复定义。

三层安全职责（D-030）：

```text
Hook：负责动作发生前、中、后的拦截、上下文采集、策略触发和记录；
Policy：负责硬约束判断，决定 allow / warn / require_gate / require_audit / block / redact 等结果；
Security / Authorization Agent：负责在 Policy 未禁止的范围内进行辅助评审、风险解释、升级建议和用户决策支持。
```

职责边界（D-030~D-031）：

```text
1. Hook 是入口，不是最终裁决；
2. Policy 是硬约束，不是建议；
3. Authorization Agent 是辅助主体，不是最高权限主体；
4. Gate 是阻塞对象，不是普通通知；
5. Audit 是审计记录，不是授权本身；
6. Trace 是过程记录，不是可信结果证明。
```

---

## 3. 总体调用链

建议调用链（D-030~D-034）：

```text
Action Request
  -> Hook 捕获动作与上下文
  -> 构造 Policy Check 输入
  -> Policy 判断
  -> 若 blocked：阻断 + Trace / Audit（按策略）
  -> 若 redaction_required：脱敏后继续或阻断展示
  -> 若 requires_gate：创建 / 关联 Gate，暂停动作
  -> 若 requires_audit：准备 Audit 记录要求
  -> 若 needs_review：交给 Security / Authorization Agent 辅助评审
  -> 若 allowed：执行动作
  -> 动作完成后写 Trace / Audit / Artifact / Evidence Candidate（按需）
  -> 刷新 Run / Stage / UI 状态
```

链路规则：

```text
1. Policy Check 必须在动作执行前发生；
2. blocked 不进入执行；
3. requires_gate 未决策不进入执行；
4. needs_review 不等于 allowed；
5. Audit 写入失败不得静默；
6. Trace 写入失败必须可见；
7. 前端仅展示状态，不本地改写安全结果（D-050）。
```

---

## 4. Action Request 契约

Action Request 是安全链路的输入对象。

R1 建议字段：

```text
action_id；
action_type；
project_id；
run_id；
stage；
task_graph_id；
node_id；
actor_type；
actor_ref；
execution_mode；
resource_ref；
tool_ref；
mcp_ref；
command_summary；
file_refs；
write_scope；
external_system_ref；
credential_refs；
input_refs；
expected_output_refs；
plan_ref；
trace_parent_ref；
request_id。
```

规则（D-032）：

```text
1. action_type 必须明确；
2. project_id 必须明确，除非是平台级只读动作；
3. run_id / stage / node_id 可为空，但为空原因应可解释；
4. credential_refs 只能是引用，不能是明文；
5. command_summary 必须脱敏；
6. write_scope 必须尽量具体；
7. Action Request 不得包含 Key / Token / Secret / Password。
```

---

## 5. Hook 触发点

Hook 应覆盖以下触发点：

```text
pre_model_call；
post_model_call；
pre_resource_call；
post_resource_call；
pre_tool_call；
post_tool_call；
pre_mcp_call；
post_mcp_call；
pre_command；
post_command；
pre_file_read；
post_file_read；
pre_file_write；
post_file_write；
pre_patch_apply；
post_patch_apply；
pre_git_operation；
post_git_operation；
pre_external_write；
post_external_write；
pre_gate_decision；
post_gate_decision；
pre_audit_write；
post_audit_write。
```

触发规则：

```text
1. pre_* Hook 用于拦截、Policy Check 和 Gate 判断；
2. post_* Hook 用于结果记录、Trace、Audit 和状态刷新；
3. pre_gate_decision 不得被跳过；
4. post_gate_decision 必须触发 Audit；
5. Hook 失败不得默认 allowed；
6. Hook 返回 unknown 时必须升级为 needs_review 或 block。
```

---

## 6. Hook Result 契约

Hook Result 建议字段：

```text
hook_result_id；
action_id；
hook_type；
result；
risk_level；
policy_check_ref；
gate_ref；
audit_required；
trace_required；
redaction_required；
review_required；
blocking_reasons；
warnings；
next_actions；
trace_ref；
error_ref。
```

result 建议枚举：

```text
allow；
warn；
require_gate；
require_audit；
needs_review；
block_by_policy；
redaction_required；
error；
unknown。
```

规则：

```text
1. block_by_policy 优先级高于 require_gate；
2. redaction_required 必须阻止明文展示；
3. error / unknown 不得默认放行；
4. require_gate 必须暂停动作；
5. require_audit 必须在执行后写入 Audit 或显示 pending；
6. blocking_reasons 不能被 warnings 覆盖。
```

---

## 7. Policy Check 输入契约

Policy Check 输入建议字段：

```text
policy_check_id；
action_request_ref；
action_type；
actor_type；
actor_ref；
execution_mode；
project_id；
run_id；
stage；
node_id；
risk_context；
permission_scope；
resource_context；
workspace_context；
credential_context；
write_scope；
external_effect；
rollback_capability；
plan_scope_ref；
existing_gate_refs；
trace_parent_ref。
```

规则：

```text
1. Policy Check 输入必须脱敏；
2. credential_context 只包含状态和引用；
3. risk_context 必须包含范围、路径、外部影响和可回滚性；
4. plan_scope_ref 用于判断是否越界；
5. 输入不足时返回 unknown / needs_review，不得默认 allowed。
```

---

## 8. Policy Check 输出契约

Policy Check 输出建议字段：

```text
policy_check_id；
matched_policy_refs；
violations；
result；
risk_level；
requires_gate；
requires_audit；
redaction_required；
allowed_scope；
blocked_scope；
reasons；
warnings；
recommended_gate_type；
recommended_audit_type；
next_actions；
trace_ref。
```

result 建议枚举：

```text
allowed；
allowed_with_warning；
requires_gate；
requires_audit；
blocked；
redaction_required；
not_applicable；
unknown。
```

规则（D-031）：

```text
1. blocked 是终止性结果；
2. blocked 不允许 Authorization Agent 批准；
3. unknown 不得默认 allowed；
4. requires_gate 必须给出 gate_type 建议；
5. redaction_required 必须阻止原文外泄；
6. allowed_scope 不得被前端扩大。
```

---

## 9. Policy 优先级

R1 建议 Policy 优先级（由高到低）：

```text
P0 Forbidden：绝对禁止；
P1 Redaction：必须脱敏；
P2 Boundary：边界 / Workspace / 路径 / Project 隔离；
P3 Credential：凭据与密钥；
P4 External Effect：外部系统写操作；
P5 High Risk：高风险动作；
P6 Gate Required：需要 Gate；
P7 Audit Required：需要 Audit；
P8 Warning：警告但可继续；
P9 Informational：信息记录。
```

处理规则：

```text
1. P0 Forbidden 命中后直接 blocked；
2. P1 Redaction 命中后不得展示明文；
3. P2 Boundary 命中后通常 blocked 或 requires_gate；
4. P4 External Effect 默认高风险；
5. P5 High Risk 至少 requires_gate 或 needs_review；
6. 低优先级不能覆盖高优先级；
7. 多条 Policy 命中时按最高风险结果处理。
```

---

## 10. Forbidden Policy（P0）

Forbidden Policy 用于禁止不可接受动作。

示例类型：

```text
泄露密钥；
越权访问 workspace 外敏感路径；
绕过 Gate；
绕过 Policy；
执行明确禁止命令；
未授权外部系统写操作；
将社区未审核可执行资源直接运行；
将 Policy blocked 改为 allowed；
将凭据写入 Memory；
将历史只读归档改写。
```

规则：

```text
1. Forbidden 命中直接 blocked；
2. 不进入批准流程；
3. 不提供"继续执行"按钮；
4. 必须记录 trace_ref；
5. 是否写 Audit 由策略决定，但高风险阻断建议写 Audit；
6. 前端文案必须明确"被 Policy 阻断"。
```

---

## 11. Redaction Policy（P1）

Redaction Policy 用于防止敏感内容外泄（D-032）。

敏感对象包括：

```text
Key；
Token；
Secret；
Password；
.env 明文；
连接串；
包含凭据的 Git URL；
Provider Key；
API Key；
未脱敏模型输入输出；
未脱敏终端输出；
未脱敏 Trace / Audit 详情。
```

规则：

```text
1. redaction_required 必须阻止展示原文；
2. 可展示 credential_ref / credential_status / redacted；
3. 错误详情必须脱敏；
4. 搜索结果不得返回敏感明文；
5. 截图、导出、报告不得包含密钥；
6. 脱敏失败不得默认展示。
```

---

## 12. Boundary Policy（P2）

Boundary Policy 用于保证 Project、Workspace、资源和权限边界。

边界类型：

```text
project_id 隔离；
workspace 边界；
只读归档边界；
参考资料只读边界；
社区资源审核边界（D-061）；
权限 scope 边界；
计划 scope 边界；
外部系统 scope 边界。
```

规则：

```text
1. workspace 外写入默认 blocked 或强 Gate；
2. 历史归档目录默认只读；
3. 参考资料默认只读；
4. 社区资源默认只读参考（D-061）；
5. plan_scope 之外动作必须升级；
6. Boundary 冲突必须可见。
```

---

## 13. Risk Policy（P4-P5）

Risk Policy 用于动作风险判断。风险级别 L0-L5 完整定义见 `02-术语表.md` 第七部分 + `02-风险分级与Gate策略.md`。

风险判断维度：

```text
动作类型；
影响范围；
写入范围；
外部影响；
是否可回滚；
是否涉及凭据；
是否涉及敏感路径；
是否涉及社区资源；
是否在已批准计划内；
是否可能影响 Evidence / Trace / Audit 完整性。
```

规则（D-033~D-034）：

```text
1. 风险级别不能只由命令名决定；
2. 同一命令在不同上下文中风险可能不同；
3. L5 暂定全部强制用户 Gate；
4. 外部系统写操作默认高风险；
5. 大范围 Patch apply 默认高风险；
6. 风险升级必须可 Trace。
```

---

## 14. Gate Policy（P6）

Gate Policy 用于判断动作是否需要 Gate。Gate 类型完整定义见 `00-安全与权限总览.md` §10 + `02-风险分级与Gate策略.md`。

常见 Gate 触发：

```text
P 阶段晋级（D-023）；
L5 高风险动作（D-034）；
高风险写盘；
Patch apply；
外部系统写操作；
Git push / merge；
Policy 允许但需用户确认的边界动作；
Evidence Gap 风险接受；
Task Plan / Stage Plan 需要用户审核；
资源调用高风险或越界；
凭据缺失或权限不确定。
```

规则：

```text
1. requires_gate 必须创建或关联 Gate；
2. Gate 未决策不得执行；
3. Gate 决策必须写 Audit；
4. P 阶段晋级 Gate 必须用户授权（D-023）；
5. Authorization Agent 不得代替用户阶段晋级 Gate；
6. Gate 关闭不等于批准。
```

---

## 15. Audit Policy（P7）

Audit Policy 用于判断动作是否需要审计。

建议强制 Audit 场景：

```text
P 阶段晋级 Gate 决策；
高风险动作；
L5 动作；
外部系统写操作；
Git push / merge；
Policy blocked；
Evidence Gap 风险接受；
资源高风险调用；
权限变更；
凭据状态变更；
安全异常；
Gate 决策。
```

规则：

```text
1. Audit 不得包含密钥明文（D-032）；
2. Audit 缺失必须显示；
3. Audit 写入失败不得静默；
4. Gate 决策没有 audit_ref 时必须显示 pending / error；
5. Audit 不等于用户授权本身；
6. Audit 详情按权限展示。
```

---

## 16. Authorization Agent 输入契约

Authorization Agent 只处理 Policy 未禁止且需要辅助判断的动作（D-031）。

输入建议：

```text
authorization_request_id；
action_request_ref；
policy_check_ref；
risk_level；
risk_reasons；
actor_ref；
project_id；
run_id；
stage；
node_id；
plan_scope_ref；
resource_context；
write_scope；
external_effect；
rollback_capability；
evidence_context；
trace_context；
audit_context；
user_decision_required；
redacted_summary。
```

规则：

```text
1. 输入必须脱敏；
2. blocked Policy 不得传入请求批准；
3. user_decision_required=true 时 Agent 只能辅助解释，不能代替用户；
4. Authorization Agent 不得接收密钥明文；
5. 输入不足时应返回 needs_more_info。
```

---

## 17. Authorization Agent 输出契约

输出建议：

```text
authorization_result_id；
authorization_request_id；
recommendation；
confidence；
risk_summary；
required_user_gate；
required_audit；
required_trace；
blocked_by_policy；
needs_more_info；
recommended_next_actions；
reasoning_summary；
trace_ref。
```

recommendation 建议枚举：

```text
recommend_allow；
recommend_gate；
recommend_reject；
recommend_rework；
needs_more_info；
cannot_decide；
blocked_by_policy。
```

规则：

```text
1. recommendation 不是最终授权；
2. blocked_by_policy 必须直接保持 blocked；
3. required_user_gate=true 时必须回到用户 Gate；
4. confidence 低时不得自动放行；
5. reasoning_summary 不得包含密钥；
6. 输出必须可 Trace。
```

---

## 18. Manual / Plan / Auto 下的处理差异

> 三模式的完整定义和权限关系见 `00-安全与权限总览.md` §7 和 `01-决策记录.md` D-023~D-027。本文只列出 Policy/Hook/Authorization 在各模式下的差异化处理规则。

### 18.1 Manual Mode

```text
1. 用户审核 Stage Plan、Task Plan、授权动作和阶段晋级；
2. 中高风险动作更倾向触发用户 Gate；
3. Policy blocked 仍直接阻断（D-031）；
4. 用户确认不覆盖 Policy；
5. 所有授权动作应可 Audit。
```

### 18.2 Plan Mode

```text
1. 用户审核 Stage Plan、Task Plan 或 Task Plan Batch；
2. 计划内动作由 Hook + Policy 检查并放行；
3. 越界、风险升级、不确定项回用户；
4. Plan Mode 第一版不默认加入 Authorization Agent（D-026）；
5. 用户仍审核阶段晋级（D-023）。
```

### 18.3 Auto Mode

```text
1. Agent 审核 Stage Plan 和 Task Plan；
2. 阶段内授权由 Hook + Policy + Auto Review Agent 处理；
3. 高风险、低置信度、冲突、超范围回用户；
4. L5 仍强制用户 Gate（D-034）；
5. 用户仍审核阶段晋级（D-023）。
```

---

## 19. 命令动作处理规范

> 命令风险分级和授权的完整定义见 `03-命令-Tool-MCP-Git-写盘授权规范.md`。本文只列出 Policy/Hook 层面的通用处理流程。

处理流程：

```text
command_request
  -> pre_command Hook
  -> Policy Check
  -> 风险分级
  -> allowed / requires_gate / blocked
  -> 执行命令
  -> post_command Hook
  -> Trace / Audit / error_ref
```

规则：

```text
1. command_summary 必须脱敏；
2. command_output 必须脱敏；
3. 命令风险不只看命令名（D-033）；
4. 高风险命令必须 Gate / Audit；
5. command_failed 必须关联 error_ref / trace_ref；
6. exit_code 不等于业务完成。
```

---

## 20. Tool / MCP 动作处理规范

> Tool/MCP 授权的完整定义见 `03-命令-Tool-MCP-Git-写盘授权规范.md`。

处理流程：

```text
resource_call_request
  -> pre_resource_call Hook
  -> Resource Check
  -> Policy Check
  -> Gate / Audit 判断
  -> Tool / MCP 执行
  -> post_resource_call Hook
  -> Trace / Audit / output_refs / error_ref
```

规则（D-040）：

```text
1. Tool / MCP active 不等于可直接执行；
2. read_only 资源不得执行；
3. 社区资源默认只读或待审核（D-061）；
4. 高风险资源必须 Gate / Audit；
5. 输出不得自动成为 Evidence validated；
6. 资源凭据不得展示。
```

---

## 21. 写盘 / Patch 处理规范

> 写盘授权的完整定义见 `03-命令-Tool-MCP-Git-写盘授权规范.md`。

状态建议：

```text
patch_generated；
patch_review_required；
patch_apply_waiting_gate；
patch_applied；
patch_apply_failed；
write_completed；
write_failed；
blocked_by_policy。
```

规则：

```text
1. patch_generated 不等于 patch_applied；
2. patch_applied 不等于 P5 验证通过；
3. 写盘范围必须明确；
4. 高风险写盘必须 Gate / Audit；
5. workspace 外写盘默认 blocked 或强 Gate；
6. 写盘结果必须 Trace。
```

---

## 22. Git 动作处理规范

> Git 授权的完整定义见 `03-命令-Tool-MCP-Git-写盘授权规范.md`。

操作分类：

```text
git_read：status / log / diff；
git_fetch：clone / fetch / pull；
git_local_write：checkout / add / commit；
git_history_rewrite：rebase / reset；
git_remote_write：push / remote merge。
```

规则：

```text
1. git_remote_write 默认高风险；
2. Git 凭据不得展示（D-032）；
3. 包含 token 的 Git URL 必须脱敏；
4. Git 目标仓库和分支必须摘要展示；
5. 高风险 Git 操作必须 Gate / Audit；
6. Git 操作失败不得泄露凭据。
```

---

## 23. 外部系统写操作处理规范

外部系统写操作包括：

```text
远程仓库写操作；
外部工单系统写操作；
云服务变更；
社区资源发布；
远程 MCP 写操作；
外部存储写入；
任何改变外部系统状态的操作。
```

规则：

```text
1. 外部系统写操作默认高风险；
2. 必须明确 external_system_ref；
3. 必须明确影响范围；
4. 必须 Gate / Audit；
5. 凭据不得展示；
6. 失败响应必须脱敏。
```

---

## 23A. 平台助手代操作处理规范（引用 D-073）

平台助手（AI 精灵 / 客服对话 Agent）是平台级使用助手，独立于 P0-P6 与 Agent/Skill 资源体系，不进 LangGraph 主编排。其"代用户操作平台"（建 Project/Run、写操作）属 **[规划/R-future]**，启用时**必须**纳入 Policy/Hook/Authorization 与风险分级，不得另开旁路。

安全红线（就地保留，不可只引用不写）：

```text
1. 平台助手代操作必须经 Hook + Policy Check + 风险分级，不得绕过既有授权与中断恢复（Gate/Resume）；
2. 涉及外部系统写 / 系统级 / 不可逆动作按 L3+ 处理——走用户确认或强制 Gate（L5 强制用户 Gate，D-034）；
3. blocked Policy 不得由助手批准继续；助手不得伪造用户授权或代替用户阶段晋级 Gate（D-023/D-031）；
4. 助手对话与代操作均纳入 Audit；
5. 模型连通性自测（切换模型/服务商）复用 ModelGateway，仅作用于助手会话，不得泄露 Key/Token/Secret/Password（D-032）。
```

> 详见 `01-决策记录.md` D-073；风险分级见 `02-风险分级与Gate策略.md`；密钥见 `04-密钥与配置脱敏规范.md`。本文只就地保留关键安全红线 + 引用，不复制 D-073 全文。

---

## 24. 凭据与脱敏处理规范

凭据处理流程（D-032）：

```text
credential_ref
  -> credential_status 查询
  -> redaction policy
  -> allowed / credential_missing / credential_invalid / redaction_required
  -> 使用引用执行动作
  -> Trace / Audit 只记录引用和状态
```

规则：

```text
1. 凭据不进入 Action Request 明文；
2. 凭据不进入模型上下文明文；
3. 凭据不进入 Memory（D-044）；
4. 凭据不进入 Trace / Audit 明文；
5. 凭据不在前端回显；
6. redaction_required 必须阻止原文展示。
```

---

## 25. Gate 与 Audit 写入规范

Gate 决策流程：

```text
Gate created
  -> waiting_decision
  -> user / authorized actor decision
  -> pre_gate_decision Hook
  -> Policy Check
  -> decision accepted / rejected / needs_more_info / rework
  -> post_gate_decision Hook
  -> Audit write
  -> Run / Stage 状态刷新
```

规则：

```text
1. Gate 决策必须写 Audit；
2. Policy blocked Gate 不得批准继续；
3. 用户阶段晋级 Gate 必须用户授权（D-023）；
4. Audit 写入失败必须显示 pending / error；
5. Gate resolved 必须以后端状态为准；
6. 前端不得本地伪造 Gate resolved。
```

---

## 26. Trace 写入规范

Trace 应记录：

```text
Action Request；
Hook Result；
Policy Check；
Authorization Recommendation；
Gate 状态变化；
Resource Call；
Model Call；
Command Execution；
File Write；
Git Operation；
Error；
Redaction Event。
```

规则：

```text
1. Trace 不得包含密钥明文（D-032）；
2. Trace 缺失必须可见（D-050）；
3. Trace 写入失败不得静默；
4. Trace 不等于 Evidence；
5. Trace 只证明过程存在，不证明结果正确；
6. Trace 详情按权限展示。
```

---

## 27. 前端展示规范

> 前端安全展示的完整要求见 `00-安全与权限总览.md` §22。

前端必须展示：

```text
policy_check_result；
blocked_by_policy；
requires_gate；
waiting_gate；
required_audit；
audit_pending；
trace_missing；
redaction_required；
credential_missing；
credential_invalid；
high_risk；
allowed_with_warning。
```

前端不得：

```text
1. 将 policy_blocked 显示为普通 warning；
2. 将 requires_gate 显示为普通 toast；
3. 提供"忽略 Policy 继续"；
4. 提供"跳过 Gate 继续"；
5. 将 Audit pending 显示为已审计；
6. 将 Trace missing 隐藏。
```

---

## 28. API 错误码建议

> 安全 API 错误码的完整清单见 `00-安全与权限总览.md` §23。本文列出 Policy/Hook/Authorization 层面新增或关注的错误码。

```text
policy_blocked；
gate_required；
gate_not_resolved；
authorization_required；
authorization_rejected；
authorization_needs_more_info；
credential_missing；
credential_invalid；
redaction_required；
permission_denied；
path_out_of_scope；
write_scope_required；
resource_read_only；
resource_blocked；
risk_level_required；
audit_required；
audit_write_failed；
trace_write_failed；
state_mismatch；
internal_error。
```

错误响应规则：

```text
1. 所有错误建议包含 request_id；
2. 可用时包含 trace_ref；
3. gate_required 包含 gate_ref；
4. policy_blocked 包含 policy_check_ref；
5. credential_invalid 不返回密钥片段；
6. internal_error 不暴露堆栈。
```

---

## 29. SSE / Event 建议

> 安全 SSE 事件的完整清单见 `00-安全与权限总览.md` §24。本文列出 Policy/Hook/Authorization 层面新增事件。

```text
policy_check_started；
policy_check_completed；
policy_blocked；
hook_result_created；
authorization_requested；
authorization_recommended；
authorization_rejected；
gate_created；
gate_waiting_decision；
gate_decided；
high_risk_gate_required；
command_waiting_gate；
resource_gate_required；
external_write_waiting_gate；
redaction_required；
audit_written；
audit_write_failed；
trace_written；
trace_write_failed；
resync_required。
```

事件规则：

```text
1. 事件不是唯一状态源；
2. 事件 payload 不得包含密钥（D-032）；
3. policy_blocked 不得触发自动重试；
4. gate_created 不得仅 toast 展示；
5. resync_required 必须重新查询对象；
6. mock SSE 必须显式标记。
```

---

## 30. 状态恢复规范

状态恢复必须重新查询（D-048）：

```text
Action 状态；
Policy Check 状态；
Gate 状态；
Authorization 状态；
Trace 状态；
Audit 状态；
Run / Stage 状态；
Workspace 状态。
```

规则：

```text
1. 浏览器缓存不能作为安全事实源；
2. SSE 事件缓存不能作为授权事实源；
3. 终端输出不能作为安全事实源；
4. Gate resolved 必须以后端查询为准；
5. Audit written 必须以后端查询为准；
6. resync_required 必须全量刷新相关对象。
```

---

## 31. R2 / R4 / R6 / R8 / R11 / R14 / R15 / R17 校准项

各阶段校准按 `06-R阶段总计划.md` 执行。

### 31.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. Policy / Hook / Authorization 边界是否清晰；
3. blocked / requires_gate / requires_audit / redaction_required 是否足够明确；
4. Manual / Plan / Auto 差异是否正确；
5. L5 Gate 暂定边界是否明确标注；
6. 是否仍无 Mission 产品层；
7. 是否重复上级事实，需要改为引用。
```

### 31.2 R4 API 与工程骨架校准

```text
1. Policy Check API 是否接入；
2. Hook 结果是否可记录；
3. Gate / Authorization API 是否接入；
4. Audit / Trace Writer 是否接入；
5. SSE 安全事件是否接入；
6. 错误码是否可映射前端。
```

### 31.3 R6 Agent / Skill / 资源基础校准

```text
1. Agent Definition 是否包含 Gate 触发规则；
2. Tool Policy 是否接入；
3. Resource Check 是否接入；
4. Authorization Agent 是否遵守 Policy 边界；
5. Case / Knowledge 只读规则是否接入。
```

### 31.4 R8 Workspace 真实化校准

```text
1. 文件读写 Hook 是否接入；
2. Workspace 边界 Policy 是否接入；
3. 凭据脱敏是否接入；
4. 前端 Gate / Policy / Audit 提示是否可见；
5. 状态恢复是否不依赖前端缓存。
```

### 31.5 R11 P4 执行链路校准

```text
1. 命令 Hook 是否接入；
2. 命令风险分级是否生效；
3. Tool / MCP Gate 是否生效；
4. Patch apply Gate 是否生效；
5. 外部写操作 Gate / Audit 是否生效。
```

### 31.6 R14 / R15 增强资源校准

```text
R14：远程资源调用 Policy / Hook / Authorization 校准；
R15：社区资源审核、许可、只读默认、可执行升级策略校准。
```

### 31.7 R17 全量联调校准

```text
1. Policy blocked 是否不可绕过；
2. Gate 是否不被 toast 化；
3. Audit / Trace 缺失是否可见；
4. 脱敏是否覆盖前端、日志、Trace、导出；
5. 用户是否能理解每个安全阻塞的下一步。
```

---

## 32. Policy / Hook / Authorization 红线

以下红线在 rebuild 当前版本任何阶段不得违反（来源：D-030~D-034、D-023、AGENTS.md §18）：

```text
1. 不得允许 Agent 绕过 Hook；
2. 不得允许 Agent 或前端绕过 Policy；
3. 不得允许 Authorization Agent 批准 Policy 禁止项；
4. 不得允许 policy_blocked 进入"批准继续"流程；
5. 不得将 unknown / error 默认按 allowed 处理；
6. 不得将 Gate 写成普通提醒后继续执行；
7. 不得将 Audit 写入等同于授权通过；
8. 不得将 Trace 写入等同于结果可信；
9. 不得将 Auto Mode 解释为所有动作自动放行；
10. 不得将静态命令清单当作唯一风险依据；
11. 不得在日志、前端、Trace、截图或交接材料中泄露 Key / Token / Secret / Password；
12. 不得将凭据明文传入模型、Memory、Trace 或 Audit；
13. 不得将模型输出、资源输出或 Artifact 显示为 Evidence validated；
14. 不得将社区资源默认显示为可执行；
15. 不得引入 Mission 产品层；
16. 不得使用旧 Phase / 旧 F0-F6 / 旧 S0-S7 作为当前主流程；
17. 不得在长期 API、DB、路由、组件、状态字段中固化 V26.1；
18. 不得把 R1 正式候选（建议契约）伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 33. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确规范目标；
2. 明确三层职责边界；
3. 明确总体调用链；
4. 明确 Action Request、Hook Result、Policy Check 输入 / 输出契约；
5. 明确 Policy 优先级 P0-P9；
6. 明确 Forbidden / Redaction / Boundary / Risk / Gate / Audit Policy 各类策略；
7. 明确 Authorization Agent 输入 / 输出契约；
8. 明确 Manual / Plan / Auto 处理差异；
9. 明确命令、Tool / MCP、写盘 / Patch、Git、外部系统写操作处理规范；
10. 明确凭据与脱敏处理规范；
11. 明确 Gate / Audit / Trace 写入规范；
12. 明确前端展示规范；
13. 明确 API 错误码建议；
14. 明确 SSE / Event 建议；
15. 明确状态恢复规范；
16. 明确 R2 / R4 / R6 / R8 / R11 / R14 / R15 / R17 校准项；
17. 明确 Policy / Hook / Authorization 红线；
18. 未新增产品决策；
19. 未引入 Mission 产品层；
20. 未固定最终实现；
21. 与 00-安全与权限总览 无冲突，引用关系清晰。
```
