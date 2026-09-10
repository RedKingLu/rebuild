# 05-Artifact-Evidence-Trace-Audit验收规范

> 文档路径：`文档/08-测试与验收/05-Artifact-Evidence-Trace-Audit验收规范.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/草稿/05-Artifact-Evidence-Trace-Audit验收规范.md`（v0.1）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R1 文档正式化流程
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。
> 文档定位：定义 rebuild 当前版本 Artifact、Evidence、Trace、Audit 四类对象的验收规范。覆盖对象边界、状态、生命周期、验收口径、P0-P6 阶段映射、迁移正确性支撑、Gate/Policy/安全脱敏、API/SSE 建议、前端展示、缺口处理、报告要求。
>
> **本目录关联文档**：
> - `00-测试与验收总览.md` — 测试与验收体系总入口（本文是其 §7-§10 的详述）
> - `03-P0-P6验收清单.md` — P0-P6 阶段检查清单（本文 §9 是其对象维度的映射）
> - `04-构建-运行-测试-回归验证规范.md` — P5 四类验证操作规范
>
> 上级依据：`文档/00-项目治理/01-决策记录.md`、`文档/06-UX与前端/05-Evidence-Trace-Audit与Gate交互.md`、`文档/07-安全与权限/05-Audit审计规范.md`。
>
> 重要边界：本文是 AETA 验收专题规范，不替代对象数据模型、API 实现契约、审计存储、Trace 存储、Evidence 验证策略或 Artifact 管理实现。本文为 R1 建议契约，R2 需按正式文档体系校准；R4/R6/R8/R9-R12/R17 需按真实实现和联调结果校准。

---

## 0. 编写原则

本文遵守文档事实源层级（`文档地图.md` §1）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 Artifact / Evidence / Trace / Audit 验收规范，不重新定义上级事实。

本文必须遵守：

```text
1. 当前版本文档体系必须区分项目文档、交接材料、产物、证据、运行记录和参考资料（详见 02-术语表 第九部分）；
2. Artifact 不自动晋升为项目文档，必须经过 Gate 或验收流程；
3. Artifact 不自动成为 Evidence；
4. Evidence candidate 不自动成为 Evidence validated；
5. Trace 不等于 Evidence，Audit 不等于 Evidence；
6. Audit 不等于用户授权本身；
7. （P 阶段晋级 Gate、Policy 优先、Manual/Plan/Auto 边界等通用红线详见 AGENTS §18 + 01-决策记录）；
8. P5 验证必须基于可验证证据（D-066）；
9. 验证不通过或证据不足时不得标记 completed；
10. No Evidence / No Trace, No Trusted Result；
11. 高风险动作必须 Audit；L5 高风险强制用户 Gate（D-034，详见 02-术语表 第七部分）；
12. Gate 决策必须 Audit；
13. Key / Token / Secret / Password 必须脱敏（不泄露，D-032）；
14. 当前版本不设独立 Mission 产品层（不引入 Mission，D-070）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

**本文特有禁止项**（通用禁止事项详见 AGENTS.md §18）：

```text
1. 不得把 Artifact 生成写成 Evidence validated；
2. 不得把 Evidence candidate 写成 Evidence validated；
3. 不得把 Trace 存在写成结果可信；
4. 不得把 Audit 存在写成用户授权或验收通过；
5. 不得把模型输出、资源输出、命令输出直接写成 Evidence validated。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 验收目标

Artifact / Evidence / Trace / Audit 验收目标是确保 rebuild 的产物、证据、过程和审计链路清晰、可追溯、不可混淆。

验收应回答：

```text
1. 当前对象是什么身份；
2. 当前对象是否来源可追溯；
3. 当前对象是否已验证；
4. 当前对象是否能支撑阶段完成；
5. 当前对象是否与 claim / action / gate / run / stage 关联；
6. 当前对象是否存在缺口；
7. 当前对象是否脱敏；
8. 当前对象是否被前端正确展示；
9. 当前对象是否被错误用于完成判定；
10. 当前对象是否满足 P5 迁移正确性验证要求。
```

---

## 2. 四类对象边界

> 完整定义见 `02-术语表.md` 第九部分。本节仅列出验收相关边界。

### 2.1 Artifact

Artifact 是过程产物或交付候选，例如报告、计划、Patch、代码变更摘要、交付包候选等。

Artifact 可以作为 Evidence 的来源之一，但不能自动成为 Evidence。

### 2.2 Evidence

Evidence 是用于支撑 claim、验证结论、阶段完成或交付结论的证据对象。

Evidence 必须有来源、验证状态和关联目标。

### 2.3 Trace

Trace 是过程记录，用于说明动作、调用、状态变化和执行链路发生过。

Trace 不证明结果正确。

### 2.4 Audit

Audit 是审计记录，用于记录 Gate 决策、高风险动作、Policy 阻断、安全事件和风险接受。

Audit 不等于用户授权本身，也不等于 Evidence。

---

## 3. 对象身份验收

必须检查：

```text
object_id 是否存在；
object_type 是否正确；
project_id 是否明确；
run_id 是否明确；
stage 是否明确；
来源是否明确；
状态是否明确；
是否脱敏；
是否有关联对象；
是否被错误晋升或误用。
```

身份规则：

```text
1. Artifact 不得显示为 Evidence；
2. Evidence candidate 不得显示为 Evidence validated；
3. Trace 不得显示为 Evidence；
4. Audit 不得显示为 Evidence；
5. 参考资料不得显示为当前项目 Evidence；
6. 历史材料不得自动成为当前事实。
```

---

## 4. Artifact 验收规范

### 4.1 Artifact 类型

Artifact 类型建议（R1 建议，R4 校准）：

```text
stage_plan；
task_plan；
task_graph；
analysis_report；
execution_report；
patch；
code_change_summary；
verification_report；
delivery_package；
delivery_report；
rework_plan；
risk_report。
```

### 4.2 Artifact 状态

```text
draft；
candidate；
submitted；
under_review；
accepted；
rejected；
superseded；
archived；
redaction_required；
invalid。
```

### 4.3 Artifact 验收项

```text
Artifact 是否有明确类型；
Artifact 是否关联 Project / Run / Stage；
Artifact 是否有来源 Trace；
Artifact 是否有创建主体；
Artifact 是否有状态；
Artifact 是否脱敏；
Artifact 是否经过必要 Gate 或审核；
Artifact 是否被错误显示为 Evidence；
Artifact 是否进入正确材料身份区域。
```

### 4.4 Artifact 通过条件

```text
1. 类型、来源、状态、关联关系清晰；
2. 必要 Trace 存在；
3. 必要审核或 Gate 已完成；
4. 不含敏感明文；
5. 未被错误晋升为 Evidence；
6. 前端展示身份正确。
```

---

## 5. Evidence 验收规范

### 5.1 Evidence 类型

Evidence 类型建议（R1 建议，R4 校准）：

```text
source_import_evidence；
structure_evidence；
analysis_evidence；
plan_evidence；
execution_evidence；
build_evidence；
runtime_evidence；
test_evidence；
regression_evidence；
behavior_evidence；
delivery_evidence；
redaction_evidence。
```

### 5.2 Evidence 状态

```text
candidate；
submitted；
validation_pending；
validated；
validation_failed；
evidence_gap；
not_applicable；
redaction_required；
invalid；
superseded。
```

### 5.3 Evidence 验收项

```text
Evidence 是否关联 claim；
Evidence 是否关联 Project / Run / Stage；
Evidence 是否有关联 Artifact / Trace；
Evidence 来源是否明确；
Evidence 验证状态是否明确；
Evidence Gap 是否可见；
Evidence 是否脱敏；
Evidence 是否支撑对应完成条件；
Evidence 是否被模型输出或资源输出冒充。
```

### 5.4 Evidence 通过条件

```text
1. claim 关联清晰；
2. 来源清晰；
3. 验证状态为 validated 或 not_applicable 且理由充分；
4. 相关 Trace 可查询；
5. 无敏感明文；
6. Gap 已关闭或经 Gate / Audit 接受风险。
```

---

## 6. Trace 验收规范

### 6.1 Trace 类型

Trace 类型建议（R1 建议，R4 校准）：

```text
model_call_trace；
resource_call_trace；
command_trace；
file_write_trace；
patch_trace；
git_trace；
policy_check_trace；
gate_trace；
audit_trace；
verification_trace；
error_trace；
redaction_trace。
```

### 6.2 Trace 状态

```text
written；
pending；
write_failed；
missing；
redaction_required；
permission_limited；
invalid。
```

### 6.3 Trace 验收项

```text
Trace 是否关联 action；
Trace 是否关联 Project / Run / Stage；
Trace 是否记录关键状态变化；
Trace 是否引用 output_ref 而非未脱敏原文；
Trace 是否可被 Evidence / Audit 引用；
Trace 缺失是否可见；
Trace 写入失败是否可见；
Trace 是否被错误显示为 Evidence。
```

### 6.4 Trace 通过条件

```text
1. 关键动作均可追溯；
2. Trace 状态为 written；
3. Trace 不含敏感明文；
4. Trace 可查询；
5. Trace 缺失不被隐藏；
6. Trace 不被用作 Evidence validated。
```

---

## 7. Audit 验收规范

### 7.1 Audit 类型

Audit 类型建议（R1 建议，R4 校准）：

```text
gate_decision_audit；
stage_promotion_audit；
high_risk_action_audit；
policy_block_audit；
external_write_audit；
resource_call_audit；
evidence_gap_risk_acceptance_audit；
credential_status_audit；
redaction_audit；
delivery_audit。
```

### 7.2 Audit 状态

```text
pending；
written；
write_failed；
missing；
redaction_required；
permission_limited；
invalid；
superseded。
```

### 7.3 Audit 验收项

```text
Gate 决策是否写 Audit；
阶段晋级是否写 Audit；
高风险动作是否写 Audit；
L5 动作是否写 Audit（详见 02-术语表 第七部分）；
外部系统写操作是否写 Audit；
Policy blocked 高风险事件是否写 Audit；
Evidence Gap 风险接受是否写 Audit；
Audit 是否关联 Trace；
Audit 是否脱敏；
Audit 缺失是否可见。
```

### 7.4 Audit 通过条件

```text
1. 必要审计事件均存在；
2. Audit 状态为 written；
3. Audit 关联 Gate / Action / Trace；
4. Audit 不含敏感明文；
5. Audit 缺失不被隐藏；
6. Audit 不被当作 Evidence 或授权本身。
```

---

## 8. 四类对象关联验收

推荐关联链：

```text
Action / Node / Run
  -> Trace
  -> Artifact candidate
  -> Evidence candidate
  -> Evidence validation
  -> Gate decision
  -> Audit
```

说明：

```text
1. 并非所有 Artifact 都会生成 Evidence；
2. 并非所有 Trace 都会生成 Evidence；
3. 并非所有 Audit 都支撑验证；
4. P5 验证必须形成足够 Evidence（D-066）；
5. Gate 决策应有 Audit；
6. 高风险动作应有 Trace + Audit。
```

验收项：

```text
关联是否可查询；
关联方向是否正确；
跨对象状态是否一致；
缺失对象是否可见；
对象引用是否脱敏；
前端是否能展示关联链。
```

---

## 9. P0-P6 阶段验收映射

### 9.1 P0 接入

```text
Artifact：接入摘要、文件清单摘要；
Evidence：源码接入成功、Workspace 隔离；
Trace：接入动作、Git / ZIP / 本地目录处理；
Audit：高风险接入动作、阶段晋级 Gate。
```

### 9.2 P1 建档

```text
Artifact：项目基础档案、材料身份登记；
Evidence：项目结构、技术栈识别、材料来源；
Trace：文件读取、Artifact 创建；
Audit：高风险读取、阶段晋级 Gate。
```

### 9.3 P2 评估

```text
Artifact：评估报告、风险清单、改造点清单；
Evidence：评估结论来源、依赖识别、风险判断；
Trace：模型 / 资源调用、评估生成；
Audit：高风险资源调用、不确定项风险接受、阶段晋级 Gate。
```

### 9.4 P3 规划

```text
Artifact：Stage Plan、Task Plan、TaskGraph；
Evidence：计划依据、任务拆分依据、风险边界；
Trace：规划生成、TaskGraph 创建；
Audit：计划审核、风险接受、阶段晋级 Gate。
```

### 9.5 P4 执行

```text
Artifact：Patch、执行报告、变更摘要；
Evidence：执行结果 candidate、Patch 应用证据；
Trace：命令、Tool / MCP、文件写入、Git 操作；
Audit：高风险动作、外部写、阶段晋级 Gate。
```

### 9.6 P5 验证

```text
Artifact：验证报告；
Evidence：构建、运行、测试、回归、行为等价 / 差异（详见 04-构建-运行-测试-回归验证规范）；
Trace：验证过程；
Audit：Evidence Gap 风险接受、差异接受、阶段晋级 Gate。
```

### 9.7 P6 交付

```text
Artifact：交付包、交付报告；
Evidence：交付内容来源、P5 验证引用、交付完整性；
Trace：交付包生成、报告生成、导出脱敏；
Audit：交付确认、风险接受、高风险导出。
```

---

## 10. P5 迁移正确性专项验收

P5 必须检查（与 `02-迁移正确性与验证策略.md` §3 证据槽位对齐）：

```text
构建 Evidence；
运行 / 启动 Evidence；
测试 Evidence；
回归 Evidence；
关键行为 Evidence；
差异登记；
Evidence Gap；
验证 Trace；
风险接受 Audit。
```

通过条件：

```text
1. 验证证据槽位齐备；
2. Evidence validated 或 not_applicable 有充分理由；
3. Evidence Gap 已关闭或经 Gate / Audit 接受；
4. 验证 Trace 可查询；
5. 验证报告不含密钥；
6. 前端不显示"看起来完成"。
```

阻断红线：

```text
无 Evidence 显示 completed；
无 Trace 显示 trusted result；
Evidence candidate 显示 validated；
差异被隐藏；
接受风险无 Audit；
验证日志泄露密钥。
```

---

## 11. 缺口处理

缺口类型：

```text
artifact_missing；
evidence_missing；
evidence_validation_failed；
trace_missing；
audit_missing；
gate_missing；
redaction_required；
relationship_missing；
permission_limited；
unknown_status。
```

处理规则：

```text
1. 缺口必须前端可见；
2. evidence_missing 必须阻塞可信完成；
3. trace_missing 必须阻塞 trusted result；
4. audit_missing 必须阻塞高风险动作完成；
5. gate_missing 必须阻塞阶段晋级；
6. redaction_required 必须阻止原文展示；
7. unknown_status 不得默认通过。
```

---

## 12. 前端展示验收

前端必须展示：

```text
Artifact 身份和状态；
Evidence 状态和 Gap；
Trace 状态；
Audit 状态；
对象关联；
缺口提示；
Gate 状态；
脱敏状态；
next_actions。
```

前端不得：

```text
1. 将 Artifact 显示为 Evidence validated；
2. 将 Trace 显示为 Evidence；
3. 将 Audit 显示为授权本身；
4. 隐藏 Evidence Gap；
5. 隐藏 Trace / Audit 缺失；
6. 用绿色成功样式展示 candidate；
7. 展示未脱敏内容；
8. 用 mock 状态伪造真实链路。
```

---

## 13. 安全与脱敏验收

必须检查：

```text
Artifact 是否脱敏；
Evidence 是否脱敏；
Trace 是否脱敏；
Audit 是否脱敏；
output_ref 是否受控；
报告 / 截图 / 导出是否脱敏；
模型上下文是否不含 Secret；
Memory 是否不写入 Secret；
redaction_required 是否不可绕过。
```

红线：

```text
1. 任何对象不得包含 Key / Token / Secret / Password 明文；
2. credential_invalid 不返回密钥片段；
3. 导出不得包含未脱敏内容；
4. 权限提升也不得展示 Secret 明文；
5. 脱敏失败不得展示原文；
6. "已脱敏"不等于"可公开"。
```

---

## 14. API 字段建议

对象公共字段建议（R1 建议契约，R4 校准）：

```text
object_id；
object_type；
project_id；
run_id；
stage；
status；
source_ref；
created_by；
created_at；
updated_at；
trace_refs；
audit_refs；
evidence_refs；
artifact_refs；
gate_refs；
redaction_status；
permission_scope；
error_ref；
request_id。
```

Evidence 专项字段建议：

```text
claim_refs；
validation_status；
validation_method；
validated_at；
evidence_gap_refs；
accepted_risk_audit_ref。
```

Trace 专项字段建议：

```text
action_id；
action_type；
trace_type；
output_ref；
parent_trace_ref；
write_status。
```

Audit 专项字段建议：

```text
audit_type；
gate_id；
decision；
risk_level；
accepted_risks；
policy_check_ref；
audit_status。
```

规则：

```text
1. R1 字段为建议契约；
2. R2/R4 按真实实现校准；
3. status 不得为空，未知使用 unknown；
4. redaction_status 必须可见；
5. 引用字段不得指向未脱敏原文；
6. API 默认不返回 Secret。
```

---

## 15. API 错误码建议

错误码建议（R1 建议，R4 校准）：

```text
artifact_missing；
artifact_invalid；
evidence_missing；
evidence_validation_failed；
evidence_gap_blocking；
trace_missing；
trace_write_failed；
audit_missing；
audit_write_failed；
gate_missing；
relationship_missing；
redaction_required；
redaction_failed；
permission_denied；
state_mismatch；
object_not_found；
internal_error。
```

规则：

```text
1. 错误响应必须包含 request_id；
2. 可用时包含 trace_ref；
3. gate_missing 应包含 gate_ref 或 next_actions；
4. redaction_required 不返回原文；
5. internal_error 不暴露堆栈；
6. 错误不得被前端显示为通过。
```

---

## 16. SSE / Event 建议

事件建议（R1 建议，R4 校准）：

```text
artifact_created；
artifact_status_changed；
evidence_candidate_created；
evidence_validation_started；
evidence_validated；
evidence_validation_failed；
evidence_gap_detected；
trace_written；
trace_missing_detected；
audit_written；
audit_write_failed；
gate_required；
relationship_updated；
redaction_required；
resync_required。
```

规则：

```text
1. SSE 事件只作为刷新提示（详见 00-测试与验收总览 §11）；
2. 事件 payload 不得包含密钥；
3. evidence_validated 不等于阶段 completed；
4. audit_written 不等于用户验收通过；
5. resync_required 必须重新查询对象；
6. mock 事件必须显式标记。
```

---

## 17. 验收报告要求

Artifact / Evidence / Trace / Audit 验收报告建议包含：

```text
验收范围；
对象清单；
对象身份检查；
对象状态检查；
关联链检查；
Evidence Gap；
Trace 缺失；
Audit 缺失；
Gate 状态；
脱敏检查；
前端展示检查；
缺陷分级（详见 00-测试与验收总览 §15）；
结论；
待确认项；
下一步建议。
```

规则：

```text
1. 报告不得包含密钥；
2. 报告不得隐藏缺口；
3. 报告不得把 candidate 写成 validated；
4. 报告不得把 Trace / Audit 写成 Evidence；
5. 报告必须可供独立验收 Agent 复核（详见 00-测试与验收总览 §17）。
```

---

## 18. R2 / R4 / R6 / R8 / R9-R12 / R17 校准项

### 18.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. Artifact / Evidence / Trace / Audit 术语是否与产品定义和架构设计一致；
3. 是否有对象数据模型文档需要引用；
4. P5 验证 Evidence 槽位是否与 02-迁移正确性与验证策略 一致；
5. 是否仍无 Mission 产品层；
6. 是否重复上级事实，需要改为引用。
```

### 18.2 R4 API 与工程骨架校准

```text
1. 对象 API 是否接入；
2. 关联字段是否可查询；
3. Evidence validation 是否有实现状态；
4. Trace / Audit Writer 是否接入；
5. 错误码和 SSE 是否可映射前端。
```

### 18.3 R6 Agent / Skill / 资源基础校准

```text
1. Agent Definition 是否包含 Artifact / Evidence 契约；
2. Tool / MCP 输出是否正确进入 output_ref / Artifact / Evidence candidate；
3. 资源调用是否有 Trace；
4. 高风险资源是否有 Audit；
5. Case / Knowledge 是否保持只读参考。
```

### 18.4 R8 Workspace 真实化校准

```text
1. 文件面板是否区分材料身份；
2. 右侧检视是否展示 Evidence / Trace / Audit；
3. 文件写入是否生成 Trace；
4. 输出和预览是否脱敏；
5. 状态恢复后对象关系是否一致。
```

### 18.5 R9-R12 主链路校准

```text
R9：P0-P1 Artifact / Evidence / Trace / Audit 是否真实生成；
R10：P2-P3 评估和规划对象链是否真实生成；
R11：P4 执行对象链是否真实生成；
R12：P5-P6 验证和交付对象链是否真实生成。
```

### 18.6 R17 发布冻结校准

```text
1. 全链路对象身份是否正确；
2. Evidence Gap 是否清零或审计接受；
3. Trace / Audit 缺失是否清零或有裁决；
4. 前端是否无误导展示；
5. mock 对象链是否替换或显式标记。
```

---

## 19. Artifact / Evidence / Trace / Audit 验收红线

```text
1. 不得把 Artifact 生成写成 Evidence validated；
2. 不得把 Evidence candidate 写成 Evidence validated；
3. 不得把 Trace 存在写成结果可信；
4. 不得把 Audit 存在写成用户授权或验收通过；
5. 不得把模型输出、资源输出、命令输出直接写成 Evidence validated；
6. 不得把没有 Evidence 的阶段显示为 completed；
7. 不得把没有 Trace 的结果显示为可信完成；
8. 不得隐藏 Evidence Gap；
9. 不得隐藏 Trace / Audit 缺失；
10. 不得在 Artifact / Evidence / Trace / Audit 中泄露 Key / Token / Secret / Password；
11. 不得将参考资料或历史材料自动显示为当前 Evidence；
12. 不得将 Audit 摘要替代 Evidence；
13. 不得将 Trace 详情替代验证结果；
14. 不得用 mock 对象链伪造真实链路；
15. 不得使用旧 Phase / 旧 F0-F6 / 旧 S0-S7 作为当前主流程；
16. 不得引入 Mission 产品层；
17. 不得在长期 API、DB、路由、组件、状态字段中固化 V26.1；
18. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 20. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确验收目标（§1）；
2. 明确四类对象边界（§2，引用 02-术语表）；
3. 明确对象身份验收（§3）；
4. 明确 Artifact 验收规范（§4）；
5. 明确 Evidence 验收规范（§5）；
6. 明确 Trace 验收规范（§6）；
7. 明确 Audit 验收规范（§7）；
8. 明确四类对象关联验收（§8）；
9. 明确 P0-P6 阶段验收映射（§9）；
10. 明确 P5 迁移正确性专项验收（§10，引用 02-迁移正确性）；
11. 明确缺口处理（§11）；
12. 明确前端展示验收（§12）；
13. 明确安全与脱敏验收（§13）；
14. 明确 API 字段、错误码、SSE / Event 建议（§14-§16）；
15. 明确验收报告要求（§17）；
16. 明确 R2 / R4 / R6 / R8 / R9-R12 / R17 校准项（§18）；
17. 明确验收红线 18 条（§19）；
18. 未新增产品决策、未引入 Mission 产品层、未固化最终实现。
```
