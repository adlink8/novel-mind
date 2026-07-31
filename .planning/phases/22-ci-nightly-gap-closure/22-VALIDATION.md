# Phase 22 Validation Ledger

## Gap Status

| Gap | Status | Evidence |
|---|---|---|
| 22-G1 | VERIFIED_LOCAL | stable workflow classifications; CI policy 116 passed; relationship test 5/5 repeated; full frontend 248 passed |
| 22-G2 | PLANNED | self-hosted runner starvation and artifact gap confirmed |
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
- `PYTHONPATH=. pytest tests/ci -q`: 116 passed.
- `actionlint v1.7.12`: passed.
- GitHub run `30607067442`: 1 frontend test failed; Nightly skipped.
- GitHub runs `30330904855`, `30424693088`, `30515165945`: Nightly runner
  unavailable until cancellation.

## Verification Verdict

`NOT_VERIFIED`. Phase 22 must remain open.
