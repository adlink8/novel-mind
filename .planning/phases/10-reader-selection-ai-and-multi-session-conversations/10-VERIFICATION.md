---
phase: 10-reader-selection-ai-and-multi-session-conversations
verified: 2026-07-15T09:50:00Z
status: partial
score: 19/20 must-have truths verified (1 PARTIAL)
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
  gaps_closed: []
  gaps_remaining:
    - real desktop+390px Playwright stack (reader-chat-real.spec.ts) not executed — host Postgres 5432 offline
  regressions: []
---

# Phase 10: Reader Selection AI and Multi-Session Conversations — Verification Report

**Phase Goal:** 让读者从阅读器选取原文后，在同一本小说的多个持久会话中进行证据受限、防剧透的 AI 对话。  
**Verified:** 2026-07-15T09:50:00Z  
**Status:** partial  
**Re-verification:** No — first independent verification after 10-01..10-05

## Goal Achievement

Phase 10 vertical slice is implement-complete in code and automated suites: PostgreSQL conversation authority, owner-scoped multi-session API, server-verified Unicode selection + visible-context manifests, Phase 09 read-only relationship consumption (not a null adapter), dual-budget cited-answer worker, collapsible reader UI, mocked browser e2e, and independent release-gate tests.

Independent re-run results this session:

| Suite | Result |
|---|---|
| Backend unit/integration/adversarial reader_chat | **93 passed**, 0 failed, 0 skipped (~109s) |
| CI `tests/ci/test_reader_chat_release_gate.py` | **10 passed** |
| Frontend Vitest `reader` | **19 passed** (2 files) |
| Real Playwright `reader-chat-real.spec.ts` | **Not run** — `127.0.0.1:5432` TcpTestSucceeded=False |
| Mocked Playwright `reader-chat.spec.ts` | Not re-run this session (10-05: 4 passed) |

**Phase 09 reader:** production path uses `Phase09RelationshipObservationReader` → `relationship_graph_query_service.load_filtered_relationship_graph` (+ optional `list_accepted_observation_refs`). Constructor **raises** if the public contract is absent (no null adapter).

**Clue product UI:** no clue/foreshadow product surface under `frontend/src/components/reader/`; panel tests assert absence of 应用建议/确认写入/线索 apply UX. Home/eval copy mentions generic “线索” search language only — not Phase 11 product.

### Observable Truths (plan must-haves)

| # | Truth | Plan | Status | Evidence |
|---:|---|---|---|---|
| 1 | A user message cannot exist without immutable selection and visible-context lineage | 10-01 | ✓ VERIFIED | `backend/app/models/reader_chat.py` + migration FKs; unit contracts + migration cascade tests in suite |
| 2 | Conversation messages have stable owner-scoped ordering and replayable citation relationships | 10-01 | ✓ VERIFIED | `next_sequence` uniqueness + citation FK to `reader_context_evidence_refs`; API replay tests |
| 3 | Generation jobs, model attempts and conversation/novel budget reservations are PostgreSQL facts | 10-01 | ✓ VERIFIED | job/attempt/ledger ORM + migration tests; dual-scope ledgers |
| 4 | Suggestion candidates have no domain-write authority | 10-01 | ✓ VERIFIED | schemas force `requires_explicit_confirmation`; OpenAPI has no apply/accept-suggestion paths (6 conversation paths checked) |
| 5 | Users can create, rename, list, switch by reading, archive, restore and delete multiple conversations per owned novel | 10-02 | ✓ VERIFIED | `conversations.py` + `api/reader_chat.py`; integration `test_conversations_api.py` (7) |
| 6 | Messages are replayed in stable order and duplicate client sends do not create duplicate user messages or jobs | 10-02 | ✓ VERIFIED | row-locked sequence + client_message_id idempotency; concurrency/API tests |
| 7 | Cross-owner child IDs return 404 without leaking metadata | 10-02 | ✓ VERIFIED | `test_owner_isolation.py` (2) + adversarial suite |
| 8 | The server proves selected text against owned Chapter.content and active hierarchy evidence before persistence | 10-03 | ✓ VERIFIED | `context.py` exact slice/hash; unit `test_context.py` (20) + PG `test_context_manifest.py` (4) |
| 9 | Every context manifest is frozen, checksum-addressed and contains only evidence visible at the message's reading snapshot | 10-03 | ✓ VERIFIED | immutable checksum assembly; retry reuses frozen manifest (generation + context tests) |
| 10 | The existing persisted full-book switch is the only authority that expands context | 10-03 | ✓ VERIFIED | `resolve_chapter_cutoff` + `timeline_full_book` snapshot; spoiler/context tests |
| 11 | Phase 09 relationship observations are consumed through a read-only version/evidence/spoiler contract | 10-03 | ✓ VERIFIED | `Phase09RelationshipObservationReader` (not null); production `ProductionContextBuilder` wires it; import of Phase 09 query service only |
| 12 | Every published answer is reconstructed from PostgreSQL and contains only manifest-valid cited blocks | 10-04 | ✓ VERIFIED | gateway citation gate + worker publish path; `test_gateway.py` (8) + generation jobs (9) |
| 13 | No-evidence input yields uncertainty or clarification, never an invented factual block | 10-04 | ✓ VERIFIED | gateway/adversarial no-evidence cases |
| 14 | Conversation and novel budgets are both reserved before a provider call and settled audibly | 10-04 | ✓ VERIFIED | `budget.py` dual `with_for_update`; `test_budget.py` (5) |
| 15 | Cancel, retry, restart and late provider outcomes cannot create duplicate or post-cancel assistant messages | 10-04 | ✓ VERIFIED | generation integration + adversarial cancel/late-result paths |
| 16 | The chat worker has no domain mutation or tool capability | 10-04 | ✓ VERIFIED | adversarial import/boundary scan empty for langchain/langgraph/apply_suggestion; worker writes chat tables only |
| 17 | Selecting visible text exposes a lightweight action bound to exact chapter source offsets | 10-05 | ✓ VERIFIED | `reader-selection.ts` + content selection action; Vitest 11 selection tests |
| 18 | The reader can continue reading while a collapsible conversation window is open on desktop and mobile | 10-05 | ✓ VERIFIED | page layout desktop column / mobile sheet; panel chip collapse; Vitest 8 panel tests |
| 19 | Users can manage multiple conversations, send/cancel/retry, replay citations and jump to exact source evidence | 10-05 | ✓ VERIFIED | `reader-chat-panel.tsx` + `readerChatApi`; citation highlight path in page/content |
| 20 | A real desktop and 390px stack proves owner, spoiler, persistence and no-domain-write behavior | 10-05 | ⚠ PARTIAL | Spec + qualification script present; **this session** Postgres **5432 offline**; real Playwright not executed. Mocked e2e artifact (10-05: 4 passed). Release-gate unit tests pass but cannot substitute live real-browser authority. |

**Score:** 19 VERIFIED + 1 PARTIAL / 20 must-have truths (0 MISSING)

## Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `backend/app/models/reader_chat.py` | Conversation/message/selection/manifest/job/budget ORM | ✓ VERIFIED | Present |
| `backend/app/schemas/reader_chat.py` | Strict API + ReaderAnswerEnvelope | ✓ VERIFIED | Present |
| `backend/migrations/versions/12_reader_chat_conversations.py` | Alembic head after Phase 09 | ✓ VERIFIED | Present; SUMMARY notes head `12readerchat01` |
| `backend/app/api/reader_chat.py` | Owner-scoped HTTP contracts | ✓ VERIFIED | OpenAPI: 6 conversation paths |
| `backend/app/services/reader_chat/conversations.py` | Lifecycle + ProductionContextBuilder | ✓ VERIFIED | Default production builder |
| `backend/app/services/reader_chat/context.py` | Selection + manifest assembly | ✓ VERIFIED | Present |
| `backend/app/services/reader_chat/retrieval.py` | Bounded retrieval + Phase09 reader | ✓ VERIFIED | No null adapter |
| `backend/app/services/reader_chat/budget.py` | Dual-scope ledgers | ✓ VERIFIED | Present |
| `backend/app/services/reader_chat/gateway.py` | Frozen call + citation gate | ✓ VERIFIED | Present |
| `backend/app/services/reader_chat/worker.py` | Durable lease/cancel/publish | ✓ VERIFIED | Present |
| `backend/prompts/reader_chat_answer.v1.txt` | Versioned evidence-only prompt | ✓ VERIFIED | Present |
| `frontend/src/lib/reader-selection.ts` | DOM→code-point conversion | ✓ VERIFIED | Present |
| `frontend/src/components/reader/reader-chat-panel.tsx` | Multi-session collapsible UI | ✓ VERIFIED | Present |
| `frontend/src/app/novels/[id]/page.tsx` | Desktop column / mobile sheet | ✓ VERIFIED | Imports ReaderChatPanel |
| `frontend/e2e/reader-chat.spec.ts` | Mocked browser journey | ✓ VERIFIED (artifact) | Present; prior 4 passed |
| `frontend/e2e/reader-chat-real.spec.ts` | Real stack journey | ⚠ PARTIAL | Artifact present; not executed (5432 down) |
| `backend/scripts/run_reader_chat_qualification.py` | Qualification CLI | ✓ VERIFIED | Present |
| `tests/ci/test_reader_chat_release_gate.py` | Independent release authority | ✓ VERIFIED | 10 passed this session |

## Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| user message | selection + manifest | FK/unique user_message_id | ✓ WIRED | models + migration tests |
| citations | context evidence allowlist | FK `context_evidence_ref_id` | ✓ WIRED | schema/DB + gateway gate |
| generation job | model attempts | durable lineage | ✓ WIRED | worker + generation tests |
| API routes | `require_owned_novel` | Depends | ✓ WIRED | router pattern |
| message append | `next_sequence` | `with_for_update` | ✓ WIRED | conversations service |
| context assembly | `Chapter.content` | exact code-point slice | ✓ WIRED | context.py |
| context cutoff | timeline spoiler | `resolve_chapter_cutoff` | ✓ WIRED | shared with Phase 08 |
| relationship evidence | Phase 09 query service | `Phase09RelationshipObservationReader` | ✓ WIRED | production builder injects real reader |
| worker | frozen manifest | job rehydrates checksum | ✓ WIRED | worker + retry tests |
| dual budgets | ledger rows | ordered locks before network | ✓ WIRED | budget.py + tests |
| reader content | selection util | `sourceStartUtf16` | ✓ WIRED | page bases |
| chat panel | API client | `readerChatApi` | ✓ WIRED | api.ts |
| citation click | chapter + highlight | `source_start` | ✓ WIRED | page/content |
| release gate | commands + DB invariants | independent verifier | ✓ WIRED | 10 CI tests passed |

## Data-Flow Trace

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| Selection validate | exact text/offsets/hash | owned `Chapter.content` | Yes | ✓ FLOWING |
| Context manifest | visible evidence set | hierarchy + knowledge/timeline + Phase09 reader after cutoff | Yes | ✓ FLOWING |
| Message API | ordered messages + jobs | PostgreSQL transaction | Yes | ✓ FLOWING |
| Worker publish | assistant blocks + citations | validated envelope from frozen manifest | Yes | ✓ FLOWING |
| Reader UI | conversations/messages/jobs | polling `readerChatApi` | Yes (unit + mocked e2e) | ✓ FLOWING |
| Real browser authority | owner/spoiler/persist proof | Next+FastAPI+PG live | Not observed this session | ⚠ BLOCKED (5432) |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Full backend reader_chat suite | `backend/.venv/Scripts/python.exe -m pytest tests/unit/reader_chat tests/integration/reader_chat tests/adversarial/test_reader_chat_boundaries.py -q` | **93 passed** (~109s) | ✓ PASS |
| Release gate | `backend/.venv/Scripts/python.exe -m pytest tests/ci/test_reader_chat_release_gate.py -q` (from repo root) | **10 passed** | ✓ PASS |
| Frontend reader Vitest | `npm test -- --run reader` | **19 passed** | ✓ PASS |
| OpenAPI boundary | Python import `app.main` OpenAPI scan | 6 conversation paths; no apply/accept-suggestion | ✓ PASS |
| Phase 09 production reader | Instantiate `Phase09RelationshipObservationReader` | Real class; load_filtered + list_refs present | ✓ PASS |
| Postgres host 5432 | `Test-NetConnection 127.0.0.1:5432` | **False** | ⚠ OFFLINE |
| Real Playwright | `npm run test:e2e -- reader-chat-real.spec.ts` | **Not run** (env residual) | ⚠ PARTIAL |
| Clue product UI scan | grep reader components for clue/foreshadow | No matches | ✓ PASS |

Pytest emitted only known unavailable `pytest-timeout` config/marker warnings; no Phase 10 reader_chat tests failed or skipped in the independent backend suite.

## Probe Execution

No Phase 10 PLAN declares a separate probe script path beyond qualification CLI. Step 7c N/A.

## Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|---|---|---|---|---|
| REQ-CHAT-01 | 01, 03 | Immutable selection/context evidence | ✓ SATISFIED | contracts + context unit/PG |
| REQ-CHAT-02 | 01, 02, 05 | Durable multi-session lifecycle | ✓ SATISFIED | API + UI; real browser residual only for e2e proof |
| REQ-CHAT-03 | 03, 05 | Server spoiler policy | ✓ SATISFIED | cutoff + adversarial; real e2e residual |
| REQ-CHAT-04 | 01, 04 | Answer/suggestion only; no domain writes | ✓ SATISFIED | gateway + adversarial + OpenAPI |
| REQ-CHAT-05 | 01, 04 | Auditable jobs/budgets/cancel/retry | ✓ SATISFIED | budget + generation PG |
| REQ-CHAT-06 | 05 | Non-obscuring desktop/mobile UI | ✓ SATISFIED | Vitest + layout code; mocked e2e prior |
| REQ-CHAT-07 | 01–05 | Full automated validation + real browser authority | ⚠ PARTIAL | unit/integration/adversarial/release-gate green; **real desktop/mobile stack not re-proven** while 5432 offline |

All REQ-CHAT-01..07 appear in PLAN frontmatter and 10-SPEC; none orphaned. REQ-CHAT-07 incomplete only on live browser authority path.

## Anti-Patterns Found

| File | Pattern | Severity | Impact |
|---|---|---|---|
| Host environment | Postgres not listening on 5432 | ⚠ WARNING | Blocks real Playwright release path only; pytest PG fixtures still passed integration suite |
| Pre-existing Alembic drift | `alembic check` index drift on chunk hierarchy (10-01 SUMMARY) | ℹ INFO | Outside Phase 10 ownership; head `12readerchat01` OK |
| `reader-chat-panel.test.tsx` | React `act(...)` warnings on async hydrate | ℹ INFO | Tests still pass; no functional failure |

Disconfirmation pass:

- No null Phase 09 adapter found; production uses `Phase09RelationshipObservationReader`.
- No clue product UI under reader components.
- No suggestion apply/accept routes in OpenAPI.
- Partial must-have among 20: only #20 (real desktop+390 stack).
- Misleading green risk: release-gate unit tests pass without live browser digests; treat real e2e as residual until Postgres is up.

## Gaps Remaining

1. **Real-stack Playwright** (`frontend/e2e/reader-chat-real.spec.ts` desktop + chromium-mobile-390) when PostgreSQL is available:
   ```powershell
   # ensure backend .env DB is up + alembic upgrade head
   cd frontend
   npm run test:e2e -- reader-chat-real.spec.ts
   ```
2. Optional: re-run mocked `reader-chat.spec.ts` and production `npm run build` if a release cut requires fresh digests this machine.

## Verdict

**PARTIAL** — 19/20 must-haves verified; Phase 10 implementation and automated (non-real-browser) gates are solid. Close residual by running real reader-chat Playwright against a live Postgres, then re-verify truth #20 / REQ-CHAT-07 to promote status to **passed**.
