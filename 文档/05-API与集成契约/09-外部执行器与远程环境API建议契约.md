# 09-外部执行器与远程环境API建议契约

> 文档路径：`文档/05-API与集成契约/09-外部执行器与远程环境API建议契约.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/外部执行器与远程环境API契约.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义外部执行器、Tool/MCP/Expert Agent、确定性转换器、Environment Profile、Execution Session、命令/构建/测试/运行、Patch、Output 的 API 建议契约。本文是 05-API 目录中覆盖"执行与运行"域的 API 详述源，与 02（Project-Run-阶段）、03（TaskPlan-TaskGraph）、04（AETA）、05（Gate）、06（Model-Resource）、07（SSE）、08（源码接入与Git操作）互补。
> 上级依据：`文档/00-项目治理/01-决策记录.md`、`文档/02-架构设计/05-集成与外部执行架构.md`、`文档/03-流程与运行时/06-外部编程Agent接入流程.md`、`文档/03-流程与运行时/07-远程环境与执行会话规范.md`、`文档/04-模型与资源/08-外部执行器与工具资源总览.md`、`文档/05-API与集成契约/04-Artifact-Evidence-Trace-Audit API建议契约.md`、`文档/05-API与集成契约/05-Gate-Authorization API建议契约.md`、`文档/05-API与集成契约/06-Model-Resource API建议契约.md`、`文档/05-API与集成契约/07-SSE与事件契约.md`、`文档/07-安全与权限/`。
> 重要边界：本文是 API 建议契约，不是最终实现契约；R2/R4/R6/R8/R11/R12 需根据真实后端、前端联调、资源注册实现、远程连接实现和安全策略校准字段、路径、状态码和事件。本文不替代 Agent Definition Contract、Resource Registry 实现、OpenCode 适配器、Tool/MCP 协议实现、ExecutionProvider 实现、安全策略或验收报告。
> 修订说明：R1 正式化版基于草稿 v0.1：(1) 头部标准化；(2) 文档编号 `09-`（05-API与集成契约/ 目录 00-08 之后，架构文档占位引用 10- 同步更新为 09-）；(3) §0 上级依据中 4 处草稿文件名修正为正式文档路径；(4) §0 新增与 04/05/06/07 的互补说明及引用提示（Gate 详述见 05、Trace/Audit 详述见 04、SSE 详述见 07）；(5) 新增 §22 待确认项。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守当前文档事实源层级：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只定义外部执行器与远程环境 API 建议契约，不重新定义产品流程、阶段体系、最终数据库结构或具体适配器实现。

**与同级文档的关系**：

| 文档 | 职责 | 本文如何引用 |
|---|---|---|
| 04-AETA API | Artifact/Evidence/Trace/Audit 完整 API | 本文 §14 列出 Trace/Audit 类型，详述见 04 |
| 05-Gate-Authorization API | Gate 完整 API | 本文 §13 列出 Gate 类型，详述见 05 |
| 06-Model-Resource API | ModelGateway + Resource Registry 基础 | 本文 §4-§5 扩展 Resource Registry/Binding 端点 |
| 07-SSE与事件契约 | SSE 通用结构 | 本文 §15 列出执行域事件类型，通用结构见 07 |
| **本文（09）** | **外部执行器/环境/Session/命令/Patch/Output 的完整 API 端点** | — |

本文必须遵守：

```text
1. R1 API 字段输出建议契约，R2 校准为实现契约；
2. Project Workspace、Environment Profile、Execution Session 是环境与执行三对象；
3. 本地资源、社区资源、在线资源、MCP、Tool、专家 Agent 走统一资源调用流程；
4. 资源调用必须记录来源、风险、权限和 Trace；
5. 确定性转换能力以 Skill、Tool、Expert Agent 或 MCP 的资源形式接入；
6. 命令、Tool、MCP、写盘、Git 操作必须先映射风险级别；
7. Policy 优先级高于 Agent 判断（D-031）；
8. Security / Authorization Agent 不能批准 Policy 禁止的动作；
9. L5 高风险动作暂定全部强制用户 Gate（D-034）；
10. 密钥、Token、env 明文、私钥、连接串和远程主机密码必须脱敏（D-032）；
11. Artifact 不自动成为 Evidence；
12. Evidence candidate 不自动成为 Evidence validated；
13. SSE 只作为刷新提示，不作为事实源；
14. completed 事件不等于阶段 completed；
15. 当前版本不设独立 Mission 产品层（D-070）。
```

本文不得：

```text
1. 将 API 200 写成业务完成；
2. 将外部执行器输出写成当前事实；
3. 将 Patch generated 写成 Patch applied；
4. 将 Patch applied 写成验证通过；
5. 将命令 exit_code=0 写成业务完成；
6. 将构建成功写成 P5 completed；
7. 将测试通过直接写成 P5 completed；
8. 将服务启动写成业务正确；
9. 将 Environment Profile 创建成功写成环境可用；
10. 将 Execution Session 创建成功写成命令执行成功；
11. 返回明文 Key / Token / Secret / Password（D-032）；
12. 在 SSE payload 中携带密钥（D-032）；
13. 绕过 Gate 执行高风险命令、写盘、远端写或外部系统写操作；
14. 将 mock / not_connected API 显示为真实能力；
15. 复活旧 RunEngine、旧 S0-S7、旧 F0-F6 或旧 Phase 主流程；
16. 把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. API 范围

本文覆盖 API 分组：

```text
Resource Registry API；
Resource Binding API；
External Executor API；
Context Package API；
Tool / MCP Invocation API；
Patch API；
Environment Profile API；
Execution Session API；
Command Execution API；
Build / Test / Runtime API；
Output API；
Gate API（引用 05）；
Trace / Audit API（引用 04）；
SSE Event Contract（引用 07）。
```

本文不覆盖：

```text
源码接入与 Git API（见 08）；
模型调用 API（见 06）；
社区资源市场 API；
文档功能 API；
最终数据库迁移字段；
具体 OpenCode 协议；
具体 MCP Server 实现；
具体 SSH / 容器 / Runner 适配器实现。
```

---

## 2. 通用约定

### 2.1 路径前缀建议

```text
/api/resources
/api/projects/{project_id}/resources
/api/projects/{project_id}/executors
/api/projects/{project_id}/context-packages
/api/projects/{project_id}/patches
/api/projects/{project_id}/environments
/api/projects/{project_id}/execution-sessions
/api/projects/{project_id}/commands
/api/projects/{project_id}/builds
/api/projects/{project_id}/tests
/api/projects/{project_id}/runtime
/api/projects/{project_id}/outputs
/api/projects/{project_id}/gates
/api/projects/{project_id}/traces
/api/projects/{project_id}/audits
```

R2 可根据后端路由体系调整。

### 2.2 通用请求头建议

```text
X-Request-Id: string，可选；客户端生成或服务端补全
X-Idempotency-Key: string，可选；用于高风险或可重试动作
```

### 2.3 通用响应字段

```json
{
  "request_id": "string",
  "project_id": "string | null",
  "run_id": "string | null",
  "status": "accepted | running | completed | failed | blocked | gate_required | redaction_required",
  "trace_ref": "string | null",
  "audit_ref": "string | null",
  "gate_ref": "string | null",
  "output_ref": "string | null",
  "error": null,
  "redaction_status": "clean | redacted | redaction_required | redaction_failed"
}
```

规则：

```text
1. request_id 必须可追踪；
2. trace_ref 用于追踪过程；
3. audit_ref 用于高风险动作或 Gate 决策；
4. gate_ref 存在时动作不得继续执行，除非 Gate 被明确批准；
5. output_ref 指向脱敏输出；
6. error 不得包含密钥片段；
7. redaction_failed 时不得返回原始输出。
```

### 2.4 通用状态枚举

```text
pending；
running；
completed；
failed；
blocked；
gate_required；
redaction_required；
not_connected；
mock；
unknown。
```

`completed` 仅表示当前 API 动作完成，不表示 P 阶段完成或验证通过。

---

## 3. 数据对象建议

### 3.1 ResourceDefinition

```json
{
  "resource_id": "string",
  "resource_name": "string",
  "resource_type": "opencode_adapter | external_coding_agent | tool_adapter | mcp_adapter | expert_agent_adapter | deterministic_transformer | local_command_executor | remote_command_executor",
  "adapter_type": "string",
  "version": "string | null",
  "source": "local | online | community | built_in | unknown",
  "source_identity": "read_only_reference | executable_resource | candidate | deprecated | unknown",
  "license_status": "unknown | verified | restricted | not_required",
  "capabilities": ["read_file", "write_file", "generate_patch", "apply_patch", "run_command", "run_build", "run_test", "start_runtime"],
  "permission_scope": ["read", "write", "execute", "external_write"],
  "default_risk_level": "L0 | L1 | L2 | L3 | L4 | L5",
  "allowed_actions": ["string"],
  "blocked_actions": ["string"],
  "credential_ref": "string | null",
  "credential_status": "missing | provided | valid | invalid | expired | permission_limited | unknown",
  "review_status": "candidate | source_verified | accepted_for_adaptation | rejected | deprecated | enabled | disabled",
  "redaction_status": "clean | redacted | redaction_required | redaction_failed"
}
```

> 资源类型的完整分类见 `文档/04-模型与资源/08-外部执行器与工具资源总览.md` §3。

### 3.2 ResourceBinding

```json
{
  "binding_id": "string",
  "resource_id": "string",
  "project_id": "string",
  "run_id": "string | null",
  "stage": "P0 | P1 | P2 | P3 | P4 | P5 | P6 | null",
  "permission_scope": ["read", "write", "execute"],
  "status": "pending | enabled | disabled | blocked | gate_required",
  "gate_ref": "string | null",
  "trace_ref": "string | null",
  "audit_ref": "string | null"
}
```

### 3.3 EnvironmentProfile

```json
{
  "environment_ref": "string",
  "project_id": "string",
  "environment_name": "string",
  "environment_type": "local_host | local_container | remote_host | remote_container | remote_workspace | managed_runner | custom_environment",
  "connection_profile_ref": "string | null",
  "credential_ref": "string | null",
  "credential_status": "missing | provided | valid | invalid | expired | permission_limited | unknown",
  "workspace_binding": "copy_to_environment | mount_workspace | remote_clone | artifact_transfer | manual_binding | unknown",
  "capabilities": ["run_command", "run_build", "run_test", "start_runtime"],
  "network_scope": "none | local | restricted | external | unknown",
  "resource_limits": {
    "cpu": "string | null",
    "memory": "string | null",
    "timeout_seconds": 0
  },
  "status": "declared | probing | reachable | failed | not_connected | mock | unknown",
  "redaction_status": "clean | redacted | redaction_required | redaction_failed"
}
```

### 3.4 ExecutionSession

```json
{
  "session_id": "string",
  "project_id": "string",
  "run_id": "string | null",
  "stage": "P0 | P1 | P2 | P3 | P4 | P5 | P6 | null",
  "environment_ref": "string",
  "workspace_ref": "string | null",
  "session_type": "local | remote | container | managed_runner | unknown",
  "status": "pending | running | paused | completed | failed | lost | closed | gate_required",
  "started_at": "string | null",
  "updated_at": "string | null",
  "expires_at": "string | null",
  "active_processes": ["string"],
  "active_gate": "string | null",
  "trace_refs": ["string"],
  "audit_refs": ["string"],
  "output_refs": ["string"],
  "error_ref": "string | null",
  "redaction_status": "clean | redacted | redaction_required | redaction_failed"
}
```

### 3.5 InvocationResult

```json
{
  "invocation_id": "string",
  "project_id": "string",
  "run_id": "string | null",
  "resource_id": "string | null",
  "session_id": "string | null",
  "action_type": "invoke_external_agent | invoke_tool | invoke_mcp | generate_patch | apply_patch | run_command | run_build | run_test | start_runtime",
  "risk_level": "L0 | L1 | L2 | L3 | L4 | L5",
  "status": "pending | running | completed | failed | blocked | gate_required | redaction_required",
  "policy_check_ref": "string | null",
  "gate_ref": "string | null",
  "trace_ref": "string | null",
  "audit_ref": "string | null",
  "output_ref": "string | null",
  "artifact_refs": ["string"],
  "evidence_candidate_refs": ["string"],
  "error_ref": "string | null",
  "redaction_status": "clean | redacted | redaction_required | redaction_failed"
}
```

---

## 4. Resource Registry API

### 4.1 Register Resource

```text
POST /api/resources
```

请求体建议：

```json
{
  "resource_name": "string",
  "resource_type": "opencode_adapter | external_coding_agent | tool_adapter | mcp_adapter | expert_agent_adapter | deterministic_transformer | local_command_executor | remote_command_executor",
  "adapter_type": "string",
  "source": "local | online | community | built_in | unknown",
  "capabilities": ["read_file", "generate_patch", "run_command"],
  "permission_scope": ["read"],
  "default_risk_level": "L1",
  "allowed_actions": ["string"],
  "blocked_actions": ["string"],
  "credential_ref": "string | null"
}
```

响应体建议：

```json
{
  "request_id": "string",
  "resource": {
    "resource_id": "string",
    "resource_name": "string",
    "resource_type": "tool_adapter",
    "status": "pending | enabled | disabled | blocked",
    "review_status": "candidate | source_verified | enabled",
    "redaction_status": "clean | redacted"
  },
  "trace_ref": "string | null",
  "gate_ref": "string | null",
  "error": null
}
```

规则：

```text
1. 社区资源默认不得直接 enabled；
2. 可执行资源必须经过安全、许可、权限和审核流程；
3. credential_ref 不得包含明文；
4. blocked_actions 优先于 allowed_actions；
5. 注册成功不等于资源可执行。
```

### 4.2 Get Resource

```text
GET /api/resources/{resource_id}
```

### 4.3 List Project Resources

```text
GET /api/projects/{project_id}/resources
```

---

## 5. Resource Binding API

### 5.1 Bind Resource to Project

```text
POST /api/projects/{project_id}/resources/{resource_id}/bind
```

请求体建议：

```json
{
  "run_id": "string | null",
  "stage": "P0 | P1 | P2 | P3 | P4 | P5 | P6 | null",
  "permission_scope": ["read", "execute"],
  "require_gate": true
}
```

规则：

```text
1. 绑定成功不等于所有动作可执行；
2. 权限升级必须 Gate / Audit；
3. read_only 资源不得显示执行入口；
4. Project 绑定不等于所有 Run 自动可用。
```

---

## 6. Context Package API

### 6.1 Create Context Package

```text
POST /api/projects/{project_id}/context-packages
```

请求体建议：

```json
{
  "run_id": "string | null",
  "stage": "P0 | P1 | P2 | P3 | P4 | P5 | P6 | null",
  "task_summary": "string",
  "workspace_scope": "string",
  "allowed_paths": ["string"],
  "blocked_paths": ["string"],
  "relevant_files": ["string"],
  "change_goal": "string | null",
  "acceptance_criteria": ["string"],
  "risk_level": "L0 | L1 | L2 | L3 | L4 | L5",
  "permission_scope": ["read", "write", "execute"],
  "redaction_policy": "default | strict",
  "output_contract": "patch | analysis | command_output | mixed"
}
```

规则：

```text
1. 上下文包不得包含 Key / Token / Secret / Password；
2. blocked_paths 优先于 allowed_paths；
3. 外部执行器不得自行扩大上下文范围；
4. redaction_failed 不得返回上下文原文。
```

---

## 7. External Executor / Tool / MCP API

### 7.1 Invoke External Agent

```text
POST /api/projects/{project_id}/executors/{resource_id}/invoke
```

请求体建议：

```json
{
  "run_id": "string | null",
  "stage": "P4",
  "context_package_ref": "string",
  "action_type": "analyze | generate_patch | write_file | run_command | mixed",
  "require_gate": true
}
```

规则：

```text
1. 外部 Agent 输出不自动成为当前事实；
2. 外部 Agent 输出不自动成为 Evidence validated；
3. 写盘、命令执行、高风险动作必须 Policy / Gate；
4. 外部 Agent 不得直接接触明文凭据；
5. invocation completed 不等于阶段 completed。
```

### 7.2 Invoke Tool

```text
POST /api/projects/{project_id}/tools/{resource_id}/invoke
```

### 7.3 Invoke MCP

```text
POST /api/projects/{project_id}/mcp/{resource_id}/invoke
```

Tool / MCP 请求体建议：

```json
{
  "run_id": "string | null",
  "context_package_ref": "string | null",
  "input": {},
  "action_type": "read | analyze | transform | execute | external_write",
  "require_gate": true
}
```

规则：

```text
1. Tool / MCP 输出不自动成为 Evidence validated；
2. external_write 必须 Gate / Audit；
3. input 不得含明文密钥；
4. 输出必须绑定 trace_ref。
```

---

## 8. Patch API

### 8.1 Generate Patch

```text
POST /api/projects/{project_id}/patches/generate
```

请求体建议：

```json
{
  "run_id": "string | null",
  "resource_id": "string | null",
  "context_package_ref": "string",
  "change_goal": "string",
  "allowed_paths": ["string"],
  "blocked_paths": ["string"]
}
```

规则：

```text
1. patch generated 不等于 patch applied；
2. patch 是 Artifact candidate；
3. patch 不自动成为 Evidence validated；
4. patch 内容必须脱敏检查。
```

### 8.2 Apply Patch

```text
POST /api/projects/{project_id}/patches/{patch_ref}/apply
```

请求体建议：

```json
{
  "workspace_ref": "string",
  "require_gate": true,
  "expected_paths": ["string"]
}
```

规则：

```text
1. Patch apply 需按风险触发 Gate；
2. Patch applied 不等于验证通过；
3. blocked_paths 不得写入；
4. apply 失败不得显示任务成功。
```

---

## 9. Environment Profile API

### 9.1 Create Environment Profile

```text
POST /api/projects/{project_id}/environments
```

请求体建议：

```json
{
  "environment_name": "string",
  "environment_type": "local_host | local_container | remote_host | remote_container | remote_workspace | managed_runner | custom_environment",
  "connection_profile_ref": "string | null",
  "credential_ref": "string | null",
  "workspace_binding": "copy_to_environment | mount_workspace | remote_clone | artifact_transfer | manual_binding",
  "capabilities": ["run_command", "run_build", "run_test"],
  "network_scope": "none | local | restricted | external | unknown",
  "resource_limits": {
    "cpu": "string | null",
    "memory": "string | null",
    "timeout_seconds": 0
  }
}
```

规则：

```text
1. Environment Profile 创建成功不等于环境可用；
2. connection_profile_ref 不得含明文密钥；
3. credential_ref 不得暴露明文；
4. capability 未验证前不得显示 verified。
```

### 9.2 Probe Environment

```text
POST /api/projects/{project_id}/environments/{environment_ref}/probe
```

规则：

```text
1. reachable 不等于 environment_verified；
2. probe artifact 不自动成为 Evidence validated；
3. 探测输出必须脱敏。
```

---

## 10. Execution Session API

### 10.1 Create Execution Session

```text
POST /api/projects/{project_id}/execution-sessions
```

请求体建议：

```json
{
  "run_id": "string | null",
  "stage": "P0 | P1 | P2 | P3 | P4 | P5 | P6 | null",
  "environment_ref": "string",
  "workspace_ref": "string | null",
  "session_type": "local | remote | container | managed_runner | unknown",
  "require_gate": false
}
```

### 10.2 Get / List / Pause / Resume / Close Session

```text
GET /api/projects/{project_id}/execution-sessions/{session_id}
GET /api/projects/{project_id}/execution-sessions
POST /api/projects/{project_id}/execution-sessions/{session_id}/pause
POST /api/projects/{project_id}/execution-sessions/{session_id}/resume
POST /api/projects/{project_id}/execution-sessions/{session_id}/close
```

规则：

```text
1. Session 创建成功不等于命令执行成功；
2. 退出 UI 不等于停止任务；
3. 高风险动作不得后台静默继续；
4. 关闭 Session 不得删除 Evidence / Trace / Audit；
5. Session 必须可查询和恢复；
6. 会话异常终止必须可见。
```

---

## 11. Command / Build / Test / Runtime API

### 11.1 Run Command

```text
POST /api/projects/{project_id}/execution-sessions/{session_id}/commands
```

请求体建议：

```json
{
  "command_summary": "string",
  "command_ref": "string",
  "command_type": "shell | build | test | runtime | custom",
  "risk_level": "L0 | L1 | L2 | L3 | L4 | L5 | unknown",
  "working_directory_ref": "string | null",
  "timeout_seconds": 0,
  "require_gate": true
}
```

规则：

```text
1. exit_code=0 不等于业务完成；
2. 命令输出不自动成为 Evidence validated；
3. 高风险命令必须 Gate；
4. command_ref 不得含明文密钥。
```

### 11.2 Run Build / Test / Runtime

```text
POST /api/projects/{project_id}/execution-sessions/{session_id}/builds
POST /api/projects/{project_id}/execution-sessions/{session_id}/tests
POST /api/projects/{project_id}/execution-sessions/{session_id}/runtime/start
POST /api/projects/{project_id}/execution-sessions/{session_id}/runtime/stop
GET /api/projects/{project_id}/runtime/status
```

规则：

```text
1. build completed 不等于 test passed；
2. test passed 不等于 P5 completed；
3. runtime started 不等于业务正确；
4. 构建、测试、运行输出只能进入 Evidence candidate，需 P5 验证确认。
```

---

## 12. Output API

### 12.1 Get Output

```text
GET /api/projects/{project_id}/outputs/{output_ref}
```

规则：

```text
1. redaction_failed 不得返回 content_ref；
2. summary 不得包含密钥片段；
3. output 不自动成为 Evidence validated；
4. 前端按权限展示摘要或详情。
```

---

## 13. Gate API 关联

> Gate 的完整端点定义（查询、决策、列表）见 `文档/05-API与集成契约/05-Gate-Authorization API建议契约.md`。本文只列出执行域特有的 Gate 类型。

可能创建的 Gate：

```text
resource_enable_gate；
context_scope_gate；
write_scope_gate；
patch_apply_gate；
command_execution_gate；
high_risk_command_gate；
git_remote_write_gate；
external_write_gate；
environment_profile_gate；
execution_session_gate；
workspace_sync_gate；
risk_acceptance_gate。
```

规则：

```text
1. Gate 决策必须 Audit；
2. rejected 不得继续动作；
3. request_changes 必须返回 next_actions；
4. accepted_risk=true 必须写风险接受 Audit；
5. Policy blocked 不得通过 Gate 强行继续。
```

---

## 14. Trace / Audit API

> Trace/Audit 的完整端点定义见 `文档/05-API与集成契约/04-Artifact-Evidence-Trace-Audit API建议契约.md`。本文只列出执行域特有的 Trace/Audit 类型。

Trace 类型建议：

```text
resource_register_trace；
resource_binding_trace；
context_package_trace；
external_agent_call_trace；
tool_call_trace；
mcp_call_trace；
patch_generate_trace；
patch_apply_trace；
environment_profile_trace；
environment_probe_trace；
session_start_trace；
command_trace；
build_trace；
test_trace；
runtime_start_trace；
output_trace；
redaction_trace；
error_trace。
```

Audit 类型建议：

```text
gate_decision_audit；
high_risk_action_audit；
write_scope_audit；
patch_apply_audit；
command_high_risk_audit；
external_write_audit；
risk_acceptance_audit；
policy_block_audit。
```

规则：

```text
1. Trace / Audit 不得包含密钥；
2. Audit 不等于 Evidence；
3. Trace 不证明结果正确；
4. 高风险动作缺 Audit 不得显示完成。
```

---

## 15. SSE 事件契约

> SSE 通用事件结构见 `文档/05-API与集成契约/07-SSE与事件契约.md`。本文只列出执行域特有的事件类型。

### 15.1 事件通用结构

```json
{
  "event_id": "string",
  "event_type": "string",
  "project_id": "string",
  "run_id": "string | null",
  "resource_ref": "string | null",
  "session_id": "string | null",
  "operation_id": "string | null",
  "status": "running | completed | failed | blocked | gate_required | redaction_required",
  "request_id": "string | null",
  "trace_ref": "string | null",
  "audit_ref": "string | null",
  "gate_ref": "string | null",
  "redaction_status": "clean | redacted | redaction_required | redaction_failed",
  "timestamp": "string"
}
```

### 15.2 事件类型建议

```text
resource_registered；
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
environment_profile_created；
environment_probe_started；
environment_probe_completed；
environment_probe_failed；
execution_session_started；
execution_session_updated；
execution_session_paused；
execution_session_resumed；
execution_session_closed；
command_started；
command_completed；
command_failed；
build_started；
build_completed；
build_failed；
test_started；
test_completed；
test_failed；
runtime_started；
runtime_stopped；
runtime_failed；
policy_blocked；
gate_required；
audit_written；
trace_written；
redaction_required；
resync_required。
```

规则：

```text
1. SSE payload 不得含密钥；
2. SSE 只作为刷新提示；
3. 前端收到事件后应重新查询后端对象；
4. patch_applied 不等于验证通过；
5. command_completed 不等于业务完成；
6. build_completed 不等于 P5 completed；
7. mock SSE 必须显式标记。
```

---

## 16. 错误码契约

错误码建议：

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
environment_type_unsupported；
connection_profile_invalid；
environment_unreachable；
workspace_sync_failed；
execution_session_create_failed；
execution_session_lost；
build_failed；
test_failed；
runtime_start_failed；
runtime_health_check_failed；
output_redaction_required；
redaction_failed；
trace_write_failed；
audit_write_failed；
state_mismatch；
not_connected；
mock_not_allowed；
unknown_failure。
```

错误响应建议：

```json
{
  "request_id": "string",
  "error": {
    "code": "policy_blocked",
    "message": "安全摘要，不含密钥片段",
    "details_ref": "string | null",
    "next_actions": ["string"]
  },
  "trace_ref": "string | null",
  "redaction_status": "redacted"
}
```

规则：

```text
1. error.message 不得包含密钥；
2. unknown_failure 不得默认通过；
3. redaction_failed 不得返回原始 details；
4. audit_write_failed 必须阻断高风险动作完成；
5. credential_invalid 不得返回凭据片段。
```

---

## 17. 前端使用规则

前端必须：

```text
1. 使用 API 查询后端状态；
2. 通过 SSE 触发刷新而不是直接信任事件；
3. 显示 resource status；
4. 显示 Environment Profile 状态；
5. 显示 Execution Session 状态；
6. 显示 active_gate；
7. 显示 Trace / Audit 缺失状态；
8. 显示 mock / not_connected；
9. 显示 Artifact、Evidence candidate 与 Evidence validated 的区别。
```

前端不得：

```text
1. 将 API 200 显示为阶段完成；
2. 将 external_agent_completed 显示为业务完成；
3. 将 patch_generated 显示为 patch_applied；
4. 将 patch_applied 显示为验证通过；
5. 将 command_completed 显示为业务完成；
6. 将 environment_probe_completed 显示为环境验证通过；
7. 展示未脱敏 URL、token、日志或错误；
8. 用 toast 替代 Gate；
9. 隐藏 policy_blocked。
```

---

## 18. 安全与脱敏规则

不得出现在 API response、SSE、Trace、Audit、日志、报告或导出中：

```text
Key；
Token；
Secret；
Password；
GitHub token；
SSH private key；
远程主机密码；
Provider Key；
.env 明文；
连接串明文；
可还原密钥片段。
```

允许出现：

```text
credential_ref；
credential_type；
credential_status；
provider；
permission_scope；
connection_profile_ref；
remote_url_redacted；
redaction_status；
request_id；
trace_ref；
audit_ref。
```

---

## 19. R 阶段校准项

### 19.1 R2 文档校准

```text
1. 本文是否与最新决策记录一致；
2. 与 `03-流程与运行时/06-外部编程Agent接入流程.md` 是否一致；
3. 与 `03-流程与运行时/07-远程环境与执行会话规范.md` 是否一致；
4. 与 `04-模型与资源/08-外部执行器与工具资源总览.md` 字段定义是否一致；
5. 与 04/05/06/07 同级文档的去重引用是否到位；
6. 字段命名是否与状态与数据模型一致；
7. 是否仍无 Mission 产品层；
8. 是否存在旧阶段术语残留。
```

### 19.2 R4 API 校准

```text
1. API 路由是否与 FastAPI 实现一致；
2. ResourceDefinition / ResourceBinding 是否落地；
3. EnvironmentProfile / ExecutionSession 是否落地；
4. InvocationResult 是否落地；
5. Gate / Trace / Audit 引用是否可查询；
6. SSE 事件是否可映射前端。
```

### 19.3 R6 资源校准

```text
1. Resource Registry API 是否真实可用；
2. Tool / MCP / Expert Agent 是否可登记；
3. OpenCode 是否作为候选外部执行器；
4. 确定性转换器是否以资源形式接入；
5. 资源权限和风险是否可配置。
```

### 19.4 R8 / R11 / R12 校准

```text
R8：Environment Profile 与 Execution Session 是否真实支撑 Workspace；
R11：Patch、命令执行、构建、测试、运行是否真实可用并写 Trace；
R12：构建、测试、运行输出是否进入 P5 验证证据链，P6 是否只引用已验证证据。
```

---

## 20. API 红线

```text
1. 不得将 API 200 写成业务完成；
2. 不得将外部执行器输出写成当前事实；
3. 不得将 Patch generated 写成 Patch applied；
4. 不得将 Patch applied 写成验证通过；
5. 不得将命令 exit_code=0 写成业务完成；
6. 不得将构建成功写成 P5 completed；
7. 不得将测试通过直接写成 P5 completed；
8. 不得将服务启动写成业务正确；
9. 不得将 Environment Profile 创建成功写成环境可用；
10. 不得将 Execution Session 创建成功写成命令执行成功；
11. 不得返回明文 Key / Token / Secret / Password（D-032）；
12. 不得在 SSE payload 中携带密钥（D-032）；
13. 不得隐藏 policy_blocked、gate_required、trace_missing、audit_missing；
14. 不得绕过用户 Gate 执行高风险命令、写盘、远端写或外部系统写操作（D-034）；
15. 不得将 mock / not_connected 显示为真实能力；
16. 不得复活旧 RunEngine、旧 S0-S7、旧 F0-F6 或旧 Phase 主流程；
17. 不得引入 Mission 产品层（D-070）；
18. 不得在长期 API、DB、路由、组件、状态字段中固化 V26.1；
19. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 21. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 API 范围；
2. 明确通用约定；
3. 明确 5 个数据对象建议；
4. 明确 Resource Registry API；
5. 明确 Resource Binding API；
6. 明确 Context Package API；
7. 明确 External Executor / Tool / MCP API；
8. 明确 Patch API；
9. 明确 Environment Profile API；
10. 明确 Execution Session API；
11. 明确 Command / Build / Test / Runtime API；
12. 明确 Output API；
13. 明确 Gate API 关联（引用 05）；
14. 明确 Trace / Audit API（引用 04）；
15. 明确 SSE 事件契约（引用 07）；
16. 明确错误码契约（37 种）；
17. 明确前端使用规则；
18. 明确安全与脱敏规则；
19. 明确 R 阶段校准项；
20. 明确 API 红线（19 条）；
21. 未新增产品决策；
22. 未引入 Mission 产品层；
23. 未固定最终实现。
```

---

## 22. 待确认项

```text
1. §2.1 路由前缀——/api/resources 作为全局资源端点 vs 全部挂在 /api/projects/{id}/ 下，R4 需确认；
2. §9.2 Probe Environment——探测结果是否直接更新 Environment Profile 状态，还是仅作为 Artifact 返回由调用方自行判断；
3. §10.2 Session pause/resume——暂停期间 Workspace 是否冻结（禁止其他 Session 修改），R4/R8 需确定；
4. §11.2 Build/Test/Runtime——是否需要独立的 build_id/test_id/runtime_id 作为一级资源 ID，还是统一使用 command_id + command_type 区分；
5. 本文编号 `09-`——架构文档 §19 占位引用为 `10-`，建议 R2 统一校准为 `09-`（05-API 目录已落位 00-08，09 为下一可用编号）。已同步更新架构文档引用。
```
