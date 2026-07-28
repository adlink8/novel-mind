# 14-03 SUMMARY — Global aggregation + manifest + report

**Date:** 2026-07-16  
**Status:** complete

## Delivered

- `global_builder.py` + package helpers for singleton Global Story from validated middle parents only.
- Worker `global_story:book` stage gated on all parent stages completed; blocked parents → zero Global transport.
- Manifest stage uses Phase 13 `load_candidate_snapshot` / `compute_manifest_from_snapshot` / `seal_and_report`.
- `builder_report.py` derives totals from persisted stages/attempts/ledgers; outcomes are `completed_candidate|partial|paused|cancelled|failed` only (not Phase 17 `qualified_candidate`).

## Evidence

- Unit: `test_global_packages.py`, `test_builder_report.py`
- PG: `test_global_worker_pg.py`, `test_builder_manifest_pg.py` (no narrative-memory pointer table; production chunk pointers unchanged).

## Notes

- Full-book structural seal success depends on continuous chapter/middle/global coverage in candidate authority; partial builds remain unsealed with explicit stage reasons.
