# 17-01 Summary: Frozen Single-book Fixture and Policy

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-QUAL-01, V08-QUAL-02, V08-QUAL-03, V08-QUAL-05

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/services/narrative_memory/qualification_contracts.py` | Strict frozen fixture/policy/case/paired/metric/two-verdict schemas |
| `backend/app/services/narrative_memory/qualification_fixtures.py` | Freeze/hash, gold-leaf prevalidation, preflight gates |
| `backend/tests/fixtures/narrative_memory/qualification/single_book_v1.json` | Five-bucket frozen fixture |
| `backend/tests/fixtures/narrative_memory/qualification/policy_v1.json` | Predeclared thresholds + G/J isolation |
| `backend/tests/unit/narrative_memory/test_qualification_contracts.py` | Contract unit tests |
| `backend/tests/unit/narrative_memory/test_qualification_fixtures.py` | Freeze/preflight unit tests |
| `backend/tests/integration/narrative_memory/test_qualification_fixture_pg.py` | PG gold re-slice tests |

## Fixture / policy hashes (byte-stable)

- **fixture_checksum:** `d311ff26d0da1af2dd5407d6007c522413ab4012c2d18f4e8cb7f241c4513578`
- **policy_checksum:** `aab9a983391ca88f1c7be2c011af901092a6d965a63e8a1483940b447becd4ce`
- Bucket counts: local=1, cross_chapter_arc=1, whole_book_global=1, no_answer=1, spoiler=1
- Gold leaves re-slice to Chapter content_hash (PG integration)
- Result-derived fields rejected; one-field hash sensitivity proven
- Paired envelopes: identical common fields; strategy + cache_namespace only differ

## Verdict vocabulary

- Public: `qualified_candidate` | `blocked` only
- `qualification_kind=single_book_candidate`
- Mandatory no-promotion / no-consumer / no-v0.3-closure disclaimer

## Verification

```text
pytest tests/unit/narrative_memory/test_qualification_contracts.py \
       tests/unit/narrative_memory/test_qualification_fixtures.py \
       tests/integration/narrative_memory/test_qualification_fixture_pg.py -q
# 31 passed (28 unit + 3 PG)
```

Provider call count during freeze/preflight: **0**.
