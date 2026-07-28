# backend/app/services — 业务逻辑层

核心业务逻辑实现层，被 API 路由层调用，向下依赖 models 和 core。每个 service 文件负责一个业务域的完整逻辑。

## 模块

| 文件 | 大小 | 职责 |
|---|---|---|
| `novel_service.py` | 18.7 KB | **最大模块**。小说文件上传（随机文件名、根目录 containment）、GB18030 / Big5 / Shift_JIS 多编码自动检测、章节分割、CRUD、删除时文件 + DB 补偿清理 |
| `import_service.py` | 11.3 KB | 导入任务状态机，租约并发控制（lease_id + 300s 超时），SHA-256 幂等键（content_hash），取消支持（cancel_job），重启恢复（recover_stale_jobs），异步后台任务处理 |
| `chunking_service.py` | 10.4 KB | 文本分块 — 语义分块算法，三级分块策略（章节 / 场景 / 段落），块类型检测 |
| `vector_store.py` | 9.1 KB | ChromaDB 向量存储封装 — 文档写入、语义搜索、集合删除 |
| `indexing_service.py` | 11.6 KB | 索引管线 — 协调分块 + embedding 生成 + 向量存储写入，进度报告 |
| `ai_service.py` | 4.9 KB | LiteLLM 统一封装 — chat / embedding / stream_chat，多提供商统一接口；调用后写 ai_usage_logs 用量日志（失败不影响主调用） |
| `ai_router.py` | 8.0 KB | AI 智能路由器 — 按任务类型和路由层级选择最优模型；全局偏好由 settings API 持久化、启动时恢复 |
| `settings_service.py` | — | app_settings 键值表读写（当前：routing_preference） |
| `hybrid_search.py` | — | PostgreSQL tsvector + Chroma 向量结果融合，返回章节/chunk/evidence |
| `eval_service.py` | — | bm25/baseline_vector/hybrid_search 评测，Recall/Precision/MRR/NDCG 与错误案例 |
| `analysis_service.py` | — | 通用剧情/人物/主题等分析读写与 hierarchy 状态 |

## 包目录（分析与叙事）

| 目录 | 职责 |
|---|---|
| `timeline/` | Phase 08：事件提取、worker、promotion、query、overrides |
| `relationships/` | Phase 09：候选、判定、gates、worker、graph query；无 accepted 时 provisional 共现回退 |
| `relationships/timeline_kg_backfill.py` | 从 active timeline 确定性回填 Characters + KG accepted +（可选）relationship observations |
| `clues/` | Phase 11：候选、evidence package、gates、worker、query；later windows 在 package 构建前 clamp（≤4 chapters） |
| `knowledge/` | Phase 04 KG 候选/判定/投影 |
| `narrative_memory/` | 叙事记忆 build/audit/qualification（样例数据可能仍为空） |
| `reader_chat/` | 阅读页对话检索与 worker |
| `chunking/` | 分层分块 build/promote/reconcile |

回填 CLI：`backend/scripts/backfill_relationship_kg_from_timeline.py`（`--novel-id` + `--write` / `--dry-run`）。

## 服务间依赖

```
api/routers
    │
    ▼
services/
├── novel_service.py       ← 独立：文件 I/O + DB
├── import_service.py      ← 依赖 novel_service + indexing_service
├── indexing_service.py    ← 依赖 chunking_service + vector_store + ai_service
├── chunking_service.py    ← 独立：纯文本处理
├── vector_store.py        ← 依赖 ChromaDB HTTP API
├── hybrid_search.py       ← 依赖 PostgreSQL + vector_store + ai_service
├── eval_service.py        ← 依赖 hybrid_search + vector_store + eval ORM
├── analysis_service.py    ← 通用分析 + hierarchy
├── timeline/              ← Phase 08 时间线
├── relationships/         ← Phase 09 关系（含 timeline_kg_backfill）
├── clues/                 ← Phase 11 线索
├── knowledge/             ← Phase 04 KG
├── narrative_memory/      ← 叙事记忆
├── ai_service.py          ← 依赖 ai_router + LiteLLM
└── ai_router.py           ← 依赖 models.AIModelConfig
```

## 约定

- Service 不直接处理 HTTP 请求/响应，只接受 Python 对象
- 异常通过自定义异常类抛出，由 API 层的异常处理器统一转换为 HTTP 响应
- 文件操作使用 `aiofiles` 异步 I/O
- 所有 DB 操作使用 `sqlalchemy.ext.asyncio.AsyncSession`
