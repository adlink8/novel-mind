# 02 — 模块地图

列出所有核心模块，包括职责、主要文件、状态和上下游关系。

## 后端模块

### 应用入口层

| 属性 | 内容 |
|---|---|
| **职责** | FastAPI 应用创建、中间件注册、路由挂载、全局异常处理 |
| **主要文件** | `backend/app/main.py`、`config.py`、`__init__.py` |
| **状态** | VERIFIED |
| **上游** | 无（启动入口） |
| **下游** | 所有路由模块、中间件 |
| **文档** | `backend/app/README.md` |

### 路由层（API）

| 属性 | 内容 |
|---|---|
| **职责** | HTTP 端点：认证、小说 CRUD、AI 模型配置、RAG 搜索 |
| **主要文件** | `backend/app/api/auth.py`、`novels.py`、`models.py`、`rag.py`、`dependencies.py` |
| **状态** | VERIFIED（认证 + 小说 + 模型 + RAG）；501 占位（分析/时间线/人物/同人） |
| **上游** | 前端 HTTP 请求 |
| **下游** | Service 层 |
| **文档** | `backend/app/api/README.md` |

### 基础设施层（Core）

| 属性 | 内容 |
|---|---|
| **职责** | 数据库引擎、JWT 安全、Fernet 加密、SSRF 防护、结构化日志 |
| **主要文件** | `backend/app/core/database.py`、`security.py`、`crypto.py`、`url_security.py`、`logging.py` |
| **状态** | VERIFIED |
| **上游** | 无（被所有上层引用） |
| **下游** | Service 层、API 层 |
| **文档** | `backend/app/core/README.md` |

### ORM 模型层

| 属性 | 内容 |
|---|---|
| **职责** | SQLAlchemy 2.0 异步声明式模型，映射 13 张 PostgreSQL 表 |
| **主要文件** | `backend/app/models/user.py`、`novel.py`、`text_chunk.py`、`ai_model.py`、`import_job.py` 等 |
| **状态** | VERIFIED（13 张表全部迁移到 head `f3c8b7b2dbf7`） |
| **上游** | 无（被 Service 层引用） |
| **下游** | PostgreSQL 数据库 |
| **文档** | `backend/app/models/README.md` |

### API 契约层（Schemas）

| 属性 | 内容 |
|---|---|
| **职责** | Pydantic v2 请求/响应模型，定义 API 的输入输出形状 |
| **主要文件** | `backend/app/schemas/common.py`、`novel.py`、`ai_model.py`、`analysis.py` 等 |
| **状态** | PARTIAL（通用 + 小说 + AI 模型已完备；分析/人物/时间线/同人仅骨架） |
| **上游** | 被 API 层路由引用 |
| **下游** | API 层响应序列化、OpenAPI 文档生成 |
| **文档** | `backend/app/schemas/README.md` |

### 业务逻辑层（Services）

| 属性 | 内容 |
|---|---|
| **职责** | 核心业务逻辑：小说上传/编码检测/章节分割、导入任务状态机、文本分块、向量存储、AI 调用 |
| **主要文件** | `backend/app/services/novel_service.py`(18.7KB)、`import_service.py`、`chunking_service.py`(10.4KB)、`indexing_service.py`(11.6KB)、`vector_store.py`(9.1KB)、`ai_service.py`、`ai_router.py` |
| **状态** | PARTIAL（小说导入 + RAG 核心验证；AI 路由业务端点未接入） |
| **上游** | API 路由层 |
| **下游** | ORM 模型层、ChromaDB、AI Providers |
| **文档** | `backend/app/services/README.md` |

### 数据库迁移

| 属性 | 内容 |
|---|---|
| **职责** | Alembic 管理的 PostgreSQL schema 版本迁移 |
| **主要文件** | `backend/migrations/env.py`、`versions/` |
| **状态** | VERIFIED（head: `f3c8b7b2dbf7`） |
| **上游** | ORM 模型变更 |
| **下游** | PostgreSQL DDL |
| **文档** | `backend/migrations/README.md` |

### 测试

| 属性 | 内容 |
|---|---|
| **职责** | pytest 后端测试套件：172 个用例，覆盖认证、安全、小说、RAG |
| **主要文件** | `backend/tests/` |
| **状态** | VERIFIED（172 passed） |
| **上游** | 所有后端模块 |
| **下游** | CI 门禁 |
| **文档** | `backend/tests/README.md` |

---

## 前端模块

### 页面层（App Router）

| 属性 | 内容 |
|---|---|
| **职责** | Next.js 16 App Router 页面路由：首页、小说列表/详情、设置、写作 |
| **主要文件** | `frontend/src/app/layout.tsx`、`page.tsx`、`novels/page.tsx`、`novels/[id]/page.tsx`、`settings/page.tsx` |
| **状态** | PARTIAL（小说阅读 + 设置完整；写作页骨架） |
| **上游** | 用户浏览器 |
| **下游** | API 客户端（lib/api.ts） |
| **文档** | `frontend/src/app/README.md` |

### 组件层

| 属性 | 内容 |
|---|---|
| **职责** | React 组件：认证门禁、小说卡片、上传对话框、阅读器（侧边栏/内容/进度）、shadcn/ui 基础组件 |
| **主要文件** | `frontend/src/components/auth-gate.tsx`、`novel-card.tsx`、`novel-upload-dialog.tsx`、`reader/` |
| **状态** | PARTIAL（核心组件完整；editor/timeline/character-graph 为空目录） |
| **上游** | 页面层 |
| **下游** | API 客户端、Stores |
| **文档** | `frontend/src/components/README.md` |

### 工具与 API 客户端

| 属性 | 内容 |
|---|---|
| **职责** | Axios API 封装（携带 Cookie 凭据）、通用工具函数 |
| **主要文件** | `frontend/src/lib/api.ts`(7.6KB)、`utils.ts` |
| **状态** | VERIFIED |
| **上游** | 组件层、Hooks |
| **下游** | FastAPI 后端 |
| **文档** | `frontend/src/lib/README.md` |

### 状态管理（Stores）

| 属性 | 内容 |
|---|---|
| **职责** | Zustand 全局状态：AI 模型配置列表、小说列表 |
| **主要文件** | `frontend/src/stores/aiConfigStore.ts`(4.3KB)、`novelStore.ts`(1.9KB) |
| **状态** | VERIFIED |
| **上游** | 组件层 |
| **下游** | API 客户端 |
| **文档** | `frontend/src/stores/README.md` |

### 自定义 Hooks

| 属性 | 内容 |
|---|---|
| **职责** | React 自定义 Hook：数据获取、加载状态管理 |
| **主要文件** | `frontend/src/hooks/use-novels.ts`、`use-ai-models.ts` |
| **状态** | VERIFIED |
| **上游** | 组件层 |
| **下游** | API 客户端 |
| **文档** | `frontend/src/hooks/README.md` |

### 前端测试

| 属性 | 内容 |
|---|---|
| **职责** | Vitest 前端测试套件：22 个用例，覆盖 API 客户端、工具函数、组件 |
| **主要文件** | `frontend/src/__tests__/`、`lib/api.test.ts`、`lib/utils.test.ts` |
| **状态** | VERIFIED（22 passed） |
| **上游** | 所有前端模块 |
| **下游** | CI 门禁 |
| **文档** | `frontend/src/__tests__/README.md` |

---

## 基础设施

### Docker 开发环境

| 属性 | 内容 |
|---|---|
| **职责** | 本地开发服务编排 |
| **主要文件** | `docker-compose.yml` |
| **状态** | VERIFIED |
| **服务** | PostgreSQL 16 (port 5432)、ChromaDB (port 8001) |

### CI

| 属性 | 内容 |
|---|---|
| **职责** | GitHub Actions 自动化测试与检查 |
| **主要文件** | `.github/workflows/backend-tests.yml`、`frontend-tests.yml` |
| **状态** | PARTIAL |
| **覆盖** | backend pytest、frontend Vitest、lint、build |

---

## 模块间通信

```
前端页面 → lib/api.ts (Axios) → FastAPI 路由 → Service → ORM → PostgreSQL
                                            → Service → ChromaDB
                                            → Service → AI Providers

Service → chunking_service → vector_store → ChromaDB
Service → ai_service → ai_router → LiteLLM → 外部 AI API
```

所有跨模块调用通过依赖注入（FastAPI `Depends`）和 Python 函数调用，不使用消息队列或事件总线。
