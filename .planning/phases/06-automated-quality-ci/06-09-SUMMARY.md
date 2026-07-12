---
phase: 06-automated-quality-ci
plan: 09
subsystem: database
tags: [baseline, prepare-commit, cross-chunker, quality-run, lineage, alembic]

requires:
  - phase: 06-08
    provides: QualityRun repository + five-tuple lineage identity chain
provides:
  - BaselineCandidate durable prepare evidence and journal
  - ActiveBaseline per-owner pointer
  - prepare/commit revalidation from current QualityRun DB state
  - same-snapshot cross-chunker report aggregation with exclusions
  - additive API routes under /api/eval/quality/baseline/* and reports/cross-chunker
affects: [07-semantic-hierarchical-chunking]

tech-stack:
  added: []
  patterns:
    - "prepare freezes fingerprint; commit reloads QualityRun and revalidates before pointer move"
    - "rejected commit never replaces ActiveBaseline; candidate history append-only"
    - "make_baseline_from_metrics remains non-promotable metrics shape helper only"

key-files:
  created:
    - backend/migrations/versions/08_baseline_candidates.py
    - backend/tests/test_rag_quality_baseline.py
  modified:
    - backend/app/models/eval.py
    - backend/app/models/__init__.py
    - backend/app/schemas/eval.py
    - backend/app/services/rag_quality.py
    - backend/app/api/eval.py
    - backend/tests/test_eval_api.py

key-decisions:
  - "Active baseline is per-owner (not per-novel) for quality gate promotion"
  - "Commit returns structured ok=false on revalidation failure so rejected journal is persisted"
  - "Report outer key is source_snapshot_hash; remaining four lineage members define series identity"

patterns-established:
  - "Two-phase promotion: prepare_token + prepare_fingerprint then commit revalidation"
  - "Cross-chunker report exclusions carry machine-readable reasons (legacy, different snapshot, incomplete)"

requirements-completed: [REQ-AUTO-11]

duration: ~40min
completed: 2026-07-13
---

# Phase 06 Plan 09: Persistent Baseline Prepare/Commit + Cross-Chunker Reports

**Durable BaselineCandidate prepare/commit with DB revalidation and same-snapshot multi-chunker report aggregation, closing REQ-AUTO-11 consumption side.**

## Performance

- **Tasks:** 3 (model, service, API)
- **Tests:** 45 related tests passed (baseline + models + scoring + eval API)
- **Alembic:** `08baselinecand01` head; upgrade + check clean

## Accomplishments

1. **BaselineCandidate + ActiveBaseline ORM** with prepare_token uniqueness, frozen five-tuple lineage, hashes/signature, metrics snapshot, fingerprint, and append-only journal.
2. **prepare_baseline_candidate / commit_baseline_candidate** reload QualityRun; only `passed`/`qualified` + `quality_comparable` + complete lineage promote; tamper after prepare → rejected, active pointer unchanged; commit idempotent.
3. **build_cross_chunker_report** groups comparable runs by same `source_snapshot_hash` with separate series per chunker name/version/config/manifest; excludes legacy/other-snapshot with reasons.
4. **Additive APIs** (legacy quality run routes unchanged):
   - `POST /api/eval/quality/baseline/prepare`
   - `POST /api/eval/quality/baseline/commit`
   - `GET /api/eval/quality/baseline/active`
   - `POST /api/eval/quality/reports/cross-chunker`

## Verification

```text
pytest tests/test_rag_quality_baseline.py tests/test_rag_quality_models.py \
  tests/test_rag_quality_scoring.py tests/test_eval_api.py -q
# 45 passed
alembic upgrade head && alembic check
# 08baselinecand01, no new upgrade operations
```

## Must-Haves

| Truth | Evidence |
|---|---|
| Prepare/commit revalidate DB QualityRun lineage | commit_tamper test leaves active unchanged |
| Only complete comparable lineage promotes | prepare_rejects_legacy_and_non_passed |
| Same-snapshot different chunkers appear as series | test_cross_chunker_report_groups_same_snapshot + API test |
| Legacy quality API paths retained | existing test_eval_api quality routes still pass |

## Next

Phase 06 gap closure complete → re-verify Phase 06 if needed → Phase 07 semantic hierarchical chunking.
