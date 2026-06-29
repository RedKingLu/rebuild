---
name: intake-review
description: P0 接入阶段 — 对 P0 产物做 Review Pass：检查导入完整性、source_index 覆盖、Environment Profile 一致性、密钥泄露排查
metadata:
  series: P
  phase: P0
  category: review_skill
  status: planned
  source: rebuild_self
  license: rebuild-internal
---

# intake-review（P0 接入 Review Pass）

## 适用阶段与触发条件

- 阶段：P0 接入小循环的 Review Pass 环节。
- 触发：source materialization 完成 + intake_report 生成后。
- 目标：评审 P0 产物的完整性与诚实性，决定是否通过或触发返工。

## 输入

- P0 Context Package（含 Project、Workspace 状态、Environment Profile、source_config）。
- source_index.json（Artifact）。
- import_result.json（Artifact）。
- intake_report（Artifact）。
- 最近 Trace/Audit 摘要。

## 执行步骤

1. **导入完整性检查**：验证 source/ 文件数与 source_index 一致；检查预期文件类型是否存在（如根据 source_type 应有构建文件）。
2. **source_index 覆盖检查**：抽样验证索引文件与实际文件系统一致。
3. **Environment Profile 一致性**：检查 language_hint / framework_hint 是否与 source_index 中的文件扩展名分布一致。
4. **密钥/敏感信息泄露排查**：扫描 Trace/Audit/intake_report 中是否出现 Key/Token/Secret/Password 明文。
5. **诚实性检查**：未知项是否以 `【未知-需用户确认】` 显式标注而非留空；空 source/ 是否诚实标记。
6. **生成 ReviewResult**：passed=true/false；列出 issues（需修正项）和 recommendations（建议改进项）。

## 输出 / 产物（Artifact / Evidence）

- ReviewResult（Artifact）：passed、issues[]、recommendations[]、reviewed_at、reviewer（skill 标识）。
- 若 passed=false：issues 中的每项关联具体文件/字段，给出修正建议。

## 质量门 / 验收标准

- 关键检查项（导入完整性、密钥泄露）必须通过；非关键项（一致性建议）可标记 warning 不阻断。
- ReviewResult 写入 Trace；若 passed=false 触发返工，写入 Audit。

## 禁止

- 禁止通过 Review 后修改 P0 产物（Review 只评审，不修改）。
- 禁止将 ReviewResult 作为 Acceptance 验收结论。
- 禁止跳过密钥泄露检查。
