---
phase: 11-clue-and-foreshadow-tracking
plan: "04"
subsystem: ui
tags: [clue, foreshadow, analysis-workspace, nextjs, vitest, axios, evidence-panel]

requires:
  - phase: 11-clue-and-foreshadow-tracking
    provides: owner-scoped /api/clues envelope, detail panels, human actions (11-03)
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: /analysis novel selector, timeline_full_book preference, confirmation dialog
provides:
  - Typed frontend clueApi isolated from api.ts with active/running separation
  - Accessible clue band + keyboard list with server-visible filters and counts
  - Evidence/payoff panel with confirm/reject/annotate/adjust_link
  - /analysis workbench tab 线索与伏笔 under one novel selector and spoiler preference
affects:
  - 11-05 browser qualification and release gate

tech-stack:
  added: []
  patterns:
    - "clue-api.ts owns clue client; never mutates timeline_full_book"
    - "Visible set from server only for filters/counts/chains"
    - "Human actions refresh from authority after POST; no optimistic state"
    - "Workbench tabs timeline|relationships|clues; no top-level /clues route"

key-files:
  created:
    - frontend/src/lib/clue-api.ts
    - frontend/src/lib/clue-api.contract.test.ts
    - frontend/src/components/clues/clue-controls.tsx
    - frontend/src/components/clues/clue-band.tsx
    - frontend/src/components/clues/clue-evidence-panel.tsx
    - frontend/src/components/clues/clue-workspace.tsx
    - frontend/src/components/clues/clue-workspace.test.tsx
  modified:
    - frontend/src/app/analysis/page.tsx
    - frontend/src/app/analysis/page.test.tsx

key-decisions:
  - "Match real 11-03 ClueVisibleEnvelope (available_states/available_character_ids/counts.by_state) rather than idealized plan sketch"
  - "ClueWorkspace owns independent clue run status/start; timeline TimelineStatus hidden on clue tab"
  - "Full-book checkbox on clue surface calls parent Phase 08 confirm + timelineApi.setFullBookPreference only"
  - "Reject and adjust_link require explicit alertdialog confirmation before POST"

patterns-established:
  - "sortVisibleClues: chapter → source_start → logical_clue_id for band/list parity"
  - "Payoff chain rendered only from server detail.payoff_chain / never client-inferred"
  - "AnalysisWorkspaceMode extends with clues without intermediate plot/theme menus"

requirements-completed: [REQ-CLUE-04, REQ-CLUE-05, REQ-CLUE-06]

duration: 45min
completed: 2026-07-15
---

# Phase 11 Plan 04: Analysis Workspace Clue UI Summary

**Typed clue client plus accessible band/filters/evidence/payoff panel integrated as a third /analysis workbench under shared Phase 08 spoiler confirmation, without a top-level /clues route.**

## Performance

- **Duration:** 45 min
- **Started:** 2026-07-15T02:15:00Z
- **Completed:** 2026-07-15T03:00:00Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- Isolated `clueApi` reuses shared Axios instance; contract tests lock paths, filters, dual-source envelope, and four action payloads; no preference mutation.
- `ClueWorkspace` loads server-visible envelopes, keeps band/list ID order parity, and renders server payoff chains in the evidence panel.
- Protected confirm/reject/annotate/adjust_link with reject/link explicit confirmation and post-action authority refresh.
- `/analysis` gains「线索与伏笔」tab; timeline ECharts and relationship Cytoscape paths remain intact when those tabs are selected.

## Task Commits

1. **Tasks 1–3: clue client + workspace UI + analysis integration** - `0d61b1b` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP docs commit follows)

## Files Created/Modified

- `frontend/src/lib/clue-api.ts` — types + client + sortVisibleClues
- `frontend/src/lib/clue-api.contract.test.ts` — path/param/payload contracts
- `frontend/src/components/clues/clue-controls.tsx` — state/person filters + version tabs
- `frontend/src/components/clues/clue-band.tsx` — horizontal band + keyboard list + chain strip
- `frontend/src/components/clues/clue-evidence-panel.tsx` — evidence/links/actions drawer
- `frontend/src/components/clues/clue-workspace.tsx` — orchestration + run status
- `frontend/src/components/clues/clue-workspace.test.tsx` — empty/loading/running/actions
- `frontend/src/app/analysis/page.tsx` — workbench tab + shared full-book dialog
- `frontend/src/app/analysis/page.test.tsx` — clue tab + spoiler confirmation regression

## Decisions Made

- Backend field names from 11-03 are authoritative (`available_states`, not `status_options`).
- Clue analysis lifecycle is independent of timeline worker controls when the clue tab is active.
- No new npm packages (D-12).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule — API fidelity] Plan interface sketch vs live envelope**
- **Found during:** Task 1 typing against 11-03 query.py
- **Issue:** Plan sketch used `status_options` / `people`; API returns `available_states` / `available_character_ids` and nested `counts.by_state`.
- **Fix:** Typed client matches live projection.
- **Files modified:** `frontend/src/lib/clue-api.ts`
- **Verification:** contract + workspace tests green

---

**Total deviations:** 1 auto-fixed  
**Impact on plan:** Required for correctness against 11-03; no scope creep.

## Issues Encountered

None blocking.

## Commands and Test Results

```text
cd frontend
npm test -- --run src/lib/clue-api.contract.test.ts
# 7 passed

npm test -- --run src/components/clues/clue-workspace.test.tsx
# 8 passed

npm test -- --run src/app/analysis/page.test.tsx src/components/clues/clue-workspace.test.tsx src/lib/clue-api.contract.test.ts
# 28 passed

npm test
# 121 passed (14 files)

npm run build
# Next.js production build OK; routes: /analysis present, no /clues
```

## Verification Mapping

| Must-have | Evidence |
|---|---|
| No new top-level route / intermediate menus | Build routes list; page tests assert no 剧情摘要/主题 and no clue link |
| Server-visible data only for filters/counts/chains | Controls map `available_*`; payoff from detail.payoff_chain |
| Four protected human actions | workspace test posts confirm/reject/annotate/adjust_link |
| Shared Phase 08 full-book confirmation | page test confirms dialog → timelineApi.setFullBookPreference |
| Timeline tests remain green | page.test.tsx 13 tests + relationships suite still pass |

## Self-Check: PASSED

## Next

- Execute `11-05-PLAN.md` (fixtures, adversarial, browser qualification, release gate).
