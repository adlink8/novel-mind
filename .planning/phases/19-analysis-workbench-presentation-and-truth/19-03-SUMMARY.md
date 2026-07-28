# 19-03 SUMMARY — Relationship honest edges & ego presentation

## Objective

Make the relationship graph **honest** and **ego-first**: accepted solid typed edges; provisional dashed gray「共现」; default prefer accepted; opt-in provisional layer with banner and non-assertive evidence copy.

## Steps done

### Task 1: Wire edge_kind from API + graph styles
- `RelationshipWorkspace` passes `include_provisional: true` only when the user opts in (default omit / not true).
- When the API returns only provisional edges (accepted empty), UI still renders them with honesty styling and banner.
- Cytoscape:
  - Accepted: solid stroke + type colors (`EDGE_COLORS`).
  - Provisional (`edge.provisional` / `relationType=cooccur`): dashed slate `#94a3b8`, label「共现」.
- `displaySlice` ranks **accepted first**, then evidence count, so display caps prefer accepted observations.
- Companion list marks provisional as「共现 · 临时共现」(optional suggested_type as 提示 only).
- Canvas legend: 实线边色 vs 灰色虚线=临时共现.

### Task 2: Controls & evidence panel
- Controls checkbox「显示临时共现」(`data-testid=relationship-include-provisional`).
- Banner when any provisional edge is visible (`relationship-provisional-banner`):
  - only-provisional copy vs mixed accepted+provisional copy.
- Evidence panel: provisional →「临时共现」header, non-assertive note (`relationship-evidence-provisional-note`), suggested type as 启发式提示 only; no confidence-as-fact / 机器推断 as primary claim.

### Task 3: Tests
- Default fetch does not send `include_provisional=true`.
- Toggle refetches with `include_provisional=true` and shows banner.
- Only-provisional envelope shows honesty banner + companion 临时共现.
- Evidence panel non-assertive for provisional edges.
- Graph unit: provisional labeled 共现, legend mentions 灰色虚线.

## Verification

```
cd D:\ADLINK\Myproject\novel-mind\frontend
npx vitest run src/app/analysis/relationships.test.tsx --reporter=dot
```

**Result: 16 passed** (3.16s)

## Must-haves check

| Truth | Status |
|-------|--------|
| Accepted vs provisional visually distinct (stroke/color/label) | Done — solid typed vs dashed gray「共现」 |
| Default UX does not present guessed types as confirmed without banner/opt-in | Done — toggle default off; banner when provisional visible; evidence honesty |
| Hub concentric layout retained | Unchanged |

## Residual risks

- Evidence API may still return typed `relation_type` for provisional observation ids; panel ignores fiction assertiveness via `edge_kind` / `cooccur`.
- Dual-layer pairs (accepted + provisional for same pair) both can appear when opted in; visual distinction relies on style classes.
- No E2E screenshot assert for dashed stroke (unit/integration only).

## Files changed

- `frontend/src/components/relationships/relationship-graph.tsx`
- `frontend/src/components/relationships/relationship-workspace.tsx`
- `frontend/src/components/relationships/relationship-controls.tsx`
- `frontend/src/components/relationships/relationship-evidence-panel.tsx`
- `frontend/src/app/analysis/relationships.test.tsx`
- `.planning/phases/19-analysis-workbench-presentation-and-truth/19-03-SUMMARY.md` (this file)

Note: `frontend/src/lib/api.ts` already had `edge_kind`, `suggested_type`, `include_provisional`, `cooccur` from 19-01 — no further API type edits required.
