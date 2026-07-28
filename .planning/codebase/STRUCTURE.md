# Project Structure

Generated: 2026-07-05 | Source: actual filesystem

## 顶层目录

```
novel-mind/
├── backend/          # FastAPI 后端
├── frontend/         # Next.js 前端
├── docs/             # 项目文档
├── .gsd/             # GSD AI 工作区（phases / 计划 / 状态）
├── .planning/        # GSD 计划辅助文件（codebase maps / intel）
├── docker-compose.yml
├── Makefile
├── README.md
└── IMPLEMENTATION-STATUS.md
```

---

## backend/app/ 详细结构

```
backend/
├── app/
│   ├── main.py           # FastAPI ASGI 入口，注册中间件与路由
│   ├── config.py         # pydantic-settings 全局配置
│   │
│   ├── api/              # 路由层（HTTP 接口，11 个模块）
│   │   ├── auth.py           # 认证（注册/登录/登出）
│   │   ├── novels.py         # 小说 CRUD + 导入触发
│   │   ├── analysis.py       # 小说分析（501 占位）
│   │   ├── timeline.py       # 时间线（501 占位）
│   │   ├── characters.py     # 人物（501 占位）
│   │   ├── fanfiction.py     # 同人文（501 占位）
│   │   ├── models.py         # AI 模型配置 CRUD
│   │   ├── rag.py            # RAG 问答
│   │   ├── search.py         # 混合搜索
│   │   ├── eval.py           # RAG 评测
│   │   ├── knowledge.py      # 知识图谱查询
│   │   └── dependencies.py   # FastAPI 依赖注入（当前用户、DB session）
│   │
│   ├── core/             # 基础设施层
│   │   ├── database.py       # SQLAlchemy async engine + session factory
│   │   ├── security.py       # JWT 生成/验证，password hash
│   │   ├── crypto.py         # API key 加解密（Fernet enc:v1）
│   │   ├── url_security.py   # SSRF 防护（协议/DNS/IP 校验）
│   │   └── logging.py        # 结构化日志 + RequestLoggingMiddleware
│   │
│   ├── models/           # SQLAlchemy ORM 层（16 张表）
│   │   ├── base.py               # DeclarativeBase
│   │   ├── user.py               # users
│   │   ├── novel.py              # novels + chapters
│   │   ├── import_job.py         # import_jobs（导入任务状态机）
│   │   ├── text_chunk.py         # text_chunks（分块 + tsvector）
│   │   ├── character.py          # characters
│   │   ├── timeline.py           # timeline_events
│   │   ├── analysis.py           # analyses
│   │   ├── fanfiction.py         # fanfictions
│   │   ├── fanfiction_chapter.py # fanfiction_chapters
│   │   ├── ai_model.py           # ai_models（提供商配置）
│   │   ├── ai_usage_log.py       # ai_usage_logs
│   │   ├── eval.py               # eval_runs + eval_results
│   │   └── knowledge.py          # knowledge_units（图谱节点/边）
│   │
│   ├── schemas/          # Pydantic v2 契约层（请求/响应 schema）
│   │   ├── common.py
│   │   ├── novel.py
│   │   ├── analysis.py
│   │   ├── character.py
│   │   ├── timeline.py
│   │   ├── fanfiction.py
│   │   └── ai_model.py
│   │
│   └── services/         # 业务逻辑层
│       ├── novel_service.py      # 小说 CRUD 业务逻辑
│       ├── import_service.py     # 导入管道（状态机 + 租约恢复）
│       ├── chunking_service.py   # 文本分块（300-500 字，5 种块类型）
│       ├── indexing_service.py   # 双写索引（PostgreSQL tsvector + ChromaDB）
│       ├── vector_store.py       # ChromaDB HTTP 客户端封装
│       ├── hybrid_search.py      # 混合搜索（BM25 0.5 + Vector 0.5）
│       ├── ai_service.py         # AI 调用（嵌入 + 生成）
│       ├── ai_router.py          # 多提供商路由（LiteLLM）
│       ├── eval_service.py       # RAG 评测管道
│       └── knowledge/            # 知识图谱管道子包
│           ├── candidates.py     # 知识候选提取
│           ├── gates.py          # 规则门控过滤
│           ├── llm_judge.py      # LLM 质量判断
│           ├── evidence.py       # 证据收集
│           ├── graph_sync.py     # 图谱写入同步
│           └── projection.py     # 图谱投影/查询
│
├── migrations/           # Alembic 数据库迁移
│   └── versions/         # 迁移脚本（head: 518675fa18f8）
├── tests/                # pytest（239 单元/集成 + 12 RAG e2e）
└── uploads/              # 上传的小说 TXT 文件（随机文件名）
```

---

## backend/services/ 关键服务职责

| 服务 | 职责 |
|---|---|
| import_service | 导入任务状态机；异步执行；过期租约恢复 |
| chunking_service | 将章节内容切为 300-500 字语义块；识别 5 种块类型 |
| indexing_service | 双写：更新 PostgreSQL tsvector + ChromaDB upsert |
| vector_store | ChromaDB HTTP 客户端；per-novel collection 管理 |
| hybrid_search | BM25（PostgreSQL）+ Vector（ChromaDB）0.5:0.5 融合排序 |
| ai_service | 统一嵌入与生成接口 |
| ai_router | 多 AI 提供商路由（LiteLLM 后端） |
| eval_service | RAG 检索评测管道 |
| knowledge/* | 知识图谱提取→过滤→判断→写入→查询完整管道 |

---

## .gsd/phases/ 列表

| Phase | 目录 | 内容 |
|---|---|---|
| 01 | `01_narrative_knowledge_unit_layer/` | PLAN.md + CONTEXT.md：叙事知识单元层新增设计 |

---

## docs/architecture/ 文件列表

| 文件 | 内容 |
|---|---|
| README.md | 架构文档索引 |
| 01-system-overview.md | 系统总览 |
| 02-module-map.md | 模块映射 |
| 03-data-model.md | 数据模型 |
| 04-request-flow.md | 请求流程 |
| 05-import-pipeline.md | 导入管道详细设计 |
| 06-rag-pipeline.md | RAG 管道详细设计 |
| 07-auth-security.md | 认证与安全 |
| 08-ai-model-layer.md | AI 模型层 |
| 09-frontend-architecture.md | 前端架构 |
| 10-testing-ci.md | 测试与 CI |
| 11-gsd-docs-structure.md | GSD 文档结构规范 |
| diagrams/system-context.mmd | 系统上下文图（Mermaid）|
| diagrams/container-view.mmd | 容器视图图（Mermaid）|
| diagrams/rag-flow.mmd | RAG 流程图（Mermaid）|
| diagrams/import-flow.mmd | 导入流程图（Mermaid）|
| diagrams/auth-flow.mmd | 认证流程图（Mermaid）|
