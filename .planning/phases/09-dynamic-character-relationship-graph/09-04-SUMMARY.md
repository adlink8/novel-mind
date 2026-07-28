---
phase: 09-dynamic-character-relationship-graph
plan: 04
subsystem: ui
tags: [cytoscape, relationship-graph, analysis-workspace, evidence-panel, vitest, nextjs]

# Dependency graph
requires:
  - phase: 09-dynamic-character-relationship-graph
    provides: spoiler-safe /api/relationships graph and evidence endpoints
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: /analysis timeline workspace, full-book preference, ECharts timeline
provides:
  - Cytoscape.js relationship workspace on /analysis with D-22 degradation
  - Typed relationshipsApi client contracts without owner_id
  - Shared novel/version/full-book/through_chapter state with ECharts timeline
  - Evidence side panel with machine/manual provenance and chapter navigation
  - Keyboard companion list derived from the same nodes/edges arrays
affects: [09-05, 10-reader-ai, browser-qualification]

# Tech tracking
tech-stack:
  added: [cytoscape@3.34.0]
  patterns:
    - workspace tab timeline|relationships only; no intermediate summary modes
    - server-filtered graph elements only; filters_required never mounts Cytoscape
    - canvas and keyboard list share the same nodes/edges arrays
    - remount RelationshipWorkspace on novel/source/version key to clear selection

key-files:
  created:
    - frontend/src/components/relationships/relationship-workspace.tsx
    - frontend/src/components/relationships/relationship-graph.tsx
    - frontend/src/components/relationships/relationship-controls.tsx
    - frontend/src/components/relationships/relationship-evidence-panel.tsx
    - frontend/src/lib/relationships.contract.test.ts
    - frontend/src/app/analysis/relationships.test.tsx
  modified:
    - frontend/package.json
    - frontend/package-lock.json
    - frontend/src/lib/api.ts
    - frontend/src/lib/api.contract.test.ts
    - frontend/src/app/analysis/page.tsx
    - frontend/src/app/analysis/page.test.tsx
    - frontend/src/components/timeline/timeline-chart.tsx

key-decisions:
  - "D-19: pin exact cytoscape@3.34.0 with built-in types; never install @types/cytoscape."
  - "OpenAPI uses singular character_id/relation_type filters; client matches server, not research plural draft."
  - "TimelineChart gains optional onNarrativePositionChange only; ECharts renderer stays."
  - "RelationshipWorkspace remounts via key on novel/source/version to clear stale selection."

patterns-established:
  - "relationshipsApi.getGraph/getEvidence never send owner_id."
  - "filters_required shows guidance and skips Cytoscape instantiation."
  - "large mode: pixelRatio 1, concentric layout, selected-only labels, haystack edges."

requirements-completed: [REQ-REL-03, REQ-REL-04, REQ-REL-05, REQ-REL-06]

# Metrics
duration: 45min
completed: 2026-07-15
---

# Phase 09 Plan 04: Cytoscape Relationship Workspace Summary

**Pinned Cytoscape.js relationship workspace on `/analysis` with shared timeline state, evidence panel, keyboard companion list, and deterministic normal/large/filters_required degradation.**

## Performance

- **Duration:** 45 min
- **Started:** 2026-07-15T08:00:00Z
- **Completed:** 2026-07-15T08:32:00Z
- **Tasks:** 3
- **Files modified:** 13

## Accomplishments

- Installed exact `cytoscape@3.34.0` (built-in TS declarations; no `@types/cytoscape`).
- Added `relationshipsApi` typed contracts matching OpenAPI graph/evidence query params and envelopes (cutoff, provenance, available filters/counts, degradation).
- Built relationship workspace components: controls, Cytoscape lifecycle, evidence panel, companion list.
- Extended AnalysisPage with timeline|relationships tabs; shared source/version/full-book/`throughChapter`; timeline selection updates narrative chapter.
- Vitest covers contracts, normal/large/filters_required, source switch, evidence navigation, cleanup, and intermediate-mode absence.

## Task Commits

1. **Tasks 1–3: pin Cytoscape, typed API, workspace UI, tests, lint/build confirm** - `bcba8c2` (feat)

**Plan metadata:** (this SUMMARY commit follows)

## Files Created/Modified

- `frontend/package.json` / `package-lock.json` — exact cytoscape@3.34.0
- `frontend/src/lib/api.ts` — Relationship* types + relationshipsApi
- `frontend/src/lib/relationships.contract.test.ts` — query names, no owner_id, envelope shape
- `frontend/src/components/relationships/*` — workspace, graph, controls, evidence panel
- `frontend/src/app/analysis/page.tsx` — workspace orchestration
- `frontend/src/app/analysis/relationships.test.tsx` — workspace behavior suite
- `frontend/src/components/timeline/timeline-chart.tsx` — optional narrative position callback (ECharts preserved)

## Decisions Made

- Client filter query names follow 09-03 backend (`character_id`, `relation_type`) rather than research-doc plurals.
- Stale selection isolation uses React remount key `${novelId}:${source}:${versionId}` plus graph refetch prune.
- Full-book preference remains the single Phase 08 preference surface for both workspaces.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Live-run spoiler banner assertion in page.test**
- **Found during:** Task 3 (full suite)
- **Issue:** Live runs hide the default “无进度则仅第一章” banner; candidate ignores cutoff.
- **Fix:** Assert live-run “不受阅读进度截断” messaging instead.
- **Files modified:** `frontend/src/app/analysis/page.test.tsx`
- **Verification:** page.test 11 passed
- **Committed in:** `bcba8c2`

**2. [Rule 1 - Bug] timelineApi contract timeout args**
- **Found during:** Task 3
- **Issue:** `startOrResume`/`resume` now pass 300s timeout; old contract expected single-arg post.
- **Fix:** Update api.contract.test expectations to match client.
- **Files modified:** `frontend/src/lib/api.contract.test.ts`
- **Verification:** api.contract.test 12 passed
- **Committed in:** `bcba8c2`

**3. [Rule 2 - Missing Critical] Lint: refs/setState-in-effect on new components**
- **Found during:** Task 3
- **Issue:** Strict react-hooks rules failed on ref assignment during render and sync effect setState.
- **Fix:** Move callback ref writes into effects; defer fetches with queueMicrotask; remount key for isolation; document zoomRef exception on timeline.
- **Files modified:** relationship-graph/workspace, timeline-chart
- **Verification:** eslint clean on Phase 09 paths; full lint 0 errors
- **Committed in:** `bcba8c2`

---

**Total deviations:** 3 auto-fixed (2 bug, 1 missing critical)
**Impact on plan:** No scope creep; contracts and UX match D-19..D-22.

## Issues Encountered

None remaining. Browser Playwright qualification deferred to 09-05.

## Commands and Test Results

```text
cd frontend

npm ls cytoscape @types/cytoscape
# cytoscape@3.34.0 (no @types/cytoscape)

npm test -- --run src/lib/relationships.contract.test.ts src/app/analysis/relationships.test.tsx
# 17 passed

npm test -- --run
# Test Files  10 passed (10)
# Tests  85 passed (85)

npm run lint
# 0 errors, 4 pre-existing warnings (eval/reader hooks)

npm run build
# Next.js production build OK; /analysis static route generated
```

Coverage highlights:

- Contract: source/version_id/through_chapter/character_id/relation_type/full_book; no owner_id.
- Normal: canvas + companion list same nodes/edges.
- Large: canvas mounts; degradation notice.
- filters_required: no canvas; guidance only.
- Active/candidate switch clears evidence selection and refetches.
- Evidence panel: provenance + `/novels/{id}?chapter=` link.
- Cleanup: Cytoscape destroy/removeAllListeners on workspace leave.
- timeline-chart still imports echarts; relationship-graph imports cytoscape.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Ready for **09-05** (qualification, adversarial, performance, release gate).
- Phase 10 may consume the same graph query contract from the UI/API path.
- Real browser desktop/mobile E2E against live API remains 09-05 scope.

## Self-Check: PASSED

- [x] key-files.created exist on disk
- [x] `git log --grep=09-04` has production commit `bcba8c2`
- [x] Task acceptance criteria verified (npm ls, contract tests, degradation tests, ECharts preserved)
- [x] Plan verification (full vitest + lint + build) logged above

---
*Phase: 09-dynamic-character-relationship-graph*
*Completed: 2026-07-15*
