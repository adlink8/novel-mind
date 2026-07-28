---
phase: 09-dynamic-character-relationship-graph
plan: 02
subsystem: pipeline
tags: [relationship-graph, llm-judgment, deterministic-gates, evidence-package, postgresql, fastapi]
requires:
  - phase: 09-dynamic-character-relationship-graph
    provides: append-only observation ORM contracts and Alembic 11relobserve01
  - phase: 04-llm
    provides: KnowledgeRelationJudgment accepted/accepted source gate
provides:
  - Deterministic candidate/evidence packages from fiction accepted judgments
  - Strict relationship semantic judgment + exact-cache / call_skipped audit
  - Ordered gates with AUTO_ACCEPT_THRESHOLD=0.85 and state machine
  - Idempotent RelationshipObservationWorker write path
affects: [09-03, 09-04, 09-05, relationship-api, relationship-query]
tech-stack:
  added: []
  patterns:
    - scripts own source/evidence/threshold/state/writes; LLM only bounded semantic fields
    - version-bound package_hash + policy_hash + idempotency_key for immutable observations
key-files:
  created:
    - backend/app/services/relationships/candidates.py
    - backend/app/services/relationships/evidence.py
    - backend/app/services/relationships/judgment.py
    - backend/app/services/relationships/gates.py
    - backend/app/services/relationships/worker.py
    - backend/prompts/relationship_semantic_judge.v1.txt
    - backend/tests/unit/relationships/test_pipeline.py
    - backend/tests/integration/relationships/test_pipeline.py
  modified: []
key-decisions:
  - "AUTO_ACCEPT_THRESHOLD = 0.85; REVIEW_THRESHOLD = 0.65; policy_hash freezes gate order and thresholds."
  - "same_entity/causes/precedes never produce RelationshipObservation; same_entity becomes identity review metadata only."
  - "Worker accepts deterministic_output and exact-cache for call_skipped audits without provider network."
patterns-established:
  - "candidate -> judged -> gated -> accepted|needs_human_review|rejected owned only by scripts."
  - "Package allowlists revalidate IDs; forged/out-of-package evidence yields zero accepted rows."
requirements-completed: [REQ-REL-01, REQ-REL-02, REQ-REL-06]
duration: 45min
completed: 2026-07-15
---

# Phase 09 Plan 02: Relationship Observation Pipeline Summary

**Accepted Phase 04 judgments → versioned evidence packages → strict semantic judgment → ordered gates (≥0.85 accept) → append-only RelationshipObservation via idempotent worker.**

## Performance

- **Duration:** 45 min
- **Started:** 2026-07-15T01:00:00Z
- **Completed:** 2026-07-15T01:45:00Z
- **Tasks:** 3
- **Files modified:** 10 created

## Accomplishments

- Built deterministic source selection that revalidates `status=accepted` AND `gate_status=accepted`, fiction domain, character endpoints, and five edge labels only.
- Froze `relationship_semantic_judge.v1` prompt and strict `RelationshipSemanticJudgment` parse path with one same-deployment repair and exact-cache / call_skipped audits.
- Implemented ordered source/fiction/scope/schema/evidence/interval/conflict/threshold gates with stable reason codes and `AUTO_ACCEPT_THRESHOLD = 0.85`.
- Delivered `RelationshipObservationWorker` as the sole accepted-observation writer with package_hash uniqueness and idempotency_key reuse; no Neo4j writes.

## Task Commits

1. **Tasks 1–3: pipeline services, prompt, unit + PostgreSQL integration** - `4e914f8` (feat)

**Plan metadata:** (this SUMMARY commit follows)

## Files Created/Modified

- `backend/app/services/relationships/candidates.py` — accepted-source selection and character endpoint resolution
- `backend/app/services/relationships/evidence.py` — version-bound bounded evidence packages and checksums
- `backend/app/services/relationships/judgment.py` — strict LLM judgment, repair, exact cache
- `backend/app/services/relationships/gates.py` — thresholds and ordered gates (`AUTO_ACCEPT_THRESHOLD = 0.85`)
- `backend/app/services/relationships/worker.py` — `RelationshipObservationWorker` orchestration
- `backend/prompts/relationship_semantic_judge.v1.txt` — frozen evidence-only fiction prompt
- `backend/tests/unit/relationships/test_pipeline.py` — candidate/evidence/threshold/judgment unit proofs
- `backend/tests/integration/relationships/test_pipeline.py` — real PostgreSQL accept/idempotency/version isolation

## Decisions Made

- Threshold band uses `confidence < 0.65` reject, `0.65 <= c < 0.85` review, `c >= 0.85` accept only after all critical gates; `uncertain` never auto-accepts.
- Entity endpoints resolve to `Character` rows by novel-scoped name; unresolved endpoints fail closed before any model call.
- Vector/BM25 recall remains in `recall_signals` metadata on candidates and never alone creates observations.

## Deviations from Plan

None - plan executed exactly as written within declared deliverables (plus package `__init__.py` exports).

## Issues Encountered

None.

## Commands and Test Results

```text
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit/relationships/test_pipeline.py -k "candidate or evidence" -q
# 4 passed, 9 deselected

.\.venv\Scripts\python.exe -m pytest tests/unit/relationships/test_pipeline.py -q
# 13 passed

.\.venv\Scripts\python.exe -m pytest tests/integration/relationships/test_pipeline.py -q
# 4 passed (real PostgreSQL 127.0.0.1:5433)

.\.venv\Scripts\python.exe -m pytest tests/unit/relationships/test_pipeline.py tests/integration/relationships/test_pipeline.py -q
# 17 passed, 0 skipped
```

Boundary checks covered: `.6499` reject, `.65`/`.8499` review, `.85` accept; forged evidence and source revocation yield zero accepted rows; second worker run is idempotent; second analysis version creates a distinct observation chain.

## Self-Check: PASSED

- All plan `files_modified` exist on disk.
- Production commit `4e914f8` present with `feat(09-02)` message.
- Targeted suite: 17 passed, 0 skipped.
- `AUTO_ACCEPT_THRESHOLD = 0.85` and `class RelationshipObservationWorker` present.
- Critical false accepts: zero in suite (forged/non-edge/revoked/history/threshold cases).

## User Setup Required

None - no external service configuration required beyond existing CI PostgreSQL.

## Next Phase Readiness

- Ready for **09-03**: owner/version/spoiler graph API, fold, overrides, and replayable Neo4j projection consuming accepted observations.
- Phase 10/11 product code intentionally untouched.

---
*Phase: 09-dynamic-character-relationship-graph*
*Completed: 2026-07-15*
