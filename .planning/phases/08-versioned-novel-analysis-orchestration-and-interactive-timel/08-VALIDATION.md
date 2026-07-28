# Phase 08 Validation Architecture

## Wave 0

- `backend/tests/unit/timeline/`: schemas, jobs, strict gateway, cache, extraction, reconcile, overrides, budget.
- `backend/tests/integration/timeline/`: PostgreSQL persistence, version lifecycle, API, spoilers, E2E.
- `backend/tests/adversarial/test_timeline_evidence.py`: forged evidence and structured-output attacks.
- `frontend/src/app/analysis/page.test.tsx` and `frontend/e2e/timeline.spec.ts`: component and browser states.
- `tests/ci/test_timeline_release_gate.py`: release evidence. Independent verify-phase owns `08-VERIFICATION.md`.

## Required Gates

- Provider calls are counted for cache, budget, restart and failure semantics.
- PostgreSQL tests prove stale CAS rejection and byte-identical rollback manifests.
- Spoiler property tests derive overlays, edges, filters and aggregates only after computing visible event IDs.
- Every plan verification command maps to the Wave 0 paths above.
