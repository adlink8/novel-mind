# 19-04 SUMMARY — Clue plant→payoff presentation

## Objective

Differentiate clues from timeline: primary vertical cards with **埋设章 → 兑现章** span bar, short title + summary, lifecycle state chips; demote horizontal event-strip presentation; spoiler-safe when `payoff_chapter` is null.

## Steps done

### Task 1: ClueCard / span UI
- New `frontend/src/components/clues/clue-card.tsx`:
  - Short `title`, optional `summary`, state chip, evidence/link counts
  - Span bar: plant ●═══○ payoff (relative positions via `spanPositions`)
  - `resolvePlantChapter`: prefers `first_cue_chapter`, else `narrative_chapter_number`
  - `resolvePayoffChapter`: only positive API `payoff_chapter`; **never invents** from state
  - Missing payoff →「兑现未公开」
- Refactored `clue-band.tsx`:
  - Primary: vertical `role=listbox` of `ClueCard`s (`data-testid=clue-keyboard-list` retained for e2e)
  - Removed horizontal「线索时间带」event strip
  - Kept server-only payoff chain panel when selected detail provides it

### Task 2: Workspace wiring
- Uses 19-01 fields already on `VisibleClue` in `clue-api.ts` (`first_cue_chapter`, `payoff_chapter`, `summary`) — no API client change required
- Empty-state copy updated (cards, not 时间带)
- Detail panel: evidence grouped by role (cue → reinforcement → payoff → disposition) with stable order

### Task 3: Tests
- New `clue-card.test.tsx` (helpers + card render + spoiler-safe payoff)
- Updated `clue-workspace.test.tsx` for vertical cards, span labels, evidence role groups

## Verification

```
cd D:\ADLINK\Myproject\novel-mind\frontend
npx vitest run src/components/clues --reporter=dot
```

**Result: 17 passed** (2 files, 2.17s)

## Must-haves check

| Truth | Status |
|-------|--------|
| Primary list is plant→payoff span cards, not timeline-like horizontal strip alone | Done — strip removed; vertical cards primary |
| Titles/summaries from API, not raw multi-line excerpts as only signal | Done — `title` + optional `summary` line |
| Lifecycle state chips retained | Done — chip with state color/dot |
| Missing `payoff_chapter` OK (spoiler-safe) | Done —「兑现未公开」, no invention |

## Residual risks

- Legacy clues without `first_cue_chapter`/`summary` fall back to narrative chapter and omit summary line until re-analysis
- E2E `clue-real.spec.ts` still targets `clue-keyboard-list` (kept); may need soft update if it asserted「线索时间带」
- Span bar is schematic (relative plant/payoff markers), not a full multi-evidence chapter track

## Files changed

- `frontend/src/components/clues/clue-card.tsx` (new)
- `frontend/src/components/clues/clue-card.test.tsx` (new)
- `frontend/src/components/clues/clue-band.tsx`
- `frontend/src/components/clues/clue-evidence-panel.tsx`
- `frontend/src/components/clues/clue-workspace.tsx`
- `frontend/src/components/clues/clue-workspace.test.tsx`
- `.planning/phases/19-analysis-workbench-presentation-and-truth/19-04-SUMMARY.md` (this file)
