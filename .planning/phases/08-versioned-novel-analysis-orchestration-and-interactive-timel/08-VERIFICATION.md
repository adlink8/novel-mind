---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
verified: 2026-07-13T04:36:28Z
status: gaps_found
score: 16/32 must-haves verified
overrides_applied: 0
gaps:
  - truth: "REQ-TIME-01/02/03/04/05/07/09 and D-08/09/12/13/14/15/22: selecting a novel starts a real durable, versioned, evidence-bound timeline pipeline"
    status: failed
    reason: "The production API only inserts an AnalysisRun in pending state. No production worker/dispatcher connects that row to Phase 07 evidence, version creation, extraction, reconciliation, event persistence, qualification, or promotion."
    artifacts:
      - path: "backend/app/api/timeline.py"
        issue: "start_or_resume creates/returns AnalysisRun only; it does not enqueue or invoke durable execution."
      - path: "backend/app/services/timeline/extraction.py"
        issue: "TimelineChapterExtractor requires InMemoryExtractionStore; no PostgreSQL extraction/cache/publication repository exists."
      - path: "backend/app/services/timeline/jobs.py"
        issue: "PostgresTimelineJobStore exists but has no production construction or worker consumer."
      - path: "backend/app/services/timeline/budget.py"
        issue: "Budget reservations are process-memory dictionaries and are not wired to AnalysisBudgetLedger/AnalysisBudgetReservation."
    missing:
      - "A production worker/dispatcher that claims AnalysisRun leases and advances durable chapter/reconcile stages."
      - "Phase 07 hierarchy/evidence package loading and PostgreSQL writes for versions, attempts, cache entries, events, participants, evidence, edges, checkpoints, and promotion."
      - "A real start/resume-to-completed integration test through the API and PostgreSQL."
  - truth: "D-14/D-15/D-22 and AI-SPEC: every timeline model call uses one strict gateway with fail-closed, persisted pre-call budget and audit semantics"
    status: failed
    reason: "The chapter gateway is not connected to a real transport or durable ledger, reconciliation bypasses TimelineModelGateway, and the supposed strict Pydantic base permits coercion."
    artifacts:
      - path: "backend/app/services/timeline/model_gateway.py"
        issue: "Transport and BudgetGate are injected test objects; GatewayAttempt records are never persisted by production code."
      - path: "backend/app/services/timeline/reconcile.py"
        issue: "Calls an arbitrary transport directly, checks for the substring 'quality' in model_id, and does not use the strict gateway, local JSON validation, repair/audit flow, or settle the reservation."
      - path: "backend/app/schemas/timeline.py"
        issue: "StrictTimelineModel sets extra='forbid' but not strict=True; entity_id='7' is accepted and coerced to integer 7."
    missing:
      - "One production model resolver/transport with no fallback and frozen provider/model revision."
      - "Database-atomic reserve/settle/release and ModelCallAttempt persistence before/after every extraction, repair, and reconciliation call."
      - "Strict Pydantic validation for all model-output fields and gateway use by reconciliation."
  - truth: "D-05/D-21 and REQ-TIME-07: the real frontend preserves chapter-aware ordering and active/candidate source isolation"
    status: failed
    reason: "The chart re-sorts narrative events using narrative_index alone, so per-chapter indices collide and can undo the backend's chapter-aware order. The person selector unions participants from active and running candidate envelopes."
    artifacts:
      - path: "frontend/src/components/timeline/timeline-chart.tsx"
        issue: "position() ignores narrative_chapter_number; real chapter-local index 0 events overlap and sort incorrectly."
      - path: "frontend/src/app/analysis/page.tsx"
        issue: "people is derived by merging active and running_candidate events, exposing candidate-only participants while the active source is selected."
      - path: "frontend/e2e/timeline.spec.ts"
        issue: "The browser test mocks every timeline endpoint and uses globally increasing narrative_index values, masking both defects."
    missing:
      - "Chapter-aware narrative projection (or a backend-provided global axis coordinate) used consistently by chart and companion list."
      - "Controls derived only from the selected version source."
      - "Browser/API coverage with chapter-local narrative indices and distinct active/candidate participants."
  - truth: "08-QUALIFICATION and the release gate measure implementation behavior rather than restating fixture claims"
    status: failed
    reason: "Offline qualification reads booleans from operational_expectations and emits hardcoded perfect metrics. Controlled 'live' qualification returns fabricated result dictionaries; the release gate validates this self-generated report and file presence, not the production pipeline."
    artifacts:
      - path: "backend/scripts/run_timeline_qualification.py"
        issue: "Metrics are constants (1.0/0), operational gates trust corpus declarations, and controlled_live_qualification never invokes extraction or reconciliation."
      - path: "backend/evals/timeline_fiction.v1.json"
        issue: "Operational success facts such as stale_cas_rejected and calls_after_budget_pause are inputs, not measured outputs."
      - path: "tests/ci/test_timeline_release_gate.py"
        issue: "The positive test calls run_offline_qualification and verifies its own report; it cannot detect the absent production worker."
    missing:
      - "Qualification that executes the actual schema/evidence/order/dedupe/causal algorithms over the frozen corpus."
      - "Controlled provider tests through the real gateway and durable budget/audit path."
      - "A release gate bound to observed API/worker/PostgreSQL artifacts and measured metrics."
---

# Phase 8: Versioned Novel Analysis Orchestration and Interactive Timeline Verification Report

**Phase Goal:** 以持久、版本化、证据约束的后台分析任务生成小说时间事件，并在全局分析工作台渐进展示防剧透的双顺序横向时间线。
**Verified:** 2026-07-13T04:36:28Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

The phase goal is not achieved. PostgreSQL read models, spoiler filtering, promotion/rollback primitives, and a polished frontend projection exist, but there is no production data-producing path from first entry to a completed timeline. The dominant failure is missing wiring, not missing files.

### Requirements — Observable Truths

| Requirement | Status | Codebase evidence |
|---|---|---|
| REQ-TIME-01 durable staged jobs | ✗ FAILED | `start_or_resume()` only writes a pending `AnalysisRun`; no worker consumes it. Unit restart tests use `InMemoryTimelineJobStore`. |
| REQ-TIME-02 immutable versions/active/override/rollback | ✗ FAILED | Real PostgreSQL promotion/rollback works when tests manually seed versions, but production code never creates or validates a candidate version. |
| REQ-TIME-03 first-entry deep-analysis trigger | ✗ FAILED | First entry is idempotent DB row creation, not a trigger for deep analysis; no dispatcher/queue/worker reference exists. |
| REQ-TIME-04 dual order and four precision classes | ✗ FAILED | Schemas/columns exist, but no production extractor persists them; frontend narrative positioning also ignores chapter number. |
| REQ-TIME-05 evidence-bound auto-publication/manual protection | ✗ FAILED | Evidence validation and provisional publication terminate in `InMemoryExtractionStore`; no machine event/evidence writes exist outside tests. |
| REQ-TIME-06 person/order/causal API | ✓ VERIFIED | `build_version_view()` filters structured participants, sorts by selected order, and returns causal edges only when requested and both endpoints are visible. |
| REQ-TIME-07 progressive global workspace | ✗ FAILED | `/analysis` renders mocked data, but real first entry remains permanently pending; chart order is wrong for chapter-local indices. |
| REQ-TIME-08 API spoiler protection | ✓ VERIFIED | Real DB integration tests prove first-chapter default, persisted full-book requirement, visible-set-first overrides/edges/counts/aggregates/previews. |
| REQ-TIME-09 tiered model routing and durable budget | ✗ FAILED | No production model resolver/worker; budget/cache are memory-only, and reconciliation bypasses the strict gateway. |
| REQ-TIME-10 fiction-only/deferred scope | ✓ VERIFIED | Phase 08 UI/contracts contain no relationship graph, reader AI, clue lifecycle, history corpus, or six intermediate analysis modes. `/search` remains reachable. |

**Requirement score:** 3/10 verified

### Decision Verification

| Decision | Status | Evidence |
|---|---|---|
| D-01 | ✓ VERIFIED | Global `/analysis` route and navigation exist; `/search` remains linked. |
| D-02 | ✓ VERIFIED | Phase 08 page exposes timeline only. |
| D-03 | ✓ VERIFIED | ECharts horizontal canvas has inside/slider zoom and desktop/mobile mocked browser coverage. |
| D-04 | ✓ VERIFIED | One endpoint applies a person filter to the selected timeline. |
| D-05 | ✗ FAILED | Storage/query support dual order, but frontend re-sorts narrative order by local `narrative_index` only. |
| D-06 | ✓ VERIFIED | Four precision shapes and anti-smuggling validators exist; exact requires expression and exact value. |
| D-07 | ✓ VERIFIED | Typed causal edges and opt-in API/UI toggle exist. |
| D-08 | ✗ FAILED | Automatic publication exists only in an in-memory test adapter and is not connected to production. |
| D-09 | ✗ FAILED | ORM lineage/evidence fields exist, but no production path writes published machine events. |
| D-10 | ✓ VERIFIED | PostgreSQL override append/overlay and promotion-time evidence-identity relink are substantive and tested against PostgreSQL. |
| D-11 | ✓ VERIFIED | Manifest recomputation, row-locked expected-revision CAS, journal, rollback, and old-version retention work in PostgreSQL tests. |
| D-12 | ✗ FAILED | First entry creates a row but never starts deep analysis. |
| D-13 | ✗ FAILED | API/UI can represent partial progress, but no chapter producer advances progress or publishes partial rows. |
| D-14 | ✗ FAILED | No production tier resolver; reconciliation uses a model-name substring and a separate arbitrary transport. |
| D-15 | ✗ FAILED | Budget/cache/checkpoint primitives are not connected into a durable execution pipeline. |
| D-16 | ✓ VERIFIED | Spoiler cutoff is enforced in the backend query before overlays and derivations. |
| D-17 | ✓ VERIFIED | Full-book preference is persisted per novel and must accompany the explicit query. |
| D-18 | ✓ VERIFIED | New Phase 08 corpus/contracts are fiction-only. |
| D-19 | ✓ VERIFIED | Relationship graph, reader AI, and clue tracking are absent and remain outside Phase 08. |
| D-20 | ✓ VERIFIED | Missing/invalid progress resolves to first chapter; no chapters resolves to no events. |
| D-21 | ✗ FAILED | API envelopes are separate, but frontend participant options merge active and candidate event sets. |
| D-22 | ✗ FAILED | The in-memory gate rejects unknown pricing, but no production pre-call durable budget path exists. |

**Decision score:** 13/22 verified  
**Combined score:** 16/32 must-haves verified

## Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `backend/app/models/analysis.py` | durable run/version/attempt/budget authority | ⚠ PARTIAL | Substantive ORM tables exist; attempt/budget/version rows have no production producer. |
| `backend/app/models/timeline.py` | immutable event graph/override/pointer authority | ✓ VERIFIED | Substantive and used by query/promotion; DB scope is primarily application-enforced rather than composite-FK enforced. |
| `backend/migrations/versions/10_analysis_timeline_versions.py` | migration for Phase 08 authority | ✓ VERIFIED | Database is at `10analysistime01`; tables exist. |
| `backend/app/services/timeline/jobs.py` | durable worker state machine | ⚠ ORPHANED | PostgreSQL repository exists, but no production construction/dispatcher uses it. |
| `backend/app/services/timeline/budget.py` | atomic persisted pre-call budget | ✗ STUB FOR PRODUCTION | Logic is in-memory only despite ORM budget tables. |
| `backend/app/services/timeline/model_gateway.py` | single strict real model gateway | ⚠ ORPHANED | Only injected fake transports use it; no real LiteLLM/AIService adapter or durable attempt sink. |
| `backend/app/services/timeline/extraction.py` | evidence-bound durable extraction | ✗ HOLLOW | Cache, audits, and publication are `dict`/`list` state in `InMemoryExtractionStore`. |
| `backend/app/services/timeline/reconcile.py` | strict quality-tier reconciliation | ⚠ PARTIAL | Deterministic graph logic is substantive, but model call path bypasses the gateway and persistence. |
| `backend/app/services/timeline/promotion.py` | validated CAS promotion/rollback | ✓ VERIFIED | Real PostgreSQL lifecycle test passes. |
| `backend/app/services/timeline/query.py` | owner-scoped spoiler-safe projection | ✓ VERIFIED | Real DB integration tests cover visible-set-first behavior. |
| `backend/app/api/timeline.py` | real lifecycle and query API | ⚠ PARTIAL | Owner-scoped reads/mutations work; start has no execution handoff. |
| `frontend/src/app/analysis/page.tsx` | usable source-isolated workspace | ⚠ PARTIAL | API consumer is real, but selected-source controls merge active/candidate participants. |
| `frontend/src/components/timeline/timeline-chart.tsx` | chapter-aware horizontal dual-order timeline | ⚠ PARTIAL | Zoom/list work; narrative axis ignores chapter number. |
| `backend/scripts/run_timeline_qualification.py` | measured frozen qualification | ✗ STUB | Emits hardcoded perfect metrics and trusts success booleans supplied by the corpus. |

## Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `/api/timeline/{novel}/start-or-resume` | durable worker | enqueue/claim/dispatch | ✗ NOT WIRED | Endpoint commits a pending row and returns. |
| durable worker | Phase 07 hierarchy/evidence | frozen evidence package loader | ✗ NOT WIRED | No production consumer builds `EvidencePackage` from PostgreSQL. |
| worker | strict chapter gateway | budgeted extraction | ✗ NOT WIRED | `TimelineChapterExtractor` is referenced only by exports/tests. |
| gateway | budget/attempt tables | reserve then persist attempt | ✗ NOT WIRED | `BudgetGate` is memory-only; `ModelCallAttempt` has no production writes. |
| extraction | machine event graph | transactional publication/checkpoint | ✗ NOT WIRED | Publication writes only `InMemoryExtractionStore.published`. |
| reconciliation | strict gateway | quality-tier structured call | ✗ NOT WIRED | Direct arbitrary callable transport is used. |
| validated candidate | active pointer | CAS promotion | ✓ WIRED | `promote_version()` recomputes manifest and commits pointer+journal transactionally. |
| API | spoiler-safe DB query | `build_version_view()` | ✓ WIRED | Owner/version/cutoff filters precede overlays and derived output. |
| frontend | timeline API | typed `timelineApi` calls | ✓ WIRED | Page starts/status-polls/queries and renders envelopes. |

## Data-Flow Trace (Level 4)

| Artifact | Data variable | Source | Produces real data | Status |
|---|---|---|---|---|
| `/analysis` page | `run`, `envelope` | real API client | API reads real PostgreSQL rows, but no producer creates timeline rows | ⚠ HOLLOW |
| `TimelineChapterExtractor` | validated events/audits/cache | injected fake transport + in-memory store | No production persistence | ✗ DISCONNECTED |
| `TimelineReconciler` | story ranks/causal edges | injected callable | No production resolver, strict gateway, or write path | ✗ DISCONNECTED |
| `run_timeline_qualification.py` | metrics/gates | fixture declarations/constants | Does not execute implementation | ✗ STATIC |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Phase 08 backend suite | `backend/.venv/Scripts/python.exe -m pytest tests/unit/timeline tests/integration/timeline tests/adversarial/test_timeline_evidence.py -q` | 56 passed | ✓ PASS (local contracts only) |
| Release gate | `backend/.venv/Scripts/python.exe -m pytest tests/ci/test_timeline_release_gate.py -q` | 5 passed | ✓ PASS (self-report gate; not end-to-end) |
| Frontend unit suite | `npm test -- --run` | 66 passed | ✓ PASS |
| Frontend production build | `npm run build` | `/analysis` generated | ✓ PASS |
| Desktop/mobile timeline browser projection | `npm run test:e2e -- timeline.spec.ts` | 2 passed | ✓ PASS (all timeline APIs mocked) |
| Strict model contract | `Participant.model_validate({'mention':'x','entity_id':'7'})` | coerced to `entity_id: 7` | ✗ FAIL |
| Production persistence producers | `rg 'AnalysisVersion\(|MachineTimelineEvent\(|...TimelineChapterExtractor\('` excluding tests | model class definitions only | ✗ FAIL |
| Migration head | `alembic current` | `10analysistime01 (head)` | ✓ PASS |
| Migration cleanliness | `alembic check` | Phase 07 index drift reported | ⚠ WARNING (pre-existing, not a Phase 08 blocker) |

## Probe Execution

No Phase 08 `probe-*.sh` files or declared probes were found. Step 7c is not applicable.

## Requirements Coverage

All plan-declared requirement IDs are present in `.planning/REQUIREMENTS.md`; no additional Phase 08 requirement IDs are orphaned. Coverage status is the 3/10 requirement table above. The `VERIFIED` labels in `REQUIREMENTS.md` and `08-QUALIFICATION.md` were treated as claims, not evidence.

## Commit Verification

All 32 task commit hashes documented in the six SUMMARY files resolve to commits. Commit existence confirms history only; it does not close the missing production links above.

## Anti-Patterns and Disconfirmation Pass

| File | Line/pattern | Severity | Impact |
|---|---|---|---|
| `backend/scripts/run_timeline_qualification.py` | hardcoded metrics at lines 76-88 | 🛑 BLOCKER | A perfect score is emitted without running extraction/reconciliation. |
| `backend/scripts/run_timeline_qualification.py` | fixture-trusted operational gates at lines 48-66 | 🛑 BLOCKER | Assertions such as restart/CAS/budget/spoiler success are inputs, not observations. |
| `backend/scripts/run_timeline_qualification.py` | controlled result dictionaries at lines 196-208 | 🛑 BLOCKER | “Live dual-model” evidence does not call a model gateway. |
| `frontend/e2e/timeline.spec.ts` | mocked API and global narrative indices | ⚠ WARNING | Browser green status cannot detect backend disconnection or real chapter-order overlap. |
| `backend/tests/unit/timeline/test_api.py` | route/schema introspection only | ⚠ WARNING | No endpoint behavior, worker handoff, or ownership mutation is exercised. |

Disconfirmation checks found: (1) partially met durable/version contracts without a producer, (2) a passing release test that only verifies a self-generated report, and (3) an uncovered production error path where a pending run has no executor and never transitions.

## Deferred Scope

Relationship graph, reader selected-text AI, clue/foreshadow tracking, historical corpus support, and six intermediate analysis modes are correctly absent. They are not counted as gaps. The current ROADMAP does not define executable later Phase 09-11 entries, so no failed Phase 08 must-have was reclassified as roadmap-deferred.

## Human Verification Required

None for the verdict. Visual polish could receive later UAT, but human testing cannot resolve the observable absence of a production execution pipeline.

## Gaps Summary

Four related blockers prevent Phase 08 from meeting its goal: no real durable executor, no durable unified model/budget/audit path, incorrect real-data frontend ordering/source isolation, and a qualification gate that certifies fixture declarations rather than implementation behavior. The passing tests verify useful primitives and mocked projections, but they do not prove that selecting a novel ever generates a timeline.

---

_Verified: 2026-07-13T04:36:28Z_  
_Verifier: the agent (gsd-verifier)_
