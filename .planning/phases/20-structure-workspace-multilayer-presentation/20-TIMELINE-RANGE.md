# Timeline server-side chapter range (structure scope)

**Date:** 2026-07-17  
**Status:** Implemented (params optional; no promote)  
**Commit intent:** `feat(timeline): server-side chapter range filter for structure scope`

## Problem

Structure Workspace filtered timeline events **client-side** by selected node
`chapterStart..chapterEnd`. Large novels paid full payload cost; spoiler and
structure bounds were applied in different layers.

## Change

### Backend

| Surface | Change |
|---------|--------|
| `GET /api/timeline/{novel_id}` | Optional query `chapter_start`, `chapter_end` (`ge=1`) |
| `GET /api/timeline/{novel_id}/versions/{version_id}` | Same optional params |
| `build_version_view(...)` | Optional `chapter_start` / `chapter_end` |
| `effective_narrative_bounds(...)` | Pure combine helper (unit-tested) |

**Combine rules (inclusive narrative_chapter_number):**

1. Spoiler / full-book / running-candidate logic unchanged.
2. When spoiler is closed and cutoff is missing → hide all (prior behavior).
3. Upper bound = spoiler upper if any; if `chapter_end` set → `min(chapter_end, spoiler_upper)` (or just `chapter_end` when spoiler open).
4. Lower bound = `chapter_start` when set.
5. Omitting both params → identical to pre-change callers.

### Frontend

| Surface | Change |
|---------|--------|
| `TimelineQuery` | `chapter_start?`, `chapter_end?` |
| Analysis `loadTimeline` | Passes selected structure node range via `selectedNodeRef` (poll-safe) |
| Structure node select | Re-fetches timeline with explicit range |
| Client `eventInChapterRange` | Kept as defense-in-depth for densify / people chips |

Progressive live runs still request `full_book` while active; structure range is
an additional narrow filter only when a node is selected. No selection → no
range params (full progressive envelope).

## Tests

- `backend/tests/unit/timeline/test_chapter_range.py` — bounds + route query params
- `backend/tests/integration/timeline/test_spoilers.py` — range ∩ spoiler integration

## Out of scope

- Promote / NM publish
- Removing multi-chapter densify
- Relationships/clues range (already use through_chapter fold)
