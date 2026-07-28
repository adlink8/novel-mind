# Codebase Architecture

Generated: 2026-07-05 | Source: actual code

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + SQLAlchemy async + Pydantic v2 |
| Database | PostgreSQL 16 + BM25 tsvector |
| Vector Store | ChromaDB (HTTP API) |
| AI | LiteLLM — ai_router 路由多提供商 |
| Frontend | Next.js (App Router) + React + TypeScript + Tailwind |
| State | Zustand |
| HTTP Client | Axios (withCredentials) |
| Testing | pytest / Vitest |
| CI | GitHub Actions |
| Migrations | Alembic |

---

## 整体架构

```
Browser
  └─ Next.js (Frontend)
       └─ HTTP/REST → FastAPI (Backend)
                          ├─ PostgreSQL   (关系数据 + BM25 全文索引 tsvector)
                          └─ ChromaDB     (向量检索，per-novel collections)
```

FastAPI 应用入口：`backend/app/main.py`

中间件执行顺序（请求进入时）：
  TrailingSlashMiddleware → RequestLoggingMiddleware → CORSMiddleware → 路由处理

挂载路由模块（11 个）：novels / analysis / timeline / characters / fanfiction / models / auth / rag / search / eval / knowledge

---

## 写入链路（Import Pipeline）

```
Upload (POST /api/novels/{id}/import)
  → import_service.create_job()
      # 创建 ImportJob 记录，状态 pending，写入 PostgreSQL
  → import_service.run_job()  [异步，支持过期租约恢复]
      ├─ 解析文本 → 写入 Novel + Chapter 表（PostgreSQL）
      ├─ chunking_service.chunk_novel()
      │    目标块大小：300-500 字
      │    按自然段落分割，合并过短段落
      │    块类型检测：
      │      dialogue     — 引号密度 > 30%
      │      description  — 含描写性关键词（风景/外貌/心理/动作）
      │      scene        — 场景转换标记（时间/地点变化）
      │      narration    — 旁白/背景介绍
      │      paragraph    — 默认
      ├─ 写入 TextChunk 表（PostgreSQL）
      └─ indexing_service.index_novel()
           ├─ 更新 PostgreSQL tsvector 列（BM25 索引，pg_catalog.simple 分词）
           └─ ChromaDB collection upsert（嵌入向量，collection: chunks_{novel_id}）
                └─ 嵌入调用：ai_service → ai_router → 外部 AI 提供商
```

---

## 读取链路（Query / Hybrid Search / RAG）

```
Query (GET /api/search or POST /api/rag)
  → hybrid_search_service.search_novel(novel_id, query)
    或 hybrid_search_service.search_global(query, owner_id)
      ├─ BM25 路径
      │    PostgreSQL tsvector + tsquery + ts_rank_cd
      │    分词器: pg_catalog.simple（按字切分，无需中文扩展）
      └─ Vector 路径
           ai_service.embed(query) → 查询向量
           vector_store.search(collection, query_vector, top_k)
  → hybrid_rerank()
      融合权重: BM25 0.5 + Vector 0.5
      按加权分数排序，去重，截取 top_k
  → Results（TextChunk 列表 + 相关性分数）

RAG 路径追加：
  Results → 构建 prompt → ai_service.generate() → 生成回答
```

---

## 知识图谱管道（Knowledge Pipeline）

```
TextChunk / NarrativeUnit（原始素材）
  → candidates.py
      从文本块提取知识单元候选（实体、关系、事件）
  → gates.py
      规则门控过滤（去重、置信度阈值检查）
  → llm_judge.py
      调用 LLM 对候选进行二次质量判断
  → evidence.py
      为通过判断的候选收集支撑证据（来源段落引用）
  → graph_sync.py
      写入知识图谱节点 / 边（PostgreSQL knowledge 表）
  → projection.py
      图谱投影与查询接口（子图检索、路径查询）
  → ReviewQueue
      低置信度候选写入人工复审队列
```

知识图谱 ORM：`backend/app/models/knowledge.py`
知识图谱 API：`/api/knowledge`（`backend/app/api/knowledge.py`）

---

## GSD Phase 01 新增层：叙事知识单元

Phase 01（`.gsd/phases/01_narrative_knowledge_unit_layer/`）在原有 TextChunk 之上新增叙事语义层。

**PostgreSQL 新增表：`narrative_units`**

| 字段 | 说明 |
|---|---|
| id | 主键 |
| novel_id | 所属小说 |
| chapter_id | 所属章节 |
| unit_type | 单元类型（event / character / relationship / setting 等）|
| content | 叙事内容文本 |
| embedding_id | 对应 ChromaDB 中的向量 ID |
| confidence | 提取置信度 |
| source_chunk_id | 来源 TextChunk |

**ChromaDB 新增 collection：`knowledge_units_{novel_id}`**

每本小说独立 collection，与原有 `chunks_{novel_id}` 并存，存储叙事知识单元的向量表示。

**检索模式扩展（mode 参数）**

| mode | 检索空间 | 适用场景 |
|---|---|---|
| `chunks`（默认）| TextChunk + chunks_{novel_id} | 原始文段精确召回 |
| `units` | narrative_units + knowledge_units_{novel_id} | 叙事语义检索 |
| `hybrid` | 两个空间同时检索后合并排序 | 全面覆盖 |

---

## RAG 理论框架

向量数据库在本系统中扮演**检索特征空间**的角色，而非简单文本缓存：

- **TextChunk（原始文段）**：保留语法结构与局部上下文，适合精确片段召回与关键词对齐。
- **NarrativeUnit（叙事知识单元）**：将原文段落抽象为语义事件、人物关系、情节节点；向量空间中语义距离更准确地映射叙事相关性，而非表面词汇相似性。
- **混合检索（BM25 + Vector，0.5:0.5）**：BM25 捕获词汇精确匹配信号，Vector 捕获语义相似信号，融合后覆盖各自的召回盲区。
- **知识图谱层**：提供结构化关系推理，补充纯向量检索无法表达的显式关联（人物共现、时间线因果等）。

整体 RAG 架构演进方向：从"检索原始文段"→"检索叙事语义表示"，使生成回答具备更强的故事理解与推理能力。
