"""
NovelMind 后端应用包

本包是 AI 辅助小说创作与理解平台的后端核心，基于 FastAPI 构建。

架构层次:
  - main.py      : ASGI 应用入口，注册中间件、路由、异常处理
  - config.py    : pydantic-settings 配置管理，从 .env 加载环境变量
  - core/        : 基础设施层（数据库引擎、结构化日志）
  - models/      : SQLAlchemy ORM 模型定义（11 张表）
  - schemas/     : Pydantic v2 请求/响应模型（API 契约）
  - services/    : 业务逻辑层（小说处理、AI 调用、智能路由）
  - api/         : FastAPI 路由层（6 个模块：小说、分析、时间线、人物、同人文、AI模型）

数据流:
  前端 → Next.js rewrite 代理 → FastAPI 路由 → Service 层 → ORM → PostgreSQL
  AI 调用链: Service → ai_router（选模型）→ ai_service（LiteLLM）→ 外部 API
"""
