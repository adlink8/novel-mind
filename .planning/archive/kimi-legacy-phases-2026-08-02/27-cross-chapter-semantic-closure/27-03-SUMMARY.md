# Phase 27-03 Summary: Clue Payoff Closure

## Result

Completed the corrected full-book clue worker as a candidate-only run. Run `32`
qualified version `28` without promoting the clue active pointer.

- Candidates: `32/32`
- Version `28`: `validated`
- Lifecycle transitions: `16 candidate→active`, `14 active→reinforced`,
  `11 reinforced→paid_off`
- Payoff evidence: `11` references; total lifecycle evidence references: `340`
- No clue active-pointer write; active clue pointer remained version `24`.

## Implementation

- Added `ClueWorkerRuntime.promote_candidate`, defaulting to `True` so normal
  production dispatch behavior is unchanged.
- Added candidate-only qualification that snapshots and validates the version,
  then completes the run with `candidate_pointer_unchanged`.
- Added `backend/scripts/_phase27_clue_candidate.py` for a full-book candidate
  run using the configured Vertex deployment.

## Verification

- `python -m py_compile app/services/clues/worker.py scripts/_phase27_clue_candidate.py`
- Formal PostgreSQL checks confirmed 32/32 completion, payoff state ordering,
  positive payoff evidence, and unchanged active pointer.
