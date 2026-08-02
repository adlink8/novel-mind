# Phase 35 Validation — Nyquist Gate

## Contract tests

- Fixtures: two owners, one novel, three spaces, two forks, two source versions, cutoff at chapter 2/whole-book flag。
- Unit: strict DTO rejects missing scope, cross-space citation, stale snapshot, future cutoff and unknown namespace。
- Integration: Original read is unchanged after derivative create/index/eval/facet attempt; fork retrieval returns only matching branch。
- Negative tests: derivative text inserted into every reachable write path must be rejected or remain absent from Original index, eval dataset and facet output。

## Commands (planned, not run in research)

```text
cd backend; pytest tests/unit/canon_fork tests/adversarial/test_canon_space_isolation.py -q
cd backend; pytest tests/adversarial/test_canon_contamination.py -q
cd frontend; npm test -- canon-fork
```

## Sampling / UAT

Per task: contract tests. Per wave: PostgreSQL scope/negative suite. Phase gate: backend adversarial + frontend contract + browser fork-selection flow green. Manual UAT: owner A creates fork, retrieves chapter-2 branch, attempts future/other-fork access; confirm blocked and Original search/eval/facet counts unchanged. Record evidence as hashes and query IDs, not prose-only screenshots.

## Failure policy

Any cross-space row, citation mismatch or Original mutation is a hard BLOCKED verdict. Do not repair by deleting data in the test; isolate the fixture and report the offending write path.
