---
name: P-code-tour
description: P1 建档 / P6 交付 — 生成锚定真实文件/行的 CodeTour 代码导览，按 SMIG 讲清现状与迁移前后差异
metadata:
  series: P
  phase: cross
  category: documentation_skill
  status: platform_runtime
  source: ECC skills/code-tour (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-code-tour（代码导览）

## 适用阶段与触发条件

- 阶段：跨阶段。P1 建档时为遗留系统生成导览帮助团队 / 用户理解；P6 交付时生成迁移前后差异导览与变更说明交付给用户。
- 触发：需要把复杂代码或迁移改动「讲清楚」——遗留系统初识、关键模块讲解、迁移变更交付评审。
- 目标：生成锚定到真实文件 / 行号的 CodeTour `.tour` JSON 导览，每个停靠点按 SMIG 结构讲解，使读者沿真实代码走查即可理解。

## 输入

- 目标代码仓库（P1：遗留源；P6：迁移后产物 + 迁移前快照）。
- P0/P1 档案、迁移变更记录、P4 执行产出与 P5 验证结论。
- 需要讲解的主题（如「订单主流程」「Oracle→达梦 DM 数据访问层改造」）。

## 执行步骤

1. **确定导览主题与受众**：P1 面向理解现状，P6 面向交付评审与运维交接。
2. **选取停靠点**：定位真实文件与行号作为锚点，覆盖主流程关键节点；锚点必须实际存在，行号准确。
3. **按 SMIG 撰写每个停靠点**：Situation（此处在做什么）/ Mechanism（怎么实现的）/ Implication（对系统 / 迁移的影响）/ Gotcha（坑与注意点）。
4. **生成 `.tour` JSON**：符合 CodeTour 结构（标题、步骤数组、每步 file + line + description），可在编辑器中按步走查。
5. **P6 差异导览**：并列迁移前 / 后锚点，讲清「改了什么、为什么改、风险点」，关联 ADR、蓝图步骤与 P5 验证证据。
6. **校验锚点有效性**：确认所有 file/line 在对应快照中可解析，无悬空锚点。

## 输出 / 产物（Artifact / Evidence）

- CodeTour `.tour` JSON 导览文件（锚定真实文件 / 行）。
- P6：迁移前后差异导览 + 变更说明文档（关联 ADR / 蓝图 / 验证结论）。
- 导览挂 Project/Stage/Node，附 Trace/Audit；锚点对应的真实代码为 Evidence。

## 质量门 / 验收标准

- 每个停靠点锚点真实存在、行号准确，无悬空 / 失效锚点。
- 每个停靠点完整覆盖 SMIG 四要素，Gotcha 不得空泛。
- P6 差异导览每条变更可追溯到 ADR / 蓝图步骤 / P5 验证证据。
- 导览叙述与代码实际一致，不描述代码中不存在的逻辑。

## 场景要点（按项目场景取用）

- 数据访问层改造导览：重点标注 Oracle/MSSQL 方言 SQL → 达梦 DM/openGauss/GaussDB 的改写点（函数 / 分页 / 序列 / 类型），Gotcha 写清精度 / 排序 / 空值语义差异。
- 中间件适配导览：标注 WebLogic/WebSphere → 东方通 / 宝兰德 的数据源、JNDI、类加载改动点。
- 平台 / OS 相关导览：标注去 Windows 化改动（路径、注册表、COM、服务）在麒麟 / 统信上的对应实现。
- 字符集 / 时区 / 排序规则在国产库上的默认差异，凡涉及的停靠点务必以 Gotcha 提示。
- P6 交付导览应让运维 / 用户无需通读全部代码即可理解迁移改了哪些关键面。

## 反例 / 禁止

- 禁止锚定到不存在的文件 / 错误行号，或讲解与代码不符的「理想逻辑」。
- 禁止省略 Gotcha 把风险点藏起来。
- 禁止把模型臆测的实现当作代码事实写进导览。
- 禁止在导览描述中暴露连接串 / Key/Token/Secret。
- 禁止把 Case / 社区资源代码当作本项目可执行产物在导览中引用。

## 与平台集成

- 导览文案撰写、SMIG 归纳经 ModelGateway 调用，不直接调 Provider。
- 生成 / 写入 `.tour` 文件属写操作，按 Gate 策略处理；不修改被导览的源代码。
- 导览与变更说明挂 Project/Run/Stage/Node，生成 Artifact/Evidence 并附 Trace/Audit/Registry。
- 模型对代码的讲解属辅助，不是 Evidence；Evidence 是锚点对应的真实代码与 P5 验证证据。
- 导览自检（锚点有效性）不替代 Acceptance 验收；社区 Skill 默认只读。

## 参考

- ECC `skills/code-tour`（MIT, https://github.com/affaan-m/ECC）
- rebuild 文档：`文档/00-项目治理/`（交付与评审）、`文档/04-模型与资源/`（Artifact/Evidence/Trace）
