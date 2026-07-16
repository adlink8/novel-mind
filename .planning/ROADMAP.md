# Roadmap: NovelMind

## Milestones

- [x] v0.1 审计与启动修复 - 建立可信实现基线和启动级契约。
- [x] v0.2 安全与架构修复 - 关闭安全、迁移、路由、依赖和导入可靠性阻断项。
- [ ] v0.3 小说导入 + RAG 索引 - 导入/索引/搜索已交付，RAG 评测质量闭环仍有缺口。
- [x] v0.5 自动质量与 CI 门禁 - 以全栈自动化和双模型仲裁替代人工 RAG 质量确认；REQ-AUTO-11 closed（06-08/06-09）。
- [ ] v0.8 分层叙事记忆与层级 RAG - 复用现有证据资产，以 candidate-only 单书 dry-run 验证上层叙事记忆、局部重建和 evidence-final 分层检索。

第一个 active milestone 已按要求设为并完成"审计与启动修复"。v0.3 经 2006-06-13 复审后恢复为 active，详见 `.planning/v0.3-MILESTONE-AUDIT.md`。

## v0.3 Plans

- [x] 核心 RAG 管线（分块/embedding/ChromaDB）
- [x] 混合搜索（BM25 + 向量融合）
- [x] 前端搜索 UI + 阅读页内搜索
- [ ] 03-rag-eval: 评测基础设施已完成；100 confirmed、有效非零指标、faithfulness/cost 待完成 (GitHub Issue #2)
- [x] 全站 UI/UX 重构：响应式应用壳、文学编辑台视觉系统、搜索与阅读体验修复

## v0.2 Plans

- [x] 02-01: 仓库与上传边界修复
- [x] 02-02: 认证、SSRF 与密钥保护
- [x] 02-03: 持久化导入任务与里程碑收尾

## v0.2 Success Criteria

1. Git/secret、上传路径、未授权访问、SSRF 和密钥存储测试通过。 VERIFIED
2. PostgreSQL Alembic upgrade/current/check 通过。 VERIFIED
3. Next.js 输出 `/novels/[id]`，集合响应不包含正文或 `source_path`。 VERIFIED
4. backend/frontend tests、build、lint、CI 和依赖审计通过。 VERIFIED
5. 导入任务有持久 job、并发安全状态机、幂等重试和重启恢复。 VERIFIED

## Progress

| Milestone / Phase | Plans Complete | Status |
|---|---:|---|
| v0.1 审计与启动修复 | 3/3 | Complete |
| v0.2 安全与架构修复 | 3/3 | Complete |
| v0.3 小说导入 + RAG 索引 | 4/5 | Gaps Found (eval gold 质量) |
| Phase 4 知识图谱 LLM 门控 | 4/4 | Complete |
| Phase 5 叙事知识单元层 | 5/5 | Complete |
| v0.5 / Phase 6 自动质量与 CI 门禁 | 9/9 | **Complete** (REQ-AUTO-11 closed) |
| Phase 7 语义/层级分块 | 6/6 | **Complete** (logic+tests+PG/indexing wiring) |
| Phase 8 版本化小说分析与时间线 | 10/10 | **Complete** (35/35 must-haves verified) |
| v0.7 / Phase 9-11 叙事关系、阅读问答与线索追踪 | Phase 09: 5/5; Phase 10: 5/5; Phase 11: 5/5 | **09 VERIFIED**; **10 PARTIAL** (real Playwright residual); **11 implement-complete** (adversarial residual closed) |
| v0.8 / Phase 12-17 分层叙事记忆与层级 RAG | 6/19 plans (12+13 done) | **IN PROGRESS** — Phase 12–13 verified; Phase 14 next; candidate-only/no pointer cutover |
| Phase 18 前端动效与过渡系统 | 2/3 | **EXECUTING** — 18-01/02 done; 18-03 qualification next |

## Auto Start

**2026-07-16 已授权执行 Phase 13–18**（用户：查看13-18阶段任务 编排GSD子代理执行）。详见 `.planning/HANDOFF.json`。
执行顺序：Phase 13 收尾并验证 → 14 → 15 → 16 → 17；**Phase 18 可并行**。Phase 14 的 provider 调用仍必须使用 Phase 12 的 `provider_calls_allowed` 门禁。
Phase 06–11 历史实现与验证状态保持不变；Phase 10 real Playwright residual 和 v0.3 质量缺口不因 v0.8 单书 dry-run 自动关闭。

## Backlog

### Phase 999.1: 首页 UI 优化 (BACKLOG)
- 删除占位统计卡片（小说总数/章节总数/AI分析次数/同人文作品）
- 已扩展为全站 UI/UX 重构并完成：2006-06-13

### Phase 999.2: Bug 修复 (BACKLOG)
- 阅读页右上角"上一章"与"退出账号"按钮重合
- 搜索返回"搜索失败请重试"无内容
- 已完成：2006-06-13

### Phase 4: LLM 语义判定与证据门控知识图谱链路

**Goal:** 建立可审计的知识图谱构建链路：脚本负责召回、证据、规则、阈值和写库；LLM 只负责语义理解、关系候选和判断。该链路同时支持小说与历史语料。
**Requirements**: REQ-KG-01..06
**Depends on:** Phase 3
**Status:** VERIFIED on 2006-07-02
**Plans:** 4/4 plans complete

Plans:
- [x] 04-01 knowledge data contracts: PostgreSQL candidate/judgment/evidence/review contracts
- [x] 04-02 candidate packages and LLM judgment: deterministic recall package + structured LLM semantic judgment
- [x] 04-03 evidence gates and projection: schema/evidence/threshold/conflict gates + accepted graph projection
- [x] 04-04 evaluation and domain fixtures: fiction/history fixtures, graph eval CLI, cost/latency/faithfulness reporting

### Phase 5: Narrative Knowledge Unit Layer

**Goal:** 将 Phase 04 的 accepted judgments 蒸馏为可追溯、可版本化、可评测和可回滚的叙事知识单元检索层，同时保留原始 chunk 混合召回。
**Requirements**: REQ-NU-01..08
**Depends on:** Phase 4
**Status:** VERIFIED on 2026-07-12
**Plans:** 5/5 plans complete

Plans:
- [x] 05-01 narrative unit contracts and source snapshot
- [x] 05-02 canonicalization and lifecycle gates
- [x] 05-03 candidate index and hybrid retrieval
- [x] 05-04 frozen evaluation, canary, and promotion
- [x] 05-05 incremental refresh, reconcile, and rollback

### Phase 6: Automated Quality and CI Gates

**Goal:** 补齐后端、前端、数据库、向量库、API、浏览器和 live AI 的自动化验证，并以冻结证据、双模型仲裁和确定性规则建立无需日常人工逐题确认的 RAG 质量门禁。
**Requirements:** REQ-AUTO-01..11
**Depends on:** Phase 3, Phase 5
**Status:** COMPLETE (06-01..06-09; REQ-AUTO-11 closed)
**Plans:** 9 plans

Plans:
- [x] 06-01 test taxonomy and deterministic quality foundations
- [x] 06-02 PostgreSQL and Chroma integration matrix
- [x] 06-03 frozen fixtures, adversarial gates, and Judge calibration
- [x] 06-04 RAG scoring, durable worker, and Eval API compatibility
- [x] 06-05 API contracts, frontend components, and browser journeys
- [x] 06-06 unified CI producer DAG, security, artifacts, and nightly qualification
- [x] 06-07 ci-gate aggregation, branch protection, and release gate
- [x] 06-08 persistent QualityRun repository and lineage-bound identity chain
- [x] 06-09 persistent baseline prepare/commit and cross-chunker reports

### Phase 7: Semantic and Hierarchical Chunking

**Goal:** 在保留原始 chunk 证据底座的前提下，使用规则初切与 LLM 低置信边界判断建立可版本化、可评测、可回滚的 chapter → scene → evidence 层级切片，并由 Phase 06 自动质量门选择可发布 chunker。
**Requirements:** REQ-CHUNK-01..08
**Depends on:** Phase 06
**Status:** COMPLETE (07-01..07-06; REQ-CHUNK-01..08)
**Plans:** 6 plans

Plans:
- [x] 07-01 chunker manifests, source lineage, and deterministic baseline
- [x] 07-02 rule boundary confidence and candidate segmentation
- [x] 07-03 LLM low-confidence boundary adjudication and fallback
- [x] 07-04 hierarchical scene/evidence storage and retrieval
- [x] 07-05 candidate rebuild, incremental refresh, promotion, and rollback
- [x] 07-06 chunker A/B quality qualification and release verification

### Phase 8: Versioned novel analysis orchestration and interactive timeline

**Goal:** 以持久、版本化、证据约束的后台分析任务生成小说时间事件，并在全局分析工作台渐进展示防剧透的双顺序横向时间线。
**Requirements**: REQ-TIME-01..10
**Depends on:** Phase 7
**Status:** COMPLETE — 10/10 plans; independent verification passed 35/35 must-haves
**Plans:** 10/10 plans complete

Plans:
- [x] 08-01 durable analysis jobs and immutable version foundation
- [x] 08-02 strict timeline schema and evidence-bound chapter extraction
- [x] 08-03 cross-chapter reconciliation, overrides, budget, and promotion
- [x] 08-04 progressive owner-scoped API and spoiler boundary
- [x] 08-05 global analysis workspace and interactive ECharts timeline
- [x] 08-06 frozen qualification, end-to-end verification, and release gate
- [x] 08-07 production worker orchestration and PostgreSQL call boundaries
- [x] 08-08 real ordering/source isolation and production qualification
- [x] 08-09 final cancellation/cache/source-offset/qualification gap closure
- [x] 08-10 executable DB-and-command release authority gap closure

### Phase 9: Dynamic Character Relationship Graph

**Goal:** 基于已证据门控的人物关系事实和时间线版本，生成可按叙事进度演化、默认防剧透的小说人物关系图。
**Requirements:** REQ-REL-01..06
**Depends on:** Phase 04, Phase 08
**Status:** COMPLETE — 5/5 plans; independent verification passed 21/21 must-haves (2026-07-15)

Plans:
- [x] 09-01 append-only relationship observation contracts, evidence lineage, overrides, and migration
- [x] 09-02 deterministic relationship candidates, bounded evidence packages, LLM judgment, and gates
- [x] 09-03 owner/version/spoiler graph API, fold, overrides, and replayable Neo4j projection
- [x] 09-04 Cytoscape.js analysis workspace, evidence panel, timeline linking, and large-graph degradation
- [x] 09-05 frozen fixtures, adversarial/performance tests, browser qualification, and release gate

**Waves:** 1 → 2 → 3 → 4 → 5 complete. Verified: `.planning/phases/09-dynamic-character-relationship-graph/09-VERIFICATION.md`.

### Phase 10: Reader Text-Selection AI and Multi-Session Conversations

**Goal:** 让读者从阅读器选取原文后，在同一本小说的多个持久会话中进行证据受限、防剧透的 AI 对话。
**Requirements:** REQ-CHAT-01..07
**Depends on:** Phase 08, Phase 09
**Status:** PARTIAL — 5/5 plans; independent verification 19/20 must-haves (2026-07-15); real Playwright residual (Postgres 5432)

Plans:
- [x] 10-01 durable conversations, messages, selections, manifests, citations, jobs, calls, budgets, and migration
- [x] 10-02 owner-scoped multi-session lifecycle, ordered messages, idempotency, and API contracts
- [x] 10-03 server-verified Unicode selections, visible-context assembly, and Phase 09 read-only contract
- [x] 10-04 cited answer worker, audit lineage, dual budgets, cancellation, retry, and adversarial tests
- [x] 10-05 reader selection entry, collapsible multi-session window, citations, browser qualification, and release gate

**Waves:** 1 → {2, 3} → 4 → 5 complete. Verified: `.planning/phases/10-reader-selection-ai-and-multi-session-conversations/10-VERIFICATION.md` (PARTIAL). Phase 09 production reader wired; no clue product UI. Close residual with `reader-chat-real.spec.ts` when DB is up.

### Phase 11: Clue and Foreshadow Tracking

**Goal:** 以证据、版本和人工可控的五状态生命周期，发现、追踪、核验小说中的线索、伏笔及其回收。
**Requirements:** REQ-CLUE-01..07
**Depends on:** Phase 08, Phase 09, Phase 10
**Status:** IMPLEMENT COMPLETE — 5/5 plans; product must-haves verified; pure-module write scan residual closed (2026-07-15)

Plans:
- [x] 11-01 clue lifecycle contracts, PostgreSQL authority, append-only states, overrides, and migration
- [x] 11-02 cross-chapter candidate recall, bounded evidence packages, Phase 09 read-only protocol, and LLM gates
- [x] 11-03 durable clue worker, versioning, budgets, overrides, owner/spoiler API, and explicit unavailable-source behavior
- [x] 11-04 analysis workspace clue timeband, filters, evidence panel, payoff chain, and manual actions
- [x] 11-05 frozen fixtures, false-positive/spoiler/version adversarial tests, browser qualification, and release gate

**Waves:** 1 → 2 → 3 → 4 → 5 complete. Verified: `.planning/phases/11-clue-and-foreshadow-tracking/11-VERIFICATION.md`. Alembic head `11cluetrack01`. Clue UI on `/analysis` only (no top-level `/clues`). Qualification: `backend/scripts/run_clue_qualification.py`. Phase 10 chat is never a clue source; Phase 09 reader outages remain `source_unavailable`, not zero signals.

### Phase 12: Read-only Asset Audit and Eligibility

**Goal:** 在任何模型调用或上层写入前，以只读方式证明单本小说现有 hierarchy 与可选分析资产是否可被 v0.8 精确复用，并给出可机器处理的阻断原因和最小重建范围。
**Requirements:** V08-AUDIT-01, V08-AUDIT-02, V08-AUDIT-03, V08-AUDIT-04
**Depends on:** Phase 07, Phase 08, Phase 09, Phase 11
**Status:** COMPLETE — 3/3 plans; independent verification passed 4/4 requirements and 10/10 truths (2026-07-15)
**Plans:** 3/3 plans complete

**Success Criteria:**
1. 运维者对一部 owner-scoped 小说运行审计后，可获得按 hierarchy、timeline、relationship、clue 的版本化资产清单，以及每项唯一的 `reusable_exact`、`rebuild_required`、`blocked` 或 `optional_unavailable` 判定和 reason codes。
2. 审计会从 PostgreSQL 重算 active Phase 07 build 的 source snapshot、manifest、chapter→scene→evidence 父子关系、offset/content hash 和覆盖率；任一 required invariant 无效时，后续构建在 provider call 前被阻断。
3. timeline、relationship 或 clue 缺失、不可用或 lineage 不匹配时会作为可选来源状态显式报告，不会被解释为“没有事件/关系/线索”，也不会阻断仅依赖 hierarchy 的资格。
4. 自动化负向验证证明审计前后 provider call 数为零，现有数据未被修复，并且 chunk、timeline、clue 及其他生产 active pointer/revision 均未变化。

Plans:
- [x] 12-01 audit contracts and read-only source adapters: eligibility enums, reason codes, owner/novel/version inventory, optional-source status
- [x] 12-02 hierarchy and domain lineage verification: manifest/tree/offset/hash/coverage checks with provider-call-before blocking
- [x] 12-03 owner-scoped audit CLI/API and PostgreSQL negative tests: report, minimal rebuild scope, no model/data/pointer writes

### Phase 13: Candidate Memory Contracts and Provenance Authority

**Goal:** 建立与现有分析生命周期隔离的不可变叙事记忆候选契约，使每条 Chapter State、Story Arc/Volume 和 Global Story Model 主张均可由数据库权威追到同一 source snapshot 的叶子原文。
**Requirements:** V08-MEM-01, V08-MEM-02, V08-MEM-03, V08-MEM-04, V08-MEM-05
**Depends on:** Phase 12
**Status:** COMPLETE — verified 2026-07-16 (`13-VERIFICATION.md` status: passed)
**Plans:** 3/3 plans complete

**Success Criteria:**
1. 系统可创建独立于 timeline/relationship/clue 的 immutable `NarrativeMemoryVersion` candidate，并持久保存 owner、novel、version、source/hierarchy lineage、prompt/schema/model/config hashes；v0.8 不存在可达的 production promotion 路径。
2. Chapter State、连续 Story Arc/Volume 和 Global Story Model 使用 strict typed claims/state deltas；extra fields、summary-only facts、包外 source refs 或未知 authoritative 字段会 fail closed。
3. 对任一候选 claim，验证器可沿 node/edge/source links 下钻并从服务器 `Chapter.content[start:end]` 重切，证明 owner、novel、snapshot、offset 和 content hash 完全一致；断链或宽泛引用使候选不合格。
4. PostgreSQL integration tests 证明父子图无环、章节范围合法、manifest 可从排序后的 node/edge/link rows 重算，且 candidate 创建/验证不会创建或移动任何 active pointer。

Plans:
- [x] 13-01 narrative memory PostgreSQL authority: additive candidate version/node/claim/edge/source-link/manifest/report migration and composite scope constraints
- [x] 13-02 strict ChapterState/StoryArc/GlobalStoryModel schemas: typed claims, deltas, uncertainty, visibility and builder lineage
- [x] 13-03 provenance closure and manifest gates: DAG/range/source re-slice validation, append-only enforcement and no-pointer invariants

### Phase 14: Durable Bottom-up Candidate Builder

**Goal:** 对已通过 Phase 12 审计的单本小说，按 Chapter State → Story Arc/Volume → Global Story Model 自下而上构建可恢复候选，同时隔离章节失败并约束所有模型费用与可选来源。
**Requirements:** V08-BUILD-01, V08-BUILD-02, V08-BUILD-03, V08-BUILD-04, V08-BUILD-05
**Depends on:** Phase 13
**Status:** PLANNED — 4/4 plans created; Phase 13 verification passed; authorized under 13–18 scope; not started
**Plans:** 4/4 plans created

**Success Criteria:**
1. 运维者启动 candidate dry-run 后，durable worker 先逐章生成 evidence-bound Chapter State，再按显式卷界或版本化连续范围生成 Story Arc/Volume，最后只从已验证 arcs 生成单个 Global Story Model。
2. 每个模型调用在执行前完成预算预留，并绑定 frozen source/prompt/schema/model/config exact-cache key；任务支持 checkpoint、取消和恢复，未知价格或依赖失效时零 provider 调用并明确暂停。
3. 模拟单章失败时，已完成兄弟 Chapter States 保持 byte-identical，worker 仅阻断包含该章的 arc 与 Global；恢复后从失败 stage 继续而不是无条件重跑整本小说。
4. timeline、accepted relationship observations 和 clue lifecycle 仅在 lineage/evidence 合格时作为可选 enrichment；来源 unavailable 被显式记录，Reader Chat imports/text/citations 均不能进入事实 package。
5. 构建完成后数据库重算 candidate manifest 与 worker artifact 一致，报告列出完成/失败 stages、calls、tokens、cost、cache hits 和来源状态，且所有生产 pointers 保持不变。

Plans:
- [ ] 14-01 durable chapter-state worker: frozen packages, strict generation, budget, exact cache, checkpoint, cancel and resume
- [ ] 14-02 contiguous arc/volume planning and aggregation: explicit volume preference, deterministic coverage gates and evidence closure
- [ ] 14-03 global story aggregation and candidate manifest: validated-child-only claims, conflict/open-loop handling and DB recomputation
- [ ] 14-04 optional source adapters and failure isolation: timeline/relationship/clue enrichment, chat exclusion and partial-stage recovery tests

### Phase 15: Adaptive Hierarchical Retrieval and Leaf Evidence Safety

**Goal:** 在不替换现有 Reader Chat 的前提下，提供可审计、全程防剧透的离线分层检索实验，使 local/arc/global/mixed 查询能够下钻并最终只引用重验后的叶子原文。
**Requirements:** V08-RETR-01, V08-RETR-02, V08-RETR-03, V08-RETR-04, V08-RETR-05
**Depends on:** Phase 14
**Status:** PLANNED — 3/3 plans created; execution gated on Phase 14 verification and explicit authorization
**Plans:** 3/3 plans created

**Success Criteria:**
1. 冻结问题进入实验入口后，确定性 router 会记录 local、arc、global 或 mixed 起始层和 reason；不同层候选确实改变检索路径，而不是仅把上层摘要附加到 leaf top-k。
2. 检索可从 Global/Arc 向 Chapter State 下钻，并在上层缺失、partial 或误路由时使用 collapsed multi-level 或 raw/leaf fallback；返回结果包含 traversal path、各层候选、omitted counts 和 fallback reason。
3. 所有最终 citation 都由服务端在 frozen hierarchy build 中重新校验 chapter、Unicode code-point offsets 和 content hash；摘要、similarity、routing score 或聊天文本不能作为 citation。
4. owner、novel、candidate version 和 persisted reading cutoff 在候选选择、下钻、leaf expansion、rerank、cache 与最终 manifest 每一步生效；跨 scope 或 future evidence fail closed。
5. 对抗测试证明未读 arc 的标题、节点数量、分数、trace、cache key 和 source status 不泄露未来内容，且实验入口默认关闭、不改变现有 Reader Chat consumer output。

Plans:
- [ ] 15-01 deterministic query router and visible candidate sets: local/arc/global/mixed intent, cutoff-first SQL/metadata filtering
- [ ] 15-02 multi-level descent and leaf resolver: adaptive/collapsed candidates, raw fallback, server re-slice and frozen retrieval manifest
- [ ] 15-03 routing audit and adversarial safety: owner/version/tenant-cache IDOR, future-metadata leakage and reader-chat no-cutover tests

### Phase 16: Dependency-aware Local Rebuild and Carry-forward

**Goal:** 以可验证的依赖图和变更 oracle 计算 candidate 的最小安全 dirty closure，在边界不确定时保守扩散，并量化未变资产复用带来的调用与成本节省。
**Requirements:** V08-REUSE-01, V08-REUSE-02, V08-REUSE-03, V08-REUSE-04
**Depends on:** Phase 15
**Status:** PLANNED — 3/3 plans created; execution gated on Phase 15 verification and explicit authorization
**Plans:** 3/3 plans created

**Success Criteria:**
1. 对 chapter edit、insert、delete、reorder 和 arc-boundary change fixture，系统可输出由 source/evidence → Chapter State → Arc/Volume → Global 构成的 dirty closure 及每个节点的失效原因。
2. 未进入 dirty closure 的节点在新 candidate 中以 checksum-identical、lineage-valid 的方式 carry forward；验证证明它们不会产生 provider call、embedding 或重复索引写入。
3. 当 arc 边界、跨章状态延续或 dependency lineage 无法证明稳定时，planner 会将重建范围扩大到受影响 arc/后缀和 Global，而不会保留可能 stale 的父节点。
4. 重用报告同时给出 rebuilt/carried/stale counts、实际与避免的 calls/tokens/cost、dirty range 和 cache reuse；与 full rebuild upper bound 的口径可复核。

Plans:
- [ ] 16-01 dependency graph and change oracle: edit/insert/delete/reorder/boundary fixtures with deterministic dirty reasons
- [ ] 16-02 checksum carry-forward and conservative propagation: no-change byte identity, stale-ref rejection and stage-only rebuild
- [ ] 16-03 reuse economics report: avoided calls/tokens/cost, rebuild scope and PostgreSQL authority verification

### Phase 17: Frozen Single-book Qualification and Candidate Verdict

**Goal:** 使用冻结单书题集和同源 leaf baseline 独立验证结构、溯源、安全、检索质量、faithfulness、成本与复用收益，只产出 `qualified_candidate` 或 `blocked`，绝不执行 promotion。
**Requirements:** V08-QUAL-01, V08-QUAL-02, V08-QUAL-03, V08-QUAL-04, V08-QUAL-05
**Depends on:** Phase 12, Phase 13, Phase 14, Phase 15, Phase 16
**Status:** PLANNED — 3/3 plans created; execution gated on Phases 12–16 verification and explicit authorization
**Plans:** 3/3 plans created

**Success Criteria:**
1. 资格运行使用在查看候选结果前冻结的单书 source、policy 和问题集，且题目明确覆盖 local、跨章节/arc、whole-book/global、no-answer 和 spoiler 分桶。
2. hierarchical candidate 与 leaf/raw baseline 使用同一 source snapshot、reading cutoff、问题和预算口径；报告逐桶给出 leaf evidence recall/ranking、routing hit/fallback、answer faithfulness/relevance、p50/p95、calls/tokens/cost 和 reuse 指标。
3. fresh PostgreSQL verifier 独立重算结构、manifest、claim→leaf lineage、owner/snapshot scope、spoiler visibility 和所有 pointer before/after；任一断链、越界、泄漏、空必需指标或 pointer 变化都得到 `blocked`。
4. fixed-command qualification 只能返回 `qualified_candidate` 或 `blocked`，并保存 policy/fixture/source/prompt/schema/model/config hashes 与命令输出 digest；没有 endpoint、CLI 或 worker 路径会执行 promotion。
5. 最终报告明确说明这是单书 candidate 结论，不会替换 timeline、relationship、clue 或 Reader Chat，也不会宣称关闭 v0.3 的 100 confirmed、faithfulness/cost 全项目缺口。

Plans:
- [ ] 17-01 frozen single-book fixture and policy: bucketed questions, no-answer/spoiler adversarial cases, same-source baseline and predeclared thresholds
- [ ] 17-02 comparative evaluation and complete metrics: retrieval, routing, faithfulness, latency, cost, reuse and fallback reports
- [ ] 17-03 independent PostgreSQL qualification authority: fixed commands, fresh observer, pointer-diff proof and candidate-only verdict

### Phase 18: Frontend Motion and Transition System

**Goal:** 在不改变业务行为、API 或数据结构的前提下，为现有 Next.js 界面建立克制、统一、可访问的动画过渡系统，并消除主题首帧闪烁、浮层退出不一致和动态内容布局跳动。
**Requirements:** UI-MOTION-01, UI-MOTION-02, UI-MOTION-03, UI-MOTION-04, UI-MOTION-05, UI-MOTION-06
**Depends on:** Existing frontend foundation (Phases 08–11); independent from the Phase 14–17 RAG execution chain
**Status:** EXECUTING — 18-01/02 complete; authorized 2026-07-16 orchestration
**Plans:** 2/3 plans complete

**Success Criteria:**
1. 所有目标交互使用 150/200/300ms 语义 token，进入为 ease-out、退出为 ease-in；新增代码无任意时长、linear 或 `transition-all`。
2. sidebar、dialog、阅读设置、搜索、Reader Chat 与证据面板支持一致的触发切换、outside click、Escape、顶层关闭和焦点返回。
3. light/dark/custom 主题在首个可见帧前恢复；切换期间无整页闪烁、正文尺寸变化、固定控件漂移或自定义背景位移动画。
4. `prefers-reduced-motion` 下所有业务状态立即可用，非必要位移/缩放/脉冲被移除，loading/progress 同时提供文本或 ARIA 状态。
5. 桌面与 390px 触摸视口验证关键面板、主题、分析增量和布局边界；无水平滚动、输入框遮挡、底部进度覆盖聊天或焦点丢失。

Plans:
- [x] 18-01 motion tokens, reduced-motion contract, pre-paint theme bootstrap and shared primitives
- [x] 18-02 dismissable sidebar/settings/search/chat/evidence panels with topmost outside-click and focus restoration
- [ ] 18-03 analysis progress/list/card transitions plus desktop/mobile Playwright motion qualification

**Scope note:** Phase 18 是独立的前端体验阶段，不属于 v0.8 candidate RAG 的生产切换，不新增后端、API、动画运行时依赖、滚动劫持或持续装饰动画。
