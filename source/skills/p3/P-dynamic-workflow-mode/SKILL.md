---
name: P-dynamic-workflow-mode
description: P3规划 — 按项目特征动态选择执行模式(Manual/Plan/Auto)并对齐平台 run-state 与 HITL gate
metadata:
  series: P
  phase: P3
  category: planning_skill
  status: platform_runtime
  source: ECC skills/dynamic-workflow-mode (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-dynamic-workflow-mode（动态执行模式与控制面规划）

## 适用阶段与触发条件
- 阶段：P3 规划。在 TaskGraph 已成形后，为整体与各批次选定执行模式与控制面 checkpoint。
- 触发：需要决定"哪些单元让 Agent 自动跑、哪些必须人确认、哪些手动逐步"，并把控制点落到平台 run-state。
- 不适用：模式已定、执行中的临时干预（执行期由 HITL gate 处理）。

## 输入
- TaskGraph 与每节点的风险等级、动作风险级别（L1-L5）。
- 平台三执行模式定义：Manual（逐步人控）/ Plan（先出计划待确认再执行）/ Auto（自动推进，遇 Gate 暂停）。
- 停机窗口、回滚成本、数据敏感度、是否触及生产库/生产中间件。

## 执行步骤
1. **为每批次设计任务局部 harness**：明确该批次的输入夹具、可运行的 eval gate（迁移后自验脚本）、以及执行边界，使批次可独立验证、失败不外溢。
2. **按风险选模式**：低风险机械批次（类型映射 codemod、纯只读分析）→ Auto；中风险（schema 变更、接口改写）→ Plan，计划经确认再执行；高风险（生产数据迁移、不可逆操作、L4-L5 动作）→ Manual + 强制 Gate。
3. **定义控制面 checkpoint**：把流程拆为 plan→queue→run→gate→handoff 五点，逐节点标注：何处生成计划、何处入队、何处实际执行、何处暂停等人、何处交接产物给下游节点/下一阶段。
4. **对齐平台 run-state**：把 checkpoint 映射到平台 run 状态机（如 planning/queued/running/awaiting_gate/handoff/done），保证控制面与平台状态一致，可观测、可恢复（HITL resume）。
5. **定义模式切换条件**：声明何种信号触发降级到更严模式（如自验失败率超阈、出现 L4 动作、源库写风险），避免执行中无依据切换。
6. **可复用 skill 抽取**：批次中反复出现的稳定动作（如某类存储过程转换、某 ORM 映射改写）抽成可复用 skill 引用，减少重复设计。
7. **产出执行模式编排表**，提交评审。

## 输出 / 产物（Artifact / Evidence）
- 执行模式编排表（每批次：模式 + 理由 + 切换条件）— Artifact。
- 控制面 checkpoint 图（plan/queue/run/gate/handoff 对齐 run-state）— Artifact。
- 任务局部 harness 定义（夹具 + eval gate）— Artifact。
- 可复用 skill 引用清单 — Artifact。

## 质量门 / 验收标准
- 每批次都有明确模式与选模式理由，且与风险等级一致。
- 所有 L4-L5 / 生产写 / 不可逆动作均落在 Manual 且有 Gate 点。
- checkpoint 与平台 run-state 一一映射，支持暂停/恢复。
- 模式切换条件可判定（有阈值或明确信号），非主观。

## 场景要点（按项目场景取用）
- 触及生产 Oracle/MSSQL 源库或目标 达梦/GaussDB 写入的批次一律 Manual + Gate，禁止 Auto。
- 涉及麒麟/统信 OS 上中间件（东方通/宝兰德）重配置、服务重启的动作通常 ≥L4，强制 Plan/Manual。
- 纯只读评估、确定性方言转换可 Auto，提升吞吐。
- handoff 点须明确产物契约（如 schema 转换产物交付给数据迁移批次），避免阶段间断链。

## 反例 / 禁止
- 禁止对生产数据迁移或不可逆动作选 Auto。
- 禁止无切换条件的"可随时切换"模糊声明。
- 禁止 checkpoint 与平台 run-state 脱节（导致不可恢复）。
- 禁止任何模式绕过 L4-L5 用户 Gate。

## 与平台集成
- 模式编排不改变平台 Gate 规则：无论何模式，L4-L5 / 外部写均触发用户 Gate（HITL）。
- checkpoint 映射平台 run-state，gate 点接 HITL resume 机制；编排表挂 Project/Run/Stage，产生 Trace/Audit、登记 Registry。
- 任何模型调用（如风险评估辅助）经 ModelGateway，不直连 Provider；模型建议非 Evidence。
- 本 skill 输出为规划决策，不替代 P4 执行期 Gate 与 D-级 Acceptance。

## 参考
- ECC skills/dynamic-workflow-mode（MIT）— 任务局部 harness + eval gate、可复用 skill 抽取、控制面 checkpoint。
- 平台三执行模式规范（Manual/Plan/Auto）与 P121 HITL 契约（gate.request/resolved/escalate、resume API）。
- 03-Agent与Skill规范 §6/§7（ModelGateway/Gate/Trace 约束）。
