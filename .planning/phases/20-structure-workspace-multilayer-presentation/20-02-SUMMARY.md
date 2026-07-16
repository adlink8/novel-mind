# 20-02 SUMMARY — Structure Workspace shell (FE)

## Result

`/analysis` is now a **Structure Workspace**: left spine selects chapter/NM range; center facets (timeline / relationships / clues) scope to the selection. NM is always **candidate_preview** (badge 预览·未发布 / 叙事记忆候选 · 预览未发布). No promote UI. No backend worker changes.

## Files changed

| Path | Role |
|------|------|
| `frontend/src/lib/narrative-memory-api.ts` | Typed client for 20-01 routes + pickLatestPreviewVersion helpers |
| `frontend/src/components/structure/structure-types.ts` | Selection model + range/clue filter helpers |
| `frontend/src/components/structure/build-structure-tree.ts` | Chapter fallback tree + NM forest from child_ids |
| `frontend/src/components/structure/structure-tree.tsx` | Tree UI |
| `frontend/src/components/structure/structure-node-panel.tsx` | Selected node + NM badge + claims list |
| `frontend/src/components/structure/structure-workspace-shell.tsx` | Left spine + banners + center slots |
| `frontend/src/components/structure/structure-workspace.test.tsx` | Vitest: fallback, badges, selection→scope |
| `frontend/src/app/analysis/page.tsx` | Integrate shell; scope timeline/rel/clues |
| `frontend/src/components/clues/clue-workspace.tsx` | Optional `chapterStart`/`chapterEnd` client filter |
| `frontend/src/app/analysis/page.test.tsx` | Mock NM API; chapter_count fixture for ch.9 ordering |
| `frontend/src/app/analysis/relationships.test.tsx` | Mock NM + clue APIs for page shell |

## Behavior

1. **No NM versions** → chapter tree (`全书结构` + 第 N 章); banner `多层叙事记忆未就绪 · 当前为章节结构 + 单层分析`
2. **NM versions exist** → pick latest `version_id` for preview only; tree L4→L3→L2; badge `叙事记忆候选 · 预览未发布`
3. **Selection** → `chapterStart..chapterEnd` drives:
   - Timeline: client filter `narrative_chapter_number` (server has no range-start param — documented in page)
   - Relationships: `through_chapter = min(user, node.chapterEnd)`
   - Clues: plant/payoff (or narrative chapter) intersection filter
4. **Claims**: load on NM node select (read-only list; deep source-link drill deferred to 20-03)

## Tests run

```
cd frontend
npm test -- --run structure
npm test -- --run analysis structure
```

**40 passed** (structure 10 + page 14 + relationships 16).

`npx tsc --noEmit` still reports pre-existing errors in unrelated files (`relationship-graph.tsx`, `app-theme-sync.test.tsx`, e2e); none in Phase 20-02 paths.

## Residual risks / follow-ups (20-03)

- Timeline multi-chapter density/aggregation not done
- Claims → source-links drill not wired in UI
- Structure tree does not re-fetch NM when user changes spoiler through_chapter mid-session (initial load only)
- Superuser cross-owner NM 404 (backend 20-01 residual)

## Non-goals confirmed

- No NM promote / active pointer
- No backend worker / facet extraction changes
- Route remains `/analysis`
