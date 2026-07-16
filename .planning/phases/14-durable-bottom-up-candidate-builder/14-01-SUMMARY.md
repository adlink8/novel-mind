# 14-01 SUMMARY — Durable control plane + Chapter State worker

**Date:** 2026-07-16  
**Status:** complete

## Delivered

- Alembic revision `14membuild01` (down_revision `13memoryauth01`) creating builder-only tables:
  - `narrative_memory_build_runs`
  - `narrative_memory_build_stages`
  - `narrative_memory_build_budget_ledgers` / `_reservations`
  - `narrative_memory_build_model_call_attempts` (append-only)
  - `narrative_memory_build_reports` (append-only)
- Completed-stage immutability trigger; one live run per explicit version (no novel-scoped active_key).
- Strict builder contracts, package rebinding, budget reservation before transport, exact-cache identity.
- Lease-safe Chapter State worker: eligibility match, cancel polling, fail-closed unknown pricing, sibling isolation.

## Evidence

- Unit: `test_builder_contracts.py`, `test_builder_packages.py`
- PG: `test_builder_control_pg.py`, `test_chapter_state_worker_pg.py`
- Ruff clean on plan files.

## Semantics frozen

- `provider_calls_allowed` from Phase 12 report only (checksum matched to version).
- Budget reserve → transport → insert final attempt row (no UPDATE on attempts).
- Unknown price / budget ceiling → zero transport calls.
- Chapter failure isolates siblings; completed artifacts byte-identical on resume.
