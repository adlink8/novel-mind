---
phase: 13-candidate-memory-contracts-and-provenance-authority
plan: 03
subsystem: provenance
tags: [provenance, manifest, seal, reslice, no-pointer, dag]
requires:
  - phase: 13-02
    provides: strict contracts and eligibility-bound CandidateAuthority
provides:
  - pure graph/range/source structural validation
  - database-row deterministic manifests and immutable seals
  - append-only structural validation reports
  - fresh-observer no-pointer side-effect proof
affects: [14-bottom-up-builder, 17-quality-qualification]
tech-stack:
  added: []
  patterns: [DB-row manifest, Chapter re-slice authority, fresh observer snapshot]
key-files:
  created:
    - backend/app/services/narrative_memory/provenance.py
    - backend/app/services/narrative_memory/manifests.py
    - backend/tests/unit/narrative_memory/test_provenance.py
    - backend/tests/unit/narrative_memory/test_manifests.py
    - backend/tests/integration/narrative_memory/test_provenance_pg.py
    - backend/tests/integration/narrative_memory/test_no_pointer_side_effects.py
  modified: []
key-decisions:
  - "Every claim needs a direct source link; graph ancestry never satisfies evidence closure."
  - "Manifest identity is computed only from sorted PostgreSQL authority rows."
  - "Sealing and blocked reports never create or move production pointers."
patterns-established:
  - "seal_and_report: load → structural + re-slice → seal → append report"
  - "Fresh observer session snapshots pointer tables before/after candidate lifecycle"
requirements-completed: [V08-MEM-03, V08-MEM-04, V08-MEM-05]
duration: 45min
completed: 2026-07-16
---

# Phase 13 Plan 03: Provenance Closure and Manifest Gates Summary

**End-to-end candidate evidence closure: legal DAG/range validation, server-side Chapter re-slicing, deterministic database-row manifests, immutable seals, append-only structural reports, and independent no-pointer proofs.**

## Performance

- **Duration:** ~45 min
- **Completed:** 2026-07-16
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Implemented pure `validate_memory_graph` for containment transitions, parent ranges, cycles, chapter-state coverage, middle-range continuity, global bypass rejection, and direct claim→source link requirements.
- Implemented lossless scoped loading, claim payload revalidation, Phase 07 leaf + `Chapter.content[start:end]` re-slice closure, deterministic manifest recomputation, seal insert, and append-only validation reports.
- Proved Unicode re-slice success, stale content / missing-link / post-seal insert / UPDATE / DELETE failures, and fresh-observer byte-equivalent production pointer snapshots.
- Static capability scan on Phase 13 write/seal package excludes provider gateway, pointer, promotion, Chroma, and Reader Chat tokens.

## Task Commits

1. **Task 1: Validate graph, range, and direct claim-to-leaf closure** — `59b29b8` (feat)
2. **Task 2: Re-slice authoritative text and seal database-derived manifest** — `11f7191` (feat)
3. **Task 3: Adversarial PostgreSQL and fresh-observer no-pointer proof** — `32338ed` (test)

## Files Created/Modified

- `backend/app/services/narrative_memory/provenance.py` — Pure structural validation.
- `backend/app/services/narrative_memory/manifests.py` — Load, re-slice, manifest, seal, report.
- `backend/tests/unit/narrative_memory/test_provenance.py` — Graph/range/DAG unit matrix.
- `backend/tests/unit/narrative_memory/test_manifests.py` — Order-independence and sensitivity.
- `backend/tests/integration/narrative_memory/test_provenance_pg.py` — PG seal/adversarial suite.
- `backend/tests/integration/narrative_memory/test_no_pointer_side_effects.py` — Fresh observer + static scan.

## Verification Evidence

- Unit provenance + manifests: **10 passed**
- Integration provenance + no-pointer: **9 passed**
- Combined Phase 13 narrative_memory authority suite (unit contracts/audit + 13-01/02/03 integration): **60 passed**, 0 skip
- `ruff check` on all 13-03 files: **All checks passed**

## Decisions Made

- Validation reports use `qualified_candidate` / `blocked` structural verdicts only; no Phase 17 quality/release semantics.
- Caller/worker checksums are never accepted as manifest inputs.
- Optional domain source refs remain enrichment; every link still requires a Phase 07 evidence leaf.

## Deviations from Plan

None material. Optional domain row deep-lineage lookups for timeline/relationship/clue IDs are structural presence checks only; full domain-table revalidation is deferred to builder phases that own those optional sources.

## Issues Encountered

- Strict claim payload revalidation from JSONB requires `validate_json` (not `validate_python`) under `strict=True`.
- Append-only triggers raise on `execute`, not only `commit`; tests catch exceptions at the execute boundary and reseed after rollback.

## User Setup Required

None.

## Next Phase Readiness

- Phase 13 candidate authority, contracts, provenance, and no-pointer boundary are complete.
- Phase 14 may start only after `13-VERIFICATION.md` reports `status: passed` and must keep Phase 12 `provider_calls_allowed` as the sole provider gate.

## Self-Check: PASSED

- All six files exist and are committed.
- Commits `59b29b8`, `11f7191`, `32338ed` present.
- Targeted unit + PostgreSQL tests and Ruff pass.

---
*Phase: 13-candidate-memory-contracts-and-provenance-authority*
*Completed: 2026-07-16*
