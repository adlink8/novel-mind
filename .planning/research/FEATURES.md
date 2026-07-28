# Feature Research

**Domain:** NovelMind v0.8 分层叙事记忆与层级 RAG（单书纵向 MVP）  
**Researched:** 2026-07-15  
**Confidence:** HIGH（项目边界与既有资产）；MEDIUM（层级检索相对收益，必须由本项目冻结评测验证）

## Research Frame

本研究不是重新设计 NovelMind 的全部分析产品，也不是照搬 RAPTOR 或 GraphRAG。v0.8 要验证的单一假设是：**在不重新调用模型生成合格 Phase 07 底层资产、也不切换任何现有 active pointer 的前提下，能否增量构建可追溯的上层叙事记忆，并让高层问题通过 coarse-to-fine 路由最终回落到原文 evidence。**

外部研究支持“多抽象层检索”这一方向，但不证明它在本项目数据上必然优于现有检索：RAPTOR 从叶子递归聚类/摘要并跨抽象层检索；GraphRAG 用社区层级报告回答全局问题、用图与原始 text units 回答局部问题。两者都表明高层表示适合长文档整体问题，但 GraphRAG 官方也明确区分昂贵的 global search 与基础 raw-chunk search。因此 v0.8 必须保留底层检索基线、测量成本与质量，而不是预设新结构胜出。

### Locked inheritance from Phases 07–11

- Phase 07 的 active `chapter → scene → evidence`、source offsets、content hash、source snapshot、build manifest 是底层权威；raw chunk fallback 永远保留。
- 候选版本不可变；构建、验证或依赖失败不得移动 active pointer。
- LLM 只产生 strict-schema 候选/判断；脚本掌握作用域、证据、状态、写库、预算、缓存、门禁与发布决定。
- Phase 08–11 的 timeline / relationship / clue 可以作为已验证、版本化的只读输入，但不能替代原文 evidence；聊天永远不是事实源。
- spoiler cutoff 必须在服务端、在候选检索和任何摘要/计数/回答生成之前应用；缺失阅读进度仍只允许第一章。
- 人工 override 与历史版本不得被重分析覆盖。本 MVP 不替换现有时间线、人物关系、线索或聊天读模型。

## Feature Landscape

### Table Stakes（纵向 MVP 缺一不可）

| Feature | Why Expected | Complexity | MVP contract |
|---|---|---:|---|
| 只读分析资产清单 | 不先知道有哪些 active/candidate、快照、版本和缺口，就无法声称“复用而非重跑” | MEDIUM | 按 novel、domain、version/build 列出 hierarchy、timeline、relationship、clue 的状态、source/hierarchy lineage、manifest、节点/证据覆盖、失败与费用；审计过程零 provider call、零 pointer write |
| 明确的复用资格判定 | “表存在”不等于可复用；错误 lineage 会把旧事实带入新层 | HIGH | 每项资产给出 `reusable_exact`、`rebuild_required`、`blocked`；可选上游缺失另记 `unavailable_optional`，不能伪装成空数据；输出 reason codes 和最小重建范围 |
| 资格的硬校验 | 防止 hash、owner、版本、offset 或 parent tree 漂移 | HIGH | 至少校验 owner/novel scope、active/immutable 状态、source snapshot、manifest 重算、chapter 覆盖、offset 边界、content hash、parent-child 完整性、evidence 可解析性；任何占位 hash、歧义 active 或跨版本引用 fail closed |
| 上层节点的 strict contract | 自由摘要不可验证、不可增量重建，也无法安全检索 | HIGH | 候选层只新增 `ChapterState → StoryArc → GlobalStoryModel`；统一保存不可变 version、父子关系、章节范围、spoiler 上界、builder lineage、checksum、状态与费用；每条 `claim` 独立绑定 child/evidence refs |
| 章节状态变化而非仅摘要 | 叙事理解的价值在“本章改变了什么”，不是压缩复述 | HIGH | ChapterState 至少表达 facts、character/world state changes、relationship/clue/timeline references、unresolved questions；缺少证据的槽位为空，不得让模型补全 |
| 连续故事阶段与全书模型 | 高层问题需要比章节更粗的导航单位 | HIGH | StoryArc 只覆盖连续章节并记录 conflict/turning points/net changes；GlobalStoryModel 聚合已建 arcs。所有结论逐 claim 下钻，不能仅有 node-level 泛化 citation |
| 单书 candidate dry-run | MVP 要证明可运行、可计费、可失败隔离，而不是只定义 schema | HIGH | 选一部已有合格 active hierarchy 的 fiction 小说，构建完整候选层；不切 hierarchy/timeline/relationship/clue/chat 或新层 active pointer；产出覆盖、复用率、provider calls、tokens、cost、p50/p95、失败和重建范围报告 |
| 自下而上、可恢复构建 | 长篇构建会中断；整书重启不可接受 | HIGH | evidence/scene → ChapterState → StoryArc → GlobalStoryModel，按 durable checkpoint 恢复；每次模型调用前预算预留，未知价格/依赖/资格失败在调用前暂停；精确缓存绑定实际 source/prompt/schema/model/config lineage |
| coarse-to-fine 检索 | 上层记忆必须改变候选选择，而不只是多存三张表 | HIGH | 查询先选择允许可见的 global/arc/chapter 候选，再展开 child，最终只返回叶子 evidence；保留 raw/evidence baseline fallback，并记录 routing trace、各层分数与被裁剪原因 |
| 原文证据溯源 | 高层摘要会累积误差，最终回答必须能被用户核验 | HIGH | 每个返回 claim 都能沿 `derived_from` 路径解析到当前 candidate 所绑定 source snapshot 内的 evidence ID、chapter、offset、content hash；链断、hash 错或证据越界即不可回答/不可合格 |
| 全链路 spoiler 安全 | 高层标题、arc 范围、计数和路由本身也可能剧透 | HIGH | cutoff 在检索任何节点前应用；节点可见性由支持证据与章节上界共同约束；不可通过 future arc 名称、分数、计数、空缺或 trace 泄露；full-book 只复用既有显式持久开关 |
| 依赖图与局部重建 | “单层失败可局部重建”是当前 milestone 的硬要求 | HIGH | 持久保存 node dependency/derivation；脏 evidence/chapter 只使其 ChapterState、包含它的 arc 和 Global 失效；未变节点按 checksum carry-forward；若 arc 边界重算会改变后续归属，必须扩大失效范围而非假装局部安全 |
| 结构与质量评测 | 能构建不等于值得推广；已有 v0.3 检索指标缺口不能被掩盖 | HIGH | 冻结单书问题覆盖 chapter/local、cross-chapter/arc、whole-book/global、证据定位、无答案和 spoiler；同时测结构 invariant、leaf evidence recall/ranking、路由命中、answer faithfulness、成本/延迟、复用节省与局部重建范围，并与 raw/evidence baseline 对比 |
| fail-closed 推广门禁 | dry-run 不能悄悄成为生产事实源 | MEDIUM | MVP 只输出 `qualified_candidate` 或 `blocked`，不执行 promotion。必须 0 个跨 owner/跨 snapshot/断链 citation、0 spoiler leak、100% 结构与 claim→leaf 可解析、全部必需 metrics 非空、固定命令与新鲜 PostgreSQL authority 可复核；质量阈值在冻结 fixture 前写入 policy |

### Reuse Qualification Matrix

复用资格必须按资产、按版本判定，不能给整本书一个含糊的“可复用”标签。

| Asset | `reusable_exact` 必要条件 | 失败后的最小动作 | 是否阻塞 MVP |
|---|---|---|---|
| Phase 07 hierarchy | active pointer 唯一且指向 immutable/qualified build；source snapshot 与小说源一致；manifest 可重算；chapter→scene→evidence tree、offset、hash、coverage 全部有效 | 只重建不合格 chapter/build；本 dry-run 不自动执行，只报告 | **是**；底层权威不可用即不调用上层模型 |
| Phase 08 timeline | owner/novel/version 与所选 source/hierarchy lineage 一致；evidence refs 全部解析；active/running 不混合 | 标记 timeline enrichments 不可用；ChapterState 仍可从 hierarchy 构建 | 否，除非实现错误地把 timeline 当必需事实源 |
| Phase 09 relationships | accepted observation、version、interval、evidence refs 可验证且 spoiler-safe | `unavailable_optional`，不生成零关系结论 | 否 |
| Phase 11 clues | lifecycle 重放有效；版本/evidence/cue-payoff 坐标与所选 lineage 一致 | `unavailable_optional`，不生成“无伏笔/未回收”结论 | 否 |
| Phase 10 chat | 不适用 | 永不读取为事实或构建输入 | 永远禁止 |

`rebuild_required` 表示已定位到可重建的 stale/missing 子树；`blocked` 表示 authority 歧义、跨 owner、manifest 无法证明或依赖状态不可信。审计不得在这两种状态下自动修复或调用模型。

### Upper-node minimum data contract

| Contract area | Required fields/behavior | Reason |
|---|---|---|
| Identity & scope | node/version ID、owner_id、novel_id、node_type、ordinal/range、parent/children | 防止跨小说和跨版本混合，支持稳定遍历 |
| Lineage | source_snapshot_hash、hierarchy_build_id/checksum、可选 timeline/relationship/clue version、prompt/schema/model/config/policy hashes | 支持精确缓存、复现和资产资格 |
| Claims | typed `claims[]`，每条含 statement、kind、confidence、support refs、narrative interval、spoiler max chapter | 防止“一份摘要挂一个宽泛引用”的伪溯源 |
| State deltas | before/after 或 added/changed/resolved/unresolved；未知显式保留 unknown | 支持章节间状态推进与局部重算 |
| Derivation | direct child refs + 可解析 leaf evidence refs；派生 checksum | 支持 coarse-to-fine 检索和 W3C PROV 式 derivation 追踪 |
| Lifecycle | immutable candidate、build status/checkpoint、created_at、usage/cost/latency、failure reason | 支持 dry-run、恢复和失败隔离 |
| Visibility | min/max chapter、支持证据集合、服务端 cutoff 结果 | 防止高层元数据泄露未来情节 |

### Differentiators（验证后才值得推广）

| Feature | Value Proposition | Complexity | Notes |
|---|---|---:|---|
| 按 claim 而非按摘要溯源 | 比常见“summary node 引用一组 chunks”更可核验，可定位哪条高层结论失去支持 | HIGH | 与 NovelMind 的 source offsets/content hash 资产天然契合 |
| 叙事状态 delta memory | 对“人物何时改变、关系为何转折、伏笔如何推进”比普通递归摘要更有表达力 | HIGH | 先做冻结字段，不做开放 ontology |
| 复用收益可量化 | 明确报告 avoided model calls/tokens/cost 与 carry-forward 节点数，直接验证“不重新分析”的商业价值 | MEDIUM | 不能只报告新构建成本 |
| 可解释的层级路由 trace | 展示 query 为什么选择某 arc/chapter、最终使用哪些 evidence，便于调试误路由 | MEDIUM | 对用户默认隐藏；先作为评测/运维 artifact |
| 基线共存而非强制替换 | 对每类问题比较 hierarchical 与 raw/evidence baseline；层级失败可回退 | MEDIUM | 新检索只有证明非劣或在目标问题上增益后才进入后续读路径 |
| 精确失效与保守升级 | 能证明安全时局部重建；arc 边界受影响时自动扩大 dirty set | HIGH | 比“永远整书重跑”和“过度乐观局部刷新”都更可靠 |
| 现有领域资产作为可选证据化 enrichments | timeline/relationship/clue 可增强 ChapterState，但任何来源缺失都不会制造负事实 | HIGH | 必须保留每个来源的版本与 availability 状态 |

### Anti-Features（本里程碑明确不做）

| Feature | Why Requested | Why Problematic | Alternative |
|---|---|---|---|
| 全书从原文重新分析 | 看起来最一致 | 浪费已验证资产和费用，违背 milestone 核心目标，也难证明复用 | 先只读审计；仅对 `rebuild_required` 子树给出后续补跑计划 |
| 一次性替换现有 timeline/relationship/clue/chat | 立刻让新架构“上线” | 将研究性候选扩大成多产品迁移，回归面过大 | v0.8 只提供 dry-run 与评测入口，后续按消费者逐个 opt-in |
| 自动切新层 active pointer | 省去人工步骤 | 未验证摘要会成为生产真值，且本轮明确选择 dry-run | 只产生 promotion-ready verdict，不执行 pointer mutation |
| 摘要或高层节点直接充当最终证据 | 回答更短、更快 | 摘要会丢细节并累积幻觉，无法核验原文 | 高层只导航；最终 citation 必须是叶子 evidence |
| 自由文本“全书世界模型” | 设计简单、展示效果强 | 无 claim 边界、不可局部失效、不可精确溯源 | strict claims + typed deltas + unresolved/unknown |
| 一开始完整复制 RAPTOR | 论文有长文本收益 | 递归聚类边界可能破坏小说自然 chapter/arc 结构，并新增 embedding/cluster 成本 | 保留既有 chapter tree，先构建三层叙事节点并与 baseline 对比 |
| 一开始完整引入 GraphRAG/Neo4j/新框架 | 名称与“层级 RAG”接近 | 项目已有 PostgreSQL authority、可选 Neo4j 投影和禁止新增编排框架的决策；迁移成本大于 MVP 信息增益 | 借鉴 global/local 分流，沿用现有 FastAPI/SQLAlchemy/PostgreSQL/LiteLLM |
| 让模型自主决定 arc 边界、事实、写库和发布 | 减少规则代码 | 不可复现，边界变化扩大重建，越权违背 Phase 04/07–11 | 模型仅给 strict candidate；脚本校验连续覆盖、证据和状态 |
| 相似度/共现/聊天作为事实 | 召回容易且信号多 | 会把相关性误当支持，聊天还违反 Phase 10/11 事实边界 | 只作候选召回信号；事实必须回到 evidence |
| 默认全书高层索引可见 | Global search 更完整 | arc 标题、节点计数甚至检索 trace 都可能剧透 | 所有层先按 persisted cutoff 构造可见候选 |
| 过度承诺“改一章只重建一个节点” | 局部刷新听起来便宜 | arc 分段或后续状态依赖可能连锁变化 | 依赖图精确失效；无法证明边界稳定时扩大到受影响 arc/后缀 + Global |
| 在线实时重建与 streaming UI | 更像生产功能 | 不属于验证层级收益的最小闭环 | durable offline/background dry-run + 状态报告 |
| 新增面向用户的摘要/arc/world-model 菜单 | 可快速展示成果 | Phase 08–11 明确不暴露分析中间件，本轮也不改现有页面 | 先用 CLI/API/eval artifact 验证，产品化另立里程碑 |

## Feature Dependencies

```text
[A. Audit schema + reason codes]
    └──requires──> [B. Read-only inventory + lineage/manifest verification]
                         └──requires──> [C. Per-asset reuse decision]

[D. Upper-node strict contract + derivation graph]
    └──requires──> [C]
    └──enables──> [E. ChapterState candidate build]
                       └──enables──> [F. contiguous StoryArc build]
                                          └──enables──> [G. GlobalStoryModel build]

[H. server cutoff policy] ──constrains──> [E, F, G, coarse-to-fine retrieval]

[E + F + G]
    └──enables──> [I. coarse-to-fine retrieval + leaf fallback + routing trace]
                       └──requires──> [J. claim-to-leaf provenance validator]

[D. dependency graph] ──enables──> [K. local invalidation/rebuild simulation]

[B..K + frozen fixture + raw baseline]
    └──enables──> [L. single-book dry-run report]
                       └──enables──> [M. qualified_candidate | blocked verdict]

[M] ──does not imply──> [active pointer switch or consumer cutover]
```

### Dependency Notes

- **先审计、后调用模型。** 若 active hierarchy 的 snapshot/manifest/tree 不能证明，后续任何上层调用都会产生不可发布资产，因此必须在 provider call 前阻塞。
- **先定 claim-level contract、后生成摘要。** 否则 builder 先产出的自由文本很难在后续补齐可靠 derivation。
- **ChapterState 是最小可复用中间层。** Arc 和 Global 都依赖章节状态；不允许直接从整书原文一次生成 Global。
- **cutoff 是构建/查询约束，不是 UI filter。** 检索节点集合、分数、计数和 trace 都必须使用相同可见集。
- **局部重建依赖 derivation graph。** 仅比较时间戳或“章节号相同”不足以证明 carry-forward。
- **评测必须同时有 baseline。** RAPTOR/GraphRAG 的外部收益不能替代 NovelMind 自身的对照证据。
- **dry-run verdict 与 promotion 分离。** 本 milestone 可证明候选达到门槛，但不得因此自动改变任何消费者。

## MVP Definition

### Launch With（v0.8 纵向 MVP）

- [ ] 资产审计 CLI/service：零模型调用，输出 per-asset 资格、reason codes、覆盖和最小重建范围。
- [ ] 三类 strict candidate 节点：ChapterState、contiguous StoryArc、GlobalStoryModel；每条 claim 可下钻到 active source snapshot 的 leaf evidence。
- [ ] 单书 durable dry-run：使用合格 Phase 07 hierarchy，可选复用 Phase 08/09/11，完整记录 lineage、checkpoint、预算、费用和失败。
- [ ] coarse-to-fine 检索实验入口：支持 local/arc/global/无答案问题，服务端 cutoff，raw/evidence fallback 和 routing trace。
- [ ] 局部重建证明：用冻结变更场景验证 dirty-set、carry-forward checksum 和保守扩大范围。
- [ ] 冻结评测与 fail-closed verdict：结构/溯源/spoiler 为硬门，检索/faithfulness/成本/延迟与 baseline 比较；只输出候选资格，不切 pointer。

### Add After Validation（v0.8.x / next milestone）

- [ ] 真实 active pointer 与 rollback journal — 仅在 dry-run 连续通过、人工确认 cutover 范围后新增。
- [ ] 让 Reader Chat opt-in coarse-to-fine context — 需先证明 spoiler、citation 和质量非劣，并保留旧 context builder 回退。
- [ ] timeline/relationship/clue 消费 ChapterState — 每个消费者独立做版本兼容和回归资格，禁止一次性切换。
- [ ] 多书/多体裁 fixture 与容量测试 — 单书证明概念后再验证泛化和并发成本。
- [ ] 人工 arc boundary correction — 只有自动边界成为主要误差源时才建立追加式 override 产品。

### Future Consideration（v0.9+）

- [ ] DRIFT 式 global-to-local follow-up query expansion — 查询集证明单次 coarse-to-fine 召回不足时再做。
- [ ] 学习式 query router/reranker — 需要足够标注日志，且不能削弱 deterministic fallback。
- [ ] 跨小说主题/母题层 — 需要新的 owner/corpus/spoiler 与版权边界，不属于单书 world model。
- [ ] 用户可见 arc/world-model 浏览器 — 需要单独 UX、剧透与纠错设计。

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---|---:|---:|---:|
| Read-only asset audit + reuse qualification | HIGH | MEDIUM | P1 |
| Claim-level upper-node contract | HIGH | HIGH | P1 |
| Single-book candidate builder/dry-run | HIGH | HIGH | P1 |
| Claim→leaf provenance + spoiler enforcement | HIGH | HIGH | P1 |
| Coarse-to-fine retrieval with baseline fallback | HIGH | HIGH | P1 |
| Frozen evaluation + fail-closed verdict | HIGH | HIGH | P1 |
| Local invalidation/carry-forward proof | HIGH | HIGH | P1 |
| Reuse/cost savings report | MEDIUM | MEDIUM | P2 |
| Optional timeline/relationship/clue enrichments | MEDIUM | HIGH | P2（MVP 中允许但不得成为关键路径） |
| Consumer cutover / active pointer | HIGH | HIGH | P2（验证后） |
| User-facing world-model UI | MEDIUM | HIGH | P3 |
| Learned router / DRIFT expansion | MEDIUM | HIGH | P3 |

## Comparative Pattern Analysis

| Capability | RAPTOR | Microsoft GraphRAG | NovelMind v0.8 decision |
|---|---|---|---|
| 上层构建 | 对 chunks 递归聚类与摘要形成多层树 | 从 text units 抽取图、社区检测并自下而上生成多层 community reports | 不复制聚类/图 pipeline；利用既有 chapter→scene→evidence，自下而上新增三个叙事语义层 |
| 全局问题 | 跨树层检索不同抽象粒度节点 | Global Search 对 community reports 做 map-reduce | 先选 Global/Arc，再下钻 Chapter/Evidence；不允许高层节点成为最终事实 |
| 局部问题 | 可检索叶与中间层 | Local Search 结合图邻域与 raw text chunks | 保留 Phase 07 evidence/raw baseline；关系图只是可选只读 enrichments |
| 成本 | 递归摘要与 embedding 增加 indexing 成本 | 官方称 global search 资源密集，并提供 basic/fast alternatives | 单书 dry-run必须报告 avoided calls 与新增 calls/cost；未知成本 fail closed |
| 溯源 | 论文重点是树检索质量 | TextUnits 提供细粒度 references，local search 混合 raw chunks | 采用更严格的 per-claim derivation chain 到 source offsets/hash |
| 版本/回滚/剧透 | 非小说产品核心 | 非小说阅读进度核心 | 继承 NovelMind immutable candidate、active isolation、server cutoff、rollback/override 边界 |

## Promotion-readiness Gates

MVP 不执行推广，但必须生成可机器验证的 verdict。建议把门分为硬正确性与对照质量两类：

### Hard gates（任一失败即 `blocked`）

- 资产审计在 provider call 前完成；底层 hierarchy 为 `reusable_exact`。
- chapter/arc/global 覆盖、连续范围、parent-child、checksum、immutable/version invariants 全部通过。
- 100% 已发布候选 claims 能解析到同 owner/novel/source snapshot 的有效 leaf evidence；0 个断链、跨版本、跨 owner 或 offset/hash mismatch。
- spoiler/无进度/显式 full-book/未来 arc metadata 对抗用例 0 泄露。
- 构建失败、预算不足、未知价格、optional source unavailable、取消与恢复均不移动任何 active pointer。
- 局部重建测试证明未变节点 checksum carry-forward；边界不确定时 dirty set 保守扩大。
- 资格命令、policy/fixture/source/prompt/schema/model/config hashes 与 PostgreSQL authority 可由独立 verifier 重算；必需 metrics 不得为 null。

### Comparative gates（阈值在看 test fixture 结果前冻结）

- 对 chapter/local、arc/cross-chapter、global/whole-book 分桶报告 leaf evidence recall@k、MRR/nDCG 或等价排序指标及 routing hit rate。
- 回答质量单独报告 faithfulness/groundedness、answer relevance、无答案拒答；不能用一个总分掩盖检索失败。
- 与现有 raw/evidence baseline 同 source snapshot、同 cutoff、同问题、同预算口径比较；至少证明总体非劣，并在目标 cross-chapter/global 桶达到预先冻结的增益门。
- 报告 indexing/build 与 query 的 p50/p95、provider calls、tokens、费用；同时报告复用避免的 calls/tokens/cost。
- 现有 v0.3 confirmed 数据与指标缺口必须显式列为外部限制；v0.8 的单书冻结 fixture 不能被描述成关闭了全项目 RAG 质量缺口。

## Recommended Delivery Order

1. **Audit & eligibility:** 定义审计 schema/reason codes，读取一部候选小说的真实 hierarchy/domain assets，得到零调用资格报告。
2. **Contracts & provenance:** 冻结三层 node/claim/derivation/checksum/cutoff contract 和不可变 candidate lifecycle。
3. **Bottom-up builder:** 先 ChapterState，后 contiguous StoryArc，最后 Global；接入 durable checkpoint、预算、exact cache 与 optional source availability。
4. **Retrieval & evidence:** coarse-to-fine router/expander、leaf evidence resolver、raw fallback、routing trace、服务端 spoiler 对抗。
5. **Local rebuild:** 以 source/hash 变更 fixture 验证 dirty-set、carry-forward 与保守扩散。
6. **Single-book qualification:** 冻结问题集和 policy，与 baseline 同源评测，产出 `qualified_candidate | blocked` dry-run 报告；明确不 promotion、不改产品 UI。

## Sources

### External primary/official sources

- Sarthi et al., **RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval**, ICLR 2024. https://openreview.net/forum?id=GN921JHCRw — 自下而上递归摘要树及跨抽象层检索的主要依据。
- Microsoft, **GraphRAG Indexing Overview**. https://microsoft.github.io/graphrag/index/overview/ — text units、图抽取、社区层级、多粒度报告与 embedding pipeline。
- Microsoft, **GraphRAG Query Engine Overview**. https://microsoft.github.io/graphrag/query/overview/ — local/global/basic/DRIFT 查询模式；local 结合图与 raw chunks，global 使用高层报告且成本较高。
- Microsoft, **GraphRAG Getting Started**. https://microsoft.github.io/graphrag/get_started/ — 官方建议先用教程数据与低成本模型实验，避免直接投入昂贵 indexing job。
- W3C, **PROV-DM: The PROV Data Model** (Recommendation). https://www.w3.org/TR/prov-dm/ — entity/activity/derivation/collection 形式化溯源概念，用于 claim/node/evidence 派生链设计。
- Es et al., **RAGAS: Automated Evaluation of Retrieval Augmented Generation**, EACL 2024. https://aclanthology.org/2024.eacl-demo.16/ — 将 retrieval context relevance/recall、generation faithfulness 与 answer relevance 分开评测的依据。

### Local authoritative sources

- `.planning/PROJECT.md` — v0.8 goal、active requirements 与“不推倒现有资产”边界。
- `.planning/STATE.md` — Phase 07–11 已验证实现事实、lineage/spoiler/qualification 决策。
- `.planning/ROADMAP.md` — 当前 milestone 和既有 phases 的依赖/完成状态。
- `.planning/phases/07-semantic-hierarchical-chunking/07-CONTEXT.md`、`07-VERIFICATION.md` — hierarchy authority、candidate lifecycle、incremental carry-forward 和 raw fallback。
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-CONTEXT.md`、`08-SPEC.md` — immutable analysis versions、evidence、budget、active isolation 与 server-side spoiler。
- `.planning/phases/09-dynamic-character-relationship-graph/09-CONTEXT.md` — accepted observations、append-only history、optional Neo4j 与 visible-set-first 边界。
- `.planning/phases/10-reader-selection-ai-and-multi-session-conversations/10-CONTEXT.md` — chat 是解释界面而非事实源。
- `.planning/phases/11-clue-and-foreshadow-tracking/11-CONTEXT.md`、`11-SPEC.md` — optional sources、evidence-only lifecycle、fail-closed qualification。

---
*Feature research for: NovelMind v0.8 分层叙事记忆与层级 RAG纵向 MVP*  
*Research scope: asset reuse → candidate upper memory → coarse-to-fine retrieval → single-book dry-run qualification; no consumer cutover.*
