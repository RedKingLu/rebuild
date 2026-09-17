# 06-外部编程Agent接入流程

> 文档路径：`文档/03-流程与运行时/06-外部编程Agent接入流程.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/外部编程Agent接入流程.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本外部编程 Agent 接入流程，覆盖 OpenCode、外部编程 Agent、Tool、MCP、Expert Agent、确定性转换器等外部执行资源的接入、注册、上下文传递、权限边界、执行动作、Patch / 文件写入 / 命令执行、Artifact / Evidence / Trace / Audit、Policy / Gate、失败处理和 R 阶段落地关系。
> 上级依据：`文档/00-项目治理/01-决策记录.md`、`文档/00-项目治理/文档地图.md`、`文档/02-架构设计/00-架构总纲.md`、`文档/02-架构设计/05-集成与外部执行架构.md`、`文档/03-流程与运行时/00-流程与运行时总览.md`、`文档/03-流程与运行时/05-项目接入与源码同步流程.md`、`文档/04-模型与资源/`、`文档/05-API与集成契约/`、`文档/06-UX与前端/`、`文档/07-安全与权限/`、`文档/08-测试与验收/`、`文档/09-参考索引/`。
> 重要边界：本文是流程与运行时层专题文档，不替代 Agent Definition Contract、Resource Registry 实现、OpenCode 适配器、Tool / MCP 协议实现、ExecutionProvider 实现、安全策略、API 契约或验收报告。本文为 R1 建议契约，R2 需校准目录与术语，R6/R8/R11/R12 需按真实实现更新。
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守当前文档事实源层级：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只定义外部编程 Agent 接入与调用流程，不重新定义 Agent 总体架构、平台资源最终实现或具体外部工具协议。

本文必须遵守：

```text
1. 默认少 Agent、多 Skill，不为相似职责过度拆分 Agent；
2. Agent 切换必须由条件触发；
3. 每个 Agent 必须有结构化定义，至少包含职责、禁止职责、Context Recipe、Memory Policy、Model Policy、Tool Policy、Skill Policy、MCP Policy、输入输出契约、Artifact/Evidence 契约、Gate 触发规则、自验证清单、验收标准和失败升级规则；
4. 本地资源、社区资源、在线资源、MCP、Tool、专家 Agent 走统一资源调用流程；
5. 资源调用必须记录来源、风险、权限和 Trace；
6. 命令、Tool、MCP、写盘、Git 操作必须先映射风险级别；
7. Policy 优先级高于 Agent 判断；
8. Security / Authorization Agent 不能批准 Policy 禁止的动作；
9. L5 高风险动作暂定全部强制用户 Gate；
10. P 阶段晋级 Gate 必须用户授权；
11. 确定性转换能力以 Skill、Tool、Expert Agent 或 MCP 的资源形式接入，不构建独立确定性转换引擎作为主线；
12. 确定性转换、LLM、外部编程 Agent 均不得绕过 Policy / Gate / Audit；
13. 密钥、Token、env 明文、私钥、连接串必须脱敏；
14. Artifact 不自动成为 Evidence；
15. Evidence candidate 不自动成为 Evidence validated；
16. 当前版本不设独立 Mission 产品层。
```

本文不得：

```text
1. 将外部编程 Agent 视为默认可信执行主体；
2. 将 OpenCode 或其他历史参考能力写成当前已实现事实；
3. 将外部编程 Agent 输出直接写成当前事实；
4. 将外部编程 Agent 输出直接写成 Evidence validated；
5. 将 Patch generated、Patch applied、命令 exit_code=0 写成业务完成；
6. 让外部编程 Agent 绕过 Stage Plan / Task Plan；
7. 让外部编程 Agent 绕过 Policy / Gate / Audit；
8. 让外部编程 Agent 直接接触明文 Key / Token / Secret / Password；
9. 让外部编程 Agent 直接决定 P 阶段晋级；
10. 让外部编程 Agent 替代 LangGraph 主编排；
11. 复活旧 RunEngine、旧 S0-S7、旧 F0-F6 或旧 Phase 主流程；
12. 把 R1 建议契约伪装为实现契约。
```

> 本节"必须遵守/本文不得"中的通用红线（P 阶段晋级用户 Gate D-023、L5 强制 Gate D-034、Policy 优先 D-031、Artifact/Evidence candidate 不自动晋升 D-014、密钥脱敏 D-032、不设 Mission 产品层 D-070、外部 Agent 不替代 LangGraph 主编排 D-037/D-065 等）完整总表见 AGENTS §18 + 01-决策记录；本文仅就地保留与外部编程 Agent 接入职责相关的子集。

---

## 1. 流程定位

外部编程 Agent 接入流程服务于 P4 执行链路，并向 P5 验证和 P6 交付提供可追溯的执行材料。

外部编程 Agent 是受控执行资源，不是平台主编排，不是阶段裁决者，不是 Evidence 验证者。

其核心目标是：

```text
1. 将 OpenCode、外部编程 Agent、Tool、MCP、Expert Agent 等执行资源纳入统一资源调用流程；
2. 通过受控上下文让外部执行资源完成代码分析、Patch 生成、文件修改、命令执行等动作；
3. 在执行前进行 Policy / Gate 检查；
4. 在执行中记录 Trace；
5. 对高风险动作写 Audit；
6. 将输出进入 Artifact candidate 或 Evidence candidate；
7. 将验证留给 P5 验证流程；
8. 将阶段晋级留给用户 Gate。
```

---

## 2. 术语定义

### 2.1 外部编程 Agent

外部编程 Agent 是指可在受控上下文下参与代码阅读、修改、Patch 生成、命令执行或修复建议的外部执行主体。

### 2.2 OpenCode

OpenCode 在本文中作为外部编程 Agent / 外部执行器候选类型处理。历史资料可作为参考，但不得直接视为当前已接入能力。

### 2.3 Tool

Tool 是可被调用的工具资源，可能执行读取、分析、转换、写入或命令动作。

### 2.4 MCP

MCP 是模型上下文协议相关资源接入形态，可暴露工具、资源、上下文或执行能力。

### 2.5 Expert Agent

Expert Agent 是在特定领域或任务上提供建议、分析或受控执行的专家型 Agent。

### 2.6 确定性转换器

确定性转换器包括 AST、codemod、规则转换等可确定处理能力，应以 Skill、Tool、Expert Agent 或 MCP 的资源形式接入。

---

## 3. 支持的执行资源类型

R1 建议支持以下资源类型：

```text
opencode_adapter；
external_coding_agent；
tool_adapter；
mcp_adapter；
expert_agent_adapter；
deterministic_transformer；
local_command_executor；
remote_command_executor。
```

每类资源必须声明：

```text
resource_id；
resource_type；
adapter_type；
capabilities；
permission_scope；
risk_profile；
context_policy；
output_policy；
trace_policy；
audit_policy；
status。
```

规则：

```text
1. 未注册资源不得执行；
2. 未声明权限边界不得执行写操作；
3. 未声明风险级别不得执行高风险动作；
4. 未声明输出策略不得进入 Evidence candidate；
5. mock / not_connected 资源必须显式标记。
```

---

## 4. 总体接入流程

外部编程 Agent 接入流程分为注册、绑定、调用、回收、验收五段。

```text
1. 资源登记：在 Resource Registry 中登记外部执行资源；
2. 能力声明：声明可执行能力、权限、风险和输入输出契约；
3. 项目绑定：在 Project 或 Run 范围内启用资源；
4. 上下文准备：按 Context Recipe 组装最小必要上下文；
5. Policy Check：执行动作前进行策略检查；
6. Gate 处理：必要时暂停等待用户或授权主体决策；
7. 执行动作：调用外部编程 Agent / Tool / MCP；
8. 输出回收：回收 stdout、stderr、patch、diff、报告、建议或错误；
9. 脱敏处理：写入 output_ref 前进行脱敏；
10. Trace 写入：记录调用链路、输入摘要、输出引用和状态；
11. Audit 写入：高风险动作、Gate 决策和风险接受写 Audit；
12. Artifact 生成：必要时生成 Artifact candidate；
13. Evidence candidate：必要时生成 Evidence candidate；
14. 自验证：调用方执行最低限度自验证；
15. 交给后续 P5 验证或 Acceptance Agent 审核。
```

---

## 5. Resource Registry 接入要求

外部编程 Agent 必须先登记再调用。

登记字段建议：

```text
resource_id；
resource_name；
resource_type；
adapter_type；
version；
source；
source_identity；
license_status；
capabilities；
permission_scope；
default_risk_level；
allowed_actions；
blocked_actions；
context_policy_ref；
credential_ref；
credential_status；
trace_required；
audit_required；
review_status；
redaction_status。
```

规则：

```text
1. source 必须明确；
2. 社区资源默认只读参考；
3. 可执行社区资源必须经过安全、许可、权限和审核流程；
4. credential_ref 不得暴露凭据明文；
5. blocked_actions 优先于 allowed_actions；
6. review_status 未通过时不得标记可执行。
```

---

## 6. Project / Run 绑定流程

绑定流程：

```text
1. 用户或系统选择资源；
2. 后端读取 Resource Registry；
3. 检查 resource status；
4. 检查 permission_scope；
5. 检查 credential_status；
6. 执行 Policy Check；
7. 必要时创建 resource_enable_gate；
8. 绑定到 project_id / run_id / stage；
9. 写 resource_binding_trace；
10. 必要时写 resource_binding_audit。
```

绑定规则：

```text
1. 资源绑定不等于资源可执行；
2. Project 绑定不等于所有 Run 自动可用；
3. Run 绑定不等于所有 Stage 自动可用；
4. 权限范围必须可见；
5. read_only 资源不得显示执行入口；
6. 权限升级必须 Gate / Audit。
```

---

## 7. 上下文传递流程

外部编程 Agent 只能接收最小必要上下文。

上下文包建议包含：

```text
task_summary；
project_summary；
current_stage；
workspace_scope；
allowed_paths；
blocked_paths；
relevant_files；
change_goal；
acceptance_criteria；
risk_level；
permission_scope；
redaction_policy；
output_contract；
trace_ref。
```

上下文包不得包含：

```text
Key；
Token；
Secret；
Password；
.env 明文；
连接串明文；
SSH private key；
GitHub token；
Provider Key；
无关完整项目 dump；
未确认历史事实。
```

规则：

```text
1. 上下文必须按层组装，不全量塞入；
2. 外部 Agent 无权自行扩大文件范围；
3. 超出 allowed_paths 的读取或写入必须阻断；
4. blocked_paths 永远优先；
5. 上下文输出前必须脱敏检查；
6. 上下文污染风险必须可登记。
```

---

## 8. 动作分类与风险

动作分类建议：

```text
A0-read_context：读取上下文；
A1-read_file：读取文件；
A2-analyze：分析和建议；
A3-generate_patch：生成 Patch；
A4-write_file：写文件；
A5-apply_patch：应用 Patch；
A6-run_command：执行命令；
A7-run_build：执行构建；
A8-run_test：执行测试；
A9-git_commit：本地提交；
A10-git_push：远端写入；
A11-external_write：外部系统写操作。
```

风险规则：

```text
1. 读取类动作通常低风险，但仍需 Trace；
2. 分析类动作不得直接形成事实；
3. Patch 生成是 Artifact candidate；
4. 写文件、应用 Patch、命令执行必须按风险级别处理；
5. Git push 和外部系统写操作必须高风险处理；
6. L5 高风险动作必须用户 Gate；
7. Policy blocked 不得通过 Agent 判断放行。
```

---

## 9. 代码修改流程

代码修改建议采用 Patch-first 原则。

```text
1. 读取任务和上下文；
2. 外部编程 Agent 生成修改计划或 Patch；
3. 平台检查 Patch 范围；
4. 执行 Policy Check；
5. 如涉及写盘或高风险，创建 Gate；
6. 用户或授权主体确认；
7. 应用 Patch；
8. 写 patch_apply_trace；
9. 生成 change_summary_artifact；
10. 进入构建 / 测试 / 验证流程；
11. P5 生成验证 Evidence。
```

规则：

```text
1. Patch generated 不等于 Patch applied；
2. Patch applied 不等于验证通过；
3. 外部 Agent 直接写文件应视为更高风险路径；
4. 建议默认要求外部 Agent 输出 Patch 或 diff；
5. 写盘结果必须可回滚或至少有补救说明；
6. 写盘前必须展示影响范围摘要。
```

---

## 10. 文件读取 / 写入流程

### 10.1 文件读取

```text
1. 检查 allowed_paths；
2. 检查 blocked_paths；
3. 检查敏感文件策略；
4. 读取文件内容或摘要；
5. 脱敏；
6. 写 file_read_trace；
7. 传递给外部 Agent。
```

### 10.2 文件写入

```text
1. 接收写入请求或 Patch；
2. 检查目标路径；
3. 检查变更范围；
4. 执行 Policy Check；
5. 必要时触发 Gate；
6. 写入文件或应用 Patch；
7. 写 file_write_trace；
8. 必要时写 Audit；
9. 生成 diff_summary_artifact；
10. 更新 Workspace 状态。
```

规则：

```text
1. blocked_paths 不得写入；
2. 写入 .env、密钥文件、配置文件必须高风险处理；
3. 文件写入失败不得显示任务成功；
4. 外部 Agent 自称写入成功不作为事实源；
5. 写入结果必须由 Workspace 状态确认。
```

---

## 11. 命令执行流程

命令执行流程：

```text
1. 接收命令请求；
2. 识别命令意图；
3. 映射风险级别；
4. 检查执行环境；
5. 执行 Policy Check；
6. 必要时创建 Gate；
7. 通过 Execution Session 执行命令；
8. 捕获 stdout / stderr / exit_code；
9. 脱敏输出；
10. 写 command_trace；
11. 必要时写 command_audit；
12. 生成 output_ref；
13. 返回结构化结果。
```

规则：

```text
1. 命令 exit_code=0 不等于业务完成；
2. 命令输出不自动成为 Evidence validated；
3. 高风险命令不得自动执行；
4. 命令日志必须脱敏；
5. 命令失败必须保留错误摘要；
6. 命令超时必须可中止和恢复。
```

---

## 12. 构建 / 测试 / 运行调用流程

外部编程 Agent 可触发构建、测试、运行请求，但结果必须进入验证链路。

```text
1. 外部 Agent 提出构建 / 测试 / 运行请求；
2. 平台检查 Stage Plan / Task Plan；
3. 执行 Policy Check；
4. 创建或复用 Execution Session；
5. 执行构建 / 测试 / 运行命令；
6. 写 execution_trace；
7. 输出脱敏；
8. 生成 build / test / runtime evidence candidate；
9. 交给 P5 验证流程确认。
```

规则：

```text
1. 构建成功不等于验证通过；
2. 测试成功不等于 P5 completed；
3. 服务启动不等于业务正确；
4. 外部 Agent 不得自行宣告 P5 通过；
5. P5 验证证据槽位必须由验证流程确认。
```

---

## 13. Git 相关动作边界

外部编程 Agent 可请求 Git 读取和本地变更动作，但远端写必须严格控制。

```text
git status：允许读取，但需 Trace；
git diff：允许读取并生成 Artifact candidate；
git add / commit：需 Policy Check，必要 Gate；
git push：必须用户 Gate 和 Audit；
PR / MR create：按外部系统写操作处理；
remote branch delete：高风险，默认必须用户 Gate。
```

规则：

```text
1. 外部 Agent 不得静默 push；
2. 外部 Agent 不得直接持有 GitHub token；
3. Git 输出必须脱敏；
4. Git 变更必须与 Workspace 状态一致；
5. Git push 成功不等于交付通过。
```

---

## 14. 输出回收与材料身份

外部编程 Agent 输出类型包括：

```text
analysis_text；
change_plan；
patch；
diff；
modified_files_summary；
command_output；
build_output；
test_output；
error_report；
next_actions。
```

输出材料身份：

```text
analysis_text -> 参考 / Artifact candidate；
change_plan -> Artifact candidate；
patch -> Artifact candidate；
diff -> Artifact candidate；
command_output -> output_ref / Evidence candidate；
build_output -> Evidence candidate；
test_output -> Evidence candidate；
error_report -> error_ref / Artifact candidate。
```

规则：

```text
1. 输出必须脱敏；
2. 输出必须绑定 trace_ref；
3. Artifact candidate 不自动成为项目文档；
4. Evidence candidate 不自动成为 Evidence validated；
5. Error output 不得被隐藏；
6. 外部 Agent 的自然语言总结不得替代结构化结果。
```

---

## 15. Artifact / Evidence / Trace / Audit

### 15.1 Artifact

可能生成：

```text
agent_analysis_artifact；
change_plan_artifact；
patch_artifact；
diff_summary_artifact；
execution_report_artifact；
error_report_artifact。
```

### 15.2 Evidence

可能生成：

```text
patch_generated_evidence_candidate；
patch_applied_evidence_candidate；
command_execution_evidence_candidate；
build_evidence_candidate；
test_evidence_candidate；
runtime_evidence_candidate。
```

### 15.3 Trace

必须记录：

```text
external_agent_call_trace；
context_package_trace；
file_read_trace；
file_write_trace；
patch_generate_trace；
patch_apply_trace；
command_trace；
tool_call_trace；
mcp_call_trace；
redaction_trace；
error_trace。
```

### 15.4 Audit

必须审计：

```text
high_risk_action_audit；
write_scope_audit；
patch_apply_audit；
command_high_risk_audit；
git_remote_write_audit；
policy_block_audit；
gate_decision_audit；
risk_acceptance_audit。
```

---

## 16. Gate 策略

Gate 类型建议：

```text
resource_enable_gate；
context_scope_gate；
write_scope_gate；
patch_apply_gate；
command_execution_gate；
high_risk_command_gate；
git_remote_write_gate；
external_write_gate；
risk_acceptance_gate。
```

Gate 规则：

```text
1. 资源启用可能需要 Gate；
2. 扩大上下文范围需要 Gate；
3. 写盘需要按风险触发 Gate；
4. Patch apply 需要按风险触发 Gate；
5. 高风险命令必须 Gate；
6. 远端写操作必须 Gate；
7. Gate 决策必须写 Audit；
8. Gate 未决策不得继续执行。
```

---

## 17. Policy 规则

Policy 必须在动作执行前触发。

检查项：

```text
action_type；
resource_type；
permission_scope；
risk_level；
allowed_paths；
blocked_paths；
credential_status；
external_write；
network_scope；
redaction_status；
user_authorization_status。
```

结果状态：

```text
allowed；
gate_required；
blocked；
redaction_required；
credential_required；
unknown；
error。
```

规则：

```text
1. blocked 不得继续；
2. error 不得默认 allowed；
3. unknown 不得默认 allowed；
4. redaction_required 不得展示原文；
5. credential_required 不得向外部 Agent 暴露密钥；
6. gate_required 必须暂停动作。
```

---

## 18. 失败处理

失败码建议：

```text
resource_not_registered；
resource_not_enabled；
resource_read_only；
capability_not_declared；
context_scope_denied；
credential_missing；
credential_invalid；
policy_blocked；
gate_required；
gate_not_resolved；
external_agent_timeout；
external_agent_failed；
tool_call_failed；
mcp_call_failed；
patch_invalid；
patch_apply_failed；
file_write_denied；
command_failed；
command_timeout；
output_redaction_required；
redaction_failed；
trace_write_failed；
audit_write_failed；
unknown_failure。
```

失败处理规则：

```text
1. 外部 Agent 失败不得显示任务成功；
2. Tool / MCP 失败不得静默降级为成功；
3. Patch invalid 不得应用；
4. command_failed 不得显示验证通过；
5. trace_write_failed 必须可见；
6. audit_write_failed 必须阻断高风险动作完成；
7. redaction_failed 不得展示原文；
8. unknown_failure 不得默认通过。
```

---

## 19. 前端展示要求

前端应展示：

```text
资源名称；
资源类型；
资源状态；
权限范围；
风险级别；
当前动作；
active_gate；
Policy 状态；
Trace 状态；
Audit 状态；
输出摘要；
Artifact candidate；
Evidence candidate；
错误摘要；
next_actions；
mock / not_connected / read_only 标记。
```

前端不得：

```text
1. 将外部 Agent 输出显示为当前事实；
2. 将 Patch generated 显示为 Patch applied；
3. 将 Patch applied 显示为验证通过；
4. 将 Tool / MCP 成功显示为业务完成；
5. 隐藏 Policy blocked；
6. 隐藏 Gate 未决策；
7. 展示未脱敏输出；
8. 用 toast 替代 Gate。
```

> 前端展示的具体布局、组件、交互规范见 `文档/06-UX与前端/` 各专题文档，本文只定义外部编程 Agent 相关的前端展示边界。

---

## 20. API / SSE 建议

### 20.1 API 分组建议

```text
Resource Registry API；
Resource Binding API；
External Executor API；
Context Package API；
Tool / MCP Invocation API；
Patch API；
Command Execution API；
Trace / Audit API；
Gate API。
```

### 20.2 核心 API 能力建议

```text
register_resource；
get_resource；
list_project_resources；
bind_resource_to_project；
bind_resource_to_run；
create_context_package；
invoke_external_agent；
invoke_tool；
invoke_mcp；
generate_patch；
apply_patch；
run_command；
get_invocation_status；
get_invocation_output；
get_invocation_trace；
get_invocation_audit。
```

### 20.3 SSE 事件建议

```text
resource_registered；
resource_binding_required；
resource_bound；
external_agent_started；
external_agent_output；
external_agent_failed；
tool_call_started；
tool_call_completed；
mcp_call_started；
mcp_call_completed；
patch_generated；
patch_apply_gate_required；
patch_applied；
command_started；
command_completed；
command_failed；
policy_blocked；
gate_required；
audit_written；
trace_written；
redaction_required；
resync_required。
```

规则：

```text
1. SSE 只作为刷新提示；
2. 前端收到事件后重新查询后端对象；
3. SSE payload 不得含密钥；
4. completed 事件不等于阶段 completed；
5. mock SSE 必须显式标记。
```

---

## 21. 与 P0-P6 的关系

```text
P0：通常不依赖外部编程 Agent，但可读取资源可用性；
P1：可用于辅助建档，但输出不得直接成为项目事实；
P2：可用于辅助分析和风险识别；
P3：可用于生成执行建议，但计划仍需审核；
P4：核心使用阶段，用于代码修改、Patch、命令执行和工具调用；
P5：可触发构建 / 测试 / 运行，但验证结论必须由 P5 验证流程形成；
P6：可辅助生成交付摘要，但不得替代交付 Gate。
```

规则：

```text
1. 外部 Agent 不改变 P0-P6 阶段；
2. 外部 Agent 不决定 P 阶段晋级；
3. 外部 Agent 不替代 Acceptance Agent；
4. 外部 Agent 不替代用户 Gate；
5. 外部 Agent 输出必须进入当前材料身份体系。
```

---

## 22. R 阶段校准项

### 22.1 R2 文档校准

```text
1. 本文是否与最新决策记录一致；
2. 与 05-集成与外部执行架构.md 是否重复或冲突；
3. 与模型与资源目录中的资源总览是否一致；
4. API 字段是否需要迁入 API 契约文档；
5. 安全规则是否需要迁入安全规范；
6. 是否仍无 Mission 产品层；
7. 是否存在旧 RunEngine / 旧阶段术语残留。
```

### 22.2 R6 Agent / Skill / 资源校准

```text
1. Resource Registry 是否支持外部编程 Agent；
2. OpenCode 是否作为候选外部执行器；
3. Tool / MCP / Expert Agent 是否可登记；
4. 确定性转换器是否以资源形式接入；
5. Agent Definition Contract 是否覆盖外部执行资源。
```

### 22.3 R8 Workspace 校准

```text
1. 外部 Agent 是否只能访问受控 Workspace；
2. allowed_paths / blocked_paths 是否生效；
3. 文件读写是否写 Trace；
4. 输出是否脱敏；
5. 状态恢复是否不依赖外部 Agent 自述。
```

### 22.4 R11 P4 执行链路校准

```text
1. 外部 Agent 是否能生成 Patch；
2. Patch apply 是否受控；
3. 命令执行是否通过 Execution Session；
4. 高风险动作是否 Gate / Audit；
5. 执行结果是否进入 Artifact / Evidence candidate。
```

### 22.5 R12 P5-P6 验证交付校准

```text
1. 构建 / 测试 / 运行结果是否进入 P5 验证；
2. 外部 Agent 输出是否没有冒充 Evidence validated；
3. P6 交付是否引用验证后的证据；
4. 交付材料是否脱敏；
5. 风险接受是否 Audit。
```

---

## 23. 流程红线

```text
1. 不得将外部编程 Agent 视为默认可信执行主体；
2. 不得将 OpenCode 或其他历史参考能力写成当前已实现事实；
3. 不得将外部编程 Agent 输出直接写成当前事实；
4. 不得将外部编程 Agent 输出直接写成 Evidence validated；
5. 不得将 Patch generated、Patch applied、命令 exit_code=0 写成业务完成；
6. 不得让外部编程 Agent 绕过 Stage Plan / Task Plan；
7. 不得让外部编程 Agent 绕过 Policy / Gate / Audit；
8. 不得让外部编程 Agent 直接接触明文 Key / Token / Secret / Password；
9. 不得让外部编程 Agent 直接决定 P 阶段晋级；
10. 不得让外部编程 Agent 替代 LangGraph 主编排；
11. 不得将 Tool / MCP 输出自动作为 Evidence validated；
12. 不得将社区资源默认显示为可执行；
13. 不得隐藏 Policy blocked、Gate 未决策、Trace 缺失或 Audit 缺失；
14. 不得复活旧 RunEngine、旧 S0-S7、旧 F0-F6 或旧 Phase 主流程；
15. 不得引入 Mission 产品层；
16. 不得在长期 API、DB、路由、组件、状态字段中固化 V26.1；
17. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md（P 阶段晋级用户 Gate D-023、L5 强制 Gate D-034、Policy 优先 D-031、Artifact/Evidence candidate 不自动晋升 D-014、密钥脱敏 D-032、不引入 Mission D-070、外部 Agent 不替代主编排 D-037/D-065）；术语与风险级别（L0-L5）详见 02-术语表.md。本文红线为外部编程 Agent 接入主题特有约束，整体保留。

---

## 24. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确流程定位；
2. 明确术语定义；
3. 明确支持的执行资源类型；
4. 明确总体接入流程；
5. 明确 Resource Registry 接入要求；
6. 明确 Project / Run 绑定流程；
7. 明确上下文传递流程；
8. 明确动作分类与风险；
9. 明确代码修改流程；
10. 明确文件读取 / 写入流程；
11. 明确命令执行流程；
12. 明确构建 / 测试 / 运行调用流程；
13. 明确 Git 相关动作边界；
14. 明确输出回收与材料身份；
15. 明确 Artifact / Evidence / Trace / Audit；
16. 明确 Gate 策略；
17. 明确 Policy 规则；
18. 明确失败处理；
19. 明确前端展示要求；
20. 明确 API / SSE 建议；
21. 明确与 P0-P6 的关系；
22. 明确 R 阶段校准项；
23. 明确流程红线；
24. 未新增产品决策；
25. 未引入 Mission 产品层；
26. 未固定最终实现。
```
