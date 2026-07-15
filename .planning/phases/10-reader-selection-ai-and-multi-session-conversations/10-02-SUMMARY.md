---
phase: 10-reader-selection-ai-and-multi-session-conversations
plan: "02"
subsystem: api
tags: [reader-chat, fastapi, postgresql, idempotency, idor, conversations, jobs]

requires:
  - phase: 10-reader-selection-ai-and-multi-session-conversations
    provides: reader-chat ORM models, schemas, migration 12readerchat01
provides:
  - owner-scoped multi-conversation lifecycle API under /api/novels/{novel_id}/conversations
  - durable message submit with selection+manifest+job in one transaction (202)
  - row-locked next_sequence, client_message_id idempotency, after_sequence replay
  - job get/cancel/retry surfaces; hard-delete cancels nonterminal jobs then cascades
  - PostgreSQL lifecycle + IDOR isolation integration tests
affects:
  - 10-03 context assembly (replaces DeterministicContextBuilder)
  - 10-04 generation worker (consumes queued jobs)
  - 10-05 reader UI

tech-stack:
  added: []
  patterns:
    - "Every route starts from require_owned_novel; child IDs scoped owner+novel+conversation → 404"
    - "Injected ContextBuilder protocol; Plan 02 ships deterministic selection-only graph"
    - "with_for_update on conversation row for monotonic sequence allocation"
    - "create_message_safe nested savepoint recovers concurrent client_message_id races"

key-files:
  created:
    - backend/app/api/reader_chat.py
    - backend/app/services/reader_chat/__init__.py
    - backend/app/services/reader_chat/conversations.py
    - backend/tests/integration/reader_chat/conftest.py
    - backend/tests/integration/reader_chat/test_conversations_api.py
    - backend/tests/integration/reader_chat/test_owner_isolation.py
  modified:
    - backend/app/main.py

key-decisions:
  - "DeterministicContextBuilder validates Chapter.content (undefer) until Plan 03 production assembly"
  - "Archived conversations return 409 on new messages; still readable via GET"
  - "DELETE marks nonterminal jobs cancelled then hard-deletes conversation graph in one transaction"
  - "Module-scoped alembic migrate + unique seed suffixes for PG API tests (no TRUNCATE races)"

patterns-established:
  - "MessageAccepted 202 envelope pairs MessageView + GenerationJobView"
  - "List responses are metadata-only (no body/excerpt/selection_text)"
  - "OpenAPI paths under conversations never include apply/accept-suggestion"

requirements-completed: [REQ-CHAT-02, REQ-CHAT-07]

duration: 25min
completed: 2026-07-15
---

# Phase 10 Plan 02: Multi-Session Lifecycle API Summary

**Owner-scoped conversation CRUD with row-locked sequences, client idempotency, durable 202 message+manifest+job commit, and PostgreSQL IDOR matrix**

## Performance

- **Duration:** 25 min
- **Started:** 2026-07-15T01:00:50Z
- **Completed:** 2026-07-15T01:12:41Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Full D-02 lifecycle: create/list/rename/archive/restore/delete under owned novel
- POST message commits user message + selection + manifest + selection evidence + queued job atomically; returns 202
- Concurrent appends receive unique monotonic sequences; duplicate client_message_id returns original message/job
- Cross-owner novel/conversation/message/job matrix returns stable 404 with no metadata leak
- OpenAPI conversation paths registered; no apply/accept-suggestion mutation surface

## Task Commits

1. **Task 1–3: Lifecycle API, isolation tests, OpenAPI/Ruff verify** - `ae905a8` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP docs commit follows)

## Files Created/Modified

- `backend/app/api/reader_chat.py` — FastAPI router for conversations/messages/jobs
- `backend/app/services/reader_chat/conversations.py` — transactional service + DeterministicContextBuilder
- `backend/app/services/reader_chat/__init__.py` — package export
- `backend/app/main.py` — register reader_chat_router under `/api/novels`
- `backend/tests/integration/reader_chat/conftest.py` — module migrate + ASGI/PG client
- `backend/tests/integration/reader_chat/test_conversations_api.py` — lifecycle/concurrency/replay tests
- `backend/tests/integration/reader_chat/test_owner_isolation.py` — IDOR 404 matrix

## Decisions Made

- Injected `ContextBuilder` so Plan 03 can replace the stub without changing lifecycle semantics
- Selection validation uses `undefer(Chapter.content)` because content is deferred for list performance
- Retry job endpoint re-queues eligible terminal/paused jobs reusing frozen manifest checksum (worker semantics in 10-04)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Async MissingGreenlet on deferred Chapter.content / expired timestamps**
- **Found during:** Task 2 (message create + patch)
- **Issue:** Deferred `Chapter.content` and post-flush `updated_at` triggered sync lazy IO under asyncpg
- **Fix:** `undefer(Chapter.content)` in context builder; `await db.refresh()` after mutating flush
- **Files modified:** `backend/app/services/reader_chat/conversations.py`
- **Verification:** 9 integration tests pass
- **Committed in:** `ae905a8`

**2. [Rule 1 - Bug] empty_postgres per-test DROP SCHEMA raced with async pools**
- **Found during:** Task 3 (full suite)
- **Issue:** Repeated schema reset crashed PostgreSQL / left undefined tables mid-suite
- **Fix:** Module-scoped migrate once + unique seed suffixes; NullPool; no TRUNCATE of users
- **Files modified:** `backend/tests/integration/reader_chat/conftest.py`, test files
- **Verification:** 9 passed in 13s
- **Committed in:** `ae905a8`

---

**Total deviations:** 2 auto-fixed (async ORM hygiene + PG test isolation)
**Impact on plan:** No API contract change; tests more stable.

## Issues Encountered

None remaining. CI PostgreSQL must be up on service-lock port for integration tests.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Ready for `10-03` context assembly (replace DeterministicContextBuilder) and/or remaining wave-2 plans
- Do not mark 10-03 done; next incomplete plan remains first without SUMMARY
- Generation worker (10-04) can consume `queued` jobs without changing lifecycle semantics

## Self-Check: PASSED

- Planned artifacts exist on disk
- `git log --grep=10-02` includes production commit `ae905a8`
- Targeted suite: 9 passed (conversations_api + owner_isolation)
- OpenAPI: conversation paths present; no apply/accept-suggestion
- Ruff clean on plan paths
- No Phase 09 files modified in this plan commit

---
*Phase: 10-reader-selection-ai-and-multi-session-conversations*
*Completed: 2026-07-15*
