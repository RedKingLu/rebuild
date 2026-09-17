# 软件现代化 —— 风险清单（具体不兼容点）

> 每条须是可验证的具体技术事实。核实方式一并给出。

- `System.Web` 整个程序集在 .NET Core / .NET 5+ 无实现，`HttpContext.Current` 无等价物 —— 核实：检索产物中对 `System.Web` 的引用与 `HttpContext.Current` 用法。
- `AppDomain.CreateDomain` 在 .NET Core 起抛 `PlatformNotSupportedException` —— 核实：检索插件加载代码，确认是否已改用 `AssemblyLoadContext`。
- `BinaryFormatter` 在 .NET 5 起默认禁用、.NET 9 起移除 —— 核实：检索序列化调用点，确认缓存 / 会话 / 远程调用已换序列化器。
- `ConfigurationManager` 在 .NET Core 下不自动读取 `Web.config`，自定义配置节 handler 无对应机制 —— 核实：确认配置读取已迁到 `appsettings.json` + 选项模式。
- WCF 服务端在 .NET Core 起不受支持（仅有客户端库） —— 核实：清点 `ServiceHost` / `.svc` 端点，确认已改为 gRPC 或 Web API。
- JDK 9+ 默认拒绝对 JDK 内部包的反射访问 —— 核实：在目标 JDK 上运行测试，检查是否依赖 `--add-opens` 才能通过。
- `javax.*` → `jakarta.*` 为破坏性改名，混用两套会在运行期出现类型不匹配 —— 核实：检索产物中是否同时存在两个命名空间的 Servlet / JPA 类型。
- `sun.misc.Unsafe` 等内部 API 在新 JDK 上可能被移除或行为变化 —— 核实：检索依赖树中使用内部 API 的库并确认其版本支持目标 JDK。
- Python 3 取消隐式字节/字符串转换，未显式指定编码的 I/O 会在非 ASCII 数据上失败 —— 核实：以含非 ASCII 的样本数据跑通全部 I/O 边界。
- 旧 MSBuild 工程格式与目标框架切换互相干扰，依赖解析会先失败并掩盖真实问题 —— 核实：确认工程已迁到 SDK-style 后再执行还原与构建。
- 已 EOL 的 Node.js / OS 版本上的构建镜像与目标运行时不匹配 —— 核实：确认 CI 镜像与目标运行时版本一致。
