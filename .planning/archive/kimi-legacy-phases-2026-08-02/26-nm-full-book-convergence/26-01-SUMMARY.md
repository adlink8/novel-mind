# Phase 26-01 Summary — Failure Diagnosis and Recovery

## Steps

1. Audited the preserved backup in an isolated PostgreSQL database without modifying the active CI database.
2. Classified persisted chapter-state stages and budget/provider evidence.
3. Recorded candidate-only and no-pointer invariants before and after the provider attempt.

## Must-Haves

- Source and candidate identity are evidence-backed: novel 91, 515 chapters, version 1.
- Existing stage counts are reproducible: 117 completed, 33 failed, 365 pending, plus one pending arc/volume stage.
- Active pointer and Reader Chat consumer state remain unchanged.

## Verification

- Backup-derived audit database inspection completed; novel 91 has 515 chapters and candidate version 1.
- The explicit `_nm_requeue_failed.py` operator path requeued 47 failed stages only after the worker lease expired; dry-run and `--apply` output were both recorded.
- Repeated Gemini recovery reached 515 completed chapter states. Final requeue/recovery left zero failed or pending chapter stages before parent aggregation.
- Candidate-only invariants held: no Narrative Memory active pointer exists for owner 2 / novel 91, and no Reader Chat consumer state was changed.

## Test, Fix, and Confirm

Diagnosis and recovery are complete for the authorized candidate scope. Failed-stage reason
codes remain in the append-only call/stage history, all 515 chapter stages reached completed,
and no promotion or active-pointer mutation was executed.
