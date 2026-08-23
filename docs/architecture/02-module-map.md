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
| **职责** | HTTP 端点：认证、小说 CRUD、AI 模型配置、RAG、混合搜索与评测 |
| **主要文件** | `backend/app/api/auth.py`、`novels.py`、`models.py`、`rag.py`、`search.py`、`eval.py`、`timeline.py`、`relationships.py`、`clues.py`、`narrative_memory.py`、`dependencies.py` |
| **状态** | VERIFIED（认证 + 小说 + 模型 + RAG + 搜索 + timeline/rel/clue 产品路由 + **NM structure 只读** + timeline 可选章范围）；PARTIAL（评测质量闭环、NM 全书构建、线索 payoff 质量）；501 占位（同人等） |
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
| **职责** | SQLAlchemy 2.0 异步声明式模型，映射 16 张 PostgreSQL 表 |
| **主要文件** | `backend/app/models/user.py`、`novel.py`、`text_chunk.py`、`ai_model.py`、`import_job.py` 等 |
| **状态** | VERIFIED（16 张表全部迁移到 head `518675fa18f8`） |
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
| **职责** | 小说导入、任务状态机、文本分块、向量索引、混合搜索、RAG 评测与 AI 调用 |
| **主要文件** | `novel_service.py`、`import_service.py`、`chunking_service.py`、`indexing_service.py`、`vector_store.py`、`hybrid_search.py`、`eval_service.py`、`ai_service.py`、`ai_router.py` |
| **状态** | PARTIAL（导入、RAG、混合搜索已验证；评测工程层可用但质量基线未通过；AI 生成业务端点未接入） |
| **上游** | API 路由层 |
| **下游** | ORM 模型层、ChromaDB、AI Providers |
| **文档** | `backend/app/services/README.md` |

### 叙事记忆结构查询（Phase 20 产品只读面）

| 属性 | 内容 |
|---|---|
| **职责** | 为 Structure Workspace 提供 candidate NM versions/tree/claims/source-links；cutoff 过滤；**不** promote、**不**启动 builder |
| **主要文件** | `backend/app/services/narrative_memory/structure_query.py`、`api/narrative_memory.py`、`schemas/narrative_memory_product.py` |
| **状态** | VERIFIED（只读 API + 单测）；样例 novel 91 有 partial candidate（少量 chapter_state）；无完整 L3/L4 前 UI 可降级章节树 |
| **上游** | `/analysis` 前端、`require_owned_novel` |
| **下游** | `narrative_memory_*` 表（只读） |
| **文档** | `.planning/phases/20-structure-workspace-multilayer-presentation/` |

### 时间线查询（含结构范围）

| 属性 | 内容 |
|---|---|
| **职责** | 版本 envelope 投影；spoiler / full_book；可选 `chapter_start`/`chapter_end` 与 spoiler 合成上下界 |
| **主要文件** | `backend/app/api/timeline.py`、`services/timeline/query.py`（`effective_narrative_bounds`） |
| **状态** | VERIFIED（单测）；生产 BE 需加载新代码后 API 计数与范围一致 |
| **上游** | Structure Workspace / progressive 轮询 |
| **下游** | analysis events / versions 表 |

### 层级分块（Phase 07）

| 属性 | 内容 |
|---|---|
| **职责** | chapter→scene→evidence 树；active pointer；asset audit 要求 content/hash 与原文一致 |
| **主要文件** | `services/chunking/hierarchy.py`、`segmentation.py`、`pg_store.py`、`analysis_service.ensure_hierarchy` |
| **状态** | VERIFIED：segment 必须用章节原文精确切片（修 content_hash_mismatch）；force rebuild 可刷新 active build |
| **文档** | `20-HIERARCHY-REBUILD.md` |

### 数据库迁移

| 属性 | 内容 |
|---|---|
| **职责** | Alembic 管理的 PostgreSQL schema 版本迁移 |
| **主要文件** | `backend/migrations/env.py`、`versions/` |
| **状态** | VERIFIED（head: `518675fa18f8`） |
| **上游** | ORM 模型变更 |
| **下游** | PostgreSQL DDL |
| **文档** | `backend/migrations/README.md` |

### 测试

| 属性 | 内容 |
|---|---|
| **职责** | pytest 后端测试套件：239 个非 e2e + 12 个 RAG e2e，覆盖认证、安全、小说、导入、RAG、混合搜索与评测 |
| **主要文件** | `backend/tests/` |
| **状态** | VERIFIED（239 non-e2e + 12 e2e passed） |
| **上游** | 所有后端模块 |
| **下游** | CI 门禁 |
| **文档** | `backend/tests/README.md` |

---

## Agent Service 模块（Phase 25.2/25.3）

### agent-service（独立 Node 服务）

| 属性 | 内容 |
|---|---|
| **职责** | Novel Agent Runtime：Pi SDK 会话编排、域工具代理、Skill 指令注入、MCP 外部工具隔离、审批策略引擎；通过 `/api/gateway` 服务到服务调用 FastAPI。2026-08-06 起 SSE run 支持**意图→skill 自动路由**（body.skill 缺省时调 FastAPI `route-skill`，Agent 自动选 skill，用户不暴露选择） |
| **主要文件** | `agent-service/src/`（`config.ts`、`server.ts`、`agent/`、`tools/`、`skills/`、`governance/`、`policy/`、`mcp/`、`transport/`） |
| **状态** | VERIFIED（223 vitest passed，tsc clean，2026-08-02） |
| **上游** | FastAPI `/api/gateway`（Bearer 令牌，fail-closed 401） |
| **下游** | `/api/agent`（skill-runs/artifacts/approval-requests）、`/api/agent-tools`（7 域工具） |
| **文档** | `agent-service/qualification/` + `.planning/AGENT-RUNTIME-CONTRACT.md` |

### 后端 Agent Runtime 模块

| 属性 | 内容 |
|---|---|
| **职责** | SkillRegistry/SkillVersion/SkillRun/Artifact/ArtifactRevision/NovelAgentProfile/ApprovalRequest 持久化与权限权威；external_evidence 物化（`prohibited_from_canon=true`，不可发布）；最终 validator 拒绝 mcp:// 引用 |
| **主要文件** | `backend/app/services/agent_runtime/`、`backend/app/api/agent_runs.py`、`agent_artifacts.py`、`agent_approvals.py`、`agent_tools.py`、`gateway.py`、`backend/app/models/agent_runtime.py` |
| **状态** | VERIFIED（集成 24 + adversarial 56 + CI 37 passed，2026-08-02） |
| **上游** | agent-service（经 gateway） |
| **下游** | PostgreSQL（Alembic `20260801_2601` head） |
| **文档** | `backend/app/services/agent_runtime/` + `.planning/AGENT-RUNTIME-CONTRACT.md` |

### QueryPlan 检索与证据模块（Phase 26）

| 属性 | 内容 |
|---|---|
| **职责** | 把读者/分析师问题解析为类型化检索计划（QueryPlan）；8 维度适配器带显式 availability 与 exact→heuristic→stable-reason 回退；确定性 fusion；leaf EvidenceRef 物化与不可变 Frozen Manifest；Reader/Analysis Chat 共享核心，保留不同 anchor |
| **主要文件** | `backend/app/services/queryplan/`（schemas/parser/repository/adapters/fusion/evidence/service）、`backend/app/services/analysis_chat/query_adapter.py` |
| **状态** | VERIFIED（queryplan unit 96 + integration 68 + adversarial，2026-08-02/03） |
| **上游** | Reader Chat / Analysis Chat 消费者 |
| **下游** | reader_chat gateway `business_validate_answer` + `validate_answer_against_manifest`（leaf-only） |
| **文档** | `backend/app/services/queryplan/` + `.planning/phases/26-question-driven-retrieval-and-evidence/` |

---

## 前端模块

### 页面层（App Router）

| 属性 | 内容 |
|---|---|
| **职责** | Next.js 16 App Router 页面路由：首页、书架、阅读、搜索、评测、设置与写作 |
| **主要文件** | `frontend/src/app/layout.tsx`、`page.tsx`、`novels/`、`search/page.tsx`、`eval/page.tsx`、`settings/page.tsx` |
| **状态** | PARTIAL（阅读、搜索、评测和设置可用；写作页仍为骨架） |
| **上游** | 用户浏览器 |
| **下游** | API 客户端（lib/api.ts） |
| **文档** | `frontend/src/app/README.md` |

### 组件层

| 属性 | 内容 |
|---|---|
| **职责** | React 组件：认证门禁、书架、上传、阅读器、搜索和基础 UI 组件 |
| **主要文件** | `auth-gate.tsx`、`bookshelf/`、`novel-upload-dialog.tsx`、`reader/`、`search/`、`ui/` |
| **状态** | PARTIAL（阅读与搜索组件已实现；编辑器、时间线和人物图仍未实现） |
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
前端页面/组件 → lib/api.ts (Axios) → Next.js rewrite → FastAPI 路由
                                                     → Service → ORM → PostgreSQL
                                                     → Service → ChromaDB
                                                     → Service → Ollama / AI Providers

Service → chunking_service → indexing_service → vector_store → ChromaDB
Service → hybrid_search → PostgreSQL BM25 + ChromaDB
Service → eval_service → 三种检索策略 → EvalRun/EvalResult
Service → ai_service → ai_router → LiteLLM → 外部 AI API
```

所有跨模块调用通过依赖注入（FastAPI `Depends`）和 Python 函数调用，不使用消息队列或事件总线。
