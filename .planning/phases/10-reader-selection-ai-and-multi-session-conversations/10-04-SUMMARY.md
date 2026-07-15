---
phase: 10-reader-selection-ai-and-multi-session-conversations
plan: "04"
subsystem: backend
tags: [reader-chat, gateway, dual-budget, worker, cancel, retry, citations, adversarial]

requires:
  - phase: 10-reader-selection-ai-and-multi-session-conversations
    provides: contracts (10-01), job lifecycle API (10-02), frozen manifests (10-03)
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: persistent reservation/call/lease patterns mirrored for chat
provides:
  - dual-scope conversation+novel budget reservation and settlement
  - strict ReaderAnswerEnvelope gateway with one budgeted schema/citation repair
  - durable generation worker with lease/cancel/retry/exact recovery and atomic publish
  - versioned fiction-only answer prompt and reader_chat→balanced routing
  - unit + PostgreSQL generation + adversarial boundary suites
affects:
  - 10-05 reader UI selection panel and browser qualification

tech-stack:
  added: []
  patterns:
    - "Dual ledger lock order: novel then conversation; both must reserve before network"
    - "Worker loads frozen evidence rows only; never rebuilds under newer reading progress"
    - "Post-cancel provider responses settle usage then discard before assistant commit"
    - "Succeeded attempt stores validated envelope in usage JSON for exact recovery / cache_hit"

key-files:
  created:
    - backend/app/services/reader_chat/budget.py
    - backend/app/services/reader_chat/gateway.py
    - backend/app/services/reader_chat/worker.py
    - backend/prompts/reader_chat_answer.v1.txt
    - backend/tests/unit/reader_chat/test_budget.py
    - backend/tests/unit/reader_chat/test_gateway.py
    - backend/tests/integration/reader_chat/test_generation_jobs.py
    - backend/tests/adversarial/test_reader_chat_boundaries.py
  modified:
    - backend/app/services/ai_router.py
    - backend/app/services/ai_service.py
    - backend/app/services/reader_chat/__init__.py
    - backend/app/services/reader_chat/conversations.py

key-decisions:
  - "reader_chat routes to balanced; deployment freezes on job process, no transparent fallback"
  - "Attempt statuses stay within CHECK (started/succeeded/failed/cache_hit/cancelled/outcome_unknown)"
  - "Exact recovery reuses envelope stored on succeeded attempt.usage without a new provider call"
  - "Chat worker imports no timeline/relationship mutation services; no apply/accept routes"

patterns-established:
  - "DualBudgetRepository creates paired reservations under one reservation_key per attempt"
  - "business_validate_answer enforces manifest refs + no-evidence + display-only suggestions"
  - "Audit logs only job_id/response_hash/counts — never raw prompt/evidence/output"

requirements-completed: [REQ-CHAT-04, REQ-CHAT-05, REQ-CHAT-07]

duration: 70min
completed: 2026-07-15
---

# Phase 10 Plan 04: Cited Answer Worker Summary

**Durable evidence-only generation with dual budgets, strict citations, cancel/retry/lease recovery, and fail-closed adversarial gates — no domain writes**

## Performance

- **Duration:** ~70 min
- **Started:** 2026-07-15T02:00:00Z
- **Completed:** 2026-07-15T03:10:00Z
- **Tasks:** 3
- **Files modified:** 12

## Accomplishments

- Dual conversation+novel PostgreSQL budget reservation/settlement with unknown-pricing and ceiling fail-closed
- Strict gateway: schema + citation repair (exactly one), no stream/retry/remote thread IDs
- Worker: lease reclaim, cancel before/during/after call, frozen manifest retry, atomic assistant+citations publish
- Adversarial import/route/prompt/citation gates; full reader-chat suite green

## Task Commits

1. **Task 1–3: tests, worker/gateway/budget, safety scan** - `c0b11d0` (feat)

**Plan metadata:** included in `c0b11d0`

## Files Created/Modified

- `backend/app/services/reader_chat/budget.py` — DualBudgetGate + DualBudgetRepository
- `backend/app/services/reader_chat/gateway.py` — ReaderChatGateway + ModelDeployment
- `backend/app/services/reader_chat/worker.py` — run_reader_chat_worker / dispatch
- `backend/prompts/reader_chat_answer.v1.txt` — evidence-only policy
- `backend/app/services/ai_router.py` — `reader_chat -> balanced`
- `backend/app/services/ai_service.py` — optional timeout/**extra for transport kwargs
- `backend/tests/unit/reader_chat/test_budget.py` — 5 unit
- `backend/tests/unit/reader_chat/test_gateway.py` — 8 unit
- `backend/tests/integration/reader_chat/test_generation_jobs.py` — 9 PostgreSQL
- `backend/tests/adversarial/test_reader_chat_boundaries.py` — 10 adversarial

## Decisions Made

- Persist validated envelope on attempt usage for restart/exact recovery without re-calling the provider
- Load frozen evidence from `reader_context_evidence_refs` by job.manifest checksum; never call ProductionContextBuilder on retry
- Cancel mid-call: settle dual ledgers, mark job cancelled, zero assistant rows

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Missing critical path] Exact recovery payload**
- **Found during:** Task 2 design
- **Issue:** Attempt model stores hashes only; process-death after call needs re-publish source
- **Fix:** On success, store `envelope` inside attempt.usage JSON; recovery records `cache_hit`
- **Files modified:** `gateway.py`, `worker.py`
- **Verification:** idempotent completion integration test

**2. [Rule 2 - Blocking] freeze_manifest_from_stored checksum vs stored prompt_inputs**
- **Found during:** Task 2
- **Issue:** Production builder appends fields after checksum; rehydrate always mismatches
- **Fix:** Worker trusts job↔manifest checksum match and loads evidence rows directly
- **Files modified:** `worker.py`
- **Verification:** generation integration suite

## Verification

```
pytest tests/unit/reader_chat tests/integration/reader_chat tests/adversarial/test_reader_chat_boundaries.py -q
→ 93 passed

pytest tests/unit/reader_chat/test_budget.py tests/unit/reader_chat/test_gateway.py \
  tests/integration/reader_chat/test_generation_jobs.py tests/adversarial/test_reader_chat_boundaries.py -q
→ 32 passed

ruff check app/services/reader_chat app/api/reader_chat.py app/services/ai_router.py \
  tests/unit/reader_chat tests/integration/reader_chat tests/adversarial/test_reader_chat_boundaries.py
→ All checks passed

Forbidden capability scan (langchain|langgraph|agent_tool|remote_thread|apply_suggestion|accept_suggestion)
→ empty
```

## Next

Execute **10-05** reader selection UI, collapsible multi-session panel, citations, browser qualification, release gate.
