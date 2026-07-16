# 16-03 Summary: Reuse Report, CLI, Adversarial + CI Contract

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-REUSE-01..04

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/services/narrative_memory/reuse_report.py` | Durable recomputation of observed / upper-bound / avoided / carry / cache |
| `backend/scripts/run_narrative_memory_rebuild.py` | Fixed `plan|status|execute|cancel|resume|report` CLI |
| `backend/tests/unit/narrative_memory/test_reuse_report.py` | 5 unit (formulas) |
| `backend/tests/integration/narrative_memory/test_reuse_report_pg.py` | 3 PG |
| `backend/tests/integration/narrative_memory/test_rebuild_cli_pg.py` | 3 PG subprocess |
| `backend/tests/adversarial/test_narrative_memory_rebuild_safety.py` | 8 static safety |
| `backend/tests/ci/test_narrative_memory_rebuild_contract.py` | 7 release contract |

## Economics formulas

| Label | Source |
| --- | --- |
| `observed_actual` | Phase 14 attempts/reservations/ledger settlements |
| `full_rebuild_upper_bound` | planned stage count × reservation envelope × optional price snapshot |
| `avoided_upper_bound` | `max(0, full − observed)` per metric (Decimal cost floor 0) |
| `carry_reuse` | count of rebuild items with `decision=carried` (never inferred from stages) |
| `exact_cache_reuse` | attempts with `status=cache_hit` only |

Independent recompute → identical `report_checksum`; append-only persist is idempotent on checksum.

## CLI contract

- Requires `--owner-id --novel-id --parent-version-id --target-version-id`
- Forbidden: promote / rollback / active / current / default / all-books / embedding / reader-chat
- `plan` is provider-free oracle; `execute` = carry + dirty stage materialize only
- Subprocess tests use `NOVELMIND_DATABASE_URL` + CI secrets (debug true for validator)

## Adversarial / CI

- Provider-free capability flags on graph/oracle/carry/executor/report
- AST bans reader_chat / litellm / openai / pointer promote APIs in Phase 16 modules
- Executor does not import builder_gateway
- No FastAPI product route for rebuild
- Migration revises `14membuild01`; models registered in `__init__.py`

## Code fixes during 16-03 green-up

1. `reuse_report` reservations keyed by `ledger_id` (not missing `run_id`)  
2. CLI subprocess env uses `NOVELMIND_*` prefix  

## Verification

```text
pytest tests/unit/narrative_memory/test_reuse_report.py \
       tests/integration/narrative_memory/test_reuse_report_pg.py \
       tests/integration/narrative_memory/test_rebuild_cli_pg.py \
       tests/adversarial/test_narrative_memory_rebuild_safety.py \
       tests/ci/test_narrative_memory_rebuild_contract.py -q
# 26 passed
```
