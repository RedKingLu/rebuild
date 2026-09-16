# 07-SSE与事件契约

> 文档路径：`文档/05-API与集成契约/07-SSE与事件契约.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/已完成/R1/07-SSE与事件契约.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本中 SSE 与事件流的 R1 建议契约——统一整合 Run/Stage/TaskGraph/Gate/AET/Model/Resource/Workspace/Execution 各域事件，定义 Event Envelope、订阅、断线重连、事件日志、安全脱敏与前端联调边界。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-016, D-037）、`文档/05-API与集成契约/00-API与集成契约总览.md`（§6）、`文档/05-API与集成契约/01-字段规范与错误响应规范.md`、`文档/05-API与集成契约/02~06`（各域 API）。
> 重要边界：本文是 SSE 与事件流的 R1 建议契约，所有端点、字段、事件类型均为 R1 建议契约。**事件不是唯一状态源**——状态恢复依赖数据库、LangGraph checkpoint、workspace 文件、trace/audit、event log。
> 修订说明：R1 正式化版基于草稿 v0.1：(1) 头部标准化；(2) §0 新增关联决策速查表；(3) 新增 §24 待确认项（3 项）；(4) 验收标准扩展（20→22 项）。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守事实源层级（D-068）：项目治理 > 产品定义 > 架构设计 > 专题规范。

**关联决策速查**：

| 决策编号 | 内容 | 本文相关节 |
|---|---|---|
| D-016 | API 字段 R1 建议，R2 校准 | 全文 |
| D-037 | LangGraph 主编排——checkpoint/interrupt/resume 事件 | §9, §10 |

**核心原则：事件不是唯一状态源**。前端断线重连后必须能从后端状态恢复。事件用于提示刷新，不用于替代数据库、checkpoint、workspace、trace/audit。

本文必须遵守：

```text
1. API 字段 R1 输出建议契约，R2 校准为实现契约；
2. 状态恢复依赖数据库、LangGraph checkpoint、workspace 文件、trace/audit、event log；
3. 容器、终端或前端 UI 不能作为状态源；
4. 事件不是唯一状态源；
5. 前端断线重连后必须能从后端状态恢复；
6. 事件 payload 不得泄露 Key/Token/Secret/Password（D-032）；
7. Gate、高风险动作、Policy 冲突、外部写操作、Audit 相关事件必须可追踪。
```

本文不得：将 SSE 事件作为唯一状态源、用前端事件缓存替代后端状态、将 mock 事件伪装为真实事件、在 payload 中传递明文密钥（D-032）、通过事件绕过 Gate/Policy/Audit、引入 Mission 产品层（D-070）。

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. SSE 与事件定位

SSE 与事件流用于向前端推送运行过程中的状态变化。它回答：后端发生了什么、属于哪个 Project/Run/Stage/Node、前端应刷新哪些对象、是否存在阻塞、是否需要用户动作、断线后如何恢复。

SSE 与事件不是：唯一状态源、数据库替代品、LangGraph checkpoint 替代品、Trace/Audit 替代品、Gate 决策替代品、Evidence 替代品。

---

## 2. 事件对象边界

```text
Event Envelope：事件外壳（统一路由和恢复）；
Event Payload：事件载荷（只放必要摘要和引用）；
Event Source：事件来源（标记来源模块）；
Event Cursor：事件游标（断线恢复）；
Subscription：订阅会话；
Event Log：事件日志（恢复与审计辅助，不替代 Trace/Audit）；
SSE Stream：SSE 流连接（传输通道，不是状态源）。
```

---

## 3. API 路由归口建议

```text
GET /api/events/stream
GET /api/projects/{project_id}/events/stream
GET /api/projects/{project_id}/runs/{run_id}/events/stream
GET /api/projects/{project_id}/runs/{run_id}/stages/{stage}/events/stream
GET /api/projects/{project_id}/events
GET /api/projects/{project_id}/runs/{run_id}/events
GET /api/events/{event_id}
POST /api/events/replay
GET /api/events/subscriptions/{subscription_id}
DELETE /api/events/subscriptions/{subscription_id}
```

> 如实际采用 WebSocket 或混合方案，R2/R4 需明确变更原因和兼容策略。

---

## 4. Event Envelope 字段建议

```text
event_id：唯一；
event_type：机器可读；
event_version：R2 后兼容；
project_id：尽量必填；
run_id / stage / task_graph_id / node_id：按上下文可选；
source：来源模块；
severity：info / progress / success / warning / blocked / error / critical；
sequence + cursor：用于排序和断线恢复；
payload：只放引用和摘要，不得含密钥；
trace_ref / audit_ref：只传引用；
created_at。
```

---

## 5. Event Source 与 Severity

Source（18 种）：project_service / run_service / stage_service / task_graph_service / gate_service / authorization_service / policy_service / artifact_service / evidence_service / trace_service / audit_service / model_gateway / resource_registry / resource_runtime / workspace_service / execution_service / context_service / system

Severity（7 级）：info（普通）→ progress（进度）→ success（完成）→ warning（非阻塞风险）→ blocked（阻塞，必须显示原因）→ error（错误，含 request_id/trace_ref）→ critical（高风险/安全，突出显示）

---

## 6. 订阅参数与初始快照

订阅参数：`project_id`, `run_id`, `stage`, `event_types`, `severity_filter`, `from_cursor`, `from_event_id`, `include_snapshot`, `client_id`

Snapshot 字段：`project_summary`, `run_summary`, `stage_summary`, `active_gate`, `recent_events_cursor`, `open_evidence_gaps`, `background_task_summary`, `workspace_status`, `execution_session_status`

---

## 7. 断线重连与事件恢复

```text
1. 前端记录最后 cursor / event_id；
2. 重连时携带 from_cursor；
3. 后端返回缺失事件或 resync_required；
4. full_resync 时重新查询对象状态；
5. 恢复后继续接收新事件。
```

规则：SSE 断线≠任务停止、事件缺失不得由前端猜测状态、后端无法补发时应返回 resync_required、重连不得绕过权限校验。

---

## 8. 各域事件汇总

### Run / 后台任务
`run_created | started | status_changed | paused | interrupted | resumed | completed | failed | canceled | blocked | background_task_updated`
Payload：run_id, run_status, current_stage, active_gate, can_pause/cancel/resume, checkpoint_ref, next_actions

### Stage / P0-P6
`stage_status_changed | plan_submitted/approved/rejected | gate_created/decided | completed | rework_required | blocked | evidence_gap_detected`
Payload：stage, stage_status, stage_plan_ref, artifact_refs, evidence_refs, evidence_gap_refs, gate_ref, trace_ref, audit_ref

### TaskGraph / Node
`task_graph_created | approved | started | status_changed | completed | failed | node_status_changed | started | completed | failed | waiting_gate | acceptance_recorded | edge_traversed | rework_required`

### Gate / Authorization / Policy
`gate_created | waiting_decision | decided | approved | rejected | canceled | expired | authorization_requested/approved/rejected | policy_check_completed | policy_blocked | high_risk_gate_required | gate_audit_written`

### AET（Artifact/Evidence/Trace/Audit）
`artifact_created | status_changed | promoted | evidence_submitted | validated | rejected | gap_created/resolved | trace_written | audit_written | claim_supported/unsupported`

### Model / Resource
`model_call_started/completed/failed | usage_recorded | fusion_evaluation_completed | resource_registered | status_changed | check_completed | call_started/completed/failed | blocked | gate_required | converter_dry_run_completed | converter_patch_generated`

### Workspace / Execution
`workspace_created | status_changed | file_changed | artifact_file_written | execution_session_created/started/output/completed/failed | runtime_status_changed | command_waiting_gate | external_write_waiting_gate`

### 错误事件
`validation_error | permission_denied | policy_blocked | gate_required | gate_not_resolved | evidence_missing | trace_missing | audit_missing | checkpoint_missing | state_mismatch | resource_unavailable | model_unavailable | integration_failed | execution_failed | redaction_required | resync_required | internal_error`

---

## 9. 事件日志与状态恢复

Event Log 字段：event_id, sequence, cursor, event_type, project_id, run_id, stage, source, payload_ref, trace_ref, audit_ref, created_at, retention_policy

恢复规则：event log 是恢复辅助非唯一状态源、状态恢复必须结合多源（数据库+checkpoint+workspace+trace/audit+event log）、事件缺失返回 resync_required、不得保存密钥明文、高风险事件关联 Audit。

---

## 10. 事件安全与脱敏

```text
1. payload 不得含 Key/Token/Secret/Password；
2. 凭据事件只展示 credential_ref/redacted/credential_status；
3. 错误事件不得含内部堆栈或敏感路径；
4. output 事件不得推送 env 明文；
5. redaction_required 阻断敏感内容下发；
6. 订阅范围必须权限校验；
7. Audit 事件详情可能需要更高权限。
```

---

## 11. 前端联调边界

前端必须：将事件作为刷新提示而非唯一事实源、断线后使用 cursor 恢复或 full_resync、显示 mock/未接真实服务能力、显示 Gate/Policy/Evidence 缺口、对 critical/blocked 事件突出提示。

前端不得：用事件缓存替代后端状态、隐藏 gate_required、把 evidence_missing 显示为完成、把 trace_written 显示为验证通过、把 mock SSE 伪装为真实事件、展示明文密钥。

---

## 12. R2 / R4 / R9-R12 校准项

- **R2**：事件类型与各 API 专题文档一致、仍坚持事件不是唯一状态源
- **R4**：SSE FastAPI 路由、event envelope schema、cursor/replay 实现、LangGraph 事件接入、脱敏 middleware、前端重连策略
- **R9-R12**：逐链路校准各域事件可用性（基础事件→规划事件→执行事件→验证事件）

---

## 13. SSE 与事件红线

```text
1. 不得将 SSE 事件作为唯一状态源；
2. 不得用前端事件缓存替代数据库、checkpoint、workspace、trace/audit、event log；
3. 不得用容器、终端或前端 UI 作为状态源；
4. 不得将 mock 事件伪装为真实后端事件；
5. 不得在事件 payload 中传递 Key/Token/Secret/Password（D-032）；
6. 不得通过事件绕过 Gate/Policy/Audit；
7. 不得将 gate_required 事件当作自动重试事件；
8. 不得将 evidence_missing 事件显示为 completed；
9. 不得将 trace_written 或 audit_written 显示为验收通过；
10. 不得将模型输出或资源输出事件显示为 Evidence validated；
11. 不得隐藏 resync_required；
12. 不得引入 Mission 产品层（D-070）；
13. 不得在长期事件字段中固化 V26.1；
14. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 14. 本文验收标准

```text
1. 明确 SSE 与事件定位（事件≠状态源）；
2. 明确事件对象边界（7 对象）；
3. 明确 API 路由归口（11 端点）；
4. 明确 Event Envelope 字段（14 字段）；
5. 明确 Event Source（18 种）、Severity（7 级）、订阅参数、初始快照；
6. 明确断线重连与 cursor 恢复；
7. 明确各域事件：Run（11）/ Stage（10）/ TaskGraph（14）/ Gate（14）/ AET（14）/ Model（15）/ Workspace（12）/ 错误（17）；
8. 明确事件日志与多源状态恢复；
9. 明确事件安全与脱敏（7 条）；
10. 明确前端联调边界；
11. 明确 R2/R4/R9-R12 校准项；
12. 明确 SSE 与事件红线（14 条）；
13. 未新增产品决策；
14. 未引入 Mission 产品层；
15. 未固定最终实现 schema；
16. 决策交叉引用完整（D-016/D-037）；
17. 待确认项显式列出。
```

---

## 15. 待确认项

```text
1. §3 传输协议——当前为 SSE，若 R4 评估后 WebSocket 更合适（如需要双向通信），是否需要保留 SSE 作为降级方案还是直接替换。当前建议 SSE 为主，WebSocket 可选。

2. §6 include_snapshot 的粒度——snapshot 是内嵌在首次 SSE 事件中还是通过单独 API 查询（当前设计为订阅参数 include_snapshot=true 时返回）。若 snapshot 数据量大（如大型 TaskGraph），可能需要分页或降级为引用。

3. §9 Event Log 保留策略——retention_policy 字段为占位。R4 需确定：按时间（如 7 天）、按 Run（Run 结束后 N 天清理）、按数量（最近 N 条）还是组合策略。
```
