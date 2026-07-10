# rebuild community 服务（独立容器）

`community/` 是 rebuild 的**独立社区服务**，与主平台完全解耦：独立 backend（FastAPI + 独立 SQLite）+ 独立 frontend（React + Vite），通过 `docker compose --profile community up` 启动。

- 主平台通过 `COMMUNITY_BASE_URL`（默认 `http://localhost:8001`）经 Community Connector 拉取本服务数据；本服务**不共享主平台 DB**、**不反向控制**主平台实例（R15 红线）。
- R15 交付本地可运行的完整骨架 + 真实 API 闭环；不承担真实官方社区内容运营。发布侧通过 seed 写入社区 DB，平台内合格性由发布侧保证（D-061 修订执行注，2026-07-09）。

## 目录

```
community/
  backend/    FastAPI + SQLite，8 个只读 API（status/news/resources/.../models/evaluations）+ seed
  frontend/   React + Vite，5 页（首页/资源列表/资源详情/模型目录/评测榜单），全部真实调用 backend
```

## 端口（登记入 文档/02-架构设计/06-容器化部署与执行隔离规范 §1.1）

- community-backend：**8001**
- community-frontend：**8081**

## 本地启动

```bash
# 容器（推荐）
docker compose --profile community up

# 或本地直跑 backend
cd community/backend && uv run uvicorn app.main:app --port 8001
```
