---
phase: 18-frontend-motion-and-transition-system
plan: "03"
subsystem: ui
tags: [content-motion, analysis-insertion, playwright, reduced-motion, source-contract]

requires:
  - phase: 18-01
    provides: motion tokens and theme boot
  - phase: 18-02
    provides: dismissable panels and spatial surfaces
provides:
  - Identity-stable analysis list insertion markers
  - Dual-viewport Playwright motion qualification
  - Phase 18 touched-file source contract
affects: []

tech-stack:
  added: []
  patterns:
    - "seenEventIdsRef + data-insertion=fresh|stable for one-shot insert motion"
    - "Loading regions: role=status aria-busy + text (no decorative pulse)"
    - "Mocked Playwright matrix for deterministic theme/panel/geometry checks"

key-files:
  created:
    - frontend/e2e/motion-and-transitions.spec.ts
    - frontend/src/components/ui/motion-source-contract.test.ts
  modified:
    - frontend/src/components/page-header.tsx
    - frontend/src/components/empty-state.tsx
    - frontend/src/components/novel-card.tsx
    - frontend/src/components/search/search-result-card.tsx
    - frontend/src/components/reader/progress-bar.tsx
    - frontend/src/components/timeline/timeline-chart.tsx
    - frontend/src/components/timeline/timeline-status.tsx
    - frontend/src/components/relationships/relationship-workspace.tsx
    - frontend/src/components/clues/clue-workspace.tsx
    - frontend/src/app/analysis/page.tsx

key-decisions:
  - "Hover translate removed from cards/list; color/border/shadow feedback only"
  - "Progress/analysis bars transition width only with fast token + ARIA progressbar"
  - "Playwright uses route mocks (no live analysis dependency)"

patterns-established:
  - "data-insertion=fresh for first-seen event ids; cleared after ~220ms"
  - "motion-source-contract scans all Phase 18 files_modified paths"

requirements-completed: [UI-MOTION-01, UI-MOTION-03, UI-MOTION-04, UI-MOTION-05, UI-MOTION-06]

duration: 50min
completed: 2026-07-16
---

# Phase 18 Plan 03: Content Feedback and Qualification Summary

**Content/state feedback is geometry-stable, progressive analysis inserts animate once by identity, and desktop/mobile-390 Playwright plus source contract qualify the full Phase 18 motion system.**

## Component Feedback Matrix

| Surface | Token | Behavior |
|---|---|---|
| PageHeader / EmptyState | standard content | fade-capable content region; empty has role=status |
| NovelCard / SearchResultCard | standard | border/shadow only; no layout hover translate |
| ProgressBar | fast width | ARIA progressbar; labels do not move |
| Analysis workspace tabs | standard | color/bg transitions |
| TimelineStatus progress | fast width | role=progressbar + live text |
| Timeline event cards | standard | insert once; no hover lift |
| Relationship/Clue loading | content | text + aria-busy (no pulse) |

## Insertion Identity Rule

- `seenEventIdsRef` records every event id observed for the current chart instance.
- First appearance ⇒ `data-insertion="fresh"` and one content transition; cleared after ~220ms.
- Subsequent poll envelopes with the same ids stay `data-insertion="stable"` (no replay).
- Ids disappearing (version/novel change) are dropped from the seen set.
- Chart height remains reserved (`min-h` / fixed echarts 420); no dimension animation around graphs.

## Desktop / Mobile Geometry Evidence

Playwright projects:

| Project | Viewport | Result |
|---|---|---|
| chromium-desktop | 1280×800 | 3/3 passed |
| chromium-mobile-390 | 390×844 | 3/3 passed |

Assertions:

- Persisted dark/custom theme attributes/classes on first navigation.
- Analysis shell: `scrollWidth <= clientWidth + 1`.
- Reader: settings Escape close; chat composer vs progress bounding-box non-overlap when chat open.
- Explicit theme switch keeps clientWidth/clientHeight stable.
- Reduced-motion media: tabs remain operable.

```
npx playwright test e2e/motion-and-transitions.spec.ts \
  --project=chromium-desktop --project=chromium-mobile-390
→ 6 passed
```

## Reduced-Motion Results

- Global CSS still final authority (0.01ms, no transform on motion utilities).
- Decorative `animate-pulse` removed from analysis/relationship/clue loaders.
- Loading always has text + `aria-busy`/`role=status`.
- `animate-spin` retained only on real loading spinners (allowlisted).

## Verification Evidence

```
npm test → 22 files, 172 tests passed
npm run lint → 0 errors
npm run build → production build succeeded
Playwright motion matrix → 6/6 passed
motion-source-contract → no arbitrary duration / linear / transition-all /
  unapproved infinite / framer-motion imports in Phase 18 files
```

## Task Commits

1. **Tasks 1–3: content feedback + analysis identity + qualification** - `743a524` (feat)

## No-Business-Change Statement

Phase 18 does not change APIs, backend models, analysis polling intervals, reading progress rules, chat job lifecycle, or navigation targets. Motion is CSS/React presentational only.
