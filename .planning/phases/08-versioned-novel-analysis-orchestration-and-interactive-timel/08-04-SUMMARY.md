---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 04
subsystem: timeline-api
tags: [fastapi, sqlalchemy, owner-scope, spoiler-boundary, progressive-api, typescript]
requires:
  - phase: 08-03
    provides: immutable active/candidate lifecycle, overlays, CAS rollback, and causal graph
provides:
  - Durable owner-scoped timeline run lifecycle and edit/rollback endpoints
  - Strictly separate active and running-candidate API envelopes
  - Visible-set-first spoiler filtering with first-chapter default and persisted full-book preference
  - Typed frontend lifecycle, ordering, person, causal, and source consumers
affects: [08-05, 08-06, analysis-workspace, timeline-ui]
tech-stack:
  added: []
  patterns: [visible event IDs before overlays and derivations, database uniqueness as concurrent-start authority, separate version-source envelopes]
key-files:
  created: [backend/app/services/timeline/query.py, backend/tests/unit/timeline/test_api.py, backend/tests/integration/timeline/test_spoilers.py]
  modified: [backend/app/api/timeline.py, backend/app/schemas/timeline.py, frontend/src/lib/api.ts, frontend/src/lib/api.contract.test.ts]
key-decisions:
  - "Missing or invalid reading progress resolves to the novel's first chapter; a novel with no chapters exposes no events."
  - "A full-book query is honored only when the same owner/novel has a persisted timeline_full_book preference."
  - "Active and running candidate carry independent status, progress, events, counts, aggregates, previews, and edges."
requirements-completed: [REQ-TIME-01, REQ-TIME-03, REQ-TIME-06, REQ-TIME-08]
duration: 11min
completed: 2026-07-13
---

# Phase 08 Plan 04: Progressive Owner-Scoped Timeline API Summary

**Durable owner-scoped timeline controls with D20 first-chapter spoiler defaults, visible-set-first derivation, and D21 active/candidate isolation.**

## Performance

- **Duration:** 11 min
- **Started:** 2026-07-13T03:46:07Z
- **Completed:** 2026-07-13T03:56:24Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Replaced timeline 501 placeholders with start-or-resume, status, cancel, resume, version, rollback, scoped edit, preference, and progressive query endpoints.
- Enforced owner/novel scope through the existing authentication and owned-novel dependencies; concurrent first entry is governed by the durable unique active-run constraint.
- Kept active and running candidate as separate envelopes so candidate progress and partial artifacts cannot contaminate prior active counts or aggregates.
- Computed visible event IDs before overrides, participants, person filtering, causal edges, counts, aggregates, and previews; causal edges require both endpoints to remain visible.
- Implemented D20: absent progress reveals only chapter one, while full-book disclosure requires both an explicit query and persisted per-novel preference.
- Added typed frontend consumers without committing or reformatting the user's unrelated `AIModelProvider` changes in `frontend/src/lib/api.ts`.

## Task Commits

1. **Task 1 RED: durable timeline API contracts** - `49db177`
2. **Task 1 GREEN: owner-scoped durable APIs** - `fb73933`
3. **Task 2 RED: visible-set spoiler contracts** - `e0580d3`
4. **Task 2 GREEN: visible-set-first derived previews** - `d71904e`
5. **Task 3: typed consumer and OpenAPI closure** - `39c5cbe`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added repository-required primary marker to timeline API unit tests**
- **Found during:** Task 1 verification
- **Issue:** The repository classification gate rejected tests without a unit/integration/contract/live marker.
- **Fix:** Added the `unit` marker to the new API contract suite.
- **Files modified:** `backend/tests/unit/timeline/test_api.py`
- **Verification:** Target backend suite passed 5 tests.
- **Commit:** `fb73933`

**2. [Rule 2 - Missing Critical Functionality] Added spoiler-safe previews derivation**
- **Found during:** Task 2 TDD fail-fast review
- **Issue:** The initial visible-set implementation covered events, edges, counts, and aggregates but omitted the plan-required preview derivation.
- **Fix:** Added previews generated only from already-visible, overlaid events.
- **Files modified:** `backend/app/schemas/timeline.py`, `backend/app/services/timeline/query.py`
- **Verification:** Hidden future override text does not appear in previews or serialized responses.
- **Commit:** `d71904e`

**Total deviations:** 2 auto-fixed (1 blocking, 1 missing critical functionality). **Impact:** Both changes enforce repository and spoiler contracts without expanding product scope.

## Verification

- Backend: `pytest tests/unit/timeline/test_api.py tests/integration/timeline/test_spoilers.py -x` — **5 passed**.
- Frontend: `npm test -- --run src/lib/api.contract.test.ts` — **12 passed**.
- Extended frontend compatibility: `api.contract.test.ts` + `api.test.ts` — **27 passed**.
- Python compile check for API/schema/query modules — passed.
- OpenAPI in-memory export check — **10 timeline paths**, required durable paths present.
- Existing pytest timeout-plugin warnings remain pre-existing and out of scope.

## Known Stubs

None.

## Threat Flags

None. The new authenticated timeline endpoints and spoiler boundary were explicitly covered by the plan threat/validation contract.

## Self-Check: PASSED

- All seven declared created/modified files exist.
- All five `08-04` task commits exist.
- Backend, frontend, compile, OpenAPI, owner/version isolation, and spoiler acceptance checks passed.
- Unrelated dirty files remain unstaged; `frontend/src/lib/api.ts` retains only the user's pre-existing AI provider diff after plan commits.

## Next Phase Readiness

- 08-05 may consume independent active/candidate envelopes and visible progress in the global analysis workspace.
- No later Phase 08 plan was executed.
