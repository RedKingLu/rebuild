---
name: P-migration-planning
description: P3 规划阶段 — 基于 P2 评估产出与用户 Gate 裁决，把迁移/重构转化为可执行、可授权、可验证、可追踪的 Stage Plan + Task Plan Batch + TaskGraph；条件化于 P2 用户裁决（未裁决=草案 contingent，不硬跑 P4）；PoC 先行（最小可验证闭环优先，显式声明非 Production）；每个高风险任务必备回退策略；验证计划映射既有 P5 结构、不臆造 P5 slot；只产固定 3 产物、不发明 artifact/Gate；不以计划替代执行/验证
metadata:
  series: P
  phase: P3
  category: stage_skill
  status: platform_runtime
  source: rebuild R17.5（D-108；吸收参考轨 2A/2B P3 + ECC agentic-engineering/dynamic-workflow-mode 语境）
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-migration-planning（迁移规划工作流）

## 适用阶段与触发条件
- 阶段：P3 规划。承 P2 评估（8 维度/风险/阻塞/验证缺口/资源/待确认 + adr_candidates）之后。
- 触发：P2 completed 或有等价评估 Evidence，且用户已授权进入 P3。
- 目标：把 P2 评估转化为可执行、可授权、可验证、可追踪的方案 + Stage Plan + Task Plan Batch + TaskGraph，为 P3→P4 用户 Gate 与 P4 执行提供可加载的任务图。**规划是设计，不是执行；不修改代码、不替代 P4 执行或 P5 验证。**

## 输入（清单驱动按需加载，D-107）
- P2 阶段完成包 `artifacts/p2/_stage_package.json`：据 key_for_next + 描述按需加载关键产物，而非写死文件名列表。
- P2 关键产物：assessment_report（8 维度 + adr_candidates）、risk_list、blocker_list、validation_gaps、resource_needs、questions_for_user。
- P0/P1 底座（按需）：source_index（DB 方言/文件面）、tech_stack、entry_points、acceptance_baseline（P5 验证策略依据）。
- 目标运行环境（Environment Profile）、用户迁移目标、以及**用户 Gate 裁决/风险接受记录**（Audit risk_acceptance）。

## 执行步骤（P3-1~P3-9，汇入固定 3 产物）
1. Stage Plan：迁移阶段拆分 + 目标/范围/out_of_scope + Gate 计划 + 完成条件 + P5 验证策略。方案必须来源于 P2 评估（basis_refs 内联引用上游 artifact ref）。
2. Task Plan Batch：按模块/层拆工作包，每包全字段——输入/动作/输出/负责 Agent 类型/验收标准/所需工具/风险等级/回退方案/证据计划。任务不得超出 Stage Plan scope，须可追溯到 Stage Plan。**`inputs` 必须给 P4 可解析的源定位**——真实 `source/…` 路径或源根引用（如 `source/`，供 P4 按需读取），**而非散文**（禁止如「源.aspx页面源码（从p0接入获取）」这类无法解析到文件的自然语言，P4 解析不到 `source/` 即被迫照标题臆造）。源定位以 P0/P1 采集的 `source_structure.json`（真实顶层目录/文件清单）为准按需选取，不臆测不存在的路径；无法定位到具体件时至少给源根 `source/` 让 P4 自行按需读取。**每包还须给 `output_target`（目标工程相对写入路径，见步骤 6 脚手架先行）——`inputs` 是源侧、`output_target` 是目标侧。**
3. TaskGraph：节点 + 依赖边（EDGE_TYPES：sequence/parallel/conditional/failure/retry/rework/merge），DAG 无环，作为 P4 加载源。只表达任务依赖，不得让图越过用户 Gate。**每个节点 = 绑定真实源文件/源根的 P4 迁移工作包**（可回溯到 `source/` 下真实件），节点 input_refs 承接对应 Task Plan 的可解析源定位；**禁止把 P3 规划过程自身建成节点**——「目标栈选型 / 画 TaskGraph 图 / 定 Task 规格 / 编纂三产物」等是 P3 自己的规划动作、不是 P4 执行工作包，一律不得作为 TaskGraph 节点。
4. PoC 优先级：将 P2 的 PoC 建议转为最小可验证闭环的优先任务；显式声明"这是 PoC 非 Production"，不伪装全量已迁。
5. 数据库迁移计划：基于 P2 方言证据规划 DDL/DML/编码/完整性/种子/回退——**在 P3 只规划不生成转换脚本**。
6. 应用/配置/部署计划：目标工程/页面·接口/数据访问/认证/配置外置/部署承载的迁移任务规划。**脚手架先行（单一可构建目标工程，R17.3-1 §8 P4-4/P4-10）**：第一个工作包应为「工程脚手架/初始化」节点——建**单一目标工程根** `output_code/{目标工程名}/`（项目文件 + 程序入口 + 配置，统一工程名与命名空间，由 P1 tech_selection 目标框架推导）；其 `output_target` 指向工程根 `output_code/{目标工程名}/`。后续每个工作包的 `output_target` 是**同一工程内的相对子路径**（如 `output_code/{目标工程名}/Services/`、`.../Data/`、`.../Pages/`、DB 脚本 `output_code/db/`、部署 `output_code/deploy/`），用 `sequence` 边表达「脚手架→填充」先后。**全批次共用同一目标工程——严禁每个任务各自建工程/各自 Program.cs/各自 .sln，那会产出无法构建的碎片。**
7. 验证计划：定义 P5 验证输入，**映射现有 P5 report 可承载结构，不臆造新 P5 slot**；环境不足项标 evidence_gap，不伪造。
8. 回退策略：每个高风险 P4 工作包必须有回退方案（源只读 D-099，回退=丢弃 output_code 对应产物）。
9. 验收标准 + P3→P4 Gate：三产物齐全、TaskGraph 可解释、P4 输入包清晰、待裁决项经 Gate/Audit 承载。

## 输出 / 产物（固定 3 项，不发明）
- `p3_stage_plan.json`（锚点字段：objective/scope/out_of_scope/risk_level/permission_boundary/expected_artifacts/expected_evidence/gate_policy/completion_criteria/validation_strategy/basis_refs）
- `p3_task_plans.json`（batch + task_plans[每项全字段 + basis_refs]）
- `p3_task_graph.json`（nodes + edges[EDGE_TYPES] + DAG；P4 加载源，version→created_at desc）
- 写入 `artifacts/p3/` 并产 `artifacts/p3/_stage_package.json` 完成包（供 P4 按需加载）。
- 模型输出为计划草案（plan_status=draft），非 Evidence 本身；待 P3→P4 用户 Gate 裁决，不自批。

## 质量门 / 验收标准
- 方案来源于 P2（basis_refs 指向真实存在的上游 artifact ref，禁杜撰；无据给空数组）。
- Task Plan 每项全字段（含回退/验收/证据计划）；高风险(L4/L5)任务显式标识。
- TaskGraph 可解释、DAG 无环、边策略显式、是 P4 可加载源。
- TaskGraph 节点均为**绑定真实源的 P4 迁移工作包**（input_refs 含可解析 `source/…` 路径或源根引用），**无「把 P3 规划过程当节点」的元步骤**（选型/画图/定规格/编纂产物不作为执行节点）。
- **脚手架先行且单一工程**：首个工作包建单一目标工程根 `output_code/{目标工程名}/`；各工作包 `output_target` 均为该工程内相对子路径，共用统一工程名/命名空间；无「每任务各自建工程/多 Program.cs/多 .sln」的碎片化。
- 条件化于 P2 用户裁决：目标库/运行时/PoC 范围未裁决时，计划标 contingent，**不硬跑 P4**。
- 验证计划映射 P5 既有结构，不臆造 slot。
- 无有效模型 Key 时诚实 blocked，不降级为规则/模板规划。

## 场景要点（按项目场景取用）
- 目标库选型/路线是高影响决策 → 承接 P2 的 adr_candidates + 用户 P3 Gate 裁决落定，P3 不越权预判终局。
- 国产环境（麒麟/统信 + 达梦/人大金仓/openGauss + TongWeb/宝兰德）不确定项进风险/evidence_gap，勿臆测。
- P2→P3→P4 路线风险接受由 Audit risk_acceptance 承载，复用 stage_promotion，不新增 gate_type。

## 反例 / 禁止（一票否决）
- 禁止发明 P3 artifact（route_decision/db_script/deploy_files/poc_output 等）——只产固定 3 项。
- 禁止把 Task Plan `inputs` / TaskGraph 节点 input_refs 写成无法解析的散文——必须给 P4 可解析的 `source/…` 路径或源根引用。
- **禁止碎片化产出**：禁止每个任务各自建独立工程（各自 Program.cs / .csproj / .sln）——全批次共用单一目标工程 `output_code/{目标工程名}/`，各任务 `output_target` 为其内相对子路径。缺 `output_target` 会使 P4 回退到 per-node 隔离目录、产出无法构建的碎片。
- 禁止把 P3 规划过程步骤（目标栈选型 / 画 TaskGraph 图 / 定 Task 规格 / 编纂三产物）当作 TaskGraph 执行节点——节点须是绑定真实源的 P4 迁移工作包。
- 禁止在 P3 生成真实代码/补丁/DB 转换脚本/部署文件（那是 P4）。
- 禁止无 P2 输入即规划；禁止路线未裁决却伪装已裁决。
- 禁止 TaskGraph 越过用户 Gate；禁止无回退策略的高风险任务。
- 禁止为 P5 臆造 slot；禁止把计划显示/记录为已执行或 accepted。
- 禁止杜撰 basis_refs；禁止把模型输出当 Evidence；禁止记录明文凭据/Key。

## 与平台集成
- 模型调用经 ModelGateway（D-098），不硬编码模型名/endpoint。
- 输入按 D-107 清单驱动按需加载，不写死文件名列表（§2.3：Agent 决定读什么）。
- 产物挂 Project/Run/Stage，附 Trace/Audit；计划落 draft，Evidence 落库（D-066）。
- 本 Skill 自检不替代独立 Acceptance Agent 验收（D-082）。
- 次级能力 skill：P-agentic-engineering（任务分解/TaskGraph 设计/复杂度路由）、P-dynamic-workflow-mode（执行模式选择）可被本阶段调用。

## 参考
- 参考轨 2A（Copilot）/2B（Claude Code）P3 基线；ECC agentic-engineering / dynamic-workflow-mode（MIT）
- rebuild：契约 §5；D-107（分文件夹+完成包+按需加载）；D-108（需求入 skill）；AGENTS §2.3
