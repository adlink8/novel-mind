---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 05
subsystem: timeline-ui
tags: [nextjs, echarts, playwright, accessibility, responsive, spoiler-controls]
requires:
  - phase: 08-04
    provides: progressive owner-scoped timeline API and isolated active/candidate envelopes
provides:
  - Global novel-selecting analysis workspace with progressive lifecycle states
  - Responsive ECharts horizontal timeline with zoom and accessible companion list
  - Explicit spoiler controls and isolated active/running-candidate views
affects: [08-06, phase-08-verification, timeline-ui]
tech-stack:
  added: []
  patterns: [accessible canvas companion list, explicit spoiler confirmation, source-isolated progressive polling]
key-files:
  created: [frontend/src/app/analysis/page.tsx, frontend/src/components/timeline/timeline-chart.tsx, frontend/src/components/timeline/timeline-controls.tsx, frontend/src/components/timeline/timeline-status.tsx, frontend/src/app/analysis/page.test.tsx, frontend/e2e/timeline.spec.ts]
  modified: [frontend/src/components/app-shell.tsx]
key-decisions:
  - "The canvas visualization and keyboard-operable event list expose the same timeline events."
  - "Active and running candidate versions remain explicit tabs backed by separate API envelopes."
  - "Full-book disclosure requires a confirmation before persisting the per-novel preference."
requirements-completed: [REQ-TIME-06, REQ-TIME-07, REQ-TIME-08, REQ-TIME-10]
duration: 11min
completed: 2026-07-13
---

# Phase 08 Plan 05: Global Interactive Timeline Summary

**Global `/analysis` workspace with progressive source-isolated versions, spoiler controls, and an accessible responsive ECharts timeline.**

## Performance

- **Duration:** 11 min
- **Started:** 2026-07-13T04:01:00Z
- **Completed:** 2026-07-13T04:12:00Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Added one global novel selector that idempotently starts/resumes timeline analysis and polls progressive status/results.
- Represented empty, queued/running, partial, paused-budget, failed, cancelled, and completed states with progress and last-update feedback.
- Kept active and running candidate events, counts, and aggregates in separate selectable views.
- Added narrative/story ordering, person filtering, optional causal overlay, first-chapter cutoff messaging, and confirmed full-book disclosure.
- Rendered a stable horizontal ECharts canvas with inside/slider zoom plus a keyboard-operable companion event list and evidence/reader drill-down.
- Replaced the primary navigation search entry with `/analysis`; `/search` remains directly reachable from evidence actions and the mobile search shortcut.

## Task Commits

1. **Task 1 RED: timeline workspace contracts** - `10f3650`
2. **Task 1 GREEN: progressive timeline workspace** - `e5d82d5`
3. **Task 2 RED: responsive browser contract** - `a57e5ce`
4. **Task 2 GREEN: accessible zoomable ECharts timeline** - `1f3401d`
5. **Task 3: navigation, polling, and final verification** - `9395823`

## Verification

- `npm run lint` — passed with 0 errors; 4 pre-existing hook warnings outside plan-owned files.
- `npm test -- --run` — 8 files, 66 tests passed.
- `npm run build` — passed; `/analysis` generated successfully.
- `npm run test:e2e -- timeline.spec.ts` — desktop and mobile-390 projects passed (2/2).
- Browser checks confirmed visible ECharts canvas, inside+slider dataZoom, keyboard detail activation, evidence links, no document-level horizontal overflow, and screenshots for both viewports.

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None. Empty select values represent intentional “all people” and “no novel selected” control states.

## Threat Flags

None. No new endpoint, authentication path, file access, or trust-boundary schema was introduced.

## Self-Check: PASSED

- All seven declared plan files exist.
- All five `08-05` task commits exist.
- Unit, lint, build, desktop browser, mobile-390 browser, canvas, zoom, overflow, and screenshot checks passed.
- Unrelated dirty backend and frontend settings/API files were not staged or committed.

## Next Phase Readiness

- 08-06 may assemble release evidence and verify the complete Phase 08 vertical slice.
- 08-06 was not executed by this plan.
