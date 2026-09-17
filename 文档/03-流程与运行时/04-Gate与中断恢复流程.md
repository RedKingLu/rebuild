# 04-Gate与中断恢复流程

> 文档路径：`文档/03-流程与运行时/04-Gate与中断恢复流程.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/04-Gate与中断恢复流程.md`（v0.1，~936 行；去重 ~55%，主要移除字段/状态枚举 + 第 6 次执行模式复述 + Artifact/Evidence 规则复述）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`03-流程与运行时/` 专题的第 4 份子文档。定义 Gate 的操作流程——触发条件、生命周期（7 阶段逐步规范）、决策选项、interrupt 规范、resume 预检查清单（16 项）、典型流程（5 场景）、状态恢复冲突处理。Gate 类型和状态字段的权威源见 `02-架构设计/02-LangGraph主编排架构.md` §10 和 `02-架构设计/03-Project-Run-TaskGraph状态架构.md`。
> 上级依据：`文档/03-流程与运行时/00-流程与运行时总览.md`、`文档/03-流程与运行时/01-P0-P6阶段契约.md`、`文档/02-架构设计/02-LangGraph主编排架构.md`。
> 重要边界：本文是 Gate 的**操作流程规范**（触发→创建→中断→决策→Audit→预检查→恢复），不重复架构层的 Gate 字段定义、不重复执行模式审核规则、不固化 API schema。
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

```text
权威源引用（本文只引用，不重新定义）：
- Gate 类型（7 种）+ interrupt/resume 机制 → 02-架构设计/02-LangGraph主编排架构.md §10
- Gate 状态字段定义                        → 02-架构设计/03-Project-Run-TaskGraph状态架构.md
- Manual/Plan/Auto 执行模式                → 02-架构设计/02-LangGraph主编排架构.md §11
- Policy/Hook/Authorization 三层安全        → 07-安全与权限/
- Artifact/Evidence/Trace/Audit 定义        → 02-架构设计/03-Project-Run-TaskGraph状态架构.md §12-§14
```

本文必须遵守：

```text
1. P 阶段晋级 Gate 必须用户授权——无论 Manual/Plan/Auto；
2. L5 高风险动作暂定全部强制用户 Gate；
3. Policy 优先级高于 Agent 判断——Policy 禁止项不得被任何 Agent 批准；
4. Gate 决策必须进入 Audit（D-066）；
5. Gate 后 resume 必须校验上下文/Policy/checkpoint/Workspace/Artifact/Evidence/Trace/Audit；
6. LangGraph 是主编排底座——Gate/interrupt/resume 落在 LangGraph 编排体系中（D-037）。
```

本文不得：

```text
1. 用前端按钮状态替代 Gate 决策；
2. 用模型判断替代用户授权；
3. 用 Acceptance 替代用户 Gate；
4. 用 Auto Mode 绕过 Gate；
5. 在 Gate 未关闭时执行阻塞动作；
6. Gate 后不校验上下文直接 resume；
7. 将 Gate 记录只保存在前端或临时内存。
```

> 本节"必须遵守/本文不得"中的通用红线（P 阶段晋级用户 Gate D-023、L5 强制 Gate D-034、Policy 优先 D-031、Gate 决策须进 Audit D-066、LangGraph 主编排 D-037 等）完整总表见 AGENTS §18 + 01-决策记录；本文仅就地保留与 Gate 操作流程职责相关的子集。

---

## 1. Gate 定位

Gate 是流程中的**正式授权、确认和中断点**。它回答 7 个问题：

```text
1. 当前流程为什么必须暂停；
2. 需要谁做出决策；
3. 决策选项是什么；
4. 风险级别是什么；
5. 用户批准/拒绝/修改后流程如何继续；
6. 决策如何进入 Audit；
7. resume 时如何保证状态一致。
```

Gate 不是：普通 UI 弹窗、前端本地状态、模型建议、Agent 自评、Acceptance 结论、Policy 豁免机制。

---

## 2. Gate 类型

架构层定义 7 种基础类型（见 `02-架构设计/02-LangGraph主编排架构.md` §10）。本文在流程层补充 5 种细化类型：

| 类型 | 触发场景 | 来源 |
|---|---|---|
| `stage_gate` | P 阶段晋级 | 架构层 |
| `plan_gate` | Stage Plan / Task Plan 审核 | 架构层 |
| `action_gate` | 高风险动作执行前 | 架构层 |
| `policy_gate` | Policy 冲突 | 架构层 |
| `verification_gate` | P5 验证结果确认 | 架构层 |
| `delivery_gate` | P6 交付确认 | 架构层 |
| `scope_gate` | 范围变更 | 架构层 |
| `task_gate` | 单任务确认（Plan Delta 触发） | **流程层补充** |
| `resource_gate` | 外部资源调用确认 | **流程层补充** |
| `secret_gate` | 密钥或敏感配置操作 | **流程层补充** |
| `recovery_gate` | 状态恢复冲突 | **流程层补充** |
| `mode_gate` | 执行模式切换（降低用户参与度时） | **流程层补充** |

> R2/R4 需结合实现校准最终类型集。阶段晋级必须使用 `stage_gate`；高风险动作必须使用 `action_gate`。

---

## 3. Gate 触发条件

### 3.1 必须触发（16 项）

```text
1. 每个启用 P 阶段进入下一阶段；
2. L5 高风险动作；
3. L4 外部系统写操作；
4. 远程资源写操作；
5. 删除或不可逆操作；
6. 密钥相关不确定项；
7. Policy 冲突；
8. 范围变化（超出已批准 scope）；
9. 权限边界不清；
10. 低置信度或结论冲突（多源判断不一致）；
11. Evidence 不足但用户请求继续；
12. Artifact 缺失但用户请求继续；
13. Trace/Audit 断链；
14. 状态恢复冲突；
15. 降低用户参与度的执行模式切换（Manual→Plan, Plan→Auto）；
16. 用户明确要求暂停或确认。
```

### 3.2 可触发（7 项）

```text
1. 中风险动作需要额外确认；
2. Task Plan Batch 范围较大（跨多个子系统）；
3. 并行任务合并出现冲突（conflict_review）；
4. P3 方案存在多个选项需用户选择；
5. P5 验证结果存在差异但可接受（需用户确认接受差异）；
6. P6 交付存在遗留风险；
7. Agent/Skill/Tool/MCP 调用风险不明。
```

---

## 4. Gate 决策选项

```text
approve                — 批准继续
reject                 — 拒绝继续（需说明后续流转：返工/跳过/取消）
modify_and_approve     — 修改后批准（必须记录修改内容和影响，生成 Plan Delta）
request_rework         — 要求返工（附返工目标和回退阶段建议）
request_more_evidence  — 要求补充 Evidence
request_more_info      — 要求补充信息
skip_with_risk         — 带风险跳过（必须显式登记风险，不得伪装 completed）
pause                  — 暂停（保留状态，等待后续条件）
cancel                 — 取消（记录取消原因）
change_scope           — 调整范围
change_mode            — 调整执行模式
```

选项规则：

```text
1. 高风险动作不得默认选 approve——推荐选项不得是 approve；
2. Evidence 不足时必须提供 request_more_evidence 选项；
3. 范围不清时必须提供 request_more_info 或 change_scope；
4. 拒绝后必须有明确流转路径——不得留空；
5. skip_with_risk 必须登记风险——不得伪装 completed；
6. modify_and_approve 必须记录修改内容和影响——产生 Plan Delta 或等价记录。
```

---

## 5. Gate 生命周期

```
trigger_detected → gate_created → run_interrupted
  → user_reviewing → decision_recorded → audit_written
  → resume_precheck → resume_or_route → gate_resolved
```

### 5.1 trigger_detected（触发识别）

必须记录：触发来源、原因、风险级别（L0-L5）、关联 Policy、关联 Run/Stage/Node、被阻塞的动作。

### 5.2 gate_created（Gate 创建）

必须记录：gate_id、Gate 类型、决策选项（≥2 个）、推荐选项（非 approve，如适用）、上下文引用（触发时的 checkpoint_ref、Trace 引用）。

### 5.3 run_interrupted（Run 中断）

必须确保：

```text
1. LangGraph interrupt 已生效（或等价状态已记录）；
2. run_status → waiting_gate；
3. active_gate_ref 已设置；
4. Execution Session 按风险处理：L4-L5 → terminated；L2-L3 → paused；L0-L1 → 可继续但不得执行阻塞动作；
5. 前端展示 Gate（类型/原因/风险/选项/推荐）；
6. 阻塞的高风险动作确认未继续执行。
```

### 5.4 decision_recorded（决策记录）

必须包含：决策人、决策时间、决策选项、决策理由、修改内容（如 modify_and_approve）、风险接受说明（如 skip_with_risk）、后续流转目标。

### 5.5 audit_written（Audit 写入）

Gate 决策必须写入 Audit。至少记录：

```text
gate_id / gate_type / risk_level / decision / decision_by
/ decision_reason / related_trace_refs / created_at
```

### 5.6 resume_precheck（恢复预检查）

详见 §8（16 项检查清单）。

### 5.7 resume_or_route（恢复或路由）

根据决策进入：原节点继续 / 替代节点 / 返工路径 / 失败路径 / 跳过路径 / 暂停状态 / 取消状态 / 新 Gate。

---

## 6. interrupt 规范

### 6.1 触发来源

```text
LangGraph 节点 / TaskGraph 边策略 / NodeLoop 异常升级
/ Policy-Hook / Auto Review Agent / Acceptance Agent
/ Execution Session / 用户主动暂停 / 状态恢复冲突
```

### 6.2 interrupt 后系统状态

| 组件 | 状态 |
|---|---|
| Run | `waiting_gate` 或 `paused` |
| Stage | `waiting_gate` 或 `paused` |
| TaskGraph | `waiting_gate` 或 `paused` |
| NodeLoop | `waiting_gate` |
| Execution Session | L4-L5 → `terminated`；L2-L3 → `paused`；L0-L1 → 可保持但受限 |
| Frontend | 显示 Gate（类型/原因/风险/选项） |
| Event Log | 记录 `interrupt` 事件 |

### 6.3 interrupt 禁止事项

```text
1. 不得只在前端显示弹窗而不更新 Run 状态；
2. 不得在 interrupt 后继续执行阻塞动作；
3. 不得丢失 checkpoint；
4. 不得省略 Trace；
5. 不得省略 Audit（高风险触发时）；
6. 不得泄露密钥。
```

---

## 7. resume 规范

### 7.1 resume 前置条件

```text
1. Gate 已有有效决策（approved/rejected/modified 等终态）；
2. Audit 已写入；
3. checkpoint 可读取；
4. Run 未取消；
5. Policy 仍允许继续；
6. 上下文未过期或已重新校准。
```

### 7.2 resume 禁止事项

```text
1. Gate 未决策不得 resume；
2. Audit 未写入不得 resume 高风险动作；
3. Policy 禁止不得 resume；
4. checkpoint 缺失不得盲目 resume；
5. Artifact/Evidence 引用失效不得继续；
6. 上下文过期不得继续——必须先 recalibrate；
7. Trace/Audit 断链不得自动推进——必须重建链或用户确认。
```

---

## 8. resume 预检查清单（16 项）

resume 前必须逐项检查：

```text
□  1. Gate 状态是否为有效终态（approved/rejected/modified）;
□  2. Gate 决策 Audit 是否已写入;
□  3. checkpoint_ref 是否存在且可读取;
□  4. Run 状态是否允许恢复（非 canceled/blocked）;
□  5. Stage 状态是否允许恢复;
□  6. TaskGraph/Node 状态是否仍匹配（未被其他路径修改）;
□  7. Stage Plan/Task Plan/TaskGraph version 是否仍为决策时的版本;
□  8. Workspace 是否存在且可访问;
□  9. Artifact 引用是否可达（文件未删除/未移动）;
□ 10. Evidence 引用是否可达;
□ 11. Policy 是否仍满足（决策后可能有新 Policy 生效）;
□ 12. 权限边界是否仍满足;
□ 13. Environment Profile 是否仍适用（未过期/未变更）;
□ 14. Execution Session 是否需要重建（如被 terminated）;
□ 15. Trace/Audit 是否连续（无断链）;
□ 16. 是否出现新风险或新 Gate（resume 前的新触发条件）.
```

预检查失败时：

```text
1. 不得继续执行；
2. 标记 recovery_conflict 或 consistency_conflict；
3. 记录 Trace/Audit；
4. 创建 recovery_gate 通知用户；
5. 不得自行选择"看起来合理"的状态继续。
```

---

## 9. 状态恢复冲突处理

### 9.1 常见冲突场景

| 场景 | 表现 | 处理 |
|---|---|---|
| Gate 孤儿 | Run `waiting_gate` 但 Gate 记录不存在 | 创建 recovery_gate，用户决策 |
| Audit 缺失 | Gate `approved` 但 Audit 未写入 | 阻止 resume，补写 Audit 后继续 |
| 静默执行 | Gate `open` 但 TaskGraph 已继续执行 | 标记 consistency_conflict，回滚或用户确认 |
| 版本漂移 | checkpoint 指向旧 plan version | 校验 Plan Delta，确认是否需重新计划 |
| 前端后端不一致 | 前端显示 Gate 已通过但后端未记录 | 以后端为准，补偿前端状态 |
| Evidence 不足放行 | Evidence insufficient 但 Gate approved | 标记高风险，记录接受理由 |

### 9.2 冲突处理规则

```text
1. 停止自动推进——任何冲突触发立即暂停；
2. 标记 recovery_conflict 或 consistency_conflict；
3. 记录 Trace/Audit——冲突本身就是高风险事件；
4. 创建 recovery_gate——让用户知晓并决策；
5. 不得自行选择"看起来合理"的状态继续执行；
6. 必要时回滚到最后一个一致 checkpoint。
```

---

## 10. 典型 Gate 流程

### 10.1 阶段晋级 Gate（stage_gate）

```text
1. 当前阶段完成条件检查（对照 01-P0-P6阶段契约.md）；
2. Artifact/Evidence/Trace/Audit 完整性检查；
3. 创建 stage_gate（含阶段摘要/产物清单/风险提示）；
4. LangGraph interrupt → Run waiting_gate；
5. 用户确认：进入下一阶段 / 返工 / 补充材料 / 暂停 / 取消；
6. Audit 写入；
7. resume 预检查 → resume 到下一阶段或指定路径。
```

### 10.2 高风险动作 Gate（action_gate）

```text
1. Hook/Policy 识别高风险动作（L4-L5）；
2. 创建 action_gate（含动作描述/风险/影响范围/替代方案）；
3. Execution Session 暂停或终止（按风险级别）；
4. 用户查看并决策：批准/拒绝/修改后批准；
5. Audit 写入；
6. resume 或进入替代路径。
```

### 10.3 Policy 冲突 Gate（policy_gate）

```text
1. Policy 检测冲突——若明确禁止则直接停止（不创建 Gate，直接 blocked）；
2. 若需用户决策（Policy 条件模糊/多 Policy 冲突），创建 policy_gate；
3. 用户查看冲突原因和 Policy 条款；
4. 用户决策：调整计划/豁免（需记录理由）/取消；
5. Audit 写入（Policy 冲突决策必须可审计）。
```

### 10.4 验证结果 Gate（verification_gate）

```text
1. P5 验证结果生成（对照 01-P0-P6阶段契约.md §7）；
2. Evidence 检查——构建/运行/测试/回归/关键行为 5 维度；
3. 差异和风险显式登记；
4. 创建 verification_gate；
5. 用户决策：接受验证结果 / 要求返工 / 补充 Evidence / 接受差异；
6. Audit 写入；
7. resume 到 P6 或返回 P4/P5。
```

### 10.5 交付 Gate（delivery_gate）

```text
1. P6 交付包生成（对照 01-P0-P6阶段契约.md §8）；
2. Artifact/Evidence/Trace/Audit 索引完整性检查；
3. 遗留风险逐项确认；
4. 创建 delivery_gate；
5. 用户确认：接受交付 / 要求补充 / 拒绝 / 冻结；
6. Audit 写入（最终 Gate 决策）；
7. 标记交付完成或进入返工。
```

---

## 11. Gate 与前端/API

### 11.1 前端展示要求

前端必须展示：Gate 类型、触发原因、风险级别、关联阶段/节点、请求动作、可选决策（含推荐选项）、Artifact/Evidence 摘要、决策后状态预览。

### 11.2 前端禁止事项

```text
1. 不得只在前端保存 Gate 决策——后端是唯一事实源；
2. 不得伪造 Gate 已批准；
3. 不得隐藏 waiting_gate 状态；
4. 不得将 mock Gate 展示为真实能力；
5. 不得在前端泄露密钥；
6. 不得用前端状态替代 Run State。
```

### 11.3 API 能力建议

R1 建议以下 Gate API 能力（R4 实现）：

```text
创建 Gate / 查询 Gate / 提交 Gate 决策 / 查询 active Gate
/ resume Run / 取消-暂停 Run / 获取 Gate Audit / 订阅 Gate SSE-event log
```

---

## 12. R2/R4/R9-R12 校准项

### R2 文档校准

```text
1. Gate 类型（7+5）是否需增减——与架构层和实际需求对齐；
2. resume 预检查 16 项是否完整——是否有遗漏的检查维度；
3. 冲突场景 6 种是否覆盖所有已知恢复问题；
4. Policy/Hook/Authorization 边界是否与安全文档一致。
```

### R4 工程骨架校准

```text
1. Gate 数据模型（gate_id/状态/决策字段）；
2. LangGraph interrupt/resume 实现；
3. checkpoint 存储与恢复；
4. Gate API + SSE/event log；
5. Audit Writer 集成；
6. Policy/Hook 在 interrupt 触发点的接入。
```

### R9-R12 主链路校准

```text
R9  (P0-P1)：阶段晋级 Gate 最小实现；
R10 (P2-P3)：计划 Gate + 方案 Gate；
R11 (P4)   ：高风险动作 Gate + NodeLoop 内 Gate；
R12 (P5-P6)：验证 Gate + 交付 Gate + 完整 Audit 链。
```

---

## 13. 规范红线

```text
1. 不得无 Gate 阶段晋级；
2. 不得无 Gate 执行 L5 高风险动作；
3. 不得用前端状态替代 Gate 决策；
4. 不得用模型判断替代用户授权；
5. 不得用 Acceptance 替代用户 Gate；
6. 不得用 Auto Mode 绕过 Gate；
7. 不得由 Agent 批准 Policy 禁止项；
8. 不得 Gate 决策无 Audit（D-066）；
9. 不得高风险动作无 Audit；
10. 不得 Gate 未关闭时继续执行阻塞动作；
11. 不得 checkpoint 缺失时盲目 resume；
12. 不得 Trace/Audit 断链时自动推进；
13. 不得泄露 Key/Token/Secret/Password。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md（P 阶段晋级用户 Gate D-023、L5 强制 Gate D-034、Policy 优先 D-031、Gate 决策须 Audit D-066、密钥脱敏 D-032）；术语与风险级别（L0-L5）详见 02-术语表.md。本文红线为 Gate 与中断恢复主题特有约束，整体保留。

---

## 14. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 Gate 定位——7 个问题 + 8 个"不是"；
2. 明确 Gate 类型——架构层 7 种 + 流程层 5 种补充；
3. 明确 Gate 触发条件——必须 16 项 + 可选 7 项；
4. 明确 Gate 决策选项——11 种 + 6 条选项规则；
5. 明确 Gate 生命周期——7 阶段逐步规范（每阶段有必须记录项）；
6. 明确 interrupt 规范——触发来源/系统状态变化表/禁止事项；
7. 明确 resume 规范——前置条件/禁止事项；
8. 明确 resume 预检查清单——16 项 checkbox + 失败处理；
9. 明确状态恢复冲突——6 种场景 + 6 条处理规则；
10. 明确典型 Gate 流程——5 种场景的逐步流程；
11. 明确 Gate 与前端/API 的关系——展示要求/禁止事项/API 能力建议；
12. 明确 R2/R4/R9-R12 校准项；
13. 明确规范红线（13 条）；
14. 未固化最终 API 字段或数据库 schema；
15. 执行模式/Gate 字段/Artifact 规则引用架构层——不复述。
```
