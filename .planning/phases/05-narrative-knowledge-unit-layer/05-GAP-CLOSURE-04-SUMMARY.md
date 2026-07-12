---
phase: 05-narrative-knowledge-unit-layer
plan: GAP-CLOSURE-04
subsystem: api
tags: [fastapi, retrieval, parity, authorization, evaluation]
requires:
  - phase: 05-narrative-knowledge-unit-layer
    provides: NarrativeRetrievalStrategy, active and candidate build selectors, frozen evaluation
provides:
  - Production global and novel APIs routed through NarrativeRetrievalStrategy
  - API/evaluator parity coverage for fusion, fallback, citations, owner and lifecycle boundaries
  - Preserved novel-search HTTP 401/403 behavior
affects: [phase-05-verification, narrative-search, frozen-evaluation]
tech-stack:
  added: []
  patterns: [FastAPI dependency-injected strategy, shared production/evaluation policy]
key-files:
  created: [.planning/phases/05-narrative-knowledge-unit-layer/05-GAP-CLOSURE-04-SUMMARY.md]
  modified: [backend/app/api/search.py, backend/app/services/knowledge_units/search.py, backend/tests/test_knowledge_unit_search.py, .planning/STATE.md]
key-decisions:
  - "Production API and candidate evaluation share NarrativeRetrievalStrategy; evaluation differs only by injecting select_candidate_build."
  - "HTTPException is re-raised before broad search failure handling so authorization semantics remain unchanged."
patterns-established:
  - "Search endpoints delegate chunks/units/hybrid routing and fusion to one strategy boundary."
requirements-completed: [REQ-NU-06]
duration: 7min
completed: 2026-07-12
---

# Phase 05 Gap Closure 04: Shared Retrieval Strategy Summary

**Production search now invokes the same NarrativeRetrievalStrategy as frozen candidate evaluation while preserving 401/403 authorization behavior.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-07-12T03:26:00Z
- **Completed:** 2026-07-12T03:33:00Z
- **Tasks:** 1
- **Files modified:** 3 implementation/test files

## Accomplishments

- Removed API-owned chunks/units/hybrid routing and fusion from both global and novel search.
- Added global strategy support and injected the production strategy through FastAPI dependencies.
- Proved API/evaluator parity for active versus candidate selection, owner/lifecycle boundaries, ranking, fallback and citations.
- Preserved existing-novel unauthenticated 401 and cross-owner 403 responses.

## Task Commits

1. **Shared production retrieval and parity regression coverage** - `0c1391b` (fix)

## Files Created/Modified

- `backend/app/api/search.py` - Delegates global and novel retrieval to the shared strategy and re-raises HTTP exceptions.
- `backend/app/services/knowledge_units/search.py` - Adds owner-scoped global retrieval to the shared strategy.
- `backend/tests/test_knowledge_unit_search.py` - Covers shared boundary parity, auth semantics, fusion fallback and citations.

## Decisions Made

- Candidate evaluation continues through the same strategy and changes only the build selector; it does not move the active pointer.
- FastAPI constructs the production strategy lazily, avoiding hybrid-service import cycles during application startup.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Preserved HTTPException status responses**
- **Found during:** Review of inherited gap04 changes
- **Issue:** The new unauthenticated-units 401 was inside `except Exception` and became a 200 empty response.
- **Fix:** Re-raise `HTTPException` before generic search failure handling and add real 401/403 API regressions.
- **Files modified:** `backend/app/api/search.py`, `backend/tests/test_knowledge_unit_search.py`
- **Verification:** Targeted API/shared-strategy suite and full backend non-e2e suite pass.
- **Committed in:** `0c1391b`

**Total deviations:** 1 auto-fixed (1 Rule 1 bug)
**Impact on plan:** Required to preserve the pre-existing authorization contract while closing the strategy-wiring gap.

## Verification Evidence

- API/shared strategy + hybrid: `34 passed in 7.65s`
- Phase 05 + hybrid: `116 passed in 61.82s`
- Backend non-e2e: `366 passed, 12 deselected in 113.98s`
- Ruff: `All checks passed!`
- `compileall -q app scripts tests`: exit 0
- FastAPI import and OpenAPI generation: PASS
- API duplicate routing scan: no direct hybrid/unit services, fusion calls, or mode branches remain
- Evidence/resume/reconcile/rollback regressions: included in the 116-test Phase 05 suite

## Known Stubs

None.

## Issues Encountered

- PowerShell did not expand the pytest wildcard; reran with an explicit file list.
- The first anonymous 401 regression retained the login cookie and correctly hit CSRF protection; the test now clears both bearer header and cookie to exercise the intended anonymous path.

## User Setup Required

None.

## Next Phase Readiness

- REQ-NU-06/D-07 shared strategy wiring is closed and ready for independent re-verification.
- No known blocker remains in gap04 scope.

## Self-Check: PASSED

- Summary file exists.
- Business/test commit `0c1391b` exists.
- All implementation and verification claims above match executed commands.

---
*Phase: 05-narrative-knowledge-unit-layer*
*Completed: 2026-07-12*
