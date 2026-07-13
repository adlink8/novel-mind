---
gsd_state_version: 1.0
milestone: v0.6
milestone_name: Versioned Novel Analysis and Timeline
status: planned
last_updated: "2026-07-13T03:17:42.961Z"
progress:
  total_phases: 7
  completed_phases: 4
  total_plans: 30
  completed_plans: 29
  percent: 57
---

# Project State

## Project Reference

See `.planning/PROJECT.md` and `IMPLEMENTATION-STATUS.md`.  
系统结构以 `docs/architecture/` 为准。

**Core value:** 先建立可信、安全、可迁移的实现基线，再扩展 RAG。

## Current Position

Phase: 08 (Versioned novel analysis orchestration and interactive timeline) — EXECUTING
Plan: 2 of 6
**Phase 08 EXECUTING** (01/06) - ready to execute 08-02

- Branch: `feat/phase2-wave2-embedding`
- Last activity: 2026-07-13 — Phase 08 SPEC/CONTEXT/RESEARCH/AI-SPEC/VALIDATION and six plans passed independent review
- Plan directory: `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/`

## Auto Routing

下一动作为执行 Phase 08 Plan 01。`auto_start` 保持关闭，必须显式启动执行。

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

1. （可选）将 Phase 07 hierarchy/build 接到 PostgreSQL + 生产索引/检索路径  
2. （可选）提交并 push 本地 BGE / UX WIP  
3. 或开启下一里程碑（无 active plan）
