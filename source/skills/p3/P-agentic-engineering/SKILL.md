---
name: P-agentic-engineering
description: P3规划 — 用评估优先思路把迁移任务拆成可验证小单元，设计 TaskGraph 并按复杂度路由模型
metadata:
  series: P
  phase: P3
  category: planning_skill
  status: platform_runtime
  source: ECC skills/agentic-engineering (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-agentic-engineering（评估优先的迁移工程规划）

## 适用阶段与触发条件
- 阶段：P3 规划。前置 P2 评估已产出资产清单、依赖图、风险分级。
- 触发：需要把"把这个系统迁到信创"的大目标，转化为可调度、可验收、可估算成本的执行计划（TaskGraph）。
- 不适用：P4 已在执行的具体改造（用执行类 skill）；纯环境准备脚本编写。

## 输入
- P2 产物：资产清单（.NET/Java 模块、Oracle/MSSQL 对象、中间件配置）、依赖图、风险分级。
- 目标基线：源端→信创目标矩阵（达梦DM/openGauss/GaussDB、麒麟/统信 OS、东方通/宝兰德）。
- 约束：交付窗口、停机容忍度、可用模型清单与成本档位（经 ModelGateway）。

## 执行步骤
1. **先定验收，后定动作（eval-first）**：对每个待迁移目标，先写"完成判据"——可执行的检查（行数一致、校验和一致、接口契约回归通过、服务在麒麟启动），再倒推动作。无可验证判据的任务不进 TaskGraph。
2. **拆到 ~15 分钟可验证单元**：把模块拆为细到"单个存储过程转换""单张表数据迁移""单个 WCF 端点改 ASP.NET Core"的单元，每单元有明确输入/输出/验收，可独立回滚。
3. **构建 TaskGraph 与依赖边**：单元间用 DAG 表达（schema 先于数据；公共库先于调用方；连接池配置先于压测）。标注关键路径与可并行批次。
4. **按复杂度路由模型档位**：机械确定性转换（类型映射、方言重写、codemod）优先用确定性工具而非模型；需要理解语义的（存储过程逻辑改写、遗留架构识别）才升档模型。先用便宜档位试，必要时再升能力档；所有调用经 ModelGateway，记录档位与用途。
5. **设定基线与度量**：为每单元记录 baseline（当前耗时/缺陷数/性能/源库行数），执行后对比，量化收益与回归，不靠主观判断。
6. **风险与回滚预案**：高风险单元（数据迁移、生产 schema 变更）标注 Gate 点与回滚脚本，禁止无回滚单元上生产路径。
7. **产出计划评审包**：TaskGraph + 每单元验收 + 成本/模型档位估算，提交评审。

## 输出 / 产物（Artifact / Evidence）
- TaskGraph（DAG，节点=可验证单元，边=依赖）— Artifact。
- 每单元《验收判据卡》（输入/输出/检查命令/回滚方式）— Artifact。
- 模型路由与成本估算表（档位、预估调用量、确定性工具占比）— Artifact。
- 基线度量表（各单元 baseline 实测值）— Evidence（实测值，非模型输出）。

## 质量门 / 验收标准
- 每个节点都有可执行的验收判据，否则不予排程。
- 关键路径与并行批次明确，无悬空依赖、无环。
- 高风险节点均有 Gate 点与回滚预案。
- 确定性转换优先原则已落实（机械转换未滥用高档位模型）。

## 场景要点（按项目场景取用）
- Oracle/MSSQL → 达梦/openGauss/GaussDB 的类型映射、方言差异属机械转换，应规划为确定性 codemod 单元，仅边界 case 升档模型。
- .NET Framework/Java legacy 的架构识别（WCF/WebForms/EJB）需语义理解，单独成高档位单元。
- 验收判据须含"在麒麟/统信 OS 上启动并通过冒烟"，不能只验本地开发机。
- 数据迁移单元的 baseline 必须含源库行数与校验和，作为后续比对依据。

## 反例 / 禁止
- 禁止把"迁移 X 系统"作为单个不可拆的任务进图。
- 禁止无验收判据的单元进 TaskGraph。
- 禁止对纯机械转换默认用高档位模型（成本不可控且结果不确定）。
- 禁止把模型输出当作验收 Evidence。
- 禁止规划出无回滚路径的生产变更单元。

## 与平台集成
- 所有模型调用经 ModelGateway，不直连 Provider；记录档位与用途到 Trace。
- TaskGraph/验收卡/估算表挂到 Project/Run/Stage，登记 Registry，产生 Trace/Audit。
- 高风险节点的 Gate 点在 P4 执行时触发 HITL；规划阶段只标注，不代替执行期 Gate。
- 本 skill 的自评估不替代 D-级 Acceptance；规划包须经评审通过方可进 P4。

## 参考
- ECC skills/agentic-engineering（MIT）— eval-first、~15min 可验证单元、成本感知模型路由、对比 baseline。
- 文档/04-模型与资源/06-确定性转换资源接入规范.md（机械转换优先）。
- 03-Agent与Skill规范 §6/§7（ModelGateway/Gate/Trace 约束）。
