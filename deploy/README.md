# rebuild 容器化部署（快速开始）

> 规范见 `文档/02-架构设计/06-容器化部署与执行隔离规范.md`。

## 一条命令起全部（SQLite，开箱即用）

```bash
cp .env.example .env        # 按需填非敏感配置；密钥只在本机 .env 填，绝不提交
docker compose up --build
```

- 前端： http://localhost:8080
- 后端健康： http://localhost:8000/api/health

## 使用 PostgreSQL（可选）

```bash
# .env 中设置 DATABASE_URL=postgresql+psycopg://rebuild:<pwd>@db:5432/rebuild
docker compose --profile pg up --build
```

## 执行沙箱（Phase C2 scaffold）

```bash
docker compose --profile sandbox build           # 构建隔离执行镜像
docker run --rm --network none rebuild-execution-sandbox:local
```

沙箱：只读根、无网络、非 root、CPU/内存/pids 限额、tmpfs 工作区。
真实容器化执行接线属预留能力（`EXECUTION_MODE=container`），见规范 §3。

## 安全

- 密钥仅经 `env_file`（`.env`）注入，绝不写入镜像（AGENTS §8/§12）。
- 飞书 Webhook、Git/模型凭据均加密存储，API 仅返回掩码。
- 数据（sqlite db / 执行沙箱 / 仓库克隆）持久化于命名卷 `backend-data`。

## 文件清单

| 文件 | 作用 |
|------|------|
| `backend/Dockerfile` | 后端多阶段镜像（uv + git/ssh，非 root，healthcheck） |
| `frontend/Dockerfile` + `frontend/nginx.conf` | 前端构建 + nginx 托管/反代 |
| `deploy/execution-sandbox/Dockerfile` | 隔离执行沙箱镜像（C2） |
| `docker-compose.yml` | 编排（backend/frontend/db[pg]/sandbox） |
| `.env.example` | 非敏感配置示例 |
