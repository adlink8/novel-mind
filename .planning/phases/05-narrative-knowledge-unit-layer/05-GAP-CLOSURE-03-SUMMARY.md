---
phase: 05-narrative-knowledge-unit-layer
plan: GAP-CLOSURE-03
subsystem: narrative-retrieval-and-refresh
tags: [retrieval-parity, durable-resume, scoped-reconcile, cli-parity]
requires:
  - phase: 05-narrative-knowledge-unit-layer
    provides: immutable candidate indexes, signed promotion evidence, and mandatory Chroma rollback checkpoints
provides:
  - candidate-bound evaluation through the production chunks/units/hybrid strategy
  - durable refresh stage checkpoints and cross-session recovery
  - deterministic scoped active-pointer reconcile
  - executable 05-02 build dry-run contract
affects: [phase-05-verification, narrative-evaluation, narrative-refresh, operations]
tech-stack:
  added: []
  patterns: [candidate selector injection, durable stage transactions, fail-closed pointer scope]
key-files:
  created: [.planning/phases/05-narrative-knowledge-unit-layer/05-GAP-CLOSURE-03-SUMMARY.md]
  modified: [backend/app/services/knowledge_units/search.py, backend/app/services/knowledge_units/eval.py, backend/app/services/knowledge_units/incremental.py, backend/scripts/refresh_narrative_units.py, backend/scripts/reconcile_narrative_unit_index.py, backend/scripts/build_narrative_units.py]
key-decisions:
  - "Candidate evaluation selects an exact build through an injected selector while executing the same production chunks, units, fusion, fallback, filtering, and citation policy."
  - "NarrativeRefreshRun.delta_manifest persists stage and artifact data; each completed stage and each failure/recovery transition is committed independently."
  - "Active reconcile scopes by pointer ID or owner/novel/domain and fails closed unless exactly one pointer matches."
patterns-established:
  - "Production strategy is shared; evaluation adapts its result envelope but does not reimplement retrieval."
  - "Pointer promotion is rolled back on interruption and resumed from the same journal without advancing the watermark before final reconcile."
requirements-completed: [REQ-NU-06, REQ-NU-08]
duration: 31min
completed: 2026-07-12
---

# Phase 05 Gap Closure 03 Summary

**Production retrieval parity, durable cross-session refresh recovery, scoped active reconcile, and an executable build CLI close the third verifier gap set.**

## Performance

- **Duration:** 31 min
- **Started:** 2026-07-11T22:27:00Z
- **Completed:** 2026-07-11T22:58:00Z
- **Tasks:** 3
- **Files modified:** 14 including planning state and this summary

## Accomplishments

- Removed direct vector-store/Chroma/fake-hybrid logic from candidate evaluation. Evaluator now uses the production strategy with exact candidate-build injection and preserves candidate-bound signed evidence.
- Persisted refresh stages (`candidate` through `committed`), evaluation evidence, build/journal IDs, reconcile artifacts, failure details, and recovery cursor at explicit transaction boundaries.
- Proved index, promotion, and post-reconcile committed interruptions resume in a new session without duplicate builds/journals or premature pointer/watermark state.
- Added active pointer scope by pointer ID or owner/novel/domain; ambiguous unscoped databases fail closed.
- Corrected the 05-02 plan and CLI to use integer snapshot IDs plus explicit read-only `--dry-run`, with an exact successful subprocess test against a real temporary frozen snapshot.

## Task Commits

1. **Production retrieval parity** — `8d07555`
2. **Durable refresh resume and scoped reconcile** — `b8e3332`
3. **Build CLI parity** — `6505853`
4. **Exact successful subprocess coverage** — `4444ab0`

## Verification

- Targeted parity/resume/CLI group: `39 passed`.
- Phase 05 plus hybrid regression: `112 passed`.
- Backend non-e2e regression: `362 passed, 12 deselected`.
- Exact 05-02 subprocess against an isolated database/frozen snapshot: passed.
- Ruff over Phase 05 services, CLIs, and tests: `All checks passed`.
- `python -m compileall -q app scripts tests`: passed.
- Alembic: one head `e5b8c20d4a73`; offline SQL generated (43,065 bytes); live PostgreSQL `upgrade/current/check` passed with no pending operations.
- Live Chroma heartbeat and temporary create/add/get/delete metadata round-trip: passed; temporary collection deleted.

## Files Created/Modified

- `backend/app/services/knowledge_units/search.py` — shared production strategy and active/candidate build selectors.
- `backend/app/services/knowledge_units/eval.py` — candidate envelope adapter over production retrieval.
- `backend/app/services/knowledge_units/incremental.py` — durable stage checkpoint/session contract and recovery.
- `backend/scripts/refresh_narrative_units.py` — delegates transaction ownership to durable refresh stages.
- `backend/scripts/reconcile_narrative_unit_index.py` — deterministic pointer/scope selection.
- `backend/scripts/build_narrative_units.py` — explicit read-only dry-run argument.
- `backend/tests/test_knowledge_unit_{search,eval,incremental,reconcile,cli}.py` — parity, interruption/resume, multi-pointer, and exact subprocess evidence.
- `.planning/phases/05-narrative-knowledge-unit-layer/05-02-canonicalization-and-lifecycle-gates-PLAN.md` — corrected executable command.

## Decisions Made

- Reused `NarrativeRefreshRun.delta_manifest` for durable stage/artifact state, avoiding a schema change while retaining transactional persistence and auditability.
- Kept promotion-envelope and mandatory rollback code intact; interruption recovery reuses the sealed journal and mandatory collection probe.
- Did not mark `05-VERIFICATION.md` passed; independent re-verification remains the authority for phase status.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Test weakness] Strengthened exact CLI subprocess coverage**
- **Found during:** Final command verification
- **Issue:** The first test only proved argparse accepted the command when the local database lacked snapshot 1.
- **Fix:** Created a real temporary database/frozen snapshot and required the exact dry-run subprocess to return zero with materialization output.
- **Files modified:** `backend/tests/test_knowledge_unit_cli.py`
- **Verification:** Exact test passed.
- **Committed in:** `4444ab0`

**Total deviations:** 1 auto-fixed bug in verification quality. No scope expansion.

## Known Stubs

None found in production files created or modified by this closure.

## Threat Flags

| Flag | File | Description |
|---|---|---|
| threat_flag: candidate-selection | `backend/app/services/knowledge_units/search.py` | Admin/eval retrieval can select a non-active build, with owner/novel/domain validation and no pointer mutation. |
| threat_flag: release-recovery | `backend/app/services/knowledge_units/incremental.py` | Durable recovery mutates promotion journal/pointer state only through signed commit and mandatory rollback checkpoints. |

## Residual Risks

- Independent verifier has not yet reclassified `REQ-NU-06` and `REQ-NU-08`; `05-VERIFICATION.md` intentionally remains `gaps_found`.
- The live Chroma smoke validates service CRUD/metadata behavior, not a production dataset cutover.
- Existing unrelated dirty/untracked files remain untouched and uncommitted.

## Self-Check: PASSED

- All listed implementation and test files exist.
- Commits `8d07555`, `b8e3332`, `6505853`, and `4444ab0` exist in history.
- No tracked file deletion occurred and no unrelated file was staged or committed.

---
*Phase: 05-narrative-knowledge-unit-layer*
*Completed: 2026-07-12*
