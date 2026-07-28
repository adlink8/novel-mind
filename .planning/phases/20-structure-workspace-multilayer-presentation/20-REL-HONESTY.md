# Relationship honesty (seed / transition) — 2026-07-17

## Scope

Minimal product honesty for seed/backfill and lifecycle transitions. **No NM promote.**

## What the product already has (kept)

| Signal | Surface | Meaning |
|--------|---------|---------|
| `edge_kind=provisional_cooccurrence` + `relation_type=cooccur` | Graph dashed slate, list「临时共现」, evidence panel note | Timeline co-occurrence only — **not** accepted ally/enemy fact (Phase 19) |
| `suggested_type` | List meta / evidence copy only | Heuristic clue; never primary accepted label |
| Empty accepted → provisional default | Query service + UI banner when only cooccur edges | Progressive UI when Phase 09 observations absent |

## Seed / ops backfill (not on graph API)

- Ops path: `backend/app/services/relationships/timeline_kg_backfill.py`
- Metadata lives on KG package/config (`source=timeline_kg_backfill`, `seed_mode=True`) and judgment rationale; **not** projected as `intake_kind` on `RelationshipGraphEdge`.
- Worker seed judgments use empty `risk_flags` so gates can accept; seed is **not** reliably recoverable from accepted observations alone without judgment joins.
- **Product rule:** Prefer provisional co-occurrence for empty graphs. Run timeline KG backfill only deliberately for ops intake; treat resulting accepted edges as **seeded establish** facts until real LLM evolution exists.

## This slice (FE)

- When API `transition` is `change` or `end` (already on graph edges), UI shows a **transition badge** (companion list + evidence panel) and appends `· 变化` / `· 结束` to type labels.
- Default `establish` stays silent (most backfill/seed and first observations).
- `end` edges are normally folded off the active graph server-side; badge still handles them if present.

## Deferred

- Graph `intake_kind` / seed badge via judgment metadata join
- Evolution quality (change/end chains in sample data)
- NM promote

## Tests

- `frontend/src/components/relationships/relationship-honesty.test.ts`
- `frontend/src/app/analysis/relationships.test.tsx` (transition badge cases)
