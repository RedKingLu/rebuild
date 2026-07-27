---
name: P-migration-verification
description: P5 验证阶段 — 验证迁移结果的正确性（编译/运行/测试/等价），回答"改对了吗"，产单一验证报告 p5_validation_report.json + Evidence；给 P4 产物+源→按需读真实产物→规划本次该验哪些维度→以确定性工具产铁证（编译器/测试框架/diff/DB 查询/浏览器）→解读失败原因+提回 P4 rework 的修复建议；确定性工具给"事实"、LLM 只给"是否达标/为何失败/怎么修"的判断，绝不产"通过"结论替代真实测试；无证据只标 evidence_gap/未通过、绝不伪造 completed（No Evidence No Completed）；不重构造（那是 P4）、不编辑源码/输出码（失败只回 P4）、不做交付打包（那是 P6）；只产固定单产物、不发明第二产物/新槽位/新 Gate
metadata:
  series: P
  phase: P5
  category: stage_skill
  status: platform_runtime
  source: rebuild R17.5-P5-R1（D-108；GAP-P5-2 skill-first + LLM 验证策略/失败解读层；吸收参考轨 2A/2B P5 + 规范 R17.4 L219-228 + 文档 08-测试与验收/02·04）
  license: MIT
---

# P-migration-verification（迁移验证工作流）

## 适用阶段与触发条件
- 阶段：P5 验证。承 P4 执行（output_code + patches + Evidence + P4→P5 Gate）与用户 P4→P5 Gate 裁决之后。
- 触发：P4 completed 且已挂 approved 的 P4→P5 `stage_promotion` Gate；从 DB / 工作区加载 P4 真实产物逐维度验证。
- 目标：验证迁移结果的**正确性**（编译/运行/测试/行为等价），生成**单一验证报告 + Evidence**，回答"**改对了吗？**"。P5 是**确定性验证的正确位置**（ground truth 锚点：编译器 / 测试框架 / diff / 等价检查器 / DB 查询 / 浏览器）。**验证是"用确定性工具证明改对了"，不是重新迁移、不是编辑源码、不是打包交付。**

## 核心纪律（本阶段的生命线，违反即一票否决）
- **确定性产事实，LLM 产判断——两者不可越界**：编译/运行/测试/diff/DB 行数抽样/浏览器截图这类**事实**只能由**确定性工具真实产生**；**LLM 绝不产出"通过/pass"结论替代真实测试**，只做"本次该验哪些维度 / 是否达标 / 为何失败 / 怎么修"的**判断与解读**。LLM 说"我认为通过"不算通过——只有确定性工具的铁证 + 反伪造门禁认定才算。
- **No Evidence No Completed**：证据不足只能标 `evidence_gap` / `未通过` / `NOT_APPLICABLE`（诚实），**绝不伪装通过**。禁 echo 假 pass；禁 `API 200 ≟ 业务通过`；禁"后端过但浏览器/业务不可用即判过"；禁 `PoC 冒充 Production`。
- **失败只回 P4，不在 P5 修**：验证失败 → 解读失败原因 + 产**回 P4 rework 的可执行修复建议**（哪个文件/哪类转换点/期望 vs 实际）。**P5 不编辑源码/输出码**（源只读 D-099；改代码是 P4 的职责）。
- **不发明**：唯一核心产物 `artifacts/p5_validation_report.json`（P6 门禁读它、前端 StagePageP5 读它）；**不新增第二核心产物、不新增槽位、不发明新 Gate 类型**。新维度的证据**桥接进现有槽/证据类别**或**诚实登记 evidence_gap**，不另立刚性枚举。
- **遵守上游**：验证目标技术栈/验收基准由 P0-P2 落定、P1 acceptance_baseline 为回归金标准（D-106）；P5 按既定基准验，不擅自改判验收口径。

## 输入（清单驱动按需加载，D-107；给路径不给硬编码内容）
- **P4 产物事实源**（经 `P5InputService.read_p4_input`，带 P4→P5 Gate 守卫，不绕过）：`output_code/{目标工程}/` 真实迁移代码、`patches/…` 补丁、P4 Evidence（含 output_sha256，D-101）、`artifacts/p4/p4_execution_summary.json`（变更清单 + patch 索引 + 逐节点结果 + PoC/Production 分级 + build/run evidence_gap）。
- **回归金标准**：`artifacts/p1/…` 的 acceptance_baseline / tech_stack（D-106，作行为等价/回归比对锚点）；P0-P2 完成包按需取。
- **源根**：`source/`（只读，D-099）——仅为对照真实源→产物做等价/结构映射核对，**不得写**。
- 用读取工具（`list_files`/`code_grep`/`fs_read`/`read_artifact`/`get_project_info`）**按需探查真实产物与基线**，再规划验证；缺失的输入如实记 evidence_gap，不臆造。

## 执行步骤（目标驱动，Agent 自主规划）
1. **读输入、确认可验范围**：读 P4 output_code/patches/Evidence/summary + P1 基线，明确"本次迁移改了什么、目标栈是什么、拿什么当回归金标准"。
2. **规划本次适用的验证维度**（LLM 判断，不硬编码）：从下面§验证维度指引里，**按本次迁移的技术栈/形态/环境判断哪些适用、哪些 NOT_APPLICABLE**（如：无浏览器前端则 E2E QA=NA；库类无入口则 run=NA；无远程 DB 则数据迁移抽样标 evidence_gap）。给出验证策略（先验什么、证据从哪来）。
3. **以确定性工具产铁证**：对每个适用维度，调用相应确定性验证器/命令产**真实事实**（编译退出码、测试通过数、diff、静态检查、DB 行数/抽样、浏览器截图）。事实以**真实产物/退出码/落盘证据**为准，非模型自报。
4. **反伪造门禁认定（不可越权）**：硬必需槽位是否真实 validated、能否 completed，由**确定性门禁 `can_mark_completed` 认定**——LLM 不参与、不能翻转。
5. **解读失败 + 提修复建议**：对未通过/evidence_gap 的维度，解读根因（编译错在哪、断言差在哪、方言不兼容点、行数不符），产**回 P4 rework 的可执行输入**（目标文件/转换点/期望值）。
6. **诚实降级**：环境不具备真实执行（无 SDK / 无远程 DB / 无可跑应用）→ 对应维度标 `evidence_gap` / `NEEDS_USER_INPUT` / `NOT_APPLICABLE` + 记"能力已具备、待环境真验"，**非阻断、不伪造**。
7. **产报告**：把确定性事实 + LLM 策略/解读/建议汇入**单一** `artifacts/p5_validation_report.json`；确定性槽位状态与 `can_be_completed` 为权威结论，LLM 部分显式标 `analysis_only`。

## 验证维度（**指引，非硬编码枚举**——由 Agent 按本次迁移判断适用性并填充证据类别）
> 以下是参考轨（2A 微步 P5-1~10 / 2B 十维度）与规范要求的**证据类别清单**，**不是必填槽位**。Agent 依本次迁移的真实形态**选择适用维度**，桥接进现有十槽或登记 evidence_gap；**不新增刚性槽位**。
> **能力接线（capability-first，R17.5-P5-R2）**：各维度已接线为可调用能力——先用 `p5_dimension_capabilities`（L0，看全量能力+环境探测清单）或 `p5_verify_dimension`（L2，探单个维度）判断**本次哪些维度环境具备可产真实事实、哪些环境缺失**。环境缺失（无 dotnet SDK / 无远程 DB / 无浏览器 / 无可跑应用）→ 该维度诚实标 `evidence_gap`（+ `capability_ready=true`，记"能力已具备、待环境真验"），**非阻断、绝不伪造通过**。这些能力/环境探测是**确定性事实**；维度是否**适用**、是否**达标**仍由你（LLM）判断，工具不产"通过"结论、不翻转 `can_be_completed`。
> **重写式迁移的结构对应（structure_mapping，R17.5-P5-R3 GAP-P5-4）**：`patches_exist` 确定性槽位【只核验 patch 文件真实存在且非空】，**不再用 patch/output 计数启发式判"对应/不对应"**（那会把 WebForms→Razor 整体重写、一源产多目标+转换脚本这类**非行级 diff** 误判为缺补丁）。source→target 的**结构对应**（含 rewrite/split/merge/rename）由你（LLM）产出 `structure_mapping`（每项 `{source, target, mapping_type, note}`）作 **diff 等价证据**，写入 advisory 分区（`analysis_only`，非门禁、不翻转 `can_be_completed`）。
> **十槽位 ↔ 文档 6/8 证据类别（R17.5-P5-R3 GAP-P5-7）**：运行时以**十槽位**为门禁权威，文档 08/02（6 类）· 04（8 类）是**面向 agent 的证据类别词表**；两者做**映射对齐**（详见 `p5_validation_plan.py` 词表映射注释），新维度证据**桥接进现有十槽或登记 evidence_gap**，**不新增刚性槽位/门禁**。
- **构建（build）**：目标栈可编译/构建（真实构建命令退出码；.NET/Java/Go/npm/make 按工程真实命令）。
- **运行 + 健康检查（run + health）**：可启动、健康端点/进程存活（库类无入口→NA）。
- **测试（test）**：单测/集成测试通过数与失败数（0 单测工程→NA 或据基线新建）。
- **静态检查（static）**：编译期/静态分析（如 py_compile/compileall、linter、类型检查）无致命项。
- **核心接口业务正确性**：关键接口按业务语义正确（**非 API 200 即通过**——要断言业务结果）。
- **DB 结构 + 数据迁移**：目标库表/约束结构一致、**数据行数核对 / 抽样比对**（无远程 DB→evidence_gap）。
- **安全脱敏 / 配置扫描**：无明文口令/密钥、敏感种子数据脱敏、配置外置正确（Key/Token 一律 [REDACTED]）。
- **浏览器 / E2E QA**：Playwright 真实走查关键页 + 截图证据（无前端→NA）。参考子 skill `P-browser-qa`。
- **业务闭环**：登录 / RBAC / 动态表单 / 审批等端到端闭环可用。
- **性能 / 兼容**：关键路径性能基准、目标运行时/中间件兼容（信创：麒麟/统信 + 达梦/人大金仓/openGauss/TongWeb）。参考子 skill `P-benchmark`。
- **回滚 / 故障验证**：回滚路径可用、故障可恢复。
- **回归对 P1 金标准（D-106）**：行为等价/回归比对以 P1 acceptance_baseline 为锚点。参考子 skill `P-eval-harness`。
- **缺陷闭环**：已知缺陷复现→修复→回归验证闭合（修复本身回 P4）。

## 输出 / 产物（固定，不发明）
- **唯一核心产物** `artifacts/p5_validation_report.json`：含验证计划（十槽位状态）、确定性 verify_results / conditional_results（事实）、`can_be_completed`（确定性门禁认定，权威）、以及 LLM 辅助层（`validation_strategy` / `failure_interpretation` / `repair_suggestions`，均 `analysis_only=true`，不改槽位状态与 can_be_completed）。
- **维度能力分区** `dimension_capabilities`（capability-first）：每个验证维度的 `capability_ready`（能力是否已接线）+ `environment_available`（运行环境是否具备）+ `status`（available / evidence_gap）。此分区为**非门禁**能力/环境事实，**不参与 can_be_completed**，环境缺失维度诚实标 `evidence_gap`（待环境真验），非阻断、不伪造。
- Evidence 以**真实落盘/退出码/截图**为准，落库（D-066）供查询。
- P5 通过（`can_be_completed=true`）时由 make_work_node 在 review passed 时挂 P5→P6 Gate；**P5 不推进 P6 业务、不做交付打包**。

## 质量门 / 验收标准
- 5 个硬必需槽位（output_code 存在 / patches 存在 / P4 Evidence 真实 sha256 / P4 summary 可解析 / P4→P5 Gate approved）**真实 validated** 才可 completed；缺任一 → P5 不得 completed（`can_mark_completed` 硬约束）。
- 有条件必需槽位（build/run/test/static）按真实命令执行结果诚实标状态；命令不可识别/环境缺失 → `NEEDS_USER_INPUT`/`evidence_gap`/`NOT_APPLICABLE`，不伪造。
- 所有"通过"结论可追溯到**确定性工具的真实证据**；无证据处显式 evidence_gap。
- 失败维度产**回 P4 rework 的可执行建议**；P5 不直接改代码。
- 只准备 P5→P6 Gate，不发明新 artifact/Gate/槽位。

## 信创迁移要点
- 目标库/运行时/中间件（麒麟/统信 + 达梦/人大金仓/openGauss + TongWeb/宝兰德等）以 P0-P2 落定为准，P5 按其验证，不越权重判。
- 国产环境不具备真实执行时标 evidence_gap（如"达梦真实连接需远程环境，本轮未执行，能力已接线待环境真验"），不臆测"已兼容/已通过"。
- 高风险验证命令（L4/L5 写/命令）走既有 L0-L5 分级 + action_approval Gate；本 skill 不放宽风险门。

## 反例 / 禁止（一票否决）
- 禁止 LLM 产出"通过/pass"结论替代真实测试；禁止把模型自报当 Evidence；禁止翻转 `can_mark_completed`。
- 禁止 echo 假 pass / 假 build 通过；禁止 `API 200` 当业务通过；禁止后端过但浏览器/业务不可用却判过；禁止 PoC 冒充 Production 全量。
- 禁止把 `evidence_gap` / `NEEDS_USER_INPUT` 渲染成"通过"或伪造 completed。
- 禁止在 P5 编辑源码/输出码修 bug（失败只回 P4 rework）；禁止写 `source/`（只读 D-099）。
- 禁止新增第二核心产物、新增十槽之外的刚性槽位、发明新 Gate 类型。
- 禁止做交付打包（那是 P6）；禁止以 P5 自检替代独立 Acceptance 验收（D-082）。
- 禁止记录明文凭据/Key（报告/日志/Trace/Audit 一律 [REDACTED]）。

## 与平台集成
- 验证走**既有工具循环**、加载**全量工具集**（读：`list_files`/`code_grep`/`fs_read`/`read_artifact`/`get_project_info`；确定性验证事实：P5 验证器 / 命令执行经 ExecutionProvider；P5 验证事实回读：`p5_verification_facts`）。
- **维度能力工具（capability-first，R17.5-P5-R2）**：`p5_dimension_capabilities`（L0 只读，全量维度能力+环境探测清单）、`p5_verify_dimension`（L2，探单个维度：browser_qa / eval_harness / db_migration / business_flow / regression_baseline / benchmark / dotnet_build）。这类工具**只报"能力是否接线 + 环境是否具备"的确定性事实**，环境缺失→诚实 `evidence_gap`（待环境真验），**不产"通过"结论、不改槽位状态、不翻转 `can_be_completed`**。真实事实由对应子 skill 工作流 + 确定性命令/工具在环境具备时产生。
- .NET build/test/static 命令 **SDK-aware 补全**：本机有 dotnet SDK → 条件槽走真实 `dotnet build/test/format` 命令产退出码事实；无 SDK → 诚实 `needs_user_input`（阻断，不伪造），并记能力已接线待 SDK / 远程执行主机环境真验。
- **工具权限**：所有节点可调所有工具，**不按阶段硬编码工具白名单**；安全交既有 **L0-L5 分级**——L3+ 强制 action_approval Gate、L5 命令 DENY 硬阻、ExecutionProvider 白名单。命令执行的真实结果是确定性事实来源。
- 模型调用经 ModelGateway（D-098），不硬编码模型名/endpoint；无可用 Key → LLM 辅助层诚实标 evidence_gap（advisory 缺席，**非阻断**），确定性验证仍照常产事实。
- 写盘经 WorkspaceMediator 单一写闸（D-099/D-104）：source/ 只读、artifacts/ 可写、越界写被拒记 Audit。
- 产物挂 Project/Run/Stage，附 Trace/Audit；Evidence 落库（D-066）以真实产物为准。
- P5 handler 由 make_work_node 在 LangGraph p5_work 节点内调用（D-037 不绕主编排）。

## 参考
- 参考轨 2A（Copilot）/ 2B（Claude Code）P5 基线（2B = 真实命令轨标尺：真实 build/test/static 退出码 + 诚实 evidence_gap + 不伪造 pass）。
- 同目录/相关子 skill 作**次级能力辅料**（按需引用，不重复其内容）：`P-browser-qa`（浏览器/E2E QA + 截图）、`P-eval-harness`（行为等价/断言/回归对基线）、`P-benchmark`（性能基准）、`P-code-tour`（产物走查/结构映射解读）。
- rebuild：契约 §4.5；文档 08-测试与验收/02·04；D-066（Evidence 落库）/D-098（ModelGateway）/D-099（source 只读）/D-101（sha256 反伪造）/D-105（DB refs 单一事实源）/D-106（P1 金标准）/D-107（清单驱动）/D-108（需求入 skill）；AGENTS §2.3、禁止项 25/26。
