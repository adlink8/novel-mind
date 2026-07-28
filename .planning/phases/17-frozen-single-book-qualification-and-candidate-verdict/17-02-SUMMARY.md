# 17-02 Summary: Paired Runner, Metrics, Fail-closed Verdict

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-QUAL-02, V08-QUAL-03, V08-QUAL-04, V08-QUAL-05

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/services/narrative_memory/qualification_baseline.py` | Cutoff-first Phase 07 leaf/raw baseline |
| `backend/app/services/narrative_memory/qualification_runner.py` | Paired hierarchical vs baseline runner (deterministic transports) |
| `backend/app/services/narrative_memory/qualification_metrics.py` | Complete MetricCell aggregation |
| `backend/app/services/narrative_memory/qualification_verdict.py` | Pure threshold evaluator (two verdicts) |
| Unit + adversarial + PG runner tests | Fairness / safety / completeness |

## Paired-field equality

Shared: source, hierarchy, cutoff, query, top-k/leaves/rerank, G/J lineage, prompt/schema/decoding, timeout/retry, token/cost ceilings, price snapshot, metric version, fixture/policy checksums.  
Differ: `strategy` ∈ {hierarchical_candidate, leaf_raw_baseline}, strategy-scoped `cache_namespace`.

## Metric inventory (required)

leaf_recall_at_k, reciprocal_rank, ndcg_at_k, route_hit, fallback_rate, citation_accept_rate, invalid_citation_rate, spoiler_leakage, critical_unsupported, faithfulness_mean, relevance_mean, end_to_end_latency p50/p95, calls/tokens/cost, cache_hit_rate, Phase 16 reuse rebuilt/carried/stale + observed_actual / full_rebuild_upper_bound / avoided_upper_bound costs.  
Conditional: no_answer_abstention, false_answer_rate when bucket present.

## Gate order

1. preflight / frozen integrity  
2. scope/lineage  
3–6. structure/build/retrieval/reuse  
7. metric completeness  
8. zero-tolerance spoiler/unsupported  
9–10. absolute + relative baseline thresholds  
11. pointer before/after  

Judge scores never override deterministic hard gates.

## Verification

```text
pytest tests/unit/narrative_memory/test_qualification_metrics.py \
       tests/unit/narrative_memory/test_qualification_verdict.py \
       tests/adversarial/test_narrative_memory_qualification.py \
       tests/integration/narrative_memory/test_qualification_runner_pg.py -q
# 19 passed
```

Default deterministic run on frozen fixture → `qualified_candidate`. Spoiler leak / no-answer hallucination / pointer mismatch → `blocked`.
