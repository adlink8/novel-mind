# Project Research Summary

**Project:** NovelMind v0.8 — 分层叙事记忆与层级 RAG  
**Domain:** 长篇小说的版本化、证据约束分层记忆与 coarse-to-fine RAG  
**Researched:** 2026-07-15  
**Confidence:** HIGH（技术栈、项目边界、集成模式与安全约束）；MEDIUM（arc 边界质量、层级检索相对收益和冻结质量阈值）

## Executive Summary

v0.8 应以一个单书纵向 MVP 验证：能否在不重建合格 Phase 07 底层资产、不修改现有分析产品读模型、也不切换任何 active pointer 的前提下，增量构建 `Chapter State → Story Arc/Volume → Global Story Model`，并让全局/跨章问题通过分层路由最终回落到原文 evidence。现有 `chapter → scene → evidence`、timeline、relationship、clue 都是只读输入；聊天永远不是事实源。

推荐使用现有 PostgreSQL、SQLAlchemy async、Pydantic、LiteLLM gateway/durable worker 和可选 Chroma，不增加生产依赖。上层记忆作为独立、不可变的 candidate sidecar version 存储，不扩展 `ChunkHierarchyNode.level`，不复用 timeline 的 version/run 发布语义。每条上层 claim 必须保存可重算的 derivation 与 evidence closure；摘要只用于导航和表达，最终证据必须是冻结 source snapshot 中可按 offset/hash 重验的叶子原文。

最大风险不是“模型能否生成摘要”，而是结构看似完整但发生逐层事实漂移、版本/证据混用，或在 spoiler、owner scope、cache、dry-run pointer 上越界。路线必须先审计再调用模型、先冻结 claim/provenance contract 再生成内容，并将 visible-set-first、candidate-only、no active pointer writes 和 fresh PostgreSQL qualification 设为硬门。

详细研究： [STACK.md](./STACK.md) · [FEATURES.md](./FEATURES.md) · [ARCHITECTURE.md](./ARCHITECTURE.md) · [PITFALLS.md](./PITFALLS.md)

## Locked MVP Boundary

### 本里程碑必须交付

- 对单本小说执行只读资产审计，按资产/版本输出复用资格、reason codes、覆盖率和最小重建范围；审计零 provider call、零 pointer write。
- 建立独立的 strict candidate contract：Chapter State、连续 Story Arc/Volume、Global Story Model、父子边、claim-level source links、manifest/checksum 和 durable stages。
- 自下而上完成单书 dry-run，记录 checkpoint、exact cache、预算、tokens、cost、延迟、失败章节和复用节省。
- 提供离线 coarse-to-fine 检索实验：global/arc/chapter 多层候选、向下展开、leaf fallback、routing trace 和服务端 spoiler cutoff。
- 证明 claim→leaf provenance、局部 invalidation/carry-forward 和结构/安全/质量门；只输出 `qualified_candidate | blocked`。

### 本里程碑明确不做

- 不从原文重新分析全部既有 hierarchy/timeline/relationship/clue 资产；不自动修复审计发现的旧资产。
- 不切换或新增可达的 active pointer，不调用 chunk/timeline/clue promotion，不改变现有 reader-chat 检索。
- 不让 timeline、relationship、clue、chat 消费新层；不新增用户可见 UI。
- 不安装 GraphRAG、RAPTOR reference implementation、Neo4j、UMAP/GMM、Leiden、LangChain/LangGraph 或新 reranker/embedding 模型。
- 不用上层摘要、相似度、共现或聊天文本充当最终事实证据。

## Key Findings

### Recommended Stack

不新增生产依赖。沿用仓库已经验证的版本、审计、预算、checkpoint 与检索设施，减少第二套生命周期和双写风险。

**Core technologies:**

- **PostgreSQL 16**：version/run/stage/node/edge/evidence link/manifest/report 的权威存储；使用 FK、复合 scope、索引、事务和必要的 recursive CTE。
- **SQLAlchemy async 2.x + asyncpg**：owner/novel/version 查询、行锁、durable worker 和真实 PostgreSQL 集成验证。
- **Pydantic 2.x**：`extra="forbid"` 的 ChapterState/Arc/Global、typed claims、state deltas、source package 和 retrieval manifest。
- **现有 LiteLLM gateway + durable worker pattern**：固定 deployment/prompt/schema/config，provider retry 关闭，显式一次 repair，调用前预算预留，exact-cache 与 checkpoint resume。
- **现有 ChromaDB 1.5.9（可选）+ PostgreSQL `tsvector`**：候选上层节点召回；Chroma 不可用时词法路由仍可运行。PostgreSQL 始终是版本与 provenance 权威。
- **pytest / Alembic / 现有 qualification pattern**：additive migration、纯契约测试、真实 PostgreSQL authority、对抗测试和固定命令 verifier。

### Expected Features

**Must have（缺一不可）:**

- 只读资产 inventory、逐资产复用资格和 hard lineage/manifest/offset/hash gates。
- 独立 candidate version、durable run/stage、strict claim/state-delta contract 和不可变 manifest。
- `ChapterState → contiguous StoryArc/Volume → GlobalStoryModel` 自下而上构建。
- 每条 claim 的 parent/child derivation 与 leaf evidence closure。
- visible-set-first spoiler enforcement、owner/novel/version scope 和 tenant-isolated cache/vector namespace。
- coarse-to-fine retrieval、raw/evidence baseline fallback、routing trace 与 leaf-only citations。
- dependency graph、局部重建/carry-forward 证明和失败隔离。
- 单书冻结评测、成本/复用报告和 fail-closed candidate verdict。

**Should have（验证价值差异）:**

- claim 级溯源，而不是整段摘要挂宽泛 citation。
- 人物/关系/线索/世界状态的 typed delta memory 与 unknown/conflict 表达。
- avoided calls/tokens/cost、carry-forward 节点数和 upper-route miss/fallback rate。
- timeline/relationship/clue 的可选 evidence-backed enrichment；任何来源缺失必须明确 `unavailable`，不能制造负事实。

**Defer（v0.8.x / 后续）:**

- memory active pointer、CAS promotion、rollback journal 和 consumer opt-in cutover。
- Reader Chat 使用新 memory，或 timeline/relationship/clue 消费 ChapterState。
- 多书/多体裁容量与泛化测试、人工 arc boundary override、用户可见 world-model UI。
- DRIFT 式 query expansion、学习式 router/reranker 和跨小说主题层。

### Architecture Approach

采用 lineage-bound sidecar hierarchy。新 `NarrativeMemoryVersion` 冻结 active Phase 07 build/checksum，并记录可选 domain source versions；独立 run/stage 逐章生成 Chapter State，再由连续章节范围构建 Arc/Volume，最后由 validated arcs 生成单个 Global root。节点、父子边和 source links 分表保存，manifest 从数据库排序重算。query 采用 adaptive coarse-to-fine：按问题类型从适当层起步，允许 collapsed multi-level/leaf fallback，最终重新切片校验原文证据。dry-run 代码路径不可到达 promotion service。

**Major components:**

1. **Asset Auditor + Source Manifest Builder** — 在任何 provider call 前验证 required Phase 07 authority，并冻结 optional source 的 `ok/absent/unavailable/ineligible` 状态。
2. **Narrative Memory Contracts + Validator** — 定义 strict claims/deltas、parent-child/source links、DAG/range/evidence closure、canonical manifest 和 scope/spoiler gates。
3. **Durable Bottom-up Worker** — Chapter State、Arc/Volume、Global 分 stage 构建，支持预算、缓存、取消、恢复和局部失败。
4. **Hierarchical Retriever** — global/local/mixed 确定性路由、多层候选融合、向下展开、leaf rerank/fallback 和 frozen context manifest。
5. **Invalidation Planner** — 根据 source/child checksum 计算 dirty closure；不能证明 arc 边界稳定时保守扩大。
6. **Single-book Qualification** — 同 source/cutoff/budget 与 leaf baseline 对比，fresh DB 重算 authority，仅输出 candidate verdict。

### Critical Pitfalls

1. **逐层摘要漂移与自由文本 authority** — 先冻结 typed claim/state-delta contract；每层 gate unsupported/conflict，摘要不能独立成为事实。
2. **snapshot/evidence lineage 断裂或混用** — provider call 前审计；每个 claim 从新鲜数据库全链解析到同一 owner/novel/source snapshot 的 leaf offset/hash。
3. **spoiler、owner scope 或 pointer 控制面越界** — visible-set-first、每跳复合 scope、tenant-isolated cache、dry-run promotion imports/write spies 为空，before/after pointer/revision 完全一致。
4. **invalidation 范围错误** — 保存明确 dependency closure；边界变化时扩大到相关 arc/后缀与 Global，不能承诺固定“一章三节点”。
5. **评测假阳性** — 同源 baseline、分桶指标、deterministic hard gates、judge 顺序稳定性、固定命令和 fresh PostgreSQL observer；单书结果不能关闭 v0.3 全项目质量缺口。

## Implications for Roadmap

建议按六个 phase 交付。每个 phase 都保持现有业务资产只读；前五个只创建/读取 candidate data，第六个仍不 promotion。

### Phase 1: Asset Audit & Eligibility

**Rationale:** 无法证明底层 authority 时，任何上层模型调用都会产生不可发布资产。  
**Delivers:** audit schema/reason codes、Phase 07 required validator、timeline/relationship/clue read-only adapters、单书真实 inventory 与 eligibility report。  
**Hard boundary:** 零 provider call、零修复、零 pointer write。  
**Avoids:** snapshot/version 混用、把 optional outage 当空事实、无效资产上的浪费调用。

### Phase 2: Candidate Contracts & Provenance Authority

**Rationale:** claim-level contract、scope、evidence closure 和 manifest 必须先于生成器存在。  
**Delivers:** additive PostgreSQL migration；独立 version/run/stage/node/edge/source-link/report；strict Pydantic schemas；canonical manifest；DAG/range/offset/hash/spoiler validator；append-only DB constraints。  
**Uses:** PostgreSQL、SQLAlchemy async、Pydantic、Alembic。  
**Avoids:** 自由文本 authority、宽泛 citation、跨 owner/version link 和不可复现 artifact。

### Phase 3: Bottom-up Candidate Builder

**Rationale:** Chapter State 是最小可复用中间层，Arc/Global 只能消费 validated children。  
**Delivers:** durable chapter stages、显式卷界/确定性连续 arc planning、Arc/Volume aggregate、Global root、预算预留、exact cache、一次 repair、取消/恢复、partial failure report。  
**Hard boundary:** candidate/dry-run only；optional sources 不在关键路径；promotion service 不可达。  
**Avoids:** 全量 fan-out、未知价格调用、整书重启、上层跨范围借证据。

### Phase 4: Retrieval, Leaf Evidence & Spoiler Safety

**Rationale:** 只有真正改变候选选择并最终回到 leaf evidence，分层记忆才产生 RAG 价值。  
**Delivers:** deterministic global/local/mixed router、multi-level candidate fusion、parent→child descent、leaf fallback/rerank、routing trace、source status/omitted counts、frozen cutoff manifest 和 adversarial IDOR/spoiler tests。  
**Uses:** PostgreSQL `tsvector`；Chroma 仅作为可选 candidate index，metadata 预过滤 version/build/level/range。  
**Avoids:** 严格 top-down 误剪枝、cutoff 后过滤、高层标题/计数/trace 泄漏和 cache 越界。

### Phase 5: Local Invalidation & Carry-forward Proof

**Rationale:** “单层失败可局部重建”必须由 dependency oracle 证明，而非凭章节号或时间戳推断。  
**Delivers:** edit/insert/delete/reorder/boundary fixtures、dirty closure planner、byte-identical carry-forward、stale-link detection、保守范围扩大和 stage-only resume。  
**Avoids:** invalidation 过小污染旧事实，或过大导致全书重跑和成本失控。

### Phase 6: Single-book Dry-run Qualification

**Rationale:** 外部 RAPTOR/GraphRAG 结果不能证明 NovelMind 本身获益；最终必须用同源对照和独立 authority 验证。  
**Delivers:** 冻结小说/问题/policy、local/arc/global/no-answer/spoiler 分桶、leaf baseline vs hierarchical 对照、结构/provenance/security/quality/cost/latency/reuse metrics、固定命令 verifier 与 `qualified_candidate | blocked` 报告。  
**Hard boundary:** 不创建/切换 active pointer，不替换 Reader Chat，不修改 UI；明确保留 v0.3 100-confirmed/faithfulness/cost residual。  
**Avoids:** 自证式 release、空指标通过、fixture 泄漏和将单书实验宣称为生产完成。

### Phase Ordering Rationale

- Audit 是所有写入和模型调用的前置条件；Contracts 是所有生成器和检索器的前置条件。
- 构建顺序必须是 Chapter State → Arc/Volume → Global，才能形成可验证 evidence closure 和局部 checkpoint。
- Retrieval 在完整 candidate hierarchy 后实现，但在 qualification 前完成，保证评测测到真实数据流而非 schema。
- Local rebuild 独立成 phase，因为 arc boundary propagation 是核心正确性/成本风险，需要专门 oracle。
- Qualification 最后执行并始终与 promotion 分离；候选合格不等于获得 consumer cutover 权限。

### Research Flags

Phases planning 时需要继续深挖：

- **Phase 1:** 明确 timeline/relationship/clue 的 eligibility SQL、active/version cardinality、source lineage 兼容矩阵和真实小说选择标准。
- **Phase 2:** 定稿 claim ontology、state-delta 字段、DB append-only trigger、复合 FK、evidence closure 表示和 manifest component schema。
- **Phase 3:** 冻结 arc boundary 策略。MVP 推荐显式卷界优先、确定性连续窗口兜底；需决定窗口/重叠、prompt package 上限、模型预算和 partial-version 语义。
- **Phase 4:** 设计 routing/ranking 实验，比较 leaf-only、strict traversal 与 adaptive/collapsed multi-level；冻结 metadata cutoff、fallback 和 query token budget。
- **Phase 5:** 定义 dependency closure oracle，尤其是章节插入/重排、arc boundary 变化与状态跨章延续时的安全失效范围。
- **Phase 6:** 在看结果前冻结单书问题集、baseline 预算、分桶门槛、faithfulness 方法、judge swap 稳定性和 release authority 命令。

可使用标准既有模式、无需重新研究生态：

- durable job lease/checkpoint、budget reservation、exact-cache、fixed deployment 和 one-repair gateway。
- PostgreSQL owner/novel scope、manifest 重算、CAS/pointer journal（仅借鉴，不在 MVP 调用）、Alembic additive migration。
- reader-chat 的 cutoff resolution、frozen context manifest、source unavailable 与 server re-slice/hash validation。

## Top Three Risks

| Rank | Risk | Severity | Planning response |
|---:|---|---|---|
| 1 | 逐层摘要漂移，且自由文本被误当 authority | P1 | Phase 2 先冻结 claim/delta/provenance contract；Phase 3 每层 gate；Phase 6 分层 faithfulness/drift 诊断 |
| 2 | source snapshot、hierarchy 或 domain version 混用，形成“结构完整但来源错误”的候选 | P1 | Phase 1 provider-call-before audit；Phase 2 复合 scope/FK；Phase 6 fresh DB 全链重算 |
| 3 | spoiler/owner/cache/pointer 控制面越界 | P0 | visible-set-first、tenant namespace、每跳 scope、future metadata 对抗、dry-run pointer byte-identical 证明 |

## Confidence Assessment

| Area | Confidence | Notes |
|---|---|---|
| Stack | HIGH | 与当前仓库实际依赖和 Phase 07–11 已验证模式一致；无需新生产依赖 |
| MVP feature boundary | HIGH | PROJECT/STATE 与四份研究一致：只读复用、candidate-only、single-book dry-run、no consumer cutover |
| Persistence/version architecture | HIGH | 现有 chunk/timeline/clue/narrative-unit 已提供 manifest、version、pointer/journal、append-only 先例 |
| Claim ontology / state deltas | MEDIUM | 必需方向明确，但字段粒度和上限需用真实小说 package 验证 |
| Arc/Volume segmentation | MEDIUM | 连续范围与显式卷界原则明确；无卷界窗口和边界稳定性尚未实测 |
| Hierarchical retrieval gain | MEDIUM | RAPTOR/GraphRAG 支持方法方向，但本项目相对 leaf baseline 的增益未知 |
| Spoiler/provenance/security controls | HIGH | 既有项目先例明确，且可用 deterministic/DB/adversarial tests 证明 |
| Evaluation thresholds | MEDIUM | 指标分类明确，但阈值必须在冻结单书 fixture、成本预算和 baseline 定稿时确定 |

**Overall confidence:** HIGH for roadmap direction；MEDIUM for quality/performance outcome。架构与执行边界足以进入 roadmap，但是否值得后续 promotion 必须由 Phase 6 决定。

### Gaps to Address During Phase Planning

- 选择哪一本已有合格 active hierarchy 的小说，以及如何证明 fixture 不含训练/调参泄漏。
- ChapterState claim kinds、state before/after 语义、unknown/conflict 规则和每章节点/claim/package 上限。
- arc 的显式卷界来源、无卷界连续窗口、重叠策略和 boundary change invalidation oracle。
- optional timeline/relationship/clue 版本兼容矩阵；来源 unavailable、available-empty、lineage-mismatch 的严格区别。
- memory candidate vector collection 的命名、metadata、reconcile checksum 和 Chroma unavailable 降级行为。
- partial candidate 是否可用于离线 retrieval、其 visible node 规则和 report 表达；不得误标 complete。
- frozen evaluation gold、baseline parity、comparative threshold、faithfulness judge 与 deterministic hard gate 的组合。
- dry-run no-write proof 的精确表清单、promotion import ban 和并发 active pointer 变化测试。

## Sources

### Primary / Official（HIGH confidence）

- [RAPTOR, ICLR 2024](https://openreview.net/forum?id=GN921JHCRw) — 多抽象层 bottom-up 构建、tree/collapsed retrieval 和长文档问答依据。
- [Microsoft GraphRAG Query Overview](https://microsoft.github.io/graphrag/query/overview/) — global/local/DRIFT/basic 模式与成本差异。
- [Microsoft GraphRAG Indexing Overview](https://microsoft.github.io/graphrag/index/overview/) — 多粒度 reports、图抽取和 embedding pipeline；用于明确“不照搬完整管线”。
- [PostgreSQL 16 Recursive Queries](https://www.postgresql.org/docs/16/queries-with.html) — 关系型树遍历、search order 与 cycle detection。
- [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) — entity/activity/derivation/collection 溯源模型。
- [OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html) — retrieved-content injection、多租户检索与数据泄漏风险。
- [RAGAS, EACL 2024](https://aclanthology.org/2024.eacl-demo.16/) 与 [RAGChecker, NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/27245589131d17368cccdfa990cbf16e-Abstract-Datasets_and_Benchmarks_Track.html) — retrieval、faithfulness 与 answer quality 分模块评测。

### Local Authority（HIGH confidence）

- `.planning/PROJECT.md`、`.planning/STATE.md`、`.planning/ROADMAP.md` — v0.8 目标、active requirements 与 Phase 07–11 决策。
- Phase 07 chunk hierarchy/build/promotion、Phase 08 timeline worker/promotion、Phase 09 relationship evidence、Phase 10 reader-chat retrieval/context、Phase 11 clue versions/lifecycle 源码与验证资料。
- 本目录的 [STACK.md](./STACK.md)、[FEATURES.md](./FEATURES.md)、[ARCHITECTURE.md](./ARCHITECTURE.md)、[PITFALLS.md](./PITFALLS.md)。

### Inference Requiring Validation（MEDIUM confidence）

- 固定叙事层级相对 RAPTOR clustering/GraphRAG community reports 更适合本项目的成本、lineage 和 spoiler 约束；需由单书对照确认质量。
- adaptive coarse-to-fine/collapsed multi-level 会比严格 tree traversal 更稳健；需测 upper-route miss、leaf recall、延迟和成本。
- 显式卷界/确定性连续窗口足以形成 MVP arcs；需用真实小说验证多线叙事与边界失效范围。

---
*Research completed: 2026-07-15*  
*Ready for roadmap: yes*  
*Non-negotiable boundary: existing assets read-only; new data candidate-only; dry-run never moves an active pointer.*
