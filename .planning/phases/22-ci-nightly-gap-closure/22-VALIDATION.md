# Phase 22 Validation Ledger

## Gap Status

| Gap | Status | Evidence |
|---|---|---|
| 22-G1 | VERIFIED_LOCAL | stable workflow classifications; CI policy 116 passed; relationship helper decoupled from unrelated timeline status; full frontend coverage 3/3 repeated (248 each) |
| 22-G2 | BLOCKED_OPERATOR_SETUP / TEMPORARILY_SKIPPED | run 30623438107 emitted signed blocked terminal artifacts without scheduling a provider Runner; token/Runner setup and real schedule observation pending |
| 22-G3 | IMPLEMENTED_LOCAL / BLOCKED_OBSERVATION | stable fingerprint, recurrence update and green auto-close covered by policy tests; 0/3 scheduled green |

## Consecutive Scheduled Green Runs

| # | Run | Commit | Artifact status | Result |
|---:|---|---|---|---|
| 1 | pending | pending | pending | pending |
| 2 | pending | pending | pending | pending |
| 3 | pending | pending | pending | pending |

## Latest Local Evidence

- `npm run test:coverage`: 29 files passed, 248 tests passed (2026-07-31).
- `npx vitest run src/app/analysis/relationships.test.tsx --coverage.enabled=false`:
  5 consecutive local runs passed, 18 tests each.
- `npm run test:coverage`: 3 additional consecutive full-suite runs passed after the
  helper was changed to wait for the user-visible relationship tab rather than the
  unrelated timeline status request.
- `PYTHONPATH=. pytest tests/ci -q`: 116 passed.
- `actionlint v1.7.12`: passed.
- Phase 22-G2 control-plane tests: 30 passed.
- Full `tests/ci`: 126 passed after hosted preflight/finalizer implementation.
- Ruff check/format: passed for the G2 Python changes.
- Production commit `495b29a`: optional provider Runner is preflight-gated; every terminal
  path produces a signed canonical report; only explicit `promotable=true` can reach
  baseline promotion.
- PR #31 merged to master as `912ca6b`; merge push CI passed.
- Workflow-dispatch run `30623438107`: all ordinary producers and Live smoke passed;
  preflight completed on GitHub-hosted capacity; provider benchmark skipped; finalizer passed;
  promotion skipped; `ci-gate` failed closed as designed for an incomparable Nightly.
- Downloaded `nightly-control-report`: `nightly-authority.v1`,
  `provider_ready=false`, `reason=runner_registry_unavailable`, runner count `0`.
- Downloaded `nightly-rag-report`: `rag-quality.v1`, `blocked_dependency`,
  `quality_comparable=false`, `metrics=null`, `promotable=false`, 64-character signature,
  lineage bound to run `30623438107` and commit `912ca6b`.
- Alert issue #32 classified the root cause as `runner-or-environment-unavailable` and
  exposed the non-comparable report summary without full text.
- GitHub run `30607067442`: 1 frontend test failed; Nightly skipped.
- GitHub runs `30330904855`, `30424693088`, `30515165945`: Nightly runner
  unavailable until cancellation.

## Verification Verdict

`BLOCKED / NOT_VERIFIED`. Phase 22 is deferred and must remain open. Phase 26 was unlocked
through an explicit user risk-acceptance override; this verdict remains unchanged.
