---
phase: 11-clue-and-foreshadow-tracking
plan: "01"
subsystem: database
tags: [clue, foreshadow, lifecycle, sqlalchemy, alembic, pydantic, postgresql, append-only]

requires:
  - phase: 10-reader-selection-ai-and-multi-session-conversations
    provides: single Alembic head 12readerchat01 (chat never a clue fact source)
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: version/pointer/override patterns and timeline_full_book spoiler preference
provides:
  - Strict fiction-only clue lifecycle/evidence/link/override/API contracts
  - Pure lifecycle transition validator and append-only replay (no mutable current-state authority)
  - Clue-owned PostgreSQL ORM tables (12): run, version, machine clue, evidence, lifecycle, link, override, budget, call, pointer, journal
  - Alembic revision 11cluetrack01 with append-only triggers and paid_off order guard
affects:
  - 11-02 candidate recall and LLM gates
  - 11-03 durable worker and spoiler API
  - 11-04 analysis workspace UI
  - 11-05 qualification and release gate

tech-stack:
  added: []
  patterns:
    - "Current clue state is always derived by replaying lifecycle events (+ overrides later)"
    - "Machine versions, human overrides and pointer journals are separate append-only tables"
    - "ORM metadata is the single DDL contract; Alembic creates tables from Base.metadata"
    - "PostgreSQL triggers reject UPDATE/DELETE on lifecycle, overrides and pointer journal"

key-files:
  created:
    - backend/app/schemas/clue.py
    - backend/app/models/clue.py
    - backend/migrations/versions/11_clue_tracking.py
    - backend/tests/unit/clues/test_schemas.py
    - backend/tests/unit/clues/test_lifecycle.py
    - backend/tests/integration/clues/test_persistence.py
  modified:
    - backend/app/models/__init__.py
    - backend/app/schemas/__init__.py

key-decisions:
  - "down_revision is 12readerchat01 (current single head), not the plan's outdated 10analysistime01"
  - "Clue analysis uses clue-owned run/version tables so concurrent timeline active_key uniqueness is untouched"
  - "paid_off requires distinct cue+payoff coordinates with strict narrative order (app + PG trigger)"
  - "Chat text and similarity_score are structurally forbidden on link contracts (extra=forbid / typed None)"

patterns-established:
  - "LEGAL_TRANSITIONS + replay_lifecycle are the pure authority used by later persistence/query layers"
  - "source_unavailable is a first-class link validation status for Phase 09 reader outages"
  - "Clue tables never treat reader_chat rows as lifecycle evidence"

requirements-completed: [REQ-CLUE-02, REQ-CLUE-03, REQ-CLUE-05]

duration: 55min
completed: 2026-07-15
---

# Phase 11 Plan 01: Clue Lifecycle Contracts and PostgreSQL Authority Summary

**Fiction-only clue contracts with append-only five-state lifecycle replay, clue-owned PostgreSQL authority tables, and Alembic `11cluetrack01` chained after `12readerchat01`.**

## Performance

- **Duration:** 55 min
- **Started:** 2026-07-15T02:00:00Z
- **Completed:** 2026-07-15T02:55:00Z
- **Tasks:** 3
- **Files modified:** 10

## Accomplishments

- Strict Pydantic enums/models for version lineage, machine clues, evidence roles, lifecycle events, typed links, human overrides, semantic judgment and visible envelopes (`extra=forbid`).
- Pure transition legality + evidence-order validator and `replay_lifecycle` with no mutable authoritative current-state write path.
- Twelve clue-owned ORM tables with CHECK/UNIQUE/indexes; physical append-only triggers on lifecycle/overrides/pointer journal; paid_off order guard.
- Migration `11cluetrack01` revises `12readerchat01`; package registries export models/schemas without touching relationship or chat business logic.

## Task Commits

1. **Tasks 1–3: contracts, ORM/migration, registration + persistence suite** - `5293b52` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP docs commit follows)

## Files Created/Modified

- `backend/app/schemas/clue.py` — strict contracts + pure lifecycle replay/validator
- `backend/app/models/clue.py` — twelve Mapped authority models
- `backend/app/models/__init__.py` — export clue ORM classes for metadata discovery
- `backend/app/schemas/__init__.py` — export clue schema contracts
- `backend/migrations/versions/11_clue_tracking.py` — Alembic upgrade/downgrade + triggers
- `backend/tests/unit/clues/test_schemas.py` — 8 unit contract tests
- `backend/tests/unit/clues/test_lifecycle.py` — 8 unit lifecycle tests
- `backend/tests/integration/clues/test_persistence.py` — 12 contract + PostgreSQL proofs

## Decisions Made

- Chained Alembic after live head `12readerchat01` (user override / current truth) rather than plan text `10analysistime01`.
- Kept AnalysisRun/AnalysisVersion timeline uniqueness intact via fully clue-owned run/version tables.
- Enforced paid_off cue/payoff narrative order both in pure Python and a PostgreSQL BEFORE INSERT trigger.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule — Dependency/head correctness] Migration down_revision**
- **Found during:** Task 2 (migration)
- **Issue:** Plan text said `down_revision 10analysistime01`, but repository single head is `12readerchat01` after Phase 09/10.
- **Fix:** `down_revision = "12readerchat01"` so one head remains at `11cluetrack01`.
- **Files modified:** `backend/migrations/versions/11_clue_tracking.py`
- **Verification:** `alembic heads` → `11cluetrack01 (head)` only
- **Committed in:** `5293b52`

---

**Total deviations:** 1 auto-fixed (head chain)
**Impact on plan:** Required for correctness; no scope creep.

## Issues Encountered

- Unit tests initially failed the classification gate for missing `pytest.mark.unit`; fixed by adding package markers consistent with timeline/reader-chat tests.

## Commands and Test Results

```text
.\.venv\Scripts\python.exe -m pytest tests/unit/clues/test_schemas.py tests/unit/clues/test_lifecycle.py -q -x
# 16 passed

.\.venv\Scripts\python.exe -m pytest tests/integration/clues/test_persistence.py -q -x
# 12 passed (CI PostgreSQL 127.0.0.1:5433)

.\.venv\Scripts\python.exe -m pytest tests/unit/clues tests/integration/clues/test_persistence.py -q -x
# 28 passed

.\.venv\Scripts\python.exe -m alembic heads
# 11cluetrack01 (head)
```

## Verification Mapping

| Must-have | Evidence |
|---|---|
| No lifecycle history overwrite path | PG append-only trigger tests on `clue_lifecycle_events` / `clue_overrides` |
| paid_off needs early cue + later payoff | unit evidence-order tests + `clue_paid_off_order_guard` |
| Machine versions / overrides / journals separate | distinct ORM tables + superseding INSERT test |
| No Phase 09/10 business implementation | only new clue models/schemas/migration/tests; chat/relationship runtime untouched |

## Next

- Execute `11-02-PLAN.md` (candidate recall, evidence packages, Phase 09 null source protocol, LLM gates).
- Do not import reader-chat as a clue fact source; relationship reader outages → `source_unavailable`.
