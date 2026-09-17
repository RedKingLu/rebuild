---
name: P-migration-assessment
description: P2 评估阶段 — 基于 P0/P1 阶段完成包与原始验收基准，评估信创迁移/重构的可行性、风险、阻塞、验证缺口与资源需求，产出证据化评估（不预判终局、不选定单一目标库）
metadata:
  series: P
  phase: P2
  category: stage_skill
  status: platform_runtime
  source: rebuild R17.5（吸收 ECC blueprint / architecture-decision-records 语境 + 参考轨 P2-1~P2-8）
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-migration-assessment（迁移评估工作流）

## 适用阶段与触发条件

- 阶段：P2 评估。承 P0 接入识别 + P1 建档（技术栈/依赖/入口/配置/测试 + 原始验收基准）之后。
- 触发：P1 建档完成并通过 P1→P2 Gate，进入迁移/重构可行性与风险评估。
- 目标：基于真实上游证据，输出可信的迁移评估，为 P3 规划与 P3→P4 用户 Gate 提供依据。**评估是辅助分析，不是事实，不做代码修改，不替代 P3 规划或 P5 验证。**

## 输入（清单驱动按需加载，D-107）

- **P0/P1 阶段完成包**（`artifacts/{stage}/_stage_package.json`）：读前序各阶段完成包清单，据 `key_for_next` 标记与产物描述**按需加载**关键产物内容，而非写死文件名列表。
- P1 关键产物：`acceptance_baseline`（原始验收基准 D-106，静态基线 + 动态黄金/needs_env）、`tech_stack`、`dependency_draft`、`entry_points`、`config_inventory`、`uncertainty_manifest`、`profiling_summary`。
- P0 关键产物：`intake_report`（接入识别）、`source_index`（含 `database_files`：每个 .sql 的方言/编码/CREATE TABLE·INSERT 计数/IDENTITY·GETDATE·[dbo].schema·外键计数等**确定性方言采集**）。
- 目标运行环境（Environment Profile，`.rebuild/environment.json`）：目标 CPU 架构/OS/DB/中间件约束。
- 用户迁移目标/意图（migration_target / user_goal）。

## 执行步骤

围绕以下 **8 个评估维度**展开（吸收参考轨 P2-1~P2-8）。每一维度都基于上游真实证据推理，无据则诚实标 evidence_gap，**不臆测、不预判终局**：

1. **兼容承载可行性（compatibility_hosting）**：源栈在目标信创环境（麒麟/统信 + 国产中间件）能否承载运行。当前/本地不可实测项标 evidence_gap，不写"大概率不可行"当终局裁决。
2. **现代化重构可行性（modernization）**：是否需要/值得重构（如 .NET Framework WebForms/WCF → .NET Core/ASP.NET Core），工作量与风险量级。
3. **数据库迁移（database_migration）**：结合 `source_index.database_files` 的方言采集证据（IDENTITY/GETDATE/[dbo].schema/NVARCHAR/AUTO_INCREMENT/外键计数/编码等）评估方言差异与迁移难度。对目标库候选（如达梦 Oracle 兼容 vs 人大金仓 PostgreSQL 兼容 vs openGauss/GaussDB）做**证据化对比**，产出 `adr_candidates`（每项含 title/options/tradeoffs/recommendation_basis）。**P2 不选定单一目标库——最终选型留 P3 用户 Gate 裁决。** 目标库 DDL/DML 实测不可在此完成的标 evidence_gap。
4. **部署与中间件（deployment_hosting）**：IIS→国产 Web 容器、WebLogic→TongWeb、WebSphere→宝兰德等替换路径与版本差异；离线/内网部署约束。
5. **PoC 范围建议（poc_scope）**：建议先行验证的最小切片（高风险/高不确定优先），为 P3 试点规划提供输入。
6. **阻塞项与验证缺口（blockers + validation_gaps）**：登记阻断迁移推进的硬阻塞；验证缺口须**引用 P1 `acceptance_baseline` 的真实基线**——静态基线（测试断言/特征化规格）+ 动态黄金（若为 needs_env 则登记为需环境实测的 evidence_gap，而非断言"无验证证据"）。
7. **资源需求（resource_needs）**：迁移所需工具/专家/环境/知识（如确定性转换工具、国产库驱动、兼容性矩阵文档）。
8. **用户待确认项（questions_for_user）**：需用户裁决的目标栈选型、路线取舍、风险接受项。

## 输出 / 产物（Artifact / Evidence）

结构化 JSON，锚点字段（供机器解析）：
`assessment_report`（对象，覆盖上述 8 维度对应字段：compatibility_hosting / modernization / database_migration[含 adr_candidates] / deployment_hosting / poc_scope / questions_for_user）、`risk_list`（每项 title/risk_level[L0-L5]/source/basis/evidence_refs）、`blocker_list`、`uncertainty_list`、`validation_gap_list`、`resource_needs`。

- 内联引用（硬约束）：每条 risk/blocker/validation_gap 在其 `evidence_refs` 内联列出所依据的上游产物 artifact ref，只能引用【可引用上游产物】清单给出的 ref，不得杜撰；确无可依据时给空数组（诚实）。
- 产物写入 `artifacts/p2/` 并产出 `artifacts/p2/_stage_package.json` 完成包清单（供 P3 按需加载，D-107）。
- 模型输出统一标记 `analysis_only`（辅助分析，非 Evidence 本身）。

## 质量门 / 验收标准

- 8 维度均有覆盖或诚实的 evidence_gap，无凭空断言。
- validation_gap 评估引用了 P1 acceptance_baseline 真实基线（不再误报"P1 无验证证据/No build/test validation evidence from P1"）。
- DB 迁移给出证据化目标库对比 + ADR 候选，且**未在 P2 选定单一库**。
- 每条 risk/blocker/gap 的 evidence_refs 指向真实存在的上游产物 ref。
- 无有效模型 Key 时诚实 blocked，不降级为规则评估。

## 场景要点（按项目场景取用）

- 目标环境（麒麟/统信 + 达梦/人大金仓/openGauss/GaussDB + TongWeb/宝兰德）差异大，训练数据稀缺——不确定一律进 uncertainty/evidence_gap，勿臆测国产组件行为。
- 兼容承载与目标库 DDL/DML 的实测往往需真实信创环境，本阶段标 evidence_gap，留 P3/PoC 实测，**不把"不可实测"写成"不可行"终局裁决**。
- 目标库选型是高影响决策 → 证据化对比 + ADR 候选，裁决权留用户 P3 Gate（P2 只给建议依据）。
- P2→P3 路线风险接受由 Audit `risk_acceptance` 承载（用户批准 P2→P3 晋级 Gate 即记录接受迁移路线风险），非新建 gate_type。

## 反例 / 禁止

- 禁止把"大概率不可行 / 大概率可行"写成终局裁决——不可实测即 evidence_gap。
- 禁止在 P2 选定单一目标数据库（越权 P3 用户裁决）。
- 禁止杜撰 evidence_refs / artifact id；无据给空数组。
- 禁止把模型输出当作 Evidence 或事实（analysis_only）。
- 禁止在评估中修改源代码或替代 P3 规划 / P5 验证。
- 禁止在报告/日志中记录明文数据库密码、连接凭据、Key/Token/Secret。

## 与平台集成

- 所有模型调用经 ModelGateway（D-098），不直连 Provider、不硬编码模型名/endpoint。
- 输入按 D-107 清单驱动按需加载前序阶段完成包，不写死文件名列表（AGENTS §2.3：Agent 决定读什么）。
- DB 方言证据来源 = P0 `source_index.database_files` 的确定性只读采集（确定性只限采集与验证，识别/研判交本阶段 LLM）。
- 产物挂 Project/Run/Stage，附 Trace/Audit；评估结果落 Evidence（D-066，No Evidence No Completed）。
- 本 Skill 自检不替代独立 Acceptance Agent 验收（D-082）。

## 参考

- ECC `skills/blueprint`、`skills/architecture-decision-records`（MIT, https://github.com/affaan-m/ECC）
- rebuild：D-106（原始验收基准）、D-107（产物分文件夹 + 阶段完成包 + 按需加载）、D-108（需求入 skill 非硬编码提示词）、AGENTS §2.3、`文档/03-流程与运行时/01-P0-P6阶段契约.md` §4
