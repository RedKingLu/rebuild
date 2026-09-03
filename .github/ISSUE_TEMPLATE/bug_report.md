---
name: Bug 报告
about: 报告一个可复现的缺陷
title: 'fix: '
labels: bug
assignees: ''
---

> ⚠️ **安全漏洞请不要在这里提交。** 请走 [SECURITY.md](SECURITY.md) 的私密渠道（仓库 Security 标签页 → Report a vulnerability）。公开描述可利用的缺陷等于把利用路径先交给攻击者。
>
> 使用问题、想法与讨论请去 GitHub Discussions，这里只用于可复现的缺陷。

## 缺陷描述

<!-- 简洁说明出了什么问题。 -->

## 复现步骤

<!-- 请写具体到能让别人照着做一遍就复现。 -->

1.
2.
3.

## 期望行为

<!-- 你认为正确的结果是什么。 -->

## 实际行为

<!-- 实际发生了什么。 -->

## 完整错误信息与堆栈

<!--
请粘贴【完整】的错误输出，包括堆栈跟踪 —— 同一种错误类型可能有上百种成因，
堆栈会告诉我们到底是哪一种。只写"报错了"我们无法定位。

⚠️ 粘贴前请把日志中的 Key / Token / Secret / Password 替换为 [REDACTED]。
-->

```text

```

## 影响范围

<!-- 勾选相关项（可多选）。 -->

- [ ] 后端 API / 服务（`backend/`）
- [ ] 前端页面（`frontend/`）
- [ ] P 阶段执行链路（P0-P6）
- [ ] 模型接入 / ModelGateway
- [ ] 场景包（`source/skills/scenarios/`）
- [ ] 部署 / 容器（`deploy/`、`docker-compose.yml`）
- [ ] 测试
- [ ] 文档
- [ ] 其他 / 不确定

## 运行环境

| 项 | 值 |
|---|---|
| 操作系统 | <!-- 例：Ubuntu 24.04 / Windows 11 + WSL2 / macOS 15 --> |
| Python 版本 | <!-- python3 --version --> |
| Node 版本 | <!-- node --version --> |
| 部署方式 | <!-- 本地 dev / docker compose --> |
| 版本或提交 | <!-- git rev-parse --short HEAD，或 GET /api/health 返回的 version --> |

## 复现频率

- [ ] 每次都能复现
- [ ] 偶发（大致频率：）
- [ ] 只出现过一次

## 已排查的内容

<!--
可选，但很有帮助。例如：
- 后端健康检查 `curl http://localhost:8000/api/health` 是否正常
- 测试是否通过：`cd backend && R176_MOCK_LLM=1 uv run pytest -q`
- 是否在干净环境（重新 uv sync --dev / npm install）下也能复现
-->

## 补充信息

<!-- 截图、相关 issue、你怀疑的原因等。不确定也可以写，判定是我们的工作。 -->
