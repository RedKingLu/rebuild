# 参与 rebuild 开发

感谢你考虑为 rebuild 做贡献。本文档说明**如何把开发环境跑起来**、**如何跑测试**、以及**提交前需要做什么**。文中每条命令都是本仓库实测可用的，遇到与实际不符的地方请开 issue 指出。

参与本项目即表示你同意遵守 [行为准则](CODE_OF_CONDUCT.md)。发现安全漏洞请**不要**开公开 issue，走 [SECURITY.md](SECURITY.md) 的私密渠道。

---

## 1. 项目是什么

rebuild 是一个**软件重构平台**。它把一个真实的代码仓库作为输入，由 AI Agent 驱动，走完一条从识别、选型、评估、执行到验证、交付的流水线（P0-P6 阶段），产出可被机器验证的重构结果。

**场景是开放的**。平台重点支持三类**典型场景**，配套资源相对更丰富：

| 场景 id | 含义 |
|---|---|
| `xinchuang_switch` | 信创切换 |
| `modernization` | 软件现代化 |
| `porting` | 软件移植 |

但平台**包括但不限于**这三类。任何重构 / 迁移类型只要满足三条准入判据——**① 有源代码 ② 有可判定的目标态 ③ 产出物可机器验证**——都可以加入，方式是**新增一个场景包目录**，不需要改任何 Python 或前端代码。场景包也允许你复制典型场景后自行改写。所以：**请不要把场景实现成固定枚举**（Python `Enum`、前端固定下拉常量、handler 里的 `if/elif` 分支）——这是本项目的硬规则，含此类改动的 PR 会被要求返工。

---

## 2. 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python **3.12+** / FastAPI / **uv**（包与虚拟环境管理）/ SQLAlchemy 2.0 / Alembic |
| 前端 | React / Vite / TypeScript |
| 主编排 | **LangGraph** |

**LangGraph 是唯一的主编排底座**，这是已裁决的架构硬规则。请不要引入第二个编排框架，也不要自建 Agent 框架来替代它。同样，**不要引入 TypeScript 运行时到后端**。

新增依赖前请先确认：能否用项目已有依赖（httpx、SQLAlchemy、Pydantic、PyYAML、LangGraph 等）或 Python 标准库实现。确实需要新增时，请在 PR 描述里说明理由。

### 端口标准（不要另设）

| 服务 | 端口 |
|---|---|
| 后端（dev 与容器一致） | **8000** |
| 前端 dev server | **5173** |
| 前端容器（nginx） | **8080** |

在代码、配置、脚本、`.env` 中**各自另设端口**是被明确禁止的。前端 dev server 已把 `/api` 反向代理到 `http://localhost:8000`（可用 `VITE_BACKEND_TARGET` 覆盖），所以前端代码用同源相对路径 `/api` 调用即可，不要硬编码后端地址。

---

## 3. 克隆后第一件事：装上运行时数据守护

```bash
python3 scripts/runtime_guard.py install
python3 scripts/runtime_guard.py snapshot
```

**为什么这不是可选项**：`backend/.data/` 下的运行库（`rebuild.db`、`graph_checkpoints.sqlite`）处于一种危险组合——它们**历史上曾被 git 跟踪**，而**现在被 `.gitignore` 忽略**。git 对"未跟踪且未忽略"的文件有保护（checkout 会拒绝覆盖），但对**已被忽略**的文件**不加任何警告直接覆盖或删除**。因此 `git checkout <旧提交>`、`git merge --ff-only`、`git reset --hard`、rebase 都可能把你的真实运行库替换成历史小版本，或者在切回时把它删掉。这在 2026-09-03 已实测发生过一次（12.5MB 的 `rebuild.db` 被 FF 删除）。

守护脚本把受保护文件滚动快照到**仓库之外**的目录（放仓库内即便被忽略也会被 `git clean -xdf` 一并删除），并把体检挂到 git 钩子上。

常用命令：

```bash
python3 scripts/runtime_guard.py status                 # 看快照与当前状态
python3 scripts/runtime_guard.py check                  # 只体检，不改动
python3 scripts/runtime_guard.py check --auto-restore    # 体检并自动回滚
```

必须用 `install` 而不是自己 `git config core.hooksPath .githooks` —— 完整原因见 `scripts/runtime_guard.py` 头部注释（简言之：`core.hooksPath` 指向工作树内的目录，切到"钩子还不存在"的旧提交时守护自身会先消失，而那恰恰是最危险的场景）。

---

## 4. 环境准备

### 后端

```bash
cd backend
uv sync --dev
```

`--dev` 不能省。开发依赖（pytest 等）声明在 `pyproject.toml` 的 `[dependency-groups]`（PEP 735），不是 `[project.optional-dependencies]`；漏掉 `--dev` 会导致 pytest 没装进 `.venv`，测试会静默回落到全局解释器。

配置环境变量：

```bash
cp backend/.env.example backend/.env    # 然后按需填写
```

`.env` 已被 `.gitignore` 保护。**绝不提交 `.env`。**

### 前端

```bash
cd frontend
npm install
```

---

## 5. 启动

### 后端

```bash
cd backend
uv run uvicorn app.main:app
```

加 `--reload` 可开启热重载。数据库表与种子数据在应用启动时自动初始化（`init_db()` + `seed_all()`），本地首次启动**不需要**手动跑 `alembic upgrade`。启动时还会做一次迁移 head 自检，若真实库落后于迁移会以 ERROR 大声报出（不自动迁移、不掩盖）。

健康检查：

```bash
curl http://localhost:8000/api/health
```

注意是 **`/api/health`**，不是 `/health`——所有路由都挂在 `/api` 前缀下。

### 前端

```bash
cd frontend
npm run dev      # http://localhost:5173
npm run build    # tsc -b && vite build
npm run lint     # oxlint
```

---

## 6. 跑测试

```bash
cd backend
R176_MOCK_LLM=1 uv run pytest -q
```

### `R176_MOCK_LLM=1` 是必需的

`R176_MOCK_LLM=1` 会把 `litellm.acompletion` 打成即时返回的桩。**这样整套测试不发起任何真实模型调用**——不需要有效的模型 Key、不产生 token 费用、不依赖外网，可离线复现。下面的基线数字与 PR 自查清单都以加了这个变量为前提。

不加它，涉及模型调用的用例会走真实网络请求：需要有效 Key、会产生费用、耗时受对端速率限制与网络状况影响，结果也不再可复现。如果你确实要在真实模型模式下验证，请自行准备 Key 并留意费用与耗时，并且**不要**把那次运行的数字当作本文的基线。

这不是"绕过了真实调用所以不算数"——桩只替换模型调用这一层，被测的路由、图编排、状态机与持久化逻辑都是真实执行的。

### 当前基线

```
1673 passed / 4 skipped / 0 failed        # 用例总数 1677，耗时约 42 分钟
```

**这个数字对运行环境敏感，允许 ±1 的浮动。** 仓库里有一条 live 端到端用例要求「后端正在运行 + 可达的模型提供方 ≥ 2 个」，条件不满足时它会诚实跳过：

| 环境条件 | 结果 |
|---|---|
| 满足 | `1673 passed / 4 skipped / 0 failed` |
| 不满足 | `1672 passed / 5 skipped / 0 failed` |

两种情况下**用例总数恒为 1677、failed 恒为 0**。所以你跑出 1672/5 并不说明你的环境有问题——对照总数与 failed 即可。

**修改前后都要跑一遍。** 如果修改前就有失败，请在 PR 里明确说明，不要让你的改动背锅。如果你的改动让通过数下降，请在 PR 中解释清楚。

在部分环境（如 WSL）全量测试耗时可能明显放大，属已知现象，不是你的改动导致的。

### 写测试的要求

- **修 bug 先复现**：先写一个能复现的测试并确认它失败，再修，再确认它通过。
- **测行为，不测实现**：校验构造函数正确赋值这类测试没有价值。
- **写不了测试就说明原因**：例如"数据库调用与业务逻辑高度耦合，难以隔离"——这本身是有用信息，不要默默跳过。

### 用 `caplog` 断日志：conftest 已统一处理，你不用管

用 `caplog` 断言日志内容的测试，曾多次出现**单跑通过、全量套件失败**。原因是 `alembic/env.py` 的
`fileConfig(...)` 默认 `disable_existing_loggers=True`，任一迁移相关用例跑过之后，此前已导入的
项目 logger 会被置 `disabled=True`，`caplog` 便再也抓不到任何 record；而 `caplog.at_level()`
**只调整级别**，不会把 `disabled` 改回来、也不修 `propagate`，所以它救不了这个坑。

`backend/tests/conftest.py` 已有 autouse 夹具 `_reenable_project_loggers`，每个用例前把
`rebuild.*` 与 `app.*` 命名空间下的 logger 复位为可发声（`disabled=False` / `propagate=True`）。
**因此你写新测试时无需自行复位**，照常用 `caplog.at_level(...)` 指定级别即可——级别仍由你的用例
决定，夹具不碰级别。

---

## 7. 端到端测试（e2e）

e2e 脚本是 Node 版 Playwright 脚本，放在 `frontend/e2e/*.mjs`。用包装脚本运行：

```bash
cd frontend
./e2e/run-e2e.sh e2e/<脚本名>.mjs
```

**为什么要用这个包装脚本**：在 WSL / 精简 Linux 上，Playwright 下载的 Chromium 缺几个系统库（`libnspr4` / `libnss3` / `libnssutil3` / `libsmime3` / `libasound.so.2`）。包装脚本会自动探测 `~/.local/playwright-deps` 并注入 `LD_LIBRARY_PATH`。两种解法：

```bash
# ① 系统级（推荐，一次到位）
sudo apt-get install -y libnss3 libasound2t64

# ② 免 sudo：把 deb 解包到 ~/.local/playwright-deps
cd /tmp && apt-get download libnss3 libnspr4 libasound2t64
for d in *.deb; do dpkg -x "$d" ~/.local/playwright-deps; done
```

浏览器本身装不上（CDN 被拦）时用镜像：

```bash
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright \
  npx playwright install chromium
```

跑 e2e 前需要后端（8000）与前端 dev server（5173）都在运行。

---

## 8. 编码约定

### 先读后写

- 动手前**通读**你将修改的文件，不是扫一眼。
- 参考同类功能的既有范式：后端是 `routes_*.py` → `*_service.py` → models；前端组件有固定目录结构。遵循既有规范，不要另起炉灶。
- 看导入语句确认项目实际用的库。项目用 httpx 就继续用 httpx，不要引入 requests。
- **读测试文件**——测试定义了功能的真实预期行为。

### 精准修改

你改的每一行都可能引入 bug、都需要有人评审、都会永久留在 `git blame` 里。

- **不要动没让你碰的代码。** 修函数 A 的 bug 时，函数 B 的变量名奇怪、函数 C 的注释有拼写错误、导入顺序不合你的偏好——都别管。
- **不要重新格式化。** 不要跑 prettier、不要改缩进、不要重排导入。重格式化会产生大量变更，掩盖真正的改动。
- **遵循文件内既有风格**，文件内一致性比你的个人偏好重要。
- 清理你自己造成的冗余（你的改动导致某个导入不再被使用 → 删掉），但原本就存在的死代码不是你的任务。

**自查标准**：审视你的 diff，能为每一行找到和需求直接相关的理由吗？如果有哪一行只是"既然改到这儿了顺便……"，改回去。

### 保持简洁

写能解决当下这个具体问题的最少代码。常见的过度设计：

- **过早抽象**：只需要一个查询函数，却写了 Repository + 策略 + 工厂三层。
- **想当然的错误处理**：给不会发生的错误套 try/except，校验上游已验证的输入。项目有统一的 FastAPI 异常处理层，不要在业务代码里重复包裹。
- **不必要的可配置性**：把批量大小、重试次数、超时秒数全做成环境变量。每个配置项都意味着要有人做决策；在有真实需求前硬编码即可。
- **僵化的灵活性**：只有一个实现的接口、只有一个子类的抽象基类。

"万一以后需要"不是需求，只是对未来的猜测。

### 异常必须发声

**不要静默 `except`。** 异常要么处理，要么抛出，要么至少记日志。吞掉异常让问题在别处以更难懂的形式爆炸。

### 不要用 placeholder 冒充实现

不要用 mock / placeholder / build-only 冒充已实现。做不到就诚实说明，标注为未实现或阻塞，不要伪装。**失败时不要回退到 mock 假装成功。**

### 命名

| 对象 | 风格 |
|---|---|
| Python 变量 / 函数 | `snake_case` |
| TypeScript 变量 / 函数 | `camelCase` |
| 类型 / 接口 / React 组件 | `PascalCase` |
| 常量 | `UPPER_SNAKE_CASE` |
| React 自定义 hook | `use` 前缀 |
| 布尔值 | `is` / `has` / `should` / `can` 前缀 |

### 文件组织

多个小文件优于少数巨型文件。函数控制在 50 行内，文件典型 200-400 行、上限 800 行，嵌套不超过 4 层（用提前返回代替层层嵌套）。按功能 / 领域组织目录，而不是按文件类型。

### 前端专项

- **用户可见文案中文优先**：标题、按钮、标签、提示、空态、错误信息。技术标识符（`provider_id`、`model_name`、字段名、API 格式名）保留原文。
- **不要用 emoji 当功能图标**：统一使用线性图标组件 `frontend/src/components/ui/Icon.tsx`。
- **能力状态必须真实**：`mock` / `static_demo` / `not_connected` 要显式区分，状态来自后端。已真实接入的页面不得挂全局 mock 横幅。
- **凭据全程脱敏**：前端不回显密钥，不请求明文回传展示。

### 模型调用

所有模型调用必须经 ModelGateway → LiteLLM Adapter，**不要直连 Provider**，**不要硬编码模型名或 endpoint**。

---

## 9. 密钥纪律（硬规则）

```
绝不提交 .env
Key / Token / Secret / Password 只从环境变量读取
```

**绝对禁止**把密钥写入：源码、配置文件、文档、报告、日志、Trace、审计记录、前端页面、截图说明、测试夹具（用明显的合成假值）、以及 **git 提交**。

- API 响应只返回凭据状态（如 `credential_status`），不返回密钥值。
- 所有日志与输出中的密钥统一标记为 `[REDACTED]`。
- 配置文件只存 Provider 的非敏感信息，密钥走环境变量。
- `.env.example` 只放**变量名与说明**，不放值。

**如果你不小心提交了真实密钥**：立即吊销该凭据，然后通过 [SECURITY.md](SECURITY.md) 的私密渠道告知我们。请注意：删除提交并不能让密钥变回安全——它已经在历史里存在过，必须假定已泄漏。

---

## 10. 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>: <描述>

<可选正文：为什么这么改，而不只是改了什么>
```

`type` 取值：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf` / `ci`

**提交信息要具体。** "修复 bug"毫无意义。

```
# 差
fix: 修复 bug
chore: 更新代码

# 好
fix: 修复邮箱含大写字母时用户查询返回空的问题
feat: 项目创建支持自定义场景包 id
refactor: 抽出重复的校验逻辑为独立函数，便于单独测试
```

一次提交只做一件事。同时改三处然后 bug 消失，你并不知道是哪处起了作用。

---

## 11. 提 Pull Request

### 提交前自查

- [ ] 后端测试通过：`cd backend && R176_MOCK_LLM=1 uv run pytest -q`（**failed 必须为 0**；passed 数与你改动**之前**在同一环境跑出的数字对照，不得减少——新增用例会让总数上升，口径见 §6「当前基线」）
- [ ] 前端可构建：`cd frontend && npm run build`
- [ ] 新功能有对应测试
- [ ] 没有硬编码密钥、没有提交 `.env`
- [ ] 没有遗留调试语句（`console.log`、`print`、临时断点）
- [ ] 没有另设服务端口
- [ ] 没有把场景实现成固定枚举
- [ ] diff 里每一行都和需求直接相关（没有顺手重构）
- [ ] 涉及前端的改动做过真实联调：页面真的发起了 API 请求并渲染了真实响应

### PR 描述要写清

- **做了什么、为什么这么做**，让评审者不必逐行读代码就能理解。
- **权衡取舍**：如果有多种方案，说明你为什么选这个。
- **主动提示风险**：例如"功能能跑，但列表每一项都会发起一次数据库查询，列表变长时性能会差"。
- **明确说出不确定的点**："我不确定这个库是否支持流式响应"是有用信息；"我觉得应该可以"没有价值。

PR 模板会引导你填写这些内容。

### 架构层面的改动请先讨论

以下改动请**先开 issue 或 Discussion 讨论**，不要直接提 PR：

- 更换或新增编排框架（LangGraph 是硬规则）
- 数据库 schema 变更（需配套 Alembic 迁移，且 revision id 须先查 head 防撞、须在真实库验证）
- API 形态变更
- 新增重量级依赖
- 改动风险分级与审批 Gate 机制

这类选择的改造成本很高，值得先对齐再动手。

### 遇到歧义就停下来问

需求不清楚时，直接说明哪里有歧义并询问，**不要用看似合理的代码掩盖认知模糊**。判断错了只浪费 10 秒，默默猜错浪费一整个小时。同样，找不到某个功能的实现范式时，直接问："我没找到 X 的实现范式，应该沿用 Y 的思路，还是采用其他方案？"主动询问永远比瞎猜好。

---

## 12. 报告 issue

| 类型 | 去哪里 |
|---|---|
| Bug | GitHub Issues → Bug 报告模板 |
| 功能建议 | GitHub Issues → 功能建议模板 |
| 使用问题、想法、讨论 | GitHub Discussions |
| **安全漏洞** | **不要开 issue** → 见 [SECURITY.md](SECURITY.md) |

Bug 报告请包含复现步骤、期望行为、实际行为、环境信息，以及**完整的错误信息与堆栈**（同一种错误类型可能有上百种成因，堆栈会告诉我们到底是哪一种）。日志里的密钥请替换为 `[REDACTED]`。

---

## 13. 许可

本项目采用 [MIT 许可证](LICENSE)。提交贡献即表示你同意你的贡献以相同许可发布。
