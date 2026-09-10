# 02-风险分级与Gate策略

> 文档路径：`文档/07-安全与权限/02-风险分级与Gate策略.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/草稿/02-风险分级与Gate策略.md`
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R1 文档正式化流程
> 最后更新时间：2026-06-24（R2 校准：§3.1 新增"级别归属创建期确定 + 可经变更控制调整"，Q11；R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留）
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`07-安全与权限/` 专题规范之三。定义 rebuild 当前版本风险分级体系（L0-L5）与 Gate 策略的详述规范，覆盖风险判断维度、每级策略（示例/默认动作/升级条件）、Gate 类型策略、Manual/Plan/Auto 下 Gate 差异、P 阶段晋级 Gate、Evidence Gap Gate、各动作类型风险归口（命令/写盘-Patch/Git/Tool-MCP-资源/外部系统写/模型调用/凭据）、Policy blocked 策略、Gate 决策与 Audit、前端展示、API 字段/错误码、SSE 事件、状态恢复和 R2/R4/R6/R8/R11/R14/R15/R17 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-023~D-027、D-030~D-034、D-036、D-040、D-043/D-044、D-048、D-050、D-061、D-063、D-065、D-066）、`文档/00-项目治理/02-术语表.md`（第七部分：L0-L3 基础定义 + Gate 术语）、`文档/07-安全与权限/00-安全与权限总览.md`（§8 风险分级速查 + §10 Gate 总览）、`文档/07-安全与权限/01-Policy-Hook-Authorization规范.md`（§13-§15 Risk/Gate/Audit Policy）。
> 同级文档：`00-安全与权限总览.md`（已落位）+ `01-Policy-Hook-Authorization规范.md`（已落位）+ 本文（02）+ `03-命令-Tool-MCP-Git-写盘授权规范.md`（已落位）
> 重要边界：本文是风险分级与 Gate 策略详述源。L0-L3 基础定义详述源为 `02-术语表.md` 第七部分，本文在此基础上扩展策略、示例、升级条件和 L4/L5。Gate 类型总览见 `00-安全与权限总览.md` §10，本文展开每种 Gate 的策略规则。本文不替代 Policy DSL、命令拦截实现、Tool/MCP 风险矩阵、资源注册表、安全中间件、Gate API、Agent Definition Contract 或代码实现。本文为 R1 正式候选（建议契约），R2/R4/R6/R8/R11/R14/R15/R17 需按真实实现和联调结果校准。

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开风险分级与 Gate 策略，不重新定义上级事实。L0-L3 基础定义见 `02-术语表.md` 第七部分（详述源），本文扩展为完整策略（示例/默认动作/升级条件）并引入 L4/L5。Gate 类型总览见 `00-安全与权限总览.md` §10，本文展开每种 Gate 的策略规则。Risk/Gate/Audit Policy 协作见 `01-Policy-Hook-Authorization规范.md` §13-§15，本文不重复。

本文必须遵守（来源标注于每条规则）：

```text
1. P 阶段晋级 Gate 必须用户授权（D-023）；
2. Manual / Plan / Auto 不改变 P0-P6 产品流程，不改变 P 阶段晋级必须用户授权的规则（D-024）；
3. Manual / Plan / Auto 只改变阶段内部计划审核、任务审核、授权审核和异常升级策略（D-025）；
4. 安全机制采用 Hook + Policy + Security / Authorization Agent 三层（D-030）；
5. Policy 是硬约束，优先级高于 Agent 判断（D-031）；
6. Security / Authorization Agent 不能批准 Policy 禁止的动作（D-031）；
7. 命令、Tool、MCP、写盘、Git 操作等必须先映射到风险级别，再按 Manual / Plan / Auto 策略处理（D-033）；
8. Auto Mode 下低风险命令是否允许 Agent 审核后自动放行，应按照动作所属风险级别判断，而不是硬编码具体命令（D-033）；
9. L5 高风险动作暂定全部强制用户 Gate（D-034）；
10. 资源使用按风险分级（D-040）；
11. 资源调用必须记录来源、风险、权限和使用 Trace（D-040）；
12. 社区资源默认只读参考，不得默认执行（D-061）；
13. 密钥、Token、Secret、Password 必须脱敏（D-032）；
14. Gate 决策和高风险动作必须可 Audit（D-034）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

本文不得：

```text
1. 将风险判断简化为固定命令白名单；
2. 将低风险动作永久自动放行；
3. 将 Auto Mode 解释为所有动作自动执行；
4. 将 Plan Mode 解释为计划内动作无需 Hook / Policy；
5. 将 Manual Mode 的用户批准解释为可覆盖 Policy；
6. 将 L5 高风险动作自动放行；
7. 将 Gate 显示为普通 toast 后继续执行；
8. 将 Policy blocked 提供为可批准；
9. 将 Audit 写入等同于授权通过；
10. 将 Trace 写入等同于结果可信；
11. 将模型输出、资源输出或 Artifact 显示为 Evidence validated；
12. 泄露 Key / Token / Secret / Password；
13. 引入 Mission 产品层或 mission_id 长期字段；
14. 把 R1 正式候选（建议契约）伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 风险分级目标

风险分级用于在动作执行前判断（D-033~D-034）：

```text
1. 是否允许执行；
2. 是否需要用户 Gate；
3. 是否需要 Security / Authorization Agent 辅助评审；
4. 是否必须写 Audit；
5. 是否必须写 Trace；
6. 是否必须脱敏；
7. 是否应被 Policy 阻断；
8. 是否需要补充 Evidence 或说明。
```

风险分级不是为了让系统"看起来安全"，而是为了保证 Agent、工具、模型、资源和前端在执行前具有统一的安全判断依据。

---

## 2. 风险分级总览

> L0-L3 基础定义详述源为 `02-术语表.md` 第七部分。本文扩展为完整策略（示例/默认动作/升级条件）并引入 L4/L5。速查引用见 `00-安全与权限总览.md` §8。

R1 风险级别：

```text
L0：只读参考；
L1：低风险读取 / 分析 / 方法调用；
L2：受控执行 / 有限写入 / 可回滚变更；
L3：外部系统写操作 / 影响外部状态 / 中高风险变更；
L4：高影响范围动作 / 大规模修改 / 敏感路径操作 / 难回滚动作；
L5：高风险动作，暂定全部强制用户 Gate（D-034）。
```

分级规则（D-033）：

```text
1. 风险级别由动作、范围、路径、上下文、权限、外部影响、可回滚性共同决定；
2. 同一命令在不同上下文中风险可能不同；
3. 同一 Tool / MCP 在不同参数、不同目标系统下风险可能不同；
4. 低风险级别不代表永远自动放行；
5. unknown 不得默认按低风险处理；
6. Policy blocked 优先于风险分级结果（D-031）。
```

---

## 3. 风险判断维度

每次风险判断至少考虑（D-033）：

```text
action_type：动作类型；
actor_type：用户 / Agent / Tool / MCP / 系统；
execution_mode：Manual / Plan / Auto；
project_scope：是否在当前 Project 范围内；
workspace_scope：是否在 Project Workspace 范围内；
write_scope：写入范围；
external_effect：是否影响外部系统状态；
credential_involved：是否涉及凭据；
secret_exposure_risk：是否存在敏感信息泄露风险；
rollback_capability：是否可回滚；
plan_scope：是否在已批准计划范围内；
resource_source：资源来源；
resource_review_status：资源审核状态；
evidence_impact：是否影响 Evidence / 验证结论；
trace_audit_requirement：是否需要 Trace / Audit。
```

规则：

```text
1. 参数、路径和目标对象必须参与判断；
2. 计划内动作仍需 Hook + Policy（D-025）；
3. 凭据参与时必须触发脱敏策略（D-032）；
4. 外部系统写操作默认高风险；
5. 无法判断时返回 needs_review 或 requires_gate。
```

### 3.1 级别归属在创建期确定，且可经变更控制调整（D-033，用户裁决 Q11）

风险级别**不硬编码具体命令清单**。命令、Tool、MCP、确定性转换器等动作/资源**在创建（注册）时设定其风险级别归属**（依据 §3 各维度评估），运行时按该归属 + 执行模式处理。

```text
1. 创建/注册某动作或资源时，必须为其指定初始风险级别（L0-L5），并记录归属理由；
2. 该级别归属后续可经变更控制规范调整（如评估后由 L5 下调为 L4，或反向上调），调整须登记 C2+ 变更并说明理由；
3. 调整不得削弱以下硬红线：L5 在所有模式强制用户 Gate（D-034）、外部系统写默认高风险、凭据脱敏（D-032）；
4. 不维护一份"被允许命令"的硬编码白名单来绕过分级判断（D-033）。
```

> 关联：D-033（按级别判断不硬编码）、D-034（L5 强制 Gate）；变更流程见 `文档/00-项目治理/04-变更控制规范.md`。

---

## 4. L0 只读参考

L0 适用于只读、不可执行、不会改变项目或外部状态的动作。

示例：

```text
查看 Case；
查看 Knowledge；
查看历史归档资料；
查看只读参考文档；
查看已脱敏配置存在状态；
查看资源说明；
读取文件元数据；
查看 Trace / Audit 摘要。
```

策略：

```text
默认动作：允许读取；
Gate：通常不需要；
Audit：通常不需要，敏感或安全对象除外；
Trace：按上下文记录；
前端标记：只读参考；
限制：不得执行、不得写盘、不得自动成为 Evidence validated。
```

升级条件：

```text
1. 涉及敏感文件且可能展示明文；
2. 只读资料被试图作为当前事实源；
3. Case 被误认为 Tool；
4. 社区只读资源被试图执行；
5. 敏感文件内容可能泄露。
```

红线：

```text
1. L0 不代表可公开；
2. 只读资料不得自动进入当前事实源；
3. Case 不得显示为 Tool；
4. 社区只读资源不得默认执行（D-061）；
5. 敏感文件即使只读也不得展示明文（D-032）。
```

---

## 5. L1 低风险读取 / 分析

L1 适用于低影响、非破坏性、通常不改变状态的读取与分析动作。

示例：

```text
读取当前 Workspace 内普通源码；
分析文件结构；
生成不落盘的摘要；
运行纯解析逻辑；
模型辅助解释；
资源方法级只读调用；
git status / log / diff 在安全上下文下的只读操作。
```

策略：

```text
默认动作：可允许或自动执行；
Gate：通常不需要；
Audit：通常不需要；
Trace：建议记录，尤其是 Run / Node 内动作；
前端标记：低风险 / 只读分析；
限制：不得包含敏感明文，不得越界读取。
```

升级条件：

```text
1. 读取敏感文件；
2. 读取 workspace 外路径；
3. 读取外部系统数据；
4. 读取结果进入模型上下文且可能包含密钥；
5. 读取历史资料并试图作为当前事实源。
```

---

## 6. L2 受控执行 / 有限写入

L2 适用于范围明确、可回滚、影响有限的执行和写入动作。

示例：

```text
在 Workspace 内生成草稿文件；
生成 Artifact draft；
运行本地测试；
运行构建命令；
应用小范围可回滚 Patch；
git add / commit 在本地且范围明确；
调用已审核的本地 Tool 生成报告；
确定性转换 dry-run。
```

策略：

```text
默认动作：按执行模式判断；
Gate：Manual 下倾向需要，Plan / Auto 下视计划范围与 Policy 判断；
Audit：视写入范围和影响决定；
Trace：必须记录；
前端标记：受控执行 / 有限写入；
限制：不得越过 Workspace 边界，不得影响外部系统。
```

升级条件：

```text
1. 写入范围扩大；
2. 写入敏感路径；
3. Patch 难回滚；
4. 生成内容可能包含密钥；
5. 执行失败可能破坏状态；
6. 动作超出已批准计划。
```

---

## 7. L3 外部影响 / 中高风险变更

L3 适用于可能影响外部状态、远程对象或项目关键状态的动作。

示例：

```text
外部系统写操作；
远程 MCP 写操作；
git push；
远程仓库分支变更；
向工单系统写入；
发布社区资源；
调用外部 API 产生变更；
影响多个文件的 Patch apply；
执行会改变环境状态的命令。
```

策略：

```text
默认动作：需要 Gate 或 Authorization 评审；
Gate：通常需要；
Audit：必须或强建议；
Trace：必须；
前端标记：高影响 / 外部写操作；
限制：不得静默执行，不得凭 Agent 自评自动放行。
```

升级条件：

```text
1. 无法回滚；
2. 影响生产或远程共享状态；
3. 涉及凭据或敏感数据；
4. 影响交付 Evidence；
5. 影响范围不明确；
6. 用户未明确授权。
```

---

## 8. L4 高影响范围动作

L4 适用于高影响、大范围、敏感或难回滚动作。

示例：

```text
大范围代码重写；
批量删除或覆盖文件；
大范围 Patch apply；
敏感路径写入；
权限配置变更；
环境关键配置变更；
数据库 schema 破坏性变更；
影响多个 Project 的操作；
可能导致 Evidence / Trace / Audit 丢失的操作。
```

策略：

```text
默认动作：必须 Gate；
Gate：必须；
Audit：必须；
Trace：必须；
前端标记：高风险；
限制：不得由 Auto Mode 静默执行。
```

升级条件：

```text
1. 不可回滚；
2. 影响外部系统；
3. 涉及密钥；
4. 影响多个项目；
5. 影响审计和证据链；
6. Policy 规则命中 forbidden。
```

---

## 9. L5 强制用户 Gate 高风险动作

L5 是当前版本暂定的最高风险级别（D-034）。

L5 策略：

```text
默认动作：禁止自动执行；
Gate：全部强制用户 Gate；
Audit：必须；
Trace：必须；
Authorization Agent：只能辅助解释和建议，不能代替用户（D-031）；
前端标记：L5 高风险 / 必须用户授权；
执行条件：用户 Gate 通过且未被 Policy 阻断。
```

L5 候选场景：

```text
不可回滚外部系统写操作；
大范围 destructive 写盘；
可能泄露密钥的操作；
绕过或修改 Policy 的操作；
删除审计 / 证据 / Trace 的操作；
越过 Workspace 边界的大范围写入；
远程发布或推送；
影响多个 Project 或共享资源的操作。
```

规则：

```text
1. L5 必须用户 Gate（D-034）；
2. L5 不能由 Auto Review Agent 自动批准；
3. L5 不能由 Authorization Agent 代替用户批准（D-031）；
4. L5 决策必须写 Audit；
5. L5 执行结果必须可 Trace；
6. L5 具体范围在 R2/R11/R14/R15 继续校准。
```

---

## 10. Gate 类型策略

> Gate 类型总览和基本规则见 `00-安全与权限总览.md` §10。本文展开每种 Gate 的策略规则和适用条件。Policy 层面的 Gate Policy（何时触发 requires_gate）见 `01-Policy-Hook-Authorization规范.md` §14。

Gate 类型建议：

```text
stage_promotion_gate：P 阶段晋级；
stage_plan_gate：阶段计划审核；
task_plan_gate：任务计划审核；
task_plan_batch_gate：批量任务计划审核；
high_risk_action_gate：高风险动作；
write_scope_gate：写盘范围；
patch_apply_gate：Patch 应用；
git_operation_gate：Git 高风险操作；
external_write_gate：外部系统写操作；
resource_call_gate：资源调用；
credential_gate：凭据问题；
evidence_gap_gate：证据缺口风险接受；
policy_block_gate：Policy 阻断展示，不提供批准继续；
delivery_gate：交付确认。
```

规则：

```text
1. stage_promotion_gate 必须由用户授权（D-023）；
2. policy_block_gate 不是批准流程，只用于展示阻断原因；
3. evidence_gap_gate 接受风险必须 Audit；
4. external_write_gate 默认高风险；
5. Gate 类型必须能映射前端展示和 API 错误码；
6. Gate 不得被折叠为普通通知。
```

---

## 11. 执行模式与 Gate 策略

> 三模式完整定义见 `00-安全与权限总览.md` §7 和 `01-决策记录.md` D-023~D-027。本文只列出各风险级别在三模式下的 Gate 差异。

### 11.1 Manual Mode

```text
L0：通常允许查看；
L1：通常允许或轻提示；
L2：倾向用户确认，尤其是写盘；
L3：需要 Gate；
L4：必须 Gate + Audit；
L5：必须用户 Gate + Audit。
```

### 11.2 Plan Mode

```text
L0：通常允许查看；
L1：计划范围内可自动执行；
L2：计划范围内由 Hook + Policy 放行，越界则 Gate；
L3：通常 Gate 或 Authorization 评审；
L4：必须 Gate + Audit；
L5：必须用户 Gate + Audit。
```

### 11.3 Auto Mode

```text
L0：通常允许查看；
L1：可由 Agent 审核后自动执行；
L2：需 Hook + Policy + Auto Review 判断；
L3：高风险、低置信度、冲突、超范围回用户；
L4：必须 Gate + Audit；
L5：必须用户 Gate + Audit。
```

共同红线（D-023、D-031、D-034）：

```text
1. Policy blocked 在任何模式下直接阻断；
2. P 阶段晋级在任何模式下必须用户授权；
3. L5 在任何模式下必须用户 Gate；
4. 凭据泄露风险在任何模式下必须 redaction_required 或 block；
5. Trace / Audit 缺失不得静默通过（D-050）。
```

---

## 12. P 阶段晋级 Gate 策略

> P 阶段晋级 Gate 的完整定义和前端/API/Agent 表达要求见 `00-安全与权限总览.md` §11。本文展开检查项和完成判定规则。

P 阶段晋级 Gate 用于从一个启用阶段进入下一阶段。

检查项建议：

```text
阶段目标是否达成；
Stage Plan 是否完成或被接受；
Task Plan / TaskGraph 是否完成或有明确结论；
Artifact 是否齐备；
Evidence 是否齐备；
Evidence Gap 是否关闭或被明确接受；
Trace 是否存在；
Audit 是否存在；
阻塞项是否清理；
用户是否明确授权。
```

规则（D-023）：

```text
1. Stage completed 必须以后端状态为准；
2. Artifact 生成不等于阶段完成；
3. Evidence candidate 不等于 Evidence validated；
4. Evidence 不足不得默认晋级；
5. 用户批准后必须写 Audit；
6. 被裁剪阶段显示 not_enabled / not_applicable，不显示 completed。
```

---

## 13. Evidence Gap Gate 策略

Evidence Gap Gate 用于处理证据不足但需要继续推进或接受风险的情况。

触发场景：

```text
P5 验证证据不足；
关键 claim 缺少 Evidence；
Evidence validation_failed；
Trace 缺失导致可信链路不足；
用户要求继续但证据不完整；
交付前证据槽位未满足。
```

策略：

```text
默认动作：阻塞或要求补证；
Gate：接受风险时必须 Gate；
Audit：接受风险必须写 Audit；
Trace：必须记录缺口和处理过程；
前端：高亮 Evidence Gap；
限制：不得把缺口隐藏为 warning 后 completed。
```

---

## 14. 命令风险策略

> 命令授权的完整处理流程见 `03-命令-Tool-MCP-Git-写盘授权规范.md`。本文只定义命令的风险分级策略。

命令风险不由命令名单独决定（D-033）。

判断维度：

```text
命令类别；
参数；
路径；
目标文件；
影响范围；
是否写盘；
是否外部写；
是否可回滚；
是否涉及凭据；
是否在计划范围内；
是否会影响 Evidence / Trace / Audit。
```

策略示例：

```text
只读查看当前 Workspace 普通文件：通常 L1；
运行本地测试：通常 L2；
修改单个受控文件：通常 L2；
批量覆盖文件：通常 L4；
触发远程写操作：通常 L3-L5；
可能泄露密钥的命令：redaction_required 或 blocked；
删除审计 / 证据 / Trace：通常 forbidden 或 L5。
```

规则：

```text
1. 命令执行必须 Trace；
2. 高风险命令必须 Gate / Audit；
3. 命令输出必须脱敏；
4. exit_code 不等于业务完成；
5. 命令失败必须 error_ref；
6. 命令风险升级必须前端可见。
```

---

## 15. 写盘与 Patch 风险策略

> 写盘授权的完整处理流程见 `03-命令-Tool-MCP-Git-写盘授权规范.md`。本文只定义风险分级策略。

写盘风险判断维度：

```text
写入路径；
写入范围；
文件身份；
是否覆盖；
是否可回滚；
是否影响 Evidence / Audit / Trace；
是否在 Workspace 内；
是否涉及敏感文件。
```

Patch 策略：

```text
patch_generated：生成候选 Patch，通常不等于写入；
patch_apply_waiting_gate：应用 Patch 前等待 Gate；
patch_applied：已应用，但不等于验证通过；
patch_apply_failed：应用失败；
patch_reverted：已回滚。
```

规则：

```text
1. Patch generated 不等于 Patch applied；
2. Patch applied 不等于 P5 验证通过；
3. 大范围 Patch apply 默认高风险；
4. Workspace 外写盘默认 blocked 或强 Gate；
5. 敏感文件写入需专门检查；
6. 写盘必须 Trace，高风险写盘必须 Audit。
```

---

## 16. Git 风险策略

> Git 操作的完整授权规范见 `03-命令-Tool-MCP-Git-写盘授权规范.md`。本文只定义风险分级策略。

Git 操作风险建议：

```text
git status / log / diff：通常 L1，但需上下文判断；
git clone / fetch / pull：通常 L1-L2，凭据必须脱敏；
git checkout：可能 L2-L3，视是否影响工作区；
git add / commit：通常 L2，需计划范围检查；
git reset / rebase：通常 L3-L4；
git merge：通常 L3-L4；
git push：通常 L3-L5，默认需要 Gate / Audit。
```

规则（D-032~D-033）：

```text
1. Git 凭据不得展示；
2. 包含 token 的 Git URL 必须脱敏；
3. 远端写操作默认高风险；
4. merge / rebase / reset 需说明影响范围；
5. Git 操作失败不得泄露凭据；
6. 高风险 Git 操作必须 Audit。
```

---

## 17. Tool / MCP / 资源风险策略

> 资源调用的完整安全策略见 `00-安全与权限总览.md` §14、§19 和 `01-Policy-Hook-Authorization规范.md` §20。本文只定义风险分级归口。

资源风险取决于资源类型、来源、审核状态、执行范围和外部影响（D-040）。

资源风险建议：

```text
Case：L0，只读参考；
Knowledge：通常 L0-L1；
Template：通常 L0-L2，应用时可能升级；
Skill：视是否执行和写入范围判断；
Tool：视输入输出和写入范围判断；
MCP：视外部影响判断；
Expert Agent：视权限和输出动作判断；
社区资源：默认只读参考（D-061）；
确定性转换资源：dry-run 通常低于 apply，apply 需 Gate 判断（D-063）。
```

规则：

```text
1. Case 不是 Skill，也不是 Tool；
2. 社区资源默认只读参考（D-061）；
3. 可执行资源必须有 Resource Check；
4. 高风险资源必须 Gate / Audit；
5. 资源调用输出不自动成为 Evidence validated；
6. 资源凭据必须脱敏。
```

---

## 18. 外部系统写操作风险策略

外部系统写操作默认高风险。

外部系统写操作包括：

```text
Git 远端写入；
外部工单系统写入；
远程 MCP 写入；
云服务状态变更；
社区资源发布；
外部存储写入；
外部 API 状态变更。
```

策略：

```text
默认风险：L3-L5；
Gate：通常必须；
Audit：必须；
Trace：必须；
前端：突出外部影响；
凭据：只显示引用和状态。
```

规则：

```text
1. 必须明确 external_system_ref；
2. 必须说明影响范围；
3. 用户未授权不得继续；
4. 失败响应必须脱敏；
5. 外部系统写操作不得由 mock 伪装；
6. Policy blocked 不得批准继续。
```

---

## 19. 模型调用风险策略

模型调用风险主要来自上下文泄露、输出误用、成本消耗和自动决策误导。

策略（D-036）：

```text
普通模型解释：通常 L1；
模型生成候选方案：通常 L1-L2；
模型生成 Patch：生成阶段通常 L2，应用 Patch 另算；
模型处理含敏感上下文：需脱敏或阻断；
模型输出用于授权：禁止；
模型输出用于 Evidence validated：禁止自动成立。
```

规则：

```text
1. 模型输入不得包含密钥明文（D-032）；
2. 模型输出不等于授权；
3. 模型输出不等于 Evidence validated；
4. Fusion 不绕过 Policy / Gate / Audit（D-036）；
5. Provider Key 不得展示；
6. 模型调用应可 Trace。
```

---

## 20. 凭据与敏感信息风险策略

凭据相关风险优先级高（D-032）。

敏感对象：

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
未脱敏终端输出；
未脱敏 Trace / Audit 详情。
```

策略：

```text
默认动作：redaction_required；
可展示：credential_ref / credential_status / redacted；
Gate：凭据缺失或权限不确定可触发 credential_gate；
Audit：凭据状态变更建议 Audit；
Memory：不得写入密钥或 env 明文（D-044）。
```

规则：

```text
1. 密钥明文不得进入日志、前端、Trace、截图、提交或交接材料；
2. 脱敏失败不得默认展示；
3. credential_invalid 不返回凭据片段；
4. 凭据不进入模型上下文；
5. 凭据不进入 Memory（D-044）；
6. 搜索结果不得返回敏感明文。
```

---

## 21. Policy blocked 策略

Policy blocked 是终止性安全结果（D-031）。详见 `01-Policy-Hook-Authorization规范.md` §10。

前端展示：

```text
被 Policy 阻断；
阻断原因；
policy_check_ref；
trace_ref；
安全下一步；
不可批准继续。
```

规则：

```text
1. blocked 不进入执行；
2. blocked 不进入用户批准继续流程；
3. Authorization Agent 不能批准 blocked（D-031）；
4. 前端不得提供"忽略 Policy 继续"；
5. blocked 建议 Trace；
6. 高风险 blocked 建议 Audit。
```

---

## 22. Gate 决策与 Audit 策略

Gate 决策必须可审计。Gate 决策流程见 `01-Policy-Hook-Authorization规范.md` §25。

Audit 字段建议：

```text
audit_id；
gate_id；
action_id；
actor_ref；
decision；
risk_level；
accepted_risks；
reason；
trace_ref；
created_at。
```

规则：

```text
1. Gate approved 必须有 Audit；
2. Gate rejected 必须有原因；
3. needs_more_info 必须生成 next_actions；
4. rework 必须关联返工目标；
5. Audit 写入失败必须显示 pending / error；
6. Audit 不得包含密钥明文（D-032）。
```

---

## 23. 前端风险展示策略

> 前端安全展示的完整要求见 `00-安全与权限总览.md` §22。本文列出风险分级与 Gate 层面的前端展示要求。

前端必须展示：

```text
risk_level；
risk_reasons；
requires_gate；
requires_audit；
blocked_by_policy；
redaction_required；
waiting_gate；
high_risk；
audit_pending；
trace_missing；
evidence_gap；
next_actions。
```

展示规则（D-050）：

```text
1. L5 使用最高优先级风险视觉；
2. Gate 不得只显示 toast；
3. Policy blocked 不得显示为普通 warning；
4. Evidence Gap 不得隐藏；
5. Trace / Audit 缺失不得隐藏；
6. mock / not_connected 不得显示为真实可执行。
```

---

## 24. API 字段建议

风险判断 API 字段建议（R1 建议契约，R4 按真实实现校准）：

```text
risk_assessment_id；
action_id；
action_type；
project_id；
run_id；
stage；
node_id；
risk_level；
risk_reasons；
policy_check_ref；
gate_required；
gate_type；
gate_ref；
audit_required；
trace_required；
redaction_required；
allowed_scope；
blocked_scope；
next_actions；
created_at。
```

规则：

```text
1. R1 字段为建议契约；
2. R2 按真实实现校准；
3. risk_level 不得为空，未知时使用 unknown；
4. gate_required=true 时必须返回 gate_type 或 gate_ref；
5. policy_blocked 必须返回 policy_check_ref；
6. 凭据字段只返回引用和状态。
```

---

## 25. API 错误码建议

> 安全 API 错误码完整清单见 `00-安全与权限总览.md` §23。本文列出风险分级与 Gate 层面关注的错误码。

```text
risk_level_required；
risk_assessment_failed；
policy_blocked；
gate_required；
gate_not_resolved；
high_risk_requires_user_gate；
audit_required；
audit_write_failed；
trace_write_failed；
redaction_required；
credential_missing；
credential_invalid；
path_out_of_scope；
write_scope_required；
external_write_requires_gate；
resource_read_only；
resource_blocked；
state_mismatch；
internal_error。
```

规则：

```text
1. 错误响应应包含 request_id；
2. 可用时包含 trace_ref；
3. gate_required 包含 gate_ref；
4. high_risk_requires_user_gate 不得提供自动继续；
5. credential_invalid 不返回密钥片段；
6. internal_error 不暴露堆栈。
```

---

## 26. SSE / Event 建议

> 安全 SSE 事件完整清单见 `00-安全与权限总览.md` §24。本文列出风险分级与 Gate 层面关注的事件。

```text
risk_assessment_started；
risk_assessment_completed；
high_risk_detected；
high_risk_gate_required；
gate_created；
gate_waiting_decision；
gate_decided；
policy_blocked；
redaction_required；
audit_required；
audit_written；
audit_write_failed；
trace_written；
trace_write_failed；
resource_gate_required；
command_waiting_gate；
external_write_waiting_gate；
resync_required。
```

规则：

```text
1. 事件只作为刷新提示；
2. 事件 payload 不得包含密钥（D-032）；
3. high_risk_gate_required 不得只 toast；
4. policy_blocked 不得自动重试；
5. resync_required 必须重新查询状态；
6. mock SSE 必须显式标记。
```

---

## 27. 状态恢复策略

状态恢复必须重新查询（D-048）：

```text
risk_assessment；
policy_check；
gate；
audit；
trace；
run；
stage；
workspace；
resource_call；
command_execution。
```

规则：

```text
1. 浏览器缓存不能作为风险事实源；
2. SSE 事件缓存不能作为 Gate 决策事实源；
3. 终端输出不能作为风险事实源；
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
2. L0-L5 是否需要调整；
3. L5 暂定范围是否充分标注；
4. Gate 类型是否与 Gate API 一致；
5. 命令 / Tool / MCP / Git / 写盘 / 外部写策略是否清晰；
6. 是否仍无 Mission 产品层；
7. 是否重复上级事实，需要改为引用。
```

### 28.2 R4 API 与工程骨架校准

```text
1. Risk Assessment API 是否接入；
2. Policy Check 是否接入；
3. Gate / Authorization API 是否接入；
4. Audit / Trace Writer 是否接入；
5. SSE 安全事件是否接入；
6. 错误码是否可映射前端。
```

### 28.3 R6 Agent / Skill / 资源基础校准

```text
1. Agent Definition 是否包含风险级别；
2. Tool / MCP 是否有风险字段；
3. Resource Registry 是否有 review_status / risk_level / permission_scope；
4. Case / Knowledge 只读策略是否实现；
5. Authorization Agent 是否只做辅助建议。
```

### 28.4 R8 Workspace 真实化校准

```text
1. 文件读写风险判断是否接入；
2. Workspace 边界是否接入；
3. 敏感文件脱敏是否接入；
4. 前端风险标签是否准确；
5. 状态恢复是否不依赖前端缓存。
```

### 28.5 R11 P4 执行链路校准

```text
1. 命令风险分级是否真实生效；
2. Tool / MCP Gate 是否真实生效；
3. Patch apply Gate 是否真实生效；
4. Git 高风险操作是否 Gate / Audit；
5. 外部写操作是否 Gate / Audit。
```

### 28.6 R14 / R15 增强资源校准

```text
R14：远程资源调用风险分级、Gate、Audit 校准；
R15：社区资源审核、许可、安全、只读默认、可执行升级策略校准。
```

### 28.7 R17 全量联调校准

```text
1. 用户是否能理解风险级别；
2. L5 Gate 是否不可绕过；
3. Gate 是否不被 toast 化；
4. Trace / Audit / Evidence Gap 缺口是否可见；
5. 前端是否停止用 mock 判断真实风险状态。
```

---

## 29. 风险分级与 Gate 策略红线

以下红线在 rebuild 当前版本任何阶段不得违反（来源：D-023、D-031、D-033、D-034、D-040、D-050、D-061、D-032、D-048）：

```text
1. 不得将风险判断简化为固定命令白名单；
2. 不得将低风险动作永久自动放行；
3. 不得将 Auto Mode 解释为所有动作自动执行；
4. 不得将 Plan Mode 解释为计划内动作无需 Hook / Policy；
5. 不得将 Manual Mode 的用户批准解释为可覆盖 Policy；
6. 不得将 L5 高风险动作自动放行；
7. 不得将 Gate 显示为普通 toast 后继续执行；
8. 不得将 Policy blocked 提供为可批准；
9. 不得将 Audit 写入等同于授权通过；
10. 不得将 Trace 写入等同于结果可信；
11. 不得将模型输出、资源输出或 Artifact 显示为 Evidence validated；
12. 不得泄露 Key / Token / Secret / Password；
13. 不得将社区资源默认显示为可执行；
14. 不得用浏览器缓存、SSE 缓存、终端输出作为风险事实源；
15. 不得引入 Mission 产品层；
16. 不得使用旧 Phase / 旧 F0-F6 / 旧 S0-S7 作为当前主流程；
17. 不得在长期 API、DB、路由、组件、状态字段中固化 V26.1；
18. 不得把 R1 正式候选（建议契约）伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 30. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确风险分级目标；
2. 明确 L0-L5 风险级别定义与策略；
3. 明确风险判断维度（15 项）；
4. 明确各风险级别示例、策略、升级条件；
5. 明确 Gate 类型（14 种）策略；
6. 明确 Manual / Plan / Auto 与 Gate 策略关系；
7. 明确 P 阶段晋级 Gate 检查项；
8. 明确 Evidence Gap Gate 策略；
9. 明确命令、写盘、Patch、Git、Tool、MCP、资源、外部系统写、模型调用、凭据风险策略；
10. 明确 Policy blocked 策略；
11. 明确 Gate 决策与 Audit 策略；
12. 明确前端风险展示策略；
13. 明确 API 字段（19 个）与错误码（19 种）建议；
14. 明确 SSE / Event 建议；
15. 明确状态恢复策略；
16. 明确 R2 / R4 / R6 / R8 / R11 / R14 / R15 / R17 校准项；
17. 明确风险分级与 Gate 策略红线（18 条）；
18. 未新增产品决策；
19. 未引入 Mission 产品层；
20. 未固定最终实现；
21. 与 00-安全与权限总览、01-Policy-Hook-Authorization规范 引用清晰，无冲突。
```
