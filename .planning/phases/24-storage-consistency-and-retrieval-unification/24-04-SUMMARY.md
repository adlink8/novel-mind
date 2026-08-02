# Phase 24-04 Summary — Reader Chat priority and projection safety

**Date:** 2026-07-27
**Status:** complete for plan scope; Phase 24 is now complete after local 24-01/02 verification.

## Delivered

- Added `backend/app/services/retrieval_policy.py` as the shared retrieval contract.
- Kept production `chunks` and `units` enabled and `narrative_memory` disabled.
- Rewired `knowledge_units.search` and `reader_chat.retrieval` to consume the shared policy.
- Preserved the public compatibility names `RETRIEVAL_LAYERS` and `SOURCE_PRIORITY`.
- Added deterministic contract tests for layer status, source ordering, and unknown source rejection.

## Verification

- `pytest tests/test_retrieval_policy.py tests/test_search_router_fallback.py tests/unit/reader_chat -q` — **116 passed**.
- `pytest tests/integration/relationships/test_projection.py -q` — **3 passed**.
- Existing projection tests prove accepted-only manifests and no PostgreSQL fact mutation on adapter failure.

## Boundary

- No Narrative Memory production routing, promotion, active pointer, or Reader Chat cutover.
- PR #23 remains a separate branch/worktree; the current branch uses a local equivalent implementation and no remote merge/push was performed.
