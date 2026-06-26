---
name: P-architecture-decision-records
description: P2 评估 / P3 规划 — 将目标栈选型（达梦 vs openGauss、中间件替换等）以 Nygard 格式 ADR 固化为评审与审计依据
metadata:
  series: P
  phase: P2
  category: documentation_skill
  status: platform_runtime
  source: ECC skills/architecture-decision-records (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---

# P-architecture-decision-records（架构决策记录 ADR）

## 适用阶段与触发条件

- 阶段：P2 评估（目标栈选型）、P3 规划（迁移路径 / 拆分策略决策）。
- 触发：识别到「决策时刻」——存在多个可行方案、且选择有长期影响或不易逆转（选库、选中间件、兼容模式、数据迁移策略、是否重写）。
- 目标：把每个关键决策的上下文、备选、理由、后果用 Nygard 格式 ADR 固化，供评审与审计追溯，杜绝「拍脑袋且无据可查」。

## 输入

- P0 接入档案、P1 建档与 P-documentation-lookup 的兼容性证据。
- 待决策项（如「Oracle 目标库：达梦 DM vs openGauss vs GaussDB」）。
- 各备选方案的约束（成本、运维能力、信创合规清单、性能要求）。

## 执行步骤

1. **识别决策时刻**：在评估 / 规划中捕获需要正式记录的分叉点，避免把可逆的小决定也写成 ADR（防止淹没）。
2. **采集上下文**：记录决策背景、约束、相关证据来源（引用文档检索结论 / 评估数据）。
3. **列备选方案**：至少 2 个真实备选，给出各自优劣、风险、信创合规符合度，不预设结论。
4. **给出决策与理由**：明确选定方案与可追溯的理由；理由须引用证据，不仅凭偏好。
5. **写明后果**：正面 / 负面后果、引入的新约束、后续待办与回滚可能性。
6. **维护索引**：在 `docs/adr/` 下按 `ADR-NNN-标题.md` 命名，更新 ADR 索引；未经用户同意不落盘正式文件。

## 输出 / 产物（Artifact / Evidence）

- Nygard 格式 ADR 文件（标题 / 状态 / 日期 / 背景 / 备选 / 决策 / 后果 / 关联）。
- ADR 索引（编号、标题、状态、关联阶段产物）。
- 与 P2 评估报告 / P3 规划的双向引用。
- ADR 挂 Project/Stage，附 Trace/Audit；ADR 引用的兼容性数据为 Evidence，ADR 本身记录决策。

## 质量门 / 验收标准

- 每个 ADR ≥2 个真实备选，理由有证据引用而非空泛偏好。
- 后果段明确列出负面后果与新约束（只写好处的 ADR 不合格）。
- 编号唯一、索引同步、状态（提议 / 已接受 / 已废弃）准确。
- 重大不可逆决策的 ADR 经用户 Gate 确认后方为「已接受」。

## 信创迁移要点

- 选库 ADR 必须比对兼容模式：达梦 DM 的 Oracle 兼容模式 vs openGauss/GaussDB 的 A 兼容模式，对 PL/SQL、存储过程、函数改写量的影响要量化进后果段。
- 中间件替换 ADR（WebLogic→东方通 / WebSphere→宝兰德）须记录 JDK 版本、数据源、JMS、类加载差异带来的改造范围。
- OS / 芯片 ADR（麒麟 / 统信 + 鲲鹏 / 飞腾）须记录架构对依赖库、原生组件、JIT 的影响。
- 「重写 vs 适配」是 .NET Framework 迁移的核心决策，必有 ADR，理由须引用 P0 识别的 Windows 强耦合点。
- 选型须对照信创合规 / 适配清单，合规符合度作为决策维度显式记录。

## 反例 / 禁止

- 禁止把多个无关决策塞进一个 ADR。
- 禁止只写选定方案、不列备选，或备选是「凑数」的假选项。
- 禁止隐藏负面后果只报喜。
- 禁止未经用户同意就在仓库写入 / 提交 ADR 文件。
- 禁止 ADR 引用不存在或无法复核的「证据」。

## 与平台集成

- ADR 草拟、备选优劣归纳经 ModelGateway 调用，不直接调 Provider。
- 落盘 / 提交 ADR 文件属写操作，按 Gate 策略经用户确认；重大不可逆决策须显式 Gate。
- ADR 与索引挂 Project/Run/Stage/Node，生成 Artifact/Evidence 并附 Trace/Audit/Registry。
- 模型给出的方案倾向属辅助意见，不是 Evidence；Evidence 是被引用的评估 / 兼容性数据。
- ADR 自检不替代 Acceptance 验收；社区 Skill 默认只读。

## 参考

- ECC `skills/architecture-decision-records`（MIT, https://github.com/affaan-m/ECC）
- rebuild 文档：`文档/00-项目治理/`（ADR 与评审）、`文档/04-模型与资源/`
