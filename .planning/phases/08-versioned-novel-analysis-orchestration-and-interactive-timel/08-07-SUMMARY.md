---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 07
subsystem: timeline-orchestration
tags: [postgresql, durable-worker, litellm, exact-cache, budget-ledger, call-audit]
requires:
  - phase: 08-01..08-06
    provides: timeline persistence, strict extraction, reconciliation, promotion, API, and qualification contracts
  - phase: 07-semantic-hierarchical-chunking
    provides: immutable active hierarchy and evidence nodes
provides:
  - Production first-entry and resume worker from Phase 07 evidence through candidate promotion
  - PostgreSQL-atomic timeline budget reservation, settlement, exact cache, and model-call audit
  - Strict no-fallback extraction and reconciliation gateway with durable repair attempts
affects: [08-08, phase-08-verification, timeline-worker, timeline-qualification]
tech-stack:
  added: []
  patterns: [short-transaction model-call reservation, checkpoint-owned exact cache artifact, frozen no-fallback deployment pair]
key-files:
  created: [backend/app/services/timeline/worker.py, backend/tests/integration/timeline/test_production_worker.py, backend/tests/integration/timeline/test_persistent_calls.py]
  modified: [backend/app/services/timeline/model_gateway.py, backend/app/services/timeline/extraction.py, backend/app/services/timeline/reconcile.py, backend/app/services/timeline/jobs.py, backend/app/api/timeline.py, backend/app/services/timeline/__init__.py]
key-decisions:
  - "Reuse AnalysisChapterStage.checkpoint as the durable exact-cache artifact authority and ModelCallAttempt as cache lineage, avoiding a new schema or migration."
  - "Freeze one OpenAI chapter deployment and one OpenAI reconciliation deployment in production runtime; capability or pricing failure pauses without fallback."
  - "Provider outcome-unknown reservations remain charged/reserved until explicitly resolved, preventing blind retry from evading budget accounting."
patterns-established:
  - "Every production timeline provider call enters through TimelineModelGateway after a row-locked PostgreSQL reservation and reserved attempt row."
  - "Chapter artifacts, evidence graph rows, and completed checkpoints commit together; resume skips completed stages."
requirements-completed: [REQ-TIME-01, REQ-TIME-02, REQ-TIME-03, REQ-TIME-05, REQ-TIME-09]
duration: 26min
completed: 2026-07-13
---

# Phase 08 Plan 07: Production Worker and Persistent Model Boundaries Summary

**Production first-entry timeline orchestration now loads Phase 07 evidence, persists progressive candidates, reconciles through one strict gateway, validates manifests, and CAS-promotes under PostgreSQL-backed budget/cache/call audits.**

## Performance

- **Duration:** 26 min
- **Started:** 2026-07-13T04:48:23Z
- **Completed:** 2026-07-13T05:14:07Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- Connected `/start-or-resume`, `/resume`, and legacy `/extract` to a real background worker that claims durable leases, loads the active Phase 07 hierarchy, publishes chapter events, reconciles, freezes a manifest, and promotes by expected-revision CAS.
- Added restart behavior that commits chapter event/evidence/checkpoint state atomically and issues zero duplicate provider calls for already completed chapters.
- Replaced process-memory production call boundaries with PostgreSQL row-locked worst-case reservations, settled usage/cost, provider request IDs, response hashes, repair attempts, outcome-unknown records, and call-skipped cache lineage.
- Added exact extraction and reconciliation cache recovery from completed stage artifacts after repository/process reconstruction.
- Proved real PostgreSQL concurrent reservations permit exactly one provider call under a one-call ceiling.

## Task Commits

1. **Task 1 RED: production worker contract** - `e8a38cd`
2. **Task 1 GREEN: durable production worker** - `f1545d7`
3. **Task 2 RED: persistent call-boundary contracts** - `65ef888`
4. **Task 2 GREEN: PostgreSQL budget/cache/audit gateway** - `ec4898c`
5. **Task 3: fail-closed reconciliation fault injection** - `01d9fa4`
6. **Required exports: production timeline package surface** - `2304cfc`
7. **Task 3 fix: checkpoint lease renewal** - `d64aa22`

## Files Created/Modified

- `backend/app/services/timeline/worker.py` - Production durable pipeline, fixed deployments, hierarchy loading, progressive persistence, reconciliation, validation, promotion, and lease renewal.
- `backend/app/services/timeline/model_gateway.py` - PostgreSQL call repository, atomic reservation/settlement, strict parsing, repairs, cache-skip and outcome audits.
- `backend/app/services/timeline/extraction.py` - Persistent exact-cache lookup from completed stage artifacts.
- `backend/app/services/timeline/reconcile.py` - Required strict reconciliation output and gateway-backed production path.
- `backend/app/services/timeline/jobs.py` - Completed durable run remains idempotently discoverable.
- `backend/app/api/timeline.py` - First-entry/resume background dispatch and completed-run reuse.
- `backend/app/services/timeline/__init__.py` - Required worker, repository, cache, and reconciliation exports.
- `backend/tests/integration/timeline/test_production_worker.py` - Real AsyncSession API-to-active pipeline, restart, audit, and invalid-reconcile tests.
- `backend/tests/integration/timeline/test_persistent_calls.py` - Durable budget/cache/audit, strict repair, zero-call, and real PostgreSQL concurrency tests.

## Decisions Made

- Used existing PostgreSQL tables only: successful stage checkpoint JSON stores the validated model artifact; `ModelCallAttempt.cache_key` and `cache_source_attempt_id` provide durable cache identity and lineage.
- Kept provider calls outside database transactions. Reservation and attempt commit first; response settlement occurs in a second short transaction.
- Kept an explicitly labeled deterministic proposal transport only for legacy unit adapters; the production worker supplies `TimelineModelGateway` for both extraction and reconciliation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Rejected incomplete reconciliation objects**
- **Found during:** Task 3 fault injection
- **Issue:** `{}` passed because all reconciliation collections had defaults, allowing an incomplete provider response to promote.
- **Fix:** Made duplicate groups, story constraints, and causal edges explicit required strict fields.
- **Files modified:** `backend/app/services/timeline/reconcile.py`, `backend/tests/integration/timeline/test_production_worker.py`
- **Verification:** Two invalid attempts persist as `schema_rejected`; run pauses and no active pointer is created.
- **Committed in:** `01d9fa4`

**2. [Rule 2 - Missing Critical Functionality] Exported production timeline contracts**
- **Found during:** Final package integration
- **Issue:** Worker/runtime, PostgreSQL repository, persistent cache, and strict reconciliation types were not available from the timeline package surface requested by the plan invocation.
- **Fix:** Added explicit package exports without changing unrelated modules.
- **Files modified:** `backend/app/services/timeline/__init__.py`
- **Verification:** Direct package import plus seven target integration tests passed.
- **Committed in:** `2304cfc`

**3. [Rule 1 - Bug] Renewed leases at durable checkpoints**
- **Found during:** Final orchestration scan
- **Issue:** Heartbeats updated but lease expiry did not, so a long novel could be reclaimed after five minutes while still running.
- **Fix:** Extend lease expiry on every durable progress heartbeat.
- **Files modified:** `backend/app/services/timeline/worker.py`
- **Verification:** Production worker interruption/resume suite passed.
- **Committed in:** `d64aa22`

**Total deviations:** 3 auto-fixed (2 bugs, 1 missing critical export). **Impact:** All changes close correctness or integration gaps directly within 08-07 scope.

## Issues Encountered

- Context7 CLI was not installed; LiteLLM capability/response behavior was checked against official LiteLLM documentation and the installed function signatures.
- Existing pytest configuration warnings for unavailable `pytest-timeout` options remain pre-existing and out of scope.

## Known Stubs

None.

## Threat Flags

| Flag | File | Description |
|---|---|---|
| threat_flag: model-network-call | `backend/app/services/timeline/worker.py` | Background worker invokes frozen LiteLLM deployments; strict capability, pricing, budget, schema, and no-fallback gates precede publication. |
| threat_flag: background-execution | `backend/app/api/timeline.py` | Authenticated owner-scoped start/resume endpoints now schedule durable in-process work after the run row commits. |

## Verification

- `pytest tests/unit/timeline tests/integration/timeline tests/adversarial/test_timeline_evidence.py -x` — **63 passed**.
- Real PostgreSQL concurrent budget test — **passed**, exactly one provider call and one `budget_rejected` attempt.
- Production worker focused integration — **3 passed**.
- Timeline package export integration — **7 passed**.
- `python -m compileall -q app/services/timeline app/api/timeline.py` — passed.
- `alembic current` — `10analysistime01 (head)`.
- `gsd-sdk query verify.schema-drift 08` — no drift detected.

## User Setup Required

Production model execution requires valid credentials for the frozen OpenAI deployments. Missing credentials fail as `paused_dependency`; they do not fall back or promote.

## Next Phase Readiness

- Verifier gaps 1-2 now have real API/worker/PostgreSQL evidence.
- 08-08 remains unexecuted and is still required for verifier gaps 3-4 (frontend ordering/source isolation and production-backed qualification).

## Self-Check: PASSED

- All three created files and the summary exist.
- All seven 08-07 implementation/test commits exist.
- No known stub patterns were found in plan-owned files.
- 08-08 remains untracked and unexecuted.

---
*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Completed: 2026-07-13*
