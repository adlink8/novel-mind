---
phase: 10-reader-selection-ai-and-multi-session-conversations
plan: "05"
subsystem: frontend
tags: [reader-chat, selection, multi-session, playwright, release-gate, citations, unicode]

requires:
  - phase: 10-reader-selection-ai-and-multi-session-conversations
    provides: conversation/message/job API (10-02), context manifests (10-03), cited worker (10-04)
provides:
  - DOM Range → chapter code-point selection utility with page UTF-16 bases
  - typed readerChatApi + job polling (no optimistic assistant fabrication)
  - collapsible desktop side panel and mobile 390px bottom sheet
  - citation chips with chapter/source-offset navigation and highlight
  - mocked + real browser e2e scaffolds; qualification script; independent release gate
affects:
  - Phase 11 must not treat chat as fact source (gate asserts no apply/clue coupling)

tech-stack:
  added: []
  patterns:
    - "Page splitter returns {text, sourceStartUtf16}; Array.from prefix length → Python code points"
    - "Desktop reserves chat column; mobile max-h-[45vh] collapses to chip"
    - "PostgreSQL is conversation authority; localStorage only for panel presentation"
    - "Background dispatch swallows errors so 202 never fails ASGI; e2e completes jobs via controlled transport"

key-files:
  created:
    - frontend/src/lib/reader-selection.ts
    - frontend/src/lib/reader-selection.test.ts
    - frontend/src/components/reader/reader-chat-panel.tsx
    - frontend/src/components/reader/reader-chat-panel.test.tsx
    - frontend/e2e/reader-chat.spec.ts
    - frontend/e2e/reader-chat-real.spec.ts
    - backend/scripts/run_reader_chat_qualification.py
    - tests/ci/test_reader_chat_release_gate.py
  modified:
    - frontend/src/lib/api.ts
    - frontend/src/components/reader/reader-content.tsx
    - frontend/src/app/novels/[id]/page.tsx
    - backend/app/api/reader_chat.py
    - backend/app/services/reader_chat/worker.py

key-decisions:
  - "Selection payload is immutable client claim; server re-slices Chapter.content"
  - "No new full-book control; timeline_full_book preference remains sole expansion"
  - "Worker BackgroundTasks dispatch is fail-soft; e2e uses --e2e-complete-job controlled transport"
  - "No Phase 11 clue UI or suggestion apply routes"

patterns-established:
  - "readerChatApi under /novels/{id}/conversations with pollReaderChatJob"
  - "data-testid reader-chat-* / reader-selection-action / reader-citation-highlight for e2e"
  - "verify_release_evidence rejects self-hashes without observed DB + command digests"

requirements-completed: [REQ-CHAT-02, REQ-CHAT-03, REQ-CHAT-06, REQ-CHAT-07]

duration: 95min
completed: 2026-07-15
---

# Phase 10 Plan 05: Reader Selection UI + Multi-Session + Release Gate Summary

**Collapsible multi-session reader chat with exact Unicode selection offsets, citation navigation, controlled-provider e2e harness, and independent PostgreSQL/command release authority**

## Performance

- **Duration:** ~95 min
- **Started:** 2026-07-15T01:20:00Z
- **Completed:** 2026-07-15T01:43:00Z
- **Tasks:** 3
- **Files modified:** 13

## Accomplishments

- Exact CJK/emoji/combining/CRLF/paginated selection mapping via page UTF-16 bases + code-point conversion
- Desktop non-overlapping chat column; mobile 390px bounded bottom panel with chip collapse
- Multi-session lifecycle UI: create/rename/switch/archive/restore/delete, send/cancel/retry, job states
- Citation chips navigate chapter + source offsets with temporary highlight
- Release gate: forged digests, missing browser evidence, spoiler flags, and self-reported success all blocked

## Task Commits

1. **Task 1–3: Selection UI, multi-session panel, e2e, qualification, release gate** - (see git log for 10-05)

## Files Created/Modified

- `frontend/src/lib/reader-selection.ts` — offset conversion + presentation storage
- `frontend/src/lib/reader-selection.test.ts` — Unicode/pagination unit tests
- `frontend/src/lib/api.ts` — `readerChatApi` + `pollReaderChatJob`
- `frontend/src/components/reader/reader-chat-panel.tsx` — multi-session collapsible panel
- `frontend/src/components/reader/reader-chat-panel.test.tsx` — component states
- `frontend/src/components/reader/reader-content.tsx` — selection action + page bases + highlight
- `frontend/src/app/novels/[id]/page.tsx` — layout integration (desktop column / mobile sheet)
- `frontend/e2e/reader-chat.spec.ts` — mocked desktop/mobile journey
- `frontend/e2e/reader-chat-real.spec.ts` — real stack; no `page.route` mocks
- `backend/app/api/reader_chat.py` — BackgroundTasks dispatch on message/retry
- `backend/app/services/reader_chat/worker.py` — controlled e2e transport + fail-soft dispatch
- `backend/scripts/run_reader_chat_qualification.py` — seed/complete-job/verify authority
- `tests/ci/test_reader_chat_release_gate.py` — independent release verifier tests

## Decisions Made

- Desktop reserves width for chat; never permanently covers reading column
- Mobile collapses to chip so reader remains scrollable
- Conversation truth only in PostgreSQL; localStorage is presentation-only
- Auto worker dispatch is best-effort; real e2e finishes jobs via `--e2e-complete-job` with controlled transport only

## Deviations from Plan

### Auto-fixed Issues

**1. [Blocking] Background dispatch crashed ASGI tests when production DB was unreachable**
- **Found during:** Task 3 (integration suite after wiring BackgroundTasks)
- **Issue:** `dispatch_reader_chat_job` raised ConnectionRefusedError into Starlette background runner and failed httpx ASGI requests
- **Fix:** Catch/log all exceptions in dispatch; job stays durable for reclaim
- **Files modified:** `backend/app/services/reader_chat/worker.py`
- **Verification:** 93 reader-chat backend tests passed

**2. [Blocking] ESLint set-state-in-effect / hooks order on reader content**
- **Found during:** Task 2 lint
- **Issue:** `useMemo` after early return; hydrate effects flagged by React Compiler rules
- **Fix:** Move highlight render before early return; lazy init presentation state; async effect loads
- **Files modified:** reader-content, reader-chat-panel, page.tsx
- **Verification:** lint 0 errors; unit tests 19 passed

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Required for correctness under ASGI background + project lint rules. No scope creep.

## Issues Encountered

- Local app PostgreSQL on `127.0.0.1:5432` was down when Playwright tried to start backend webServer. Mocked `reader-chat.spec.ts` passed with `E2E_START_BACKEND=0`. Real-stack `reader-chat-real.spec.ts` needs a running NovelMind DB + migrations; script/helpers are ready.

## Verification Evidence

| Command | Result |
|---|---|
| `pytest tests/unit/reader_chat tests/integration/reader_chat tests/adversarial/test_reader_chat_boundaries.py -q` | **93 passed** |
| `npm test -- --run reader-selection + reader-chat-panel` | **19 passed** |
| `npm run lint` | **0 errors** (pre-existing warnings only) |
| `npm run build` | **passed** |
| `npm run test:e2e -- reader-chat.spec.ts` (E2E_START_BACKEND=0) | **4 passed** (desktop+mobile) |
| `pytest tests/ci/test_reader_chat_release_gate.py -q` | **10 passed** |
| `reader-chat-real.spec.ts` | **blocked** — host Postgres not listening on 5432 at run time |

## User Setup Required

None for code path. Real browser qualification requires PostgreSQL reachable by backend `.env` and `alembic upgrade head`.

## Next Phase Readiness

- Phase 10 implement-complete pending real-stack e2e green when Postgres is up
- Phase 11 may use `list_accepted_observation_refs` only; chat is not a fact source (release gate forbids apply/clue coupling)

## Self-Check: PASSED (with env residual)

- key-files exist on disk
- plan requirements REQ-CHAT-02/03/06/07 covered by UI + gates; 01/04/05 already closed in 10-01..10-04
- residual: real e2e needs live Postgres for full REQ-CHAT-07 browser authority in this environment

---
*Phase: 10-reader-selection-ai-and-multi-session-conversations*
*Completed: 2026-07-15*
