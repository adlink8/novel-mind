# Phase 22 Validation Ledger

## Gap Status

| Gap | Status | Evidence |
|---|---|---|
| 22-G1 | VERIFIED_LOCAL | stable workflow classifications; CI policy 116 passed; relationship helper decoupled from unrelated timeline status; full frontend coverage 3/3 repeated (248 each) |
| 22-G2 | IMPLEMENTED_LOCAL / BLOCKED_REMOTE_SETUP | hosted preflight and signed terminal finalizer committed; Runner/token setup and scheduled artifact inspection pending |
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
- GitHub run `30607067442`: 1 frontend test failed; Nightly skipped.
- GitHub runs `30330904855`, `30424693088`, `30515165945`: Nightly runner
  unavailable until cancellation.

## Verification Verdict

`NOT_VERIFIED`. Phase 22 must remain open.
