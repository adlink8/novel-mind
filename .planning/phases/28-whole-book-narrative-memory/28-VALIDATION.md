# Phase 28 Validation Strategy

| Slice | Fixture | Proof |
|---|---|---|
| Recovery | crash at every stage, retry, cancel | resume without restart-all |
| Terminality | partial long book | completed/isolated/blocked, no pending |
| Hierarchy | uncertain boundaries, gaps/overlaps | continuous ranges/lineage |
| Reuse | unchanged/changed chapter | carry-forward and dirty closure |
| Closure | dimension partial/failure | dimension statuses and progress |
| Safety | pointer mutation attempt | candidate-only fail closed |

Quick: cd backend; pytest tests/unit/narrative_memory -q. Wave: integration narrative-memory.
Gate: adversarial safety, long-book dry run, and DB manifest recompute.
Human UAT: start/pause/resume, isolate chapter, inspect dependent arc/global status, verify
no production pointer changes.
