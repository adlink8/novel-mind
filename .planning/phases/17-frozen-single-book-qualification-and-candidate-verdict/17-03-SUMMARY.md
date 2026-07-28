# 17-03 Summary: Authority, Fresh Verifier, Fixed CLI

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-QUAL-03, V08-QUAL-04, V08-QUAL-05

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/models/narrative_memory_qualification.py` | Append-only run/case/report ORM |
| `backend/migrations/versions/17_narrative_memory_qualification.py` | Alembic `17memqual01` ← `16memrebuild01` |
| `backend/app/services/narrative_memory/qualification_repository.py` | Scoped insert + sealed report |
| `backend/app/services/narrative_memory/qualification_verifier.py` | Fresh pointer snapshot + lineage recompute |
| `backend/scripts/run_narrative_memory_qualification.py` | Fixed CLI, two-verdict + output_digest |
| PG authority/verifier/command + CI contract tests | Independence and no-capability proofs |

## Migration head

- **Revision:** `17memqual01`
- **Down:** `16memrebuild01`
- Tables: `narrative_memory_qualification_runs`, `_case_results`, `_reports`
- Append-only UPDATE/DELETE triggers; no active/current/promotion selectors
- Verdict CHECK: `qualified_candidate|blocked` only; kind=`single_book_candidate`

## Fixed command

```text
python scripts/run_narrative_memory_qualification.py \
  --owner-id N --novel-id N --version-id N \
  --fixture PATH --policy PATH --acknowledge-budget [--dry-run]
```

- Exit 0 → `qualified_candidate`
- Exit 2 → `blocked` (qualification)
- Exit 1 → command/config failure
- `output_digest` = SHA-256 of canonical payload excluding itself
- Forbidden options: promote/rollback/active/current/reader-chat/cutover

## Fresh observer

- Complete production pointer snapshot (chunk/timeline/clue/active_baselines + schema-discovered selector-like tables)
- Excludes qualification audit rows from pointer digest
- Before/after byte equality required; unknown narrative_memory selector tables block
- No repair capability

## Verification

```text
pytest tests/integration/narrative_memory/test_qualification_authority_pg.py \
       tests/integration/narrative_memory/test_qualification_verifier_pg.py \
       tests/integration/narrative_memory/test_qualification_command_pg.py \
       tests/ci/test_narrative_memory_qualification_contract.py -q
# 13 passed
```

Full Phase 17 suite: **63 passed**.
