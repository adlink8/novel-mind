# 16-02 Summary: Carry-forward and Dirty-only Phase 14 Stages

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-REUSE-01, V08-REUSE-02, V08-REUSE-03

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/services/narrative_memory/carry_forward.py` | Exact semantic copy + target leaf rebind |
| `backend/app/services/narrative_memory/rebuild_executor.py` | Frozen dirty stage mask; carry then Phase 14 `ensure_stages` only for dirty |
| `backend/tests/unit/narrative_memory/test_carry_forward.py` | 2 unit (provider-free + mask) |
| `backend/tests/integration/narrative_memory/test_carry_forward_pg.py` | 5 PG |
| `backend/tests/integration/narrative_memory/test_local_rebuild_pg.py` | 4 PG |

## Semantic vs lineage checksum rules

| Component | Rule |
| --- | --- |
| Node `content_checksum` | Must equal parent after carry |
| Claim typed payload / uncertainty / confidence / visibility | Byte-identical after carry |
| Claim composite `claim_checksum` | May change: includes package-local `source_keys` not stored on link rows |
| Source links | Fresh target hierarchy build/id, evidence node, offsets, snapshot |
| Edges | Rebuilt between carried node keys only; `EdgeType` enum coerced |

## Target leaf mapping

- Match parent link fingerprint → exactly one target evidence leaf  
  (`chapter_id`, `source_start`, `source_end`, `content_hash`)
- Ambiguous/missing → `CarryForwardError` before partial write
- Idempotent retry: existing target carried keys with matching node checksums → no-op

## Stage / call matrix

| Fixture | Carry nodes | Phase 14 stages | Provider attempts |
| --- | --- | --- | --- |
| No-change | all semantic assets | **0** | **0** |
| One-chapter edit (force_full rebuild) | only assets still `decision=carried` (often none when evidence remap expands suffix) | dirty stage keys only | 0 until worker runs |
| Stale plan checksum | rejected | no run | 0 |
| Cross-scope owner | rejected | no run | 0 |

**Invariant:** `decision=carried` never creates Phase 14 stage/call/reservation rows.  
**Invariant:** carried `boundary_plan` is not force-injected as `arc_volume_plan:book` dirty stage.

## Parent immutability

- Parent node content checksum snapshot equal before/after materialize
- No pointer / production authority mutation in executor or carry modules

## Code fixes during 16-02 green-up

1. `MemoryEdge` / claim reconstruction via enum-safe paths  
2. Leaf map helper uses `_MappedLeaf` (no empty `claim_key`)  
3. Dirty mask does not force-add boundary stage when boundary is carried  

## Verification

```text
pytest tests/unit/narrative_memory/test_carry_forward.py \
       tests/integration/narrative_memory/test_carry_forward_pg.py \
       tests/integration/narrative_memory/test_local_rebuild_pg.py -q
# 11 passed
```
