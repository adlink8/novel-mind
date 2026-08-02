# Phase 27-01 Summary: Timeline Causality

## Result

Completed a real-book candidate facet for owner `2` / novel `91` without moving
the active timeline pointer. Candidate version `48` cloned active version `14`
and processed all `1,933` timeline events in 29 overlapping semantic windows.

- Run `39`: `completed`, `phase27_candidate`
- Accepted causal edges: `177`
- Edge types: `causes=123`, `triggers=43`, `responds_to=10`, `blocks=1`
- Every accepted edge passed the two-endpoint evidence gate; invalid or
  out-of-window proposals were discarded.
- Provider audit: 29 succeeded calls, 5 schema-rejected calls, 1 prior
  outcome-unknown call; no reserved calls remain.
- Active timeline pointer remained version `14`; candidate version `48` has a
  complete manifest.

## Implementation

- Added `backend/scripts/_phase27_timeline_candidate.py` for resumable,
  evidence-gated candidate-only semantic closure.
- Candidate runs use explicit `phase27` run scope and never call promotion.
- Provider truncation and operator-stopped reservations are recoverable and
  remain visible in model-call audit rows.

## Verification

- `python -m py_compile scripts/_phase27_timeline_candidate.py`
- Formal PostgreSQL queries confirmed 29 completed windows, 177 edges, and
  active pointer version `14`.
