# Technical Concerns & Known Debt
<!-- generated: 2026-07-05 -->

---

## C-01  GSD Phase 01 — narrative_units 层未实施

**状态**: Planned（未开始）

- `narrative_units` 表未创建（无 Alembic migration）
- `services/knowledge/promote.py` 不存在
- `services/knowledge/gates.py` 末尾 +2 行的 `promote_judgment` 钩子未接入
- `services/narrative_indexing_service.py` 不存在
- `api/rag.py` 的 `mode=units` 分支未添加
- Chroma `knowledge_units_{novel_id}` collection 未初始化

**影响**: Wave 2 无法执行；`/search?mode=units` 路由不存在；当前 RAG 只能返回原文 chunk。

**阻塞**: 需先完成 C-04（确认 candidates 不为空）。

---

## C-02  KnowledgeRelationJudgment 字段确认（Wave 2 接线必读）

**已通过读取 `backend/app/models/knowledge.py` 确认**，字段如下：

| 关注点 | 实际字段名 | 备注 |
|--------|-----------|------|
| accepted 状态精确拼写 | `status = "accepted"` | 来自 `JUDGMENT_STATUSES` 元组 |
| 关系类型字段 | `relation_type: String(80)` | 枚举值见 `FICTION_RELATION_TYPES` / `HISTORY_RELATION_TYPES` |
| 置信度字段 | `confidence: Mapped[float]` | 非空，默认 0.0 |
| chunk 引用字段 | **不存在 `chunk_ids`** | chunk 引用存于 `evidence_refs: Mapped[list]`（JSON） |
| 摘要/答案字段 | **不存在 `summary` / `evidence_excerpt`** | 可用字段：`rationale: Text`（nullable）、`structured_output: JSON` |

**风险**: `promote.py` 构造 narrative unit 的 answer 文本只能从 `rationale` 或
`structured_output` 中提取，若 LLM 未填写 `rationale`，answer 质量下降。

---

## C-03  ChromaDB 版本锁定

- 依赖版本：`chromadb==0.4.0`（requirements.txt 中锁定）
- ChromaDB 0.5.x 起 collection API 有 breaking change（`get_or_create_collection` 签名变化）
- 升级路径需迁移 embedding 存储，现有 collection 无法直接复用

**建议**: 升级前在独立环境验证 collection 兼容性；暂不升级，记录为已知债。

---

## C-04  知识图谱 candidate 数量（Phase 01 Wave 2 前提）

- Wave 2 依赖 `knowledge_relation_judgments` 表中 `status='accepted'` 的行
- 若提取 run 从未执行，accepted candidate 数量为 0，Wave 2 写入无数据
- 尚无自动化检查脚本确认 candidate 数量

**建议**: Wave 2 前执行 `SELECT COUNT(*) FROM knowledge_relation_judgments WHERE status='accepted'`；若为 0，先触发一次 extraction run。

---

## C-05  PostgreSQL 异步连接池配置

- 后端使用 SQLAlchemy async engine（asyncpg）
- 连接池参数（`pool_size`、`max_overflow`、`pool_timeout`）未在代码中显式配置，依赖 SQLAlchemy 默认值（pool_size=5, max_overflow=10）
- 知识图谱批处理（大量 bulk insert）可能触发连接耗尽

**建议**: 在 `database.py` / `config.py` 显式设置连接池参数，并在批处理路径加连接重试逻辑。

---

## C-06  分块策略局限

来源：`backend/app/services/chunking_service.py`

### 硬编码字符限制
- `min_chunk_size=300, max_chunk_size=500`（字符数，非词数）
- 构造函数可传参，但全局单例 `chunking_service = ChunkingService()` 使用默认值，调用方无法按需调整

### 跨场景边界问题
- `_detect_chunk_type` 在分块后检测类型，不影响分割边界
- 场景转换标记（`SCENE_MARKERS`）只用于类型标注，分割时不在场景边界切断
- 结果：同一 chunk 可能跨越两个场景，导致检索噪声

### 短段落合并风险
- `_split_into_paragraphs` 将 < 50 字的段落与下一段合并
- 可能将独立对话行（常见于短对白）与后续叙述合并，混淆对话/叙述语义

### 无 chunk 重叠
- 相邻 chunk 之间无 overlap window
- 边界处的上下文信息在检索时可能丢失

### 顺序处理
- `chunk_novel` 对章节顺序迭代，未并发；大型长篇小说处理时间线性增长

---

## C-07  BM25 中文分词依赖 pg_catalog.simple

- 当前 BM25 全文索引使用 PostgreSQL 内置的 `simple` 文本搜索配置
- `simple` 不做中文分词，直接按字符分割，对多字词组（人名、地名）召回率低
- 升级选项：`zhparser`（需编译安装）或 `pg_jieba`

**建议**: 中期升级至 `zhparser`，近期可通过在 `tsvector` 中手动插入人名/地名关键词缓解。

---

## C-08  前端 Next.js 未启用 TypeScript strict mode

- `frontend/tsconfig.json` 未配置（或文件不存在于当前工作树）
- TypeScript strict mode（`noImplicitAny`、`strictNullChecks` 等）未强制开启
- 接口类型错误可能在运行时才暴露

**建议**: 在 `tsconfig.json` 中设置 `"strict": true`，逐步修复现有类型错误后锁定。

---

## 优先级汇总

| ID | 关切 | 优先级 | 阻塞 Wave 2 |
|----|------|--------|------------|
| C-01 | Phase 01 未实施 | 高 | 是 |
| C-02 | Judgment 字段确认 | 高（已确认）| 是（信息已就绪）|
| C-04 | Candidate 数量为零 | 高 | 是 |
| C-05 | 连接池配置 | 中 | 否 |
| C-06 | 分块策略局限 | 中 | 否 |
| C-03 | ChromaDB 版本锁定 | 低 | 否 |
| C-07 | BM25 中文分词 | 低 | 否 |
| C-08 | TS strict mode | 低 | 否 |
