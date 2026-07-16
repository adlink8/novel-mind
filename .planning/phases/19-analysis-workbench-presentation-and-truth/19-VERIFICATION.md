# Phase 19 Verification

**Status:** PARTIAL → product must-haves covered by unit/vitest; real browser e2e not re-run in this orchestration  
**Date:** 2026-07-16  
**Cursor:** plans 19-01..04 SUMMARYs present

## Must-haves

| ID | Truth | Evidence |
|----|--------|----------|
| V09-TRUTH-01 | Provisional vs accepted distinguishable in API | 19-01: `edge_kind`, `cooccur`, `include_provisional` |
| V09-TRUTH-02 | Default prefers accepted | query default exclude provisional when accepted exist |
| V09-TRUTH-03 | Clue titles not raw cue[:80] | worker short title + list span fields |
| V09-TL-01 | Chapter X + type Y lanes | timeline-chart swimlanes, not index%4 primary |
| V09-TL-02 | Chapter-oriented axis | `第 N 章` labels |
| V09-REL-01 | Visual honesty accepted vs provisional | solid typed vs dashed 共现 |
| V09-REL-02 | Default not flooded by guessed types | include_provisional opt-in |
| V09-CLUE-01 | Plant→payoff span cards | ClueCard vertical list |
| V09-CLUE-02 | Short API titles | uses title/summary fields |

## Automated tests (orchestrator re-run)

```
backend: pytest tests/unit/relationships tests/unit/clues → 94 passed
frontend: vitest timeline-chart + analysis page + relationships + clues → 56 passed
```

## Residual

- Existing DB machine_clue titles need re-run to refresh short titles
- Seed backfill observations may still exist as accepted with metadata
- No full Playwright re-qualification this pass
- Unrelated WIP (eval/novels/config) not in phase scope

## Verdict

**Implement-complete for Phase 19 plans 01–04** under unit/component gates. Recommend human UAT on `/analysis` with slime after backend restart.
