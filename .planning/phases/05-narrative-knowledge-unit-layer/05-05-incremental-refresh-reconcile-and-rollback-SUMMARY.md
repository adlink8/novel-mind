---
phase: 05-narrative-knowledge-unit-layer
plan: 05-05-incremental-refresh-reconcile-and-rollback
subsystem: narrative-unit-lifecycle
tags: [incremental, watermark, reconcile, rollback, lifecycle]
key-files:
  - backend/app/services/knowledge_units/incremental.py
  - backend/app/services/knowledge_units/reconcile.py
  - backend/app/services/knowledge_units/rollback.py
  - backend/migrations/versions/e5b8c20d4a73_add_narrative_refresh_watermarks.py
metrics:
  phase_tests: 58
  full_non_e2e_tests: 331
  full_non_e2e_deselected: 12
  migration_heads: 1
  lifecycle_residue: 0
status: complete
completed: 2026-07-11
---

# Phase 05 Plan 05 Summary

Closed the narrative knowledge lifecycle with content-hash deltas, affected-subject rebuilds, immutable fresh candidates, deleted/deprecated propagation, exact actual-ID reconcile, prepare/commit rollback/restore, and final-only source watermark advancement.

## Commits

| Commit | Description |
|---|---|
| `4bdd1c6` | Add refresh/watermark contracts, delta, reconcile, rollback, CLI, and tests |
| `7f302cc` | Align legacy vector-store tests with the required lazy client contract |
| `548e943` | Fix exact duplicate canonical representative publication |
| `154c8b1` | Add canonical snapshot to candidate build preparation |
| `1727ac2` | Rebuild affected subjects while carrying unaffected units forward |
| `22b79d2` | Propagate deleted lifecycle explicitly |

## Verification

- Phase 05 unit suite: 58 passed.
- Phase 03-05 targeted integration suite: 118 passed before final hardening.
- Full backend `pytest tests -m "not e2e" -q`: 331 passed, 12 e2e deselected.
- Ruff and `compileall`: passed.
- Offline Alembic full-chain SQL: passed; one head `e5b8c20d4a73`.
- Fiction/history frozen dry-runs: passed.
- No-change refresh: LLM/canonical/Chroma/pointer/watermark writes all zero.
- Rollback -> reconcile -> restore behavior: passed in deterministic database tests.

## Deviations

- Added `NarrativeSourceWatermark` and `NarrativeRefreshRun` contracts because the planned final-only watermark and resumable refresh could not be represented safely in existing tables.
- Live PostgreSQL upgrade/current/check is blocked by connection refusal on `127.0.0.1:5432`.
- Live Chroma indexing/canary is blocked by the unhealthy local Chroma service; fake-store exact-ID tests and blocked behavior are verified.
- Independent GSD verifier remains required before phase completion because the executor subagent quota was exhausted.

## Self-Check: PASSED

- Deltas use content hashes, not timestamps alone.
- Added/changed judgments are rebuilt; removed/changed old units are excluded from candidates.
- Unaffected canonical units are carried into the fresh candidate without rewrite.
- Watermark advances only after active pointer and clean reconcile agree.
- Deleted/deprecated actual IDs make reconcile fail.
