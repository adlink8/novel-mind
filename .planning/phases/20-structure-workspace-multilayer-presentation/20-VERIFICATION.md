# Phase 20 Verification — Structure Workspace

**Date:** 2026-07-16  
**Verdict:** **ACHIEVED (P0 product surface)** with known residuals  
**Promotion:** **NOT done** (forbidden by authorization)

## Must-haves

| # | Must-have | Result | Evidence |
|---|-----------|--------|----------|
| 1 | Structure is primary axis on `/analysis` | PASS | `page.tsx` + `structure-workspace-shell.tsx` |
| 2 | Facets scoped by selected chapter range | PASS | timeline client filter; rel through_chapter; clue range props |
| 3 | NM candidate always preview badge | PASS | API `candidate_preview`; UI 预览·未发布 |
| 4 | No NM → honest chapter fallback | PASS | banner + chapter tree |
| 5 | Read-only API explicit version_id | PASS | `api/narrative_memory.py` |
| 6 | through_chapter server filter on tree/claims | PASS | `structure_query.py` + unit tests |
| 7 | No promote endpoint | PASS | grep: no promote in narrative_memory product API |
| 8 | Phase 19 honesty preserved | PASS | relationship/clue suites still green in 20-03 run |
| 9 | Claims → source-links drill | PASS | structure-node-panel + tests |
| 10 | Docs updated after facts | PASS | IMPLEMENTATION-STATUS + architecture 02/09 |

## Automated tests (re-run 2026-07-16)

```
backend: pytest tests/unit/narrative_memory/test_structure_query.py → 13 passed
frontend: vitest --run structure → 14 passed
```

Prior 20-03 agent also reported analysis/relationships/clue regressions green and broader NM unit suite 161 passed.

## Residuals (not blockers for P0)

1. Sample novels often have **zero** NM rows — L3/L4 only after ops build via existing CLI
2. Timeline range filter is **client-side** (no server chapter_start..end API)
3. Superuser cross-owner NM scope may 404 (owner_id = current_user.id)
4. No NM promote / active pointer (by design)
5. Multi-chapter timeline uses density sampling (cap), not true L3 arc claims unless NM data exists

## Non-goals confirmed untouched

- Phase 08/09/11 worker algorithms not rewritten as aggregation parents
- Reader Chat cutover not performed
- `ChunkHierarchyNode.level` enum unchanged
