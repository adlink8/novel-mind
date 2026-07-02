---
phase: 04-llm
plan: 04-01-knowledge-data-contracts
subsystem: database
tags: [postgresql, sqlalchemy, alembic, pydantic, knowledge-graph, evidence-gates]
requires:
  - phase: 03-rag-eval
    provides: text_chunks, novels, users, and current Alembic head 518675fa18f8
provides:
  - PostgreSQL source-of-truth contracts for knowledge extraction runs, evidence refs, candidates, judgments, and review queue rows
  - Strict Pydantic LLM output schemas separated from API response schemas
  - SQLite-compatible regression tests for relationships, JSON fields, evidence refs, and domain profiles
affects: [04-02-candidate-packages-and-llm-judgment, 04-03-evidence-gates-and-projection, 04-04-evaluation-and-domain-fixtures]
tech-stack:
  added: []
  patterns:
    - SQLAlchemy audit tables with owner_id, novel_id, run_id, evidence_refs, status, and gate_status fields
    - Pydantic strict LLM schemas with extra fields forbidden and evidence refs required
key-files:
  created:
    - backend/app/models/knowledge.py
    - backend/app/schemas/knowledge.py
    - backend/migrations/versions/7bbf6b6c0d24_create_knowledge_contract_tables.py
    - backend/tests/test_knowledge_models.py
  modified:
    - backend/app/models/__init__.py
    - backend/app/schemas/__init__.py
key-decisions:
  - "Kept PostgreSQL as the source of truth; no Neo4j projection or accepted graph table was added in 04-01."
  - "Stored relation outputs as candidates, judgments, gate statuses, and review rows rather than accepted facts."
  - "Used domain_profile and ontology_profile to support fiction and history without renaming Novel."
patterns-established:
  - "Evidence-first contract: candidates and judgments carry evidence_refs and strict schemas reject empty evidence refs."
  - "Reviewability contract: relation candidates store recall_signals/package_snapshot, while judgments store raw_output/structured_output/gate_failures."
requirements-completed: [REQ-KG-01, REQ-KG-02]
duration: 22min
completed: 2026-07-02
---

# Phase 04 Plan 01: Knowledge Data Contracts Summary

**PostgreSQL-backed knowledge graph audit contracts with evidence refs, strict LLM output schemas, and reviewable candidate/judgment state.**

## Performance

- **Duration:** 22 min
- **Started:** 2026-07-02T06:46:20Z
- **Completed:** 2026-07-02T07:07:49Z
- **Tasks:** 1 implementation slice
- **Files modified:** 6

## Accomplishments

- Added seven SQLAlchemy knowledge contract models: extraction runs, evidence refs, entity candidates, event candidates, relation candidates, relation judgments, and review queue rows.
- Added Alembic revision `7bbf6b6c0d24` after `518675fa18f8`, with owner/novel/run foreign keys and indexes for status, gate status, relation type, and domain profile.
- Added strict Pydantic schemas for API-facing contracts and LLM outputs, with `extra="forbid"` on LLM schemas and evidence refs required.
- Added targeted tests proving relationship persistence, JSON fields, cascade behavior, unique evidence refs, schema strictness, and no accepted graph table in this plan.

## Task Commits

1. **Task 1: Knowledge graph data contracts** - `ea1f899` (feat)

## Files Created/Modified

- `backend/app/models/knowledge.py` - SQLAlchemy source-of-truth audit models and ontology/profile constants.
- `backend/app/schemas/knowledge.py` - request/response schemas plus strict LLM output schemas.
- `backend/migrations/versions/7bbf6b6c0d24_create_knowledge_contract_tables.py` - PostgreSQL migration for the knowledge contract tables and indexes.
- `backend/tests/test_knowledge_models.py` - SQLite-compatible model/schema regression tests.
- `backend/app/models/__init__.py` - exports knowledge models for `Base.metadata` discovery.
- `backend/app/schemas/__init__.py` - exports knowledge schemas.

## Decisions Made

- No LLM calls were added; this plan only creates contracts needed by later judging/gating plans.
- No accepted graph edge table was added, so candidates cannot bypass judgment and gate status into graph facts.
- `owner_id` is stored directly on knowledge tables and `novel_id` keeps Novel ownership joins available for owner isolation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed async SQLAlchemy test loading and transaction boundaries**
- **Found during:** Task 1 targeted pytest.
- **Issue:** Initial tests triggered async lazy-loading outside a greenlet and rollback expired ORM instances after an expected unique-key failure.
- **Fix:** Switched tests to explicit `selectinload`, cleared identity map after cascade delete, and cached IDs before rollback.
- **Files modified:** `backend/tests/test_knowledge_models.py`
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py -v` passed.
- **Committed in:** `ea1f899`

**Total deviations:** 1 auto-fixed bug.
**Impact on plan:** Test-only correction; no scope expansion.

## Issues Encountered

- System Python could not run pytest because `sqlalchemy` was not installed. Retried with project virtualenv `backend/.venv`, which has the required dependencies.
- Online Alembic verification could not run because PostgreSQL at `127.0.0.1:5432` refused connections. Migration syntax and head registration were verified offline.

## Verification

| Command | Result |
|---|---|
| `python -m py_compile backend/app/models/knowledge.py backend/app/schemas/knowledge.py backend/tests/test_knowledge_models.py` | Passed |
| `.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py -v` | Passed, 8 tests |
| `.\.venv\Scripts\python.exe -m ruff check app/models/knowledge.py app/schemas/knowledge.py tests/test_knowledge_models.py migrations/versions/7bbf6b6c0d24_create_knowledge_contract_tables.py` | Passed |
| `.\.venv\Scripts\python.exe -m alembic upgrade head` | Failed: PostgreSQL connection refused at `127.0.0.1:5432` |
| `.\.venv\Scripts\python.exe -m alembic heads` | Passed, head is `7bbf6b6c0d24` |
| `.\.venv\Scripts\python.exe -m alembic upgrade 518675fa18f8:head --sql` | Passed offline SQL generation for the new revision |

## Known Stubs

None. Stub scan hits were intentional test inputs for empty-list validation, not product placeholders or UI data stubs.

## User Setup Required

PostgreSQL must be running and reachable at the configured `NOVELMIND_DATABASE_URL` before online migration verification can pass.

## Next Phase Readiness

04-02 can now build deterministic candidate packages and structured LLM judgment persistence against the new run, evidence, candidate, and judgment tables. The remaining risk is online PostgreSQL migration execution, which still needs a running database.

---
*Phase: 04-llm*
*Completed: 2026-07-02*

## Self-Check: PASSED

- Found SUMMARY file.
- Found created model, schema, migration, and test files.
- Found implementation commit `ea1f899`.
