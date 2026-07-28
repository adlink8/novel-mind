---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 01
subsystem: database
tags: [postgresql, sqlalchemy, alembic, durable-jobs, budget-ledger, timeline]
requires:
  - phase: 07-semantic-hierarchical-chunking
    provides: immutable hierarchy build and evidence lineage
provides:
  - PostgreSQL authority for durable analysis runs and immutable timeline versions
  - Mixed-precision dual-order event graph with evidence, overrides, and pointer journal
  - CAS job leases, restart checkpoints, and fail-closed pre-call budget reservations
affects: [08-02, 08-03, 08-04, timeline-api, timeline-worker]
tech-stack:
  added: []
  patterns: [immutable candidate plus CAS active pointer, worst-case pre-call reservation, stable stage checkpoints]
key-files:
  created: [backend/migrations/versions/10_analysis_timeline_versions.py, backend/app/services/timeline/jobs.py, backend/app/services/timeline/budget.py]
  modified: [backend/app/models/analysis.py, backend/app/models/timeline.py, backend/app/schemas/analysis.py, backend/app/models/__init__.py]
key-decisions:
  - "Unknown provider pricing fails closed and permanently pauses the current budget gate before network access."
  - "Stable stage keys and completed artifact checksums are the restart/idempotency boundary."
patterns-established:
  - "Timeline candidates remain immutable; only a revisioned owner/novel pointer and append-only journal represent activation or rollback."
  - "Production job operations use short AsyncSession transactions; deterministic in-memory adapters are test-only."
requirements-completed: [REQ-TIME-01, REQ-TIME-02, REQ-TIME-03]
duration: 14min
completed: 2026-07-13
---

# Phase 08 Plan 01: Timeline Persistence and Orchestration Foundation Summary

**PostgreSQL-backed immutable timeline versions with durable CAS jobs, mixed-time event graphs, reversible active pointers, and priced pre-call budget reservations.**

## Performance

- **Duration:** 14 min
- **Started:** 2026-07-13T03:03:00Z
- **Completed:** 2026-07-13T03:17:00Z
- **Tasks:** 3
- **Files modified:** 11

## Accomplishments

- Added 13 authoritative tables covering runs, versions, chapter stages, model-call/cache audit, budgets, events, participants, evidence, causal edges, overrides, active pointers, and promotion/rollback journal.
- Persisted exact/relative/fuzzy/unknown time without fabricated precision, independently from narrative and derived story order.
- Implemented idempotent first-entry startup, CAS lease/reclaim, restart checkpoints, cancel/resume, and fail-closed cost reservations.

## Task Commits

1. **Task 1 RED: persistence contract** - `5bde3cd`
2. **Task 1 GREEN: PostgreSQL timeline authority** - `e2b5fa3`
3. **Task 2 RED: job and budget gates** - `b130dbb`
4. **Task 2 GREEN: durable orchestration foundation** - `dd56cb7`
5. **Task 3: fault and rollback confirmation** - `4425571`

## Files Created/Modified

- `backend/app/models/analysis.py` - Runs, immutable manifests, stages, call audit, and budget records.
- `backend/app/models/timeline.py` - Versioned event graph, override layer, active pointer, and journal.
- `backend/migrations/versions/10_analysis_timeline_versions.py` - Ordered PostgreSQL creation/drop migration.
- `backend/app/services/timeline/jobs.py` - Stable stages, CAS leases, checkpoint recovery, cancel/resume.
- `backend/app/services/timeline/budget.py` - Idempotent worst-case reserve/settle/release gate.
- `backend/tests/unit/timeline/` and `backend/tests/integration/timeline/test_persistence.py` - Restart, budget, metadata, and failure contracts.

## Decisions Made

- Kept legacy `AnalysisResult` and `TimelineEvent` read-compatible; they do not fabricate Phase 08 lineage.
- Used explicit immutable version/event rows and a separate override overlay so reanalysis cannot overwrite user corrections.
- Made unknown pricing equivalent to a budget pause; token/call ceilings cannot authorize an unpriced call.

## Deviations from Plan

None - plan executed within declared files, plus the required `app.models` exports needed by Alembic metadata discovery.

## Issues Encountered

- The default shell Python lacked SQLAlchemy; verification used the existing `backend/.venv` without installing packages.
- `alembic upgrade head` and `alembic current` passed against PostgreSQL at `10analysistime01`. `alembic check` reports two pre-existing Phase 07 index drifts (`ix_chunk_hierarchy_nodes_build_id` missing and `idx_text_chunks_hierarchy_node` extra). These files are outside 08-01 ownership and were preserved.
- The target PostgreSQL pytest case was skipped by the repository's opt-in PG marker, while the migration command itself exercised the configured PostgreSQL database successfully.

## Known Stubs

None.

## Self-Check: PASSED

- All 11 created/modified plan files exist.
- All five `08-01` task commits exist.
- Target suite: 12 passed, 1 skipped.
- Python compile check passed.

## User Setup Required

None.

## Next Phase Readiness

- 08-02 may build strict extraction schemas and gateway attempts on the persisted run/version/call contracts.
- The Phase 07 Alembic index drift remains an unrelated repository maintenance item.

---
*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Completed: 2026-07-13*
