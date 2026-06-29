---
name: profile-review
description: P1 建档阶段 — 对 P1 全量识别产物做 Review Pass：检查 14 项覆盖完整性、Artifact 一致性、Evidence Gap 诚实性、密钥泄露排查
metadata:
  series: P
  phase: P1
  category: review_skill
  status: planned
  source: rebuild_self
  license: rebuild-internal
---

# profile-review（P1 建档 Review Pass）

## 适用阶段与触发条件

- 阶段：P1 建档小循环的 Review Pass 环节。
- 触发：full-stack-profiler 执行完成 + P1 建档产物生成后。
- 目标：评审 P1 全量识别的完整性与诚实性，决定是否通过或触发返工。

## 输入

- P1 Context Package（含 P0 intake_report、source_index、Environment Profile、已有 Artifact/Evidence）。
- full-stack-profiler 产出的全部 Artifact（14 项）。
- 最近 Trace/Audit 摘要。
- 上轮 ReviewResult（如为返工轮）。

## 执行步骤

1. **覆盖完整性检查**：14 项识别中是否至少 12 项有产出；未覆盖项是否有说明理由。
2. **confidence 诚实性**：抽查 high confidence 项是否有文件证据支撑；low/uncertain 项是否诚实标记。
3. **交叉一致性检查**：tech_stack 中的语言是否与 source_structure 中的文件分布一致；dependency_draft 中的框架是否与 cicd_inventory 中的构建配置一致。
4. **密钥/敏感信息泄露排查**：扫描所有 Artifact 和 Trace/Audit 中是否出现 Key/Token/Secret/Password 明文或掩码片段。
5. **Evidence Gap 诚实性**：不确定项是否已登记为 Evidence Gap；缺失项是否有说明。
6. **P2 输入包完整性**：p2_input_manifest.json 是否列出所有 P1 产出 Artifact 引用。
7. **生成 ReviewResult**：passed=true/false；列出 issues 和 recommendations。

## 输出 / 产物

- ReviewResult（Artifact）：passed、issues[]、recommendations[]、reviewed_at、reviewer。
- 若 passed=false：issues 关联具体 Artifact 和字段，给出修正建议。

## 质量门 / 验收标准

- 覆盖完整性和密钥泄露为阻断项（必须通过）。
- 一致性建议为 non-blocking warning。
- ReviewResult 写入 Trace；若 passed=false 触发返工，写入 Audit。

## 禁止

- 禁止通过 Review 后修改 P1 产物。
- 禁止将 ReviewResult 作为 Acceptance 验收结论。
- 禁止跳过密钥泄露检查。
- 禁止把 confidence=uncertain 的项在 Review 中改为 confirmed（应由执行者修正，非 Reviewer）。
