# 19-02 SUMMARY — Timeline multi-lane chapter plot view

## Objective

Replace drill-down “single horizontal jitter line” with a novel plot timeline: **chapter ruler on X** + **event_type swimlanes on Y**.

## Steps done

### Task 1: ECharts layout rewrite (`timeline-chart.tsx`)

- **X**: `narrative_chapter_number` + micro-offset within chapter (`source_start` blended with within-chapter rank). Primary layout is no longer event index.
- **Y**: fixed lanes `plot | conflict | character | world` (Chinese labels: 情节 / 冲突 / 人物 / 世界观). Unknown types normalize to `plot`.
- **X labels**: `第 N 章` (chapter ticks only; fractional micro-offsets unlabeled).
- **Y labels**: type names on horizontal dashed swimlanes.
- **Overview**: `buildEventWindows` density/valley stages unchanged; drill-in uses swimlanes.
- **Labels**: short titles when `visibleEvents.length ≤ 16`; else hover tooltip.
- **Causal edges**: only when parent passes edges **and** visible window ≤ 24 events; endpoints use swimlane coordinates (not index Y=0).
- Canvas marked `data-layout="chapter-swimlane"` for tests/UI contract.
- Point colors follow lane (manual title provenance still amber).

Exported pure helpers for layout unit tests:

- `normalizeEventType`, `eventTypeLaneY`
- `buildChapterXPositions`, `buildSwimlanePoints`
- `EVENT_TYPE_LANES`

### Task 2: Tests

- Extended `timeline-chart.test.tsx`: layout unit tests (distinct Y lanes, chapter X, not legacy `index%4` jitter) + progressive disclosure / swimlane drill-in.
- `page.test.tsx` regression suite included in verification.

### Task 3: `page.tsx`

- No change required. Causal toggle already gates `causalEdges` at the analysis page; chart additionally density-gates drawing.

## Verification

```
cd D:\ADLINK\Myproject\novel-mind\frontend
npx vitest run src/components/timeline/timeline-chart.test.tsx src/app/analysis/page.test.tsx --reporter=dot
```

**Result: 2 files, 23 passed**

## Must-haves check

| Truth | Status |
|-------|--------|
| Drill-down uses chapter-scaled X + event_type Y swimlanes | Met |
| Not primary-dependent on `index % 4` scatter jitter | Met (grep clean; tests assert ≠ legacy Y) |
| Axis labels communicate chapter progression | Met (`第 N 章`) |
| ≥2 distinct Y lanes when multiple types present | Met (test) |

## Residual risks

- ECharts option is built in `useMemo` but not assertable via mocked canvas beyond `data-layout` and pure helpers — visual polish still needs a manual/e2e glance.
- Many same-type events in one chapter still cluster on one horizontal lane (by design); only micro-offset separates them on X.
- Story ordering still plots on chapter X (label notes 故事序视图); list sort follows `ordering`, chart geometry is chapter-first.

## Files changed

- `frontend/src/components/timeline/timeline-chart.tsx`
- `frontend/src/components/timeline/timeline-chart.test.tsx`
- `.planning/phases/19-analysis-workbench-presentation-and-truth/19-02-SUMMARY.md` (this file)
