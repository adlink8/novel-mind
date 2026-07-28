---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 08
subsystem: timeline-qualification-and-e2e
tags: [react, echarts, postgresql, playwright, production-worker, spoiler-safety, qualification]

requires:
  - phase: 08-07
    provides: durable production timeline worker, PostgreSQL call audit, evidence persistence, and active promotion
provides:
  - Chapter-aware timeline projection and selected-version participant isolation
  - PostgreSQL-backed qualification with signed raw worker artifacts and measured metrics
  - Unmocked desktop/mobile API-browser proof of partial candidate, active promotion, and spoiler cutoff
affects: [phase-08-verification, timeline-ui, timeline-release-gate, browser-e2e]

tech-stack:
  added: []
  patterns: [signed production artifact qualification, controlled-provider production-worker E2E, visible-query browser assertions]

key-files:
  created: [frontend/e2e/timeline-real.spec.ts, backend/tests/integration/timeline/test_real_qualification.py, .planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-08-SUMMARY.md]
  modified: [frontend/src/app/analysis/page.tsx, frontend/src/components/timeline/timeline-chart.tsx, frontend/src/app/analysis/page.test.tsx, backend/scripts/run_timeline_qualification.py, backend/app/services/timeline/query.py, tests/ci/test_timeline_release_gate.py, .planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-QUALIFICATION.md]

key-decisions:
  - "Narrative projection uses chapter number, persisted source offset when exposed, narrative index compatibility fallback, then event ID."
  - "Only signed artifacts observed after a production worker run on PostgreSQL may satisfy the release qualification gate."
  - "Browser E2E uses real FastAPI, Next.js, PostgreSQL, and timeline APIs; only the model provider transport is controlled."

patterns-established:
  - "Qualification authority: execute worker first, then score persisted events/evidence/attempts/stages/budget and production visible queries."
  - "Source isolation: participant options derive solely from the selected version and completed runs cannot reappear as running candidates."

requirements-completed: [REQ-TIME-04, REQ-TIME-06, REQ-TIME-07, REQ-TIME-08, REQ-TIME-10]

duration: 24min
completed: 2026-07-13
---

# Phase 08 Plan 08: Real Ordering, Production Qualification, and Browser E2E Summary

**Chapter-aware source-isolated timeline rendering with signed PostgreSQL worker artifacts and an unmocked partial-to-active spoiler-safe browser journey.**

## Performance

- **Duration:** 24 min
- **Started:** 2026-07-13T05:21:03Z
- **Completed:** 2026-07-13T05:45:24Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- Corrected narrative projection across non-contiguous chapters and prevented active/candidate participant filters from sharing event sources.
- Replaced release qualification self-claims with a production worker run on PostgreSQL and metrics derived from persisted events, evidence, call attempts, stage artifacts, budget settlement, active pointer, and spoiler-safe query output.
- Added desktop and 390px Playwright coverage using real Next.js, FastAPI, PostgreSQL, and timeline APIs with a controlled provider transport; no timeline request is routed or mocked.
- Signed the complete raw production artifact in `08-QUALIFICATION.md`; the recorded artifact digest was independently recomputed and matched.

## Task Commits

1. **Task 1 RED: ordering and source-isolation contracts** - `5405075`
2. **Task 1 GREEN: chapter-aware ordering and selected-source filters** - `68bb4a5`
3. **Task 2 RED: production qualification and anti-self-claim contracts** - `ccbcad9`
4. **Task 2 GREEN: persisted PostgreSQL qualification artifacts** - `8e422fd`
5. **Task 3: real partial-to-active API/browser E2E** - `b4df8d8`

## Files Created/Modified

- `frontend/src/app/analysis/page.tsx` - Derives people only from the selected version and clears stale person filters on source switches.
- `frontend/src/components/timeline/timeline-chart.tsx` - Uses stable chapter/source/event ordering and global ordinal chart positions.
- `frontend/src/app/analysis/page.test.tsx` - Covers non-contiguous chapters and distinct active/candidate participant sets.
- `frontend/e2e/timeline-real.spec.ts` - Real desktop/mobile partial candidate, promotion, default cutoff, and full-book browser flow.
- `backend/scripts/run_timeline_qualification.py` - Executes/scans production worker artifacts, signs reports, enforces release evidence, and provides controlled-provider browser setup.
- `backend/tests/integration/timeline/test_real_qualification.py` - Runs qualification against real PostgreSQL and proves missing expected output cannot qualify.
- `backend/app/services/timeline/query.py` - Excludes completed runs from the running-candidate source.
- `tests/ci/test_timeline_release_gate.py` - Rejects self-claimed reports and mocked timeline browser tests.
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-QUALIFICATION.md` - Signed raw production artifact, measured metrics, gates, and required commands.

## Decisions Made

- Kept compatibility with the current frontend event contract by falling back from optional `source_start` to persisted chapter-local `narrative_index`; event ID remains the deterministic final tie-breaker.
- Retained the frozen corpus and controlled dual-model helpers as diagnostics only. Their report version cannot pass the production release gate.
- Used direct local PostgreSQL setup only for browser test orchestration. The browser itself exercises normal authenticated production API routes without interception.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed completed active versions from running-candidate responses**
- **Found during:** Task 3 real browser E2E
- **Issue:** After promotion, the completed run still had `active_key=active`, so the same version appeared simultaneously as active and running candidate.
- **Fix:** Excluded completed runs in running-candidate resolution.
- **Files modified:** `backend/app/services/timeline/query.py`
- **Verification:** Real desktop/mobile Playwright requires active with one default-visible event and `running_candidate=null`; backend timeline suite also passed.
- **Committed in:** `b4df8d8`

---

**Total deviations:** 1 auto-fixed bug. **Impact on plan:** The fix closes a direct D-21 production isolation defect discovered by the planned unmocked E2E; no unrelated scope was added.

## Issues Encountered

- The first browser attempt exposed direct-script import path handling; the CLI now inserts the backend root before importing `app` modules.
- The second browser attempt used Playwright `check()` on a confirmation-gated checkbox; the test now clicks, confirms disclosure, and then waits for the real API response.
- Existing pytest warnings for unavailable `pytest-timeout` configuration remain pre-existing and out of scope.

## Known Stubs

None.

## Threat Flags

| Flag | File | Description |
|---|---|---|
| threat_flag: local-test-database-mutation | `backend/scripts/run_timeline_qualification.py` | Explicit E2E CLI modes seed/resume local PostgreSQL records for a named test user; they are not exposed as HTTP routes and use controlled provider transport only. |

## Verification

- Backend timeline unit/integration/adversarial: **65 passed**.
- Frontend Vitest: **68 passed**.
- Next.js production build: **passed**, including `/analysis`.
- Real Playwright desktop + mobile-390: **2 passed**; no `page.route` or `route.fulfill` in `timeline-real.spec.ts`.
- Timeline release gate: **7 passed**.
- PostgreSQL production qualification: **2 events, 2 evidence refs, 3 model attempts, 3 completed stages**, with event precision/recall/order/evidence coverage all 1.0 and spoiler leaks 0.
- Qualification artifact SHA-256 recomputation: **matched**.
- `08-VERIFICATION.md`: **not modified**.

## User Setup Required

None - the deterministic qualification and browser path use the existing local PostgreSQL service and controlled provider transport.

## Next Phase Readiness

- Verifier gaps 3-4 now have production-backed ordering/source-isolation, qualification, API, and browser evidence.
- Phase 08 is ready for independent re-verification; no verification report was created by this executor.

## Self-Check: PASSED

- All created/key artifact files exist.
- All five 08-08 task commits resolve.
- The signed qualification artifact digest recomputes exactly.
- `08-VERIFICATION.md` has no executor changes.

---
*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Completed: 2026-07-13*
