---
name: P-migration-delivery
description: P6 交付阶段 — 将 P4 真实产物 + P5 真实验证结论整理为可移交/可审计/可复查/可回退的最终交付包，回答"交付了什么、能交付吗（PoC 还是 Production）"；给确定性交付事实（清单/hash SHA-256/脱敏扫描/许可检测/AETA 索引）+ P5 验证报告→组织交付叙述（部署/运维/回退说明、许可说明、经验沉淀、用户验收摘要）+ 验收结论建议；确定性内核给"事实与门禁认定"、LLM 只给"如何交付/验收建议"的叙述，绝不产"通过/accepted"结论替代确定性门禁、绝不翻转双向门禁/脱敏门禁/final gate；无 P5 真实验证即 accepted / 无许可说明即分发 / PoC 冒充 Production / 隐藏失败缺口 / 含明文密钥 / 无回退说明却称可交付 = 一票否决；只产单一核心产物 p6_delivery_report.json（内部承载清单/索引/风险/许可/运维/回退）、不发明第二核心产物/新 Gate 类型；source/ 无条件排除、下载仅 output_code/+patches/ 且脱敏 + SHA-256
metadata:
  series: P
  phase: P6
  category: stage_skill
  status: platform_runtime
  source: rebuild R17.5-P6-R1（D-108；GAP-P6-1 skill-first + LLM 交付叙述/验收建议 advisory 层；对称已 accepted 的 P5 P-migration-verification；吸收参考轨 2A P6-1~P6-10 + 2B 双向门禁/许可 Gate/ACCEPTANCE_RESULTS + 契约 §8）
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-migration-delivery（迁移交付工作流）

## 适用阶段与触发条件
- 阶段：P6 交付。承 P5 验证（`p5_validation_report.json` + Evidence + P5→P6 Gate）与用户 P5→P6 Gate 裁决之后。
- 触发：P5 `can_be_completed=true` 且已挂 approved 的 P5→P6 `stage_promotion` Gate；从 DB / 工作区加载 P4 真实产物 + P5 真实验证结论生成交付包。
- 目标：将 P4 真实产物 + P5 真实验证结论整理为**可移交、可审计、可复查、可回退**的最终交付包，回答"**交付了什么？能交付吗（PoC 还是 Production）？**"。**P6 是交付/归档，不是普通总结、不是重新验证、不是重跑迁移。**

## 核心纪律（本阶段的生命线，违反即一票否决）
- **确定性产事实与门禁，LLM 产叙述与建议——两者不可越界**：交付清单 / hash（SHA-256）/ 脱敏扫描 / 许可文件检测 / AETA 索引 / P5→P6 双向门禁 / 脱敏放行 Gate / final gate 这类**事实与门禁认定**只能由**确定性内核真实产生**；**LLM 绝不产出"通过 / accepted / 可交付"确定性结论**，只做"交付了什么 / 怎么部署运维回退 / 验收建议（哪一档、为何）"的**叙述与建议**（`analysis_only`）。LLM 说"我认为可以 accepted"不算 accepted——是否可交付由确定性门禁 + 用户 final Gate 认定。
- **双向门禁不可翻转**：P5 未通过（读 `p5_validation_report.json` 的 `can_be_completed=false`）→ 编排层 `blocked` + API 层 422，P6 不得执行；LLM 不参与、不能翻转此判定。
- **No Fake Delivery**：证据不足只能标 `evidence_gap` / `risk_manifest` 项（诚实），**绝不伪装可交付**。禁 `PoC 冒充 Production`；禁隐藏失败 / 缺口；禁"无 Evidence archive 却称已交付"；禁"无回退说明却声称可交付"。
- **许可须清楚才可对外分发**：交付含第三方来源代码时须检查**许可声明 / 来源链接 / 改造范围 / 分发边界 / LICENSE 文件存否**；许可不清 → **默认 `accepted_with_warning` + 计入 `risk_manifest` 明列**（内部验证可继续）；**仅当标记"对外分发"时升 `blocked`**（对外分发前须核实，Q-P6-2 裁决口径）。**不硬编码具体许可判断（MIT/无 LICENSE 等），作检测数据随项目替换。**（R3 已落地确定性检测：`P6DeliveryService._build_license_notice` 产 `license_notice`——LICENSE/COPYING 文件存否 + 第三方来源标记 + 许可声明片段 + `distribution_flag` + `license_clarity`；许可不清默认 `accepted_with_warning` + `risk_manifest`，`distribution_flag=true` 才升 `blocked`；只记路径/模式名，不写内容/密钥 D-032。）
- **不发明**：唯一核心产物 `artifacts/p6_delivery_report.json`（P6 门禁 / 前端 StagePageP6 经 `/p6/package` 读、下载走 `/p6/download`），内部承载 delivery / risk / hash manifest + 四类 AETA 索引 + 许可 / 范围 / 验收档 / 运维 / 回退分区；**不新增第二核心产物、不发明新 Gate 类型**（复用 `stage_promotion` / `desensitization_release`）。
- **遵守上游**：交付范围 / PoC-Production 定级 / 验收基准由 P0-P5 落定；P6 按既定结论整理交付，不擅自改判验证口径、不重做验证（那是 P5）、不改源码 / 输出码（源只读 D-099，改代码是 P4）。

## 输入（清单驱动按需加载，D-107；给路径不给硬编码内容）
- **P5 验证结论事实源**（经 `RealP6Handler._load_p5_report` / `P5InputService.read_p4_input`，带 P5→P6 Gate 守卫，不绕过）：`artifacts/p5_validation_report.json`（十槽位真状态 + `can_be_completed` 权威门禁 + verify_results/conditional_results 事实 + LLM 验证 advisory）。
- **P4 产物事实源**：`output_code/{目标工程}/` 真实迁移代码、`patches/…` 补丁、P4 Evidence（含 output_sha256，D-101）、`artifacts/p4/p4_execution_summary.json`（变更清单 + PoC/Production 分级 + build/run evidence_gap）。
- **确定性交付事实**（由 `P6DeliveryService` 产）：`delivery_manifest`（output_code/patch 路径 + SHA-256 + 字节数）、`hash_manifest`（全文件 SHA-256）、`risk_manifest`（未通过项 / evidence_gap，不混入通过交付）、四类 AETA 索引（artifact/evidence/trace/audit）、脱敏扫描结论（`desensitization_ok`）。
- **源根**：`source/`（只读，D-099，且**无条件排除出交付包**——仅供对照不得纳入分发）。
- 用读取工具（`list_files`/`code_grep`/`fs_read`/`read_artifact`/`get_project_info`）**按需探查真实产物与验证报告**，缺失的输入如实记 `evidence_gap` / `risk_manifest`，不臆造。

## 执行步骤（目标驱动，Agent 自主规划；确定性内核先行、LLM 叙述在后）
1. **确定性内核先行（权威，不因 LLM 层放松）**：P5→P6 双向门禁校验（P5 未过 → blocked/422）→ `P6DeliveryService.generate_delivery_package` 产 delivery/risk/hash manifest + AETA 索引 + 脱敏扫描 → 脱敏硬门禁（含疑似密钥 → 创 L5 `desensitization_release` Gate 并 blocked，唯一放行 = 用户显式批准 override）→ source/ 无条件排除 → final Gate（`stage_promotion`，D-023 用户授权）。**以上判定全由确定性内核认定，LLM 不参与。**
2. **读交付事实、明确交付范围**：读 P5 验证报告 + P4 summary + 确定性交付清单，明确"本次交付了什么、目标栈、PoC 还是 Production、哪些通过 / 哪些进 risk_manifest"。
3. **组织交付叙述（LLM，advisory）**：产面向用户的**可读交付摘要**（交付了什么、覆盖范围、限制），以及**部署 / 运维 / 回退提示框架**（`deploy_notes` / `operation_notes` / `rollback_notes`，来源须为真实 P2/P3/P4 产物，不硬编码目标环境实例；本轮 R1 先给占位框架，真实内容由 R5 充实）。
4. **验收结论建议（LLM，advisory，绝不替代确定性门禁）**：基于 P5 证据完整度 + 风险 + 许可清晰度给**验收档建议**（accepted / accepted_with_warning / rework_required / blocked）+ 依据 + 注意事项——PoC + evidence_gap + 许可不清 → 诚实建议 `accepted_with_warning`，**绝不建议伪 Production accepted**。此为**建议**，最终由确定性门禁 + 用户 final Gate 决定。
5. **经验沉淀（LLM，advisory，反过拟合）**：产迁移经验候选（Skill / Case / ADR 候选），来源真实产物，供人工采纳。
6. **诚实降级**：交付期 env-不具备能力（物理打包 zip / 部署冒烟 / 许可网络核验 / 目标运行时探测）无环境 → 对应项标 `evidence_gap` / `NOT_APPLICABLE` + 记"能力已具备、待环境真验"，**非阻断、不伪造**（capability-first，R17.5-P6-R2 落地）。
7. **产报告**：把确定性交付事实 + 门禁认定 + LLM 交付叙述 / 验收建议 / 经验沉淀汇入**单一** `artifacts/p6_delivery_report.json`；确定性清单 / 门禁认定为权威结论，LLM 部分显式标 `analysis_only`。

## P6-1~P6-10 环节（指引，非硬编码枚举——Agent 按本次交付判断适用性）
> 以下是参考轨（2A 微步 P6-1~10 / 2B 十维度）的交付环节清单，**不是必填槽位**。Agent 依本次交付真实形态选择适用环节，桥接进 `p6_delivery_report.json` 现有分区或登记 evidence_gap；**不新增刚性槽位 / 新 Gate**。
- **P6-1 P5 输入校验 + 双向门禁**：P5 `can_be_completed` 权威读取；未过 → blocked/422（确定性，不可翻转）。
- **P6-2 交付范围 + PoC/Production 定级**：从 P5 证据完整度确定性推导 `scope_level`；**PoC 不得伪装 Production**（R3 已由 `P6DeliveryService._derive_scope_and_acceptance` 确定性映射产出：硬必需全 validated + 无 evidence_gap + build/run validated → `production_candidate`，否则 `poc`）。
- **P6-3 交付清单 + hash 校验**：output_code + patches 路径 + SHA-256 + 字节数（确定性事实）。
- **P6-4 AETA 索引**：artifact / evidence / trace / audit 四索引完整。
- **P6-5 部署 / 运维说明**：来自真实 P2/P3/P4 产物（R3 起由 handler 确定性喂入 `upstream_artifacts` 事实接地）；PoC 标注限制（LLM advisory 叙述）。
- **P6-6 回退说明 + 验收结论 ACCEPTANCE_RESULTS**：回退路径可用性说明；验收多档（R3 已由 `_derive_scope_and_acceptance` 确定性映射产出 `acceptance_result`：accepted / accepted_with_warning / rework_required / blocked，权威事实档；LLM `acceptance_advice` 建议并存不冲突）。
- **P6-7 风险清单 risk_manifest**：未通过项 / evidence_gap / not_covered_items（确定性，不混入通过交付）。
- **P6-8 许可 Gate + 经验沉淀**：许可声明 / 来源 / 改造范围 / 分发边界检测（R3 已落地：`_build_license_notice` 产 `license_notice`，许可不清计入 `risk_manifest`，`distribution_flag=true` 才升 blocked）；迁移经验候选（LLM advisory）。
- **P6-9 用户验收材料 / 交付摘要**：面向用户的可读交付叙述（供判断 接受 / 警告 / 返工 / 阻塞）。
- **P6-10 final Gate + 脱敏放行**：`stage_promotion`（D-023 用户授权）；含明文密钥 → `desensitization_release`（D-034 L5）唯一放行路径。

## 输出 / 产物（固定，不发明）
- **唯一核心产物** `artifacts/p6_delivery_report.json`：含 delivery/risk/hash manifest（确定性事实与门禁，权威）、四类 AETA 索引、脱敏结论布尔、以及 LLM 交付 advisory 层（`delivery_narrative` / `operation_rollback_notes` / `acceptance_advice` / `experience_notes`，均 `analysis_only=true`，**不改门禁认定、不翻转双向门禁 / 脱敏门禁 / final gate**）。**不含 source/ 与密钥明文**（D-105③④ / AGENTS §8）。
- **交付边界红线**：source/ 无条件拒绝纳入；下载仅 `output_code/`+`patches/` 且脱敏 + SHA-256（D-105④⑧）；未通过项进 `risk_manifest` 不混入交付。
- final Gate（`stage_promotion`，D-023 用户授权）由 handler 创建；**P6 不越过用户 final Gate 自称已交付**。

## 质量门 / 验收标准
- P5 `can_be_completed=true`（读 `p5_validation_report.json`）+ P5→P6 Gate approved 才可交付；否则 blocked/422（双向门禁，硬约束）。
- 脱敏扫描 `desensitization_ok=true`，否则默认 blocked（唯一放行 = 用户批准 `desensitization_release` L5 Gate）。
- 交付清单 + hash_manifest（SHA-256）完整；risk_manifest 记录未通过项且不混入交付；四类 AETA 索引齐全。
- 所有"可交付 / accepted"结论可追溯到**确定性门禁 + 用户 final Gate**；LLM 建议显式 `analysis_only`，无证据处显式 evidence_gap。
- 只准备 P6 final Gate，不发明新 artifact / Gate / 槽位。

## 场景要点（按项目场景取用）
- 目标库 / 运行时 / 中间件（麒麟 / 统信 + 达梦 / 人大金仓 / openGauss + TongWeb / 宝兰德等）以 P0-P5 落定为准，P6 按其结论整理交付，不越权重判。
- 目标环境不具备真实交付验证（物理打包 / 部署冒烟 / 许可网络核验）→ 诚实标 evidence_gap（"能力已接线待环境真验"），不臆测"已交付 / 已部署验证通过"。
- 高风险交付动作（L4/L5 写 / 命令）走既有 L0-L5 分级 + Gate；脱敏放行走 L5 `desensitization_release`；本 skill 不放宽风险门。

## 反例 / 禁止（一票否决）
- 禁止 LLM 产出"通过 / accepted / 可交付"确定性结论替代确定性门禁；禁止翻转 P5→P6 双向门禁 / 脱敏门禁 / final gate。
- 禁止无 P5 真实验证即 accepted；禁止 PoC 冒充 Production 全量；禁止隐藏失败 / 缺口。
- 禁止无许可说明即对外分发；禁止交付含明文密钥；禁止无 Evidence archive 却称已交付；禁止无回退说明却声称可交付。
- 禁止把 `evidence_gap` / `risk_manifest` 项渲染成"通过 / 可交付"或伪造 completed。
- 禁止纳入 source/ 进交付包；禁止在 P6 编辑源码 / 输出码（源只读 D-099；改代码是 P4）；禁止重做验证（那是 P5）。
- 禁止新增第二核心产物、发明新 Gate 类型；禁止以 P6 自检替代独立 Acceptance 验收（D-082）。
- 禁止记录明文凭据 / Key（报告 / 日志 / Trace / Audit 一律 [REDACTED]）。

## 与平台集成
- 交付走**既有工具循环**、加载**全量工具集**（读：`list_files`/`code_grep`/`fs_read`/`read_artifact`/`get_project_info`；确定性交付事实：`P6DeliveryService`；门禁：`RealP6Handler` 双向门禁 + 脱敏硬门禁 + final Gate）。
- **工具权限**：所有节点可调所有工具，**不按阶段硬编码工具白名单**；安全交既有 **L0-L5 分级**——L3+ 强制 action_approval Gate、L5 命令 DENY 硬阻、脱敏放行走 L5 `desensitization_release`。
- 模型调用经 ModelGateway（D-098），不硬编码模型名/endpoint；无可用 Key → LLM 交付叙述 / 验收建议层诚实标 evidence_gap（advisory 缺席，**非阻断**），确定性交付内核 + 门禁仍照常认定。
- 写盘经 WorkspaceMediator 单一写闸（D-099/D-104）：source/ 只读 + 排除、artifacts/ 可写、越界写被拒记 Audit。
- 产物挂 Project/Run/Stage，附 Trace/Audit；Evidence 落库（D-066）以真实产物为准。
- P6 handler 由 make_work_node 在 LangGraph p6_work 节点内调用（D-037 不绕主编排）。

## 参考
- 参考轨 2A（Copilot）P6-1~P6-10 + P6-Gate + 一票否决 Rubric / 2B（Claude Code）双向门禁 + 许可 Gate + ACCEPTANCE_RESULTS 多档 + 经验沉淀。
- 同目录相关子 skill 作**次级能力辅料**（按需引用，不重复其内容）：`P-deployment-patterns`（部署模式）、`P-canary-watch`（灰度观察）。这些是 ECC 领域 skill，与本交付阶段编排主 skill 并存不冲突。
- rebuild：契约 §8；文档 08-测试与验收；D-023（P 阶段晋级 Gate 用户授权）/D-032·D-034（脱敏 + L5 强制 Gate）/D-066（Evidence 落库）/D-098（ModelGateway）/D-099（source 只读）/D-101（sha256 反伪造）/D-105（③ P5 不改 output_code、④ 交付包默认不含 source、⑧ 下载脱敏 + SHA-256）/D-107（清单驱动）/D-108（需求入 skill）；AGENTS §2.3、禁止项 25/26。
