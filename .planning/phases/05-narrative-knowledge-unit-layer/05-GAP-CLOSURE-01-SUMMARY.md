---
phase: 05-narrative-knowledge-unit-layer
plan: GAP-CLOSURE-01
subsystem: narrative-release-lifecycle
tags: [candidate-eval, hmac, chroma, promotion, reconcile, rollback, incremental]
requires:
  - phase: 05-narrative-knowledge-unit-layer
    provides: immutable candidate indexing and narrative lifecycle contracts
provides:
  - candidate-bound signed frozen retrieval evidence
  - complete refresh-to-watermark production state machine
  - durable joint rollback/restore checkpoints
affects: [phase-05-verification, narrative-promotion]
tech-stack:
  added: []
  patterns: [signed release evidence, immutable per-query eval outputs, resumable publication]
key-files:
  created: [backend/tests/test_knowledge_unit_cli.py]
  modified: [backend/app/services/knowledge_units/eval.py, backend/app/services/knowledge_units/promotion.py, backend/app/services/knowledge_units/incremental.py, backend/app/services/knowledge_units/reconcile.py, backend/app/services/knowledge_units/rollback.py]
key-decisions:
  - "A corpus-scoped candidate requires frozen evidence for its own fiction or history domain; a mixed-domain build requires both domains."
  - "NARRATIVE_EVAL_SIGNING_SECRET authenticates immutable per-query run evidence; unsigned/static dictionaries cannot promote."
  - "Watermark advancement remains the final publication write after post-promotion direct-Chroma reconcile."
patterns-established:
  - "Production injectable adapters always have CLI-wired defaults; tests verify the retrieval callback is actually invoked."
requirements-completed: [REQ-NU-06, REQ-NU-07, REQ-NU-08]
duration: 32min
completed: 2026-07-11
---

# Phase 05 Gap Closure 01 Summary

**Candidate-bound chunks/units/hybrid evaluation with signed evidence, direct-Chroma reconcile, complete incremental publication, and durable joint rollback/restore checkpoints**

## Performance

- **Duration:** 32 min
- **Started:** 2026-07-11T07:05:00Z
- **Completed:** 2026-07-11T07:37:00Z
- **Tasks:** 3
- **Files modified:** 25

## Accomplishments

- Removed self-scoring frozen fixtures. Every query now executes all three retrieval strategies, measures runtime latency, stores per-query outputs, and binds evidence to build/checksum/collection/owner/novel/domain/dataset hash.
- Promotion now verifies signed run evidence, corpus domain coverage, faithfulness, canary, direct reconcile, candidate lineage, and approver; forged/static/cross-candidate reports fail.
- Refresh now runs delta preparation, affected rebuild, immutable indexing, real eval/canary, promotion, direct Chroma reconcile, and final-only watermark advancement with rollback on post-promote failure.
- Rollback journals persist before/after collection, manifest, pointer, and watermark checkpoints; committed/new-session rollback and restore plus collection failure injection are tested.
- Reconcile supports `--active`, reads Chroma IDs/metadata directly, and detects wrong-build, owner, missing, orphan, duplicate, deleted, and deprecated residue.

## Task Commits

1. **Candidate-bound evaluation and promotion evidence** — `da43d5c`
2. **Refresh, direct reconcile, and durable recovery chain** — `09ccbc3`
3. **Executable documented CLI contracts** — `ddf1e29`

## Verification

- Phase 05 targeted: `70 passed`.
- Ruff over Phase 05 services, CLIs, and tests: `All checks passed`.
- Alembic: one head `e5b8c20d4a73`; offline `upgrade head --sql` passed.
- Chroma: heartbeat passed; temporary collection create/add/get/delete passed.
- PostgreSQL online `alembic current`: blocked by connection refusal at `127.0.0.1:5432`; no pass claimed.
- Subprocess CLI smoke: seven entrypoints plus documented index/rollback dry-runs passed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added authenticated release evidence**
- **Found during:** Frozen evaluation closure
- **Issue:** Persisted JSON alone was caller-editable and could not prove candidate lineage.
- **Fix:** Added HMAC-signed immutable run reports and strict promotion verification.
- **Committed in:** `da43d5c`

**2. [Rule 1 - Bug] Added build metadata to Chroma projection**
- **Found during:** Direct reconcile implementation
- **Issue:** Existing collection metadata could not detect wrong-build residue.
- **Fix:** Indexed build ID, manifest checksum, and explicit lifecycle metadata.
- **Committed in:** `09ccbc3`

**3. [Rule 3 - Blocking] Corrected invalid PLAN CLI examples**
- **Found during:** Subprocess command verification
- **Issue:** `TEST` integer arguments and missing candidate/output arguments were not executable.
- **Fix:** Updated PLAN commands and added smoke tests.
- **Committed in:** `ddf1e29`

**Total deviations:** 3 auto-fixed (2 correctness/security, 1 blocking documentation contract).

## Known Stubs

None. Empty collections in evaluation are runtime accumulators, and optional `None` arguments select production defaults; they do not flow as placeholder release evidence.

## Threat Flags

| Flag | File | Description |
|---|---|---|
| threat_flag: release-evidence-secret | `backend/app/services/knowledge_units/eval.py` | HMAC signing secret is required for promotion evidence and must remain in environment-backed credentials. |

## Residual Risks

- A real PostgreSQL-backed end-to-end cutover was not runnable because PostgreSQL was offline. SQLite committed-session tests and live Chroma projection checks passed, but independent verifier should repeat against PostgreSQL.
- `05-VERIFICATION.md` remains `gaps_found`; only the subsequent independent verifier may change its status.

## Self-Check: PASSED

- All listed implementation and test files exist.
- Commits `da43d5c`, `09ccbc3`, and `ddf1e29` exist in git history.
- No unrelated working-tree changes were staged or committed.

---
*Phase: 05-narrative-knowledge-unit-layer*
*Completed: 2026-07-11*
