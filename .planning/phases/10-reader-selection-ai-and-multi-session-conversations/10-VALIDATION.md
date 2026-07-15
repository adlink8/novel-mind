# Phase 10 Validation Architecture

## Purpose

Validation is planned before implementation. Every REQ-CHAT acceptance condition has a fast unit/contract path and a release-level PostgreSQL or browser path. Provider transport may be controlled; FastAPI, Next.js, PostgreSQL, owner checks, context assembly and persistence may not be replaced by browser route mocks in the release proof.

## Wave 0 Test Scaffolds

- `backend/tests/unit/reader_chat/test_contracts.py`
- `backend/tests/unit/reader_chat/test_context.py`
- `backend/tests/unit/reader_chat/test_budget.py`
- `backend/tests/unit/reader_chat/test_gateway.py`
- `backend/tests/integration/reader_chat/test_migration.py`
- `backend/tests/integration/reader_chat/test_conversations_api.py`
- `backend/tests/integration/reader_chat/test_owner_isolation.py`
- `backend/tests/integration/reader_chat/test_context_manifest.py`
- `backend/tests/integration/reader_chat/test_generation_jobs.py`
- `backend/tests/adversarial/test_reader_chat_boundaries.py`
- `frontend/src/components/reader/reader-chat-panel.test.tsx`
- `frontend/src/lib/reader-selection.test.ts`
- `frontend/e2e/reader-chat.spec.ts`
- `frontend/e2e/reader-chat-real.spec.ts`
- `tests/ci/test_reader_chat_release_gate.py`

## Requirement-to-Test Matrix

| Requirement | Observable proof | Automated gate |
|---|---|---|
| REQ-CHAT-01 | Unicode selection maps to exact persisted code-point slice; manifest is immutable and evidence-bound | unit contracts/context + PostgreSQL context manifest integration |
| REQ-CHAT-02 | create/rename/list/switch/archive/restore/delete; stable concurrent ordering and replay | conversation API + concurrency integration + browser |
| REQ-CHAT-03 | default cutoff excludes every future side channel; only persisted `timeline_full_book` expands | context integration + adversarial + real browser |
| REQ-CHAT-04 | strict cited blocks/suggestions only; no chat-to-domain mutation | gateway unit + adversarial import/DB assertions |
| REQ-CHAT-05 | durable lease/restart, dual budgets, usage/cost, cancel/retry and late-result discard | budget unit + generation PostgreSQL integration |
| REQ-CHAT-06 | lightweight selection entry, collapsible panel, mobile usability, citation navigation and states | Vitest + desktop/mobile Playwright |
| REQ-CHAT-07 | all suites and signed/hashed release observations pass | CI release gate |

## Required Commands

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit/reader_chat -q
.\.venv\Scripts\python.exe -m pytest tests/integration/reader_chat -q
.\.venv\Scripts\python.exe -m pytest tests/adversarial/test_reader_chat_boundaries.py -q
.\.venv\Scripts\python.exe -m ruff check app/models/reader_chat.py app/schemas/reader_chat.py app/services/reader_chat app/api/reader_chat.py tests/unit/reader_chat tests/integration/reader_chat tests/adversarial/test_reader_chat_boundaries.py

cd ..\frontend
npm test -- --run src/lib/reader-selection.test.ts src/components/reader/reader-chat-panel.test.tsx
npm run lint
npm run build
npm run test:e2e -- reader-chat.spec.ts reader-chat-real.spec.ts

cd ..
.\backend\.venv\Scripts\python.exe -m pytest tests/ci/test_reader_chat_release_gate.py -q
```

## Adversarial Matrix

| Attack / failure | Expected result |
|---|---|
| surrogate pairs, emoji, CJK, combining marks, CRLF, repeated text across pages | exact server slice or stable 409; never silently relocates selection |
| client forges evidence IDs or future chapter offsets | 422/409 before persistence/model call |
| cross-owner novel/conversation/message/job IDs | 404 with no title/count/timing distinction |
| `full_book=true` without persisted preference | ignored/rejected; first/progress chapter cutoff remains authoritative |
| future event/relationship hidden but citation/count/error remains | no hidden metadata in manifest, prompt or response |
| novel text says “ignore rules” or emits fake JSON/citations | treated as data; strict local schema and manifest-membership gate |
| model returns valid JSON with unknown refs | one separately budgeted repair, then `failed_validation`; no assistant message |
| no evidence supports question | empty factual blocks plus uncertainty/clarification |
| simultaneous sends with same client ID | one user message/job; replay returns original result |
| simultaneous sends with different IDs | unique monotonic sequence, no cross-message manifest swap |
| conversation or novel budget exhausted | zero provider calls; deterministic paused state |
| cancel during provider call | usage settled, response discarded, no assistant message |
| process dies after call/before publish | auditable outcome state; no blind duplicate billing; explicit recovery path |
| suggestion candidate targets domain row | stored/displayed as proposal only; no mutation route or domain write |

## Privacy / Owner Boundary Assertions

- List endpoints return metadata, not message bodies or evidence excerpts.
- Logs and release evidence contain hashes/status/counts, not prompts, selected text, excerpts, raw output, credentials or provider secrets.
- Hard delete removes conversation content, manifests, citations, attempts and ledgers after canceling jobs.
- Every child lookup includes owner+novel+conversation scope and inaccessible resources are indistinguishable 404s.
- Browser storage may remember panel presentation only; PostgreSQL remains the conversation/context authority.

## Phase 09 / Phase 11 Assertions

- Phase 10 changed-file list contains no `.planning/phases/09-*` or Phase 09 implementation file.
- Relationship observations enter context only through `RelationshipObservationReader` and carry version/evidence/cutoff lineage.
- A static architecture test rejects imports from reader-chat models/services in clue fact production and rejects domain mutation imports in the chat worker.
- No accept/apply endpoint exists for suggestion candidates.

## Release Evidence

`tests/ci/test_reader_chat_release_gate.py` must verify command exit codes/digests, PostgreSQL-observed counts and invariants, real browser artifact metadata, requirement coverage and the no-domain-write boundary. Self-reported JSON without independent DB/command observations cannot pass.

## Multi-Source Coverage Audit

| SOURCE | ID | Feature / constraint | Plan | Status | Notes |
|---|---|---|---|---|---|
| GOAL | — | Reader selection to evidence-bound multi-session AI | 01-05 | COVERED | full vertical slice |
| REQ | REQ-CHAT-01 | immutable selection/context evidence | 01,03 | COVERED | schema + context builder |
| REQ | REQ-CHAT-02 | durable multi-session lifecycle | 01,02,05 | COVERED | DB/API/UI |
| REQ | REQ-CHAT-03 | server spoiler policy | 03,05 | COVERED | existing full-book preference only |
| REQ | REQ-CHAT-04 | answer/suggestion only; no writes | 01,04 | COVERED | strict output + architecture gate |
| REQ | REQ-CHAT-05 | auditable jobs/budgets/cancel/retry | 01,04 | COVERED | PostgreSQL dual ledgers |
| REQ | REQ-CHAT-06 | non-obscuring desktop/mobile reader UI | 05 | COVERED | component + browser |
| REQ | REQ-CHAT-07 | full automated validation | 01-05 | COVERED | per-plan tests + release gate |
| RESEARCH | R-01 | Unicode code-point offset conversion and exact server re-slice | 03,05 | COVERED | unit + browser |
| RESEARCH | R-02 | visible-set-first context packing | 03 | COVERED | all derived data after cutoff |
| RESEARCH | R-03 | strict cited answer blocks and no-evidence behavior | 04 | COVERED | local gate |
| RESEARCH | R-04 | dual-scope atomic budget and late cancel discard | 04 | COVERED | PostgreSQL integration |
| RESEARCH | R-05 | no new dependencies / no remote conversation authority | 04 | COVERED | existing stack |
| CONTEXT | D-01 | lightweight selection entry/collapsible window/mobile | 05 | COVERED | UI + browser |
| CONTEXT | D-02 | multiple persistent owner-scoped conversations | 01,02,05 | COVERED | lifecycle end-to-end |
| CONTEXT | D-03 | immutable message snapshot and manifest | 01,03 | COVERED | retry reuses manifest |
| CONTEXT | D-04 | server spoiler + existing full-book switch | 03,05 | COVERED | no client authority |
| CONTEXT | D-05 | evidence-only cited answer / uncertainty | 04 | COVERED | strict schema |
| CONTEXT | D-06 | no domain writes; candidate interface only | 01,04 | COVERED | no apply route |
| CONTEXT | D-07 | existing AI/job patterns; no frameworks/tools/remote state | 04 | COVERED | no installs |
| CONTEXT | D-08 | PostgreSQL source and auditable lineage | 01,04 | COVERED | migration + attempts |
| CONTEXT | D-09 | migration/API/all test layers/budget/privacy/owner | 01-05 | COVERED | distributed gates |
| CONTEXT | D-10 | Phase 09 read-only public dependency; no file changes | 03 | COVERED | protocol adapter |
| CONTEXT | D-11 | Phase 11 never treats chat as facts | 04,05 | COVERED | architecture/release assertions |
| CONTEXT | D-12 | fiction only; no clue or relation UI | 01-05 | COVERED | explicit scope checks |

**Audit result:** all non-deferred GOAL/REQ/RESEARCH/CONTEXT items are covered. No phase split is required.

## Pre-Mortem

1. **Offset mismatch ships late:** catch in Wave 2 with Unicode unit + PostgreSQL exact-slice tests before UI integration.
2. **Spoiler filter is applied after retrieval:** context-manifest tests assert hidden IDs never appear in package snapshots, counts or errors.
3. **Chat accidentally becomes a fact pipeline:** strict no-mutation import/API/DB assertions run in adversarial and release gates.

