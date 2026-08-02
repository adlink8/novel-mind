# Phase 34-01 Verification

## Status

**VERIFIED for the authorized local revision-export scope.**

## Evidence

- `backend`: `pytest tests/test_fanfiction.py tests/integration/test_postgres_migrations.py -q` — 12 passed.
- `backend`: `pytest tests/contract/test_openapi_contract.py -q` — 7 passed.
- `backend`: Ruff passed for the changed export/API/test modules; compileall passed.
- `frontend`: targeted API/editor tests — 20 passed.
- `frontend`: `npx tsc --noEmit` — passed.
- `frontend`: targeted ESLint — passed.

## Acceptance

- Export requests require an explicit positive `revision_id` and support only `markdown` or
  `epub`.
- The service loads the owner-scoped revision and exports its stored content/hash, not mutable
  latest state.
- Repeated EPUB exports for the same revision are byte-identical.
- Export response headers have a safe ASCII fallback and UTF-8 filename parameter.
- No provider call, Original Canon read, Narrative Memory mutation, active-pointer mutation,
  Reader Chat cutover, deployment, or remote publication occurred.

## Remaining

Phase 34-02 deployment baseline and 34-03 final closeout remain blocked by missing deployment
authorization and unresolved Phase 22/26–30 evidence. Phase 33 real generation remains blocked
by provider/budget/price/correct NM candidate data prerequisites.
