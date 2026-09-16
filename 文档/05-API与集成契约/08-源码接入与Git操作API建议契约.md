# 08-源码接入与Git操作API建议契约

> 文档路径：`文档/05-API与集成契约/08-源码接入与Git操作API建议契约.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/已完成/R1/08-项目接入契约-本地-Git-ZIP-GitHub.md`（v0.1）+ `产物/草稿/源码接入与Git API契约.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本中项目接入与 Git 操作的全链路 API 建议契约——前半部分覆盖本地目录、Git、ZIP、GitHub 四类接入的 Import Job 契约（原 08 内容 + D-058），后半部分覆盖接入完成后的 Git 工作流操作 API（status / diff / branch / commit / push / credential status）。本文是 02-Project-Run-阶段API 的接入层与操作层补充——02 覆盖 Project CRUD，本文覆盖源码进入 Workspace 的全流程及后续 Git 操作。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-016, D-058）、`文档/05-API与集成契约/00-API与集成契约总览.md`（§8, §22）、`文档/05-API与集成契约/01-字段规范与错误响应规范.md`、`文档/05-API与集成契约/02-Project-Run-阶段API建议契约.md`、`文档/05-API与集成契约/04-Artifact-Evidence-Trace-Audit API建议契约.md`、`文档/05-API与集成契约/05-Gate-Authorization API建议契约.md`、`文档/05-API与集成契约/07-SSE与事件契约.md`、`文档/07-安全与权限/03-命令-Tool-MCP-Git-写盘授权规范.md`。
> 重要边界：本文所有端点、字段、状态和错误码均为 R1 建议契约，R2/R4/R7/R9 需按真实实现校准为实现契约。
> 修订说明：R1 补录合并版（2026-06-24）：(1) 基于用户裁决"合并至 08"，将 `源码接入与Git API契约.md` 草稿去重后的 Git 操作 API 内容并入本文；(2) 文档改名：`08-项目接入契约-本地-Git-ZIP-GitHub.md` → `08-源码接入与Git操作API建议契约.md`；(3) 新增 §11 Git 操作 API 契约（6 端点 + 凭据状态查询）；(4) 新增 §12 Git 操作事件与错误码；(5) §13-§16 对应原 §11-§14 重新编号并扩展覆盖 Git 操作维度；(6) §0 补入关联决策速查（Git 操作相关）；(7) 删除草稿中与本文 §1-§8 及 04/05/07 重复的 SourceIntegration/本地目录/ZIP/Repository Clone/Gate/Artifact/Trace/SSE 通用结构复述（~500 行）。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守事实源层级（D-068）。本文是 02-Project-Run-阶段API 的接入层与操作层补充——02 覆盖 Project CRUD，本文覆盖源码进入 Workspace 及后续 Git 工作流操作。

**关联决策速查**：

| 决策编号 | 内容 | 本文相关节 |
|---|---|---|
| D-016 | API 字段 R1 建议，R2 校准 | 全文 |
| D-058 | 项目接入首批全支持本地目录/Git/ZIP/GitHub | §1-§8 |
| D-033 | Git 操作必须先映射风险级别 | §11, §14 |
| D-034 | L5 高风险动作暂定全部强制用户 Gate | §11.7, §14 |

本文必须遵守：

```text
1. API 字段 R1 输出建议契约，R2 校准为实现契约；
2. 项目接入首批支持本地目录、Git、ZIP、GitHub（D-058）；
3. 每个用户项目必须有独立 Project Workspace；
4. 接入成功≠P0 完成，接入成功≠迁移完成；
5. Git 操作必须先映射风险级别（D-033）；
6. Git push 等远端写操作必须按 L5 高风险处理（D-034）；
7. API 响应、SSE、Trace、Audit、前端不得泄露 Key/Token/Secret/Password（D-032）；
8. 当前版本不设独立 Mission 产品层（D-070）。
```

本文不得：将接入完成等同于 P0 完成或迁移完成、将凭据明文写入 API/事件/Trace/Audit/前端（D-032）、让 ZIP 解压越过 Workspace 边界、用前端状态替代后端状态、将 mock 接入伪装为真实接入、将 API 200 写成业务完成、将 Git push 成功写成 P6 交付通过。

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 项目接入定位

项目接入是 Project 进入 P0/P1 流程前后的源码输入与 Workspace 初始化能力。它回答：源码来自哪里、如何进入 Workspace、接入是否成功、产生哪些 Artifact/Evidence 候选、是否需要凭据、是否触发 Gate/Policy、如何被追踪。

项目接入不是：P0 自动完成、P1 自动建档、P5 验证、迁移执行、交付、凭据管理系统本体。

---

## 2. 接入对象边界

```text
Import Job ── 1 Import Source ── 1 Workspace Binding
Import Job ── n Import Artifact / Evidence Candidate / Trace / Audit / Event
```

对象：Import Job、Import Source（local_dir/git/zip/github）、Import Result、Source Credential（只引用，不存明文）、Workspace Binding、Import Artifact/Evidence Candidate/Event/Error

边界：Import Job≠Run、Import Source 不保存凭据明文、Workspace Binding 只指向 Project Workspace、Import Artifact 不自动=Evidence validated。

---

## 3. API 路由归口建议

```text
POST /api/projects/{project_id}/imports
GET /api/projects/{project_id}/imports
GET /api/projects/{project_id}/imports/{import_id}
POST /api/projects/{project_id}/imports/{import_id}/cancel
POST /api/projects/{project_id}/imports/{import_id}/retry
GET /api/projects/{project_id}/imports/{import_id}/events
GET /api/projects/{project_id}/imports/{import_id}/artifacts
GET /api/projects/{project_id}/imports/{import_id}/evidence-candidates
POST /api/projects/{project_id}/imports/local-dir
POST /api/projects/{project_id}/imports/git
POST /api/projects/{project_id}/imports/zip
POST /api/projects/{project_id}/imports/github
```

> 如实际采用统一 `POST /imports` + `source_type`，也必须保持四类接入语义清晰。

---

## 4. 通用 Import Job 字段与状态

字段：`import_id`, `project_id`, `source_type`（local_dir/git/zip/github）, `source_ref`（不含凭据明文）, `credential_ref`（只引用）, `credential_status`, `workspace_ref`, `import_status`, `progress`, `current_step`, `artifact_refs`, `evidence_candidate_refs`, `trace_ref`, `audit_ref`, `error_ref`

状态（15 种）：`created → validating_source → waiting_credential → credential_checking → preparing_workspace → importing → extracting → scanning → indexing → waiting_gate | blocked | failed | canceled | completed | completed_with_warnings`

规则：completed 仅表示接入任务完成≠P0 完成、completed_with_warnings 必须有 warnings、waiting_gate 必须关联 gate_ref、failed 必须有 error_ref、状态变化必须 Trace、高风险或外部写操作必须 Audit。

---

## 5. 四类接入契约

### 本地目录

字段：`local_path_ref`, `copy_mode`, `include_patterns`, `exclude_patterns`, `follow_symlinks`（须防越界）, `scan_options`
规则：local_path_ref 不暴露敏感绝对路径、copy_mode 说明复制/引用/受控同步语义、不得越过 Workspace 边界。

### Git

字段：`git_url_ref`（不含用户名密码/token）, `branch`, `tag`, `commit`, `depth`, `credential_ref`
规则：credential_ref 只引用凭据、branch/tag/commit 冲突策略 R2 校准、clone 失败返回明确错误、私有仓库失败不泄露凭据。

### ZIP

字段：`zip_upload_ref`, `checksum_ref`, `extract_root`, `overwrite_policy`
规则：解压必须受 Workspace 边界控制、必须防路径穿越、ZIP 文件名不作信任依据、overwrite_policy 必须明确。

### GitHub

字段：`github_repo_ref`, `owner_ref`, `repo_ref`, `branch`, `tag`, `commit`, `credential_ref`
规则：token 不得出现在响应/Trace/Audit/事件/前端、credential_status 可显示 exists/missing/invalid/redacted、可复用 Git 底层语义但 API 层保留 source_type=github。

---

## 6. Workspace 落点与接入产物

Workspace 字段：`workspace_ref`, `workspace_root_ref`, `source_snapshot_ref`, `material_view_ref`, `code_view_ref`, `import_manifest_ref`, `file_index_ref`

接入 Artifact：`import_manifest`（接入清单）、`source_snapshot`（源码快照引用）、`file_index`（文件索引）、`tech_stack_hint`、`import_warning_report`、`import_error_report`

Evidence 候选：`source_accessible`、`workspace_initialized`、`source_snapshot_created`、`file_index_created`、`credential_validated`（只证明凭据状态，不暴露凭据）

规则：接入 Artifact 不自动=Evidence validated、Evidence 候选必须进入验证流程、凭据证据不得暴露凭据、接入证据不足不得让 P0 completed。

---

## 7. 凭据与脱敏契约

凭据字段：`credential_ref`（只引用）、`credential_status`（exists/missing/invalid/redacted）、`credential_type`、`credential_scope`、`last_checked_at`、`validation_error_ref`

规则：API 不得返回明文 Key/Token/Secret/Password、Git/GitHub 凭据不得进入 source_ref、错误响应/Trace/Audit/Event 不得含凭据明文、前端只能展示 credential_status 和 redacted 标识。

---

## 8. 接入前检查契约

检查项：Project 是否存在、Workspace 是否可用、source_type 是否支持、source_ref 格式、凭据状态、权限、路径/仓库/文件可访问性、风险级别是否需要 Gate、Workspace 冲突/覆盖风险。

响应：`precheck_id`, `allowed`, `requires_gate`, `requires_credential`, `credential_status`, `workspace_ready`, `blocking_reasons`, `warnings`

规则：precheck 通过≠接入成功、allowed=true 不代表无需 Gate、overwrite/外部写操作需 Gate、precheck 失败不得继续接入。

---

## 9. 接入事件与错误响应

事件（17 种）：`import_created → precheck_started/completed → credential_required/checked → workspace_preparing → started → progress_updated → extracting → scanning → indexing → completed/completed_with_warnings/failed/canceled → gate_required → artifact_created → evidence_candidate_created`

错误（24 种）：含域特定错误 `zip_path_traversal_blocked`、`git_clone_failed`、`git_ref_not_found`、`github_repo_not_found`、`github_rate_limited`、`github_permission_denied`、`workspace_conflict` 等

---

## 10. 前端联调边界

前端必须标记：mock/未接真实服务、凭据已脱敏/缺失/无效、接入中、接入完成但 P0 未必完成、带警告完成、等待 Gate、Evidence 候选而非已验证 Evidence。

前端不得：展示 Git/GitHub token、将 ZIP 解压完成显示为迁移完成、将 import_completed 显示为 P0 completed、将 Evidence 候选显示为 validated、隐藏接入/凭据失败、将 mock 伪装为真实接入。

---

## 11. Git 操作 API 契约

> 本章定义接入完成后的持续 Git 工作流操作 API。接入阶段（Import Job）的端点见 §3-§5，本章不重复。Gate 查询/决策的完整 API 见 `05-Gate-Authorization API建议契约.md`，Artifact/Evidence/Trace/Audit 查询 API 见 `04-Artifact-Evidence-Trace-Audit API建议契约.md`，SSE 通用结构见 `07-SSE与事件契约.md`。

### 11.0 GitOperation 数据对象

```json
{
  "operation_id": "string",
  "project_id": "string",
  "repository_ref": "string",
  "operation_type": "clone | fetch | pull | status | diff | branch_create | checkout | commit | push",
  "risk_level": "L0 | L1 | L2 | L3 | L4 | L5",
  "policy_check_ref": "string | null",
  "gate_ref": "string | null",
  "trace_ref": "string | null",
  "audit_ref": "string | null",
  "status": "pending | running | completed | failed | blocked | gate_required",
  "started_at": "string | null",
  "finished_at": "string | null",
  "summary_ref": "string | null",
  "error_ref": "string | null",
  "redaction_status": "clean | redacted | redaction_required | redaction_failed"
}
```

Git 操作按风险分为三类（详见 `文档/07-安全与权限/03-命令-Tool-MCP-Git-写盘授权规范.md`）：

```text
读取类（L0-L1）：git status / log / diff / fetch / branch list
本地变更类（L2-L3）：git checkout / branch create / add / commit
远端写类（L5）：git push / remote branch create / remote branch delete / tag push / PR-MR create
```

风险级别映射、Policy Check 链路、Gate 触发条件详见 `文档/07-安全与权限/02-风险分级与Gate策略.md`。

### 11.1 查询仓库状态

```text
GET /api/projects/{project_id}/repositories/{repository_ref}/status
```

响应体建议：

```json
{
  "request_id": "string",
  "repository": {
    "repository_ref": "string",
    "provider": "git | github | other",
    "remote_url_redacted": "string",
    "default_branch": "string | null",
    "current_branch": "string | null",
    "commit_sha": "string | null",
    "working_tree_status": "clean | dirty | conflict | unknown",
    "credential_status": "valid | missing | invalid | expired | permission_limited | unknown",
    "read_capability": true,
    "write_capability": false,
    "last_sync_at": "string | null",
    "status": "connected | disconnected | failed | not_connected | mock | unknown"
  },
  "trace_ref": "string | null"
}
```

### 11.2 Git Status

```text
POST /api/projects/{project_id}/git/status
```

请求体建议：

```json
{
  "repository_ref": "string",
  "workspace_ref": "string | null"
}
```

响应体建议：

```json
{
  "request_id": "string",
  "operation_id": "string",
  "status": "completed | failed | blocked",
  "working_tree_status": "clean | dirty | conflict | unknown",
  "current_branch": "string | null",
  "commit_sha": "string | null",
  "summary_ref": "string | null",
  "trace_ref": "string | null",
  "error": null
}
```

### 11.3 Git Pull / Fetch

```text
POST /api/projects/{project_id}/git/pull
POST /api/projects/{project_id}/git/fetch
```

请求体建议：

```json
{
  "repository_ref": "string",
  "branch": "string | null",
  "strategy": "fast_forward_only | merge | rebase | fetch_only",
  "allow_dirty_workspace": false
}
```

响应体建议：

```json
{
  "request_id": "string",
  "operation_id": "string",
  "status": "running | completed | failed | blocked | gate_required",
  "before_commit_sha": "string | null",
  "after_commit_sha": "string | null",
  "working_tree_status": "clean | dirty | conflict | unknown",
  "gate_ref": "string | null",
  "trace_ref": "string | null",
  "summary_ref": "string | null",
  "error": null
}
```

规则：

```text
1. dirty workspace 不得静默覆盖；
2. pull 冲突必须返回 git_conflict；
3. pull 成功不等于 P0 completed；
4. 输出必须脱敏。
```

### 11.4 Git Diff

```text
POST /api/projects/{project_id}/git/diff
```

请求体建议：

```json
{
  "repository_ref": "string",
  "base_ref": "string | null",
  "target_ref": "string | null",
  "paths": ["string"],
  "redaction_policy": "default | strict"
}
```

响应体建议：

```json
{
  "request_id": "string",
  "operation_id": "string",
  "status": "completed | failed | blocked | redaction_required",
  "diff_summary_ref": "string | null",
  "diff_artifact_ref": "string | null",
  "redaction_status": "clean | redacted | redaction_required | redaction_failed",
  "trace_ref": "string | null",
  "error": null
}
```

规则：

```text
1. diff 可作为 Artifact candidate；
2. diff 不自动成为 Evidence validated；
3. diff 中的密钥必须脱敏；
4. redaction_failed 不得返回原始 diff。
```

### 11.5 Branch / Checkout

```text
POST /api/projects/{project_id}/git/branches
POST /api/projects/{project_id}/git/checkout
```

创建分支请求体建议：

```json
{
  "repository_ref": "string",
  "new_branch": "string",
  "base_ref": "string | null"
}
```

切换分支请求体建议：

```json
{
  "repository_ref": "string",
  "branch": "string",
  "allow_dirty_workspace": false
}
```

响应体建议：

```json
{
  "request_id": "string",
  "operation_id": "string",
  "status": "completed | failed | blocked | gate_required",
  "current_branch": "string | null",
  "gate_ref": "string | null",
  "trace_ref": "string | null",
  "error": null
}
```

规则：

```text
1. 分支切换如可能覆盖工作区变更，必须 Gate；
2. branch_not_found 必须明确；
3. checkout 成功不等于任务完成。
```

### 11.6 Commit Changes

```text
POST /api/projects/{project_id}/git/commit
```

请求体建议：

```json
{
  "repository_ref": "string",
  "message": "string",
  "paths": ["string"],
  "diff_summary_ref": "string | null",
  "require_gate": true
}
```

响应体建议：

```json
{
  "request_id": "string",
  "operation_id": "string",
  "status": "completed | failed | blocked | gate_required",
  "commit_sha": "string | null",
  "change_summary_ref": "string | null",
  "gate_ref": "string | null",
  "trace_ref": "string | null",
  "audit_ref": "string | null",
  "error": null
}
```

规则：

```text
1. commit 需要校验变更范围；
2. 高风险文件变更必须 Gate；
3. commit 成功不等于验证通过；
4. commit message 不得包含密钥。
```

### 11.7 Push Changes

```text
POST /api/projects/{project_id}/git/push
```

请求体建议：

```json
{
  "repository_ref": "string",
  "remote": "origin",
  "branch": "string",
  "commit_sha": "string",
  "change_summary_ref": "string | null",
  "diff_summary_ref": "string | null",
  "require_user_gate": true,
  "idempotency_key": "string | null"
}
```

响应体建议：

```json
{
  "request_id": "string",
  "operation_id": "string",
  "status": "gate_required | running | completed | failed | blocked",
  "gate_ref": "string | null",
  "audit_ref": "string | null",
  "trace_ref": "string | null",
  "remote_url_redacted": "string | null",
  "branch": "string | null",
  "commit_sha": "string | null",
  "error": null
}
```

规则：

```text
1. push 必须用户 Gate（L5 高风险动作，D-034）；
2. push 必须 Audit；
3. push 成功不等于 P6 交付通过；
4. push 失败不得静默重试；
5. remote_url_redacted 不得含 token；
6. Gate 审核必须展示目标仓库、目标分支、commit 和变更摘要。
```

### 11.8 凭据状态查询

```text
GET /api/projects/{project_id}/credentials/{credential_ref}/status
```

响应体建议：

```json
{
  "request_id": "string",
  "credential": {
    "credential_ref": "string",
    "credential_type": "git_token | github_token | ssh_key | username_password | unknown",
    "provider": "git | github | other | unknown",
    "credential_status": "missing | provided | valid | invalid | expired | permission_limited | unknown",
    "permission_scope": ["read", "write"],
    "redaction_status": "redacted"
  },
  "trace_ref": "string | null"
}
```

规则：

```text
1. 只返回状态，不返回明文；
2. invalid / expired 不返回失败密钥片段；
3. permission_limited 必须说明受限能力；
4. 查询凭据状态也应记录 Trace。
```

---

## 12. Git 操作事件与错误码

### 12.1 Git 特有 SSE 事件

以下事件在接入事件（§9）的基础上扩展，覆盖 Git 工作流操作。SSE 通用结构见 `07-SSE与事件契约.md`。

```text
git_status_completed；
git_pull_started；
git_pull_completed；
git_conflict_detected；
git_diff_completed；
git_branch_created；
git_checkout_completed；
git_commit_completed；
git_push_gate_required；
git_push_completed；
git_push_failed；
credential_invalid；
resync_required。
```

规则：

```text
1. SSE payload 不得含密钥；
2. SSE 只作为刷新提示；
3. 前端收到事件后应重新查询后端对象；
4. git_push_completed ≠ P6 completed；
5. mock SSE 必须显式标记。
```

### 12.2 Git 特有错误码

以下错误码在接入错误（§9，24 种）的基础上扩展，覆盖 Git 操作特有错误。通用错误码（参数校验、认证等）见 `01-字段规范与错误响应规范.md`。

```text
git_clone_failed；
git_pull_failed；
git_conflict；
git_commit_failed；
git_push_rejected；
branch_not_found；
workspace_dirty；
credential_missing；
credential_invalid；
credential_expired；
git_remote_write_blocked；
not_connected；
mock_not_allowed。
```

错误响应结构：

```json
{
  "request_id": "string",
  "error": {
    "code": "git_push_rejected",
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
2. git_conflict 必须提供 next_actions（展示冲突文件摘要和推荐策略）；
3. git_push_rejected 必须提供远端拒绝原因的安全摘要；
4. credential_invalid 不得返回凭据片段；
5. workspace_dirty 必须提供 next_actions（stash / commit / discard 选项）。
```

---

## 13. R2 / R4 / R7 / R9 / R11 / R12 校准项

### 13.1 R2 文档校准

```text
1. 本文是否与最新决策记录一致；
2. 与 03-流程与运行时/05-项目接入与源码同步流程.md 是否一致；
3. 与 07-安全与权限/03-命令-Tool-MCP-Git-写盘授权规范.md Git 章节是否去重、互补；
4. Import Job 路由 vs Git 操作路由是否统一为同一前缀风格；
5. Git 操作端点是否与 FastAPI 路由规划一致；
6. 是否仍无 Mission 产品层；
7. 是否存在旧阶段术语残留。
```

### 13.2 R4 API 校准

```text
1. Import API 路由、Pydantic schema、Import Job 数据模型、脱敏 middleware；
2. Git 操作 API 的路由实现、GitOperation 数据模型；
3. Repository 对象是否落地；
4. Credential Status API 是否脱敏；
5. Gate / Trace / Audit 引用是否可查询；
6. SSE 事件是否可映射前端。
```

### 13.3 R7 集成校准

```text
1. 四类接入实现（本地/Git/ZIP/GitHub）；
2. 凭据引用和状态检查；
3. Workspace 边界保护；
4. Git clone / pull / status / diff API 是否真实可用；
5. Git branch / checkout API 是否真实可用；
6. 接入 Artifact / Evidence candidate / Trace 是否真实生成。
```

### 13.4 R9 / R11 / R12 执行与交付校准

```text
R9：接入结果进入 P0、接入 Artifact 被 P1 引用、Evidence 候选验证、接入失败阻断 P0 completed；
R11：Git diff / commit 支撑 P4 执行、Git 状态变更可追溯；
R12：Git push 强制 Gate / Audit、Git 变更进入 P5 验证证据链、P6 只引用验证后的 Git 变更和交付材料。
```

---

## 14. 红线

```text
1. 不得将 Project 创建等同于项目接入完成；
2. 不得将项目接入完成等同于 P0 完成；
3. 不得将接入完成等同于迁移完成；
4. 不得将 Evidence 候选自动标记为 Evidence validated；
5. 不得让 ZIP 解压越过 Project Workspace 边界；
6. 不得允许 ZIP 路径穿越；
7. 不得将 Git/GitHub 凭据明文写入 source_ref（D-032）；
8. 不得在 API 响应、错误、事件、Trace、Audit、前端中泄露 Key/Token/Secret/Password（D-032）；
9. 不得将 mock 接入伪装为真实接入；
10. 不得用前端状态替代后端状态；
11. 不得引入 Mission 产品层（D-070）；
12. 不得在长期 API/DB/路由中固化 V26.1；
13. 不得把 R1 建议契约伪装为实现契约；
14. 不得绕过用户 Gate 执行 git push（L5）（D-034）；
15. 不得将 git push 成功显示为 P6 交付通过；
16. 不得将 API 200 写成业务完成；
17. 不得将 git clone / pull 成功写成 P0 自动完成；
18. 不得将 git commit / push 与 git clone / pull 混为同一风险级别。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 15. 本文验收标准

```text
1. 明确项目接入定位（接入≠P0≠迁移）；
2. 明确接入对象边界（9 对象）；
3. 明确 Import Job API 路由归口（12 端点）；
4. 明确 Import Job 字段（23 字段）与状态（15 种）；
5. 明确四类接入契约：本地目录/Git/ZIP/GitHub；
6. 明确 Workspace 落点与接入产物（6 Artifact + 5 Evidence 候选）；
7. 明确凭据与脱敏契约（credential_ref 只引用）；
8. 明确接入前检查契约（10 检查项）；
9. 明确接入事件（17 种）与错误响应（24 种）；
10. 明确 GitOperation 数据对象与风险分类（3 类）；
11. 明确 Git 操作 API（6 端点：status / pull / diff / branch / commit / push）；
12. 明确凭据状态查询 API；
13. 明确 Git 特有 SSE 事件（13 种）与错误码（13 种）；
14. 明确前端联调边界（接入 + Git 操作）；
15. 明确 R2/R4/R7/R9/R11/R12 校准项；
16. 明确红线（18 条，含 Git 操作 4 条新增）；
17. 未新增产品决策；
18. 未引入 Mission 产品层；
19. 未固定最终实现 schema；
20. 决策交叉引用完整（D-016/D-058/D-033/D-034）；
21. 待确认项显式列出。
```

---

## 16. 待确认项

```text
接入相关（原 §14 保留）：
1. §3 路由设计——当前四类接入各有独立端点（/local-dir, /git, /zip, /github），同时也有统一 POST /imports。R4 是否统一为 POST /imports + source_type 字段区分，还是保留独立端点。
2. §4 Import Job 状态 15 种——extracting/scanning/indexing 三个步骤状态是否需要独立，还是合并为统一的 processing 状态。
3. §6 接入 Evidence 候选——credential_validated 只证明凭据状态而不暴露凭据。若凭据无效（credential_invalid），是否仍需生成 credential_validated 的 Evidence 候选（标记为 failed）以保持证据链完整。

Git 操作相关（本次补录新增）：
4. §11 路由前缀——Git 操作端点使用 /api/projects/{id}/git/，与 Import Job 的 /api/projects/{id}/imports/ 前缀风格是否统一接受，还是统一为 /api/projects/{id}/repositories/{ref}/git/。
5. §11.4 Git Diff——diff 输出是否直接作为 Artifact candidate 存储于 Workspace artifacts_root，还是只存储 diff_summary_ref 引用。
6. §11.6 Commit——高风险文件变更触发 Gate 的具体判断标准（文件数阈值、文件类型、变更行数）需 R2/R4 确定。
7. §12.1 SSE 事件——Git 操作 SSE 事件是否与接入 SSE 事件共享同一 event stream，还是按资源类型分离 stream。建议共享以减少前端连接数，待 R4 确认。
```
