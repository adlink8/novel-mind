---
adr: 0001
title: 唯一层级注册表（S* / D* / R* / A* 四正交命名空间）
status: accepted
date: 2026-07-26
snapshot: >
  本 ADR 的事实性描述以 git commit 9f01680 时的代码为准
  （backend/app/models/、backend/app/services/ 实读核对）。
  数据库表名、字段名、枚举值均引用当时的 ORM 定义；后续 schema 变更时
  以迁移与代码为准，本文档需随之修订并标注 supersedes。
closes:
  - NM-ARCH-001
  - NM-ARCH-002
  - NM-ARCH-003
  - NM-ARCH-004
references:
  - .planning/ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md
  - docs/adr/0002-narrative-unit-vs-narrative-memory.md
---

# ADR-0001: 唯一层级注册表

## 背景与决定

NovelMind 历史文档中至少存在两套互相冲突的 `L0-L6` 层级编号
（见审计 NM-ARCH-001：Phase 20 文档以 `L0 Evidence → L4 Global`，
跨项目推演文档以 `L0 Source → L6 Global`），同一个 `L2/L3/L4` 在不同文档
指向不同实体。同时"层级"一词还被混用于语义粒度、数据成熟度、发布状态、
存储拓扑与 UI 下钻（NM-ARCH-002）。

**决定：** 自本 ADR 起，固定四个**正交**命名空间，任何一个都不得复用
另一个的编号或术语：

| 命名空间 | 维度 | 取值 |
|---|---|---|
| `S*` Semantic | 语义粒度 | S0-S6（下文） |
| `D*` Data maturity | 数据成熟度 | Raw / Canonical / Derived / Serving |
| `R*` Release lifecycle | 发布生命周期 | Draft-Candidate / Validated / Qualified / Active / RolledBack |
| `A*` Architecture | 软件架构层 | Delivery / Application / Domain / Infrastructure |

**裸 `L0-L6` 自本 ADR 起禁止进入任何新代码、schema、API、UI 或规划文档**；
存量文档中的 `L*` 仅作历史记录，一律以本表换算：

| 旧 Phase 20 文档 | 旧跨项目文档 | 本 ADR |
|---|---|---|
| —（隐含） | L0 Source | **S0** |
| L0 Evidence | L1 Evidence | **S1** |
| L1 Scene | L2 Scene | **S2** |
| —（章节列表壳） | L3 Chapter | **S3** |
| L2 Chapter State | L4 Chapter State | **S4** |
| L3 Arc/Volume | L5 Arc/Volume | **S5** |
| L4 Global | L6 Global | **S6** |

---

## 1. S* 语义层注册表（S0-S6）

总原则（与项目核心价值一致）：**PostgreSQL 是唯一权威事实库；模型只产候选；
候选未经证据闭包、门禁与版本化不得被引用为事实；失效自下而上传播、
上层重建不得反写下层。**

### S0 — Source Text（小说原文）

| 项 | 内容 |
|---|---|
| 定义 | 用户导入的小说原文本体，不可由任何摘要、模型输出替代 |
| 输入 | 用户上传/导入（`import_jobs`，`backend/app/models/import_job.py`） |
| 输出 | 供 S1-S3 分块、供一切证据引用回跳的原文 |
| SSOT 表 | `novels`、`chapters`（`Chapter.content`，`backend/app/models/novel.py:113-152`） |
| 可重建 | 否。只能重新导入；系统内任何流程不得生成或覆盖 S0 |
| 模型可写 | **禁止**（`Chapter.summary` 是挂在 S0 行上的派生展示字段，不属于 S0 事实本身） |
| 被引用为事实的条件 | 无条件；S0 是全部上层事实的最终裁决依据 |
| 失效传播 | S0 变更（`source_snapshot_hash` 变化）→ S1-S6 全部失效待重建 |

### S1 — Evidence（证据叶子）

| 项 | 内容 |
|---|---|
| 定义 | 带精确 `chapter_id + source_start/source_end + content_hash` 的原文片段引用；一切上层事实的叶子证据 |
| 输入 | S0 原文，经确定性分块器切分 |
| 输出 | 供 S2-S6 与各 Facet 引用的证据锚点 |
| SSOT 表 | `chunk_hierarchy_nodes`（`level='evidence'`，`ChunkHierarchyNode`，`backend/app/models/chunk_build.py:73-112`）；旧 raw 链为 `text_chunks`（`hierarchy_level` 取 `evidence|scene|raw`，`backend/app/models/text_chunk.py:87-89`）。各消费方的证据引用表：`knowledge_evidence_refs`、`timeline_evidence_refs`、`relationship_evidence_links`、`clue_evidence_refs`、`narrative_memory_source_links` |
| 可重建 | 是。由 S0 + chunker 版本（`ChunkBuild.chunker_name/chunker_version/chunker_config_hash`）确定性重建；build 不可变（`ChunkBuild.immutable`） |
| 模型可写 | **禁止**。分块是确定性过程，`decision_lineage` 记录切分决策 |
| 被引用为事实的条件 | 引用必须携带 `build_id + node_id`（或 `text_chunk_id`）+ offset + `content_hash`，且 hash 与 S0 当前内容重切校验一致 |
| 失效传播 | S0 变更 → 对应 build 失效；S1 失效 → 引用它的 S2-S6 claim 与 Facet 证据链失效 |

### S2 — Scene（场景）

| 项 | 内容 |
|---|---|
| 定义 | 语义完整的场景/事件片段，由若干 S1 Evidence 组成 |
| 输入 | S1 节点（同一 immutable build 内 `parent_id/child_ids` 组织） |
| 输出 | 供 UI 下钻、供 Facet 挂载、供 S4 生成时定位上下文 |
| SSOT 表 | `chunk_hierarchy_nodes`（`level='scene'`） |
| 可重建 | 是（同 S1，属于同一 `chunk_builds` 构建单元） |
| 模型可写 | 禁止写入结构本身 |
| 被引用为事实的条件 | 同 S1：build + hash 校验 |
| 失效传播 | 子 S1 失效 → 所属 S2 失效 → 上层失效 |

### S3 — Chapter（章节结构）

| 项 | 内容 |
|---|---|
| 定义 | 章节结构与其场景集合；是结构坐标，**不是**模型摘要 |
| 输入 | S0 章节切分 + S2 场景集合 |
| 输出 | 结构骨架（UI 章节树、S4 的章节边界） |
| SSOT 表 | 章节实体：`chapters`（`chapter_number`、`title`）；结构节点：`chunk_hierarchy_nodes`（`level='chapter'`）；当前生效构建由 `chunk_active_pointers`（`ChunkActivePointer`，chunk_build.py:55-70）指定 |
| 可重建 | 结构节点可重建；`chapters` 行本身承载 S0 不可重建 |
| 模型可写 | 禁止 |
| 被引用为事实的条件 | 结构引用需绑定 active build（或显式 build_id） |
| 失效传播 | 章节内容变更 → 该章 S1-S3 节点失效 → 引用该章的 S4 失效 |

### S4 — Chapter State（章节末状态）

| 项 | 内容 |
|---|---|
| 定义 | 章节结束时的角色、关系、冲突、线索与世界状态变化 |
| 输入 | S1-S3（必需）；Timeline/Relationship/Clue Facet（可选 enrichment，`MEMORY_SOURCE_KINDS = ('hierarchy','timeline','relationship','clue')`，`backend/app/models/narrative_memory.py:40`） |
| 输出 | S5 聚合的输入 |
| SSOT 表 | `narrative_memory_nodes`（`node_kind='chapter_state'`，且约束 `chapter_start = chapter_end`）+ `narrative_memory_claims` + `narrative_memory_source_links`；版本身份在 `narrative_memory_versions` |
| 可重建 | 是（模型重跑；lineage 由 `prompt_hash/schema_hash/model_lineage/decoding_hash` 冻结） |
| 模型可写 | **允许，但仅限 candidate**。写入即为候选，无生产写入权 |
| 被引用为事实的条件 | 当前**不允许被引用为生产事实**（candidate-only，见 ADR-0002）。最低要求：每条 claim 有 `narrative_memory_source_links` 下钻到 S1 叶子（FK `fk_memory_links_evidence_leaf` → `chunk_hierarchy_nodes(build_id,node_id)`，narrative_memory.py:365-370）且通过 manifest 封存与 validation report `qualified_candidate` |
| 失效传播 | 所引 hierarchy build 或章节内容变更（`hierarchy_build_id/hierarchy_checksum/source_snapshot_hash` 不匹配）→ 该版本失效；重建计划见 `narrative_memory_rebuild_plans` |

### S5 — Arc / Volume（卷/故事阶段）

| 项 | 内容 |
|---|---|
| 定义 | 连续章节形成的故事阶段、冲突与人物变化 |
| 输入 | 已完成的 S4 chapter_state 序列 |
| 输出 | S6 聚合的输入 |
| SSOT 表 | `narrative_memory_nodes`（`node_kind IN ('story_arc','volume')`）+ claims/edges/source_links（`narrative_memory_edges` 表达 `contains/derives_from`） |
| 可重建 | 是 |
| 模型可写 | 允许，仅限 candidate |
| 被引用为事实的条件 | 同 S4，且仅可由已通过校验的 S4 聚合（不得跳层直接从原文臆造全卷结论） |
| 失效传播 | 任一成员 S4 失效 → 所属 S5 失效 → S6 失效 |

### S6 — Global Story（全书模型）

| 项 | 内容 |
|---|---|
| 定义 | 只从已验证子层聚合出的全书叙事模型 |
| 输入 | S5（及必要的 S4） |
| 输出 | 全书级问答/展示的候选依据 |
| SSOT 表 | `narrative_memory_nodes`（`node_kind='global_story'`）+ 同上配套表 |
| 可重建 | 是 |
| 模型可写 | 允许，仅限 candidate |
| 被引用为事实的条件 | 同 S5；当前同样 candidate-only |
| 失效传播 | 终点层；接收全部下层失效，不向任何层传播 |

### Facet：Timeline / Relationship / Clue 不是层

Timeline、Relationship、Clue **不是新的 S 层**，而是挂载在 S2-S6 节点上的
**只读 Facet/Projection**（对结构主轴而言）。其表分别为：
`machine_timeline_events`/`timeline_causal_edges`/`timeline_active_pointers`
（`backend/app/models/timeline.py`）、
`relationship_observations`/`relationship_evidence_links`
（`backend/app/models/relationship.py`）、
`machine_clues`/`clue_lifecycle_events`/`clue_active_pointers`
（`backend/app/models/clue.py`）。每个 Facet 在自己域内有独立的
candidate→accepted→active 生命周期（各自的 active pointer + pointer journal），
但对 S* 主结构：

- Facet 必须保留来源类型、版本、置信度与 S1 叶子证据；
- **禁止反馈环**（NM-GOV-005）：Facet 派生结果不得无证据地反写
  Source/Evidence/Scene（S0-S2），再作为自身下一轮输入。可选 enrichment
  必须携带 lineage（如 NM 的 `optional_source_lineage`，
  narrative_memory.py:113）；
- Facet 数据 `unavailable` 只能表达"不可用"，不得被解释为"事实为零"。

---

## 2. D* 数据成熟度（与 S* 正交）

同一个 S 层的数据可以处于不同成熟度；成熟度描述"这份数据离权威事实多远"，
与语义粒度无关。

| D* | 定义 | NovelMind 示例 |
|---|---|---|
| **Raw** | 原始输入，未经系统加工 | `chapters.content`、`import_jobs` 载荷 |
| **Canonical** | 通过证据闭包与门禁的权威事实 | `knowledge_relation_judgments(status='accepted')`、`relationship_observations`（表级 CHECK `status='accepted'`）、active build 下的 `narrative_units` |
| **Derived** | 可由 Raw/Canonical 确定性或模型化重建的派生物 | `chunk_hierarchy_nodes`、`narrative_memory_*` 候选、`text_chunks.search_vector` |
| **Serving** | 为查询/展示优化的投影，可整体丢弃重建 | Chroma collections（`ChunkBuild.collection_name`、`NarrativeIndexBuild.collection_name`）、Neo4j 可选投影、API 聚合响应 |

规则：Serving 永远不是第二真相源；Serving 与 Canonical/Derived 的绑定必须
经 manifest/checksum 可验证（如 `manifest_checksum` 系列字段）。

## 3. R* 发布生命周期（与 S* 正交）

R* 描述"一份版本化产物能否被生产消费"，与它属于哪个 S 层无关。

| R* | 定义 | 代码锚点 |
|---|---|---|
| **Draft-Candidate** | 模型或流程刚产出，未经校验 | `NARRATIVE_UNIT_STATUSES` 的 `draft/candidate`（knowledge_unit.py:37-44）；NM 全体版本 |
| **Validated** | 结构/证据校验通过 | `narrative_memory_validation_reports.verdict='qualified_candidate'` |
| **Qualified** | 质量资格评测通过 | `narrative_memory_qualification_runs/_reports`；`quality_runs`/`baseline_candidates` |
| **Active** | 被唯一 active pointer 指认、允许生产消费 | `chunk_active_pointers`、`narrative_active_pointers`、`timeline_active_pointers`、`clue_active_pointers`、`active_baselines` |
| **RolledBack** | 曾 Active、已被审计回退 | `narrative_promotion_journals.status='rolled_back'`、`timeline_pointer_journal`、`clue_pointer_journal` |

规则：Active 不是"更高语义层"，只是发布状态；晋升必须走 journal
（prepare/commit）且可回滚。**Narrative Memory 当前整体停在
Draft-Candidate/Validated/Qualified，无 Active（见 ADR-0002）。**

## 4. A* 架构层（与 S* 正交）

| A* | 定义 | 代码位置 |
|---|---|---|
| **Delivery** | HTTP/UI 边界 | `backend/app/api/`、`frontend/` |
| **Application** | 用例编排、流程与策略 | `backend/app/services/` |
| **Domain** | 领域契约与权威数据模型 | `backend/app/models/`、`backend/app/schemas/` |
| **Infrastructure** | 数据库、向量库、模型网关等适配器 | `backend/app/core/`（database 等）、Chroma/Neo4j/LLM adapter |

规则：依赖方向 Delivery → Application → Domain；Infrastructure 被上层通过
契约调用。A* 与 S* 无关：同一个 API（Delivery）可以同时暴露 S1 与 S6 数据。

---

## 5. 命名与字段规范（强制，新代码/schema/API）

1. **禁用裸 `L0-L6`**：新字段、变量、枚举、路由、文档标题一律不得使用。
2. 三种"level"必须使用不同字段名，禁止合并：
   - `chunk_level` — 结构分块层（现 `ChunkHierarchyNode.level`：
     `chapter|scene|evidence`，chunk_build.py:93-95。存量列名不强制迁移，
     但**新** API schema 暴露该值时必须命名为 `chunk_level`）；
   - `semantic_level` — S0-S6 语义层（新增字段时使用）；
   - `release_status` — R* 发布状态（禁止用 `level` 表达 Active/Candidate）。
3. `ChunkHierarchyNode.level` 与 NM `node_kind` 属于两个系统
   （NM-ARCH-004），API 响应中不得出现在同名字段里。
4. Timeline/Relationship/Clue 相关 API/UI 必须以 Facet 呈现（挂载于某个
   S 节点/区间），不得自立为层级；其写路径只存在于各自 Facet 域内的
   candidate→accepted 管线，**任何指向 S0-S2 主结构的写入都被禁止**
   （NM-GOV-005）。

## 6. 后果

- 后续 Phase（24 存储一致性、25 Facet 契约、30 NM promotion 决策）均以本
  注册表为层级语言；ROADMAP Phase 23 Success Criteria 1/3/4/5 由本 ADR 与
  配套 superseded 标注承接（契约测试属 Plan 23-02，不在本文档任务内）。
- 历史 `.planning/phases/20-*` 文档已在文件头标注
  "Layer numbering superseded by docs/adr/0001-layer-registry.md"。
