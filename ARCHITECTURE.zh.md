# ARCHITECTURE —— rebuild 是怎么实现的

> **语言 / Language**：中文（当前页，**权威版本**） · [English](./ARCHITECTURE.md)
> 本仓库对外文档以**中文为权威版本**：英文版是中文版的翻译，两版表述冲突时**以中文版为准**。
> 语言变体清单见 [`.i18n.yaml`](./.i18n.yaml)。本页内的文档链接指向中文版。

> 平台：rebuild · 版本：V26.2（在建） · 许可：[MIT](./LICENSE)
> 配套阅读：[VISION.md](./VISION.zh.md)（为什么存在）、[ROADMAP.md](./ROADMAP.zh.md)（下一步）、[README.md](./README.zh.md)（如何跑起来）
> 本文档面向想读代码、想改代码或想判断这套设计是否可信的外部读者。文中的代码坐标是写作时的实测位置，行号会随迭代漂移，符号名相对稳定。
> **写法约定：每写一条机制，同时写它当前的实现边界。核实不到的不写。**

---

## 1. 一句话架构

**rebuild 是一个用 LangGraph 编排的、按 P0–P6 阶段推进的目标驱动 Agent 系统：每个阶段的目标、验收锚点、用户 Gate 和证据契约是固定的，达成目标的计划与执行由 LLM 自主生成；确定性代码只负责两件事 —— 采集事实与验证结果。**

---

## 2. 分层

```
┌─────────────────────────────────────────────────────────────┐
│ 前端  React + Vite + TypeScript                              │
│   项目概览 / ProjectWorkspace / 阶段页 P0-P6 / Gate 裁决      │
│   模型与资源可视化（14 种真实能力状态标记）                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP /api  +  SSE 事件流
┌──────────────────────────┴──────────────────────────────────┐
│ API 层  FastAPI（32 个路由模块，统一挂 /api 前缀）              │
├─────────────────────────────────────────────────────────────┤
│ 编排层  LangGraph StateGraph                                 │
│   p0_work → p0_gate → p1_work → p1_gate → … → p6            │
│   checkpoint（SqliteSaver）/ interrupt / resume              │
├─────────────────────────────────────────────────────────────┤
│ 循环层  StageLoop / NodeLoop / ReviewPass / TaskGraph        │
│   WorkAgent（施工）与 ValidationAgent（验收）身份分离          │
├─────────────────────────────────────────────────────────────┤
│ 能力层（被 Agent 按需调用，不作为流程主线）                    │
│   ModelGateway → LiteLLM 适配                                │
│   ToolRegistry（工具分派 + 风险分级 + Gate）                  │
│   ExecutionProvider（本地/容器/工具链容器/工作区/远程 SSH）    │
│   HookEngine · Skill/Resource Loader · ContextAssembler      │
│   场景包 Loader · MCP · 能力探测                              │
├─────────────────────────────────────────────────────────────┤
│ 记录层  Evidence（文件）· Trace（内存+jsonl）· Audit（同）      │
│         Artifact · Gate（数据库表 p_gate）                     │
├─────────────────────────────────────────────────────────────┤
│ 存储层  SQLAlchemy 2.0 + Alembic                             │
│         SQLite（默认）/ PostgreSQL（可选）· 项目工作区文件系统  │
└─────────────────────────────────────────────────────────────┘
```

后端约 90 个 service 模块、30 张数据库表。规模不小，但主干只有上面这几层 —— 大部分模块是"能力"，不是"流程"。

---

## 3. P0–P6：目标驱动的 Agent 循环

这是整个平台最核心的设计约束，也是最容易被误解的部分。

### 3.1 每个阶段"固定"的只有四样

| 固定项 | 含义 |
|---|---|
| 阶段目标 | 这个阶段要达成什么 |
| 验收锚点 | 判定达成与否的相对固定标准 |
| 用户 Gate | 晋级到下一阶段需要人授权 |
| Artifact / Evidence 契约 | 产出物与证据的结构约定 |

**除此之外的一切 —— 计划怎么定、调用哪些工具与命令、任务怎么拆、产物写什么内容 —— 都由阶段内的 Agent（LLM）在过程中自主生成**，不写死为确定性规则或流程主线。

代码里能看到这条约束被明确写下来。以 P0–P3 的共享循环为例（`backend/app/services/stage_agent_loop.py` 模块头）：

- 确定性预采集的"事实包"只作为**提示材料**（help），**不是读取上限** —— Agent 可以按需读更多真实源码，多轮进行；
- 阶段的**结构化输出契约不变**，这个 helper 只改变文本"怎么产生"（多轮工具循环 vs 单次调用），从不改变契约；
- **不做阶段级工具白名单** —— 加载完整可用工具集，工具安全完全由 `execute_tool` 内部的 L0–L5 分级承担（L3 及以上触发审批 Gate）；
- 无可用模型或全部尝试失败 → **返回诚实的 failed 结果并附尝试链，绝不规则兜底、绝不伪造完成**。

循环轮数有界（当前上限 6 轮工具调用，`_MAX_TOOL_ROUNDS`）。同一"工具名 + 参数"组合连续调用达到阈值时，会提醒模型换方法或收尾 —— **只提醒，不拦截**。

### 3.2 循环骨架

平台复用四个既有骨架，不为每个阶段另造一套（`backend/app/graph/stage_loop.py`、`backend/app/services/node_loop.py`、`review_pass.py`、`task_graph_service.py`）：

```
读上下文 → 阶段计划 → 执行 → 自检 → Review Pass
   → 返工（≤N 轮） → 超限或高风险 ⇒ 升级为 Gate
   → 通过 ⇒ 进入晋级 Gate
```

### 3.3 施工与验收身份分离

验收不是"施工方自己再看一遍"。`backend/app/services/validation_agent.py` 是**独立身份 + 独立上下文**的验收主体：

- 独立 agent 身份、**独立数据库会话**，不复用施工 Agent 的会话；
- 输入**只读落盘产物** —— 只消费落盘引用，并从磁盘重新读取内容再验，**不读施工 Agent 的进程内推理字段**；
- 不合格 → 触发返工轮，施工 Agent 重跑；**合格才创建用户 Gate**。

**边界**：这套分离在阶段验收层成立；它约束的是"谁来判定"，不代表判定本身一定正确 —— 判定质量仍取决于模型与证据质量。

---

## 4. 确定性只用于两处

这是一条硬约束，不是风格偏好：

```
确定性代码只做两件事：
  ① 采集 —— clone / 列文件 / 读内容 / 跑命令 / 计数 / 脱敏扫描
  ② 验证 —— 编译 / 测试 / diff / 等价检查

识别、理解、规划、解读、风险研判、验收裁量 —— 一律 LLM 推理。
```

推论有两条，都能在代码里对上：

1. **确定性能力以 Skill / Tool / Expert Agent / MCP 的形式被 Agent 调用**，不做阶段主线。
2. **场景知识、维度知识写进 Skill 正文，Python 侧不做场景 if/elif 分支**。

**边界**：这条约束防止的是"用规则冒充智能"。它的代价是 —— 阶段结论的稳定性取决于模型，同一个项目两次跑的措辞和细节不会完全一致。结构化契约字段是稳定的，自然语言解读不是。

---

## 5. LangGraph 主编排

LangGraph 是**唯一**主编排底座，不引入第二个编排框架。

`backend/app/graph/graph.py` 组装 `StateGraph`：P0 到 P6 每个阶段各有两个节点 —— `{stage}_work`（施工）与 `{stage}_gate`（晋级门）。

- **状态持久化**：编译时挂 async SqliteSaver checkpointer，`thread_id == run_id`。
- **中断与恢复**：晋级 Gate 用 `interrupt()` 暂停等用户裁决，**进程重启后仍可 resume 续跑**。这条不是设计意图而已 —— 曾在一次意外杀掉后端进程后，图状态（阶段状态、产物、全部 Gate）完好，单次 resume 即重跑成功。
- **恢复与重试**：`backend/app/graph/recovery.py`、`stage_retry.py` 承担冲突恢复与阶段重试。

**边界**：
- 编排状态的默认存储是本地 SQLite 文件，**不是为多实例并发设计的**。
- `GET /graph/state` 的 `paused` 字段当前恒为 `False`（中断态未持久化到该字段），**不可用作"是否暂停"的判据**；判断请以 Gate 记录为准。
- 长时任务只有心跳注册表可以**发现**挂起（`backend/app/services/heartbeat_service.py`），它**从不重试、不恢复、不接管** —— 这是模块自述的设计边界，不是缺陷描述。

---

## 6. ModelGateway：统一模型接入

所有模型调用必须经 `backend/app/services/model_gateway.py` 的 `ModelGateway`（`call()` / `call_stream()`），再由 `backend/app/adapters/litellm_adapter.py` 的 `LiteLLMAdapter` 落到具体 Provider。

**硬约束**：不直连 Provider、不硬编码模型名与 endpoint。策略、档位、Provider 走配置与动态解析。

**无有效凭据时的行为**（这是 capability-first 在模型层的落点）：

| 层次 | 行为 |
|---|---|
| 预检 | `stage_model_readiness()` 在**一次网络调用都不发生**的情况下就能诚实判定不可用，候选链给出 `not_configured` / `credential_missing` / `capability_unmet` / `candidate_ready` |
| 运行期全失败 | 强制中断，返回 `blocked` + "无任一已配置且具备有效凭据的模型"，并写审计 |
| 各阶段主链路 | P0/P1/P2/P3、技术选型、验收基准捕获等处返回 `status="blocked"` + `reason="no_model_key:…"`，代码注释直写"不降级为规则识别 / 规则评估" |
| 图层 | 阶段状态置 `blocked`，另建一个中断 Gate + 审计 |
| **advisory 层例外** | P5/P6 的 LLM 建议层在无模型时诚实标 `skipped` + `evidence_gap="llm_advisory_unavailable"`，**非阻断** —— 因为它本来就不是门禁 |

**边界**：凭据只从环境变量读取、加密存储，接口只返回 `credential_status`（`configured / missing / invalid / redacted / not_checked`），**不回显明文**。平台不内置模型，不代管额度。

---

## 7. 能力接缝：一项能力必须三角色齐备

这是平台用来防止"看起来完成"的分析单元。一项能力 = **接口声明方** + **实现提供方** + **使用消费方**，三者缺一不可：

```
接口声明方   定义调用契约 —— 函数签名 / Protocol / 抽象基类 / 消息格式
实现提供方   真正执行该契约行为的具体代码
使用消费方   在真实运行路径上调用该接口的代码
             —— 不是文档或注释里"将调用"，而是 grep 可实测到的真实调用点
```

**两类禁止**：只加实现而没有任何真实调用点（等价于"看起来完成"而实际不生效）；只定义接口而没有真实实现（接口空转、调用即报错或落到 stub）。任一角色缺失，验收时**不得记为已实现**。

平台已登记的四组接缝（三列均为读代码实测的真实坐标）：

| 接缝 | 接口声明方 | 实现提供方 | 使用消费方 |
|---|---|---|---|
| ExecutionProvider | `services/execution_provider.py` 的 `ExecutionProvider` Protocol + 工厂 `get_execution_provider()` | **5 个**实现：本地子进程 / 容器 / 工具链容器 / 工作区本地 / 远程 SSH | 工具分派、工作区路由、P5 验证命令、验收基准 |
| ModelGateway | `services/model_gateway.py` 的 `call()` / `call_stream()` | `adapters/litellm_adapter.py` | 各阶段循环、P4 执行 worker、P3 规划、自动评审、独立验证 Agent、模型自测路由 |
| Registry 驱动的 Tool | `services/tool_registry.py` 的 `load_schemas()` + `execute_tool()` | 按写入范围分派的内部执行函数（读 / 工作区写 / 生成补丁 / 应用补丁 / 委托 Provider / 委托 MCP / builtin） | 通用 Agent 循环、阶段 Agent 循环、P4 执行 worker |
| Registry 驱动的 Hook | `services/hook_engine.py` 的 `run_hooks()` | `_BUILTIN_HOOKS` —— **当前仅注册 1 个**：`pre_write_policy`（拦截写源码目录、拦截写入疑似明文密钥） | **唯一消费方**：`execute_tool()` 的两处调用（工具调用前，可 block；工具调用后，advisory） |

**边界（重要）**：Hook 体系**定义了 10 个挂点，当前只有工具调用前后 2 个真正接线**，其余 8 个在后端全目录零命中 —— 已定义未接线，**不得读作已生效的安全控制点**。

---

## 8. 执行与隔离

### 8.1 五种执行提供方

`ExecutionProvider` 是 Tool、命令、MCP、外部 Agent 的统一执行入口，受 Hook + Policy + Gate 约束。当前五种实现：本地子进程、容器、**工具链容器**（按需拉取 SDK 镜像做真实构建）、工作区本地、远程 SSH。

### 8.2 源码只读与明文密钥拦截

**两类写入拦截都有不可关闭的代码层防护，但两者的边界性质不同。**

**源码只读。** 改造对象的源码目录是只读的。除了 Registry 驱动的 Hook，`backend/app/services/workspace_mediator.py` 还有一层**独立、无条件**的防护 —— 即使 Hook 被关掉，写源码目录仍然进不去。

**明文密钥内容级检测。** 同样有一层不可关闭的防护：`backend/app/services/tool_registry.py` 在 Registry hook 分派**之前**无条件调用 `hook_engine.enforce_pre_write_policy()`，**不读** `ResourceEntry.enabled`，因此禁用那条 Hook 记录也关不掉它。这条强制检查还是 **fail-closed** 的 —— 检查本体自身抛异常时按 `block` 处理并以 ERROR 发声，而不是 fail-open 放行。它委托的是与 Registry Hook **完全相同**的检查体（`_impl_pre_write_policy`），所以两个调用点不可能行为漂移。此外，可关闭该 Hook 记录的三个入口也各自补上了审批 Gate。

**边界（如实标注 —— 差异在检测完备性，不在层数）**：

- **源码只读是结构性路径判定** —— 写不写得进某个目录是确定的，在其范围内是完备的。
- **明文密钥检测是模式匹配** —— 它**无法声称覆盖全部凭据形态**。模式集已收敛为单一事实源（`security_authorization.contains_secret()`），历史上正是因为存在一份私有副本而漏掉了一整类 URL 内嵌凭据形态（`scheme://user:pass@host`），现已合并为单一实现，并有一条**结构性回归测试**锁定"不得再出现第二份模式副本"。但"模式集当前够用"与"模式集完备"是两回事：**新形态的凭据仍可能不被识别**。请勿把这层防护当作可以把密钥写进产物的许可。


### 8.3 子进程与容器

- 子进程超时后的进程组终止逻辑收敛在 `backend/app/services/subprocess_runner.py` 一处，避免两处同类实现漂移。
- 容器执行**默认关闭**，需显式 opt-in 并挂载 Docker socket。这个动作等同把宿主 Docker 控制权交给后端进程，属高风险，仅建议在可信单机环境启用。
- 容器内临时文件权限当前为 `0755`，是**有明确理由的刻意放宽**（容器以非 root 用户运行，需可遍历可读）。收紧它需要换传参方案，属独立设计变更。

---

## 9. L0–L5 风险分级与 Gate

### 9.1 风险词表

```
L0 只读参考       无破坏性风险，不需额外授权
L1 方法调用       纯函数、分析脚本、生成报告、跑测试
L2 受控执行       本地命令、限定范围文件写入、本地 Git
L3 外部系统写     远程调用、外部 API 写、数据库变更
L4 系统级操作     系统配置、服务启停、权限变更
L5 高风险         必须用户 Gate
```

### 9.2 三处判定，共用同一套词表

| 判定点 | 依据 | 后果 |
|---|---|---|
| 工具执行前 | 工具注册时的**静态风险位**（覆盖完整 L0–L5） | L3 及以上必须先过审批 Gate；已批准则重新分派，避免双重门 |
| 命令内容 | `execution_provider._classify_risk()` + DENY 名单与正则 | 命中 DENY → 直接 L5，执行前 block + 建 Gate |
| 执行模式 | `mode_policy.authorize_action()` | L4 及以上**永不自动放行**；阶段晋级 Gate 无论何种模式都需用户确认 |

DENY 层是子串名单 + 编译正则双通道（正则优先、子串兜底），拦截包括 fork bomb、`chmod 777 /`、`chown -R root /` 等。执行环境变量会被清洗，内嵌在 URL 里的凭据会被剥离。

### 9.3 Gate 的实际类型

Gate 落库为 `p_gate` 表，代码中真实创建的类型至少包括：`stage_promotion`（阶段晋级）、`plan_presentation`（计划呈现）、`source_pending`、`model_unavailable`、`action_approval`（工具动作审批）、`l5_high_risk_command`、`desensitization_release`（交付脱敏放行）、`community_resource_introduction`、`manual_confirmation`。

P1→P2 的晋级 Gate 有专属副作用：批准时才把技术选型固化落库。晋级还有硬校验 —— 阶段没有真实产物时，晋级请求会被拒（HTTP 422），防止空壳晋级。

**边界（诚实标注）**：
- **L5 命令会建 Gate，L4 命令当前不建 Gate**（L4 是否需要 Gate 属于范围之外的扩展）。
- 运行期命令内容的自动分类**当前只产出 L1/L3/L4/L5 四级**，L0 与 L2 已定义但该实现未使用。这不影响现有 Gate 的触发；但在远程 SSH 路径上，分类结果会被喂给模式策略并据此决定是否放行，所以**改动分类分支属于授权阈值变更**，不能顺手改。
- 常量文件里的 Gate 类型清单在代码中**没有强制校验点**，`gate_type` 实为自由字符串 —— 实际使用的类型比该清单更多。这是已知的实现不整齐处。

---

## 10. Evidence / Trace / Audit：三条独立记录

| 记录 | 回答什么问题 | 实现与载体 |
|---|---|---|
| **Evidence** | 这个结论凭什么成立 | `services/aet_service.py`；**文件系统** —— 项目工作区下的 `evidence/{evidence_id}.json`（工作区根目录可配置） |
| **Trace** | 当时到底发生了什么 | `core/trace_writer.py`；**内存环形缓冲（上限 10000）+ 项目工作区下的 jsonl 文件**；15 种轨迹类型 |
| **Audit** | 谁批准了什么、依据什么风险等级 | `core/audit_writer.py`；同上载体；13 种审计类型 |

三者都不是数据库表。Gate 记录上有 `artifact_refs` / `evidence_refs` / `trace_refs` / `audit_ref` 四个字段做双向绑定，裁决时写 `gate_decision` 审计并把审计 ID 回填到 Gate。

配套原则：

```
No Evidence, No Decision.
No Artifact, No Completed.
No Trace, No Trusted Result.
```

**边界（如实标注）**：
- Trace 写盘失败时会把 `persistence` 字段**诚实降级**为 `memory`；Audit 的同名字段目前**无条件**写成 `file+memory` 且写在文件落盘之前 —— 即项目 ID 缺失或写盘失败时该字段与事实不符。这是两个 writer 之间的不一致，属已知缺陷。
- 内存缓冲上限 10000 条，超出后旧记录被挤出；长期可追溯性依赖 jsonl 文件。

---

## 11. P5 验证：十个槽位与抗伪造门禁

`backend/app/services/p5_validation_plan.py` 把验证拆成十个槽位，分三类：

| 类别 | 槽位 | 说明 |
|---|---|---|
| **硬必需**（5 个） | 产出代码存在 / 补丁存在 / **P4 证据真实（sha256 前置）** / P4 摘要可解析 / P4→P5 Gate 已批准 | 缺任一项，阶段不得标完成 |
| **有条件必需**（4 个） | 构建验证 / 运行验证 / 测试通过 / 静态检查 | 环境具备则必须给真实结论；不具备则诚实 `evidence_gap` |
| **增强项**（1 个） | 源码未被修改的正向证据 | 不作门禁 |

**这套设计的要点是：好消息不能覆盖坏消息。** 已实证的一次运行里，构建槽位手握真实的"编译成功"结论，但因为硬必需的"P4 证据真实"槽位失败，阶段仍然判定**不可标记完成** —— 没有用"构建成功"掩盖证据基准失效。这是平台最重要的正面资产。

条件必需槽位只有在 `evidence_gap` **且经 Gate 明确接受风险**时才放行；出现"需用户输入"状态则一律不放行。

除十个槽位外，还有一个**非门禁**的维度能力分区（浏览器走查、行为等价、数据库核对、业务闭环、回归对比、性能基准等）：能力已接线但环境缺失时标 `evidence_gap` 并记"能力已具备、待环境真验"，**不参与门禁、不能翻转完成判定**。

### 11.1 真实构建结论如何取得

`backend/app/services/toolchain_resolver.py` 的探测是**只读的**（不拉镜像）。构建能力判定为 `available` **仅当**：Docker 可达 + 镜像已在本地 + 已发现构建对象 + 目标框架有映射。三种情况：

1. 宿主有 SDK → `available`；
2. 无 SDK 但工具链容器通道可用 → `available`（记录镜像引用与 digest）；
3. 其余 → **`evidence_gap` 并写明具体原因与预热命令提示，绝不伪造 `available`**。

执行侧还有一层区分：Docker 守护 / 镜像 / SDK 不可用被标为 `toolchain_unavailable`，**这不等于"构建失败"** —— 判定时它先于"退出码是否为 0"生效，落成 `evidence_gap` 而不是 `validation_failed`。

**边界**：容器构建成功只证明"可编译"。样本没有测试工程时，测试槽位诚实标"需用户输入"；无可跑应用时运行槽位标"不适用"。**编译通过不是业务等价的证明。**

---

## 12. 场景包机制

场景是**改造/迁移的类型**，不是固定枚举。

```
Project.scenario                    ← 自由文本 id（不是 Python Enum）
source/skills/scenarios/<id>/       ← 场景包目录，运行期扫描发现
  ├── SKILL.md                      目标态词表 / 迁移模式 / 常见陷阱
  ├── acceptance-anchors.md         该场景的验收锚点
  ├── risk-catalog.md               该场景的风险清单
  └── meta.yaml                     tier: typical | open；关联资源引用
source/skills/scenarios/_generic/   ← 兜底包：无专用场景包时的通用重构方法论
```

`backend/app/services/scenario_loader.py` 在运行期扫描目录（`list_scenarios()` / `resolve_scenario_pack()`）。**实测：后端持续运行、未重启的情况下新建场景包目录，场景立刻出现在接口返回中，且正文逐字一致；删除目录后立刻不再列出。** 前后对比 `*.py` / `*.tsx` 的改动集合完全为空 —— 新增场景确实是零代码改动。

当前内置三个典型场景包（`xinchuang_switch` / `modernization` / `porting`）加一个 `_generic` 兜底包。**典型场景不是全集** —— 平台支持"**包括但不限于**"这三类的场景；"典型"的含义是"配套资源相对更丰富"（各自带目标态词表、验收锚点、风险清单、选型候选与关联 Skill），而不是"平台只支持这三类"。用户可新建场景包，也可复制典型场景包后改写；场景包不是只读内置资产。

无专用场景包时走 `_generic` 并**明确标注"无专用场景知识"**，不静默套用某个既有场景的口径。

**边界（实测所得，重要）**：场景包内容确实进入模型上下文并被准确引用，但一次对照实验证明 —— 当场景包规则与显式填写的**迁移目标**冲突时，模型倾向于跟随迁移目标，并在理由中指出这一冲突、把"是否改挂场景包"交还用户裁决。**场景包不能替代把目标态写清楚。**

---

## 13. 上下文装配与预算

`backend/app/services/context_assembler.py` 按分层装配上下文（C0–C4：身份/场景、平台约定、Skill 正文、项目事实等），由配方（recipe）声明所需层与预算。

- 预算超限时按层裁剪，并输出装配清单（含预算规格与是否发生裁剪）。
- 这条链路曾经"只声明不消费" —— 现已真正接线：**实测某次真实运行中 Skill 正文近两万字符确实进入 prompt 未被预算挤出**。

**边界**：预算裁剪当前可能丢弃配方声明为必需的层而没有显著发声，这是已登记的改进项（见 [ROADMAP.md](./ROADMAP.zh.md)）。

---

## 14. 前端

React + Vite + TypeScript。核心页面是项目概览、ProjectWorkspace（IDE 式布局）、P0–P6 阶段页、Gate 裁决、模型与资源可视化。

约定：

- **14 种真实能力状态标记**（如 `real_available` / `real_limited` / `credential_missing` / `not_connected` / `mock` / `static_demo` / `blocked_by_policy` / `waiting_gate` / …），**颜色 + 中文文字双通道**，状态**来自后端，前端不猜**。
- `mock` / `static_demo` / `not_connected` 三者必须显式区分，不得混为"可用"。
- Key / Token 全程脱敏，前端不回显、不请求明文回传展示。
- 功能图标统一用线性图标组件，不用 emoji 当功能图标。
- 用户可见文案中文优先；技术标识符（provider id / model name / 字段名）保留原文。

**边界**：部分前端交互仍在打磨，少量页面的运行期联调证据不完整（例如缺浏览器级录屏证据）。界面上标为部分实现或未接入的能力，就是字面意思。

---

## 15. 技术栈、端口与目录

### 15.1 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · uv · SQLAlchemy 2.0 · Alembic |
| Agent 编排 | **LangGraph**（含 checkpoint / interrupt / resume） |
| 模型接入 | ModelGateway → LiteLLM 适配层 |
| 前端 | React · Vite · TypeScript |
| 存储 | SQLite（默认）/ PostgreSQL（可选） |
| 部署 | Docker Compose（含受控执行沙箱镜像） |

### 15.2 端口与目录

端口是单一口径，请勿在代码、配置、脚本或容器编排里另设：**后端 8000**（开发与容器一致）、**前端开发 5173**、**前端容器 8080**。

```
backend/     FastAPI 服务、Agent 编排、模型网关、数据模型与迁移
frontend/    React + Vite 前端
source/      平台运行期资源（Skill / 场景包 / 案例 / 资源清单）
deploy/      部署与执行沙箱构建上下文
scripts/     运维与维护脚本
community/   独立社区服务（默认不启动，定位与启用条件见 ROADMAP.md）
```

接口统一挂 `/api` 前缀（健康检查是 `/api/health`，不是 `/health`）。首次启动自动建表并写入种子数据；若实际库落后于迁移版本，启动时以 ERROR 明确报出而不静默处理。

---

## 16. 架构层面明确做不到什么

把散落在各节的边界集中在这里，便于一次读完。

| 做不到 | 说明 |
|---|---|
| **无人值守的全自动迁移** | 阶段晋级需用户 Gate，阶段晋级 Gate 在任何执行模式下都需用户确认。这是编排层的设计，不是待补的自动化 |
| **无模型凭据时继续工作** | 主链路诚实 `blocked`，不降级为规则引擎。硬约束，非配置项 |
| **无 Docker / 无 SDK 时给出真实构建结论** | 一律 `evidence_gap` 并写明原因 |
| **缺环境时给出验证结论** | 无浏览器 → 无走查证据；无远程主机 → 无远程执行证据；无测试工程 → 测试槽位"需用户输入"。**看到大量降级是设计如此** |
| **证明业务语义等价** | 平台只能证明可编译、测试通过、diff 可读 |
| **多实例并发 / 多用户** | 没有登录、账号与租户隔离；编排状态默认落本地 SQLite。**不要暴露到公网** |
| **自动接管挂起的长时任务** | 只有心跳可以发现挂起，不重试、不恢复 |
| **10 个生命周期挂点全部生效** | 只有工具调用前后 2 个真正接线 |
| **运行期命令自动分类出全部六级 / L4 命令建 Gate** | 分类当前只产出 L1/L3/L4/L5（L0/L2 已定义未使用）；命令级 Gate 当前只在 L5 建立 |
| **用 `paused` 字段判断是否暂停** | 该字段恒为 `False`，请以 Gate 记录为准 |
| **千文件级真实规模项目的端到端验证** | 当前基线上完成的完整端到端真跑，源码规模为 **5 个文件**（微型示例桩）；千文件级真实系统的阶段轨迹与执行阶段 PoC 产出于**更早的基线**。当前基线尚未做过千文件级完整端到端验证 —— 详见 [VISION.md](./VISION.zh.md) 第 7.6 节 |
| **开箱可用的发行物** | 无 PyPI 包、npm 包或公共镜像，需从源码构建 |

---

## 17. 想改代码的话

- 后端测试套件已达千级用例规模，可全量运行；改动请附测试。具体数字随每轮新增的锁定测试上升，此处不写死，口径见 [CONTRIBUTING.md](./CONTRIBUTING.md) §6。
- 单一编排底座是硬约束：**不引入第二个编排框架**，也不引入运行时的 TypeScript 依赖。
- 新增能力请按第 7 节的三角色检查自己：**接口、实现、真实调用点，缺一不算完成**。
- 新增能力若在某些环境不可用，请让它诚实降级（`blocked` / `evidence_gap` / `not_applicable`），**不要回退到 mock 或占位内容** —— 这会直接损害平台唯一真正的卖点。
- 详细开发约定见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

---

> 如果你发现本文档某条描述与代码实际行为不符，那是需要修正的缺陷，请提 issue —— 这类反馈的优先级高于新功能请求。
