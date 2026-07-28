# Architecture Research

**Domain:** 长篇小说的版本化分层叙事记忆与 coarse-to-fine RAG 纵向 MVP  
**Researched:** 2026-07-15  
**Confidence:** HIGH（仓库集成事实）；MEDIUM（尚未用本项目语料验证的检索权重与 arc 划分质量）

## Executive Recommendation

在现有 Phase 07 `chapter → scene → evidence` 不可变层级之外，新建独立的 **Narrative Memory candidate version**，按叙事顺序自下而上生成：

```text
Global Story Model
└── Story Arc / Volume
    └── Chapter State
        └── source links
            ├── Phase 07 chapter / scene / evidence（强制，最终事实依据）
            ├── active Timeline event evidence（可选派生输入）
            ├── accepted Relationship observation evidence（可选派生输入）
            └── accepted Clue lifecycle evidence（可选派生输入）
```

MVP 不修改 `chunk_hierarchy_nodes` 的三层枚举，不复用 `analysis_results` 作为事实源，不替换 timeline / relationship / clue / reader-chat 的 active pointer。它只对单本小说创建一个 `candidate + dry_run` 版本，冻结输入 lineage，持久化节点、父子边和叶子 evidence link，执行验证并输出审计报告。

查询使用 **上层路由 + 多层候选融合 + 叶子回落**，而不是不可回退的严格树遍历。任何可展示结论必须至少包含一条在冻结 Phase 07 build 中仍可重切并校验哈希的原文 evidence；spoiler cutoff 在每一层候选生成、展开和最终组包时重复执行，不能只在入口过滤。

## Current Architecture Facts

### 可直接复用的资产

| 资产 | 当前事实 | v0.8 用法 |
|---|---|---|
| Phase 07 hierarchy | `ChunkBuild` + `ChunkActivePointer` + `ChunkHierarchyNode`；不可变 build，节点为 chapter/scene/evidence，Unicode code-point 半开区间与 content hash | 作为唯一强制底层 source snapshot，不重新调用模型 |
| Timeline | `AnalysisVersion` candidate/active/superseded；manifest checksum；CAS pointer + journal；event → `TimelineEvidenceRef` | 仅作为 evidence-backed 派生输入；lineage 不合格则标 unavailable/ignored |
| Relationships | accepted observation + evidence link + version；append-only observation/override 约束 | 仅作为章节状态的可选辅助事实，不允许替代原文 evidence |
| Clues | 独立 candidate version、active pointer、pointer journal、append-only lifecycle | 仅作为 clue delta 的可选辅助事实，不从聊天文本推断 |
| Reader chat | 冻结 context manifest；owner/novel/cutoff 重验证；最终引用 hierarchy/timeline/relationship evidence | 后续集成新的 read-only retrieval adapter；MVP 不改现有读路径 |
| Phase 05 narrative units | 自己的 source snapshot/build/pointer/promotion 生命周期 | 可审计但不作为 v0.8 强制依赖，避免把另一套派生索引误当原文真值 |

### 不能原样扩展的部分

- `ChunkHierarchyNode.level` 和 Pydantic `HierarchyLevel` 被限定为 `chapter|scene|evidence`，且每章恰好一个 chapter root。把 story arc/global 节点塞入同表会破坏 Phase 07 invariant、增量 rebuild 和 reader-chat 查询假设。
- `AnalysisResult` 是旧式可变 JSON 结果，没有 owner-scoped version、父子证据、manifest 或 active pointer，不满足新事实层的审计要求。
- `AnalysisVersion` 当前是 timeline 的共享版本根，relationship 依赖它；把叙事记忆节点直接挂入其 manifest 会扩大现有 timeline 发布语义并使 dry-run 难以隔离。
- 现有 `fetch_hierarchy_evidence` 主要按选区重叠和章节顺序截断，不是基于问题的 top-down retrieval；它可以保留为 fallback，但不能充当新检索器。

## Standard Architecture

### System Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                     Candidate Build / Dry-run API                   │
├─────────────────────────────────────────────────────────────────────┤
│ Asset Auditor → Source Freezer → Durable Memory Worker → Validator │
│                       │                    │                │        │
├───────────────────────┴────────────────────┴────────────────┴────────┤
│                     Narrative Memory Domain                         │
│ Chapter State Builder → Arc Planner/Builder → Global Model Builder │
│             └──────────── parent/child + evidence links ───────────┘│
├─────────────────────────────────────────────────────────────────────┤
│                          Read-only Sources                          │
│ Phase 07 hierarchy │ Timeline │ Relationships │ Clues              │
├─────────────────────────────────────────────────────────────────────┤
│                         PostgreSQL Authority                        │
│ versions │ runs/stages/calls/budgets │ nodes │ edges │ links │ eval│
├─────────────────────────────────────────────────────────────────────┤
│                    Future Retrieval Adapter (off by default)        │
│ Query router → multi-level candidates → descend/fallback → evidence│
└─────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Project-aligned implementation |
|---|---|---|
| `AssetAuditService` | 只读检查 active hierarchy、source hash、节点覆盖、可选分析版本及 evidence 可追溯性 | SQLAlchemy read-only service，输出逐资产 eligibility + reason codes |
| `SourceManifestBuilder` | 冻结本次 candidate 的所有输入 identity/checksum/cutoff | canonical JSON + SHA-256；保存 Phase 07 build 为 required，其他 source 为 `ok/absent/unavailable/ineligible` |
| `NarrativeMemoryWorker` | durable、可取消、可恢复地按 stage 构建候选 | 复用 timeline worker 的 lease/checkpoint/stage/预算/精确缓存模式，但使用独立表和 active key |
| `ChapterStateBuilder` | 从一章的 scene/evidence 与合格派生事实生成结构化状态变化 | strict Pydantic schema；每个 claim 必须带 source link；失败只影响该章 |
| `ArcPlanner` | 产生连续、无重叠、覆盖全书的 arc/volume chapter ranges | 优先显式卷边界；否则用 chapter-state change signals 产生候选，再由确定性 coverage gate 校验 |
| `ArcBuilder` | 汇总连续 Chapter State，记录 arc-level conflicts/open loops | 子节点引用 + 传递 evidence closure，不复制原文为新真值 |
| `GlobalModelBuilder` | 汇总 arcs，表达全书主线、人物状态、冲突和未决问题 | 单个 global root；只接受子节点支持的 claims |
| `MemoryValidator` | 校验 scope、lineage、哈希、DAG、coverage、spoiler、manifest 和局部失败边界 | 纯 gate + PostgreSQL integration checks；验证不自动 promotion |
| `HierarchicalRetriever` | 后续以问题路由到 global/arc/chapter，再回落 scene/evidence | read-only service；多层融合；每个结果带 path 与 leaf citations |

## Candidate Version and Data Contracts

### 推荐的独立持久化模型

命名可在计划阶段微调，但边界应保持独立：

| 模型 | 关键字段/约束 |
|---|---|
| `NarrativeMemoryVersion` | `owner_id`, `novel_id`, `version_key`, `parent_version_id`, `mode='dry_run'`, `status`, `source_snapshot_hash`, `hierarchy_build_id`, `hierarchy_checksum`, optional source version IDs/checksums, prompt/schema/model/config hashes, manifest/checksum, validated_at；scope unique |
| `NarrativeMemoryRun` | 独立 `active_key`，status/lease/cancel/checkpoint/progress；不得占用 timeline `analysis_runs` 的 active key |
| `NarrativeMemoryStage` | unique `(run_id, stage_key)`；stage key 至少支持 `audit`, `chapter:{id}`, `arc-plan`, `arc:{key}`, `global`, `validate` |
| `NarrativeMemoryNode` | immutable per version；`node_key`, `level=chapter_state|story_arc|volume|global_story`, chapter range, title, strict `payload`, content hash, order index, confidence |
| `NarrativeMemoryEdge` | `(version_id,parent_node_id,child_node_id,edge_type='contains')` unique；DAG，无跨 version/scope 边 |
| `NarrativeMemorySourceLink` | node/claim → source type/id + chapter/order/offset/hash + required lineage；至少支持 `hierarchy_evidence`, `timeline_evidence`, `relationship_evidence`, `clue_evidence` |
| `NarrativeMemoryCallAttempt/Budget*` | 独立模型调用审计、exact-cache、预算 reservation/settlement |
| `NarrativeMemoryDryRunReport` | 审计覆盖率、eligible/reused/rejected counts、node/claim/evidence coverage、failure chapters、token/cost/latency、retrieval eval、promotion recommendation（只读建议） |
| `NarrativeMemoryActivePointer/Journal` | 为后续发布预留；本纵向 MVP 可建表但 dry-run 路径必须保证零写入，或推迟到 promotion phase |

### Node payload 最小契约

```json
{
  "schema_version": "chapter-state.v1",
  "summary": "...",
  "claims": [
    {
      "claim_id": "stable hash",
      "kind": "event|character_state|relationship_delta|clue_delta|world_state",
      "statement": "...",
      "source_link_ids": [101, 102],
      "confidence": 0.0,
      "uncertainty": "explicit|inferred|conflicted"
    }
  ],
  "open_loops": [],
  "conflicts": []
}
```

Arc/global payload 保持相同 claim + source-link 思路，并额外保存 `child_node_ids`、`chapter_start/end` 和聚合状态。上层 claim 可以引用子 claim，但最终必须有计算出的 **evidence closure**；仅有摘要文本或 similarity score 不合格。

### 父子与证据不变量

1. `global → arc/volume → chapter_state` 是新层 DAG；`chapter_state → Phase 07 source` 使用 source link，不反向修改 Phase 07 parent_id。
2. 每个 chapter state 只覆盖一个 active source snapshot 中的 chapter；arc/volume 只能覆盖连续、无重叠 chapter ranges；global 恰好一个。
3. 每个非空 claim 至少有一条 source link，且 source link 最终能解析到 `ChunkHierarchyNode(level='evidence')` 或能通过 Timeline/Relationship/Clue evidence ref 再解析到同一 source snapshot 的原文坐标。
4. source link 必须同时匹配 owner、novel、frozen version/build、chapter、`[source_start, source_end)`、content hash；服务端从 `Chapter.content` 重切验证。
5. 上层只聚合其后代 chapter 的 evidence；不允许跨父范围借证据。
6. candidate rows append-only；重试新写 stage attempt 或复用 exact-cache，不覆盖已完成 artifact。
7. candidate manifest 由排序后的 node/edge/link component hashes 组成；验证时从数据库重算，不能信任 worker 自报。

## Architectural Patterns

### Pattern 1: Lineage-bound Sidecar Hierarchy

**What:** 新叙事记忆作为 Phase 07 的 sidecar version，而不是向原层级表增加 level。  
**Why here:** 保留现有 evidence→scene→chapter invariant、索引和 reader-chat 行为，同时允许独立失败、回滚和评测。  
**Trade-off:** 多一组表与 join；换来明确的发布边界和无损 dry-run。

### Pattern 2: Bottom-up Materialization with Evidence Closure

**What:** chapter state 先由叶子证据构建，arc/global 只消费已验证子节点，并为每个 claim 计算叶子 evidence closure。  
**Why here:** RAPTOR 证明多抽象层有利于长文档与 NarrativeQA 类问题，但其论文也报告少量摘要幻觉；本项目需要比通用摘要树更严格的事实可追溯性。  
**Trade-off:** prompt/存储更大，验证更复杂；但不会让上层摘要成为不可追溯的新事实源。

### Pattern 3: Candidate-first / CAS Promotion

**What:** freeze → build → validate → evaluate →（后续）CAS pointer promotion；所有旧版本保留，journal append-only。  
**Why here:** 与 timeline/clue 已验证的 manifest、pointer revision 和 rollback 模式一致。  
**MVP rule:** `dry_run=true` 在代码层拒绝调用 promotion service，测试断言所有现有 active pointers 与未来 memory pointer 均未变化。

### Pattern 4: Adaptive Coarse-to-fine Retrieval

**What:** 问题分类为 global/arc/local 后，从适当层取候选；对候选向下展开；同时保留跨层/叶子 fallback；最后仅以通过 cutoff 的 evidence 组包。  
**Why here:** RAPTOR 比较了严格 tree traversal 与 collapsed-tree retrieval，后者表现更好；因此“上层选错就永久剪枝”的严格 top-down 不应成为唯一路径。GraphRAG 也区分 global、local、DRIFT 与 basic search，而不是单一查询模式。  
**Trade-off:** 比纯 top-k 多一次路由/融合，但能降低上层摘要错误导致的召回损失。

## Data Flow

### 1. Asset Audit and Frozen Source

```text
owner + novel
  → resolve active Phase 07 pointer/build (required)
  → recompute/verify source + hierarchy checksum
  → verify chapter/scene/evidence coverage and offset hashes
  → inspect active timeline/relationship/clue versions (optional)
  → verify every optional fact has valid evidence under the same source lineage
  → persist eligibility report + frozen source manifest
```

Eligibility 应分为 `eligible`, `absent`, `unavailable`, `stale_lineage`, `invalid_evidence`, `unsupported_schema`。只有 Phase 07 required source 不合格才阻止整个 dry-run；可选分析不合格时降级并明确计数，不能伪装成空数据。

### 2. Bottom-up Dry-run Backfill

```text
candidate version (mode=dry_run; no pointer write)
  → per chapter: load frozen scenes/evidence + eligible derived refs
  → build/validate Chapter State; checkpoint each chapter
  → arc planner: explicit volume markers first, semantic boundaries second
  → deterministic range coverage gate
  → build each Arc/Volume from validated Chapter States
  → build Global Story Model from validated arcs
  → recompute evidence closure + DB manifest
  → quality/cost/coverage report
  → status = validated_dry_run | partial | failed (still no promotion)
```

局部失败策略：

- chapter stage 失败：记录 `failed_chapter_ids`；相邻成功 chapter 不重跑。
- arc 覆盖失败：只重跑相关 range 的 planner/builder，不重跑 chapter states。
- global 失败：保留所有 validated lower nodes，下次仅重跑 global stage。
- source lineage 改变：旧 candidate 保留为 stale；基于新 active build 创建新 version，不原地修补。

### 3. Future Top-down Retrieval

```text
question + frozen reading cutoff
  → owner/novel/version scope
  → query intent: global | arc | local | mixed
  → retrieve allowed upper nodes and/or collapsed multi-level candidates
  → descend parent→children within chapter_end <= cutoff
  → expand source links to scene/evidence
  → server re-slice + hash/lineage validation
  → rerank/deduplicate under token budget
  → ContextManifest(paths + leaf citations + omitted counts + source status)
```

建议 score 只用于候选排序，不作为事实置信度或证据。结果中保存：

- `memory_version_id` 与 manifest checksum；
- traversal path（global/arc/chapter node IDs）；
- leaf evidence keys 与 frozen source lineage；
- 每层候选/过滤/遗漏数量；
- cutoff snapshot；
- fallback reason（如 `upper_miss`, `partial_version`, `source_unavailable`）。

### Spoiler Enforcement

1. cutoff 继续复用 `resolve_chapter_cutoff` / persisted `timeline_full_book` 规则。
2. 查询任何 node 必须满足 `chapter_end <= cutoff`；跨 cutoff 的 arc 不能返回原摘要，应改为按可见 chapter states 动态投影或跳过。
3. 展开 source links 时再次检查 evidence chapter；最终 context manifest 再做一次 fail-closed 过滤。
4. full-book 只能来自已持久化偏好，不能由 query 参数或模型决定。
5. dry-run 可构建全书，但对 reader-visible retrieval 的测试必须覆盖 future evidence 不泄露。

## Recommended Project Structure

```text
backend/app/
├── models/
│   └── narrative_memory.py           # 新 version/run/stage/node/edge/link/report 模型
├── schemas/
│   └── narrative_memory.py           # strict chapter/arc/global + audit/report contracts
├── services/narrative_memory/
│   ├── audit.py                      # 只读资产资格与 lineage 检查
│   ├── sources.py                    # Phase 07/08/09/11 read-only adapters
│   ├── manifests.py                  # canonical component hashes
│   ├── chapter_state.py              # per-chapter package/build/gates
│   ├── arc_planner.py                # volume/arc ranges + deterministic coverage gate
│   ├── aggregate.py                  # arc/global build + evidence closure
│   ├── worker.py                     # durable dry-run orchestration
│   ├── validation.py                 # DAG/scope/evidence/spoiler/manifest gates
│   ├── retrieval.py                  # future feature-flagged coarse-to-fine retriever
│   └── eval.py                       # frozen retrieval + faithfulness/cost metrics
├── api/
│   └── narrative_memory.py           # owner-scoped audit/start/status/report; no publish in MVP
└── migrations/versions/
    └── *_narrative_memory_*.py

backend/tests/
├── unit/narrative_memory/
├── integration/narrative_memory/
├── adversarial/narrative_memory/
└── ci/test_narrative_memory_release_gate.py
```

### Existing Modules to Modify Minimally

| Existing module | MVP change |
|---|---|
| `backend/app/models/__init__.py` | export new ORM models |
| `backend/app/main.py` / API router registry | register owner-scoped audit/dry-run/status/report endpoints |
| `backend/app/services/reader_chat/retrieval.py` | **不在初始 backfill slice 修改**；验证通过后仅增加 feature-flagged read-only adapter，默认仍走现有来源 |
| timeline/relationships/clues services | 不写入；只通过窄 read-only adapter 读取 active version/evidence，避免循环依赖 |
| `analysis_service.py` | 不作为新 worker；可在未来把旧分析入口导向读取 validated memory，但 MVP 保持不变 |

## Implementation Order

1. **Read-only asset auditor**：先实现 eligibility reason codes、Phase 07 必需校验、optional source adapter 与审计报告；不新增模型调用。
2. **Data contract + migration**：独立 version/run/stage/node/edge/link/report；数据库 scope、unique、range、append-only trigger 和 FK 约束。
3. **Manifest/evidence validator**：先于 LLM worker完成，从 DB 重算 lineage、offset/hash、DAG、range coverage、evidence closure。
4. **Chapter State vertical slice**：单章 package → strict structured output → source-link gate → checkpoint；加入 exact-cache/budget。
5. **Arc/Volume planning and aggregation**：显式卷优先，连续 arc 候选；coverage gate；局部重建。
6. **Global Story Model**：仅从 validated arcs 聚合；构建完整 candidate manifest。
7. **Single-novel dry-run CLI/API**：固定小说、明确 budget、保证 no pointer writes；输出 coverage/cost/failure report。
8. **Retrieval experiment**：离线比较 flat evidence、strict traversal、adaptive top-down/collapsed multi-level；只有在 recall/faithfulness/spoiler gates 通过后才接 feature-flagged reader adapter。
9. **Promotion 留到后续 slice**：实现 memory-specific CAS pointer/journal/rollback；不自动替换 timeline/relationship/clue/chat 读模型。
10. **Test, Fix, and Confirm**：PostgreSQL authority、局部失败恢复、active pointer 不变、adversarial lineage/spoiler、固定 eval 和成本报告。

## Verification Strategy

### Must-pass architecture gates

- active Phase 07 build 合格时，Chapter State backfill 不触发 chunking 或重新分析原文边界。
- dry-run 前后 `chunk_active_pointers`, `timeline_active_pointers`, relationship/clue pointers 及未来 memory pointer 完全一致。
- 随机抽样与全量机器校验均能从每个上层 claim 追到原文 `Chapter.content[start:end]` 且 hash 相等。
- 删除/伪造父子 link、跨 novel evidence、错误 build/version、future chapter evidence、仅 similarity/chat 文本 evidence 时 fail closed。
- 单章 provider failure 后，其余章节可完成；恢复只重跑失败 stage。
- arc ranges 连续、无重叠、覆盖所有成功 chapter；global 恰好一个 root；图无环。
- candidate manifest 从数据库重算与 stored checksum 一致；任意 node/link 变化都会失配。
- cutoff 下不会返回跨界 arc 摘要或其 evidence；full-book 未持久化时 fail closed。
- 检索评测至少对比现有 flat evidence baseline；不能只证明层级检索“能运行”。

### Dry-run report minimum metrics

| 类别 | 指标 |
|---|---|
| 复用 | eligible hierarchy ratio、reused evidence/scenes/chapters、optional source acceptance/rejection reasons |
| 完整性 | chapter-state coverage、claim evidence coverage、arc coverage、global evidence closure |
| 质量 | retrieval Recall@k/MRR/NDCG、citation validity、faithfulness、upper-route miss/fallback rate |
| 安全 | spoiler leak count、cross-owner/cross-version rejection count |
| 运行 | completed/failed/retried stages、cache hit、tokens、cost、latency、局部重建范围 |

## Scaling Considerations

| Scale | Architecture adjustments |
|---|---|
| 单本 100–500 章 MVP | PostgreSQL authority；per-chapter durable stages；arc/global 串行；无需新队列/图数据库 |
| 多本并发 | 复用现有 lease worker；按 novel 限流；exact-cache；`(version,level,chapter_start/end)` 索引 |
| 大规模查询 | 为 memory node embedding 建 candidate/active 分离索引；仍以 PostgreSQL link/manifest 为权威 |

第一瓶颈是 LLM chapter-state 调用成本，不是 SQL。先用 lineage 审计跳过不需重算的章节、精确缓存和局部 stage 恢复。第二瓶颈是高层候选误路由，应通过 collapsed multi-level fallback 和离线 eval 解决，而不是提前引入 Neo4j 或通用 GraphRAG runtime。

## Anti-Patterns

### 扩展 `ChunkHierarchyNode.level`

会破坏 Phase 07 每章树 invariant 和现有查询假设。使用独立 sidecar hierarchy + source links。

### 把摘要当证据

上层摘要可能包含压缩误差；RAPTOR 论文也报告摘要存在少量幻觉。摘要只用于路由与表达，最终 evidence closure 必须回到原文。

### 严格逐层剪枝且无 fallback

上层选错后叶子永久丢失；RAPTOR 实验中 collapsed tree 优于严格 traversal。保留多层融合/flat leaf fallback，并测 upper-route miss rate。

### 复用 timeline `AnalysisVersion` / `AnalysisRun` 写新产品

会耦合 timeline 发布状态、active key 和 downstream dispatch。使用 memory-owned version/run/pointer 表，只读消费 timeline lineage。

### dry-run 完成即自动发布

违反当前里程碑“不切 active pointer”。dry-run endpoint/worker 不应依赖 promotion service；DB 测试直接证明零 pointer 写入。

### 只在请求入口执行 spoiler cutoff

上层摘要本身可能包含未来内容。必须在 node 候选、child descent、source expansion 和最终 manifest 四处执行范围检查。

### 直接安装/嵌入完整 RAPTOR 或 GraphRAG

两者提供有价值的模式，但本项目已有不可变叙事层、版本治理与领域图谱。MVP 需要的是窄集成和可验证 lineage，不是第二套索引运行时或新生产依赖。

## Integration Points

### Internal Boundaries

| Boundary | Communication | Rule |
|---|---|---|
| memory ↔ chunking | read-only SQL/service adapter | active immutable build required；不调用 `ensure_hierarchy(force)` |
| memory ↔ timeline | read-only version/evidence adapter | lineage/checksum 不一致即 ineligible；不改 timeline manifest/pointer |
| memory ↔ relationships | read-only accepted observation refs | 无 evidence 的 graph preview 不进入 package |
| memory ↔ clues | read-only lifecycle/evidence refs | chat/freeform text 永不作为 clue/memory evidence |
| memory ↔ reader chat | future read-only retrieval adapter | feature flag；返回 frozen memory lineage + leaf citations；旧路径保留 |
| worker ↔ provider | existing fixed-deployment gateway pattern | strict schema、one controlled repair at most、budget before call、exact-cache |

## Research Interpretation

- **采用 RAPTOR 的部分：** 自下而上生成多抽象层；查询可混合不同层；适合 NarrativeQA/长文档综合问题。
- **不照搬 RAPTOR 的部分：** 不使用无叙事约束的 GMM semantic clustering 作为首版 arc 边界；小说 arc 需要连续 chapter range、spoiler projection 和领域状态变化。也不采用严格 tree traversal 作为唯一检索。
- **采用 GraphRAG 的部分：** 区分 global/local/mixed 查询模式；上层报告服务于整体问题，叶子文本服务于具体问题。
- **不照搬 GraphRAG 的部分：** 不重抽完整 entity graph、不引入 Leiden/community reports runtime；项目已有 timeline/relationship/clue 的权威版本，重复抽取会增加成本和冲突。

## Sources

- [RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval (ICLR 2024 / arXiv)](https://arxiv.org/abs/2401.18059) — bottom-up recursive summaries、tree traversal vs collapsed-tree、多层检索、NarrativeQA/QuALITY/QASPER 结果；论文报告约 4% 摘要含轻微幻觉。
- [Microsoft GraphRAG — Query Engine Overview](https://microsoft.github.io/graphrag/query/overview/) — local/global/DRIFT/basic 四种查询模式，以及 global map-reduce 与 local raw text/graph 组合。
- [Microsoft GraphRAG — Project Overview](https://microsoft.github.io/graphrag/) — hierarchical community summaries、自下而上报告与不同 query mode 的官方说明。
- [Microsoft GraphRAG — Indexing Overview](https://microsoft.github.io/graphrag/index/overview/) — configurable indexing workflow、multi-granularity reports、vector embeddings 与存储边界。
- 项目事实来源：`.planning/PROJECT.md`、`.planning/STATE.md`、`.planning/ROADMAP.md`、`docs/architecture/`、`backend/app/models/{chunk_build,analysis,timeline,relationship,clue,knowledge_unit}.py`、`backend/app/services/{chunking,timeline,relationships,clues,reader_chat}/`、`backend/app/services/analysis_service.py`（2026-07-15 工作区）。

---
*Architecture research for: NovelMind v0.8 分层叙事记忆与层级 RAG 纵向 MVP*  
*核心约束：复用底层、候选优先、证据闭包、零 pointer 切换、局部可重建。*
