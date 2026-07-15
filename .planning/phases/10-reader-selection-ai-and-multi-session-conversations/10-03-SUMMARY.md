---
phase: 10-reader-selection-ai-and-multi-session-conversations
plan: "03"
subsystem: backend
tags: [reader-chat, selection, context-manifest, spoiler, unicode, phase09-reader, checksum]

requires:
  - phase: 10-reader-selection-ai-and-multi-session-conversations
    provides: reader-chat persistence contracts and ContextBuilder injection point from 10-01/10-02
  - phase: 09-dynamic-character-relationship-graph
    provides: load_filtered_relationship_graph and list_accepted_observation_refs public readers
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: reading-progress cutoff policy and timeline evidence refs
provides:
  - exact code-point selection validation against owned Chapter.content
  - public resolve_chapter_cutoff shared with timeline query
  - immutable checksum-addressed visible-context manifests
  - RelationshipObservationReader protocol bound to Phase 09 (no null adapter)
  - ProductionContextBuilder as conversation lifecycle default
affects:
  - 10-04 generation worker (frozen manifest inputs)
  - 10-05 reader UI selection submission

tech-stack:
  added: []
  patterns:
    - "Client selection text/offsets/hashes are claims; server re-slices Chapter.content with undefer"
    - "Visible-set-first retrieval then deterministic rank/cap; omit counts recorded, no mid-excerpt cuts"
    - "Retry rehydrates freeze_manifest_from_stored checksum; never rebuild under newer progress"
    - "Phase 09 consumed only via RelationshipObservationReader; ORM imports forbidden in Phase 10 retrieval"

key-files:
  created:
    - backend/app/services/reader_chat/context.py
    - backend/app/services/reader_chat/retrieval.py
    - backend/tests/unit/reader_chat/test_context.py
    - backend/tests/integration/reader_chat/test_context_manifest.py
  modified:
    - backend/app/services/timeline/query.py
    - backend/app/services/reader_chat/conversations.py
    - backend/app/services/reader_chat/__init__.py
    - backend/app/models/reader_chat.py

key-decisions:
  - "resolve_chapter_cutoff is the public shared cutoff; _chapter_cutoff remains a compatibility alias"
  - "timeline_full_book is the only full-book authority; request flags alone never expand scope"
  - "Phase 09 bound via Phase09RelationshipObservationReader → load_filtered_relationship_graph (+ evidence refs); runtime outages become source_unavailable"
  - "ProductionContextBuilder replaces DeterministicContextBuilder as ConversationService default; stub kept for tests"

patterns-established:
  - "canonical_manifest_checksum over sorted JSON payload excluding checksum field"
  - "dialogue framing is CONVERSATIONAL_FRAMING_NOT_EVIDENCE with body hashes only"
  - "JSON server_default uses '{}' without PostgreSQL ::json cast so SQLite create_all stays green"

requirements-completed: [REQ-CHAT-01, REQ-CHAT-03, REQ-CHAT-07]

duration: 55min
completed: 2026-07-15
---

# Phase 10 Plan 03: Selection Validation and Visible-Context Assembly Summary

**Server-side Unicode-exact selection validation, shared spoiler cutoff, visible-set-first retrieval with Phase 09 read-only consumer, and checksum-frozen context manifests wired into conversation lifecycle**

## Performance

- **Duration:** 55 min
- **Started:** 2026-07-15T01:00:00Z
- **Completed:** 2026-07-15T01:55:00Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments

- Exact half-open code-point selection checks (CJK/emoji/combining/CRLF/stale hash/forged refs)
- Immutable context manifests with deterministic checksums and retry-stable rehydrate path
- Phase 09 consumer protocol without Phase 09 file edits or null adapter; outages explicit
- ProductionContextBuilder becomes default for 10-02 conversation message submission

## Task Commits

1. **Task 1–3: Selection/context tests, implementation, and spoiler closure** - `1748768` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP docs commit follows)

## Files Created/Modified

- `backend/app/services/reader_chat/context.py` — validate_selection, assemble_context_manifest, freeze_manifest_from_stored
- `backend/app/services/reader_chat/retrieval.py` — visible retrieval + RelationshipObservationReader + Phase 09 binder
- `backend/app/services/timeline/query.py` — public `resolve_chapter_cutoff` (+ alias + ruff-safe order keys)
- `backend/app/services/reader_chat/conversations.py` — ProductionContextBuilder default
- `backend/app/services/reader_chat/__init__.py` — export builders
- `backend/app/models/reader_chat.py` — SQLite-safe JSON server_default
- `backend/tests/unit/reader_chat/test_context.py` — 20 unit tests
- `backend/tests/integration/reader_chat/test_context_manifest.py` — 4 PostgreSQL spoiler/manifest tests

## Decisions Made

- Bind Phase 09 through `load_filtered_relationship_graph` (and evidence via `list_accepted_observation_refs` on the same service) without importing relationship ORM models
- Keep DeterministicContextBuilder for isolation/tests; production default is ProductionContextBuilder
- Fix 10-01 `server_default=text("'{}'::json")` to `"'{}'"` so SQLite timeline spoiler suite create_all remains green (Phase 08 regression)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Deferred Chapter.content greenlet failure**
- **Found during:** Task 2 (integration)
- **Issue:** `Chapter.content` is deferred; accessing it after scalar load raised MissingGreenlet under asyncpg
- **Fix:** `select(Chapter).options(undefer(Chapter.content))` in validate_selection
- **Files modified:** `backend/app/services/reader_chat/context.py`
- **Verification:** 4 PostgreSQL context tests pass
- **Committed in:** `1748768`

**2. [Rule 1 - Bug] SQLite create_all broken by PG-only JSON defaults**
- **Found during:** Task 3 (timeline spoiler regression)
- **Issue:** `server_default=text("'{}'::json")` makes SQLite DDL fail, breaking Phase 08 spoiler tests using in-memory create_all
- **Fix:** Use dialect-portable `server_default=text("'{}'")`
- **Files modified:** `backend/app/models/reader_chat.py`
- **Verification:** HEAD timeline spoiler tests pass under SQLite
- **Committed in:** `1748768`

**3. [Rule 2 - Missing Critical] Wire ProductionContextBuilder into 10-02 lifecycle**
- **Found during:** Task 2 (after concurrent 10-02 merge)
- **Issue:** Plan 02 left DeterministicContextBuilder as default; production assembly would not run on message submit
- **Fix:** Add ProductionContextBuilder and set as ConversationService default
- **Files modified:** `backend/app/services/reader_chat/conversations.py`, `__init__.py`
- **Verification:** 9 conversation API/IDOR tests still pass with production builder
- **Committed in:** `1748768`

---

**Total deviations:** 3 auto-fixed (2 bugs, 1 missing critical wiring)
**Impact on plan:** Necessary for correctness and concurrent 10-02 integration; no scope creep into generation worker or UI.

## Issues Encountered

- Local uncommitted WIP test `test_running_candidate_ignores_reading_progress_cutoff` expects RUNNING_CANDIDATE cutoff skip not present on HEAD query behavior; not part of 10-03. HEAD spoiler tests verified green.
- Concurrent 10-02 completed during execution; STATE/ROADMAP merged carefully after 10-02 docs commit.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Ready for 10-04 cited answer worker using frozen manifests and dual budgets
- Context assembly is production-default on POST messages; worker must never re-retrieve under newer progress
- Phase 09 files remain untouched

## Self-Check: PASSED

- key-files.created exist on disk
- `git log --grep=10-03` includes feat commit `1748768`
- Unit 20 + PG context 4 + conversation API/IDOR 9 + HEAD spoilers 2 = 35 targeted tests passed
- Ruff clean on plan paths
- Phase 09 models/services not modified

---
*Phase: 10-reader-selection-ai-and-multi-session-conversations*
*Completed: 2026-07-15*
