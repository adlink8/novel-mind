# 03 — 数据模型与实体关系

NovelMind 使用 PostgreSQL 16 作为主数据库，ChromaDB 作为向量存储。Phase 04 新增知识图谱候选、证据、判定和 review audit 表；Neo4j 仍是可选投影边界，默认不启用。

## 核心实体

### User — 用户

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int (PK) | 主键 |
| `username` | str | 用户名（unique, index） |
| `email` | str | 邮箱（unique） |
| `hashed_password` | str | bcrypt 密码哈希 |
| `is_active` | bool | 软删除标记 |
| `is_superuser` | bool | 管理员标记 |

**来源**: `backend/app/models/user.py`

### Novel — 小说

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int (PK) | 主键 |
| `owner_id` | int (FK → users.id) | 所有者，级联删除 |
| `title` | str | 标题（index） |
| `author` | str? | 作者 |
| `chapter_count` | int | 章节总数 |
| `word_count` | int | 总字数 |
| `status` | str | importing / ready / analyzing / analyzed |
| `source_path` | str? | 原始 TXT 存储路径 |
| `reading_progress` | JSON? | {chapter_id, progress_percent} |
| `style_fingerprint` | JSON? | 文风分析结果 |

**来源**: `backend/app/models/novel.py`

### Chapter — 章节

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int (PK) | 主键 |
| `novel_id` | int (FK → novels.id CASCADE) | 所属小说 |
| `chapter_number` | int | 章节序号 |
| `title` | str | 章节标题 |
| `content` | str (Text) | 完整正文 |
| `word_count` | int | 字数 |

**来源**: `backend/app/models/novel.py`

### TextChunk — 文本块

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int (PK) | 主键 |
| `novel_id` | int (FK → novels.id CASCADE) | 所属小说 |
| `chapter_id` | int? (FK → chapters.id SET NULL) | 所属章节（删章时保留 chunk） |
| `chunk_index` | int | 章节内序号 |
| `content` | str (Text) | 文本内容（300-500 字） |
| `chunk_type` | str | scene / dialogue / description / narration / paragraph |
| `embedding_status` | str | pending / embedded / failed |
| `metadata_json` | JSON? | {characters, location, time} |

**来源**: `backend/app/models/text_chunk.py`

### ImportJob — 导入任务

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int (PK) | 主键 |
| `novel_id` | int? (FK → novels.id CASCADE) | 关联小说 |
| `status` | str | pending → uploading → detecting → parsing → chunking → embedding → ready / failed |
| `progress` | int | 0-100 百分比 |
| `message` | str | 状态描述 |
| `error_detail` | str? | 错误详情 |
| `retry_count` | int | 已重试次数 |
| `max_retries` | int | 最大重试次数（默认 3） |

**来源**: `backend/app/models/import_job.py`

### AIModelConfig — AI 模型配置

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int (PK) | 主键 |
| `owner_id` | int (FK → users.id CASCADE) | 所有者 |
| `name` | str | 显示名称（同用户下唯一） |
| `provider` | str | openai / anthropic / ollama / custom |
| `model_id` | str | 如 gpt-4o、claude-3.5-sonnet |
| `api_key` | str? | Fernet 加密密文（`enc:v1` 前缀） |
| `base_url` | str? | 自定义 API 地址 |
| `tier` | str | quality / balanced / budget |
| `is_default` | bool | 默认模型标记 |

**来源**: `backend/app/models/ai_model.py`

### AIUsageLog — AI 调用日志

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int (PK) | 主键 |
| `model_name` | str | 模型名称 |
| `task_type` | str | analysis / embedding / fanfiction / summary / timeline |
| `input_tokens` | int | 输入 token |
| `output_tokens` | int | 输出 token |
| `cost_usd` | float | 费用（美元） |
| `latency_ms` | int | 延迟（毫秒） |
| `status` | str | success / failed / timeout |
| `novel_id` | int? | 关联小说（无 FK 约束，仅逻辑关联） |

**来源**: `backend/app/models/ai_usage_log.py`

### RAG 评测模型

| 模型 | 表名 | 职责 |
|---|---|---|
| EvalDataset | `eval_datasets` | 测试问题、题型、难度与 gold chunk 标注 |
| EvalRun | `eval_runs` | 一次评测运行的策略、状态、聚合指标和延迟 |
| EvalResult | `eval_results` | 单题检索结果、指标与错误案例标记 |

**来源**: `backend/app/models/eval.py`

### 知识图谱候选、证据与判定模型

Phase 04 的知识图谱链路以 PostgreSQL audit row 为事实源。召回信号只能生成候选和 evidence package；只有通过 LLM 语义判定和 deterministic gates 的 `KnowledgeRelationJudgment(status='accepted', gate_status='accepted')` 才能作为 accepted graph fact 的输入。

| 模型 | 表名 | 职责 |
|---|---|---|
| KnowledgeExtractionRun | `knowledge_extraction_runs` | 一次 CLI/批处理运行，记录 domain/ontology profile、候选/判定/accepted/review 计数、token、cost、latency 和 metrics summary |
| KnowledgeEvidenceRef | `knowledge_evidence_refs` | evidence ref 定位器，绑定 owner、novel、run、source_type、chunk/chapter locator 和 excerpt |
| KnowledgeEntityCandidate | `knowledge_entity_candidates` | deterministic 或 LLM 输出前的实体候选，带 evidence refs、domain profile 和 review status |
| KnowledgeEventCandidate | `knowledge_event_candidates` | 事件候选，带 time/location/participant/evidence refs |
| KnowledgeRelationCandidate | `knowledge_relation_candidates` | 关系候选；保存 recall_signals 和 package_snapshot，但不是图事实 |
| KnowledgeRelationJudgment | `knowledge_relation_judgments` | LLM 结构化语义判定及 schema/evidence/threshold/conflict gate 状态 |
| KnowledgeReviewQueue | `knowledge_review_queue` | 低置信度、risk flag、冲突或人工复核项 |

**来源**: `backend/app/models/knowledge.py`

### 知识图谱离线评测 Fixture

| 文件 | 领域 | 内容 |
|---|---|---|
| `backend/evals/knowledge_graph_fiction_sample.json` | fiction | 10 个 labeled examples，覆盖 character relation、conflict、foreshadowing、event sequence、alias/family 等 |
| `backend/evals/knowledge_graph_history_sample.json` | history | 10 个 labeled examples，覆盖 person/organization、rule、succession、causality、temporal sequence、source conflict 等 |

`backend/scripts/run_knowledge_graph_eval.py` 离线读取这些 fixture，分别报告 recall signal quality、judgment quality、accepted graph fact quality、faithfulness、cost/latency。该 eval 不写库，live LLM faithfulness check 为可选且不可用时报告 blocked，不阻塞 deterministic tests。

### 后续业务预留模型（表已存在，业务未实现）

| 模型 | 表名 | 状态 |
|---|---|---|
| AnalysisResult | `analysis_results` | MISSING（表存在，代码未接入） |
| Character | `characters` | MISSING |
| CharacterRelation | `character_relations` | MISSING |
| TimelineEvent | `timeline_events` | MISSING |
| FanFiction | `fan_fictions` | MISSING |
| FanFictionChapter | `fanfiction_chapters` | MISSING |

## 实体关系图

```
users (1)
  ├──< ai_model_configs (owner_id, CASCADE)
  └──< novels (owner_id, CASCADE)
            ├──< chapters (novel_id, CASCADE)
            ├──< text_chunks (novel_id CASCADE, chapter_id SET NULL)
            ├──< import_jobs (novel_id CASCADE)
            ├──< eval_datasets (novel_id CASCADE)
            ├──< eval_runs (novel_id CASCADE)
            │     └──< eval_results (run_id CASCADE, dataset_id CASCADE)
            ├──< knowledge_extraction_runs (owner_id + novel_id CASCADE)
            │     ├──< knowledge_evidence_refs (run_id CASCADE)
            │     ├──< knowledge_entity_candidates (run_id CASCADE)
            │     ├──< knowledge_event_candidates (run_id CASCADE)
            │     ├──< knowledge_relation_candidates (run_id CASCADE)
            │     │     └──< knowledge_relation_judgments (candidate_id CASCADE)
            │     └──< knowledge_review_queue (run_id CASCADE)
            ├──< analysis_results (novel_id CASCADE, chapter_id SET NULL)
            ├──< characters (novel_id CASCADE)
            │     └──< character_relations (source + target, CASCADE)
            ├──< timeline_events (novel_id CASCADE, chapter_id SET NULL)
            ├──< fan_fictions (novel_id CASCADE, parent_chapter_id SET NULL)
            │     └──< fanfiction_chapters (fanfiction_id CASCADE)
            └──< ai_usage_logs (novel_id 无 FK，仅逻辑关联)
```

## Owner 隔离规则

所有资源操作必须校验 `owner_id`：

- **Novel**: 列表只返回 `owner_id == current_user.id` 的记录
- **Chapter**: 通过 `novel.owner_id` 间接校验
- **AIModelConfig**: 列表/更新/删除/测试均校验 `owner_id`
- **TextChunk**: 通过 `novel.owner_id` 间接校验
- **Knowledge\***: 表内直接保存 `owner_id`，API 和 gate/projection 查询同时校验 owner、novel、run 范围
- 跨用户访问统一返回 **404**（不暴露资源是否存在）

## 存储分布

| 数据 | 存储 | 说明 |
|---|---|---|
| 用户/小说/章节/AI 配置 | PostgreSQL | 主表，关系数据 |
| 文本块内容 | PostgreSQL | Text 字段 |
| 向量 embedding | ChromaDB | 768 维向量，collection 命名：`novel_{novel_id}` |
| TXT 源文件 | 文件系统 | `backend/uploads/`，随机十六进制文件名 |
| API Key 密文 | PostgreSQL | `enc:v1` 前缀的 Fernet 密文 |

## 关键注意事项

1. **API Key 绝不存明文** — 写入时加密，读取时解密。支持 key rotation
2. **小说详情不返回 `source_path`** — API 响应中排除文件系统路径
3. **章节列表不返回正文** — 使用 `ChapterSummaryResponse`，正文通过单章接口获取
4. **删除补偿** — 文件与数据库双写失败时，失败方执行补偿清理
