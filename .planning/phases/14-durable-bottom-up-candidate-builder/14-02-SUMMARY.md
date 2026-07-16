# 14-02 SUMMARY — Arc/Volume planning and aggregation

**Date:** 2026-07-16  
**Status:** complete

## Delivered

- `arc_planner.py`: explicit volume preference when exact continuous cover validates; otherwise deterministic consecutive windows with stable stage keys and plan checksum.
- Parent packages load only completed revalidated Chapter State authority with direct Phase 07 leaf links.
- Worker materializes arc_volume_aggregate stages after chapter completion; failed children mark containing parent `blocked_dependency` without a provider call.

## Evidence

- Unit: `test_arc_planner.py`, `test_arc_packages.py`
- PG: `test_arc_worker_pg.py` (partial chapter failure / parent blocking)
- Regression: chapter worker suite still green.

## Boundary policy example

- Chapters `[1,2,3,4,5]`, window=2 → ranges `1-2`, `3-4`, `5` with checksum over policy/source/ranges only.
- `blocked_closure_for_chapter(2)` → containing arc stage + `global_story:book`.
