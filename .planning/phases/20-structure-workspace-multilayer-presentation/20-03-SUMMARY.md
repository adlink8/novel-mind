# 20-03 SUMMARY — Scope-bound facets, claims drill, honesty polish

## Result

Structure selection now drives **density-aware** facet projections; NM claims drill to source-links with honest empties; spoiler `through_chapter` re-fetches the NM tree; Phase 19 honesty (provisional / edge_kind / plant→payoff) preserved. Still **no** NM promote.

## Files changed

| Path | Role |
|------|------|
| `frontend/src/components/structure/structure-types.ts` | `isMultiChapterScope`, `countEventsByChapter`, `densifyTimelineForMultiChapter` |
| `frontend/src/components/structure/build-structure-tree.ts` | `findTreeNodeByNmId` / `findTreeNodeById` for selection preserve |
| `frontend/src/components/structure/structure-workspace-shell.tsx` | Scope label always in center column near facet tabs; claims/source-link props |
| `frontend/src/components/structure/structure-node-panel.tsx` | Clickable claims → source-links; chapter/hash/offset; reader link; honest empties |
| `frontend/src/app/analysis/page.tsx` | Multi-chapter density note + cap; claims drill; NM tree re-fetch on through_chapter |
| `frontend/src/components/structure/structure-workspace.test.tsx` | Scope label, densify filter, claims/source-links honesty |
| `backend/tests/unit/narrative_memory/test_structure_query.py` | Multi-chapter tree assembly still cutoff-safe |

## Behavior

1. **Scope label** — 「视图范围：第 A–B 章」 always rendered in the center column above facet tabs (also when tree collapsed).
2. **Single chapter** — full Phase 19 timeline swimlane (no density banner/cap).
3. **Multi-chapter (span > 1)** — density banner with per-chapter counts; proportional sample cap (120) with 「还有 N 条」 when truncated; relationships still fold at `min(user, node.chapterEnd)`; clues still client-filter plant/payoff intersection.
4. **Claims drill** — select NM claim → `getSourceLinks`; show chapter + offset + short hash; `/novels/{id}?chapter=&start=` when novelId present; empty → 「无叶子证据链接」 / 「此节点暂无可见声明」.
5. **through_chapter change** — when NM is active, re-`getTree` with new cutoff and preserve selection when node still visible.
6. **Regression** — no changes to relationship provisional toggle / edge_kind display / clue plant→payoff cards; tree collapse does not unmount facet parent state.

## Tests run

```
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit/narrative_memory -q --tb=line
# 161 passed (structure_query 13)

cd ../frontend
npm test -- --run structure
# 14 passed
npm test -- --run analysis
# 30 passed (page 14 + relationships 16)
npm test -- --run relationships clue
# 45 passed (honesty regression surfaces)
```

**Structure + analysis FE: 44 passed** (14 + 30). **NM BE unit: 161 passed**.

## Residual risks / follow-ups

- Timeline multi-chapter density is client-only (server still lacks chapter_start..end params).
- Source-links API is node-scoped; claim filter is client-side.
- Superuser cross-owner NM 404 (20-01 residual) unchanged.
- No promote / no worker rewrites.

## Non-goals confirmed

- No NM promotion / active pointer
- No backend worker / facet extraction algorithm changes for 08/09/11
- Phase 19 honesty retained
