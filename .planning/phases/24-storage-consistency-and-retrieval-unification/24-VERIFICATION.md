---
phase: 24-storage-consistency-and-retrieval-unification
status: complete
verified: 2026-07-27
---

# Phase 24 Verification — Storage consistency and retrieval unification

| Plan | Result | Evidence |
|---|---|---|
| 24-01 journal/idempotency/fail-closed | PASS | `tests/test_indexing.py` + `tests/test_indexing_journal.py`: 29 passed |
| 24-02 reconcile/manifest gate | PASS | `tests/test_indexing_reconcile.py`: 2 passed |
| 24-03 retrieval fallback contract | PASS | Existing search fallback contract suite remains green |
| 24-04 shared retrieval policy/projection boundary | PASS | Shared policy, reader-priority and projection evidence; NM remains disabled/candidate-only |
| Migration chain | PASS | `tests/integration/test_postgres_migrations.py`: 6 passed; single head `24idxjournal1` |
| Combined affected backend suite | PASS | Hybrid/search/indexing/journal/reconcile suite: 92 passed |

## Delivered

- Added `ChunkIndexJournal` and migration `24idxjournal1`.
- Added deterministic source/manifest checksums and idempotent indexing checks.
- Changed destructive ordering to delete Chroma before DB chunks; failures are fail-closed and auditable.
- Partial embedding results persist `Novel.status=partial` and surface `index_status` to hybrid-search consumers.
- Added read-only reconcile reporting and explicit repair CLI at `backend/scripts/run_index_reconcile.py`.
- Kept Narrative Memory promotion, active-pointer mutation, and Reader Chat cutover disabled.

## Boundary

PR #23 remains a separate remote branch and was not merged, cherry-picked, or pushed. The local implementation was verified independently and does not authorize any Narrative Memory or Reader Chat cutover.
