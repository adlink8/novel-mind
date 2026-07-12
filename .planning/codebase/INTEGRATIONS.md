# Novel-Mind — 集成文档

## 1. ChromaDB 集成

- 客户端类型：HTTP Client（`chromadb.HttpClient`）
- 连接地址：`host=localhost`，`port=8001`（docker-compose 宿主机端口映射）
- Collection 命名规则：`novel_{novel_id}`（每个小说独立 collection）
- 相似度算法：余弦距离（cosine）
- 所有操作封装在 `VectorStore` 类（`backend/app/services/vector_store.py`）
- 异步模式：内部使用 `asyncio.to_thread` 包装同步 ChromaDB SDK 调用

主要接口：

| 方法 | 说明 |
|---|---|
| `add_chunks(novel_id, chunks)` | 批量写入文本块向量 |
| `search(novel_id, query_embedding)` | 语义搜索（余弦相似度） |
| `delete_novel_chunks(novel_id)` | 删除指定小说的全部向量 |
| `get_chunk_count(novel_id)` | 查询向量数量 |
| `update_chunk_status(novel_id, ...)` | 更新块的 embedding 状态 |

---

## 2. PostgreSQL 集成

- ORM：SQLAlchemy（>=2.0，async 模式）
- 驱动：asyncpg（>=0.30）async 主驱动；psycopg2-binary 备用
- 扩展：pgvector（在 PostgreSQL 16 上启用 `vector` 类型）
- 配置入口：`backend/app/core/database.py`
- 迁移工具：Alembic（>=1.14）

核心 core 模块（`backend/app/core/`）：

| 文件 | 职责 |
|---|---|
| `database.py` | async engine / session 工厂 |
| `security.py` | JWT 鉴权、密码哈希 |
| `crypto.py` | 加密工具 |
| `url_security.py` | URL 安全校验 |
| `logging.py` | 日志配置 |

---

## 3. LiteLLM 集成

- 库：litellm（>=1.83.10）
- 路由策略文件：`backend/app/services/ai_router.py`
- 三种路由模式：

| 模式 | 用途 |
|---|---|
| `quality` | 高质量模型，复杂推理（知识图谱判断、知识单元审核） |
| `balanced` | 均衡模型，日常生成（摘要、标注） |
| `budget` | 低成本模型，批量处理（候选提取、批量分析） |

- 支持多 provider：OpenAI、Anthropic、本地模型（通过 LiteLLM proxy 统一接口）

---

## 4. 知识图谱集成

流水线阶段：

```
candidates → gates → judgments → review queue
```

| 阶段 | 说明 |
|---|---|
| candidates | 从文本块提取候选知识单元（LLM 批量抽取） |
| gates | 过滤门控：去重、质量阈值筛选 |
| judgments | LLM 判断：确认候选是否晋升为正式知识单元 |
| review queue | 人工审核队列：低置信度条目进入等待复核 |

---

## 5. GSD Phase 01 新增接线

- ChromaDB 新增 collection：`knowledge_units`
  - 用于存储已晋升知识单元的向量表示
  - 与 `novel_{novel_id}` 文本块 collection 并列存在

- `promote_to_narrative_unit` 接线：
  - 当 judgment 阶段通过后，调用 `promote_to_narrative_unit(unit_id)`
  - 将知识单元 embedding 写入 `knowledge_units` collection
  - 同时在 PostgreSQL 中更新单元状态为 `promoted`
  - 触发后续叙事单元分析流程
