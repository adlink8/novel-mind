---
phase: 15-adaptive-hierarchical-retrieval-and-leaf-evidence-safety
status: passed
verified_at: 2026-07-16
requirements: [V08-RETR-01, V08-RETR-02, V08-RETR-03, V08-RETR-04, V08-RETR-05]
---

# Phase 15 Verification

**status:** `passed`

## Must-Haves vs Requirements

| ID | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| V08-RETR-01 | Deterministic local/arc/global/mixed router independent of candidates/provider | **pass** | `routing.py` + route matrix unit tests |
| V08-RETR-02 | Multi-level descent with collapsed/raw fallback under immutable scope | **pass** | `descent.py` + unit/PG descent tests |
| V08-RETR-03 | Final citations only from fresh Phase 07 leaf re-slice + hash | **pass** | `citations.py` + Unicode/tamper PG tests |
| V08-RETR-04 | Cutoff-first visibility; no future metadata leakage in results/traces/cache | **pass** | `candidate_reader.py` + adversarial PG |
| V08-RETR-05 | Default-off offline experiment; no Reader Chat/production cutover | **pass** | `experiments.py` + CLI + no-cutover + CI contract |

## Plan Completion

| Plan | Status | Summary |
| --- | --- | --- |
| 15-01 | complete | Contracts, router, visible loaders, cache isolation |
| 15-02 | complete | Descent/fallback, citations, retrieval manifests |
| 15-03 | complete | Offline experiment, adversarial, no-cutover, CI |

## Commands and Results

```text
cd backend
.\.venv\Scripts\python.exe -m pytest \
  tests/unit/narrative_memory/test_retrieval_routing.py \
  tests/unit/narrative_memory/test_retrieval_descent.py \
  tests/unit/narrative_memory/test_retrieval_manifests.py \
  tests/integration/narrative_memory/test_retrieval_candidates_pg.py \
  tests/integration/narrative_memory/test_retrieval_leaf_pg.py \
  tests/integration/narrative_memory/test_retrieval_experiment_pg.py \
  tests/integration/narrative_memory/test_retrieval_reader_chat_no_cutover.py \
  tests/integration/narrative_memory/test_retrieval_adversarial_pg.py \
  tests/adversarial/test_narrative_memory_retrieval_safety.py \
  tests/ci/test_narrative_memory_retrieval_contract.py -q

# 59 passed

.\.venv\Scripts\ruff.exe check \
  app/services/narrative_memory/retrieval_contracts.py \
  app/services/narrative_memory/routing.py \
  app/services/narrative_memory/candidate_reader.py \
  app/services/narrative_memory/descent.py \
  app/services/narrative_memory/citations.py \
  app/services/narrative_memory/retrieval_manifests.py \
  app/services/narrative_memory/experiments.py \
  app/config.py \
  scripts/run_hierarchical_retrieval_experiment.py \
  tests/unit/narrative_memory/test_retrieval_*.py \
  tests/integration/narrative_memory/test_retrieval_*.py \
  tests/adversarial/test_narrative_memory_retrieval_safety.py \
  tests/ci/test_narrative_memory_retrieval_contract.py
# All checks passed
```

## Safety Properties Confirmed

1. Router is pure and policy-versioned; unauthorized global wording never widens cutoff.
2. SQL visibility filters run before counts/ranking/cache/trace construction.
3. Sealed + Phase 14 `completed_candidate` required; no active/current pointer resolution.
4. Citations require fresh `Chapter.content[start:end]` Unicode re-slice and hash equality.
5. Experiment setting defaults false; CLI inert without enable; no FastAPI product route.
6. Reader Chat OpenAPI paths and production pointer table checksums unchanged after experiment.

## Residual Risks

- Seeded PG fixtures often use placeholder content hashes for non-leaf paths; citation success is proven on the dedicated Unicode leaf fixture (`test_retrieval_leaf_pg`).
- Ranking is deterministic stable-ID order only (no embedding/provider quality tuning) — Phase 17 owns quality qualification.
- Process-local visible cache is identity-revalidated but not multi-process shared (acceptable for offline experiment).

## Verdict

Phase 15 **passed**. Phase 16 unblocked (subject to user orchestration authorization already granted for 13–18).
