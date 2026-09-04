# 工具链构建镜像（R19-1 G1 容器真构建）

> 用途：宿主**没装对应语言 SDK**、但 **Docker 可用**时，让 P5 在隔离容器内用厂商官方 SDK 镜像
> 跑**真实**构建/测试/静态检查，产真实退出码与真实诊断（NU/CS/MSB/NETSDK），
> 而不是落 `needs_user_input` 或伪造 `available`。
>
> 映射事实源：`backend/app/config/toolchain_images.yaml`（框架 → 镜像、registry 白名单、资源上限）。
> 执行实现：`backend/app/services/execution_provider.py` 的 `ToolchainContainerExecutionProvider`
> （`toolchain_build` 硬化档）。
> 方案与裁决：`产物/草稿/R19-1-容器构建方案.md`、`证据/进度追踪/03-待确认项.md` 的 `Q-R19-1-1~8`。

---

## 1. 为什么要「预热」而不是运行期拉取

镜像拉取会写宿主镜像库、触及外部 registry ⇒ 属 **L4 系统级操作**（`文档/00-项目治理/02-术语表.md`
第七部分）。而 P5 对 L4/L5 命令一律置 `needs_user_input`。

如果拉取发生在 P5 循环**内**，验收锚点 `R19-1-03`（要求 **真实 `validation_failed`**）就会被
"等用户批准拉镜像"污染成 `needs_user_input`。

所以口径是（`Q-R19-1-2` 裁决 C）：

```
Gate 只作用于「镜像获取」，不作用于「构建执行」。
① P5 之外显式预热（一次性 L4 动作，人在场、审计在案）
② 镜像已在本地
③ 跑 P5 —— 构建命令本身是 L3，不触发 L4/L5 Gate ⇒ 得到干净的真实构建结论
```

这不是绕过 Gate：L4 照样要人确认，只是不把「等用户批准拉镜像」混淆成「构建结论待用户输入」。

运行期按需拉取的能力保留在配置里（`allow_runtime_pull`，**默认关闭**）；关闭时缺镜像 →
诚实 `evidence_gap` + 打印预热命令，**绝不**伪造 `available`。

---

## 2. 预热

```bash
# 看映射表里有哪些镜像（不联网）
bash deploy/toolchain-images/pull.sh --list

# 拉取映射表中的全部镜像
bash deploy/toolchain-images/pull.sh

# 只拉某一个（必须是映射表中登记过的引用）
bash deploy/toolchain-images/pull.sh mcr.microsoft.com/dotnet/sdk:8.0
```

脚本护栏：

- 只允许 `registries_allowed` 内的 registry host；
- 只允许**映射表中出现过**的镜像引用 —— 不接受来自项目内容或模型输出的镜像名
  （否则等于任意镜像执行）；
- 拉取后打印 `RepoDigest`：滚动 tag（如 `sdk:8.0`）会随时间漂移，**digest 才可复现**
  （`Q-R19-1-6`）。平台每次执行都会把解析到的 `image_ref` 与 digest 一并写进证据。

脚本**不会**挂载或改动 `/var/run/docker.sock`，也不会停宿主 Docker。

---

## 3. 离线 / 内网（信创场景常见）

外网机器上导出：

```bash
bash deploy/toolchain-images/pull.sh --save /tmp/toolchain-offline mcr.microsoft.com/dotnet/sdk:8.0
# → /tmp/toolchain-offline/mcr.microsoft.com__dotnet__sdk__8.0.tar
```

目标机器上导入：

```bash
docker load -i mcr.microsoft.com__dotnet__sdk__8.0.tar
docker image inspect mcr.microsoft.com/dotnet/sdk:8.0 --format '{{index .RepoDigests 0}}'
```

导入后镜像即"在本地"，平台探测转为具备，无需联网即可跑构建
（**注意**：`dotnet restore` 仍需能访问包源；完全离线时请另行配置内网包源并同步
`toolchain_images.yaml` 的 `package_config`）。

---

## 4. 包缓存

- 位置：`REBUILD_TOOLCHAIN_CACHE_DIR`（默认取 `Settings.toolchain_cache_dir`）下的
  `container_paths.package_cache` 同名子目录，以 **rw** 挂进容器。
- **不复用宿主 `~/.nuget`**：宿主的包源配置可能带私有源凭据，挂进容器等于把凭据面暴露给构建。
  平台改用自管的**无凭据**包源配置（`toolchain_images.yaml` 的 `package_config`）。
- 清理：直接删该目录即可（纯缓存，删了只是下次重下）。

---

## 5. 挂载与安全边界（`R19-1-06`）

容器内只挂 3 处，且**绝不挂 `docker.sock`** —— Docker API 由**宿主侧 backend 进程**调用，
容器内只跑构建命令，架构上不需要 sock：

| 宿主 | 容器内 | 模式 | 为何必要 |
|---|---|---|---|
| 构建根（通常 `output_code`） | `container_paths.src` | **ro** | 构建输入；ro 保证构建不篡改平台产物，也不往 `output_code` 落 `obj/`/`bin/` |
| `<workspace>/.rebuild/build/<ts>` | `container_paths.build` | rw | **唯一写入位**：HOME / CLI 状态 / 中间产物 / 输出 / 包源配置 / 日志 |
| 包缓存目录 | `container_paths.package_cache` | rw | 跨轮复用，避免每轮重下 |

明确不挂：`docker.sock`、`source/`（D-099）、仓库根 / `.env*`、`~/.ssh`、`~/.gnupg`、
`~/.nuget`、其他项目工作区、`/` `/etc` `/proc` `/sys` `/dev` 任何部分。

`toolchain_build` 档保留的硬化项：`cap_drop=ALL`、`no-new-privileges`、根 `read_only`
（+ `tmpfs /tmp`）、资源上限（mem / pids / cpu）、**非 root**（显式传宿主 uid:gid）、
一次性容器、**不发布任何端口**、DENY 安检与风险分级先行。

**唯一弱化项 = 网络**（经用户批准的 C2 变更，`Q-R19-1-1`）：`restore` 必须访问包源，
故本档不能用 `network_mode: none`。docker 原生无法做精细 egress 白名单 ⇒
**如实标注为弱化项，不假称"已白名单化"**。既有 `untrusted_snippet` 强隔离档
（`ContainerExecutionProvider`）**一字未改**，弱化只发生在本新增档，且本档**仅用于工具链构建**。

---

## 6. 排障

| 现象 | 原因 / 处置 |
|---|---|
| build 槽 `evidence_gap`，`probe.docker=unreachable` | Docker 守护不可达。检查 `docker version`、`DOCKER_HOST` |
| build 槽 `evidence_gap`，`probe.image=unavailable` | 镜像不在本地。按报告里给出的预热命令拉一次 |
| build 槽 `evidence_gap`，原因含「未映射的目标框架」 | 在 `toolchain_images.yaml` 增一条映射（平台**不猜**镜像） |
| 想彻底回到旧行为 | `REBUILD_TOOLCHAIN_CONTAINER_ENABLED=0`（kill switch，回到诚实 `needs_user_input`） |
| 想删掉预热的镜像 | `docker rmi <image>` —— **只删本方案预热的镜像**；不要用 `docker image prune`（会误删他人镜像） |
| 构建中间产物占盘 | 删 `<workspace>/.rebuild/build/<ts>/`（不在 `output_code` 内，删了不影响产物） |
