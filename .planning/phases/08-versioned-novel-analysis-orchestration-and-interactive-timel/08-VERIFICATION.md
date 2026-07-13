---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
verified: 2026-07-13T06:33:15Z
status: gaps_found
score: 32/33 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 29/32
  gaps_closed:
    - "REQ-TIME-01: production workers now re-read durable cancel_requested at stage boundaries and stop before later calls or promotion"
    - "D-15: reconciliation exact-cache identity now binds the actual prompt and Pydantic output-schema hashes"
    - "D-05: persisted evidence source_start now flows through the real API and required TypeScript contract into frontend ordering"
    - "Qualification spoiler measurement now compares default/full production queries against the persisted reading cutoff"
  gaps_remaining:
    - "Release authority is not wired to an independent DB-and-command observer; the positive gate still accepts caller-supplied synthetic observations and fake output digests"
  regressions: []
gaps:
  - truth: "Phase 08 release authority requires independently re-read PostgreSQL evidence and actual successful command output, and rejects a synthetic self-hashed artifact even when the caller repeats its claims"
    status: failed
    reason: "A safe DB-rechecking helper exists, but no CI, CLI, or other release entrypoint calls it. The only positive release-gate test calls the lower-level verifier with observed_authority copied from the synthetic report and command results containing fabricated 64-character digests; this returns qualified. Command digests are checked only for length and exit_code, not produced by executing or binding actual commands."
    artifacts:
      - path: "backend/scripts/run_timeline_qualification.py"
        issue: "verify_release_evidence_from_db is orphaned; main() only generates reports. _command_results_valid accepts arbitrary 64-character strings and never executes or cryptographically binds command output."
      - path: "tests/ci/test_timeline_release_gate.py"
        issue: "The positive test builds a synthetic report, passes its own authority back as observed_authority, and uses 'f' * 64 for every output digest; no test invokes verify_release_evidence_from_db against PostgreSQL or captures real command output."
      - path: ".github/workflows/ci.yml"
        issue: "No Phase 08 release-authority command or DB-backed verification entrypoint is wired into CI."
    missing:
      - "A real release command/CI entrypoint that executes required checks, captures exit codes and output digests itself, then calls verify_release_evidence_from_db with a live PostgreSQL session."
      - "An integration/CI test proving a self-hashed synthetic report cannot qualify even when it supplies matching caller-controlled authority and fabricated command digests."
---

# Phase 8: Versioned Novel Analysis Orchestration and Interactive Timeline Verification Report

**Phase Goal:** 以持久、版本化、证据约束的后台分析任务生成小说时间事件，并在全局分析工作台渐进展示防剧透的双顺序横向时间线。
**Verified:** 2026-07-13T06:33:15Z
**Status:** gaps_found
**Re-verification:** Yes — third independent verification after 08-09

## Goal Achievement

The production timeline path itself is now substantive and working: durable cancellation, prompt/schema-bound reconciliation caching, evidence-derived source ordering, progressive active/candidate APIs, spoiler-safe queries, and the real desktop/mobile browser journey all passed. Phase 08 still cannot pass because the release-authority must-have is not connected to an independent observer. The repository can qualify a synthetic report when a caller repeats the report's own authority and supplies fake 64-character command digests.

### Previous Blocker Re-check

| Previous blocker | Status | Code and executed evidence |
|---|---|---|
| Worker polls persisted cancellation between production stages | ✓ VERIFIED | `worker.py` re-reads `AnalysisRun.cancel_requested` after preparation, before extraction transport, after extraction, after chapter persistence, before/after reconciliation, and before promotion. Five PostgreSQL boundary cases passed in `test_final_gaps.py`. |
| Reconcile cache binds actual prompt/schema/version lineage | ✓ VERIFIED | `reconciliation_contract_hashes()` hashes `RECONCILIATION_PROMPT` and `ReconciliationOutputModel.model_json_schema()`; both enter the durable cache key. Prompt/schema mutation cases each caused a cache miss and audited call. |
| `source_start` flows DB → API → TS → frontend sort | ✓ VERIFIED | `query.py` takes the minimum persisted `TimelineEvidenceRef.source_start`; `TimelineVisibleEvent` and `TimelineEvent` require it; comparator is chapter → source_start → event ID. Real authenticated API serialization/order test passed. |
| Spoiler metric and release authority are independently observed | ✗ FAILED | Default/full query comparison is fixed and passed, but DB authority and command attestations remain caller-supplied. The DB wrapper is uncalled and fake command digests qualify in the positive contract test. |

### Requirements — REQ-TIME-01..10

| Requirement | Status | Evidence |
|---|---|---|
| REQ-TIME-01 durable/recoverable/cancellable/progressive jobs | ✓ VERIFIED | PostgreSQL lease/checkpoint/resume, atomic chapter publication, and five mid-run cancellation boundaries passed; cancelled runs do not promote. |
| REQ-TIME-02 immutable versioning/validation/override/rollback | ✓ VERIFIED | Version lifecycle suite proves immutable candidates, stale-CAS rejection, failed-candidate isolation, override relink, and byte-identical rollback manifests. |
| REQ-TIME-03 first analysis entry triggers deep analysis only | ✓ VERIFIED | Owner-scoped start/resume dispatches the production worker idempotently; completed stages are reused and import has no timeline-worker call. |
| REQ-TIME-04 dual order and four precision classes | ✓ VERIFIED | Strict schemas and persistence cover exact/relative/fuzzy/unknown; narrative and story order remain separate; source-offset API ordering passed. |
| REQ-TIME-05 evidence-bound publication/manual protection | ✓ VERIFIED | Production events require persisted Phase 07 evidence offsets/hashes and lineage; invalid evidence fails; append-only manual overlays survive reanalysis. |
| REQ-TIME-06 person/order/causal controls | ✓ VERIFIED | Selected-version person filtering, story/narrative switch, and opt-in evidence-backed causal edges pass backend/frontend tests. |
| REQ-TIME-07 progressive global workspace | ✓ VERIFIED | `/analysis` displays separate active/candidate lifecycle, progress/errors/update state, zoomable chart and accessible list; real desktop/mobile journey passed. |
| REQ-TIME-08 server-side spoiler protection | ✓ VERIFIED | Visible-set-first PostgreSQL queries enforce persisted cutoff, first-chapter default, explicit full-book preference, endpoint-safe edges/counts/previews; default/full observation passed. |
| REQ-TIME-09 tiered routing and deterministic budget pause | ✓ VERIFIED | Frozen balanced/quality deployments, strict no-fallback gateway, PostgreSQL reserve/settle, unknown-price pause and exact-cache audits passed. |
| REQ-TIME-10 fiction-only scope | ✓ VERIFIED | Timeline UI/corpus remain fiction-only; relationship graph, reader AI, clue lifecycle, history and six intermediate UI modes are absent. |

**Requirement score:** 10/10 verified

### Decision Verification — D-01..D-22

| Decision | Status | Evidence |
|---|---|---|
| D-01 | ✓ VERIFIED | Global `/analysis` is primary; `/search` remains available. |
| D-02 | ✓ VERIFIED | Workspace exposes timeline only, not analysis intermediates. |
| D-03 | ✓ VERIFIED | ECharts horizontal zoom/pan plus companion list passed desktop and 390px Playwright. |
| D-04 | ✓ VERIFIED | Unified timeline is filtered by selected participant. |
| D-05 | ✓ VERIFIED | Dual order persists; narrative projection is chapter → persisted evidence source_start → ID end to end. |
| D-06 | ✓ VERIFIED | Four strict precision shapes reject fabricated/incomplete time forms. |
| D-07 | ✓ VERIFIED | Typed causal overlay is hidden by default and explicitly toggled. |
| D-08 | ✓ VERIFIED | Evidence-valid LLM events publish provisionally without a mandatory review queue. |
| D-09 | ✓ VERIFIED | Events retain chapter, offsets/hash, confidence, timestamps and prompt/schema/model lineage. |
| D-10 | ✓ VERIFIED | Field-level append-only overrides survive or become explicit `needs_relink`. |
| D-11 | ✓ VERIFIED | Candidate/active pointer, validation, CAS promotion, history and rollback are PostgreSQL-backed. |
| D-12 | ✓ VERIFIED | Deep work starts on analysis entry; import does not trigger timeline LLM work. |
| D-13 | ✓ VERIFIED | Chapter artifact/evidence/checkpoint commit progressively; partial and active views remain distinct. |
| D-14 | ✓ VERIFIED | Extraction uses frozen balanced deployment; reconciliation uses frozen quality deployment. |
| D-15 | ✓ VERIFIED | Budget/checkpoint/cache lineage includes source, hierarchy, actual prompt/schema, model, decoding and config. |
| D-16 | ✓ VERIFIED | API filters visible IDs before overrides, edges, filters, counts, aggregates and previews. |
| D-17 | ✓ VERIFIED | Full-book disclosure requires confirmation and persisted per-novel preference. |
| D-18 | ✓ VERIFIED | New contracts, fixtures and UI are fiction-only. |
| D-19 | ✓ VERIFIED | Relationship graph, reader selected-text AI and clue tracking remain absent/deferred. |
| D-20 | ✓ VERIFIED | Missing progress exposes only the first chapter; no chapter means no events. |
| D-21 | ✓ VERIFIED | Active/running-candidate envelopes, events, people and aggregates are source-isolated. |
| D-22 | ✓ VERIFIED | Unknown pricing pauses before transport and no later provider call is authorized. |

**Decision score:** 22/22 verified

### Additional Plan Must-Have

| Must-have | Status | Evidence |
|---|---|---|
| Release authority requires independent DB observations and real command outputs; synthetic self-hash artifacts are rejected | ✗ FAILED | `verify_release_evidence_from_db()` has no call site. `main()` only generates reports. Positive release test qualifies a synthetic report with copied authority and `'f' * 64` output hashes. |

**Combined score:** 32/33 unique must-haves verified

## Required Artifacts

| Artifact | L1/L2/L3/L4 status | Details |
|---|---|---|
| `backend/app/services/timeline/worker.py` | ✓ VERIFIED | Substantive production Phase 07 → extraction → persistence → reconcile → validate/promote pipeline; API dispatches it; DB evidence and checkpoints flow. |
| `backend/app/services/timeline/model_gateway.py` | ✓ VERIFIED | Strict persistent capability/budget/attempt/repair/cache boundary used by both production stages. |
| `backend/app/services/timeline/reconcile.py` | ✓ VERIFIED | Actual prompt/schema contract is shared by transport and cache hashing; deterministic topology/causal materialization is wired. |
| `backend/app/services/timeline/query.py` | ✓ VERIFIED | PostgreSQL visible-set-first projection derives source_start from evidence and returns real active/candidate data. |
| `backend/app/api/timeline.py` | ✓ VERIFIED | Owner-scoped lifecycle, dispatch, query, edit, preference and rollback endpoints are wired. |
| `frontend/src/app/analysis/page.tsx` | ✓ VERIFIED | Real API polling and selected-source state feed timeline controls/chart/status. |
| `frontend/src/components/timeline/timeline-chart.tsx` | ✓ VERIFIED | Required typed source_start drives stable narrative ordering and real browser rendering. |
| `backend/scripts/run_timeline_qualification.py` | ⚠ PARTIAL | Production qualification and spoiler observation are real, but release verification's independent-observer wrapper is orphaned and command attestations are not produced by execution. |
| `tests/ci/test_timeline_release_gate.py` | ✗ INCOMPLETE WIRING | Negative self-hash case exists, but positive gate uses synthetic copied authority and fabricated hashes instead of DB-backed/output-backed observations. |
| `frontend/e2e/timeline-real.spec.ts` | ✓ VERIFIED | Real Next.js/FastAPI/PostgreSQL/API path; no timeline interception; desktop/mobile passed. |

## Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| Start/resume API | production worker | `BackgroundTasks` → `dispatch_timeline_run` | ✓ WIRED | Real API/browser and production-worker tests reach progressive candidate and promotion. |
| Worker | Phase 07 hierarchy | active build + evidence-node queries | ✓ WIRED | Evidence offsets/hashes become persisted event refs. |
| Worker | cancellation state | fresh DB read at stage boundaries | ✓ WIRED | Five boundary cases end `cancelled`, suppress later calls and active pointer. |
| Worker | strict model gateway | extraction and reconciliation `generate()` | ✓ WIRED | Durable attempts, reservation, settlement and exact cache are observed. |
| Reconcile cache | actual contract lineage | canonical prompt + Pydantic JSON schema hashes | ✓ WIRED | Either hash mutation causes one new provider call. |
| Evidence refs | API event | minimum persisted source_start | ✓ WIRED | Real authenticated response returns ordered `[0, 80, 100]`. |
| API event | frontend comparator | required `TimelineEvent.source_start` | ✓ WIRED | No optional cast/fallback remains in TS comparator. |
| Default/full query | spoiler metric | persisted cutoff + event/edge/count set comparison | ✓ WIRED | Non-tautological observation passed against PostgreSQL. |
| Release report | independent DB/command authority | DB wrapper + command execution | ✗ NOT WIRED | Wrapper has zero call sites; command output is never executed/captured by the gate. |

## Data-Flow Trace (Level 4)

| Artifact | Data variable | Source | Produces real data | Status |
|---|---|---|---|---|
| Worker | evidence package/checkpoint | Phase 07 PostgreSQL hierarchy | Yes | ✓ FLOWING |
| Reconciliation | output/cache identity | strict quality gateway + actual prompt/schema lineage | Yes | ✓ FLOWING |
| Timeline query/chart | `source_start`, events, people, edges | persisted event/evidence rows through API | Yes | ✓ FLOWING |
| Spoiler qualification | default/full IDs, edges, counts, cutoff | two production queries + reading progress | Yes | ✓ FLOWING |
| Release authority | observed DB rows and command outputs | caller-supplied values; safe DB wrapper unused | No authoritative producer wired | ✗ DISCONNECTED |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Four final gap regressions | `backend/.venv/Scripts/python.exe -m pytest tests/integration/timeline/test_final_gaps.py -q` | 9 passed | ✓ PASS |
| Full Phase 08 backend suite | `backend/.venv/Scripts/python.exe -m pytest tests/unit/timeline tests/integration/timeline tests/adversarial/test_timeline_evidence.py -q` | 74 passed | ✓ PASS |
| Release-gate contracts | `backend/.venv/Scripts/python.exe -m pytest tests/ci/test_timeline_release_gate.py -q` | 8 passed; positive test is synthetic and demonstrates the remaining wiring gap | ⚠ PARTIAL |
| Frontend unit/contracts | `npm test -- --run` | 68 passed | ✓ PASS |
| Frontend production build | `npm run build` | Compiled/typed; `/analysis` and `/search` generated | ✓ PASS |
| Real desktop/mobile user flow | `npm run test:e2e -- timeline-real.spec.ts` | exit 0; 2 projects through real Next.js/FastAPI/PostgreSQL APIs | ✓ PASS |
| Migration head | `backend/.venv/Scripts/python.exe -m alembic current` | `10analysistime01 (head)` | ✓ PASS |
| Schema drift | `backend/.venv/Scripts/python.exe -m alembic check` | Phase 07 add/remove index drift remains | ⚠ WARNING, pre-existing/non-Phase-08 |

## Probe Execution

No Phase 08 plan declares a probe and no `scripts/**/probe-*.sh` file was found. Step 7c is not applicable.

## Requirements Coverage

All `REQ-TIME-01..10` occur in Phase 08 plan frontmatter and `.planning/REQUIREMENTS.md`; none is orphaned. All ten are satisfied by current implementation and executed tests. The distinct release-authority must-have comes from 08-06/08-09 qualification plans and remains blocking despite the product requirements passing.

## Anti-Patterns and Disconfirmation Pass

| File | Pattern | Severity | Impact |
|---|---|---|---|
| `backend/scripts/run_timeline_qualification.py:219-229` | Command evidence validates only command names, `exit_code == 0`, and string length 64 | 🛑 BLOCKER | Fabricated digests satisfy the release gate; no real output binding. |
| `backend/scripts/run_timeline_qualification.py:523-542` | DB-backed verifier has no repository call site | 🛑 BLOCKER | Independent authority exists as an orphaned helper, not a release path. |
| `tests/ci/test_timeline_release_gate.py:25-88` | Synthetic positive report, copied authority, `'f' * 64` command digests | 🛑 BLOCKER | Green test does not prove independent DB evidence or actual commands. |

No unreferenced `TBD`, `FIXME`, or `XXX` marker exists in Phase 08-owned implementation files. The apparent `return {}` matches an intentional failed authority lookup; the reconcile empty mapping is paired with an explicit chronology conflict and is not a stub.

Disconfirmation checks:

- Partial requirement: none among REQ-TIME-01..10; the separate release-authority must-have is incomplete.
- Misleading green test: the positive release test accepts caller-controlled synthetic observations while claiming independent authority.
- Uncovered error path: no integration test invokes the DB-backed release wrapper and proves forged caller observations cannot bypass it.

## Deferred Items

No later roadmap phase specifically owns this release-authority wiring. Relationship graph, reader AI and clue tracking are intentional future scope and are not Phase 08 gaps.

## Human Verification Required

None for this verdict. The remaining failure is programmatically observable and requires wiring/test changes, not subjective UAT.

## Gaps Summary

08-09 closes the prior cancellation, reconciliation-cache, source-offset and spoiler-measurement defects. The phase remains blocked at the release gate: independent PostgreSQL observation and real command-output production are implemented only as an unused helper/parameter contract, while the actual positive test can qualify synthetic caller-controlled evidence. Under goal-backward verification, an orphaned safety function is not a working release authority.

---

_Verified: 2026-07-13T06:33:15Z_
_Verifier: the agent (gsd-verifier)_
