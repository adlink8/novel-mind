---
phase: 14-durable-bottom-up-candidate-builder
source: approved-v0.8-requirements
requirements: [V08-BUILD-01, V08-BUILD-02, V08-BUILD-03, V08-BUILD-04, V08-BUILD-05]
planning_mode: non-interactive
execution_authorized: false
---

# Phase 14 Context

## Outcome

对一个已通过 Phase 12 资格审计、且已由 Phase 13 创建显式 candidate version 的小说，建立可持久恢复的自底向上构建器：先逐章构建 Chapter State，再按显式卷界或冻结的连续范围构建 Story Arc/Volume，最后只从已验证的中层节点构建唯一 Global Story Model。整个过程只产生 candidate authority、执行审计与报告，不创建、解析或移动任何生产 active pointer。

## Locked Decisions

- 执行前置条件是 Phase 13 全部计划完成并独立验证通过；当前 Phase 13 暂停状态不得被本规划自动推进。
- Phase 12 `EligibilityReport.provider_calls_allowed` 是唯一 pre-provider 总门禁。其 hierarchy 不是 `reusable_exact`、报告 lineage 不匹配、价格未知或依赖失效时，必须在零 provider 调用下暂停。
- 顺序固定为 Chapter State → Story Arc/Volume → Global Story Model。父层 package 只能引用已持久化且重新验证的直接子层内容与 Phase 07 leaf evidence，不能绕过层级。
- PostgreSQL 保存 run、stage/checkpoint、调用尝试、预算预留/结算与报告；candidate version/node/claim/edge/source-link/manifest/report 继续由 Phase 13 authority 持有。
- 每次模型调用前先持久化预算预留，并使用 frozen source snapshot、hierarchy、evidence/package、prompt、schema、model revision、decoding、config 与 policy 构造 exact-cache key。仅严格 schema 与业务验证都通过的输出可作为 cache source。
- checkpoint 是 stage 级且幂等；取消在每次调用前、调用后和持久化前轮询。恢复从第一个未完成/失败 stage 继续，已完成的兄弟产物必须 byte-identical，不允许整书无条件重跑。
- 章节失败只阻断包含该章的 arc/volume 和 Global。其他 Chapter State 与不相交 arc 可继续；失败原因、阻断闭包和恢复点必须明确持久化。
- 卷界优先使用显式、冻结、可验证的卷元数据；不存在或非法时使用版本化、连续、全覆盖且非重叠的确定性 arc 范围。不得由模型隐式决定或静默改变边界。
- timeline、accepted relationship observations 与 clue lifecycle 仅作为可选 enrichment。每条引用都要验证 owner/novel/version/source/hierarchy lineage，并仍然落到同一 Phase 07 leaf evidence；`unavailable` 与健康空集必须区分。
- Reader Chat 的表、service、text、message、citation、similarity 或生成结果禁止进入输入 package、claim、source link、cache key 或报告事实。
- Global 只能从所有必要且已验证的 arc/volume 构建；冲突和 open loop 使用 Phase 13 严格 typed claims 表达，不得用自由文本补齐事实。
- 构建结束后由数据库行重算 Phase 13 candidate manifest；worker artifact 只能比对，不能自证。成功或失败均不得产生 narrative-memory pointer、promotion、rollback 或 consumer cutover。

## Scope

### In scope

- Additive Phase 14 worker control-plane migration and ORM: durable run, stage/checkpoint, model call attempt, budget ledger/reservation and append-only run report.
- Strict chapter/arc/global generation packages, fixed deployments, one bounded schema repair, exact-cache recovery and auditable provider outcomes.
- Deterministic volume/arc range planner, child validation, failure isolation, cancel/resume and stage-level idempotency.
- Optional timeline/relationship/clue adapters with explicit source status and frozen lineage.
- Final database-derived manifest/seal/structural validation integration and complete cost/call/cache/source-status report.
- PostgreSQL concurrency, crash recovery, adversarial source, no-chat and fresh-observer no-pointer tests.

### Out of scope

- Completing or modifying unfinished Phase 13 implementation as part of Phase 14 planning.
- Production promotion, active pointer, rollback, default/current-version resolver or existing consumer cutover.
- Hierarchical query routing, descent and citations (Phase 15), dirty closure/carry-forward (Phase 16), or quality qualification (Phase 17).
- Product UI, Reader Chat integration, automatic full-library analysis, GraphRAG/RAPTOR/Neo4j/LangChain, new provider or production dependency.
- Reanalysis of existing books during planning or verification; execution begins with controlled fixtures/single-book dry-run only after separate authorization.

## Verification Standard

- Migration is additive from the independently verified Phase 13 head, leaves one Alembic head, round-trips cleanly, and creates no pointer/promotion surface.
- Real PostgreSQL tests demonstrate lease recovery, atomic budget reservation/settlement, exact-cache reuse, checkpoint idempotency, cancellation, crash resume and same-stage concurrency.
- Controlled provider transport proves strict Chapter → Arc/Volume → Global order, zero-call fail-closed paths, bounded repair and no fallback deployment.
- Failure fixtures prove a chapter failure preserves completed siblings byte-for-byte, blocks only dependent parents, and resumes from the failed stage.
- Optional-source tests distinguish non-empty, healthy-empty, unavailable and stale lineage; all final claims retain direct Phase 07 leaf closure.
- Fresh observer compares candidate authority, all existing production pointers/revisions/journals and provider-call audit before/after; worker artifact must equal the database-recomputed manifest while pointers remain byte-equivalent.

## Execution Gate

These files are planning artifacts only. Do not execute Phase 14 until the user separately authorizes implementation and Phase 13 is complete, verified, and free of unreviewed WIP.

