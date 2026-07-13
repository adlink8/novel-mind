---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
verified: 2026-07-13T05:58:19Z
status: gaps_found
score: 29/32 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 16/32
  gaps_closed: []
  gaps_remaining:
    - "REQ-TIME-01: production cancellation is not observed after worker claim"
    - "D-15: reconciliation exact-cache identity omits prompt/schema hashes"
    - "D-05 / 08-08: production frontend contract does not carry source_start"
    - "08-QUALIFICATION: spoiler metric and release artifact provenance remain self-assertable"
  regressions: []
gaps:
  - truth: "REQ-TIME-01: a running production analysis can be cancelled and stops at a durable checkpoint"
    status: failed
    reason: "The cancel endpoint persists cancel_requested, but the production worker checks it only while claiming the run. Once claimed, chapter extraction, reconciliation, and promotion continue without another cancellation check."
    artifacts:
      - path: "backend/app/services/timeline/worker.py"
        issue: "cancel_requested is read only in _claim_run at line 149; the chapter loop and pre-promotion path never re-read it."
      - path: "backend/tests/integration/timeline/test_production_worker.py"
        issue: "The production integration suite has first-entry, resume, and invalid-reconcile tests, but no mid-run cancellation test."
    missing:
      - "Re-read cancel_requested under lock before each provider call/checkpoint and before reconciliation/promotion."
      - "A PostgreSQL production-worker test that cancels during a blocked chapter and proves no later provider call or promotion occurs."
  - truth: "D-15: exact cache identity includes source, prompt, schema, model, decoding, and config lineage for extraction and reconciliation"
    status: failed
    reason: "Extraction uses ExactCacheKey with prompt_hash and schema_hash, but the production reconciliation cache key omits both. A changed reconciliation prompt/schema can reuse a stale completed artifact."
    artifacts:
      - path: "backend/app/services/timeline/worker.py"
        issue: "The cross_chapter_reconcile cache-key object at lines 440-450 includes source/build/events/model/decoding/config but not prompt_hash or schema_hash."
      - path: "backend/tests/integration/timeline/test_persistent_calls.py"
        issue: "Persistence/restart is tested, but reconciliation cache invalidation on prompt/schema changes is not."
    missing:
      - "Include reconciliation prompt hash and ReconciliationOutputModel schema hash in the durable cache key."
      - "A restart test proving either hash change causes a cache miss and a new audited provider call."
  - truth: "D-05 and 08-08: narrative projection uses chapter_number, persisted source_start, then event ID through the real API/frontend contract"
    status: failed
    reason: "The comparator supports an optional source_start, but TimelineVisibleEvent and the frontend TimelineEvent API type do not expose that field. The passing frontend test injects an extra source_start property into mocked data that the real API cannot return, so production falls back to narrative_index."
    artifacts:
      - path: "backend/app/schemas/timeline.py"
        issue: "TimelineVisibleEvent has chapter number and narrative_index but no source_start."
      - path: "backend/app/services/timeline/query.py"
        issue: "The visible event projection does not derive or return a persisted evidence source offset."
      - path: "frontend/src/lib/api.ts"
        issue: "TimelineEvent has no source_start contract."
      - path: "frontend/src/components/timeline/timeline-chart.tsx"
        issue: "Comparator casts to an undeclared optional field and falls back to narrative_index."
      - path: "frontend/src/app/analysis/page.test.tsx"
        issue: "The source-offset ordering test passes only because mocked objects contain an out-of-contract field."
    missing:
      - "Expose a deterministic persisted source_start/global narrative coordinate in the backend response and typed frontend client."
      - "Exercise the ordering through a real API response, including conflicting narrative_index/source_start values."
  - truth: "08-QUALIFICATION and the release gate prove spoiler safety and production provenance from observed artifacts rather than self-asserted report content"
    status: failed
    reason: "Production qualification now executes the real worker, but spoiler_leaks compares visible chapters to max(visible_chapters), making the metric zero by construction. The release gate accepts a synthetic report whose raw artifact and booleans are created in the test and protected only by an unkeyed recomputable SHA-256; it does not bind the report to a database run or independently execute qualification."
    artifacts:
      - path: "backend/scripts/run_timeline_qualification.py"
        issue: "Lines 234-236 derive cutoff from the visible output itself; verify_release_evidence trusts report gate booleans and self-computed hashes."
      - path: "tests/ci/test_timeline_release_gate.py"
        issue: "_production_report constructs a fake completed PostgreSQL artifact and the positive release test accepts it."
      - path: ".planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-QUALIFICATION.md"
        issue: "Artifact digest matches, but the markdown contains no independently verifiable report provenance/signature."
    missing:
      - "Compute spoiler leaks against persisted reading-progress cutoff (or expected visible IDs), not the visible set's own maximum."
      - "Bind release qualification to freshly executed production qualification/DB evidence, or use an external CI attestation that cannot be regenerated from edited report content."
---

# Phase 8: Versioned Novel Analysis Orchestration and Interactive Timeline Verification Report

**Phase Goal:** 以持久、版本化、证据约束的后台分析任务生成小说时间事件，并在全局分析工作台渐进展示防剧透的双顺序横向时间线。
**Verified:** 2026-07-13T05:58:19Z
**Status:** gaps_found
**Re-verification:** Yes — after 08-07 and 08-08 gap closure

## Goal Achievement

Phase 08 is substantially closer to the goal: the API now dispatches a real worker; PostgreSQL is the authority for versions, events, evidence, checkpoints, budgets, attempts, and promotion; selected-version participant isolation works; and the real desktop/mobile browser test passed without timeline API interception. The phase still cannot pass because three REQ/D truths and the qualification authority contract remain observably false.

### Prior Blocker Re-check

| Prior blocker | Result | Current evidence |
|---|---|---|
| Production worker chain | ⚠ PARTIAL | `start_or_resume` schedules `dispatch_timeline_run`; the worker loads active Phase 07 hierarchy evidence, persists chapter events/checkpoints, reconciles, snapshots, and CAS-promotes. Real PostgreSQL test passed. Mid-run cancellation remains unwired. |
| DB-backed strict gateway/budget/cache/audit and reconcile | ⚠ PARTIAL | Both production stages call `TimelineModelGateway`; PostgreSQL concurrent reservation, strict repair, attempt audit, settlement, cache recovery, and zero-call fail-closed tests passed. Reconcile cache identity omits prompt/schema hashes. |
| Frontend chapter ordering/active-candidate isolation | ⚠ PARTIAL | Chapter-aware sorting and selected-source people derivation exist; frontend 68 tests and real browser E2E passed. The source-offset test relies on a mocked field absent from the real API contract. |
| Production qualification and unmocked API/browser E2E | ⚠ PARTIAL | Production qualification executes the worker and reads PostgreSQL artifacts; unmocked desktop/mobile E2E passed. Spoiler metric is tautological and the release gate accepts a synthetic self-hashed artifact. |

### Requirements — Observable Truths

| Requirement | Status | Evidence |
|---|---|---|
| REQ-TIME-01 durable/recoverable/cancellable/progressive jobs | ✗ FAILED | Durable lease/checkpoint/resume and chapter publication execute successfully, but `cancel_requested` is checked only at claim; a running production worker does not stop on cancel. |
| REQ-TIME-02 immutable versioning/validation/active/override/rollback | ✓ VERIFIED | Worker creates lineage-bound candidates; `test_version_lifecycle.py` proves PostgreSQL stale-CAS rejection and byte-identical rollback; overrides remain separate. |
| REQ-TIME-03 first analysis entry triggers deep analysis only | ✓ VERIFIED | `/start-or-resume` idempotently creates/reuses a run and schedules the worker; production integration proves repeat entry adds no calls. Import paths contain no timeline worker trigger. |
| REQ-TIME-04 dual order and four precision classes | ✓ VERIFIED | Strict schema and PostgreSQL columns preserve narrative chapter/index, story constraints/rank, and exact/relative/fuzzy/unknown shapes; relevant schema/persistence tests passed. |
| REQ-TIME-05 evidence-bound auto-publication/manual protection | ✓ VERIFIED | Worker validates Phase 07 evidence, writes provisional machine events/evidence per chapter, and promotion/override tests pass. |
| REQ-TIME-06 person/order/causal API | ✓ VERIFIED | Visible-set query applies person filtering, story/narrative order, and opt-in causal edges with both endpoints visible; frontend controls passed. |
| REQ-TIME-07 progressive global workspace | ✓ VERIFIED | `/analysis` selects a novel, displays status/partial/active states, horizontal zoom/list, errors and update state; real desktop/mobile E2E passed. |
| REQ-TIME-08 server-side spoiler protection | ✓ VERIFIED | PostgreSQL spoiler tests and real browser flow prove first-progress cutoff, persisted full-book confirmation, and hidden future events. Qualification measurement quality is separately blocked. |
| REQ-TIME-09 tiered routing and per-novel budget | ✓ VERIFIED | Frozen balanced/quality deployments, strict no-fallback gateway, PostgreSQL atomic reserve/settle, unknown-price pause, and concurrent one-call ceiling all executed successfully. |
| REQ-TIME-10 fiction-only/deferred scope | ✓ VERIFIED | Frozen corpus/UI remain timeline-only fiction; relationship graph, reader AI, clue tracking, history support, and six intermediate modes are absent. |

**Requirement score:** 9/10 verified

### Decision Verification

| Decision | Status | Evidence |
|---|---|---|
| D-01 | ✓ VERIFIED | Global `/analysis` exists and `/search` remains available. |
| D-02 | ✓ VERIFIED | Analysis workspace exposes timeline only. |
| D-03 | ✓ VERIFIED | ECharts horizontal canvas has inside/slider zoom; desktop and 390px real E2E passed. |
| D-04 | ✓ VERIFIED | One selected timeline supports person filtering. |
| D-05 | ✗ FAILED | Dual order is persisted/switchable, but 08-08's required chapter→source_start→ID production projection is not wired through the API. |
| D-06 | ✓ VERIFIED | Four strict precision shapes reject time smuggling and incomplete exact/relative forms. |
| D-07 | ✓ VERIFIED | Typed causal edges are hidden by default and exposed by an explicit toggle. |
| D-08 | ✓ VERIFIED | Evidence-valid chapter events are automatically persisted as provisional; no review queue is required. |
| D-09 | ✓ VERIFIED | Production events retain chapter, evidence offsets/hashes, confidence, prompt/schema/model lineage, and timestamps. |
| D-10 | ✓ VERIFIED | Append-only manual overlays and evidence-identity relink/needs_relink behavior are tested. |
| D-11 | ✓ VERIFIED | Immutable candidates, validated CAS promotion, retained versions, journal, and rollback are PostgreSQL-tested. |
| D-12 | ✓ VERIFIED | First analysis entry dispatches deep timeline work; import does not. |
| D-13 | ✓ VERIFIED | Chapter event/evidence/stage commit is atomic; real browser E2E observes candidate partial then active completion. |
| D-14 | ✓ VERIFIED | Production runtime freezes balanced chapter and quality reconciliation deployments; both use one strict gateway. |
| D-15 | ✗ FAILED | Durable budgets/checkpoints/audits work, but reconciliation exact-cache identity excludes prompt/schema lineage. |
| D-16 | ✓ VERIFIED | API computes visible event rows before overrides, filters, edges, counts, aggregates, and previews. |
| D-17 | ✓ VERIFIED | Full-book disclosure requires explicit confirmation and persisted per-novel preference. |
| D-18 | ✓ VERIFIED | Phase 08 contracts, fixtures, and UI remain fiction-only. |
| D-19 | ✓ VERIFIED | Relationship graph, reader selected-text AI, and clue tracking are absent/deferred. |
| D-20 | ✓ VERIFIED | No valid progress exposes the first chapter only; no chapters yields no visible events. |
| D-21 | ✓ VERIFIED | API envelopes remain separate; UI people derive solely from `envelope[source]`; completed runs no longer appear as candidates. |
| D-22 | ✓ VERIFIED | Unknown pricing persists `budget_rejected`/`paused_budget` before any provider call; later calls remain blocked. |

**Decision score:** 20/22 verified  
**Combined score:** 29/32 must-haves verified

## Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `backend/app/services/timeline/worker.py` | production Phase 07→timeline→promotion chain | ⚠ PARTIAL | 571 substantive lines and fully wired; cancellation is checked only at claim. |
| `backend/app/services/timeline/model_gateway.py` | strict persistent model-call boundary | ✓ VERIFIED | PostgreSQL reservation/attempt/settlement/cache-skip/outcome audit and strict one-repair flow execute. |
| `backend/app/services/timeline/extraction.py` | evidence/cache contracts | ✓ VERIFIED | Production worker consumes exact key/cache loader; in-memory adapter is limited to unit contracts. |
| `backend/app/services/timeline/reconcile.py` | strict quality-tier reconciliation | ✓ VERIFIED | Production path uses the gateway and deterministic materialization; direct transport remains test-only. |
| `backend/app/services/timeline/query.py` | source-isolated spoiler-safe read model | ✓ VERIFIED | Real PostgreSQL and browser tests exercise visible-set-first active/candidate output. |
| `backend/app/api/timeline.py` | owner-scoped lifecycle + dispatch | ✓ VERIFIED | Start/resume commits then schedules worker; reads/mutations are owner-scoped. |
| `frontend/src/app/analysis/page.tsx` | selected-source global workspace | ✓ VERIFIED | People and rendering derive from selected view; source switch clears stale person filter. |
| `frontend/src/components/timeline/timeline-chart.tsx` | chapter/source-aware dual-order chart | ⚠ PARTIAL | Chapter and ID ordering work; source_start branch is unreachable from the real typed API. |
| `backend/scripts/run_timeline_qualification.py` | production-derived qualification | ⚠ PARTIAL | Executes worker and reads DB artifacts, but spoiler metric is self-referential and release verification trusts report claims. |
| `frontend/e2e/timeline-real.spec.ts` | unmocked real browser journey | ✓ VERIFIED | No `page.route`/`route.fulfill`; real Next.js/FastAPI/PostgreSQL run passed at both viewports. |

## Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| start/resume API | worker | FastAPI `BackgroundTasks` → `dispatch_timeline_run` | ✓ WIRED | Actual API integration reaches completed active timeline. |
| worker | Phase 07 hierarchy | active pointer/build/evidence-node queries | ✓ WIRED | Real PostgreSQL worker test loads evidence nodes. |
| worker | strict gateway | extraction and reconciliation `generate()` calls | ✓ WIRED | Three persisted succeeded attempts observed in qualification. |
| gateway | budget/attempt tables | row lock → reserve attempt → provider → settle | ✓ WIRED | Real concurrency allowed exactly one call under a one-call budget. |
| worker | event/evidence/stage tables | per-chapter transaction | ✓ WIRED | Two events, two refs, three stages in signed artifact and fresh tests. |
| candidate | active pointer | snapshot validation + expected-revision CAS | ✓ WIRED | Promotion and rollback lifecycle tests passed. |
| cancel API | running worker | `cancel_requested` polling | ✗ NOT WIRED | Worker never re-checks after claim. |
| reconcile cache | prompt/schema lineage | exact-key fields | ✗ NOT WIRED | Both hashes absent from reconciliation key. |
| API event | chart comparator | typed `source_start` | ✗ NOT WIRED | Backend/client omit field; mock injects it. |
| qualification | production worker/DB | `run_production_qualification` | ✓ WIRED | Fresh PostgreSQL qualification tests passed. |
| release gate | independent production provenance | attested artifact | ✗ NOT WIRED | Synthetic self-hashed report passes positive gate test. |

## Data-Flow Trace (Level 4)

| Artifact | Data variable | Source | Produces real data | Status |
|---|---|---|---|---|
| `/analysis` | `run`, `envelope[source]` | real timeline API/PostgreSQL | Yes | ✓ FLOWING |
| production worker | evidence package | Phase 07 active build/evidence nodes | Yes | ✓ FLOWING |
| model gateway | reservation/attempt/output | PostgreSQL + controlled/production transport | Yes | ✓ FLOWING |
| chapter publication | machine events/evidence/stages | validated gateway output | Yes | ✓ FLOWING |
| reconciliation | story ranks/edges/stage artifact | strict quality gateway | Yes, cache identity incomplete | ⚠ PARTIAL |
| qualification | metrics/raw artifact | fresh worker + PostgreSQL rows + visible query | Yes, spoiler metric invalid | ⚠ PARTIAL |
| chart source offset | `source_start` | mocked test property only | No real API source | ✗ HOLLOW_PROP |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Full Phase 08 backend timeline suite | `backend/.venv/Scripts/python.exe -m pytest tests/unit/timeline tests/integration/timeline tests/adversarial/test_timeline_evidence.py -q` | 65 passed, including real PostgreSQL worker/call/qualification tests | ✓ PASS |
| Frontend unit/contract suite | `npm test -- --run` | 68 passed | ✓ PASS |
| Frontend production build | `npm run build` | `/analysis` generated; TypeScript/build passed | ✓ PASS |
| Real desktop/mobile browser journey | `npm run test:e2e -- timeline-real.spec.ts` | exit 0, 2 Playwright tests through real Next.js/FastAPI/PostgreSQL | ✓ PASS |
| Timeline release tests | `backend/.venv/Scripts/python.exe -m pytest tests/ci/test_timeline_release_gate.py -q` | 7 passed | ✓ PASS, but positive test is synthetic |
| Qualification artifact digest | parse canonical JSON and recompute SHA-256 | `ddd1f8...` matched documented digest; PostgreSQL, completed run, 2/2/3/3 rows | ✓ PASS (integrity only) |
| Migration head | `alembic current` | `10analysistime01 (head)` | ✓ PASS |
| Migration schema drift | `alembic check` | Phase 07 index add/remove drift remains | ⚠ WARNING (pre-existing, not Phase 08 blocker) |

## Probe Execution

No Phase 08 `probe-*.sh` files or probe declarations were found. Step 7c is not applicable.

## Requirements Coverage

All `REQ-TIME-01..10` IDs appear in plan frontmatter and `.planning/REQUIREMENTS.md`; no Phase 08 requirement is orphaned. The requirement table above is based on current code and executed tests, not the `VERIFIED` labels in REQUIREMENTS or SUMMARY files.

## Anti-Patterns and Disconfirmation Pass

| File | Line/pattern | Severity | Impact |
|---|---|---|---|
| `backend/app/services/timeline/worker.py` | cancellation checked only at line 149 | 🛑 BLOCKER | Running work can call providers and promote after user cancellation. |
| `backend/app/services/timeline/worker.py` | reconcile key lines 440-450 omit prompt/schema | 🛑 BLOCKER | Stale reconciliation artifact can survive a prompt/schema revision. |
| `frontend/src/components/timeline/timeline-chart.tsx` | undeclared optional `source_start` cast/fallback | 🛑 BLOCKER | Mocked ordering proof does not represent production API data. |
| `backend/scripts/run_timeline_qualification.py` | cutoff derived from visible result at lines 234-236 | 🛑 BLOCKER | Spoiler metric cannot detect a future chapter leak. |
| `tests/ci/test_timeline_release_gate.py` | `_production_report` synthetic positive artifact | 🛑 BLOCKER | Gate can pass without an observed DB run. |

No unreferenced `TBD`, `FIXME`, or `XXX` markers were found in 08-07/08-08-owned files. The disconfirmation pass found: one partially implemented requirement (production cancel), one misleading green test (mock-only source_start), and one uncovered error path (reconcile cache reuse after prompt/schema change).

## Deferred Items

The roadmap has no later executable Phase 09-11 entries whose goals or success criteria specifically close these four gaps. Relationship graph, reader AI, clue tracking, and historical support remain intentionally deferred and are not gaps.

## Human Verification Required

None for this verdict. Visual polish is optional future UAT; the four failures are programmatically observable and require code/test changes rather than human judgment.

## Gaps Summary

08-07 and 08-08 converted the previous hollow architecture into a functioning production-backed timeline path, but did not fully satisfy the phase contract. A running task cannot be cancelled safely, reconciliation cache identity is incomplete, the source-offset ordering proof is mock-only, and qualification/release evidence remains self-assertable in two important ways. These are blockers under the must-have decision tree, so Phase 08 must not advance as passed.

---

_Verified: 2026-07-13T05:58:19Z_  
_Verifier: the agent (gsd-verifier)_
