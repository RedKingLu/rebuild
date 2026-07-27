---
name: P-browser-qa
description: P5 验证 — 迁移Web应用(WebForms→现代前端)迁移前后浏览器级回归、截图对比与关键交互验证
metadata:
  series: P
  phase: P5
  category: validation_skill
  status: platform_runtime
  source: ECC skills/browser-qa (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---

# P-browser-qa（浏览器级 QA 验证）

## 适用阶段与触发条件
P5 验证阶段，当迁移对象为 Web 应用（如 ASP.NET WebForms/MVC、JSP → 现代前端或重构后页面）时触发。用受控浏览器自动化（Playwright 等）对迁移前后做可视、交互、可达性回归，并给出明确发布裁决。

## 输入
- 源系统 Web 应用可访问地址（迁移前基线）
- 迁移后目标系统可访问地址（信创栈部署）
- 关键用户路径清单（登录、查询、表单提交、报表导出等）
- 基线截图集（如已采集）与可达性（a11y）要求

## 执行步骤
1. **路径建模**：将关键业务路径写成可重放脚本（导航、输入、点击、断言），覆盖核心交易与高频页面。
2. **采集基线**：在源系统跑脚本，采集页面截图、DOM 结构、关键元素文本/状态作为基线。
3. **迁移后回放**：在目标系统以相同脚本回放，采集同位截图与状态。
4. **截图对比**：逐页做视觉 diff，标注布局/文案/控件差异；区分预期改动（UI 重构）与回归缺陷。
5. **交互验证**：断言关键交互结果正确（提交成功、跳转正确、数据展示一致、错误提示等价）。
6. **console / 网络检查**：捕获 console error、未捕获异常、4xx/5xx 请求，国产浏览器内核（如基于 Chromium 的奇安信/红莲花）兼容性问题尤需关注。
7. **可达性（a11y）**：检查关键页面 a11y 规则（语义标签、对比度、可聚焦），记录违规。
8. **裁决**：综合给出 SHIP / SHIP-WITH-FIXES / DO-NOT-SHIP，附阻塞项与可延后项。

## 输出 / 产物（Artifact / Evidence）
- 关键路径自动化脚本
- 基线截图集与迁移后截图集 + 视觉 diff 结果
- 交互断言结果（通过/失败）
- console error / 网络异常清单
- a11y 违规清单
- 裁决报告（SHIP / SHIP-WITH-FIXES / DO-NOT-SHIP + 阻塞项）

## 质量门 / 验收标准
- 全部关键用户路径有脚本且已回放
- 视觉差异逐项分类（预期改动 vs 回归），回归缺陷有处置
- 无未处置的 console error / 5xx
- 关键交互断言通过；登录、提交、查询等核心路径必须 SHIP 级通过
- DO-NOT-SHIP 阻塞项清零后方可判 completed

## 信创迁移要点
- 目标环境可能要求国产浏览器（奇安信、红莲花、统信浏览器）兼容，须在目标内核回放，不能只验 Chrome。
- WebForms 的 ViewState/回发模型迁移到现代前端后交互模型变化大，断言应聚焦业务结果而非 DOM 细节等价。
- on-prem 内网部署，自动化脚本须能在隔离网络运行，依赖（浏览器二进制、字体）需预置离线。
- 报表/导出类功能在国产 OS 字体缺失时易现乱码/截断，须专项验证。

## 反例 / 禁止
- 禁止只在公网/Chrome 验证而忽略目标国产浏览器内核
- 禁止把所有视觉差异一律判为通过或一律判为失败（须分类）
- 禁止忽略 console error 与 5xx 直接判 SHIP
- 禁止脚本中硬编码真实账号口令（须用脱敏测试账号 + secret 注入）
- 禁止把模型对截图的描述当作交互正确性 Evidence

## 与平台集成
- **能力接线（capability-first，R17.5-P5-R2）**：本维度已接线为平台能力——先用 `p5_verify_dimension` 探 `browser_qa`（探测 Playwright 是否安装 + 浏览器内核二进制是否可用），或 `p5_dimension_capabilities` 看全量矩阵。**浏览器运行时具备** → 按上述步骤跑真实回放/截图/断言产铁证；**运行时缺失**（无 Playwright / 无浏览器二进制 / 无可访问目标 URL）→ 该维度诚实标 `evidence_gap`（`capability_ready=true`，记"浏览器 QA 能力已接线，待浏览器运行时/目标部署环境真验"），**非阻断、绝不伪造 SHIP**。能力/环境探测是确定性事实，工具不产"通过"结论、不翻转 `can_be_completed`。
- 若用模型辅助分析截图/差异，调用经 ModelGateway；模型描述须有截图/断言佐证，模型输出不等于 Evidence。
- 自动化对目标系统执行写操作（提交表单、改数据）属高风险（L4-L5），须经用户 Gate 或限定在隔离测试数据。
- 脚本、截图集、diff、裁决报告挂 Artifact/Evidence，回放动作记 Trace/Audit。
- 裁决为 DO-NOT-SHIP 或证据不足时不得标记 completed；SHIP-WITH-FIXES 的修复项须回 P4 处置后复验。

## 参考
- ECC skills/browser-qa（MIT）
- webapp-testing / Playwright（受控浏览器自动化）
- P-eval-harness（接口与功能等价评估）
- P-deployment-patterns（发布裁决衔接上线）
