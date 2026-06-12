# backend/app — 应用入口层

NovelMind 后端核心包，基于 FastAPI 构建，负责应用启动、中间件注册、路由挂载与全局配置。

## 文件

| 文件 | 职责 |
|---|---|
| `main.py` | ASGI 应用入口 — 创建 FastAPI 实例、注册三层中间件（TrailingSlash / RequestLogging / CORS）、全局异常处理、挂载 8 个路由模块 |
| `config.py` | pydantic-settings 配置管理 — 以 `NOVELMIND_` 为前缀从 `.env` 加载，涵盖数据库连接、AI 密钥、文件存储、JWT 认证、SSRF 白名单 |
| `__init__.py` | 包文档 — 架构层次概览与数据流说明 |

## 架构层次

```
backend/app/
├── main.py          ← ASGI 入口
├── config.py        ← 配置中心
├── core/            ← 基础设施层（DB 引擎、安全、日志）
├── models/          ← ORM 模型层（13 张表）
├── schemas/         ← Pydantic 契约层（请求/响应模型）
├── services/        ← 业务逻辑层（小说处理、AI 调用、索引管线）
└── api/             ← FastAPI 路由层（8 个端点模块）
```

## 数据流

```
前端 → Next.js rewrite 代理 → FastAPI 路由 → Service 层 → ORM → PostgreSQL
AI 调用链: Service → ai_router（选模型）→ ai_service（LiteLLM）→ 外部 API
```

## 启动

```bash
cd backend
source venv/Scripts/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
