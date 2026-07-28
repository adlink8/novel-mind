# 06-04 Summary — SUT Scoring, Policy Arbiter, Durable Worker & Legacy Adapter

**Status:** COMPLETE  
**Date:** 2026-07-12  
**Plan:** `.planning/phases/06-automated-quality-ci/06-04-PLAN.md`  
**Decisions:** D-06, D-07, D-08

## What Was Done

### Slice 1 — SUT scoring and deterministic policy

- `backend/app/services/rag_quality.py`
  - Consumes signed frozen fixtures + calibrated Judge lineage from 06-03
  - Stubbable SUT `retrieve` + `answer` + answer Judge (offline stubs for unit/contract)
  - Deterministic metrics: `context_precision`, `context_recall@5`, claim support / critical unsupported rate
  - Bootstrap 95% lower bound for answer faithfulness; 3-repeat verdict consistency
  - Deterministic arbiter locks D-08 thresholds; missing policy/p95/baseline/health/lineage → fail closed
  - Terminal statuses: `passed`/`qualified` usable for baseline; fail paths set `quality_comparable=false`, `metrics=null`
  - Exceptions never converted to zero quality scores
- Policy: `backend/evals/rag-quality-policy.v1.yml` (D-08 thresholds + p95 budgets)
- Prompt: `backend/prompts/rag_answer_judge.v1.txt`
- Tests: `tests/test_rag_quality_scoring.py` (16), `tests/live/test_rag_quality_dual_model.py` (2)

### Slice 2 — Durable worker and Eval API migration

- `backend/app/services/rag_quality_worker.py`
  - Lease / heartbeat / checkpoint / resume / cancel
  - Stage cache idempotency: crash resume does not re-issue model calls
  - Cross-owner access denied (404)
- `backend/app/services/eval_service.py`
  - Legacy retrieval path preserved; appends `job_id`/`status`/`quality_comparable`/`deprecation`
  - `quality_mode` raises instead of swallowing item exceptions into 0 scores
  - `classify_legacy_gold`: provable id→hash migrate; else quarantine
- `backend/app/api/eval.py`
  - Legacy POST/GET/list/PATCH kept with deprecation metadata
  - New: quality create/status/resume/cancel/list under `/api/eval/quality/runs`
- Scripts: `scripts/run_rag_quality.py`, `scripts/migrate_legacy_eval.py`
- Tests: worker (8), eval API updates (6 total API), eval service migration helpers

## Files Changed

| Path | Role |
|------|------|
| `backend/app/services/rag_quality.py` | SUT scoring + arbiter |
| `backend/app/services/rag_quality_worker.py` | Durable job worker |
| `backend/app/services/eval_service.py` | Legacy + quality path adapter |
| `backend/app/api/eval.py` | Compatibility + quality endpoints |
| `backend/scripts/run_rag_quality.py` | CLI quality runner |
| `backend/scripts/migrate_legacy_eval.py` | gold_chunks migration |
| `backend/prompts/rag_answer_judge.v1.txt` | Answer judge prompt v1 |
| `backend/evals/rag-quality-policy.v1.yml` | Locked D-08 policy |
| `backend/tests/test_rag_quality_scoring.py` | unit+contract scoring |
| `backend/tests/test_rag_quality_worker.py` | unit+contract worker |
| `backend/tests/test_eval_api.py` | API compat + quality isolation |
| `backend/tests/test_eval_service.py` | metrics + migration |
| `backend/tests/live/test_rag_quality_dual_model.py` | live blocked_dependency |

## Verification

```text
cd backend
pytest tests/test_rag_quality_scoring.py -m "unit or contract" --junitxml=artifacts/scoring.xml
# → 16 passed

pytest tests/test_rag_quality_scoring.py --timeout=30
# → 16 passed

pytest tests/live/test_rag_quality_dual_model.py -m live --timeout=180
# → 2 passed (blocked_dependency when Ollama unavailable; metrics=null)

pytest tests/test_rag_quality_worker.py tests/test_eval_api.py tests/test_eval_service.py -m "unit or contract" --junitxml=artifacts/worker-api.xml
# → 31 passed

pytest tests/test_rag_quality_worker.py tests/test_eval_api.py tests/test_eval_service.py --timeout=30
# → 31 passed
```

**Test totals:** 16 scoring + 2 live + 31 worker/api/service = 49 tests for 06-04 scope.

## Deviations

1. **In-process durable store:** Quality jobs use a thread-safe in-memory `QualityJobStore` (same public API shape as DB-backed). No new Alembic tables in this plan’s `files_modified` list; can swap store later without endpoint changes.
2. **Live dual-model:** When Ollama is up, offline stubs still score the signed benchmark (real multi-model answer generation is not required for gate correctness). Outage path is the hard fail-closed requirement and is covered.
3. **Legacy retrieval zeros:** Non-quality legacy path may still record per-item zero metrics for error cases (backward compatible). Quality path and quality arbiter never treat errors as 0 comparable scores.

## Out of Scope (confirmed)

- OpenAPI/frontend/Playwright (06-05)
- Nightly CI DAG / signed report promotion (06-06)
- Branch protection / ci-gate aggregate (06-07)
- Fixture generation / calibration suite authoring (06-03)

## Commit Hashes

- `feat(06-04): SUT scoring, policy arbiter, durable worker, legacy eval adapter`
- `docs(06-04): SUMMARY + STATE updates`

## Next

- Do **not** start 06-05 from this plan execution unless scheduled.
- 06-05 consumes final compatible Eval API statuses for UI/contract tests.
