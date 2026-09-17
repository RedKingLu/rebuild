---
name: P-docker-patterns
description: P4 执行 — 将迁移目标容器化到国产容器平台，麒麟/统信基础镜像、国产DB驱动镜像层与非root安全
metadata:
  series: P
  phase: P4
  category: execution_skill
  status: platform_runtime
  source: ECC skills/docker-patterns (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-docker-patterns（容器化施工模式）

## 适用阶段与触发条件
P4 执行阶段，当迁移方案确定以容器形态承载目标系统时触发。典型场景：源系统（.NET/Java）改造后需打包为镜像，部署到国产容器平台（如基于 Kubernetes 的麒麟云、CNCP、DaoCloud 等）。当目标仍为传统虚机/物理机部署时不触发本 skill。

## 输入
- P2/P3 产出的目标技术栈与运行时矩阵（OS、JDK/运行时版本、中间件、DB 驱动）
- 应用构建产物路径（jar/war、发布目录、静态资源）
- 国产基础镜像清单与镜像仓库地址（麒麟 V10 / 统信 UOS 官方基础镜像）
- DB 连接驱动制品（达梦 DmJdbcDriver、openGauss/GaussDB JDBC、KingbaseES 驱动等）

## 执行步骤
1. **选基础镜像**：优先选用麒麟/统信官方维护的最小基础镜像（kylin-minimal、uos-base），核对 CPU 架构（多为 ARM64 鲲鹏/飞腾，非 x86_64），FROM 行必须锁定 digest 而非 latest。
2. **多阶段构建**：builder 阶段编译/打包（含国产 JDK，如毕昇 BiSheng JDK），runtime 阶段仅复制制品，剥离编译工具链，缩小攻击面与体积。
3. **嵌入国产 DB 驱动层**：将达梦/openGauss/GaussDB 驱动作为独立镜像层 COPY，便于缓存与按 DB 目标切换；驱动版本须与 P5 验证用版本一致。
4. **本地开发 compose**：用 docker-compose 编排 app + 国产 DB 容器（dm8、opengauss）+ 中间件（东方通 TongWeb / 宝兰德 BES），供 P4 开发自验；network 用自定义 bridge，服务间用服务名 DNS 解析。
5. **非root与cap-drop**：创建专用用户（USER appuser:appgroup），`cap_drop: [ALL]` 仅按需 add，只读根文件系统 `read_only: true` + tmpfs 挂载可写目录。
6. **卷策略**：配置/密钥用 secret 挂载（不入镜像层），日志与数据用具名卷；明确区分有状态卷与无状态可重建数据。
7. **麒麟兼容性自验**：在目标架构（ARM64）下 build + 启动，确认动态库依赖（glibc 版本、libaio 等达梦依赖）齐备，无 x86 二进制残留。

## 输出 / 产物（Artifact / Evidence）
- Dockerfile（多阶段，digest 锁定，含国产基础镜像与驱动层注释）
- docker-compose.yml（本地开发编排）
- 镜像构建日志与 `docker images` 体积清单
- 麒麟 ARM64 启动自验日志（容器健康、DB 驱动可加载）
- 镜像 SBOM / 依赖清单（供 P6 上线安全审查）

## 质量门 / 验收标准
- 镜像在目标 CPU 架构下成功 build 且容器启动健康检查通过
- 以非 root 用户运行，`docker inspect` 确认未授予多余 capability
- 镜像不含任何明文密钥、Token、生产连接串
- 国产 DB 驱动可被应用加载并完成一次连通性探测
- 镜像基于官方国产基础镜像，FROM 锁定 digest，无 latest 漂移

## 场景要点（按项目场景取用）
- 国产服务器多为 ARM64（鲲鹏/飞腾）或申威架构，构建机与运行环境架构必须一致，避免在 x86 开发机产出无法运行的镜像；必要时用 buildx 跨架构构建。
- 达梦容器需 libaio、字符集（GBK/UTF-8）与时区配置；openGauss 容器对内存与 huge page 有要求，compose 须显式声明。
- 内网/离线环境无法拉取公网镜像，所有基础镜像与驱动必须先入私有镜像仓库（Harbor 国产化部署）。
- 等保合规要求容器最小权限、镜像签名与漏洞扫描，非 root 与 cap-drop 是硬性要求。

## 反例 / 禁止
- 禁止 FROM 公网 docker hub 镜像（如 openjdk:latest）作为信创目标基础镜像
- 禁止以 root 运行业务容器，禁止 `privileged: true`
- 禁止把 Key/连接串/证书写进 Dockerfile 或镜像层（须 secret 挂载）
- 禁止单阶段构建把编译工具链带入 runtime 镜像
- 禁止在 x86 开发机直接产出部署到 ARM64 的镜像而不做跨架构验证

## 与平台集成
- 构建脚本若调用模型生成 Dockerfile/compose，模型调用一律经 ModelGateway；模型产出仅为草稿，须经本 skill 质量门校验后方可采纳，模型输出不等于 Evidence。
- 推送镜像到仓库、修改运行环境等外部写操作属高风险（L4-L5），必须经用户 Gate 确认。
- Dockerfile、compose、构建日志、SBOM 均挂 Artifact/Evidence，构建动作记入 Trace/Audit，镜像登记 Registry。
- 本 skill 的麒麟兼容性自验是开发自验，不替代 P5 验证与 D 验收；自验通过不等于可上线。

## 参考
- ECC skills/docker-patterns（MIT）
- P-deployment-patterns（P6 国产环境部署）
- P-benchmark（容器化前后性能基线）
- 信创基础镜像与国产 DB 驱动制品清单（项目资源库）
