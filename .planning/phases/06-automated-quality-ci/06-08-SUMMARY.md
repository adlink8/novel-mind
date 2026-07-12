---
phase: 06-automated-quality-ci
plan: 08
subsystem: database
tags: [quality-run, lineage, alembic, worker, postgres, hashing]

requires:
  - phase: 06-03
    provides: SourceSnapshot manifest_hash, signed fixtures, model lineage
  - phase: 06-04
    provides: durable worker ops, scoring, report_signature baseline
provides:
  - QualityRun ORM + Alembic 07qualityruns01
  - QualityRunRepository with CAS leases over AsyncSession
  - five-tuple chunker/source lineage in input/stage/output/signature hashes
  - legacy_incomparable fail-closed without invented hashes
affects: [06-09, 07-semantic-hierarchical-chunking]

tech-stack:
  added: []
  patterns:
    - "QualityRun is sole production fact source; InMemory QualityJobStore is test double only"
    - "Recompute chunker_config_hash via stable_hash; never trust caller config hash alone"
    - "CAS lease acquire/heartbeat/release via UPDATE WHERE predicates"

key-files:
  created:
    - backend/migrations/versions/07_quality_runs.py
  modified:
    - backend/app/models/eval.py
    - backend/app/models/__init__.py
    - backend/app/schemas/eval.py
    - backend/app/api/eval.py
    - backend/app/services/rag_quality_worker.py
    - backend/app/services/rag_quality.py
    - backend/tests/test_rag_quality_models.py
    - backend/tests/test_rag_quality_worker.py
    - backend/tests/test_rag_quality_scoring.py
    - backend/tests/test_eval_api.py
    - backend/scripts/run_rag_quality.py

key-decisions:
  - "Async worker + repository protocol; production uses QualityRunRepository(session)"
  - "work_id only set when caller proves novel FK (fixtures may not have novels row)"
  - "Missing lineage on new required runs → invalid_lineage before scoring; empty lineage on row → legacy_incomparable"

patterns-established:
  - "Five-tuple: chunker_name, chunker_version, chunker_config_hash, chunk_manifest_hash, source_snapshot_hash"
  - "Report signs unsigned payload including chunker_lineage; output_hash over same unsigned body"

requirements-completed: [REQ-AUTO-11]

duration: ~45min
completed: 2026-07-12
---

# Phase 06 Plan 08: Persistent QualityRun + Lineage Identity Summary

**PostgreSQL QualityRun repository with CAS leases and five-tuple chunker/source lineage bound into every input, stage-cache, output hash, and report signature**

## Performance

- **Duration:** ~45 min
- **Started:** 2026-07-12T20:00:00Z
- **Completed:** 2026-07-12T21:00:00Z
- **Tasks:** 3
- **Files modified:** 12

## Accomplishments

- Added `QualityRun` ORM + Alembic revision `07qualityruns01` (after `f6a0303ragfix`) with comparable-requires-lineage CHECK and owner/status/lease indexes
- Replaced process-global store as API fact source: routes inject `AsyncSession` → `QualityRunRepository` / `make_quality_worker`
- Canonical five-tuple lineage recomputes config hash, verifies snapshot evidence, and enters `input_hash`, stage-cache keys, signed report, and `output_hash`
- Legacy / incomplete rows stay readable with `quality_comparable=false` and `incomparable_reason=legacy_incomparable` (no invented hashes)

## Task Commits

1. **Task 1–3 (ORM, repository, lineage chain)** - `b66457d` (feat)

**Plan metadata:** (this SUMMARY commit)

## Must-Haves Evidence

| Truth | Evidence |
|---|---|
| Quality run/lease/checkpoint/cache/metrics/report survive restart | `test_db_repository_restart_resume` + `QualityRunRepository` commits each stage |
| Five-tuple on comparable runs | `QualityRun` columns + CHECK; `test_quality_run_persist_with_lineage` |
| Lineage in input/stage/output/signature | `build_quality_input_hash`, `build_stage_cache_key`, `run_quality_evaluation` report fields; scoring collision tests |
| Legacy never invents hashes | `canonicalize_chunker_lineage(None) → legacy_incomparable`; `test_quality_run_legacy_without_lineage_incomparable` |

## Verification

```
cd backend
pytest tests/test_rag_quality_models.py tests/test_rag_quality_worker.py tests/test_rag_quality_scoring.py tests/test_eval_api.py -x
# → 49 passed

alembic upgrade head && alembic current && alembic check
# → 07qualityruns01 (head); No new upgrade operations detected
```

## Files Created/Modified

- `backend/app/models/eval.py` — `QualityRun` ORM
- `backend/migrations/versions/07_quality_runs.py` — table + constraints
- `backend/app/schemas/eval.py` — `ChunkerLineage`, public run schema, reason constants
- `backend/app/services/rag_quality_worker.py` — async worker, in-memory double, DB repository
- `backend/app/services/rag_quality.py` — lineage canonicalize/hash/stage-key/report bind
- `backend/app/api/eval.py` — AsyncSession-injected quality routes + `chunker_lineage` body field
- Tests for models/worker/scoring/API

## Decisions Made

- Public worker ops preserved as async: `create_job`, `acquire_lease`, `heartbeat`, `release_lease`, `request_cancel`, `run`, `resume`
- Production API never uses process-global dictionary; in-memory store remains for unit tests and CLI offline durable mode

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] work_id FK not taken from fixture snapshot blindly**
- **Found during:** Task 2 DB restart test
- **Issue:** Fixture `work_id=10` is not a real novels row → IntegrityError
- **Fix:** `create_job(work_id=...)` only when caller supplies proven novel id
- **Files modified:** `rag_quality_worker.py`
- **Verification:** `test_db_repository_restart_resume` passed
- **Committed in:** `b66457d`

**2. [Rule 1 - Bug] Stage-cache idempotency test expected hits across different baselines**
- **Found during:** Task 3 scoring tests
- **Issue:** input_hash now includes baseline; different baseline correctly changes stage keys
- **Fix:** Test uses same baseline + explicit `run_input_hash` for second pass
- **Files modified:** `test_rag_quality_scoring.py`
- **Verification:** 49 tests green
- **Committed in:** `b66457d`

---

**Total deviations:** 2 auto-fixed (1 missing critical, 1 bug)
**Impact on plan:** Required for FK safety and correct lineage-bound cache identity. No scope creep.

## Issues Encountered

None remaining.

## User Setup Required

None - no external service configuration required. Operators should run `alembic upgrade head` on each environment.

## Next Phase Readiness

- Ready for **06-09** persistent baseline prepare/commit and cross-chunker reports
- Do not start 06-09 from this summary alone without its plan

## Self-Check: PASSED

- [x] Key artifacts exist on disk
- [x] `git log --grep=06-08` has production commit
- [x] Plan verification pytest green (49)
- [x] Alembic head/current/check clean after upgrade

---
*Phase: 06-automated-quality-ci*
*Completed: 2026-07-12*
