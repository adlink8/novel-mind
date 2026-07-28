---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 03
subsystem: timeline-lifecycle
tags: [postgresql, reconciliation, causal-graph, overrides, cas, rollback, budget]
requires:
  - phase: 08-02
    provides: strict evidence-bound chapter candidates and timeline model gateway contracts
  - phase: 08-01
    provides: immutable timeline persistence, active pointers, and budget reservations
provides:
  - Quality-tier cross-chapter deduplication, chronology constraints, and evidence-backed causal edges
  - Append-only field overlays with stable-evidence relink and explicit needs_relink state
  - PostgreSQL manifest validation, stale-CAS promotion, journaled rollback, and pointer integrity
affects: [08-04, 08-05, timeline-api, timeline-worker]
tech-stack:
  added: []
  patterns: [pre-call budget reservation, deterministic topology conflict retention, immutable machine rows plus overlays, row-locked pointer CAS]
key-files:
  created: [backend/app/services/timeline/reconcile.py, backend/app/services/timeline/overrides.py, backend/app/services/timeline/promotion.py, backend/tests/unit/timeline/test_reconcile.py, backend/tests/unit/timeline/test_overrides.py, backend/tests/integration/timeline/test_version_lifecycle.py]
  modified: [backend/app/services/timeline/__init__.py]
key-decisions:
  - "Contradictory chronology produces unranked events plus an explicit conflict instead of a fabricated story order."
  - "Causal edges publish only when supplied evidence covers both source and target events."
  - "Promotion and rollback recompute the graph manifest before a row-locked expected-revision pointer move."
requirements-completed: [REQ-TIME-02, REQ-TIME-04, REQ-TIME-05, REQ-TIME-06, REQ-TIME-09]
duration: 9min
completed: 2026-07-13
---

# Phase 08 Plan 03: Reconciliation, Overrides, and Promotion Summary

**Quality-tier reconciliation with fail-closed pre-call budgets, append-only override relinking, and PostgreSQL CAS promotion/byte-identical rollback.**

## Performance

- **Duration:** 9 min
- **Started:** 2026-07-13T03:32:52Z
- **Completed:** 2026-07-13T03:41:58Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Reconciled duplicate cross-chapter events and participants while deriving story rank only from an acyclic deterministic constraint graph.
- Enforced quality-tier, worst-case budget reservation before transport access; unknown prices set `paused_budget` with zero provider calls.
- Added evidence-backed `causes`, `triggers`, `responds_to`, and `blocks` edges, rejecting endpoint-incomplete evidence.
- Preserved field corrections as append-only overlays and relinked exact evidence identities across reanalysis, with ambiguous/missing targets retained as `needs_relink`.
- Validated immutable graph component checksums before row-locked active-pointer CAS, with pointer+journal transactions and byte-identical rollback manifests.

## Task Commits

1. **Task 1 RED: reconciliation contracts** - `d16b5c5`
2. **Task 1 GREEN: chronology and causal reconciliation** - `9d6eff6`
3. **Task 2 RED: override lifecycle contracts** - `c71a2ef`
4. **Task 2 GREEN: append-only overlays and relink** - `f36264f`
5. **Task 3 RED: PostgreSQL lifecycle contract** - `d1b0844`
6. **Task 3 GREEN: promotion/CAS/rollback lifecycle** - `8705173`

## Files Created/Modified

- `backend/app/services/timeline/reconcile.py` - Budgeted quality reconciliation, dedupe, topology, conflicts, and causal evidence gates.
- `backend/app/services/timeline/overrides.py` - Append-only field overlays, visible-field derivation, stable relink, and `needs_relink`.
- `backend/app/services/timeline/promotion.py` - Graph manifests, scope/status validation, row-lock CAS, journaled promotion/rollback, persisted override relink.
- `backend/app/services/timeline/__init__.py` - Timeline package exports required by later worker/API plans.
- `backend/tests/unit/timeline/test_reconcile.py` - Budget, dedupe, causal, and contradiction coverage.
- `backend/tests/unit/timeline/test_overrides.py` - Supersession, overlay, relink, ambiguity, and edge visibility coverage.
- `backend/tests/integration/timeline/test_version_lifecycle.py` - Real PostgreSQL stale CAS, invalid candidate, rollback, and override lifecycle proof.

## Decisions Made

- Quality reconciliation rejects non-quality deployment identities; it does not fall back to a cheaper tier.
- Exact evidence-ID sets are the automatic override relink identity; zero or multiple matches remain active but require explicit relink.
- Manifest identity includes separate event, participant, evidence, and edge component checksums plus canonical graph rows.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected PostgreSQL fixture chapter and rollback expectations**
- **Found during:** Task 3 GREEN verification
- **Issue:** The initial RED fixture used a non-owned hardcoded chapter ID and expected the override to remain on the promoted ID after rolling back.
- **Fix:** Created an owned chapter fixture and asserted successful reverse relink to the rollback target.
- **Files modified:** `backend/tests/integration/timeline/test_version_lifecycle.py`
- **Verification:** Real PostgreSQL lifecycle test and the full 9-test plan suite passed.
- **Committed in:** `8705173`

**Total deviations:** 1 auto-fixed bug. **Impact:** Test fixture correctness only; planned production behavior was unchanged.

## Issues Encountered

- PostgreSQL CI service was initially stopped. The pinned `docker-compose.ci.yml` `db` service was started, Alembic upgraded to `10analysistime01`, and the integration test then passed against PostgreSQL.
- Existing pytest timeout-option warnings remain pre-existing and outside 08-03 ownership.

## Known Stubs

None.

## Verification

- Plan suite: **9 passed** (`test_reconcile.py`, `test_overrides.py`, `test_version_lifecycle.py`).
- PostgreSQL lifecycle test: **1 passed**, not skipped.
- `python -m compileall -q app/services/timeline`: passed.
- Alembic PostgreSQL upgrade reached `10analysistime01`.

## User Setup Required

None.

## Self-Check: PASSED

- All six created implementation/test files and the required timeline package export exist.
- All six RED/GREEN task commits exist.
- All plan acceptance and verification commands passed.

## Next Phase Readiness

- 08-04 may expose owner-scoped progressive candidates from the now validated active/candidate lifecycle.
- No later Phase 08 plan was executed.

---
*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Completed: 2026-07-13*
