# Phase 37 Validation — Nyquist Gate

## Frozen fixtures

One short novel snapshot with state at chapter 2: valid continuation; wrong character knowledge; impossible causal order; unresolved clue incorrectly paid off; missing world rule; citation outside package; explicit allowed divergence. Provider responses are deterministic fixture payloads.

## Automated tests (planned)

```text
cd backend; pytest tests/unit/derivative_generation -q
cd backend; pytest tests/adversarial/test_derivative_generation_boundaries.py -q
cd backend; pytest tests/integration/test_derivative_generation.py -q
```

Map: `REQ-CRE-05` → package contract/hash/ref tests; `REQ-CRE-06` → contradiction and override adversarial tests; `REQ-FORK-03` → branch/no-original-write integration. Wave 0 creates fixtures and test files; commands not run now.

## Manual UAT

Select fork/cutoff/intent, inspect compiled package before generation, request candidate, inspect gate report, accept only valid candidate, submit explicit divergence, retry/ cancel a job, and confirm Original retrieval/eval/facet remain unchanged. Capture model/prompt/schema/config hashes and verdict.

## Failure policy

Invalid schema/evidence/out-of-cutoff/constraint violation is `blocked` or `needs_override`, never silently repaired or published. Provider success without gate success is not a pass.
