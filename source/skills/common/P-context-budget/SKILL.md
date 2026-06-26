---
name: P-context-budget
description: 跨阶段 — 审计各 Agent/Skill/MCP/Rule 的上下文窗口消耗并按需加载，防 token 超限
metadata:
  series: P
  phase: cross
  category: resource_skill
  status: platform_runtime
  source: ECC skills/context-budget (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---

# P-context-budget（上下文预算控制）

## 适用阶段与触发条件
- 阶段：跨阶段（P0–P6）。大型迁移项目上下文极大——遗留代码 + 设计文档 + 迁移规范 + 多 Agent 交接。
- 触发：长任务推进中上下文接近上限、Agent 切换前后、加载社区 Skill/MCP/Rule 前、出现 token 超限或截断风险。
- 不适用：单步短任务、上下文充裕且无超限风险的场景。

## 输入
- 当前上下文构成：系统提示、Agent 定义、已加载 Skill、MCP 工具描述、Rule、历史对话、引用的代码/文档。
- 各组成的 token 估算与剩余预算（来自 ModelGateway 用量）。
- 任务所需的「必须保留」清单：当前决策、Gate 记录、红线、关键交接信息。

## 执行步骤
1. **盘点消耗**：按来源（agents / skills / MCP / rules / 历史 / 代码引用）统计 token 占用，从大到小排序。
2. **分类频次**：标注每项为 always-needed（恒需）/ sometimes-needed（按需）/ rarely-needed（罕用）。
3. **按需加载**：rarely-needed 项改为按需检索（引用而非内联）；sometimes-needed 延迟到触发时加载。
4. **保护核心**：always-needed（C0–C2 基座、关键决策、Gate、红线）永不裁剪。
5. **生成节省建议**：给出可裁剪项与预计 token 节省量，附裁剪后对任务的影响评估。
6. **回填用量**：将实测 token 占用与节省效果反馈 ModelGateway，更新预算视图。

## 输出 / 产物（Artifact / Evidence）
- 上下文审计表（Artifact）：各来源 token 占用、频次分类、是否可裁剪。
- 节省建议（Artifact）：裁剪/延迟加载项清单 + 预计节省 + 风险说明。
- 证据（Evidence）：实测 token 计量值（来自 ModelGateway），非估算；估算值显式标「估算」。
- 写入 Trace/Audit 并登记 Registry。

## 质量门 / 验收标准
- token 占用以 ModelGateway 实测为准；无实测时标注「估算」，不冒充实测。
- 恒需项（基座/决策/Gate/红线）在任何裁剪建议中均被保留。
- 每条裁剪建议附影响评估，不盲目压缩。
- 审计后上下文低于配置预算上限，或给出无法达成时的明确说明。

## 信创迁移要点
- 遗留代码体量大（.NET/Java/Oracle/MSSQL 工程动辄数十万行），整库内联必爆预算——须按文件/符号按需检索。
- 迁移规范与映射表（SQL 方言映射、API 兼容矩阵）属 sometimes-needed，按当前任务分片加载。
- 多 Agent 交接信息（P0→P6）须保留关键决策摘要，丢弃逐字历史。
- 国产栈专项知识库按目标栈裁剪，只加载本项目相关条目。

## 反例 / 禁止
- 禁止把整个遗留代码库/全部文档一次性塞入上下文。
- 禁止裁剪 Gate 记录、红线、关键决策以「省 token」。
- 禁止用估算 token 值冒充 ModelGateway 实测。
- 禁止为压缩而丢弃后续 Agent 交接所需的必要信息。

## 与平台集成
- token/用量来源与回填均经 ModelGateway，作为预算依据。
- 与 P-cost-aware-llm-pipeline 协同：上下文瘦身直接降低单次调用成本。
- 审计结果 → Artifact/Evidence + Trace/Audit + Registry。
- 社区 Skill/MCP 默认只读，加载前先评估其上下文占用。

## 参考
- ECC skills/context-budget（MIT, https://github.com/affaan-m/ECC）
- 平台 `04-模型与资源/07-上下文与记忆策略.md`
- P-cost-aware-llm-pipeline（成本侧协同）
