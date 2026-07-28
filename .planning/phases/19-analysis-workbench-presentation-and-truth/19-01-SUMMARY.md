# 19-01 SUMMARY — Truth layer & API honesty

## Objective

Make relationship and clue projection contracts honest so UI can distinguish accepted fact vs provisional co-occurrence, and clue list titles/spans are product-grade without a full KG retrain.

## Steps done

### Task 1: Relationship edge honesty
- Added `RelationshipEdgeKind` (`accepted_observation` | `provisional_cooccurrence`) and graph label `cooccur` via `RelationshipGraphEdgeLabel`.
- Graph edges expose optional `edge_kind` + `suggested_type` (heuristic only).
- Provisional timeline edges:
  - `relation_type=cooccur` (never ally/enemy as primary claim)
  - `suggested_type` carries heuristic fiction label
  - `evidence_preview` says 共现 + 「非已确认关系」, not 「同盟×N」 as fact
- Default graph: accepted observations only when any exist.
- New query/API param `include_provisional=true` layers provisional co-occur for pairs without accepted edges.
- When accepted empty: provisional surface still available, honesty-typed.
- Caps/quotas and `_degradation_mode` retained.
- `timeline_kg_backfill.py`: documented as seed/ops path; metadata `source=timeline_kg_backfill`, `seed_mode=True` on run config and judgment outputs.

### Task 2: Clue title & span fields
- Worker: `build_machine_clue_title()` — rationale first line cleaned, else `伏笔·第N章` + short stem; **never** raw `cue_unit.text[:80]` as sole title.
- Raw cue kept in `package_snapshot.cue_excerpt`.
- `ClueVisibleItem`: optional `first_cue_chapter`, `payoff_chapter` (spoiler-safe null when beyond cutoff), `summary` short line.
- `frontend/src/lib/clue-api.ts` + `api.ts` types aligned (`edge_kind`, `suggested_type`, `include_provisional`, clue span fields).

### Task 3: Tests
- New: `tests/unit/relationships/test_graph_honesty.py`
- New: `tests/unit/clues/test_title_honesty.py`
- Full suite: `tests/unit/relationships` + `tests/unit/clues`

## Verification

```
cd D:\ADLINK\Myproject\novel-mind\backend
.\.venv\Scripts\python.exe -m pytest tests/unit/relationships tests/unit/clues -q --tb=line
```

**Result: 94 passed** (0.52s)

## API notes (non-breaking additive)

| Surface | Change |
|--------|--------|
| `GET /relationships/{id}/graph` | New optional `include_provisional` (default false). Response edges may include `edge_kind`, `suggested_type`; `relation_type` may be `cooccur`. |
| `available_relation_types` | May include `cooccur` when provisional surface is active. |
| Clue list items | Additive optional fields: `first_cue_chapter`, `payoff_chapter`, `summary`. |

Existing clients ignoring new fields continue to work. Clients that assume provisional edges are `ally`/`enemy` must read `edge_kind` / `cooccur`.

## Residual risks

- Existing DB machine clues still have old long titles until re-analyzed; only new persists use short titles.
- Seed backfill still writes **accepted** KG judgments when operators run it — honesty depends on metadata + operator discipline, not automatic demotion.
- `include_provisional` dual-layer UX is API-ready; Phase 19 UI polish may still style `cooccur` vs accepted.
- Timeline swimlanes UI is **out of scope** (19-02).

## Files changed

- `backend/app/schemas/relationship.py`
- `backend/app/services/relationships/query.py`
- `backend/app/services/relationships/timeline_kg_backfill.py`
- `backend/app/api/relationships.py`
- `backend/app/schemas/clue.py`
- `backend/app/services/clues/worker.py`
- `backend/app/services/clues/query.py`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/clue-api.ts`
- `backend/tests/unit/relationships/test_graph_honesty.py` (new)
- `backend/tests/unit/clues/test_title_honesty.py` (new)
- `.planning/phases/19-analysis-workbench-presentation-and-truth/19-01-SUMMARY.md` (this file)
