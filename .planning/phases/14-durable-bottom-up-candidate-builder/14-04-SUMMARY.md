# 14-04 SUMMARY — Optional sources, isolation, CLI, regression

**Date:** 2026-07-16  
**Status:** complete

## Delivered

- `optional_sources.py`: read-only timeline/relationship/clue adapters with `non_empty|healthy_empty|unavailable|lineage_mismatch`.
- Failure isolation PG proof: completed sibling artifacts/checksums preserved across resume.
- Static/dynamic no-chat / no-pointer scans + CI contract tests.
- CLI `backend/scripts/run_narrative_memory_build.py`: `start|status|cancel|resume` requiring explicit owner/novel/version; forbidden promote/current/default/all-books flags.

## Evidence

- Unit: `test_optional_sources.py`
- PG: `test_optional_sources_pg.py`, `test_builder_failure_isolation_pg.py`, `test_builder_no_chat_no_pointer_pg.py`
- CI: `tests/ci/test_narrative_memory_builder_contract.py`
- Full Phase 14 suite: **37 passed**

## Hard boundaries held

- Reader Chat never imported into builder runtime modules.
- No `narrative_memory_active_pointers` table.
- Candidate-only: no promotion / consumer cutover.
