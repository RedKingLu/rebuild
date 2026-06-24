# 05-资源调用与Case-Knowledge-Tool-MCP边界

> 文档路径：`文档/04-模型与资源/05-资源调用与Case-Knowledge-Tool-MCP边界.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.1.1
> 来源草稿：`产物/草稿/05-资源调用与Case-Knowledge-Tool-MCP边界.md`（v0.1，~877 行；去重 ~77%，主要移除与 00 总览/04/术语表重复的资源类型边界详述 + 调用流程复述 + 字段枚举）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R2
> 最后更新时间：2026-06-24
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`04-模型与资源/` 专题的第 5 份子文档。定义资源**调用模式**分类（6 种模式 × 资源类型映射）、资源输出→Evidence 边界（资源特有角度）、资源调用错误处理（13 种失败模式）。资源类型定义和调用流程见 `00-模型与资源总览.md` §3+§5（专题入口）和 `04-平台资源与Registry规范.md` §3（Registry 层详述源）。
> 上级依据：`文档/04-模型与资源/00-模型与资源总览.md`、`文档/04-模型与资源/04-平台资源与Registry规范.md`、`文档/00-项目治理/01-决策记录.md` D-041。
> 重要边界：本文不重复资源类型逐一定义（→00 总览 §3+术语表 §10）、不重复调用流程（→04 §3）、不重复 Case 转化规则（→04 §5）、不重复 MCP/EA 额外约束（→04 §6）。本文只提供调用模式分类和错误处理——这两个维度是上游有意留白的操作空白。

---

## 0. 编写原则与上游分工

本文遵守事实源层级（D-068）。详述源约定：

```text
资源类型定义（9 类+）           → 00-模型与资源总览.md §3 + 02-术语表.md §10
资源调用完整流程（14 步）        → 04-平台资源与Registry规范.md §3
Case 转化规则（6 条前提）        → 04-平台资源与Registry规范.md §5
MCP/Expert Agent 额外约束       → 04-平台资源与Registry规范.md §6
Agent 类型职责/禁止              → 03-Agent与Skill规范.md §1
资源风险分级 L0-L5              → 02-术语表.md §7 + 01-决策记录.md D-042
资源红线（15 条）               → 00-模型与资源总览.md §4
```

本文展开两个上游未覆盖的操作空白：**调用模式分类**（资源类型→允许的调用模式映射）和**资源调用错误处理**（与 01 §11 模型错误处理互补）。

---

## 1. 资源调用模式分类

资源类型决定了允许的调用模式。这是本文的核心操作规范——上游只定义了"是什么资源"，本文定义"资源可以被怎么调用"。

### 1.1 调用模式定义（6 种）

```text
reference_only       — 只读引用（检索、阅读、引用来源，不产生副作用）
analysis_call        — 分析调用（输入→模型/算法分析→输出建议，仍不写盘）
controlled_execute   — 受控执行（在 Workspace/Execution Session 内执行，可写 Workspace）
workspace_write      — 写入 Project Workspace（修改文件、生成 Patch）
external_write       — 外部系统写操作（远程仓库、工单、云服务、第三方系统）
privileged_operation — 高权限或不可逆操作（删除、大规模修改、密钥操作、生产发布）
```

### 1.2 资源类型 → 允许的调用模式映射

| 资源类型 | 默认模式 | 可升级至 | 硬约束 |
|---|---|---|---|
| Case | reference_only | analysis_call（引用分析） | 永远不得 controlled_execute+ |
| Knowledge | reference_only | analysis_call | 永远不得 controlled_execute+ |
| Template | reference_only | analysis_call | 不得成为事实源；若驱动执行需升级+审核 |
| Tool | analysis_call | controlled_execute / workspace_write | 外部写必须 Gate |
| MCP | reference_only | analysis_call / external_write | external_write 必须 Gate；凭据不得明文 |
| Expert Agent | analysis_call | controlled_execute（辅助） | 不得替代最终验收/Gate/P5 Evidence |
| Skill | analysis_call | controlled_execute / workspace_write | 必须通过 Agent 调用；外部写必须 Gate |
| Policy | — | — | 不是被调用的资源——是调用前的硬检查 |
| 确定性转换 | analysis_call | controlled_execute / workspace_write | 不享有特殊流程特权 |

### 1.3 调用模式升级规则

```text
1. reference_only → analysis_call：检查来源/许可/风险，Policy 放行即可
2. analysis_call → controlled_execute：需受控 Workspace/Execution Session
3. → workspace_write：必须 Gate（L3+）
4. → external_write：必须 Gate + Audit（L3+）
5. → privileged_operation：所有模式强制用户 Gate + Audit（L5）
6. Case/Knowledge/Template 永远不得升级超过 analysis_call——若需执行，走转化流程（见 04 §5）
```

---

## 2. 资源输出与 Evidence 边界

> 模型输出→Evidence 边界见 `01-ModelGateway与模型策略.md` §12。本节补充资源调用的特有角度。

### 2.1 资源输出可以成为

```text
参考输出 / Artifact 候选 / Evidence 候选 / 风险提示 / 计划输入 / 验收辅助材料
```

### 2.2 资源输出不得自动成为

```text
accepted 决策 / 用户授权 / Policy 豁免 / Evidence validated
/ P5 验证通过 / P6 交付完成 / 阶段 completed
```

### 2.3 按资源类型的特殊规则

```text
Case 输出      → 必须标注"参考意见/案例引用/待验证建议"，不可标注"已验证"
Knowledge 输出 → 必须区分"当前事实源"vs"历史/外部参考资料"
Template 输出  → 必须经过对应流程校验——模板填充≠验收通过
Tool/MCP 输出  → 高风险输出必须 Gate；不可信输出不得进入 Evidence
```

### 2.4 资源输出进入 Evidence 的前提

```text
1. 有明确 claim 和可验证来源
2. 有验证方法
3. Trace 完整
4. 必要时经过测试/Acceptance/用户 Gate
5. 不含未脱敏敏感信息
```

---

## 3. 资源调用错误处理

> 模型调用错误处理见 `01-ModelGateway与模型策略.md` §11。本节覆盖资源调用的特有失败模式。

### 3.1 失败模式（13 种）

```text
资源不存在          — resource_id 在 Registry 中未找到
资源状态不可用       — disabled/blocked/deprecated
来源不可信           — source_trust_level 为 unknown/unreviewed/blocked
许可不明             — license_or_usage_note 缺失且可能侵权
权限不足             — 请求的调用模式超过 permission_scope
Policy 禁止          — Policy 明确拦截
Gate 未批准          — 需要 Gate 但未获得用户授权
输入不符合契约       — 调用参数不匹配 input_contract
输出不符合契约       — 返回结果不匹配 output_contract
Tool/MCP 执行失败    — 执行层错误
外部系统失败          — 远程服务不可用或返回错误
Trace/Audit 写入失败 — 记录层故障（不影响执行结果但必须告警）
敏感信息风险         — 检测到可能的密钥泄露
```

### 3.2 处理规则

```text
1. 所有失败必须记录 Trace（resource_call_id + error_ref）
2. 高风险失败（L3+）必须 Audit
3. Policy 禁止 → 立即停止，不得 fallback
4. 权限不足 → Gate 或 blocked，不得自行提权
5. 输出不可信 → 不得进入 Evidence
6. 外部系统写失败 → 不得伪装成功；需回滚或用户确认
7. 多次失败 → 升级为 blocked / gate_required / rework_required
8. 敏感信息风险 → 暂停并进入安全处理流程（与 01 §10 密钥规则一致）
9. Trace/Audit 写入失败 → 告警但不阻塞主流程（记录不可丢失的事实优先于记录本身）
```

### 3.3 与模型错误处理的互补

```text
模型调用错误（01 §11）           资源调用错误（本文）
──────────────────────────────  ──────────────────────────
Provider 不可用/认证失败/限流     资源不存在/状态不可用/许可不明
上下文超限/输出不符合 schema      输入不符合契约/输出不符合契约
模型 fallback/retry              权限不足/Tool-MCP 执行失败
Policy 禁止/预算限制              Gate 未批准/外部系统失败/敏感信息风险
```

---

## 4. R2/R4/R6 校准项

### R2 文档校准

```text
1. 调用模式 6 种分类是否完整（是否有遗漏的调用模式）
2. 资源类型→调用模式映射是否准确（R6 实现时可能调整边界）
3. Case/Knowledge/Template 永远不得升级的硬约束是否过严
4. 是否仍有 Mission/mission_id 残留
```

### R4 工程骨架

```text
调用模式在 API 中的表达；调用前检查（模式 vs 权限）接入 Policy/Hook
```

### R6 Agent/Skill/资源基础

```text
调用模式在 NodeLoop 中的落地；资源输出→Evidence 候选的自动标记
```

---

## 5. 红线

```text
1. 不得让 Case/Knowledge/Template 进入 controlled_execute 或更高模式
2. 不得让未审核资源进入 analysis_call+
3. 不得让 read_only 资源进入 controlled_execute+
4. 不得跳过调用模式检查直接执行
5. 不得无 Trace 调用资源
6. 不得无 Audit 执行 L3+ 资源调用
7. 不得把资源输出自动标记为 Evidence validated
8. 不得泄露 Key/Token/Secret/Password
9. 不得引入 Mission 产品层
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 6. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 6 种调用模式定义（reference_only→privileged_operation）——§1.1
2. 明确资源类型→调用模式映射表（9 行 × 默认/可升级/硬约束）——§1.2
3. 明确调用模式升级规则 6 条——§1.3
4. 明确资源输出与 Evidence 边界——按资源类型的 4 条特殊规则 + 5 条准入前提——§2
5. 明确资源调用错误处理——13 种失败模式 + 9 条处理规则——§3
6. 明确与模型错误处理的互补关系——§3.3
7. 明确 R2/R4/R6 校准项——§4
8. 明确红线 9 条——§5
9. 未重复 00 总览的资源类型定义、04 的调用流程/Case 转化/MCP 约束
10. 未固化调用记录字段 schema（→R4）
11. 未引入 Mission 产品层
12. 未新增产品决策
```
