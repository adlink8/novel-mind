# Phase 27-02 Summary: Relationship Evolution

## Result

Completed a real-book candidate relationship run on analysis version `50`
using the Vertex structured judgment path and the existing evidence/interval
gates. The earlier run on version `49` remains as an audit comparison; version
`50` explicitly records semantic output as `llm_judgment` intake.

- Build run `67`: `completed`
- Candidates/judgments: `41/41`
- Accepted: `25`; human review: `14`; rejected: `2`
- Accepted transitions: `24 establish`, `1 end`
- Closed intervals: `6` with non-null `valid_to_chapter`
- Evidence links: `63`
- All accepted rows have `intake_kind=llm_judgment` and Vertex lineage.
- No active timeline pointer or Reader Chat consumer was changed.

The first attempt exposed formal-DB drift: `relationship_observations` lacked
the existing `intake_kind` contract. The authorized migration path was applied
from Alembic revision `18appsetting1` through `25relintake02`; no later
migrations were run.

## Implementation

- Added `backend/scripts/_phase27_relationship_candidate.py`.
- The script creates a versioned candidate, binds the worker explicitly to
  `vertex_google/<vertex_model>`, and preserves rejected/review audit rows.

## Verification

- `python -m py_compile scripts/_phase27_relationship_candidate.py`
- `python -m alembic upgrade 25relintake02`
- Formal PostgreSQL counts verified run `67`, transitions, intervals, intake,
  evidence links, and unchanged timeline pointer.
