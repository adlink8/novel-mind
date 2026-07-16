# 15-03 Summary: Offline Experiment, Adversarial Safety, No-Cutover

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-RETR-03, V08-RETR-04, V08-RETR-05

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/config.py` | `narrative_memory_retrieval_experiment_enabled: bool = False` |
| `backend/app/services/narrative_memory/experiments.py` | Fixed offline runner; `completed\|blocked` only |
| `backend/scripts/run_hierarchical_retrieval_experiment.py` | Default-off CLI; explicit version/cutoff/question |
| `backend/tests/integration/narrative_memory/test_retrieval_experiment_pg.py` | Enable/disable/determinism/CLI |
| `backend/tests/integration/narrative_memory/test_retrieval_reader_chat_no_cutover.py` | OpenAPI + pointer snapshot before/after |
| `backend/tests/integration/narrative_memory/test_retrieval_adversarial_pg.py` | Future-metadata / IDOR / cache / tamper PG |
| `backend/tests/adversarial/test_narrative_memory_retrieval_safety.py` | Static adversarial gates |
| `backend/tests/ci/test_narrative_memory_retrieval_contract.py` | AST import/call + route-surface contract |

## Experiment boundary

- Default `False`; CLI without enable → exit 2 `experiment_disabled`, no side effects.
- Requires owner/novel/explicit version/frozen question/cutoff snapshot hash.
- No FastAPI product route, no provider call, no pointer/promotion, no Phase 17 qualification.
- Report embeds `query_hash` only (never raw question text); `qualification=null`, `promotion=null`.

## Adversarial evidence

- Future labels/keys (`FUTURE_*`, `arc-fut`, `ch-3`, `claim-future`) absent from full experiment report.
- Cross-owner version selection → `blocked` / `candidate_ineligible`.
- Cache identity differs across cutoff hashes; public peek omits raw key material.
- Corrupt leaf lineage → zero citations + blocked when minimum required.

## No-cutover evidence

- Reader Chat OpenAPI path set byte-identical before/after real experiment execution.
- All `narrative_memory*` tables unchanged set (no pointer/promotion tables).
- Production active-pointer table row checksums unchanged.
- Static scan: Phase 15 modules do not import reader_chat / provider / pointer setters.

## Verification

```text
pytest tests/integration/narrative_memory/test_retrieval_experiment_pg.py \
       tests/integration/narrative_memory/test_retrieval_reader_chat_no_cutover.py \
       tests/integration/narrative_memory/test_retrieval_adversarial_pg.py \
       tests/adversarial/test_narrative_memory_retrieval_safety.py \
       tests/ci/test_narrative_memory_retrieval_contract.py -q
# all passed
ruff clean
```
