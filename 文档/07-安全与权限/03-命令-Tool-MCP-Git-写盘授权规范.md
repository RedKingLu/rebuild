# 03-命令-Tool-MCP-Git-写盘授权规范

> 文档路径：`文档/07-安全与权限/03-命令-Tool-MCP-Git-写盘授权规范.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/03-命令-Tool-MCP-Git-写盘授权规范.md`
> 最后更新时间：2026-06-24
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`07-安全与权限/` 专题规范之四。定义 rebuild 当前版本命令、Tool、MCP、Git、写盘与 Patch 六类动作的授权规范——动作分类、授权链路、前置检查字段、风险判断、Manual/Plan/Auto 差异、输出处理、Gate 卡片要求、Audit/Trace 要求和 R2/R4/R6/R8/R11/R14/R15/R17 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-023~D-027、D-030~D-034、D-036、D-040、D-043/D-044、D-048、D-050、D-061、D-063、D-065、D-066）、`文档/00-项目治理/02-术语表.md`（第七部分）、`文档/07-安全与权限/00-安全与权限总览.md`（§13-§16 命令/Tool/MCP/写盘/Git 速查）、`文档/07-安全与权限/01-Policy-Hook-Authorization规范.md`（§3 调用链 + §19-§23 动作处理流程 + §25-§26 Gate/Audit/Trace 写入）、`文档/07-安全与权限/02-风险分级与Gate策略.md`（§14-§17 命令/写盘/Git/Tool-MCP 风险策略 + §11 三模式 Gate 差异）。
> 同级文档：`00-安全与权限总览.md`（已落位）+ `01-Policy-Hook-Authorization规范.md`（已落位）+ `02-风险分级与Gate策略.md`（已落位）+ 本文（03）
> 重要边界：本文是六类动作的操作级授权详述源。授权链路完整定义见 01 规范 §3，风险分级策略见 02 规范 §14-§17，三模式差异见 02 规范 §11，Gate/Audit/Trace 写入规范见 01 规范 §25-§26。本文不替代具体命令执行器、ExecutionProvider、Tool Runtime、MCP Runtime、Git Adapter、文件系统服务、Policy DSL、Gate API、审计存储或代码实现。本文为 R1 正式候选（建议契约），R2/R4/R6/R8/R11/R14/R15/R17 需按真实实现和联调结果校准。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开命令、Tool、MCP、Git、写盘与 Patch 的操作级授权规范，不重新定义上级事实。授权链路完整定义见 `01-Policy-Hook-Authorization规范.md` §3，风险分级策略见 `02-风险分级与Gate策略.md` §14-§17，三模式差异见 `02-风险分级与Gate策略.md` §11，Gate/Audit/Trace 写入规范见 `01-Policy-Hook-Authorization规范.md` §25-§26。本文引用而不复述（AGENTS.md §8.2.1 单一事实源原则）。

本文必须遵守（来源标注于每条规则）：

```text
1. 安全机制采用 Hook + Policy + Security / Authorization Agent 三层（D-030）；
2. Policy 是硬约束，优先级高于 Agent 判断（D-031）；
3. Security / Authorization Agent 不能批准 Policy 禁止的动作（D-031）；
4. 命令、Tool、MCP、写盘、Git 操作等必须先映射到风险级别，再按 Manual / Plan / Auto 策略处理（D-033）；
5. Auto Mode 下低风险命令是否允许 Agent 审核后自动放行，应按照动作所属风险级别判断，而不是硬编码具体命令（D-033）；
6. L5 高风险动作暂定全部强制用户 Gate（D-034）；
7. P 阶段晋级 Gate 必须用户授权（D-023）；
8. Tool / MCP / 资源调用必须记录来源、风险、权限和使用 Trace（D-040）；
9. 社区资源默认只读参考，不得默认执行（D-061）；
10. 每个 Project 必须有独立 Workspace，文件、产物、Evidence、runs、任务状态按 project_id 隔离（D-046）；
11. 容器、终端或前端 UI 不能作为状态源（D-048）；
12. 密钥与配置必须脱敏，不得泄露 Key / Token / Secret / Password（D-032）；
13. 当前版本不设独立 Mission 产品层（D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 用固定命令白名单替代上下文风险判断；
2. 将命令 exit_code=0 显示为业务完成；
3. 将 Tool / MCP active 显示为可直接执行；
4. 将 Patch generated 显示为 Patch applied；
5. 将 Patch applied 显示为 P5 验证通过；
6. 将 Git push / 外部系统写操作自动放行；
7. 将 Workspace 外写盘静默允许；
8. 将 Gate 显示为普通 toast 后继续执行；
9. 将 Policy blocked 提供为可批准；
10. 将 Audit 写入等同于授权通过；
11. 将模型输出、资源输出或 Artifact 显示为 Evidence validated；
12. 泄露 Key / Token / Secret / Password；
13. 引入 Mission 产品层或 mission_id 长期字段；
14. 把 R1 正式候选（建议契约）伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 授权目标

命令、Tool、MCP、Git、写盘授权的目标是：

```text
1. 所有会读取、执行、写入或影响外部系统的动作都有明确上下文；
2. 所有动作先做风险判断，再决定是否执行；
3. 高风险动作不会被 Agent 静默执行；
4. Policy 禁止项不可被用户、Agent 或前端绕过；
5. Gate 决策和高风险动作可审计；
6. 命令输出、Tool 输出、MCP 输出、Git 错误、文件内容默认脱敏；
7. Workspace 边界和 Project 隔离不被破坏；
8. 执行产物与 Evidence validated 明确区分；
9. 用户能在前端理解"为什么暂停、能不能继续、继续会影响什么"。
```

本文关注"动作授权"，不负责定义迁移流程目标、具体执行算法或最终验收判定。

---

## 2. 动作类型总览

R1 授权覆盖以下动作类型：

```text
command_read：只读命令；
command_execute：一般命令执行；
command_write：会写盘或改变环境的命令；
tool_call：Tool 调用；
mcp_call：MCP 调用；
file_read：文件读取；
file_write：文件写入；
patch_generate：Patch 生成；
patch_apply：Patch 应用；
git_read：Git 只读；
git_fetch：Git 获取 / clone / pull；
git_local_write：Git 本地写入；
git_history_rewrite：Git 历史改写；
git_remote_write：Git 远端写入；
external_write：外部系统写操作；
credential_access：凭据访问或验证；
resource_call：平台资源调用。
```

规则：

```text
1. action_type 必须明确；
2. 动作类型决定初始风险，但不决定最终风险（D-033）；
3. 最终风险还要结合路径、参数、影响范围、执行模式、计划范围、可回滚性和凭据情况；
4. unknown action_type 不得默认执行；
5. action_type 变化必须重新评估风险。
```

---

## 3. 授权链路总览

> 完整调用链定义见 `01-Policy-Hook-Authorization规范.md` §3。本文只列出六类动作在授权链路中的关键节点。

```text
Action Request
  -> pre_* Hook（详见 01 §5 触发点列表）
  -> Risk Assessment（详见 02 §3-§9 风险分级）
  -> Policy Check（详见 01 §7-§8 契约）
  -> Resource / Credential / Workspace Check（本文 §8-§17）
  -> Gate / Authorization 判断（详见 01 §14-§17 + 02 §10-§11）
  -> 执行或阻断
  -> post_* Hook
  -> Trace / Audit / Error / Output Ref（详见 01 §25-§26）
  -> 前端刷新状态
```

链路规则：

```text
1. 执行前必须完成风险判断和 Policy Check；
2. blocked 不执行（D-031）；
3. requires_gate 未决策不执行；
4. redaction_required 不展示原文（D-032）；
5. 执行后必须写 Trace；
6. 高风险动作必须写 Audit（D-034）；
7. 前端不得本地伪造授权完成（D-050）。
```

---

## 4. Action Request 字段

> Action Request 完整契约见 `01-Policy-Hook-Authorization规范.md` §4。本文列出六类动作特有的补充字段。

建议字段：

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
plan_scope_ref；
command_summary；
command_args_summary；
tool_ref；
mcp_ref；
resource_ref；
file_refs；
write_scope；
patch_ref；
git_operation_type；
git_target_ref；
external_system_ref；
credential_refs；
expected_output_refs；
trace_parent_ref；
request_id。
```

规则（D-032）：

```text
1. command_summary 和 command_args_summary 必须脱敏；
2. credential_refs 只能是引用；
3. write_scope 必须尽量具体；
4. git_target_ref 不得包含 token；
5. external_system_ref 必须摘要说明目标系统；
6. file_refs 不得包含可还原密钥片段；
7. Action Request 不得包含 Secret 明文。
```

---

## 5. 命令授权总规则

> 命令风险分级策略见 `02-风险分级与Gate策略.md` §14。本文列出命令授权特有的判断维度和输出处理。

命令授权不使用固定白名单作为唯一依据（D-033）。

判断维度：

```text
命令类别；
参数；
路径；
目标文件；
是否写盘；
是否外部写；
是否涉及凭据；
是否可回滚；
是否在 Workspace 内；
是否在已批准计划范围内；
是否影响 Evidence / Trace / Audit；
执行模式。
```

通用规则：

```text
1. 只读命令通常低风险，但读取敏感文件会升级；
2. 写盘命令必须检查 write_scope；
3. 外部系统写命令默认高风险；
4. 涉及凭据的命令必须脱敏；
5. 命令输出必须脱敏；
6. 命令执行必须 Trace；
7. 高风险命令必须 Gate / Audit；
8. exit_code=0 不等于业务完成。
```

---

## 6. 命令风险示例

R1 示例，仅作建议，不作为最终硬编码清单（D-033）：

```text
查看当前目录、普通源码、git diff：通常 L1；
运行本地测试、构建：通常 L2；
写入单个受控文件：通常 L2；
批量重写、覆盖、删除文件：通常 L4；
远端写入、发布、推送：通常 L3-L5；
触碰密钥、env、连接串：redaction_required 或 blocked；
删除 Evidence / Trace / Audit：通常 forbidden 或 L5。
```

使用规则：

```text
1. 示例不能替代 Policy；
2. 示例不能替代上下文判断；
3. 命令名相同但路径不同，风险可能不同；
4. 命令参数变化必须重新判断；
5. 工程实现可建立参考矩阵，但不得把矩阵当成唯一裁决。
```

---

## 7. 命令输出处理

命令输出建议只通过 output_ref 展示。

输出字段：

```text
execution_id；
command_summary；
output_ref；
stdout_summary；
stderr_summary；
exit_code；
redaction_status；
trace_ref；
audit_ref；
error_ref。
```

规则：

```text
1. stdout / stderr 默认脱敏；
2. 包含密钥时必须 redaction_required（D-032）；
3. 输出不自动成为 Artifact；
4. 输出不自动成为 Evidence validated；
5. 输出过长时展示摘要和引用；
6. 输出详情按权限展示。
```

---

## 8. Tool 授权

Tool 调用必须走资源调用流程（D-040）。

前置检查字段：

```text
tool_ref；
tool_status；
source_type；
review_status；
risk_level；
permission_scope；
input_contract；
output_contract；
write_scope；
gate_policy；
trace_policy；
audit_policy。
```

规则：

```text
1. Tool active 不等于可直接执行；
2. Tool 调用必须匹配 input_contract；
3. Tool 输出必须匹配 output_contract；
4. Tool 写盘必须检查 write_scope；
5. 高风险 Tool 必须 Gate / Audit；
6. 社区 Tool 默认只读或待审核（D-061）；
7. Tool 输出不自动成为 Evidence validated；
8. Tool 凭据不得展示。
```

---

## 9. MCP 授权

MCP 调用必须按资源风险处理（D-040）。

前置检查字段：

```text
mcp_ref；
mcp_server_ref；
source_type；
connection_status；
credential_status；
capability_type；
permission_scope；
external_effect；
risk_level；
gate_policy；
audit_policy；
trace_policy。
```

规则：

```text
1. MCP connected 不等于所有能力可执行；
2. MCP capability 必须逐项授权；
3. 远程 MCP 写操作默认高风险；
4. MCP 凭据不得展示；
5. MCP 输出必须脱敏；
6. 高风险 MCP 调用必须 Gate / Audit；
7. MCP 调用失败不得泄露连接信息或密钥。
```

---

## 10. Tool / MCP 输出处理

输出可能形成 output_ref、Artifact 或 Evidence candidate，但不得自动成为 Evidence validated。

输出字段建议：

```text
resource_call_id；
resource_ref；
output_refs；
artifact_refs；
evidence_candidate_refs；
redaction_status；
risk_level；
trace_ref；
audit_ref；
error_ref。
```

规则：

```text
1. output_refs 不等于 Artifact accepted；
2. artifact_refs 不等于 Evidence；
3. evidence_candidate_refs 不等于 Evidence validated；
4. 输出摘要必须脱敏；
5. 高风险输出必须可追溯；
6. 输出用于验证时必须进入 Evidence 验证流程。
```

---

## 11. 文件读取授权

文件读取需区分普通读取、敏感读取、只读参考读取和越界读取。

规则：

```text
1. Workspace 内普通源码读取通常 L1；
2. 敏感文件读取必须脱敏（D-032）；
3. 历史归档资料默认只读参考；
4. Workspace 外路径读取需 Boundary Policy（详见 01 §12）；
5. 读取结果进入模型上下文前必须检查敏感内容；
6. file_read 必须可 Trace，具体粒度由 R2/R8 校准。
```

---

## 12. 写盘授权总规则

> 写盘风险分级策略见 `02-风险分级与Gate策略.md` §15。本文列出写盘授权特有的 write_scope 字段和状态。

写盘必须明确 write_scope。

write_scope 建议包含：

```text
目标路径；
目标文件身份；
写入类型；
覆盖策略；
影响文件数量；
是否可回滚；
是否涉及敏感文件；
是否在 Workspace 内；
是否影响 Evidence / Trace / Audit。
```

规则：

```text
1. Workspace 内受控写入可按风险处理；
2. Workspace 外写盘默认 blocked 或强 Gate；
3. 历史归档目录默认只读；
4. 参考资料默认只读；
5. 高风险写盘必须 Gate / Audit；
6. 写盘结果必须 Trace；
7. 写盘失败不得默认成功。
```

---

## 13. 写盘状态

写盘状态建议：

```text
write_planned；
write_waiting_policy；
write_waiting_gate；
write_running；
write_completed；
write_failed；
write_blocked_by_policy；
write_reverted；
write_unknown。
```

状态规则：

```text
1. write_planned 不等于已写入；
2. write_completed 不等于阶段完成；
3. write_failed 必须 error_ref；
4. write_blocked_by_policy 不可批准继续；
5. write_unknown 不得显示为成功；
6. write_reverted 必须可 Trace。
```

---

## 14. Patch 授权

Patch 必须区分生成、预览、审核、应用、验证、回滚。

Patch 状态建议：

```text
patch_generated；
patch_previewed；
patch_review_required；
patch_apply_waiting_gate；
patch_applying；
patch_applied；
patch_apply_failed；
patch_reverted；
patch_rejected；
patch_superseded。
```

规则：

```text
1. patch_generated 不等于 patch_applied；
2. patch_applied 不等于 P5 验证通过；
3. 大范围 patch_apply 默认高风险；
4. patch_apply 必须检查 write_scope；
5. 高风险 Patch 必须 Gate / Audit；
6. Patch 内容不得泄露密钥；
7. Patch 回滚必须 Trace。
```

---

## 15. Git 授权

> Git 风险分级策略见 `02-风险分级与Gate策略.md` §16。本文列出 Git 操作类型分类和凭据处理规则。

Git 操作需要结合目标仓库、分支、远端影响、凭据和工作区状态判断。

操作类型：

```text
git_read：status / log / diff；
git_fetch：clone / fetch / pull；
git_local_write：checkout / add / commit；
git_history_rewrite：reset / rebase；
git_merge：merge；
git_remote_write：push / remote merge / release；
git_config_change：配置变更。
```

规则：

```text
1. git_read 通常低风险，但需脱敏；
2. git_fetch 需检查凭据状态；
3. git_local_write 需计划范围检查；
4. git_history_rewrite 高风险；
5. git_remote_write 默认高风险，通常 Gate / Audit；
6. Git URL 不得包含明文 token（D-032）；
7. Git 操作失败不得泄露凭据。
```

---

## 16. Git 凭据与远端写入

Git 凭据只以引用方式参与（D-032）。

可展示：

```text
git_repo_ref；
branch；
commit；
credential_status；
credential_ref；
redacted；
permission_scope；
trace_ref；
audit_ref。
```

不得展示：

```text
Git token；
GitHub token；
包含用户名密码的 URL；
含 token 的 remote；
私有仓库敏感错误详情；
可还原密钥片段。
```

规则：

```text
1. git_remote_write 必须展示影响范围；
2. git_remote_write 必须 Gate / Audit；
3. 私有仓库权限失败不得泄露敏感信息；
4. credential_invalid 不返回凭据片段；
5. Git 凭据不得进入模型上下文；
6. Git 凭据不得进入 Memory（D-044）。
```

---

## 17. 外部系统写操作授权

> 外部系统写操作风险策略见 `02-风险分级与Gate策略.md` §18。本文列出授权特有的 external_system_ref 要求。

外部系统写操作包括：

```text
Git 远端写入；
远程 MCP 写操作；
外部 API 状态变更；
工单系统写入；
云服务变更；
社区资源发布；
外部存储写入；
任何改变 Project Workspace 外部系统状态的操作。
```

规则：

```text
1. external_system_ref 必须明确；
2. 影响范围必须明确；
3. 默认 L3-L5；
4. 通常必须 Gate / Audit；
5. 凭据不得展示；
6. mock 外部写不得伪装为真实写；
7. 执行失败必须脱敏。
```

---

## 18. Manual / Plan / Auto 授权差异

> 三模式完整定义见 `00-安全与权限总览.md` §7 和 `01-决策记录.md` D-023~D-027。三模式 Gate 差异见 `02-风险分级与Gate策略.md` §11。本文只列出六类动作在各模式下的授权摘要。

### 18.1 Manual Mode

```text
L0 / L1：可查看或轻提示；
L2：写盘和执行倾向用户确认；
L3-L5：必须 Gate；
用户批准不覆盖 Policy（D-031）；
高风险动作必须 Audit。
```

### 18.2 Plan Mode

```text
已批准计划范围内 L1 / L2：由 Hook + Policy 放行；
越界、风险升级、不确定项：回用户；
L3：通常 Gate 或 Authorization 评审；
L4-L5：必须 Gate / Audit；
用户仍审核 P 阶段晋级（D-023）。
```

### 18.3 Auto Mode

```text
L1：可由 Agent 审核后自动执行；
L2：需 Hook + Policy + Auto Review 判断；
L3：高风险、低置信度、冲突、超范围回用户；
L4-L5：必须 Gate / Audit；
用户仍审核 P 阶段晋级（D-023）。
```

---

## 19. Authorization Agent 参与条件

> Authorization Agent 完整边界见 `00-安全与权限总览.md` §6 和 `01-Policy-Hook-Authorization规范.md` §16-§17。本文列出六类动作场景下的参与条件。

Authorization Agent 可参与：

```text
风险原因解释；
计划范围越界判断辅助；
低置信度动作建议；
是否升级 Gate 建议；
是否需要补充信息；
是否建议返工；
是否建议拒绝。
```

不得参与（D-031）：

```text
1. 批准 Policy blocked；
2. 代替用户批准 P 阶段晋级；
3. 代替用户批准 L5；
4. 忽略 Gate；
5. 修改风险级别为低风险以绕过 Gate；
6. 生成密钥明文。
```

---

## 20. Gate 卡片要求

> Gate 决策流程和 Audit 写入规范见 `01-Policy-Hook-Authorization规范.md` §25。本文列出六类动作相关 Gate 卡片的展示字段。

命令 / Tool / MCP / Git / 写盘相关 Gate 卡片必须展示：

```text
gate_id；
gate_type；
action_type；
risk_level；
risk_reasons；
actor_ref；
project_id；
run_id；
stage；
write_scope；
resource_ref；
command_summary；
git_target_ref；
external_system_ref；
policy_check_ref；
trace_ref；
audit_ref；
options；
recommended_option；
next_actions。
```

规则：

```text
1. Gate 卡片不得显示密钥（D-032）；
2. 高风险 Gate 必须醒目；
3. Policy blocked 不显示批准按钮；
4. 用户决策必须写 Audit；
5. Gate 决策后必须刷新动作状态；
6. Gate 关闭不等于批准或拒绝。
```

---

## 21. Audit 要求

> Audit 完整规范见 `01-Policy-Hook-Authorization规范.md` §15 和 §25。本文列出六类动作特有的必须 Audit 场景。

必须 Audit 的场景：

```text
P 阶段晋级 Gate（D-023）；
L5 动作（D-034）；
高风险写盘；
Patch apply；
Git remote write；
外部系统写操作；
高风险 Tool / MCP；
Policy blocked 高风险事件；
用户接受 Evidence Gap 风险；
凭据状态变更；
权限变更。
```

Audit 规则：

```text
1. Audit 不得包含密钥明文（D-032）；
2. Audit 写入失败必须显示 pending / error；
3. Audit 缺失不得隐藏（D-050）；
4. Audit 不等于授权本身；
5. Audit 详情按权限展示；
6. 高风险动作没有 Audit 不得静默通过。
```

---

## 22. Trace 要求

> Trace 完整规范见 `01-Policy-Hook-Authorization规范.md` §26。本文列出六类动作特有的必须 Trace 场景。

必须 Trace 的场景：

```text
命令执行；
Tool 调用；
MCP 调用；
文件写入；
Patch 生成 / 应用；
Git 操作；
外部系统写操作；
Policy Check；
Gate 决策；
Audit 写入；
redaction_required；
错误和阻断。
```

Trace 规则：

```text
1. Trace 不得包含密钥明文（D-032）；
2. Trace 缺失必须可见（D-050）；
3. Trace 写入失败不得静默；
4. Trace 不等于 Evidence；
5. Trace 只解释过程，不证明结果正确；
6. Trace 详情按权限展示。
```

---

## 23. 前端展示要求

> 前端安全展示完整要求见 `00-安全与权限总览.md` §22。本文列出六类动作特有的前端展示要求。

前端必须展示：

```text
action_type；
risk_level；
risk_reasons；
requires_gate；
waiting_gate；
blocked_by_policy；
redaction_required；
credential_missing；
credential_invalid；
write_scope；
external_effect；
trace_ref；
audit_ref；
error_ref。
```

前端不得：

```text
1. 将 Gate 变成 toast；
2. 将 blocked_by_policy 变成普通 warning；
3. 将 high_risk 自动折叠；
4. 将 output_ref 显示为 Evidence validated；
5. 将 patch_applied 显示为验证通过；
6. 将 git push 成功显示为交付完成；
7. 将 terminal 输出作为状态源。
```

---

## 24. API 字段建议

授权检查 API 字段建议（R1 建议契约，R4 按真实实现校准）：

```text
authorization_check_id；
action_id；
action_type；
project_id；
run_id；
stage；
node_id；
execution_mode；
risk_level；
risk_reasons；
policy_check_ref；
allowed；
blocked；
requires_gate；
gate_type；
gate_ref；
requires_audit；
requires_trace；
redaction_required；
credential_status；
write_scope；
external_effect；
allowed_scope；
blocked_scope；
next_actions；
trace_ref；
request_id。
```

规则：

```text
1. R1 字段为建议契约；
2. R2/R4 按真实实现校准；
3. blocked=true 时不得 allowed=true；
4. requires_gate=true 时应返回 gate_type 或 gate_ref；
5. redaction_required=true 时不返回原文；
6. credential_status 不返回密钥片段。
```

---

## 25. API 错误码建议

> 安全 API 错误码完整清单见 `00-安全与权限总览.md` §23。本文列出六类动作特有的错误码。

```text
command_blocked_by_policy；
command_requires_gate；
command_output_redacted；
tool_read_only；
tool_blocked_by_policy；
tool_requires_gate；
mcp_blocked_by_policy；
mcp_requires_gate；
file_write_out_of_scope；
file_write_requires_gate；
patch_apply_requires_gate；
patch_apply_failed；
git_credential_missing；
git_credential_invalid；
git_remote_write_requires_gate；
git_operation_blocked；
external_write_requires_gate；
credential_redaction_required；
audit_write_failed；
trace_write_failed；
state_mismatch；
internal_error。
```

规则：

```text
1. 错误响应应包含 request_id；
2. 可用时包含 trace_ref；
3. gate_required 类错误应包含 gate_ref；
4. policy blocked 类错误应包含 policy_check_ref；
5. credential_invalid 不返回密钥片段；
6. internal_error 不暴露堆栈。
```

---

## 26. SSE / Event 建议

> 安全 SSE 事件完整清单见 `00-安全与权限总览.md` §24。本文列出六类动作特有的事件。

```text
command_started；
command_waiting_gate；
command_completed；
command_failed；
command_blocked_by_policy；
tool_call_started；
tool_call_waiting_gate；
tool_call_completed；
tool_call_failed；
mcp_call_started；
mcp_call_waiting_gate；
mcp_call_completed；
mcp_call_failed；
file_write_waiting_gate；
file_write_completed；
file_write_failed；
patch_generated；
patch_apply_waiting_gate；
patch_applied；
patch_apply_failed；
git_operation_waiting_gate；
git_operation_completed；
git_operation_failed；
external_write_waiting_gate；
external_write_completed；
external_write_failed；
redaction_required；
audit_written；
trace_written；
resync_required。
```

规则：

```text
1. 事件只作为刷新提示；
2. 事件 payload 不得包含密钥（D-032）；
3. waiting_gate 不得只 toast；
4. blocked_by_policy 不得自动重试；
5. completed 不等于业务完成；
6. resync_required 必须重新查询状态。
```

---

## 27. 状态恢复要求

状态恢复必须重新查询（D-048）：

```text
action；
command_execution；
tool_call；
mcp_call；
file_write；
patch；
git_operation；
external_write；
gate；
policy_check；
audit；
trace；
run；
stage；
workspace。
```

规则：

```text
1. 浏览器缓存不能作为授权事实源；
2. SSE 事件缓存不能作为动作完成事实源；
3. 终端输出不能作为业务状态源；
4. Gate resolved 必须以后端查询为准；
5. Audit written 必须以后端查询为准；
6. resync_required 必须全量刷新相关对象。
```

---

## 28. R2 / R4 / R6 / R8 / R11 / R14 / R15 / R17 校准项

各阶段校准按 `06-R阶段总计划.md` 执行。

### 28.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. 命令 / Tool / MCP / Git / 写盘动作分类是否需要调整；
3. 风险示例是否过度具体，需要改为引用；
4. Gate / Audit / Trace 要求是否完整；
5. 是否仍无 Mission 产品层；
6. 是否重复上级事实，需要改为引用。
```

### 28.2 R4 API 与工程骨架校准

```text
1. authorization_check API 是否接入；
2. command / tool / mcp / git / file write API 是否有统一安全字段；
3. policy_check_ref / gate_ref / trace_ref / audit_ref 是否接入；
4. 错误码是否能映射前端；
5. SSE 事件是否接入。
```

### 28.3 R6 Agent / Skill / 资源基础校准

```text
1. Tool / MCP 注册是否含 risk_level / permission_scope；
2. Resource Check 是否接入；
3. Case / Knowledge 只读策略是否实现；
4. Expert Agent 输出动作是否走授权检查；
5. 确定性转换资源 dry-run / apply 是否区分。
```

### 28.4 R8 Workspace 真实化校准

```text
1. 文件读写 Hook 是否接入；
2. Workspace 边界是否接入；
3. Patch preview / apply 是否接入；
4. 敏感文件脱敏是否实现；
5. 前端是否正确展示写盘风险和 Gate。
```

### 28.5 R11 P4 执行链路校准

```text
1. 命令执行是否真实接入 Hook / Policy；
2. Tool / MCP 调用是否真实接入 Gate；
3. Git 高风险操作是否真实 Gate / Audit；
4. 外部系统写操作是否真实 Gate / Audit；
5. 执行输出是否脱敏。
```

### 28.6 R14 / R15 增强资源校准

```text
R14：远程资源调用、远程 MCP、外部系统写操作授权校准；
R15：社区资源从只读参考到可执行资源的审核和授权升级策略校准。
```

### 28.7 R17 全量联调校准

```text
1. 用户是否能理解每类动作风险；
2. Gate 是否不被 toast 化；
3. Policy blocked 是否不可绕过；
4. 命令 / Tool / MCP / Git / 写盘输出是否脱敏；
5. 前端是否停止用 mock 判断授权完成。
```

---

## 29. 命令 / Tool / MCP / Git / 写盘授权红线

以下红线在 rebuild 当前版本任何阶段不得违反（来源：D-023、D-031、D-032、D-033、D-034、D-044、D-048、D-050、D-061）：

```text
1. 不得用固定命令白名单替代上下文风险判断；
2. 不得将命令 exit_code=0 显示为业务完成；
3. 不得将 Tool / MCP active 显示为可直接执行；
4. 不得将 Patch generated 显示为 Patch applied；
5. 不得将 Patch applied 显示为 P5 验证通过；
6. 不得将 Git push / 外部系统写操作自动放行；
7. 不得将 Workspace 外写盘静默允许；
8. 不得将 Gate 显示为普通 toast 后继续执行；
9. 不得将 Policy blocked 提供为可批准；
10. 不得将 Audit 写入等同于授权通过；
11. 不得将模型输出、资源输出或 Artifact 显示为 Evidence validated；
12. 不得泄露 Key / Token / Secret / Password；
13. 不得将凭据明文传入模型、Memory、Trace 或 Audit；
14. 不得将社区资源默认显示为可执行；
15. 不得用浏览器缓存、SSE 缓存、终端输出作为授权事实源；
16. 不得引入 Mission 产品层；
17. 不得使用旧 Phase / 旧 F0-F6 / 旧 S0-S7 作为当前主流程；
18. 不得在长期 API、DB、路由、组件、状态字段中固化 V26.1；
19. 不得把 R1 正式候选（建议契约）伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 30. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确授权目标；
2. 明确 17 种动作类型总览；
3. 明确授权链路关键节点；
4. 明确 Action Request 字段（~23 个）；
5. 明确命令授权总规则、风险示例、输出处理；
6. 明确 Tool 授权（13 项前置检查）和 MCP 授权（13 项前置检查）；
7. 明确 Tool / MCP 输出处理；
8. 明确文件读取、写盘授权（write_scope 9 项 + 状态 9 种）、Patch 授权（状态 10 种）；
9. 明确 Git 授权（7 种操作类型）和 Git 凭据规则；
10. 明确外部系统写操作授权；
11. 明确 Manual / Plan / Auto 授权差异；
12. 明确 Authorization Agent 参与条件；
13. 明确 Gate 卡片字段（~19 个）、Audit（11 场景）、Trace（12 场景）要求；
14. 明确前端展示（14 项）、API 字段（27 个）、错误码（22 种）、SSE 事件（30 种）、状态恢复；
15. 明确 R2 / R4 / R6 / R8 / R11 / R14 / R15 / R17 校准项；
16. 明确授权红线（19 条）；
17. 未新增产品决策；
18. 未引入 Mission 产品层；
19. 未固定最终实现；
20. 与 00/01/02 引用关系清晰，无冲突。
```
