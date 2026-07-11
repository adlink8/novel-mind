---
phase: 05-narrative-knowledge-unit-layer
plan: 05-04-frozen-evaluation-canary-and-promotion
type: implementation
wave: 4
depends_on: [05-03-candidate-index-and-hybrid-retrieval]
files_modified:
  - backend/app/services/knowledge_units/eval.py
  - backend/app/services/knowledge_units/promotion.py
  - backend/scripts/run_narrative_unit_eval.py
  - backend/scripts/promote_narrative_unit_index.py
  - backend/evals/narrative_units_fiction.json
  - backend/evals/narrative_units_history.json
  - backend/tests/test_knowledge_unit_eval.py
  - backend/tests/test_knowledge_unit_promotion.py
  - .planning/phases/05-narrative-knowledge-unit-layer/05-UAT.md
autonomous: false
requirements_addressed: [REQ-NU-05, REQ-NU-06, REQ-NU-07]
truths:
  - "D-07: frozen A/B, faithfulness, isolation, latency, reconcile, and canary gate promotion."
  - "D-08: promotion uses exact checksums and a prepare/commit journal."
  - "D-10: optional LLM judging occurs only after deterministic gates."
---

# 05-04 - Frozen Evaluation, Canary, and Promotion

## Objective

Prove candidate retrieval quality on fiction and history, then promote only an exact, approved candidate through a recoverable journal.

## Steps

1. Create versioned dev and frozen fixtures for fiction/history with query, gold unit/evidence IDs, no-answer, literal-name, alias, conflict, temporal, causality, and cross-owner cases. Freeze hashes; tuning may change dev but not frozen data.
2. Evaluate chunks, units, and hybrid using Recall@5, MRR@5, NDCG@5, evidence faithfulness, zero-result/fallback rate, and latency p50/p95. Report unit-only separately; hybrid must not regress from raw baseline.
3. Reuse Phase 03 metric primitives where contracts are sound, but do not claim closure of the broader Phase 03 gap from synthetic Phase 05 fixtures.
4. Add optional calibrated LLM faithfulness judging after deterministic checks. Unavailable credentials produce `blocked`; they do not fabricate a pass.
5. Implement canary reporting on an approved query sample. Require zero critical wrong/stale/cross-owner results and exact candidate collection reconcile.
6. Implement `prepare` and `commit` promotion journal. Prepare binds candidate collection, manifest, eval, canary, config, and checksum; commit accepts only that journal ID/checksum and atomically updates the PostgreSQL active pointer.
7. Add failure-injection tests at every prepare/commit boundary and prove the previous active pointer remains valid. Require explicit human checkpoint before first real cutover and record it in `05-UAT.md`.
8. Test, Fix, and Confirm: run deterministic fixtures, candidate dry-run, blocked LLM path, and journal tests.

## Must-Haves

- Frozen test is never used to tune weights or rewritten by feedback.
- No promotion without exact eval/canary/manifest lineage and human approval for first cutover.
- Failed commit leaves prior DB/index/pointer state usable.
- Candidate IDs cannot be supplied by ordinary search users.
- Covers D-07, D-08, D-10 and REQ-NU-05/06/07.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_unit_eval.py tests/test_knowledge_unit_promotion.py -v
python scripts/run_narrative_unit_eval.py --fixture evals/narrative_units_fiction.json --dry-run
python scripts/run_narrative_unit_eval.py --fixture evals/narrative_units_history.json --dry-run
python scripts/promote_narrative_unit_index.py --candidate TEST --prepare --dry-run
```
