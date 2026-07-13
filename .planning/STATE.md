---
gsd_state_version: 1.0
milestone: v0.6
milestone_name: Versioned Novel Analysis and Timeline
status: in_progress
last_updated: "2026-07-13T05:16:30.733Z"
progress:
  total_phases: 7
  completed_phases: 4
  total_plans: 32
  completed_plans: 35
  percent: 57
---

# Project State

## Project Reference

See `.planning/PROJECT.md` and `IMPLEMENTATION-STATUS.md`.  
系统结构以 `docs/architecture/` 为准。

**Core value:** 先建立可信、安全、可迁移的实现基线，再扩展 RAG。

## Current Position

Phase: 08 (Versioned novel analysis orchestration and interactive timeline) — GAP CLOSURE IN PROGRESS
Plan: 7 of 8
**Phase 08 gap plan 08-07 COMPLETE** — 08-08 remains pending and was not executed

- Branch: `feat/phase2-wave2-embedding`
- Last activity: 2026-07-13 — 08-07 production worker orchestration and PostgreSQL model-call boundaries completed (63 timeline tests passed)
- Plan directory: `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/`

## Auto Routing

下一动作为 08-08 gap plan；本次执行明确停止于 08-07，不自动启动 08-08。`auto_start` 保持关闭。

## Phase 08 Execution Metrics

- 08-02: 18min, 3 tasks, 11 files, 23 targeted tests passed.
- 08-03: 9min, 3 tasks, 7 files, 9 targeted tests passed including real PostgreSQL lifecycle coverage.
- 08-04: 11min, 3 tasks, 7 files, backend 5 tests and frontend 12 contract tests passed.
- 08-05: 11min, 3 tasks, 7 files, frontend 66 unit tests plus desktop/mobile Playwright passed.
- 08-06: 9min, 3 tasks, 6 files, backend 56 + controlled live 7 + CI 5 + frontend 66 tests and build passed.
- 08-07: 26min, 3 tasks, 9 files, 63 timeline tests passed including real PostgreSQL concurrency coverage.

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

1. 08-08 仍待单独执行；本次不进入该计划。
2. 08-08 完成后再由独立 verifier 复验 Phase 08 gaps。
3. 保留本地 BGE / 阅读器 UX 等非 Phase 08 WIP，不纳入 Phase 08 提交。
