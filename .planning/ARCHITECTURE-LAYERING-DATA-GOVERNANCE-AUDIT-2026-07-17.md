---
audit_type: architecture-layering-data-governance
project: NovelMind
date: 2026-07-17
status: gaps_found
method: gsd-docs-update + gsd-audit-milestone + live code/database/test verification
scope:
  - architecture boundaries
  - semantic/data/release layering
  - SSOT and projection authority
  - lineage, lifecycle, rebuild, promotion and rollback
  - documentation and implementation drift
non_goals:
  - feature completion percentage
  - authorizing Narrative Memory promotion
  - fixing source code in this audit
---

# NovelMind 架构、分层与数据治理完整审计

## 1. 审计结论

NovelMind 的底层方向总体正确：原文与派生认知分离，PostgreSQL 作为权威事实库，Chroma/Neo4j 作为可重建索引或投影，Candidate 与 Active 分离，模型输出必须通过证据闭包、门禁、版本和审计链路后才能消费。

当前主要问题不是“缺少更多层”，而是以下四类边界尚未完全收口：

1. **层级语义没有统一命名空间**：语义粒度、数据成熟度、发布生命周期、软件架构层都曾使用普通的 `L0/L1/...` 表达，容易发生同名异义。
2. **多个叙事系统并存**：Narrative Unit、Narrative Memory、Timeline、Relationship、Clue、旧 Character API 同时存在，权威关系和退役路径未被一份统一契约完整描述。
3. **文档完成状态与真实数据状态分离不足**：Phase/Plan 完成常代表“能力实现完成”，不代表“整书数据已生成或质量已证明”。
4. **当前工作树与验证基线不干净**：存在测试契约漂移、前端构建失败、大量 WIP/运维临时文件和过期审计文档。

本审计不建议新增更多通用层。建议先固定四个正交命名空间：

| 命名空间 | 解决的问题 | NovelMind 示例 |
|---|---|---|
| `S*` Semantic | 语义粒度 | Source → Evidence → Scene → Chapter State → Arc → Global |
| `D*` Data maturity | 数据成熟度 | Raw → Canonical → Derived → Serving |
| `R*` Release lifecycle | 发布状态 | Draft/Candidate → Validated → Qualified → Active → Rolled back |
| `A*` Architecture | 软件依赖 | Delivery → Application → Domain contracts → Infrastructure adapters |

禁止再用单一 `L0-L6` 同时表达以上四种概念。

---

## 2. 建议固定的语义层级

```text
S0 Source Text
  小说原文 Chapter.content；不可由摘要替代

S1 Evidence
  精确 chapter/offset/hash 引用；上层事实的叶子证据

S2 Scene
  语义完整的场景/事件片段；由 Evidence 组成

S3 Chapter
  章节结构与场景集合；不是模型摘要

S4 Chapter State
  章节结束后的角色、关系、冲突、线索和世界状态变化

S5 Arc / Volume
  连续章节形成的故事阶段、冲突和人物变化

S6 Global Story
  只从已验证子层聚合的全书模型
```

Timeline、Relationship、Clue 不是新的主层级，而是挂载于 `S2-S6` 节点上的 **Facet/Projection**。它们必须保留来源类型、版本、置信度和叶子证据，不得成为第二套原文权威。

---

# 3. 完整问题清单

状态说明：

- **OPEN**：当前需要处理。
- **DESIGN DEBT**：方向可用，但边界或契约未收口。
- **DOC DRIFT**：文档与当前代码/数据库不一致。
- **INTENTIONAL BOUNDARY**：当前是有意限制，不应误判为已完成能力。
- **OBSERVED DATA GAP**：代码能力存在，但真实样例数据未形成目标结构。

## 3.1 层级定义与命名

### NM-ARCH-001 — 层级编号漂移

**状态：OPEN / DESIGN DEBT**

Phase 20 产品文档曾使用：

```text
L0 Evidence → L1 Scene → L2 Chapter State → L3 Arc/Volume → L4 Global
```

跨项目层级推演文档则使用：

```text
L0 Source → L1 Evidence → L2 Scene → L3 Chapter
→ L4 Chapter State → L5 Arc/Volume → L6 Global
```

同一个 `L2/L3/L4` 在不同文档中代表不同实体，后续数据库、API、UI、评测和 Agent 计划都可能误读。

**治理要求：**采用 `S0-S6`；旧 `L*` 仅作为历史文档，不再进入新 schema、API 或变量名。

### NM-ARCH-002 — 语义层、发布层、数据层混用“层级”一词

**状态：OPEN / DESIGN DEBT**

当前文档中“层级”可能分别指：

- Chapter→Scene→Evidence；
- Chapter State→Arc→Global；
- Candidate→Active；
- PostgreSQL→Chroma→Neo4j；
- UI 全书→章节→事件下钻。

这些是正交维度，若继续混称，会导致错误依赖，例如把 UI 聚合层当作数据库事实层，或把 Active 当作更高语义层。

### NM-ARCH-003 — 缺少单一 Layer Registry / ADR

**状态：OPEN**

当前层级定义散落在 Phase 07、12-17、20、architecture docs 和 cleanup 思考文档中。缺少一份机器和人共同遵循的权威注册表，明确：

- 每层输入、输出和 SSOT；
- 是否可重建；
- 是否允许模型写入；
- 是否允许被引用为事实；
- 父子依赖和失效传播；
- 对应数据库表、API 和索引。

### NM-ARCH-004 — `ChunkHierarchyNode.level` 与产品语义层级存在概念碰撞

**状态：DESIGN DEBT**

Phase 20 明确保留旧 `ChunkHierarchyNode.level` 枚举，同时 UI 又以 Narrative Memory 层作为 L2-L4 骨架。两个“level”属于不同系统，若未在 API schema 中使用不同字段名，容易被前端或后续 Agent 合并成一套层级。

**建议字段：**`chunk_level`、`semantic_level`、`release_status` 分离。

---

## 3.2 SSOT、权威与投影边界

### NM-GOV-001 — 多个“叙事”子系统名称过近

**状态：OPEN / DESIGN DEBT**

代码中同时存在：

- `NarrativeUnit` / `NarrativeIndexBuild` / `NarrativeActivePointer`；
- `NarrativeMemoryVersion` / Node / Claim / Edge / SourceLink；
- `NarrativeMemoryBuild*`；
- `narrative_source_*`、`narrative_refresh_*`。

Narrative Unit 已具有 Active Pointer 和 Promotion Journal，而 Narrative Memory 当前又是 candidate-only。相近命名容易让维护者误以为二者共享发布生命周期或相同数据模型。

**治理要求：**在架构文档中明确两者的用途、依赖、是否替代、各自 Active 的含义；必要时改为 `RetrievalUnit` 与 `StoryMemory` 等不同命名域。

### NM-GOV-002 — PostgreSQL、Chroma 和 Neo4j 的权威顺序虽有原则，但缺少统一运行契约

**状态：DESIGN DEBT**

现有原则是 PostgreSQL 权威、Chroma 为向量索引、Neo4j 为可选投影且不得成为第二真相源。仍需一份统一契约说明：

- 哪些表构成事实源；
- 索引 checksum/manifest 如何绑定数据库版本；
- 索引缺失、损坏、滞后时如何 fail closed 或 fallback；
- Neo4j 如何 replay，如何证明无反向写权；
- API 是否允许在投影落后时返回混合版本数据。

### NM-GOV-003 — PostgreSQL/Chroma 双写没有分布式事务

**状态：OPEN / KNOWN CONSISTENCY GAP**

RAG 管线先写 PostgreSQL TextChunk，再写 Chroma embedding。任一步失败会产生：

- DB 有 Chunk、向量缺失；
- 候选 collection 部分写入；
- 数据库版本与索引 manifest 不一致；
- 查询命中旧 collection。

已有版本、reconcile、candidate/promotion 思想，但基础 TextChunk→Chroma 链仍需要明确 journal、幂等键、完成标记和重建检查。

### NM-GOV-004 — Narrative Memory 无生产 Promotion 是有意边界，不等于发布链完成

**状态：INTENTIONAL BOUNDARY**

当前 Phase 20 API 明确只读 `candidate_preview`，禁止 active pointer 和 promote，也未切换 Reader Chat。这是正确的安全边界，但状态文档必须避免把“Phase 20 complete”解释为“NM 已成为生产权威”。

未来若授权 Promotion，必须先补：

- Active Pointer 唯一权威；
- CAS promotion；
- before/after manifest；
- rollback journal；
- Reader Chat 兼容与对照；
- 旧 Narrative Unit 与 NM 的消费优先级。

### NM-GOV-005 — Facet 生产权威与结构主轴缺少禁止反馈环的正式规则

**状态：DESIGN DEBT**

Phase 08/09/11 是 Timeline/Relationship/Clue 的生产权威，Phase 20 只消费它们。需要明确禁止：

```text
Timeline/Relationship/Clue 派生结果
→ 无证据地反写 Source/Evidence/Scene
→ 再作为自身下一轮事实输入
```

可选 enrichment 必须携带 lineage，并且 unavailable 不能被解释为“事实为零”。

### NM-GOV-006 — Neo4j 可选投影尚缺长期防双写约束

**状态：DESIGN DEBT**

文档说明 Neo4j 默认关闭、仅为投影，但仍需自动化约束：

- Neo4j adapter 只读 PostgreSQL accepted facts；
- 无 Neo4j→PostgreSQL domain write；
- 投影可全量 replay；
- projection version 与 PostgreSQL manifest 绑定；
- Neo4j 不可单独支撑用户可见“事实”。

---

## 3.3 数据依赖、重建与生命周期

### NM-DATA-001 — Phase 完成度与真实数据覆盖率未使用独立指标

**状态：OPEN / DOC DRIFT**

`.planning/STATE.md` 显示 20/20 phases、82/82 plans、100%，但小说 91 的 Narrative Memory 当前实际为：

- 515 章；
- 117 个 completed stage；
- 33 failed；
- 366 pending；
- 117 个 `chapter_state`；
- 0 Arc；
- 0 Global；
- Build Run=`partial`。

Plan completion 与 Data readiness 都是真实信息，但必须分别展示，不能共用单一百分比。

**建议新增：**`implementation_readiness`、`sample_data_coverage`、`quality_qualification` 三个独立维度。

### NM-DATA-002 — 父层构建被长篇局部失败阻断

**状态：OBSERVED DATA GAP / ORCHESTRATION RISK**

Arc/Global 必须等待所需 Chapter State 完成。515 章长篇中少量 schema/package/budget/transport 失败会阻断整本上层生成。虽然已有 checkpoint、resume 和 failure isolation，但当前数据证明“理论可恢复”尚未稳定转化为“长篇可完成”。

需要持续验证：

- 单章失败是否只阻断所属 Arc；
- 是否允许 qualified partial Arc；
- 父层完整性与可用性如何区分；
- retry 是否可能无限积累失败 stage；
- 预算结算与实际 token 超限后如何继续。

### NM-DATA-003 — 真实样例尚未形成跨层闭环

**状态：OBSERVED DATA GAP**

当前样例同时存在：

- 1,933 timeline events，但 causal edges=0；
- 41 accepted relationship observations，全部 `establish`；
- `change=0`、`end=0`、`valid_to=0`；
- 32 clues，`payoff_chapter=0`；
- clue lifecycle events=0；
- clue links=0；
- NM 无 Arc/Global。

这不表示分层架构错误，但说明上层认知尚未证明能从底层数据稳定产生。

### NM-DATA-004 — Timeline 事件量与因果层缺失

**状态：OPEN / SEMANTIC QUALITY GAP**

时间线目前更接近按章事件目录，尚未形成事件因果图。若 UI 或 Global Story 把事件顺序误当因果，会产生错误认知。

### NM-DATA-005 — Relationship seed/backfill 可被写成 accepted，但缺少 intake provenance

**状态：OPEN**

样例 accepted 关系以 seed/backfill `establish` 为主。当前图查询无法低成本区分：

- 正式 LLM 多阶段观察；
- Timeline seed/backfill；
- 临时共现候选。

仅有 `accepted` 不足以表达事实来源。应增加并贯穿 API/UI 的 `intake_kind` / `producer_kind`。

### NM-DATA-006 — Clue payoff 状态机存在结构性阻断

**状态：OPEN / P0 DATA GOVERNANCE BUG**

Live judge 对 29 次调用中多次返回 `classification=payoff`，但 worker 首次状态迁移只允许 `cue_only` 进入 Active。`payoff` 被 gate 拒绝后进入 provisional 并提前返回，后续 lifecycle/payoff materialization 永远不执行。

结果：模型判断与数据库生命周期事实不一致。重复模型调用无法修复，必须修改状态机/门禁。

### NM-DATA-007 — Clue 标题字段由 rationale 截断生成

**状态：OPEN**

Judge schema 没有独立 `short_title`。当前标题直接截断 rationale 首行，造成 `The cue evidence (ev-hn_...)` 等元信息标题。标题是展示字段，不能复用审计理由字段。

### NM-DATA-008 — Clue 成本审计未写入真实 cost

**状态：OPEN / AUDITABILITY GAP**

Live re-judge 记录了 tokens 和调用，但数据库 `cost_usd` 合计为 0，只能离线估算约 `$0.05`。预算与成本治理要求 provider price snapshot、实际 usage 和 settled cost 一致落库。

### NM-DATA-009 — 评测数据权威位置漂移

**状态：OPEN / DOC DRIFT**

`IMPLEMENTATION-STATUS.md` 记载过 100 条题、10 confirmed、6 次运行；当前 PostgreSQL 实查 `eval_datasets=0`、`eval_runs=0`。可能是旧数据库、旧环境或历史快照，但当前文档没有标明数据快照 ID 和数据库来源。

结果：当前环境无法证明基础 RAG 的 Recall/MRR/NDCG/Faithfulness。

**治理要求：**评测报告必须绑定 DB URL fingerprint、dataset version、source snapshot、run manifest，禁止只写“当前有 N 条”。

### NM-DATA-010 — 基础 RAG、Narrative Unit、Narrative Memory 三种检索层的消费优先级未统一

**状态：DESIGN DEBT**

系统同时存在 raw chunk hybrid、Narrative Unit 索引和 NM hierarchical retrieval experiment。需要固定：

- 哪个是当前生产默认；
- query router 的降级顺序；
- 上层缺失/partial 时是否回落；
- citation 只能来自哪一层；
- 同一问题是否允许跨版本混合。

---

## 3.4 API 与系统演进

### NM-API-001 — 旧 Character API 与新 Relationship 系统双轨

**状态：OPEN**

`backend/app/api/characters.py` 仍声明占位，查询返回空、抽取返回 501；新 Phase 09 Relationship API 已实现。若旧路由仍暴露在 OpenAPI 或前端文档中，会造成两个“人物关系权威”。

需要选择：删除、明确 deprecated 410、或适配到新系统。禁止继续返回看似合法的空数组。

### NM-API-002 — `analysis/analyze/stream` 仍为 501

**状态：OPEN / CONTRACT GAP**

非流式分析可用，但路由和文档仍暴露 stream 占位。应删除未实现契约、返回 capability metadata，或实现；长期 501 会让客户端误判暂时故障。

### NM-API-003 — Fanfiction API 暴露但完全未实现

**状态：OPEN / PRODUCT CONTRACT GAP**

创建与续写均返回 501，查询路径仍存在。若该能力不是当前里程碑，应从正式产品能力图中移出并标明 deferred。

### NM-API-004 — Superuser 跨 owner NM 查询可能 404

**状态：OPEN / AUTHORIZATION CONTRACT GAP**

Phase 20 residual 指出 NM scope 固定使用 current user owner，超级用户跨 owner 读取可能失败。需要明确超级用户是否具有审计读取权；不能由不同 API 各自猜测。

### NM-API-005 — 结构范围过滤文档已过期

**状态：DOC DRIFT**

`20-VERIFICATION.md` 仍写 Timeline 范围是 client-side residual；当前代码和 `IMPLEMENTATION-STATUS.md` 已实现服务端 `chapter_start/chapter_end`。验证文档必须带“superseded by”标记或更新，避免后续重复实现。

---

## 3.5 测试、构建、运行与仓库治理

### NM-ENG-001 — 后端关键域测试未完全通过

**状态：OPEN**

本次执行：301 passed / 1 failed。失败是 `TimelineModelGateway` 已将本地预算 key 改为 `stage:repair:N`，测试仍期待 `stage:attempt:N`。属于代码与测试契约漂移。

### NM-ENG-002 — 前端测试未完全通过

**状态：OPEN**

Windows Node 环境执行：217 passed / 1 failed。Motion source contract 检测 `timeline-chart.tsx` 使用任意 duration class。

### NM-ENG-003 — 前端生产构建失败

**状态：OPEN / RELEASE BLOCKER**

`next build` 编译完成后 TypeScript 检查失败：`relationship-graph.tsx` 的 Cytoscape `core` style 不满足类型要求，缺少颜色字段。当前不能视为可发布基线。

### NM-ENG-004 — pytest timeout 配置未安装对应插件

**状态：OPEN / TEST GOVERNANCE**

测试输出包含 unknown config/marker warning：`timeout`、`timeout_method`、`pytest.mark.timeout`。超时门禁可能没有实际生效。

### NM-ENG-005 — 工作树包含大量未提交 WIP 与运行产物

**状态：OPEN / REPRODUCIBILITY RISK**

包括：

- backend/frontend 大量修改；
- `_inspect_*`、`_probe_*`、`_rerun_*`、`_nm_*` 临时脚本；
- `.pid`、keep-alive、detached、tunnel 脚本；
- `.playwright-cli` 截图/YAML；
- timeline prototype；
- deploy WIP。

代码、运维脚本、临时诊断、日志和浏览器产物未按稳定目录/生命周期分类，导致“当前可重现版本”不明确。

### NM-ENG-006 — 审计脚本本身已漂移

**状态：OPEN / TOOLING DRIFT**

`backend/scripts/_audit_novel_gaps.py`：

- 引用不存在的 `CharacterRelation.source_id`；
- 查询不存在的 `clue_cards`、`clue_items`、`clue_observations`、`narrative_memory_units`、`narrative_unit_nodes`；
- 产生多次 ProgrammingError 后 rollback。

审计工具若不受契约测试保护，会给出不完整或误导结论。

### NM-ENG-007 — `.planning/codebase/CONCERNS.md` 严重过期

**状态：OPEN / DOC DRIFT**

该文件仍声称 Narrative Unit 表/服务/索引未实现、tsconfig 不存在等早期状态，而后续 Phase 05-20 已大量实现。Codebase map 作为 Agent 入口时会误导规划。

### NM-ENG-008 — 状态文档使用不同时间快照但未统一标识

**状态：OPEN / DOC GOVERNANCE**

`IMPLEMENTATION-STATUS.md`、`STATE.md`、`ROADMAP.md`、Phase Verification 和当前数据库存在不同日期、不同环境、不同授权边界。文档虽有部分日期说明，但缺少统一的：

- snapshot ID；
- database fingerprint；
- git commit/dirty state；
- supersedes relation；
- 当前权威优先级。

### NM-ENG-009 — 分支长期 ahead 且工作树脏，里程碑证据难以对应 commit

**状态：OPEN**

当前分支相对 origin ahead 218，且存在大量未提交修改。很多验证报告引用 commit hash，但当前运行结果可能来自未提交代码。需要 release evidence manifest 绑定 exact tree hash + dirty diff digest。

---

# 4. 不应误判为问题的正确边界

以下设计方向应保留：

1. PostgreSQL 是权威，Chroma/Neo4j 可重建。
2. 模型只生成候选，不能直接写生产事实。
3. Claim 必须下钻到叶子原文并重切校验。
4. 上层缺失或误路由时允许 leaf/raw fallback。
5. Candidate 与 Active 分离；当前禁止 NM Promotion 是安全边界。
6. Timeline/Relationship/Clue 缺失应返回 unavailable，不得解释为零事实。
7. 局部修改应通过 dependency graph 计算 dirty closure，而不是全量推倒重来。
8. 上层节点可重建，原文和审计事实不可由摘要覆盖。

---

# 5. 建议收口顺序

## P0 — 固定底层契约

1. 建立唯一 Layer Registry/ADR，采用 `S/D/R/A` 四个命名空间。
2. 明确 Narrative Unit 与 Narrative Memory 的边界、命名和消费顺序。
3. 固定 PostgreSQL→Chroma→Neo4j authority/replay/manifest 契约。
4. 修复 Clue payoff 状态机，避免模型判断与生命周期事实断裂。
5. 将 Phase 完成度、数据覆盖率、质量资格分开报告。

## P1 — 数据治理闭环

1. 为 Timeline/Relationship/Clue 强制 producer/intake lineage。
2. 为双写链增加 journal、reconcile、index completeness gate。
3. 固定评测数据快照和数据库 fingerprint。
4. 统一 raw chunk、Narrative Unit、NM 的 router/fallback/citation 规则。
5. 清理旧 API 双轨和过期 codebase map。

## P2 — 工程基线

1. 修复后端 1 个测试、前端 1 个测试和生产构建。
2. 安装并验证 pytest-timeout。
3. 将临时脚本、运行 PID、日志、浏览器产物迁入受治理目录。
4. 给审计脚本增加 schema contract tests。
5. 为每份 verification 标记 snapshot、commit、DB 和 supersedes。

---

# 6. 验证证据摘要

本次审计实际读取或执行的证据包括：

- `AGENTS.md`、README、architecture data/RAG/import docs；
- `.planning/ROADMAP.md`、`STATE.md`、Phase 10/20 Verification；
- Phase 20 NM partial、clue live re-judge、quality-next；
- Novel 91 PostgreSQL 实查；
- Narrative Memory/Unit ORM 模型；
- 关键后端测试：301/302；
- 前端 Vitest：217/218；
- Next production build：失败；
- 当前工作树状态。

本报告记录问题，不授权 Promotion、删除数据、修改 Active Pointer 或清理工作树。
