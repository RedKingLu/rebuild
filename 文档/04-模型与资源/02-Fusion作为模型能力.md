# 02-Fusion作为模型能力

> 文档路径：`文档/04-模型与资源/02-Fusion作为模型能力.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/02-Fusion作为模型能力.md`（v0.1，~751 行；去重 ~56%，主要移除与 01 §8 重复的定位/调用规则/合入规则 + 字段枚举）
> 最后更新时间：2026-06-24
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`04-模型与资源/` 专题的第 2 份子文档。定义 Fusion 的**应用层规范**——适用/不适用场景矩阵、输出格式契约、与 Gate/确定性转换的交互规则、R13 建设验收清单。Fusion 的定位定义、调用规则和合入规则见 `01-ModelGateway与模型策略.md` §8（专题详述源）和 `01-决策记录.md` D-035（治理详述源）。
> 上级依据：`文档/04-模型与资源/01-ModelGateway与模型策略.md` §8、`文档/00-项目治理/01-决策记录.md` D-035。
> 重要边界：本文不重复 Fusion 的定位定义、调用规则和合入规则（已在 01 §8）。本文只展开应用场景判断、输出规范、交互规则和建设验收。

---

## 0. 编写原则

本文遵守事实源层级（D-068）。Fusion 的详述源约定：

```text
Fusion 定位与定义       → 01-决策记录.md D-035（治理层）
Fusion 调用规则与合入规则 → 01-ModelGateway与模型策略.md §8（专题详述源）
Fusion 应用场景与交互规范 → 本文（应用层补充）
```

本文不重复 01 §8 已定义的内容：Fusion 是什么/不是什么、7 条调用规则、5 条合入规则。

---

## 1. Fusion 适用场景矩阵

### 1.1 推荐使用（辅助分析）

| 场景 | 阶段 | Fusion 角色 | 不可替代的底线 |
|---|---|---|---|
| 风险分析 | P2 | 识别风险模式、整理阻塞项 | 风险结论必须保留来源和 Evidence |
| 方案比较 | P3 | 多方案对比、优劣分析 | 不得自动批准 Stage Plan/Task Plan |
| 计划评审 | P3 | TaskGraph 风险检查、完整性检查 | 最终审批必须用户 Gate |
| 验证结果解读 | P5 | 失败原因归纳、差异说明 | 不得替代构建/运行/测试/回归对比 |
| 交付说明 | P6 | 风险摘要、证据链整理 | 不得隐藏验证失败或遗留风险 |
| 决策辅助 | 全阶段 | 选项解释、影响范围分析 | 不得自动关闭 Gate |
| 复杂变更解释 | P4 | Patch 风险提示、失败原因分析 | 不得直接执行 Patch |

### 1.2 绝不适用（Fusion 不得直接用于）

```text
1. 直接批准高风险命令（L4-L5）
2. 直接执行写盘或外部系统写操作
3. 直接关闭 Gate
4. 直接标记 P 阶段 completed
5. 直接标记 P5 验证通过
6. 直接替代确定性测试
7. 直接替代回归基线对比
8. 直接替代关键行为等价证据
9. 直接替代用户授权
10. 直接替代 Policy 裁决
```

执行类任务可参考 Fusion 输出，但必须经过：Task Plan → Policy/Hook → Execution Session → Trace → 必要时 Gate/Audit → 自验证 → Acceptance → Evidence。

---

## 2. Fusion 输出格式契约

Fusion 输出应结构化，至少包含以下要素：

```text
summary              — 摘要
claims               — 明确主张（可逐条验证）
assumptions          — 假设前提
source_refs          — 输入来源引用
risk_notes           — 风险说明
confidence_note      — 置信度说明（低/中/高 + 理由）
recommended_actions  — 建议动作
evidence_needed      — 仍缺失的证据
limitations          — 已知限制
next_gate_needed     — 是否需要 Gate（true/false + 原因）
trace_ref            — 调用 Trace 引用
```

输出规则：

```text
1. 必须区分事实、推断和建议
2. 必须标记输入来源
3. 必须说明证据缺口——不能假装信息充分
4. 不得夸大确定性——低置信度必须显式标注
5. 不得把用户举例扩展为完整范围
6. 不得输出密钥或敏感信息
```

---

## 3. Fusion 与 Gate 的交互规则

Fusion 不替代 Gate。Fusion 参与 Gate 时的交互模式：

### 3.1 Fusion 可以辅助 Gate

```text
生成决策选项解释 / 汇总风险 / 对比方案 / 解释验证差异
/ 提出推荐（不自动批准）/ 帮助用户理解影响范围
```

### 3.2 必须 Gate 的情况不因 Fusion 参与而取消

```text
P 阶段晋级 / L5 高风险动作 / 外部系统写操作 / 远程资源写操作
/ 删除或不可逆操作 / Policy 冲突 / 范围变化 / Evidence 不足但请求继续
/ 用户明确要求确认
```

### 3.3 Fusion 参与 Gate 时的记录要求

```text
fusion_call_ref / selected_model / model_policy_ref
/ input_context_refs / output_ref / trace_ref
/ 用户最终 decision / audit_ref
```

### 3.4 Fusion 相关 Audit 触发条件

Fusion 参与以下情况时必须进入 Audit：

```text
1. 高风险决策辅助
2. 输出影响 Gate 选项
3. 输出影响方案选择
4. 输出影响 P5 验证解释
5. 输出影响 P6 遗留风险接受
6. 触发模型策略变更
```

---

## 4. Fusion 与确定性转换的区分

这是两种不同类型能力，容易混淆：

```text
Fusion              确定性转换
──────────────────  ──────────────────
模型能力/策略         Skill/Tool/Expert Agent/MCP
分析、比较、解释      可控转换、比对、验证
输出是"建议"         输出是"结果"
需人工判断           可自动验证
不确定/概率性        确定/可重复
```

协作原则：

```text
1. 可先用确定性转换处理可确定部分（AST/codemod/比对）
2. 再用 Fusion 分析长尾和复杂风险
3. Fusion 不替代确定性转换工具
4. 确定性转换不享有特殊流程特权
5. 二者均不绕过 Policy/Gate/Audit——输出均需 Trace
6. 进入 P5 结论时仍需可验证 Evidence（不因 Fusion 而降低标准）
```

---

## 5. Fusion 与 P0-P6 的交互速查

| 阶段 | Fusion 推荐用途 | 硬约束 |
|---|---|---|
| P0 接入 | 材料摘要、项目类型初步判断 | 不得替代接入 Evidence |
| P1 建档 | 复杂结构解释、多栈依赖摘要 | 不得替代实际文件扫描和来源记录 |
| P2 评估 | 风险分析、阻塞项整理、验证缺口说明 | 风险结论必须保留来源和 Evidence |
| P3 规划 | 方案比较、计划评审、TaskGraph 风险检查 | 不得自动批准 Stage Plan/Task Plan |
| P4 执行 | 复杂变更解释、Patch 风险提示 | 不得直接执行 Patch；须经 Execution Session |
| P5 验证 | 验证结果解释、差异说明、补充测试建议 | 不得替代构建/运行/测试/回归基线对比 |
| P6 交付 | 交付说明整理、风险摘要、证据链说明 | 不得隐藏验证失败或遗留风险 |

---

## 6. Fusion 与 TaskGraph / NodeLoop

Fusion 调用应通过 TaskGraph / NodeLoop 显式表达：

```text
Task Node 声明 fusion_capable 或 model_policy_override
→ Task Edge 声明 Fusion 输出传递方式
→ NodeLoop 调用 ModelGateway（Fusion 走 ModelGateway，不走独立通道）
→ Fusion 输出回到 NodeLoop 自验证
→ Acceptance 检查 Fusion 输出是否可用
→ TaskGraph 根据结果路由
```

Fusion 相关 Node 必须明确：输入上下文、目标、输出格式、适用模型策略、风险级别、是否需要 Gate、Artifact/Evidence 期望、Trace/Audit 要求、失败策略。

Fusion 调用失败必须进入：retry_policy → failure_policy → rework_required / gate_required / blocked。不得静默 fallback 且不记录。

---

## 7. Fusion 错误处理

Fusion 特有错误模式（与通用模型调用错误互补，后者见 01 §11）：

```text
多模型结果冲突  → 必须进入人工评审或 Gate，不得自动选"多数"
低置信度       → 不得伪装确定；显式标注并建议补充 Evidence
输出不符合 schema → 进入 retry；多次失败则 blocked
敏感信息风险   → 暂停并进入安全处理流程
```

通用规则（fallback/retry/Policy 禁止）与 01 §11 一致，不重复。

---

## 8. R13 Fusion 建设与合入验收清单

> 合入规则见 `01-ModelGateway与模型策略.md` §8.3。本文展开验收清单。

### 8.1 R13 建设目标

```text
1. Fusion 作为模型能力可登记（Model Profile fusion_capable 标签）
2. Fusion 经 ModelGateway 调用（不走独立通道）
3. Fusion 输出可追踪（Trace + Audit 完备）
4. Fusion 不绕过 Policy/Gate/Audit
5. Fusion 可在 P2/P3/P5/P6 场景辅助分析
6. Fusion 不污染 P0-P6 主流程
```

### 8.2 R13 不应做的事

```text
1. 重写主编排（LangGraph 结构不受 Fusion 影响）
2. 引入独立 Fusion Flow（不新增编排路径）
3. 将 Fusion 变成默认验收主体
4. 将 Fusion 变成自动授权主体
5. 将 Fusion 变成独立执行器
6. 要求所有阶段必须使用 Fusion
```

### 8.3 R13 合入前置检查（10 项）

```text
□ Policy      — Fusion 调用是否受 Policy 约束
□ Gate        — 高风险 Fusion 用途是否可触发 Gate
□ Trace       — Fusion 调用是否有完整 Trace
□ Audit       — 高风险 Fusion 辅助结论是否进入 Audit
□ Artifact    — Fusion 输出是否作为 Artifact 登记
□ Evidence    — Fusion 输出是否不自动成为 Evidence validated
□ 安全与许可   — 密钥脱敏、权限边界、外部模型调用许可
□ token/成本  — Fusion 调用是否纳入成本记录
□ 前端展示     — 是否明确标注"辅助分析/建议"而非"已授权/已验收"
□ 薄基础设施   — 是否未引入重型编排或自研 Provider 框架
```

---

## 9. R2/R5/R13 校准项

### R2 文档校准

```text
1. Fusion 是否仍被严格限定为模型能力（无特殊流程残留）
2. 是否误把 Fusion 输出写成 Evidence 或验收依据
3. 是否误把 Fusion 写成 Gate/Acceptance/验收主体
4. 是否仍有 Mission/mission_id 残留
5. 适用/不适用场景是否与 P0-P6 阶段契约一致
```

### R5 模型网关校准

```text
1. Fusion 如何以 Model Profile/Policy 表达（capability tags）
2. Fusion 调用是否经 ModelGateway（验证调用链）
3. Fusion Trace 字段与输出结构落地
4. Fusion fallback/retry 实现
5. Fusion token/成本记录
```

### R13 合入校准

```text
1. §8.3 合入前置检查 10 项逐项通过
2. Fusion 是否不绕过 Policy/Gate/Audit
3. Fusion 是否不替代 P5 Evidence
4. Fusion 是否不替代用户授权
5. Fusion 是否不污染 P0-P6 主流程
6. 前端是否明确标注辅助性质
```

---

## 10. Fusion 红线

```text
1. 不得把 Fusion 写成特殊 P 阶段/特殊流程/特殊评审引擎/独立运行时角色
2. 不得让 Fusion 绕过 ModelGateway（必须走统一调用链）
3. 不得让 Fusion 绕过 Policy/Gate/Audit
4. 不得让 Fusion 自动批准高风险动作
5. 不得让 Fusion 自动关闭 Gate
6. 不得让 Fusion 输出自动成为 Evidence validated
7. 不得让 Fusion 替代 P5 验证（构建/测试/回归基线/关键行为等价）
8. 不得让 Fusion 替代用户授权
9. 不得因 Fusion 引入重型编排或自研 Provider 框架（违反 D-065）
10. 不得泄露 Key/Token/Secret/Password
11. 不得引入 Mission 产品层
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 11. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 Fusion 适用场景矩阵（7 推荐 + 10 绝不适用）——§1
2. 明确 Fusion 输出格式契约（11 要素 + 6 输出规则）——§2
3. 明确 Fusion 与 Gate 的交互规则（辅助模式 + 不取消清单 + 记录要求 + Audit 触发）——§3
4. 明确 Fusion 与确定性转换的区分（对照表 + 协作原则）——§4
5. 明确 Fusion 与 P0-P6 的交互速查（7 阶段表）——§5
6. 明确 Fusion 与 TaskGraph/NodeLoop 的集成模式——§6
7. 明确 Fusion 特有错误处理（4 种 + 通用规则引用 01）——§7
8. 明确 R13 建设目标/不应做/合入前置检查 10 项——§8
9. 明确 R2/R5/R13 校准项——§9
10. 明确 Fusion 红线 11 条——§10
11. 未重复 01 §8 的定位定义/调用规则/合入规则
12. 未固化最终 API/DB schema
13. 未引入 Mission 产品层
14. 未新增产品决策
```
