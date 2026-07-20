# 参考轨吸收与目标驱动阶段循环（stage-agent-loop）

> references 补充材料 | 触发：R17.4 参考轨复核 + AGENTS.md §2.3 落地（2026-07-20）
> 定位：把**本地轨（Claude Code 真实命令轨）与在线轨（Copilot 参考规格轨）已经做对的东西**沉淀为可复用执行方法，防止"别人做对了、我们没吸收"。硬规则详述源是 AGENTS.md §2.3，本文件只讲"怎么做"。
> 何时读取：施工/设计/验收任一 P 阶段（P0-P6）执行链路时；复核参考轨时；判断"某逻辑该确定性还是该 LLM"时。

---

## 1. 一句话结论（吸收自两条参考轨）

两条参考轨本身都是 **LLM Agent 调工具/命令 + 推理**来达成阶段目标的：
- **在线 Copilot 轨**：为每个 P0 微步定义"目标 / 通用框架层要求 / 样本实例输入 / 参考命令 / 期望 Evidence / Artifact / Trace / Gate 逻辑 / JSON 结构"——是**规格**。
- **本地 Claude Code 轨**：在临时目录**真跑** `git clone`/`find`/`grep`/`iconv`/`wc`，观察真实输出，**还当场抓到 3 处与静态报告不符的计数**——是**活样本**：一个带 shell 的编码 Agent 跑采集命令、推理判断、产出报告。

**这正是 rebuild 的 Node Worker Agent 该有的样子。** 不是写死的 handler，而是"目标 → 计划 → 调工具采集 → 推理 → 验收"。

## 2. 必须吸收的分层红线（两条轨都白纸黑字立了）

> "**通用必需步骤可沉淀为平台规则；样本实例（53 表 / WebForms / openEuler / 国产库 / SQL 方言 / 具体文件名 / 具体命令等）不得硬编码到平台 handler、Project schema、前端固定字段或默认 Gate 规则中**，随目标项目替换。"

| 层 | 属性 | 落地方式 |
|----|------|---------|
| **通用必需层** | 稳定、平台级 | 沉淀为**阶段目标 + 验收标准锚点 + 证据要求 + Gate 逻辑**（固定骨架） |
| **样本实例层** | 随项目变 | 由 **Node Worker Agent 按项目在过程中生成**（查什么文件、跑什么命令、什么栈、什么目标环境）——**严禁写进代码常量/schema/前端固定字段** |

> R17.4-3 D 轨实证：当前 P0/P1 代码恰恰违反了这条——把样本层的识别逻辑（文件名表、NuGet 解析、大小写表、主语言加权）烧进了确定性 `FullStackProfiler`/`SourceMaterializer`，导致换项目失效（.NET 主语言误判 JS、依赖 0/49、漏 Web.config）。这就是"别人（参考轨）早写明了红线，实现没吸收"。

## 3. 确定性的正确位置（唯二）

```
① 采集 gather：git clone / 解压 / 列文件 / 读内容 / 跑命令 / 计数 / 脱敏扫描
② 验证 verify：编译 / 跑测试 / diff / 行为等价检查（迁移正确性的 ground truth）
```

识别、理解、规划、解读、风险研判、**验收判定**——全部 LLM 推理。确定性能力以 Skill/Tool/Expert Agent/MCP 资源被 Agent 调用（D-063），不做阶段主线。

> 判据（Python helloworld → Rust）：任何按场景写死的规则都不生效——规则是按场景枚举的长尾，只有"目标固定 + Agent 推理"才泛化。若你在为某个栈/某种写法**新增一条识别规则**，停下——那属于 LLM 的活。

## 4. 目标驱动阶段循环（每个 P 阶段统一骨架）

复用既有原语（`stage_loop.py` StageLoop / `review_pass.py` / `node_loop.py` NodeLoop / TaskGraph），**不另造**（D-065）：

```
① 接受目标      ← 平台注入固定 goal + acceptance_criteria（锚点）
② 装配上下文    ← context_assembler：源码事实 + 上阶段产物 + 资源(三阶优先级) + Skill
③ 识别计划      ← Node Worker Agent(LLM) 自主生成计划（含拆分决策，见 §5）
④ 执行工具      ← execute_fn = Node Worker Agent：调 ModelGateway + Tool/Skill/MCP +
                  ExecutionProvider（或委托外部 CodingAgent D-078）；确定性工具在此被"调用"
⑤ 自检          ← self_check：Agent 初步自查
⑥ 切换独立验收  ← 独立 Acceptance Agent(LLM，D-082)：基于目标 + 证据自适应裁量是否达标
                  （验收判定是 LLM 的活，不是确定性检查表；确定性只在其调用的验证工具层）
⑦ 反馈路由      ← 通过→出阶段；不通过→返工(ReviewPass ≤N，回③/④)；超轮/高风险→升级 Gate
⑧ 用户 Gate     ← 晋级前必须用户拍板（D-023）
      └────────► 下一阶段 ① / 本阶段返工再循环
```

## 5. 任务拆分与子循环（吸收自 NodeLoop split_decision）

```
计划级拆分（P3）：Agent 规划把目标拆成 Task Plan(Batch) → 生成 TaskGraph（节点+边策略，必生）
执行级拆分（P4，NodeLoop 第4步 split_decision）：
  inline / serial / parallel / hybrid / nested_loop（子任务本身是完整闭环 → 发起子循环，递归套用 §4）
```
约束：子循环复用同一 ReviewPass 内核；产物逐级向上打包 + 每级独立验收；拆分不得越过用户 Gate；高风险动作无论哪层都强制 Gate。

## 6. P0-P8 通用目标清单（吸收自参考轨 P0-1~P0-8，去样本值、只留通用目标）

> 下列是**通用目标锚点**（可沉淀），执行时由 Agent 按项目决定"怎么查、查什么文件、跑什么命令"。样本值（MicroOA 的具体文件/计数/目标环境）**不写死**。

| 微步 | 通用目标（固定锚点） | Agent 灵活项 | 确定性工具 |
|------|---------------------|-------------|-----------|
| 项目源接入 | 记录源 URL/方式/物化结果 + Evidence + Trace；失败诚实 blocked 不伪装 | 选采集方式、判断来源可信/权限 | clone/解压/拷贝 |
| 仓库元数据 | commit/branch/remote/status/evidence_refs | 判断版本基线是否可用 | git 命令 |
| 关键文件识别 | 识别入口/配置/依赖清单/DB 脚本/README；缺失登记 gap | 判断哪些是"关键"（按项目栈） | 列目录/读文件 |
| 迁移目标/假设登记 | 登记用户目标，标 source=user_intent/confidence=unverified/decision_status=pending_p2 | 归纳用户意图，不裁决路线 | — |
| 原始运行环境线索 | 识别语言/框架/运行时/Web宿主/DB/依赖/项目类型/部署线索 | **推理**判定（泛化任意栈） | grep/读配置(供参考) |
| 数据库入口识别 | 找 DB 脚本/迁移/schema/连接入口，记路径/方言/编码/规模；字段级留 P1 | 判断方言/编码/入口 | file/wc/iconv/grep(采集) |
| 可用性等级判定 | A/B/C 判定必须有证据链；不因当前环境不可跑就判 C；无实跑证据不判 A | **推理**判定 + 理由链 | 汇总前序 evidence |
| P1 建档任务生成 | 生成可执行 P1 任务清单，防 P1 空转 | 按项目结构生成任务 | 结构扫描(采集) |
| P0-Gate | 用现有 Gate 机制，不发明新类型；列进入 P1 条件；失败给 reason | 判断是否建议 approve | — |

> 关键：这些是**目标**，不是**实现**。Agent 达成目标时的具体命令/文件/栈由它按项目生成——与在线轨"参考命令"、本地轨"真实命令"一致：命令是示例，目标是规格。

## 7. 反面清单（AGENTS.md §10-25/26 的执行细化）

- ❌ 为某栈/文件名/方言新增确定性识别规则 → 应交 Agent 推理
- ❌ 把样本值（53 表 / net48 / openEuler / 具体文件名）写进 handler/schema/前端/默认 Gate
- ❌ 把"识别/规划/验收判定"做成确定性主线或检查表
- ❌ 无有效 Key 时用确定性规则冒充 P 阶段完成（应诚实 blocked，不兜底）
- ❌ 只吸收参考轨的"样本值"，却漏掉它的"通用目标 + 分层红线 + Agent 执行范式"

---

*stage-agent-loop：R17.4 吸收本地/在线参考轨 + AGENTS.md §2.3。核心：参考轨是"LLM 调工具+推理"的范式与通用目标规格；样本值不写死；确定性只在采集与验证。*
