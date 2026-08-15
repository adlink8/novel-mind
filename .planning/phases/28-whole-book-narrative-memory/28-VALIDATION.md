# Phase 28 Validation Strategy

| Slice | Fixture | Proof |
|---|---|---|
| Recovery | crash at every stage, retry, cancel | resume without restart-all |
| Terminality | partial long book | completed/isolated/blocked, no pending |
| Hierarchy | uncertain boundaries, gaps/overlaps | continuous ranges/lineage |
| Reuse | unchanged/changed chapter | carry-forward and dirty closure |
| Closure | dimension partial/failure | dimension statuses and progress |
| Safety | pointer mutation attempt | candidate-only fail closed |
| Chapter artifact context | bounded digests, previous/next context, continuity notes | source/input hash, cutoff, max length and spoiler-policy parity; digest is not indexed and is not an EvidenceRef |
| Candidate hierarchy artifacts | outline/mainline candidate with gaps and uncertainty | source lineage is complete, candidate-only, no Canon or pointer write |
| Progress transport | SSE notification, DB checkpoint, browser reload/reconnect | existing Agent SSE/Job transport is reused; DB replay is authoritative and `/analyze/stream` remains absent |
| Scope exclusion | emotion-memory-shaped input/output attempt | no emotional memory artifact, field, index, or consumer is admitted |

Quick: cd backend; pytest tests/unit/narrative_memory -q; run the targeted Agent Skill contract
tests for Phase 28. Wave: integration narrative-memory and Agent Runtime transport tests.
Gate: adversarial safety, long-book dry run, DB manifest recompute, artifact parity, and
reconnect-after-reload evidence.
Human UAT: start/pause/resume, isolate chapter, inspect dependent arc/global status, verify
no production pointer changes, reload during progress, reconnect, and verify the DB checkpoint
reconstructs state without browser memory. Confirm no future facts leak through next hints and no
emotional memory is present.
