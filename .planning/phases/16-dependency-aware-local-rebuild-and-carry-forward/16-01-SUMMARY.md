# 16-01 Summary: Rebuild Authority, Dependency Graph, Change Oracle

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-REUSE-01, V08-REUSE-03

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/models/narrative_memory_rebuild.py` | Append-only rebuild plans/items + reuse reports (candidate-only) |
| `backend/migrations/versions/16_narrative_memory_rebuild_authority.py` | Alembic `16memrebuild01` ← `14membuild01` |
| `backend/app/services/narrative_memory/rebuild_contracts.py` | Frozen graph/change/decision DTOs + `stable_checksum` |
| `backend/app/services/narrative_memory/dependency_graph.py` | Lossless parent/target graph reconstruction |
| `backend/app/services/narrative_memory/change_oracle.py` | Conservative dirty closure + immutable plan materialization |
| `backend/tests/unit/narrative_memory/test_dependency_graph.py` | 10 unit |
| `backend/tests/unit/narrative_memory/test_change_oracle.py` | 8 unit |
| `backend/tests/integration/narrative_memory/test_rebuild_authority_pg.py` | 3 PG |
| `backend/tests/integration/narrative_memory/test_change_oracle_pg.py` | 4 PG |

## Migration / tables

- Head: `16memrebuild01` (single head after upgrade)
- Tables: `narrative_memory_rebuild_plans`, `narrative_memory_rebuild_items`, `narrative_memory_reuse_reports`
- No active pointer / promotion / current-version / provider tables
- Round-trip: upgrade → downgrade to `14membuild01` → upgrade verified

## Graph identity (excludes DB IDs / insertion order / retrieval telemetry)

- Source chapters by stable chapter id + separately compared narrative order
- Evidence by chapter id + code-point offsets + content hash leaf fingerprint
- Semantic nodes by `node_key` / kind / chapter range / content checksum
- Closed decisions: `dirty` | `carried` | `stale_blocked` | `not_applicable`

## Change / propagation matrix (representative)

| Fixture | Dirty seed | Propagated |
| --- | --- | --- |
| No-change | none | all semantic assets `carried` |
| Stable chapter edit | edited source + evidence | chapter_state + containing arc + global |
| Insert/delete/reorder / MAPPING_UNPROVEN | earliest affected | conservative suffix + global (monotonic) |
| Cross-scope / unsealed parent | rejected | no plan persisted |

## Zero provider / no-pointer

- `graph_has_provider_capability()` / `oracle_has_provider_capability()` → False
- PG observer: zero `narrative_memory_build_model_call_attempts` on plan path
- Static: no reader_chat / litellm / openai / pointer tables in oracle/graph modules

## Verification

```text
pytest tests/unit/narrative_memory/test_dependency_graph.py \
       tests/unit/narrative_memory/test_change_oracle.py \
       tests/integration/narrative_memory/test_rebuild_authority_pg.py \
       tests/integration/narrative_memory/test_change_oracle_pg.py -q
# 25 passed
```
