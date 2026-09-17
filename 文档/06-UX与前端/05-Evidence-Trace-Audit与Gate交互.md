# 05-Evidence-Trace-Audit与Gate交互

> 文档路径：`文档/06-UX与前端/05-Evidence-Trace-Audit与Gate交互.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/05-Evidence-Trace-Audit与Gate交互.md`（Copilot v0.1，2026-06-23）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本前端中 Evidence/Trace/Audit/Gate 四对象交互的 R1 正式候选规范——覆盖四对象边界（Evidence 证明结果/Trace 解释过程/Audit 记录审计/Gate 承载决策）、右侧检视分区与优先级、Gate 卡片（17 字段+10 状态+9 决策动作）、阶段晋级 Gate/高风险动作 Gate/Policy 阻断三种场景、Evidence 卡片+Evidence Gap 卡片+6 验证状态、Trace/Audit 卡片、联合时间线、Artifact-Evidence 联动、阶段页/Workspace 联动、用户决策确认体验、SSE 事件 17 种、状态恢复、安全脱敏、mock 边界，以及 R2/R3/R4/R9-R12 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-001~D-010, D-016, D-023~D-034, D-045~D-055, D-066, D-068, D-070）、`文档/06-UX与前端/00-UX与前端总览.md`（§13-§15）、`文档/06-UX与前端/03-ProjectWorkspace布局规范.md`（§18-§21）、`文档/06-UX与前端/04-文件面板与材料视图.md`（§8-§11）、`文档/05-API与集成契约/04-Artifact-Evidence-Trace-Audit API建议契约.md`、`文档/05-API与集成契约/05-Gate-Authorization API建议契约.md`、`文档/05-API与集成契约/07-SSE与事件契约.md`
> 重要边界：本文是 UX 与前端专题下的 Evidence/Trace/Audit/Gate 交互规范，不替代产品定义、Gate/Authorization API、AET API、SSE 契约、状态与数据模型、安全权限规范或前端代码实现。本文为 R1 正式候选契约，R2/R3/R4/R9-R12 需按真实实现和联调结果校准。
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 Evidence/Trace/Audit/Gate 前端交互，不重新定义上级事实。

### 0.1 本文必须遵守

```text
1.  Artifact 不自动晋升为项目文档
2.  Artifact 不自动成为 Evidence
3.  Evidence candidate 不自动成为 Evidence validated（D-066）
4.  P 阶段晋级 Gate 必须用户授权（D-023）
5.  安全机制采用 Hook+Policy+Security/Authorization Agent 三层（D-030~D-034）
6.  Policy 是硬约束，优先级高于 Agent 判断（D-030）
7.  Security/Authorization Agent 不能批准 Policy 禁止的动作（D-031）
8.  L5 高风险动作暂定全部强制用户 Gate（D-034）
9.  P5 验证必须基于可验证证据（D-066）
10. 验证不通过或证据不足时不得标记 completed（D-066）
11. No Evidence / No Trace, No Trusted Result（D-066）
12. Gate 决策必须可追溯 Trace/Audit（D-023, D-034）
13. 前端不得用 UI 状态替代后端 Gate/Evidence/Trace/Audit 事实源（D-053）
14. 密钥与敏感内容必须脱敏（AGENTS.md §12）
15. mock 必须显式标记（D-049）
```

### 0.2 本文不得

```text
1.  把 Gate 显示为普通 toast 后继续执行（D-023）
2.  隐藏 Active Gate（D-023）
3.  隐藏 Evidence 缺口（D-066）
4.  隐藏 Trace/Audit 缺失
5.  把 Audit 显示为用户授权本身
6.  把 Trace 显示为 Evidence
7.  把 Artifact 显示为 Evidence validated（D-066）
8.  把模型输出或资源输出显示为 Evidence validated（D-066）
9.  把 Policy 禁止项显示为可批准（D-030）
10. 用前端本地状态判断 Gate resolved、Evidence validated 或 Stage completed（D-053）
11. 展示 Key/Token/Secret/Password（AGENTS.md §12）
12. 将 mock Gate/mock Evidence/mock Trace/mock Audit 显示为真实能力（D-049）
13. 引入 Mission 产品层（D-070）
14. 使用旧 Phase/旧 F0-F6/旧 S0-S7 作为当前主流程
```

---

## 1. 交互目标

Evidence/Trace/Audit/Gate 交互的目标是让用户理解：

```text
1. 当前结果是否有证据支撑
2. 当前流程为什么暂停
3. 哪个 Gate 需要用户决策
4. 哪些风险被接受、拒绝或阻断
5. 哪些高风险动作已被审计
6. 哪些 Evidence 仍不足
7. 哪些 Trace 可解释运行过程
8. 当前阶段是否具备可信完成条件
```

核心体验判断标准：

```text
用户不需要阅读后端日志，也能判断"这个项目是否可信地推进到了当前状态，
以及下一步为什么需要我操作"。
```

> 关联决策：D-066（No Evidence/No Trace, No Trusted Result）

---

## 2. 四对象边界

```text
Evidence — 用于支撑 claim/验收/验证结论的证据（证明结果）
Trace   — 用于记录过程、来源、状态变化、模型调用、资源调用和执行链路（解释过程）
Audit   — 用于记录 Gate 决策、高风险动作、Policy 冲突和授权相关审计（记录审计）
Gate    — 用于阻塞流程并等待用户、Agent 或系统按规则做出决策（承载决策）
```

边界规则：

```text
1. Evidence 证明结果
2. Trace 解释过程
3. Audit 记录审计
4. Gate 承载决策
5. Trace 不等于 Evidence
6. Audit 不等于 Evidence
7. Audit 不等于用户授权本身
8. Gate 决策不自动等于 Evidence validated（D-066）
```

> 关联决策：D-023（Gate）、D-066（Evidence/Trace/Audit）

---

## 3. 右侧检视总体布局

Evidence/Trace/Audit/Gate 统一收敛到 Workspace 右侧检视（与 `03-ProjectWorkspace布局规范.md` §18 一致）。

检视分区（6 类）：

```text
Gate      — 当前 Gate 详情 + 历史 Gate 列表（主展位在中央横幅）
Evidence  — 证据链与证据缺口
Trace     — 运行过程记录
Audit     — 审计记录
Context   — Agent 上下文引用
Resource  — 资源调用状态
Model     — 模型调用状态
关联对象（Artifact/Claim）
```

优先级：

```text
1. blocking Evidence Gap（D-066）
2. Policy blocked（D-030）
3. Active Gate 详情（D-023，主展位在中央横幅）
4. L5 高风险动作（D-034）
5. Audit missing
6. Trace missing
7. Evidence insufficient（D-066）
8. 普通 Context/Resource/Model 摘要
```

展示规则：

```text
1. Gate 主展位为 Workspace 中央横幅（D-023），右侧检视为详情+历史
2. blocking Evidence Gap 必须可见（D-066）
3. 右侧检视可折叠为 36px 竖条（显示"证据·过程·审计"），折叠不得隐藏 Active Gate 指示
4. 检视内容只展示摘要和引用，详情按权限查询
5. 敏感内容必须脱敏
```

> 关联决策：D-023, D-030, D-034, D-066

---

## 4. Gate 交互总览

Gate 是阻塞流程的决策对象（D-023），采用双层展示结构。

### 4.1 中央横幅（主展位）

Active Gate 在 Workspace 中央编辑区顶部常驻黄色/橙色横幅（位于 Tab bar 上方），用户决策前不可关闭或折叠。

```text
横幅内容：Gate 标题 + 摘要 + 风险级别（L0-L5）+ 操作按钮（批准/拒绝/要求补充/返回修改）
Policy blocked 时横幅为红色阻断态，不显示"批准"按钮（D-030）
L5 高风险动作时明确提示"需用户 Gate 确认"（D-034）
用户决策后横幅消失，状态从后端刷新
```

见 `03-ProjectWorkspace布局规范.md` §8 详细规范。

### 4.2 右侧 Gate 检视（详情 + 历史）

Gate 卡片建议字段（17 个）：

```text
gate_id / gate_type / gate_status
关联 project_id / run_id / stage / node_id
触发原因
risk_level（L0-L5）
policy_check_ref
artifact_refs / evidence_refs / evidence_gap_refs
options / recommended_option
trace_ref / audit_ref
next_actions
```

交互规则：

```text
1. Gate 主展位为中央横幅，不得只显示在右侧检视或 toast 中（D-023）
2. Gate 未决策时不得继续阻塞动作
3. Gate 决策必须进入 Audit
4. P 阶段晋级 Gate 必须用户授权（D-023）
5. L5 高风险动作必须用户 Gate（D-034）
6. Policy 禁止项不得显示 approve（D-030）
7. Gate 详情必须能看到关联 Evidence/Trace/Audit
8. 右侧 Gate 检视展示完整字段 + 历史 Gate 列表
```

> 关联决策：D-023, D-030, D-034

---

## 5. Gate 状态展示

Gate 状态（10 种）：

```text
created
waiting_decision     ← 阻塞态视觉
under_review
approved             ← 必须显示决策主体和 audit_ref
rejected             ← 必须显示原因
needs_more_info      ← 必须显示需要补充的信息
expired              ← 不得显示为自动批准
canceled
resolved             ← 必须显示 Trace/Audit 引用
failed               ← 必须显示 error_ref
```

---

## 6. Gate 决策动作

Gate 决策动作（9 种）：

```text
批准 / 拒绝 / 要求补充信息 / 要求返工 / 取消
查看关联证据 / 查看关联 Trace / 查看 Audit / 查看 Policy 阻断原因
```

交互规则：

```text
1. 批准动作必须展示风险提示
2. 拒绝动作必须填写或选择原因
3. 要求补充信息必须产生 next_actions
4. 返工必须关联返工目标或计划
5. Policy blocked 不得出现批准按钮（D-030）
6. L5 Gate 必须明确"需要用户确认"（D-034）
7. 决策提交后必须刷新 Gate/Run/Stage/Audit
```

> 关联决策：D-030, D-034

---

## 7. 阶段晋级 Gate

阶段晋级 Gate 是 P0-P6 的硬约束（D-023）。

前端应展示：

```text
当前阶段 / 建议下一阶段
完成条件
Artifact / Evidence / Evidence Gap
风险说明 / 决策选项 / Audit 引用
```

规则：

```text
1. 每个启用的 P 阶段进入下一阶段前都必须用户授权（D-023）
2. Stage Plan/Task Plan 审核不等于阶段晋级授权
3. Evidence 不足不得默认晋级（D-066）
4. 被裁剪阶段不得通过 Gate 显示为 completed（D-009）
5. 用户批准后仍需以后端状态刷新为准
```

> 关联决策：D-009, D-023, D-066

---

## 8. 高风险动作 Gate

高风险动作 Gate 用于写盘、shell、外部系统写操作等场景（D-034）。

前端应展示：

```text
动作摘要 / risk_level（L0-L5）
影响范围 / write_scope / resource_ref
command_summary（脱敏）/ policy_check_ref
Trace 引用 / Audit 引用 / 决策选项
```

规则：

```text
1. L5 高风险动作暂定全部强制用户 Gate（D-034）
2. 命令和 Tool/MCP 操作按风险级别展示
3. command_summary 必须脱敏
4. 外部系统写操作必须突出显示
5. 用户未授权不得继续
6. 高风险动作必须写 Audit
```

> 关联决策：D-034

---

## 9. Policy 阻断交互

Policy 是硬约束（D-030）。

Policy 阻断卡片：

```text
policy_check_id / policy_refs / violation_refs
blocked_action / blocking_reason
risk_level / trace_ref / audit_ref / next_actions
```

规则：

```text
1. Policy blocked 不得显示为普通 validation_error（D-030）
2. Policy blocked 不得提供"批准继续"
3. Security/Authorization Agent 不能批准 Policy 禁止的动作（D-031）
4. 前端不得建议绕过 Policy
5. 可提供"查看原因""调整计划""返回修改"等安全动作
```

> 关联决策：D-030, D-031

---

## 10. Evidence 总览交互

Evidence 检视用于展示证据链和证据缺口（D-066）。

Evidence 卡片：

```text
evidence_id / evidence_type
evidence_status / validation_status
claim_refs / 关联 stage / 来源 Artifact
限制说明 / evidence_gap_refs
trace_ref / audit_ref
```

展示规则：

```text
1. Evidence 必须关联 claim 或验证目标
2. Evidence candidate 必须标记为候选
3. validation_status 必须可见
4. Evidence 不足必须显示缺口（D-066）
5. P5 验证必须突出证据槽位（D-066）
6. Evidence 缺失不得显示 completed
```

> 关联决策：D-066

---

## 11. Evidence Gap 交互

Evidence Gap 是证据不足的显式对象（D-066）。

Evidence Gap 卡片：

```text
evidence_gap_id / 关联 stage / 关联 claim
缺口描述 / required_evidence_type
blocking / severity / status
resolution_plan_ref / trace_ref / next_actions
```

交互规则：

```text
1. blocking=true 必须高亮
2. Evidence Gap 不得折叠到普通日志中
3. Evidence Gap 不得被 dismiss 后视为解决
4. 关闭缺口必须有补充证据、接受风险或明确裁决
5. 接受风险必须写 Audit
6. P5 Evidence Gap 不得弱化为普通 warning（D-066）
```

> 关联决策：D-066

---

## 12. Evidence 验证状态

验证状态（6 种）：

```text
not_validated
validation_pending        — 不得显示为完成
validation_passed         — 才可作为可信证据的一部分
validation_failed         — 必须显示原因
validation_blocked        — 必须显示阻塞对象
validation_not_applicable — 必须显示适用性说明
```

规则：前端不得自行修改 validation_status（D-053）。

> 关联决策：D-053, D-066

---

## 13. Trace 总览交互

Trace 用于解释过程。Trace 卡片：

```text
trace_id / trace_type
关联 project_id/run_id/stage/node_id
来源模块 / 摘要
关联 Artifact/Evidence/Gate
created_at / 详情入口
```

规则：

```text
1. Trace 不等于 Evidence
2. Trace 缺失必须可见
3. 模型调用 Trace 不得展示密钥
4. 资源调用 Trace 必须展示资源来源和风险摘要
5. 执行 Trace 必须可定位到 Run/Node
6. Trace 详情按权限展示
```

---

## 14. Audit 总览交互

Audit 用于审计关键决策和高风险动作。Audit 卡片：

```text
audit_id / audit_type
关联 gate_id / 关联 risk_action_id
risk_level / 决策摘要 / actor_ref
accepted_risks / created_at / 详情入口
```

规则：

```text
1. Audit 不等于用户授权本身
2. Gate 决策必须写 Audit（D-023）
3. 高风险动作必须写 Audit（D-034）
4. Policy 冲突应进入 Audit 或安全审计策略
5. Audit 缺失必须可见
6. Audit 详情按权限展示并脱敏
```

> 关联决策：D-023, D-034

---

## 15. 时间线交互

联合时间线事件（11 种）：

```text
gate_created → gate_decided
evidence_submitted → evidence_validated
evidence_gap_created → evidence_gap_resolved
trace_written / audit_written
policy_blocked
run_interrupted / run_resumed
```

规则：

```text
1. 时间线不是唯一状态源
2. 时间线用于理解先后关系
3. 时间线事件点击后查询对象详情
4. 缺失事件不得由前端补造
5. mock 时间线必须显式标记（D-049）
```

> 关联决策：D-049

---

## 16. Artifact 与 Evidence 联动

联动展示：

```text
Artifact 是否为 Evidence 候选
Evidence 来源 Artifact
Artifact 关联 Trace
Evidence 关联验证状态
Evidence Gap 关联 Artifact 缺失或不足
```

规则：

```text
1. Artifact 不自动成为 Evidence
2. Artifact 被用于 Evidence 时必须有明确关系
3. Evidence validated 必须来自后端验证（D-066）
4. Artifact rejected 不得作为有效 Evidence
5. Artifact promoted_to_document 不等于 Evidence validated
```

> 关联决策：D-066

---

## 17. 阶段页联动

阶段页与 Evidence/Trace/Audit/Gate 强联动：

```text
当前阶段 Gate / 阶段完成条件
Artifact / Evidence / Evidence Gap
Trace 摘要 / Audit 摘要 / 下一步动作
```

规则：

```text
1. P 阶段 completed 必须由后端状态给出
2. Evidence 不足不得显示 completed（D-066）
3. Stage Gate 未决策不得晋级（D-023）
4. Audit 缺失时不得静默通过高风险决策
5. Trace 缺失时应提示可信链路不足
```

> 关联决策：D-023, D-066

---

## 18. Workspace 联动

Workspace 右侧检视应根据中央 Tab 和文件面板选择联动：

```text
1. 选中文件时显示相关 Artifact/Trace
2. 选中 Artifact 时显示 Evidence 候选和 Trace
3. 选中 Evidence 时显示 claim 和验证状态
4. 选中 Gate 时显示关联证据和审计
5. 选中执行输出时显示 Execution Trace
6. 联动不得改变业务状态
```

---

## 19. 交互错误与阻塞展示

常见错误/阻塞（14 种）：

```text
gate_required → 打开 Gate 检视
gate_not_resolved → 不允许继续执行
gate_rejected
policy_blocked → 不提示反复重试
evidence_missing → 打开 Evidence Gap
evidence_insufficient
evidence_validation_failed
trace_missing → 显示可信链路不足
audit_missing → 显示审计缺失
checkpoint_missing
permission_denied
redaction_required → 不展示原始敏感内容
state_mismatch
internal_error
```

---

## 20. 用户决策确认体验

用户做出 Gate 决策前，前端应明确展示：

```text
决策对象 / 影响范围 / 风险级别
关联 Evidence / Evidence Gap / Trace
将写入 Audit
可选动作 / 不可逆或高风险提示
```

规则：

```text
1. 高风险批准必须明确二次确认或强提示
2. 拒绝必须记录原因
3. 要求补充信息必须形成 next_actions
4. 接受 Evidence 缺口风险必须写 Audit（D-066）
5. 决策提交后需刷新后端状态
6. 前端不得本地伪造决策成功
```

> 关联决策：D-066

---

## 21. 与 SSE / 事件联动

建议订阅事件（17 种）：

```text
gate_created / gate_waiting_decision / gate_decided / gate_approved / gate_rejected
policy_blocked
evidence_submitted / evidence_validated / evidence_rejected
evidence_gap_created / evidence_gap_resolved
trace_written / audit_written / gate_audit_written
run_interrupted / run_resumed
resync_required
```

规则：

```text
1. 事件不是唯一状态源
2. 事件只作为刷新提示
3. 断线后必须从后端状态恢复
4. resync_required 必须重新查询 Gate/Evidence/Trace/Audit
5. 事件 payload 不得包含密钥
6. mock SSE 必须显式标记（D-049）
```

> 关联决策：D-049

---

## 22. 状态恢复体验

恢复场景：刷新页面 / 重新进入 Workspace / SSE 断线重连 / Gate 决策后刷新 / 后台任务恢复 / Trace-Audit 写入延迟。

恢复规则：

```text
1. 必须重新查询 Gate/Evidence/Trace/Audit 对象
2. 不得从前端缓存推断 Gate resolved（D-053）
3. 不得从事件缓存推断 Evidence validated（D-053）
4. Audit 写入延迟时必须显示 pending/missing/retry 状态
5. resync_required 必须触发全量刷新
6. 恢复失败必须显示 error_ref
```

> 关联决策：D-053

---

## 23. 安全与脱敏

可展示：credential_status/credential_ref/redacted/trace_ref/audit_ref/policy_check_ref/resource_ref/command_summary/source_type/source_display_name。

不得展示：Key/Token/Secret/Password/.env 明文/未脱敏连接串/可还原密钥片段/包含凭据的 Git URL/含敏感内容的终端输出/未脱敏模型输入输出/未脱敏 Trace-Audit 详情。

---

## 24. Mock 与体验壳规则

R3 可先建设交互体验壳，但必须显式标记 mock（D-049）。

允许：静态 Gate 示例/Evidence 列表/Evidence Gap/Trace 时间线/Audit 卡片/Policy blocked 示例/决策流程演示。

禁止：mock Gate 显示为真实授权/mock Evidence 显示为 validated/mock Trace 显示为真实运行记录/mock Audit 显示为真实审计/mock Policy blocked 显示为真实阻断/mock SSE 显示为真实事件/mock completed 显示为真实完成。

> 关联决策：D-049

---

## 25. R2 / R3 / R4 / R9-R12 校准项

以下校准项供后续 R 阶段执行时对照使用。

### 25.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录
2. Evidence/Trace/Audit/Gate 边界是否清晰
3. No Evidence/No Trace 规则是否足够显式（D-066）
4. Gate/Authorization API 与 AET API 引用是否一致
5. mock 与脱敏规则是否足够明确
6. 是否仍无 Mission 产品层（D-070）
7. 是否重复上级事实，需要改为引用（D-068）
```

### 25.2 R3 前端主干校准

```text
1. 右侧检视是否可展示 Gate/Evidence/Trace/Audit
2. Gate 卡片是否可用
3. Evidence Gap 是否可见
4. Trace/Audit 时间线是否可展示
5. mock 是否显式标记
6. 脱敏提示是否可见
```

### 25.3 R4 API 联调校准

```text
1. AET API 是否接入
2. Gate/Authorization API 是否接入
3. Policy Check API 是否接入
4. SSE/Event 是否接入
5. 错误响应是否能正确映射 UI
6. 前端是否停止用 mock 判断 resolved/validated/completed
```

### 25.4 R9-R12 主链路校准

```text
R9： P0-P1 接入与建档证据候选、Trace、Gate 是否可展示
R10：P2-P3 评估、规划证据、风险 Gate、Trace 是否可展示
R11：P4 执行、高风险动作 Gate、Tool/MCP Trace/Audit 是否可展示
R12：P5-P6 验证 Evidence、Evidence Gap、交付 Audit 是否可展示
```

---

## 26. Evidence / Trace / Audit / Gate 红线

```text
1.  不得把 Gate 显示为普通 toast 后继续执行（D-023）
2.  不得隐藏 Active Gate（D-023）
3.  不得隐藏 Evidence 缺口（D-066）
4.  不得隐藏 Trace/Audit 缺失
5.  不得把 Audit 显示为用户授权本身
6.  不得把 Trace 显示为 Evidence
7.  不得把 Artifact 显示为 Evidence validated（D-066）
8.  不得把 Evidence candidate 显示为 Evidence validated（D-066）
9.  不得把模型输出或资源输出显示为 Evidence validated（D-066）
10. 不得把 Policy 禁止项显示为可批准（D-030）
11. 不得用前端本地状态判断 Gate resolved、Evidence validated 或 Stage completed（D-053）
12. 不得在 Evidence 不足或 Trace 缺失时显示可信完成（D-066）
13. 不得展示 Key/Token/Secret/Password（AGENTS.md §12）
14. 不得将 mock Gate/mock Evidence/mock Trace/mock Audit 显示为真实能力（D-049）
15. 不得引入 Mission 产品层（D-070）
16. 不得使用旧 Phase/旧 F0-F6/旧 S0-S7 作为当前主流程
17. 不得在长期前端路由、组件、状态字段中固化 V26.1（D-002）
18. 不得把 R1 正式候选契约伪装为实现契约（D-016）
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 27. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1.  明确交互目标（§1）
2.  明确 Evidence/Trace/Audit/Gate 四对象边界（§2）
3.  明确右侧检视 4 区+8 级优先级（§3）
4.  明确 Gate 交互 17 字段+10 状态+9 决策动作（§4-§6）
5.  明确阶段晋级 Gate/高风险动作 Gate/Policy 阻断三种场景（§7-§9）
6.  明确 Evidence 总览+Evidence Gap 卡片+6 验证状态（§10-§12）
7.  明确 Trace/Audit 总览交互（§13-§14）
8.  明确联合时间线 11 事件（§15）
9.  明确 Artifact-Evidence/阶段页/Workspace 联动（§16-§18）
10. 明确 14 种错误与阻塞展示（§19）
11. 明确用户决策确认体验（§20）
12. 明确 SSE 17 事件联动（§21）
13. 明确状态恢复 6 规则（§22）
14. 明确安全与脱敏（§23）
15. 明确 Mock 与体验壳规则（§24）
16. 明确 4 阶段校准项（§25）
17. 明确 ETA-Gate 红线 18 条（§26）
18. 未新增产品决策
19. 未引入 Mission 产品层
20. 未固定最终实现
```
