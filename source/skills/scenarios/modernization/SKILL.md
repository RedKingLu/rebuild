---
name: modernization
description: 软件现代化场景包 —— 旧框架/旧运行时升级到当前受支持版本的目标态词表、改造模式与已知不兼容点
---

# 软件现代化（modernization）

> 本场景为平台**典型场景之一**。目标是把应用从已停止支持或明显落后的框架 / 运行时升级到当前受支持版本，**目标态是"当前受支持的上游版本"，不是国产化替代**。

## 目标态词表

**运行时与框架候选**

- .NET：`.NET Framework 4.x` → `.NET 8`（LTS）/ `.NET 9`；WebForms / WCF → `ASP.NET Core`
- Java：`JDK 8` → `JDK 17` / `JDK 21`（LTS）；Spring Boot 2.x → 3.x（伴随 `javax.*` → `jakarta.*`）
- Python：`Python 2.7` → `Python 3.12`；`setup.py` → `pyproject.toml`
- Node.js：已 EOL 的 12/14/16 → 当前 LTS
- 前端：jQuery / 无构建脚本 → React 或 Vue 3 + 现代构建工具链

**平台与配套候选**

- 操作系统：已 EOL 的发行版 → 当前 LTS 发行版
- 构建：MSBuild 旧格式 → SDK-style 工程；Maven / Gradle 升级到支持目标 JDK 的版本
- 依赖管理：`packages.config` → `PackageReference`；`requirements.txt` 锁定 → 现代锁文件

## 迁移与重构模式

- 工程格式先行：先把工程文件迁到 SDK-style / 现代构建格式，再改代码，否则依赖解析与目标框架切换互相干扰。
- 配置外移：`Web.config` / `App.config` → `appsettings.json` + 环境变量；配置读取入口收敛到一处再替换实现。
- API 分层替换：先替换宿主与管道（WebForms/WCF → ASP.NET Core 中间件），再逐个替换业务处理器，保持每步可构建。
- 同步转异步：I/O 密集路径由同步阻塞改 `async/await`，注意不要在改造中途混用 `.Result` / `.Wait()` 造成死锁。
- 命名空间批量迁移：`javax.*` → `jakarta.*` 属机械替换，但须同步升级依赖版本，否则出现同名类冲突。
- 依赖分批升级：先升到能与旧代码共存的最高版本，再切目标框架，避免一次跨越多个不兼容大版本。

## 常见陷阱与已知不兼容点

- **`System.Web` 在 .NET Core / .NET 5+ 无对应实现**：`HttpContext.Current`、`HttpRuntime`、`System.Web.UI` 全部不可用，须改用 `IHttpContextAccessor` 与 ASP.NET Core 等价机制。
- **`AppDomain` 的创建与卸载能力被移除**：.NET Core 起只有默认 AppDomain，插件隔离须改用 `AssemblyLoadContext`。
- **`BinaryFormatter` 已被禁用**：.NET 5 起默认禁用、.NET 9 起移除，依赖它做序列化的缓存 / 会话 / 远程调用必须换序列化器。
- **`ConfigurationManager` 行为差异**：.NET Core 下需显式引入包且不再自动读取 `Web.config`，配置节自定义 handler 无对应机制。
- **WCF 服务端不受支持**：.NET Core 起仅有客户端替代（`System.ServiceModel` 客户端库），服务端须改为 gRPC 或 ASP.NET Core Web API。
- **JDK 9+ 模块化限制反射**：对 JDK 内部包的反射访问默认被拒绝，`--add-opens` 只是过渡手段而非长期方案。
- **`javax.*` → `jakarta.*` 属破坏性改名**：Servlet / JPA / Validation 的包名全部变更，混用两套会在运行期出现类型不匹配。
- **`sun.misc.Unsafe` 与内部 API**：JDK 升级后可能被移除或行为变化，依赖它的库须整体升级。
- **Python 2 → 3 的字节与字符串分离**：隐式编解码不再存在，所有 I/O 边界都要显式指定编码。

## 反例与禁止

- **现代化路径不得产出国产化替代建议** —— 本场景的目标态是"当前受支持的上游版本"；若项目真实目标是国产化替代，应改用相应场景包，而不是在本场景里给出替代清单。
- 禁止跳过工程格式迁移直接改目标框架版本号（依赖解析会先失败并掩盖真实问题）。
- 禁止用 `--add-opens` / 反射绕过模块化限制后即宣称升级完成。
- 禁止把"构建通过"当作现代化完成的判据（须有目标运行时上的行为验证）。
- 禁止一次跨越多个不兼容大版本而无中间可运行状态。
