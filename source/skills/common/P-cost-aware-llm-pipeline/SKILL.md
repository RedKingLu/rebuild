---
name: P-cost-aware-llm-pipeline
description: 跨阶段 — 大规模迁移的 LLM 成本优化：按复杂度路由模型、预算追踪、仅瞬时错误重试、缓存
metadata:
  series: P
  phase: cross
  category: resource_skill
  status: platform_runtime
  source: ECC skills/cost-aware-llm-pipeline (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-cost-aware-llm-pipeline（成本感知模型流水线）

## 适用阶段与触发条件
- 阶段：跨阶段（P0–P6）。大规模迁移调用模型频繁（批量 SQL 转换、代码改写、文档生成）。
- 触发：设计批处理流水线、单 run 调用量大、预算预警、成本/质量需要权衡时。
- 不适用：单次低频调用、对成本不敏感的一次性分析。

## 输入
- 任务清单与各任务复杂度画像（机械转换 vs 复杂语义判断）。
- 可用模型档位及其能力/相对成本（经 ModelGateway 暴露的路由策略）。
- 预算上限（按 Project/Stage/Run）与当前已用量。
- 缓存可用性（prompt caching / 结果缓存键）。

## 执行步骤
1. **任务分级**：按复杂度分档——机械批量转换（低）→ 结构化改写（中）→ 复杂兼容性判断/架构决策（高）。
2. **模型路由**：低复杂度走便宜模型，高复杂度走强模型；路由规则交 ModelGateway 执行，不在 Skill 内硬编码 Key。
3. **预算追踪**：以不可变累加方式记录每次调用 token/用量，按维度聚合；接近上限触发预警。
4. **重试策略**：仅对瞬时错误（超时、429、网络抖动）退避重试；对内容/逻辑错误不重试（避免重复计费）。
5. **缓存命中**：相同 prompt/上下文走 prompt caching 与结果缓存，降低重复转换成本。
6. **预算 Gate**：突破预算阈值的继续执行需用户 Gate 确认，不静默超支。

## 输出 / 产物（Artifact / Evidence）
- 路由与成本报告（Artifact）：各任务所用模型档位、调用次数、token、聚合用量、缓存命中率。
- 优化建议（Artifact）：可下沉到便宜模型的任务、可缓存项、可合并的调用。
- 证据（Evidence）：ModelGateway 实测用量；无计价数据时诚实标记「计价不可用」，仅报 token 不报金额。
- 写入 Trace/Audit 并登记 Registry。

## 质量门 / 验收标准
- 用量为 ModelGateway 真实计量；金额仅在有计价时给出，否则标「不可用」。
- 模型路由不以牺牲正确性为代价（高风险判断不得为省钱降档）。
- 仅瞬时错误重试，逻辑错误不重试。
- 预算超限不静默继续，须经 Gate。

## 场景要点（按项目场景取用）
- 批量 SQL 方言转换、注释/日志翻译等机械任务优先低档模型，留强模型给存储过程/触发器等复杂语义改写。
- 国产栈兼容性判断（达梦/openGauss/GaussDB 特性差异）属高复杂度，不降档。
- 内网/离线部署若模型可用档位有限，路由策略据实调整并声明约束。
- 大库迁移与 P-context-budget 协同：先瘦身上下文再调用，双向降本。

## 反例 / 禁止
- 禁止为省成本把高风险兼容性判断交给弱模型。
- 禁止在 Skill 代码/配置中存储或打印 Key/Token/Secret。
- 禁止对逻辑/内容错误重试（重复计费且无改善）。
- 禁止伪造金额或在无计价数据时编造成本数字。
- 禁止静默超预算继续执行。

## 与平台集成
- 所有调用与路由经 ModelGateway 统一计量与裁决，Skill 不直连模型/不持有 Key。
- 用量 → Trace/Audit + Registry，并作为全局预算视图输入。
- 超预算继续走 HITL Gate。
- 与 P-context-budget 形成「上下文瘦身 + 模型路由」的成本双控。

## 参考
- ECC skills/cost-aware-llm-pipeline（MIT, https://github.com/affaan-m/ECC）
- 平台 ModelGateway 用量与计费口径
- P-context-budget（上下文侧协同）
