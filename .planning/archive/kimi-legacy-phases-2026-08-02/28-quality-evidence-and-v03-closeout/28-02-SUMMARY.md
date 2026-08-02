# Phase 28-02 Summary: Faithfulness, Cost, and Candidate Qualification

## Status

PARTIAL — the repository's signed benchmark fixture and novel 91 live SUT quality now qualify with comparable calibration/lineage evidence; Phase 28 remains open only for the real Browser/v0.3 residual and milestone closeout.

## Delivered

- Ran `scripts/run_rag_quality.py` against `evals/fixtures/rag-quality-benchmark.v1.json`.
- Result: `qualified`, `quality_comparable=true`, faithfulness 1.0, context recall@5 1.0, critical unsupported claim rate 0.0, and fixture cost `$0.009`.
- Formal novel 91 runs remain candidate-only and did not mutate any active pointer.
- Novel 91 live review: 100/100 semantic judgments accepted, 0 errors; the current review report estimates `$0.02223144` from the dated Gemini 3.5 Flash-Lite price snapshot. The full real SUT path separately estimates `$0.17057084`.
- Live calibration passed on 7 independent synthetic cases × 3 repeats with consistency `1.0` and critical false accept `0`.
- Real SUT report `evals/results/phase28/novel91-live-rag-quality.json` passed 100/100 cases with formal PostgreSQL BM25 Recall@5 `1.0`, accepted rate `0.96`, Judge consistency `0.9933`, zero errors/critical ambiguity, and `quality_comparable=true` after calibration/lineage matching.
- The bounded BM25 fallback now preserves punctuation/nested quotes; the 100-case read-only smoke verifies the evaluator resolves all signed evidence hashes.

## Cost evidence

- The generic `ai_usage_logs` implementation still writes `cost_usd=0.0`; this is not treated as actual cost. The review report keeps the token totals and applies the versioned price snapshot separately.
- The final SUT estimate is reproducible, but it is not an invoice reconciliation: the final report estimates `$0.17057084` from the dated snapshot and the generic usage logger still records `cost_usd=0.0`.

## Remaining gate

Calibration/lineage binding and comparable live SUT quality are now complete for the candidate evidence. Promotion remains prohibited until the owner-scoped Browser/v0.3 residual and the separate Phase 30 cutover audit are closed.
