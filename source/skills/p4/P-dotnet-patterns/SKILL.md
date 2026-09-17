---
name: P-dotnet-patterns
description: P4执行 — .NET Framework(WebForms/WCF/MVC5)→.NET Core/ASP.NET Core 迁移：识别遗留模式、映射现代等价物、麒麟Linux兼容
metadata:
  series: P
  phase: P4
  category: execution_skill
  status: platform_runtime
  source: ECC skills/dotnet-patterns (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-dotnet-patterns（.NET 现代化与信创兼容模式）

## 适用阶段与触发条件
- 阶段：P4 执行为主；P1/P2 读遗留代码识别模式时亦可参考。
- 触发：将 .NET Framework 4.x（WebForms/WCF/ASP.NET MVC5/Web Forms）迁移到 .NET 8+/ASP.NET Core，并在麒麟/统信 Linux 运行。
- 不适用：纯数据库迁移（用 P-database-migrations）；通用分层设计（用 P-backend-patterns）。

## 输入
- 遗留 .NET 项目源码、`packages.config`/`.csproj`、`web.config`、依赖清单。
- P2 识别出的遗留模式与风险点；目标运行环境（麒麟 OS + .NET 8 运行时）。

## 执行步骤
1. **识别遗留模式并建映射**：先扫描出 Framework 专属构造，列"遗留→现代等价物"映射表（见信创要点表），机械可替换的走确定性 codemod，语义重写的单列。
2. **工程现代化**：`packages.config`→SDK 风格 `.csproj`+`PackageReference`；`web.config`→`appsettings.json`+`IConfiguration`；过渡期可用 `Microsoft.Windows.Compatibility` 抹平 API，但目标是去 Windows 依赖。
3. **依赖注入归一**：把静态访问/`HttpContext.Current`/手动 new 改为构造函数注入；服务注册到 `IServiceCollection`，按生命周期（Singleton/Scoped/Transient）正确分类。
4. **异步与取消**：阻塞调用改 `async/await`，I/O 路径全程异步并贯穿 `CancellationToken`；避免 `.Result`/`.Wait()` 死锁。
5. **现代语法落地**：DTO 用 `record` + nullable 引用类型；配置用 Options 模式（`IOptions<T>`）；数据访问用 EF Core + Repository；可失败操作可用 Result 模式替代异常滥用；端点可用 Controller 或 Minimal API。
6. **服务模型迁移**：WCF→gRPC 或 ASP.NET Core Web API；`.asmx`/`[WebMethod]`→Controller action；WebForms 页面→MVC/Razor 或 API+前端。
7. **先编译后功能**：先让项目在 .NET 8 编译通过，再逐模块恢复功能，每步对回归测试基线。
8. **麒麟 Linux 验证**：去除 `System.Drawing.Common` GDI+、注册表、Windows 服务、IIS 专属等不可移植依赖；在麒麟上 `dotnet run`/部署冒烟通过。

## 输出 / 产物（Artifact / Evidence）
- 遗留→现代映射表与改造 diff/PR — Artifact。
- 现代化后的工程文件与配置（`.csproj`/`appsettings.json`）— Artifact。
- 编译通过日志、单元/契约回归结果、麒麟上启动冒烟日志 — Evidence。

## 质量门 / 验收标准
- 目标项目在 .NET 8+ 编译零错误；回归测试不低于迁移前基线。
- 无残留 Framework 专属/Windows 专属 API（或已用兼容层显式隔离并记录）。
- 在麒麟/统信 OS 上成功启动并通过冒烟。
- DI/async/配置三项现代化模式落地，无 `.Result`/`.Wait()` 同步阻塞。

## 场景要点（按项目场景取用）
- 高频映射：`HttpContext.Current`→`IHttpContextAccessor`；`ConfigurationManager.AppSettings`→`IConfiguration`；`[WebMethod]`/`.asmx`→Controller；WCF→gRPC/Web API；`System.Web`→`Microsoft.AspNetCore`。
- Windows 不可移植项：GDI+ 绘图、注册表、`EventLog`、Windows 服务、IIS `web.config` 处理管线——迁移时替换为跨平台等价（SkiaSharp、配置文件、systemd、Kestrel 中间件）。
- 数据库访问层连接到信创 DB（达梦/openGauss）须换驱动与连接串，与 P-database-migrations 协同。
- 时区/区域/文件路径分隔符等隐式 Windows 假设在 Linux 上须显式处理。

## 反例 / 禁止
- 禁止用 `.Result`/`.Wait()` 在异步路径上同步阻塞。
- 禁止保留对 `System.Drawing.Common`(GDI+)、注册表等 Windows 专属 API 的运行期依赖。
- 禁止仅本地 Windows 编译通过即判定迁移完成（须麒麟实测）。
- 禁止把配置/连接串/密钥硬编码进代码或留在 `web.config` 明文。

## 与平台集成
- 涉及代码理解/改写的模型调用经 ModelGateway，不直连 Provider；模型产出 diff 非 Evidence，须编译+测试验证。
- 修改生产代码仓库、合并 PR、部署到麒麟环境等外部写为 L4-L5，需用户 Gate。
- 映射表/diff/测试与冒烟日志挂 Project/Run/Stage/Node，登记 Registry，产生 Trace/Audit。
- 自验证不替代 D-级 Acceptance。

## 参考
- ECC skills/dotnet-patterns（MIT）— DI、async+CancellationToken、records/nullability、Options、EF Core repository、Result、Minimal API、反模式。
- 文档/04-模型与资源/06-确定性转换资源接入规范.md；03-Agent与Skill规范 §6/§7。
