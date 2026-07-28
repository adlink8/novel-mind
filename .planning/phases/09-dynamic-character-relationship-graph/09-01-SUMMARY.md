---
phase: 09-dynamic-character-relationship-graph
plan: 01
subsystem: database
tags: [postgresql, sqlalchemy, alembic, relationship-graph, append-only, pydantic]
requires:
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: analysis_versions and timeline spoiler/full-book lineage
  - phase: 04-llm
    provides: knowledge_relation_judgments accepted/accepted source gate
provides:
  - Immutable relationship observation authority (8 ORM tables)
  - Strict fiction-only edge/override/graph envelope contracts
  - Alembic 11relobserve01 with physical append-only triggers
affects: [09-02, 09-03, 09-04, 09-05, relationship-pipeline, relationship-api]
tech-stack:
  added: []
  patterns: [append-only observation + superseding override INSERT, interface-first graph envelopes]
key-files:
  created:
    - backend/app/models/relationship.py
    - backend/app/schemas/relationship.py
    - backend/migrations/versions/11_relationship_observations.py
    - backend/tests/integration/relationships/test_persistence.py
  modified:
    - backend/app/models/__init__.py
key-decisions:
  - "Phase 09 edge types are only ally/enemy/family/mentor/romantic; causes/precedes/same_entity remain non-edges."
  - "Accepted observations and protective overrides are physically append-only via PostgreSQL triggers; supersession is always INSERT."
  - "Legacy character_relations is left untouched and never used as Phase 09 truth."
patterns-established:
  - "Relationship authority tables bind owner/novel/analysis_version + Phase 04 source_judgment_id + evidence checksum + idempotency_key."
  - "Downstream plans consume RelationshipGraphEnvelope and override schemas without inventing alternate edge enums."
requirements-completed: [REQ-REL-01, REQ-REL-02, REQ-REL-05]
duration: 28min
completed: 2026-07-15
---

# Phase 09 Plan 01: Relationship Observation Contracts and Migration Summary

**PostgreSQL append-only relationship observation authority with strict fiction-only Pydantic contracts, evidence/override lineage, and Alembic `11relobserve01`.**

## Performance

- **Duration:** 28 min
- **Started:** 2026-07-15T00:00:00Z
- **Completed:** 2026-07-15T00:28:00Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Defined eight authority ORM classes: build run, candidate, judgment, accepted observation, evidence link, character identity override, relationship override, projection audit.
- Locked strict Pydantic enums and graph/override/evidence/degradation envelopes for Plans 02–04.
- Migrated `10analysistime01` → `11relobserve01` with composite indexes and UPDATE/DELETE rejection triggers on accepted facts and overrides.
- Proved legacy `character_relations` is not observation truth and remains empty of fabricated Phase 09 facts after migration.

## Task Commits

1. **Tasks 1–3: contracts, migration, persistence suite** - `f4f9809` (feat)

**Plan metadata:** (this SUMMARY commit follows)

## Files Created/Modified

- `backend/app/models/relationship.py` — eight Mapped authority models with check/unique/index contracts.
- `backend/app/models/__init__.py` — export new ORM classes for Alembic metadata discovery.
- `backend/app/schemas/relationship.py` — strict edge/transition/override/graph envelopes.
- `backend/migrations/versions/11_relationship_observations.py` — ordered table create + append-only triggers.
- `backend/tests/integration/relationships/test_persistence.py` — contract + real PostgreSQL migration/append-only/idempotency proofs.

## Decisions Made

- Used repository metadata-create migration pattern (same as Phase 08) rather than hand-written column DDL.
- Enforced append-only at the database boundary with a shared `relationship_append_only_guard()` trigger function for observations and both override tables.
- Kept `CharacterRelation` unchanged and excluded from new graph contracts per D-04.

## Deviations from Plan

None - plan executed exactly as written within declared files (plus empty package `__init__.py` for the relationships test package).

## Issues Encountered

- Default `alembic` without `NOVELMIND_DATABASE_URL` targets port 5432 (not running). Verification used CI PostgreSQL on `127.0.0.1:5433` via the integration fixture / env override, matching Phase 08 practice.
- `alembic check` still reports pre-existing Phase 07 index drift (`ix_chunk_hierarchy_nodes_build_id` missing, `idx_text_chunks_hierarchy_node` extra). Outside 09-01 ownership; reported separately and not hidden.

## Commands and Test Results

```text
.venv\Scripts\python.exe -m pytest tests/integration/relationships/test_persistence.py -k "contract" -q
# 10 passed, 3 deselected

.venv\Scripts\python.exe -m pytest tests/integration/relationships/test_persistence.py -q
# 13 passed

alembic current (NOVELMIND_DATABASE_URL → CI 5433)
# 11relobserve01 (head)

alembic check
# FAILED: pre-existing Phase 07 index drift only (not Phase 09 tables)
```

## Known Stubs

None.

## Self-Check: PASSED

- All plan `files_modified` exist on disk.
- Production commit `f4f9809` present with `feat(09-01)` message.
- Targeted suite: 13 passed, 0 skipped.
- Acceptance criteria: eight ORM classes, fiction edge rejection, FKs to `analysis_versions` and `knowledge_relation_judgments`, append-only UPDATE/DELETE rejection, empty observations after migration.

## User Setup Required

None - no external service configuration required beyond existing CI PostgreSQL.

## Next Phase Readiness

- Ready for **09-02**: candidate/evidence packages, judgment gates, and observation worker against these contracts.
- Phase 10/11 product code intentionally untouched.

---
*Phase: 09-dynamic-character-relationship-graph*
*Completed: 2026-07-15*
