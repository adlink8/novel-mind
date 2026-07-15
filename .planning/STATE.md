---
gsd_state_version: 1.0
milestone: v0.7
milestone_name: Narrative Relationships, Reader AI, and Clue Tracking
status: executing
last_updated: "2026-07-15T09:15:00.000Z"
progress:
  total_phases: 11
  completed_phases: 9
  total_plans: 56
  completed_plans: 46
  percent: 82
---

# Project State

## Project Reference

See `.planning/PROJECT.md` and `IMPLEMENTATION-STATUS.md`.  
系统结构以 `docs/architecture/` 为准。

**Core value:** 先建立可信、安全、可迁移的实现基线，再扩展 RAG。

## Current Position

Phase: 09 (Dynamic Character Relationship Graph) — VERIFIED (5/5 plans; 21/21 must-haves)
Plan: 09-05 complete; 09-VERIFICATION passed
**Phase 10-11 authorized for GSD execute** — Phase 09 independent verification complete 2026-07-15

- Branch: `feat/phase2-wave2-embedding`
- Last activity: 2026-07-15 — Phase 09 verification report (21/21 must-haves; backend 60 + frontend relationships 17 re-run)
- Plan directories: `.planning/phases/09-dynamic-character-relationship-graph/`, `.planning/phases/10-reader-selection-ai-and-multi-session-conversations/`, `.planning/phases/11-clue-and-foreshadow-tracking/`

## Auto Routing

Phase 09 已独立验证通过；下一步进入 Phase 10（依赖 `load_filtered_relationship_graph` only）。

## Phase 09 Execution Metrics

- 09-01: 28min, 3 tasks, 6 files, 13 targeted persistence tests passed (0 skip); alembic head `11relobserve01`.
- 09-02: 45min, 3 tasks, 10 files, 17 targeted pipeline tests passed (13 unit + 4 PostgreSQL integration, 0 skip).
- 09-03: 55min, 3 tasks, 8 files, 10 targeted graph API/projection PostgreSQL tests passed (0 skip); OpenAPI graph path registered.
- 09-04: 45min, 3 tasks, 13 files, frontend 85 Vitest + lint (0 errors) + Next production build passed; cytoscape@3.34.0 pinned.
- 09-05: 95min, 3 tasks, 9 files, backend 60 + frontend 85 + Playwright 4 + release qualified; scope_clean true.

## Phase 09 Decisions

- Phase 09 edge types are only ally/enemy/family/mentor/romantic; causes/precedes/same_entity are not graph edges.
- Accepted observations and protective overrides are physically append-only via PostgreSQL triggers; supersession is always INSERT.
- Legacy character_relations is left untouched and never used as Phase 09 truth.
- AUTO_ACCEPT_THRESHOLD = 0.85; REVIEW_THRESHOLD = 0.65; policy_hash freezes gate order and thresholds.
- same_entity/causes/precedes never produce RelationshipObservation; same_entity is identity-review metadata only.
- RelationshipObservationWorker is the sole accepted-observation writer; LLM cannot choose owner/version/status.
- Graph cutoff reuses only Phase 08 timeline_full_book; missing progress defaults to chapter one.
- Latest-wins overrides use highest id per logical key/field without mutating prior override rows.
- Hard cap responses empty nodes/edges with filters_required while preserving spoiler-safe counts.
- Phase 10/11 get documented read-only service functions only; no chat/clue tables or routes in Phase 09.
- Cytoscape.js is the relationship renderer (exact 3.34.0); ECharts timeline remains; workspaces share version/full-book/through_chapter only.
- Graph client filter params match OpenAPI singular character_id/relation_type; filters_required never mounts Cytoscape.
- Phase 09 release uses independent PostgreSQL observations + internally executed command digests; verdict is passed or blocked_release.
- Character-filtered graph queries prefilter endpoints in SQL so 10k observation seeds meet p95<=300ms.

## Phase 08 Execution Metrics

- 08-02: 18min, 3 tasks, 11 files, 23 targeted tests passed.
- 08-03: 9min, 3 tasks, 7 files, 9 targeted tests passed including real PostgreSQL lifecycle coverage.
- 08-04: 11min, 3 tasks, 7 files, backend 5 tests and frontend 12 contract tests passed.
- 08-05: 11min, 3 tasks, 7 files, frontend 66 unit tests plus desktop/mobile Playwright passed.
- 08-06: 9min, 3 tasks, 6 files, backend 56 + controlled live 7 + CI 5 + frontend 66 tests and build passed.
- 08-07: 26min, 3 tasks, 9 files, 63 timeline tests passed including real PostgreSQL concurrency coverage.
- 08-08: 24min, 3 tasks, 9 files, backend 65 + frontend 68 + real desktop/mobile browser 2 + release gate 7 tests passed.
- 08-09: 16min, 3 tasks, 10 files, backend 74 + frontend 68 + release gate 8 tests and production build passed.
- 08-10: 7min, 2 tasks, 4 files, PostgreSQL qualification 5 + release/workflow/CI-gate 47 tests passed.

## Phase 08 Decisions

- Timeline extraction keeps narrative position separate from four strict story-time precision shapes.
- Timeline model calls allow one same-deployment repair with an independent budget reservation and no fallback.
- Only evidence-valid complete output is cached and provisionally published.
- Contradictory chronology remains explicitly unranked instead of receiving fabricated story order.
- Promotion and rollback require recomputed graph manifests and row-locked expected-revision CAS.
- Missing reading progress exposes only the first chapter; full-book access requires an explicit persisted per-novel preference.
- Active and running candidate responses keep independent progress, events, counts, aggregates, previews, and edges.
- Canvas and keyboard companion list expose the same visible event set; active and candidate views remain source-isolated.
- Full-book disclosure requires confirmation before persisting the per-novel preference.
- Blocked or unavailable live timeline dependencies produce `metrics=null` and cannot satisfy the release gate.
- Phase 08 qualification is fiction-only and proves deferred relationship graph, reader AI, clue tracking, and history products are absent.
- Production timeline work is driven by durable background workers over the active Phase 07 hierarchy and resumes from completed chapter/stage checkpoints.
- Gateway budget reservations, call attempts, outcomes, and exact cache recovery are PostgreSQL-backed and auditable across process restarts.
- Production extraction and reconciliation use fixed no-fallback deployments; unsupported capability, unknown pricing, or budget rejection pauses before a provider call.
- Narrative timeline projection orders by chapter, source offset/index, then event ID; participant controls derive only from the selected version.
- Phase 08 release qualification requires signed PostgreSQL production-worker artifacts and measured visible-query output.
- Timeline browser E2E uses real Next.js, FastAPI, PostgreSQL, and timeline APIs with only the provider transport controlled.
- Running workers poll durable cancellation between every production stage and stop before later calls or promotion.
- Reconciliation exact-cache identity binds version lineage plus hashes of the actual prompt and output schema.
- Timeline `source_start` is required end-to-end and derives from persisted evidence offsets.
- Release qualification requires independent DB authority observations and successful command-output digests; self-hashes prove integrity only.
- The executable release CLI owns fixed argv/cwd commands and the PostgreSQL session factory; command digests are recomputed from internally captured combined output bytes.
- Release verdicts expose command, exit code, and digest without exposing captured test or service output.

## Phase 06 (v0.5) — COMPLETE

REQ-AUTO-01..11 已交付（含 06-08 QualityRun 持久化、06-09 BaselineCandidate prepare/commit 与跨 chunker 报告）。

关键产物：

- `backend/app/models/eval.py` — `QualityRun` / `BaselineCandidate` / `ActiveBaseline`
- Alembic: `07qualityruns01`, `08baselinecand01`
- API: `/api/eval/quality/runs*`, `/api/eval/quality/baseline/*`, `/api/eval/quality/reports/cross-chunker`

## Phase 07 — COMPLETE (logic + tests; PG wiring residual)

| Plan | 交付 |
|---|---|
| 07-01 | baseline manifest、unicode offsets、`chunking/{schemas,manifests,baseline}` |
| 07-02 | AtomicSpan、BoundaryProposal、CandidateSegmentation |
| 07-03 | BoundaryAdjudicator、BudgetLedger、adversarial fallback |
| 07-04 | HierarchyTree chapter→scene→evidence、scene expand / raw fallback |
| 07-05 | InMemoryBuildStore 不可变构建、增量、reconcile、promote、rollback |
| 07-06 | 同 snapshot A/B、`QualifiedChunkerEvidence`、release verifier、CLI |

包路径：`backend/app/services/chunking/`  
CLI：`backend/scripts/run_chunker_qualification.py`  
测试：`tests/unit|integration/chunking` + adversarial + legacy `test_chunking` → **88 passed**

### PG / indexing wiring (done)

- Alembic `09chunkhier01`: `chunk_builds`, `chunk_active_pointers`, `chunk_hierarchy_nodes`, `text_chunks.hierarchy_*`
- `pg_store.create_and_persist_hierarchy_build` after raw index
- `hybrid_search` scene expand with raw fallback
- Tests: `test_pg_hierarchy_wiring.py` + full chunking suite

### Residuals

- 分支上仍有本地 BGE / 阅读器 UX 等非 Phase 07 WIP 未提交。

## Verified In v0.3 (historical snapshot)

### 混合搜索后端

- BM25 全文搜索（PostgreSQL tsvector）
- 向量语义搜索（ChromaDB；默认 embedding 已改为本地 BGE 路径，见运行配置）
- 加权融合排序
- 全局 / 小说内搜索 API

### 前端搜索 UI

- 全局搜索栏、结果页、阅读页内搜索

### RAG 评测基础设施

- EvalDataset / EvalRun / EvalResult + quality durable jobs
- Phase 06 自动质量门与 baseline 晋升

## Next Action

1. Start Phase 10 (Reader Text-Selection AI and Multi-Session Conversations); depend on `load_filtered_relationship_graph` only.
2. Phase 11 may depend on `list_accepted_observation_refs`; never treat chat as fact source.
3. 保留当前所有非 `.planning` 的本地 WIP，不纳入本次规划提交。
4. Optional residual: re-run Playwright `relationships-real.spec.ts` and full frontend suite/build in CI or full-stack host if desired (not blocking).

## Session

- Stopped at: Completed 09-VERIFICATION.md (passed)
- Resume file: None
