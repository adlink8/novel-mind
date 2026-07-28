---
phase: 10-reader-selection-ai-and-multi-session-conversations
plan: "01"
subsystem: database
tags: [reader-chat, sqlalchemy, alembic, pydantic, postgresql, budgets, citations]

requires:
  - phase: 09-dynamic-character-relationship-graph
    provides: single Alembic head 11relobserve01 and read-only relationship observations for later waves
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: durable job/budget/call lineage patterns and analysis_versions FK target
provides:
  - reader-chat ORM tables for conversations, messages, selections, manifests, evidence refs, citations
  - generation jobs, model call attempts, dual-scope budget ledgers and reservations
  - Alembic revision 12readerchat01 with user-role selection/manifest guards
  - strict ReaderAnswerEnvelope and SuggestionCandidate contracts without domain-write authority
affects:
  - 10-02 conversation lifecycle API
  - 10-03 context assembly
  - 10-04 generation worker and budgets
  - 10-05 reader UI

tech-stack:
  added: []
  patterns:
    - "ORM metadata is the single DDL contract; Alembic creates tables from Base.metadata"
    - "Partial unique index enforces one nonterminal generation job per user message"
    - "Dual-scope chat budgets (conversation + novel) with independent partial unique indexes"
    - "PostgreSQL trigger requires selection/manifest rows attach only to role=user messages"

key-files:
  created:
    - backend/app/models/reader_chat.py
    - backend/app/schemas/reader_chat.py
    - backend/migrations/versions/12_reader_chat_conversations.py
    - backend/tests/unit/reader_chat/test_contracts.py
    - backend/tests/integration/reader_chat/test_migration.py
  modified:
    - backend/app/models/__init__.py

key-decisions:
  - "down_revision is 11relobserve01 (single Phase 09 head at execution)"
  - "Chat budgets are separate reader_* tables, not FKs into analysis_budget_*"
  - "Suggestions require Literal[True] confirmation; no apply/confirm model"
  - "Hard-delete cascades private chat graph; novel-scoped ledger survives conversation delete"

patterns-established:
  - "reader_* tables never FK into timeline/relationship/clue fact tables"
  - "Fiction-only evidence source types: selection, hierarchy, timeline, knowledge, relationship_observation"
  - "validate_answer_against_manifest is the business gate for citation allowlists"

requirements-completed: [REQ-CHAT-01, REQ-CHAT-02, REQ-CHAT-04, REQ-CHAT-05]

duration: 45min
completed: 2026-07-15
---

# Phase 10 Plan 01: Reader-Chat Persistence Authority Summary

**Durable PostgreSQL conversations/messages with immutable selection+manifest lineage, citation allowlists, generation jobs, dual-scope budgets, and strict ReaderAnswerEnvelope contracts**

## Performance

- **Duration:** 45 min
- **Started:** 2026-07-15T09:15:00Z
- **Completed:** 2026-07-15T10:00:00Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments

- Ten reader-chat authority tables with CHECK/UNIQUE/partial unique indexes and cascade delete
- User-message-only selection/manifest PostgreSQL trigger guard
- Strict Pydantic answer envelope rejecting uncited blocks, extra fields, unknown refs, and unconfirmed suggestions
- Migration `12readerchat01` revises single Phase 09 head; upgrade/downgrade verified on CI PostgreSQL

## Task Commits

1. **Task 1–3: Persistence contracts, ORM/migration, and verification** - `d53b1d3` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP docs commits follow)

## Files Created/Modified

- `backend/app/models/reader_chat.py` — ten ORM entities and fiction-only enums
- `backend/app/models/__init__.py` — export reader-chat models
- `backend/app/schemas/reader_chat.py` — API and ReaderAnswerEnvelope contracts
- `backend/migrations/versions/12_reader_chat_conversations.py` — Alembic upgrade/downgrade
- `backend/tests/unit/reader_chat/test_contracts.py` — 18 unit contract tests
- `backend/tests/integration/reader_chat/test_migration.py` — 10 PostgreSQL migration/cascade tests

## Decisions Made

- Bound migration `down_revision` to actual head `11relobserve01` rather than guessing
- Kept chat budget ledgers independent of Phase 08 analysis budgets so conversation hard-delete cannot erase novel-level chat ceilings incorrectly (novel ledger has null conversation_id and survives delete)
- Enforced assistant-cannot-own-selection via DB trigger, not only service code

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Split budget duplicate-scope assertion into its own transaction**
- **Found during:** Task 3 (integration verification)
- **Issue:** IntegrityError on duplicate conversation ledger rolled back the committed dual ledgers when tested in the same transaction, causing a false FK failure on reservations
- **Fix:** Move the duplicate-insert negative case into a separate `engine.begin()` block after dual ledgers commit
- **Files modified:** `backend/tests/integration/reader_chat/test_migration.py`
- **Verification:** 28 targeted tests pass
- **Committed in:** `d53b1d3`

---

**Total deviations:** 1 auto-fixed (test isolation)
**Impact on plan:** No production schema change; only test transaction hygiene.

## Issues Encountered

- `alembic check` against CI PostgreSQL reports pre-existing index drift on `chunk_hierarchy_nodes` / `text_chunks` unrelated to Phase 10 tables. Single head `12readerchat01` is confirmed; Phase 10 tables create/drop cleanly. Not fixed (out of plan scope).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Ready for `10-02` owner-scoped conversation lifecycle API consuming these models/schemas
- Do not start Phase 11 clue product code from this plan

## Self-Check: PASSED

- All six planned artifacts exist on disk
- `git log --grep=10-01` includes production commit `d53b1d3`
- Targeted unit + migration suite: 28 passed
- Alembic heads: single `12readerchat01`
- Ruff clean on plan paths
- No Phase 09 files modified

---
*Phase: 10-reader-selection-ai-and-multi-session-conversations*
*Completed: 2026-07-15*
