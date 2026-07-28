# Stack Research

**Domain:** NovelMind v0.8 分层叙事记忆与层级 RAG 纵向 MVP  
**Researched:** 2026-07-15  
**Confidence:** HIGH（现有栈与代码事实）；MEDIUM（层级检索质量收益，仍需单本小说评测验证）

## Executive Recommendation

不引入新的 RAG 框架、图数据库或聚类依赖。以现有 PostgreSQL 16 为版本、谱系、父子边、证据引用和审计真相源；以现有 SQLAlchemy/Pydantic 定义严格契约；仅在候选版本需要语义召回时复用已固定的 ChromaDB 1.5.9 与现有 embedding 服务。实现“固定叙事层级 + 自下而上生成 + 自上而下检索”，借鉴 RAPTOR 的多尺度摘要思想，但不复制其 UMAP/GMM 聚类树；借鉴 GraphRAG 的全局/局部查询分流思想，但不安装或运行 GraphRAG 管线。

本里程碑只补建 `Chapter State → Story Arc/Volume → Global Story Model`。既有 `chapter → scene → evidence`、时间线、人物关系和线索均作为只读输入；候选版本未验证前不得切换任何 active pointer，也不得替换现有聊天检索。

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| PostgreSQL | 16（现有 Compose 基线） | 分层记忆版本、父子关系、evidence ref、状态差量、checkpoint、manifest 与候选指针 | `chunk_builds`、`chunk_hierarchy_nodes`、`analysis_versions` 和各模块 pointer/journal 已证明该模式；递归 CTE 原生适合树遍历，无需图数据库 |
| SQLAlchemy async | 2.0.x（本机 2.0.50） | ORM、事务、行锁、候选版本和局部重建查询 | 与全后端一致；可直接复用 `AsyncSession`、CAS/pointer 和 PostgreSQL 集成测试模式 |
| Pydantic | 2.x（本机 2.13.4） | `ChapterState`、`StoryArc`、`GlobalStoryModel`、检索 manifest 的严格 schema | 现有 chunking/timeline/reader-chat 已使用 `extra="forbid"`、validator 和 schema hash，能阻止模型输出越权或漂移 |
| ChromaDB | 1.5.9（已固定） | 候选上层摘要的可选语义召回；叶子仍复用既有向量索引 | 当前生产实现已使用 Chroma HTTP 与传入 query embedding；官方查询支持 `ids` 和 `where` 元数据过滤，可按 `novel_id/build_id/version_id/level/cutoff` 限定候选 |
| 现有 LLM gateway + durable worker | 项目当前版本 | 自下而上生成、预算、精确缓存、修复一次、暂停/续跑 | timeline worker 已具备固定 deployment/prompt/schema、预算预留、章级 checkpoint、缓存与取消语义；应复用模式，不新增作业框架 |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Alembic | 1.x（本机 1.18.4） | 新增候选版本、节点、边、证据引用和运行 checkpoint 表 | 仅做 additive migration；旧表和 active pointer 保持不变 |
| asyncpg | 0.30+（本机 0.31.0） | PostgreSQL 异步驱动 | 沿用现有数据库连接；不增加第二套访问层 |
| LiteLLM | `>=1.83.10`（现有约束） | 调用项目已配置模型 | 只通过现有 gateway 使用；记录 deployment、prompt/schema/config hash 与价格快照 |
| `hashlib` / `json` | Python 标准库 | canonical checksum、节点身份与 manifest | 沿用现有 canonical JSON + SHA-256 模式；无需第三方内容寻址库 |
| PostgreSQL `tsvector` | PostgreSQL 16 内置 | 上层摘要的词法召回与无 Chroma 降级 | 对候选上层节点建立 GIN 全文索引；与现有 `TextChunk.search_vector` 策略一致 |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| pytest + pytest-asyncio | 契约、lineage、局部重建、spoiler 与检索测试 | 先单元测试纯函数，再用真实 PostgreSQL 验证 FK、唯一约束、CAS、递归遍历和并发 |
| 现有 RAG eval/qualification 基础设施 | 对比 leaf-only 与 coarse-to-fine | 当前 confirmed 题目和运行指标不足，v0.8 dry-run 必须单独记录 coverage、leaf evidence recall、faithfulness、成本与失败范围，不得把未确认题目当发布证据 |
| `EXPLAIN (ANALYZE, BUFFERS)` | 验证树遍历和候选过滤查询 | 检查 `(version_id, level, chapter range)`、`(version_id, parent_id)`、evidence ref 唯一键和全文 GIN 索引 |

## Installation

```bash
# v0.8 纵向 MVP 不新增生产依赖。
cd backend
pip install -r requirements.txt -r requirements-dev.txt

# 使用现有迁移与测试工具；不得安装 graphrag、raptor、langchain、langgraph、networkx、neo4j driver、umap-learn 或 sklearn。
alembic upgrade head
pytest
```

## Architecture Fit

### Persisted shape

采用关系型邻接表，不把上层摘要塞回现有 `chunk_hierarchy_nodes.level` 三值契约，也不覆盖 timeline/relationship/clue 表：

```text
NarrativeMemoryVersion (candidate only in MVP)
└─ GlobalStoryModel
   └─ StoryArc / Volume (contiguous chapter range)
      └─ ChapterState
         └─ source links → active Chapter / Scene / Evidence
```

建议把高频过滤字段规范化为列：`owner_id`、`novel_id`、`version_id`、`node_id`、`level`、`parent_id`、`chapter_start`、`chapter_end`、`status`、`content_hash`、`source_snapshot_hash`、`hierarchy_build_id`。结构化差量（人物状态、关系变化、新增/回收线索、世界状态）可存 JSONB，但证据引用、父子边和可见范围必须是可约束、可索引的独立行。PostgreSQL 官方建议大多数可查询 JSON 数据使用 JSONB，因为其无需重复解析且支持索引；这不意味着把核心关系隐藏在 JSON 内。

每个上层事实都必须至少有一条可递归展开的证据路径，最终落到 active source snapshot 中的 evidence `node_id + chapter_id + [source_start, source_end) + content_hash`。摘要是检索路由和解释材料，不是事实权威。

### Build path: bottom-up, checkpointed, incremental

1. 资格审计：校验 active hierarchy build、source snapshot、manifest checksum 和 evidence offsets；不合格章节只标记待补建。
2. Chapter State：按章读取合格 scene/evidence，并以只读方式吸收同版本 timeline/relationship/clue 资产；模型只输出严格 schema 的状态差量与 evidence IDs。
3. Story Arc/Volume：MVP 使用章节顺序和已有卷界/确定性连续窗口组成父节点；不要先做语义聚类。只有子 Chapter State checksum 变化时重建该 arc。
4. Global Story Model：只由通过验证的 arc 归纳；保留输入节点 checksum 列表。
5. 每层写入候选版本、stage checkpoint、artifact checksum、调用成本和失败原因；单节点失败不阻断已完成兄弟节点，也不触发全书重跑。
6. dry-run 只产出报告与候选索引，绝不写现有 active pointers。

### Query path: coarse-to-fine, evidence-final

1. 在检索前解析并固化 spoiler cutoff；先过滤 `chapter_start/end`，不能先召回后遮盖。
2. 全局问题先检索 Global/Arc；局部实体或当前章问题可从 Chapter State/既有 leaf hybrid search 起步。MVP 可用简单、可审计的意图规则，不需要模型路由 Agent。
3. 对上层候选使用 PostgreSQL `tsvector`，可选叠加同一 embedding 模型写入的 Chroma 候选 collection；Chroma metadata 必须包含 version/build/level/chapter range，且查询使用 `where` 预过滤。
4. 通过 `parent_id`/独立 edge 表向下展开少量候选。PostgreSQL 16 的 `WITH RECURSIVE` 原生支持树遍历、搜索顺序和 cycle detection；层数固定且很浅时也可逐层查询，避免无界递归。
5. 在候选章节/场景内复用现有 BM25 + Chroma leaf hybrid retrieval，最终返回原文 evidence，而不是只返回上层摘要。
6. 将 version/build/checksum/cutoff/候选路径/omitted counts 冻结到 reader-chat context manifest；v0.8 仅旁路评测，不替换当前聊天读路径。

## RAPTOR vs GraphRAG vs Recommended Hierarchical Retrieval

| Approach | What It Requires | Fit for NovelMind v0.8 | Decision |
|----------|------------------|-------------------------|----------|
| RAPTOR | 对叶子 embedding，递归聚类，再由 LLM 为每个 cluster 摘要；论文实现以多尺度树共同检索 | 多尺度摘要非常契合长篇小说，但无监督聚类会绕开已有 chapter/scene/evidence 谱系，并引入聚类参数、重建成本和难解释的跨章父子关系 | **借鉴，不安装。** 保留 bottom-up summary + multi-level retrieval；用叙事层级和连续章节替代 UMAP/GMM 树 |
| Microsoft GraphRAG | LLM 实体/关系/claim 提取、实体归并、Hierarchical Leiden community detection、多层 community reports、embedding、独立输出/迁移与 prompt tuning | 适合跨语料全局 sensemaking；但 NovelMind 已有 timeline/relationship/clue 事实层，整套索引会重复提取、增加大量模型调用，并建立第二套真相和版本格式 | **MVP 不采用。** 仅借鉴 local/global query 分流与高层报告概念 |
| 固定叙事层级 coarse-to-fine | 现有 evidence tree、连续章节 arc、严格摘要契约、PostgreSQL 邻接表、现有 hybrid retrieval | 最大化复用现有资产；每层可追溯、可增量、可局部失败；最容易在单本 dry-run 中测量收益与成本 | **推荐。** 作为 v0.8 唯一实现路线 |

RAPTOR（ICLR 2024）明确采用“递归 embedding、clustering、summarizing”从底向上建树，并在推理时跨抽象层检索；因此本项目可以采用其核心认知结构，但不能把论文算法名等同于当前叙事树。GraphRAG 官方默认 dataflow 则包含 LLM 实体/关系提取、Hierarchical Leiden、community reports 和多类 embedding；官方还警告 indexing 会消耗大量 LLM 资源并要求针对数据调 prompt，超出本次“复用旧资产、单书 dry-run”的边界。

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| PostgreSQL 邻接表 + recursive CTE | Neo4j | 只有在已验证需求需要大量任意深度、多关系类型的在线图遍历，并且 PostgreSQL 查询已成为实测瓶颈时 |
| 现有 Chroma 1.5.9 候选 collection | 迁移到 pgvector | 只有后续独立里程碑证明双存储运维成本显著，且完成向量一致性、索引参数、回滚和性能迁移方案时；v0.8 不应顺带迁移 |
| 连续章节/卷界构造 Story Arc | RAPTOR 语义聚类 | 当固定结构在经确认的全局问答集上持续失败，并能证明跨远距离聚类的增益覆盖额外成本与谱系复杂度时 |
| 现有领域事实 +叙事父子树 | 完整 GraphRAG | 当产品目标变成跨大量小说的开放式全局主题分析，且愿意接受独立图索引、prompt tuning、迁移和显著 indexing 成本时 |
| 显式检索函数 | LangChain/LangGraph Agent | 当未来确有多工具、动态分支和人工审批编排需求时；确定性 3–5 层下降不需要 Agent runtime |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `graphrag` package | 会带入独立配置、输出格式、图抽取、community detection、prompt 和版本迁移；重复现有人物关系/时间线资产 | 本项目 SQLAlchemy 模型 + 只读领域 reader +固定叙事层级 |
| RAPTOR reference package / `umap-learn` / GMM 聚类依赖 | v0.8 没有经验证的聚类需求；会令父子关系随参数漂移并削弱 evidence lineage | 章节/卷界/连续窗口的确定性构建 |
| Neo4j/APOC | 当前树只有单一父子向下路径，PostgreSQL 已是版本和权限真相源；引入双写与一致性风险 | PostgreSQL FK、索引和 recursive CTE |
| NetworkX / Leiden / graspologic | 不需要 community detection；在应用内建立另一份图会绕开数据库约束 | SQL 查询和已有 relationship projection（只读） |
| LangChain / LangGraph / LlamaIndex | 现有 gateway、budget、checkpoint、citation 和 spoiler 安全契约已成熟；框架抽象会产生第二套生命周期 | 显式 service functions + durable workers |
| 新 embedding 模型或 reranker | 会令候选上层与现有叶子向量不可直接比较，并扩大模型评测范围 | 复用当前 embedding provider/model/config hash；先做 RRF/现有融合 |
| 在 v0.8 将 Chroma 迁入 pgvector | 虽然 Compose 与依赖已准备 pgvector，但当前实际索引和检索真相是 Chroma；迁移不属于新增分层能力 | Chroma candidate collection + PostgreSQL metadata truth |
| 直接复用/扩展旧 active pointer | dry-run 误切换会改变 timeline/chat/clue 用户可见事实 | 独立 candidate version；MVP 不提供 promotion 命令 |
| 只存摘要、不存 evidence edge | 会放大逐层幻觉且无法验收“最终回落原文” | 独立 evidence refs + offset/hash/source snapshot 校验 |

## Don't Hand-Roll

- 不手写向量索引或余弦 ANN；使用现有 Chroma。
- 不在 Python 中维护权威树副本；持久关系、唯一性和事务由 PostgreSQL 管理。
- 不自创第二套预算、模型重试、缓存或 job lease；复用 timeline worker/gateway 模式。
- 不让 LLM 自行选择 `owner_id`、`novel_id`、version、parent、status、active pointer 或 spoiler cutoff；这些均由服务端从冻结输入确定。
- 不让上层摘要直接成为答案证据；必须下降并重新校验 leaf evidence。
- 不为 MVP 手写“智能 Agent 路由”；局部/全局意图采用确定性规则并记录 route reason。

## Stack Patterns by Variant

**如果 Chroma 可用：**
- 上层候选摘要写入以 candidate version/build 隔离的现有命名 collection。
- 使用同一 embedding 配置，并通过 metadata `where` 先限制版本、层级与 spoiler 范围。
- PostgreSQL 保存 vector ID 清单和 reconcile checksum；Chroma 不是谱系权威。

**如果 Chroma 不可用：**
- 使用 PostgreSQL `tsvector` + 结构/实体过滤完成 coarse routing。
- source status 明确为 `unavailable`，不伪装空结果；dry-run 仍可验证 lineage 和 bottom-up build，但向量质量项标记未测。

**如果小说有可靠卷界：**
- Story Arc 首先按卷界构建，卷内过长时再用确定性连续窗口分段。

**如果没有卷界：**
- 用可配置但版本化的连续章节窗口/重叠策略；不要在首版启用语义聚类。

**如果某章 source checksum 变化：**
- 仅使该 Chapter State、包含它的 Story Arc 及 Global Story Model 失效；兄弟 Chapter State 复用。

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| PostgreSQL 16 | SQLAlchemy 2.0.x + asyncpg 0.30+ | 当前项目既有组合；新增迁移必须在真实 PostgreSQL 验证，SQLite 不能证明 recursive CTE/JSONB/锁语义 |
| ChromaDB client 1.5.9 | Chroma server 1.5.9 | CI 已固定 server/client 1.5.9；`docker-compose.yml` 的 `latest` 有漂移风险，但修改镜像不属于本研究文件范围，实施计划应将版本一致性列为 gate |
| Pydantic 2.13.x | FastAPI 0.115+（本机 0.136.3） | 使用 v2 validators 与 `ConfigDict(extra="forbid")`；不要引入依赖 Pydantic v1 的 RAG 框架 |
| 候选上层 embedding | 当前 leaf embedding model + dimension | Chroma 官方要求 query embedding 与 collection 维度一致；model/config hash 变化必须新建候选索引，不得混写 |
| GraphRAG current docs | Python 3.10–3.12 | 当前项目环境可能高于该范围，且 GraphRAG 官方提示版本迁移/重索引；这是不引入它的附加理由，不是主理由 |

## Verification Implications

- 资产审计：对每章输出 `reusable / missing / stale / source_unavailable`，以及 hash 不一致的精确原因。
- 构建正确性：每个上层节点均能重算 child manifest；随机抽样和全量约束测试都必须下降到 active evidence hash。
- 局部重建：修改一个测试章的 snapshot 后，仅该章、父 arc 和 global checksum 改变；其他 Chapter State byte-identical。
- spoiler：对截止章之后才出现的实体/事件设计对抗问题；上层召回、向下展开和最终 evidence 三处均不得泄漏。
- 检索对比：同一已确认问题集比较 leaf-only 与 coarse-to-fine 的 leaf evidence Recall@k、MRR/NDCG、faithfulness、回答完整度、延迟、token/cost。
- 失败隔离：模拟 LLM、Chroma、relationship/clue reader 单独故障；已有结果保持、状态明确、不得切 active pointer。
- dry-run 完成条件：coverage/cost/failure report 与候选 manifest 完整；不是“模型跑完”或“摘要看起来合理”。

## Sources

### External primary/official sources

- [RAPTOR, ICLR 2024 proceedings](https://proceedings.iclr.cc/paper_files/paper/2024/hash/8a2acd174940dbca361a6398a4f9df91-Abstract-Conference.html) — 2024；验证递归 embedding、clustering、summarization 与跨抽象层检索。**Confidence: HIGH**。
- [RAPTOR full paper, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/file/8a2acd174940dbca361a6398a4f9df91-Paper-Conference.pdf) — 2024；验证树构建细节、多尺度摘要与 NarrativeQA 动机。**Confidence: HIGH**。
- [From Local to Global: A Graph RAG Approach, arXiv 2404.16130](https://arxiv.org/abs/2404.16130) — 2024-04-24；验证 entity graph、community summaries 与 global query-focused summarization。**Confidence: HIGH（论文方法）；MEDIUM（迁移到小说产品的收益）**。
- [Microsoft GraphRAG default dataflow](https://microsoft.github.io/graphrag/index/default_dataflow/) — accessed 2026-07-15；验证 LLM entity/relationship extraction、Hierarchical Leiden、community reports 和 embedding 工序。**Confidence: HIGH**。
- [Microsoft GraphRAG query overview](https://microsoft.github.io/graphrag/query/overview/) — accessed 2026-07-15；验证 local/global/DRIFT/basic query 分类。**Confidence: HIGH**。
- [Microsoft GraphRAG getting started](https://microsoft.github.io/graphrag/get_started/) — accessed 2026-07-15；验证 Python 要求及官方“大量 LLM 资源”警告。**Confidence: HIGH**。
- [Chroma Query and Get documentation](https://docs.trychroma.com/docs/querying-collections/query-and-get) — accessed 2026-07-15；验证 query embedding 维度要求、`ids` 限制和 `where` metadata filtering。**Confidence: HIGH**。
- [PostgreSQL 16 recursive queries](https://www.postgresql.org/docs/16/queries-with.html) — PostgreSQL 16 docs, accessed 2026-07-15；验证 hierarchical/tree traversal、search order 和 cycle detection。**Confidence: HIGH**。
- [PostgreSQL 16 JSON types](https://www.postgresql.org/docs/16/datatype-json.html) — PostgreSQL 16 docs, accessed 2026-07-15；验证 JSONB 处理和索引特性。**Confidence: HIGH**。

### Local implementation evidence

- `backend/app/models/chunk_build.py`、`backend/app/services/chunking/pg_store.py`、`hierarchy.py`：PostgreSQL hierarchy build、active pointer、parent/child 和 evidence → scene expansion。
- `backend/app/models/analysis.py`、`backend/app/services/timeline/worker.py`、`promotion.py`：候选版本、manifest、budget、checkpoint、CAS promotion/rollback 的成熟模式。
- `backend/app/services/reader_chat/retrieval.py`、`context.py`：visible-set-first、spoiler cutoff、source status、冻结 context manifest 与 evidence 引用。
- `backend/app/services/hybrid_search.py`、`vector_store.py`：PostgreSQL `tsvector` + Chroma 1.5.9 混合召回的实际实现。
- `.planning/PROJECT.md`、`.planning/STATE.md`：v0.8 只读审计、单书 dry-run、不得切 active pointer 以及当前 RAG 质量缺口。

---
*Stack research for: NovelMind v0.8 hierarchical narrative memory and hierarchical RAG vertical MVP*  
*Researched: 2026-07-15*
