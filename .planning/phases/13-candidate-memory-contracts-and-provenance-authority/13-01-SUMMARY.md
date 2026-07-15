---
phase: 13-candidate-memory-contracts-and-provenance-authority
plan: 01
subsystem: database
tags: [postgresql, sqlalchemy, alembic, provenance, append-only]
requires:
  - phase: 12-read-only-asset-audit-and-eligibility
    provides: verified owner-scoped hierarchy eligibility and frozen audit lineage
provides:
  - seven-table candidate-only narrative-memory PostgreSQL authority
  - composite owner/novel/version and Phase 07 leaf foreign keys
  - physical append-only, seal, source-closure, range, transition, and DAG guards
affects: [13-02-strict-contracts, 13-03-provenance-gates, 14-bottom-up-builder]
tech-stack:
  added: []
  patterns: [scoped composite foreign keys, immutable candidate sidecar, deferred constraint trigger]
key-files:
  created:
    - backend/app/models/narrative_memory.py
    - backend/migrations/versions/13_narrative_memory_authority.py
    - backend/tests/integration/narrative_memory/test_candidate_authority_pg.py
  modified:
    - backend/app/models/__init__.py
key-decisions:
  - "Phase 13 authority contains exactly version/node/claim/edge/source-link/manifest/report tables; execution control remains Phase 14."
  - "PostgreSQL repeats owner/novel/version scope and independently enforces exact Phase 07 leaf/build/snapshot closure."
  - "A manifest seal prevents late candidate content inserts; all seven authority tables reject UPDATE and DELETE."
patterns-established:
  - "Candidate sidecar: explicit immutable versions have no mutable status or implicit current selector."
  - "Database defense-in-depth: composite FKs plus PL/pgSQL guards enforce invariants independently of callers."
requirements-completed: [V08-MEM-01, V08-MEM-04, V08-MEM-05]
duration: 20min
completed: 2026-07-15
---

# Phase 13 Plan 01: Narrative-memory PostgreSQL Authority Summary

**Seven immutable, owner-scoped candidate authority tables with exact Phase 07 leaf lineage, database sealing, and deferred graph guards—without any production selector or execution control plane.**

## Performance

- **Duration:** 20 min
- **Started:** 2026-07-15T18:56:00+08:00
- **Completed:** 2026-07-15T19:16:00+08:00
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Added version, node, claim, edge, source-link, manifest, and validation-report ORM authority with frozen source/hierarchy/model/config lineage.
- Added Alembic revision `13memoryauth01` directly from `11cluetrack01`, with complete downgrade cleanup of migration-owned triggers, functions, and tables.
- Proved real PostgreSQL scope isolation, exact evidence-leaf closure, legal containment/ranges/DAGs, physical append-only behavior, seal enforcement, and absence of any narrative-memory pointer/control-plane table.

## Task Commits

1. **Task 1: Define the isolated candidate ORM authority** — `fb19e52` (feat)
2. **Task 2: Add the migration, constraints, and physical guards** — `c11c38d` (feat)
3. **Task 3: Prove database scope, immutability, and candidate-only boundaries** — `da2d77f` (test)

## Files Created/Modified

- `backend/app/models/narrative_memory.py` — Seven candidate-only ORM tables and closed database constraints.
- `backend/app/models/__init__.py` — Registers and exports all seven authority models.
- `backend/migrations/versions/13_narrative_memory_authority.py` — Additive schema and database guard functions/triggers.
- `backend/tests/integration/narrative_memory/test_candidate_authority_pg.py` — Metadata, migration, adversarial PostgreSQL, immutability, and no-pointer proofs.

## Verification Evidence

- `python -m alembic heads` → one head: `13memoryauth01`.
- `upgrade head -> downgrade 11cluetrack01 -> upgrade head` → passed; downgrade inspection found no narrative-memory tables or guard functions.
- `pytest tests/integration/narrative_memory/test_candidate_authority_pg.py -q -x` → **9 passed**, no skips.
- `ruff check app/models/narrative_memory.py migrations/versions/13_narrative_memory_authority.py tests/integration/narrative_memory/test_candidate_authority_pg.py` → **All checks passed**.
- Existing repository warnings remain for unregistered/unavailable `pytest-timeout`; no Phase 13 test was skipped or failed.

## Decisions Made

- Kept the authority strictly candidate-only: no run, stage, checkpoint, provider, API, worker, active pointer, promotion, rollback, Chroma, or Reader Chat table/path was introduced.
- Used a direct composite FK to Phase 07 `(build_id, node_id)` and a trigger to close the owner/novel/build/evidence-level/chapter/offset/hash/snapshot dimensions that the legacy key cannot express.
- Used a `DEFERRABLE` edge constraint trigger so legal batches remain possible while transition, containment, and DAG invariants stay database-authoritative.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- `alembic check` still reports two pre-existing non-Phase-13 index drifts: missing `ix_chunk_hierarchy_nodes_build_id` and database-only `idx_text_chunks_hierarchy_node`. The new seven narrative-memory tables produce no detected drift. These legacy index differences were not modified because doing so would violate the additive, candidate-only scope of 13-01.
- The first raw-SQL test fixture omitted `novels.chapter_count` and `novels.word_count`, whose ORM defaults are not server defaults. The fixture was corrected and the full suite then passed.

## User Setup Required

None - no external service configuration or dependency was added.

## Next Phase Readiness

- Ready for 13-02 strict Pydantic contracts and explicit-version persistence against this authority.
- 13-02 must preserve the no-provider/no-pointer boundary and must not create Phase 14 run/stage/checkpoint tables.
- Legacy Alembic index drift is independently visible but does not block the tested Phase 13 candidate tables or migration roundtrip.

## Self-Check: PASSED

- All four implementation/test files exist.
- Commits `fb19e52`, `c11c38d`, and `da2d77f` are present.
- Targeted PostgreSQL tests and Ruff pass.

---
*Phase: 13-candidate-memory-contracts-and-provenance-authority*
*Completed: 2026-07-15*
