---
name: rebuild-work-guidelines
description: Execute rebuild project tasks following mandatory agent execution guidelines. Use this skill whenever the user asks you to work on rebuild documentation, handoff materials, construction planning, formal reviews, audits, acceptance checks, or any R-series construction task. Also use it when you are about to write, edit, review, or formalize any file under the rebuild project — even if the user doesn't explicitly mention "rebuild work guidelines." The skill ensures you follow the correct startup sequence (read AGENTS.md → load Skill → identify stage → build fact-source list → plan → execute → self-check) and never skip the mandatory fact-source reading step. This skill is MANDATORY for all rebuild tasks per AGENTS.md §9.1.
---

# rebuild 工作准则 Skill

> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.1.1
> 审核状态：经用户审核通过（2026-06-25）
> 来源：由原 `rebuild工作准则skill.md`（R2）重构为 SKILL.md + references/（R4-2 收口）

本 Skill 是 AGENTS.md 硬规则的可执行化身。AGENTS.md 定义"什么必须做/什么禁止做"，本 Skill 定义"怎么做"。

## 优先级规则

```
AGENTS.md（硬规则） > 交接/当前/（任务入口） > 本 Skill（执行方法） > 其他专题 Skill
```

- 冲突时以左侧为准，登记冲突为待确认项
- **本 Skill 是强制入口**（AGENTS.md §9.1）：Agent 不得跳过本 Skill 直接凭 AGENTS.md 执行任务

---

## 0. 启动序列

执行任何 rebuild 任务时，严格按以下顺序启动：

### Step 0：写下自检

```
我正在执行 rebuild 当前版本建设任务。我不能依赖旧窗口记忆，必须先读取当前任务入口、
项目治理文档、相关专题材料和必要事实源。用户举例只是线索，不是完整范围。复杂任务
必须分步读取、分步写入、分段校验。遇到不确定项、路径缺失、范围冲突、实现困难或
风险升级，必须停下并列入待确认项。
```

### Step 1：加载硬规则

读取 `/home/king/rebuild/AGENTS.md`（平台名、版本号、硬规则、禁止事项）。

### Step 2：读取任务入口

读取 `/home/king/rebuild/交接/当前/` 下的最新交接材料，获取当前阶段、任务目标、执行边界。

### Step 3：读取项目治理

最小启动三件套（任何任务必读）：

```
/home/king/rebuild/文档/00-项目治理/00-项目总览.md          ← 项目当前状态
/home/king/rebuild/文档/00-项目治理/01-决策记录.md          ← 完整决策记录（D-010 含 R 阶段定义）
/home/king/rebuild/文档/00-项目治理/02-术语表.md            ← 术语与枚举定义
```

如任务涉及版本、目录、变更、交接、阶段计划或历史吸收，应继续读取：

```
/home/king/rebuild/文档/00-项目治理/03-版本与目录策略.md
/home/king/rebuild/文档/00-项目治理/04-变更控制规范.md
/home/king/rebuild/文档/00-项目治理/05-交接与报告规范.md
/home/king/rebuild/文档/00-项目治理/06-R阶段总计划.md
/home/king/rebuild/文档/00-项目治理/07-版本演进与历史资产吸收.md
/home/king/rebuild/文档/00-项目治理/文档地图.md
```

### Step 4：输出阶段判断

```
当前阶段：R?（子阶段：R?-?）
阶段目标：
允许事项：
禁止事项：
输入材料：
输出材料：
完成条件：
是否需要用户 Gate：
```

> 如果无法确定当前阶段，必须停止并输出待确认项。

### Step 5：建立事实源清单

列出本任务将读取的所有事实源（五层结构）：

```
## 本任务事实源清单
### L1 任务入口
### L2 项目治理
### L3 专题材料
### L4 产物/证据
### L5 历史参考（如需要）
```

---

## 1. 任务类型路由表

根据任务类型选择对应工作流（详细流程见 [references/workflows.md](references/workflows.md)）：

| 任务类型 | 工作流 | 关键约束 |
|---------|--------|---------|
| 文档正式化 | workflows.md §1 | 单份处理、四步分析、生成正式候选 |
| 交接材料编写 | workflows.md §2 | 自包含、13 必含项、写入交接/当前/ |
| 历史版本参考 | workflows.md §3 | 只读、记录吸收/不吸收、禁止复制 |
| 施工前计划 | workflows.md §4 | 必须先输出计划、检查停止条件 |
| 代码施工 | workflows.md §4 | 三步法：自有方案→历史吸收→完善施工 |
| 文档审核 | workflows.md §1 | 对照检查维度表逐项审核 |
| 测试与验收 | workflows.md §4 | 必须有证据、结论区分四档 |

---

## 2. 文档与产物完整流转规则

### 2.1 文档生命周期（状态机）

```
                    ┌──→ rework（返工）──┐
                    │                    │
产物/草稿/ → Agent评估完善 → 文档/ (待审核) → 用户审核 → accepted
                              │                           │
                              └──→ blocked（阻塞）        └──→ superseded（被替代）
```

**状态枚举**（详述源：`02-术语表.md`）：

| 状态 | 含义 | 谁可设置 |
|------|------|---------|
| `draft` | 草稿，尚未正式化 | Agent |
| `待审核` | 正式候选，等待审核 | Agent |
| `accepted` | 审核通过，可作为事实源 | 用户 |
| `superseded` | 已被后续版本替代 | 用户/Agent |
| `rework` | 需返工 | 用户 |
| `blocked` | 阻塞，等待条件 | Agent |

### 2.2 交接材料流转

```
交接/当前/  →  本轮完成  →  交接/已完成/<轮次>/
    │                           │
    ├── 阶段总报告（追加式）     └── 保留：原始文件、总报告、总表、待确认项
    ├── 阶段总表（同步更新）
    ├── 变更日志
    └── 交接提示词
```

**交接材料不自动成为项目文档**。如需晋升为正式文档，必须走 §2.1 的正式化流程。

### 2.3 产物流转

```
产物/草稿/  →  Agent 处理  →  产物/已完成/  或  晋升至 文档/ (走正式化流程)
```

### 2.4 更新时机规则

| 事件 | 必须更新的内容 |
|------|--------------|
| 任务开始 | 读取交接/当前/，确认阶段和事实源 |
| 每份文档处理完成 | 追加阶段总报告 + 更新总表 |
| 阶段完成 | 归档交接材料至交接/已完成/ |
| 阶段切换 | 创建新交接提示词，写明新阶段的输入/输出/禁止/验收 |
| 决策变更 | 先按变更控制规范提出待审核修改建议；经用户确认后更新 01-决策记录.md 和变更日志 |
| 文档 accepted | 更新文档地图 |
| 发现待确认项 | 登记到总报告 + 总表 |
| 本 Skill 发现不足 | 按 §8 自维护规则处理 |

### 2.5 禁止的流转操作

- 草稿直接搬入 `文档/`（不走正式化流程）
- 交接材料直接标为 accepted
- 产物自动视为事实源
- 跳过总报告/总表更新直接标记完成
- 归档后删除原始交接材料（应保留索引）

---

## 3. 通用执行流程

任何任务必须：

```
1. 识别任务目标、阶段、边界（§0-§1）
2. 确认应读取的事实源（§0 Step 5）
3. 分步读取文件
4. 列出事实、推断、建议（三者必须分离，见 AGENTS.md §6.1）
5. 识别风险和待确认项（§6）
6. 给出执行计划
7. 按计划分步写入
8. 分段校验（每段自检：事实源完整？遗漏裁决？旧版本污染？未确认推断？）
9. 输出完成记录
10. 更新交接材料、总报告、总表或对应状态文件（§2.4）
```

### 3.1 单一事实源 + 引用（防漂移）

- Manual/Plan/Auto、风险级别 L0-L5、材料身份、禁止事项明细等 → **只引用详述源**（`01-决策记录`/`02-术语表`/`AGENTS.md`），不整段复制
- 修改时只改详述源，其余位置确认仍为引用
- 发现两处以上全文复制 → 收敛或登记待确认项

---

## 4. 施工安全网

### 4.1 必须先输出施工计划

涉及代码、前端、后端、API、测试、运行时、模型、集成、资源等施工时，**必须先输出施工计划**（模板见 [references/templates.md](references/templates.md#3-施工计划模板)）。

### 4.2 必须停止的情形

遇到以下任一情况**必须停止**，登记待确认项：

- 当前阶段不允许施工
- 事实源不足
- 路径不清
- 技术栈变更未确认
- 涉及密钥或 L5 高风险权限
- 会改变已确认决策
- 会绕过 LangGraph 主编排
- 会复活旧 RunEngine 或旧流程
- 会把草稿/产物直接晋升为正式文档
- 违反模块建设三步法（AGENTS.md §7.1）

### 4.3 技术栈硬约束

LangGraph 是主编排底座（D-037）。变更技术栈须按 C4 级别提交申请（模板见 `04-变更控制规范.md` §6）。薄编排/NIH 约束见 AGENTS.md §13.0（引用自 AGENTS.md 原文，此处不复制）。

---

## 5. 安全与密钥

### 允许

识别是否存在 `.env`、配置文件、密钥文件、Provider 配置、认证配置。

### 绝对禁止

禁止输出或写入：Key、Token、Secret、Password、`.env` 明文、API Key 明文、认证凭据。

禁止写入目标：报告、日志、Trace、Audit、前端页面、截图说明、交接材料、正式文档、Git 提交。

### 安全记录写法

```
发现某类配置文件，内容已脱敏，需用户或本地环境自行确认。
```

---

## 6. 风险与待确认项

### 6.1 必须停止的情况

- 任务目标不清 / 阶段不清 / 事实源冲突 / 路径缺失
- 范围争议 / 实现困难 / 技术栈变更
- 安全或权限风险（L4/L5）
- 历史版本与当前决策冲突
- 需要用户 Gate
- 可能导致返工或污染正式事实源

### 6.2 待确认项模板

```markdown
## 待确认项 Q-<序号>
问题：
背景：
可选方案：
建议方案：
建议理由：
风险：
如果不确认的影响：
需要用户裁决：是
```

**禁止写**：`后续再说 / 先占位 / 暂时跳过但标记完成 / 后面补齐`

---

## 7. 完成自检

任何任务结束前逐条自检（与 AGENTS.md §11 对齐）：

```
1. 是否加载并遵循了本 Skill？
2. 是否识别了当前阶段和任务类型？
3. 是否读取了必要事实源？
4. 是否区分事实、推断和建议？
5. 是否执行了分步读取、分步写入、分段校验？
6. 是否更新了必要的文档、交接材料、总报告或总表？
7. 是否存在待确认项？
8. 是否存在安全或密钥风险？
9. 是否误用历史术语或旧版本事实？
10. 是否把草稿、产物、证据、正式文档混淆？
11. 是否可以交给用户审核？
```

---

## 8. 本 Skill 自维护规则

### 8.1 版本信息

- **适用平台**：rebuild V26.1.1
- **当前阶段快照**：R4 进行中；仅作提示，实际以 `交接/当前/` 最新材料为准
- **本 Skill 路径**：`skills/R-建设执行/rebuild-work-guidelines/SKILL.md`

### 8.2 更新触发条件

| 触发事件 | 更新内容 | 更新人 |
|---------|---------|--------|
| 阶段切换（如 R4→R5） | 更新 §8.1 当前阶段引用 | Agent |
| 新决策改变工作流 | 更新对应工作流章节或 references | Agent |
| 发现流程缺陷 | 追加到对应 references，登记待确认项 | Agent |
| 硬规则变更 | 不修改本 Skill——修改 AGENTS.md | 用户 |
| 模板不足 | 追加到 references/templates.md | Agent |

### 8.3 不更新原则

- **硬规则不改本 Skill**：硬规则（禁止事项、红线、安全规则）的详述源是 AGENTS.md。Skill 只引用，不复制定义。硬规则变更时改 AGENTS.md，Skill 的引用自动跟随。
- **版本号不写死到 Skill 正文**：V26.1.1 仅在 §8.1 声明一次，正文不重复。
- **阶段状态不固化**：§8.1 仅给快照提示，Agent 必须以 `交接/当前/` 实时状态为准。

---

## 9. 详细参考

以下文件按需读取：

| 文件 | 内容 | 何时读取 |
|------|------|---------|
| [references/workflows.md](references/workflows.md) | 完整工作流（文档正式化/交接材料/历史参考/施工前计划/安全） | 执行具体任务时 |
| [references/templates.md](references/templates.md) | 全部输出模板 | 需要模板格式时 |

**Agent 任务开始声明**：

```
我将遵循 AGENTS.md 硬规则，使用 rebuild-work-guidelines Skill 作为执行流程约束。
```
