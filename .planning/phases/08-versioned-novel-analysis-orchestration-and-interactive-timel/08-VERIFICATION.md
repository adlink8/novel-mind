---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
verified: 2026-07-13T07:01:25Z
status: passed
score: 35/35 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 32/33
  gaps_closed:
    - "The executable release gate now owns fixed command execution, internally hashes captured output, independently re-reads PostgreSQL authority, and is exercised by CI."
  gaps_remaining: []
  regressions: []
---

# Phase 8: Versioned Novel Analysis Orchestration and Interactive Timeline Verification Report

**Phase Goal:** 以持久、版本化、证据约束的后台分析任务生成小说时间事件，并在全局分析工作台渐进展示防剧透的双顺序横向时间线。
**Verified:** 2026-07-13T07:01:25Z
**Status:** passed
**Re-verification:** Yes — final independent verification after 08-10

## Goal Achievement

Phase 08 is achieved in the current codebase. The previous release-authority blocker is closed by an executable CLI path that owns both evidence producers: fixed subprocess execution with output-bound SHA-256 attestations and a fresh PostgreSQL authority read. The complete production timeline, cancellation, cache lineage, source ordering, spoiler boundary, API/UI, qualification, and release paths passed independent execution.

### Observable Truths

| # | Truth | Status | Evidence |
|---:|---|---|---|
| 1 | REQ-TIME-01 durable, recoverable, cancellable, progressive jobs | ✓ VERIFIED | `worker.py:127-185`; five cancellation boundaries and restart/checkpoint suites passed. |
| 2 | REQ-TIME-02 immutable versions, validated promotion, overrides, rollback | ✓ VERIFIED | PostgreSQL lifecycle/CAS/rollback tests passed in the 77-test backend suite. |
| 3 | REQ-TIME-03 deep work starts on first analysis entry, not import | ✓ VERIFIED | API dispatches the production worker; production-worker/idempotent-start tests passed. |
| 4 | REQ-TIME-04 dual order and exact/relative/fuzzy/unknown precision | ✓ VERIFIED | Strict schemas, reconciliation, real API source ordering, and frontend contracts passed. |
| 5 | REQ-TIME-05 evidence-bound publication and protected manual corrections | ✓ VERIFIED | Evidence adversarial tests and override lifecycle tests passed. |
| 6 | REQ-TIME-06 person/order/causal controls | ✓ VERIFIED | Query and frontend tests cover selected-version participants, ordering, and opt-in causal edges. |
| 7 | REQ-TIME-07 progressive global analysis workspace | ✓ VERIFIED | 68 frontend tests, production build, and real desktop/mobile browser flow passed. |
| 8 | REQ-TIME-08 server-side spoiler protection | ✓ VERIFIED | Default/full PostgreSQL query comparison, spoiler tests, and real browser disclosure flow passed. |
| 9 | REQ-TIME-09 tiered routing and deterministic budget pause | ✓ VERIFIED | Persistent call-boundary, budget, capability, no-fallback, and cache tests passed. |
| 10 | REQ-TIME-10 fiction-only scope | ✓ VERIFIED | New contracts/UI/fixtures remain timeline-only; deferred products are absent. |
| 11 | D-01 global `/analysis` is primary and `/search` remains available | ✓ VERIFIED | Production build generated both routes; app shell and evidence links are wired. |
| 12 | D-02 only timeline is exposed, not six intermediates | ✓ VERIFIED | Analysis page and browser/unit tests expose no intermediate-mode menu. |
| 13 | D-03 responsive zoomable horizontal timeline | ✓ VERIFIED | ECharts dataZoom plus companion list passed desktop and 390px real E2E. |
| 14 | D-04 one unified timeline supports participant filtering | ✓ VERIFIED | Selected-source participant/query tests passed. |
| 15 | D-05 story and narrative order are distinct and source-backed | ✓ VERIFIED | DB evidence `source_start` reaches API/type/comparator; real order regression passed. |
| 16 | D-06 four strict time precision shapes do not invent dates | ✓ VERIFIED | Schema and adversarial suites passed. |
| 17 | D-07 typed causal overlay is optional | ✓ VERIFIED | Query returns edges only when requested and both endpoints are visible. |
| 18 | D-08 evidence-valid events publish without mandatory review | ✓ VERIFIED | Worker persists provisional chapter events after validation. |
| 19 | D-09 events retain source/evidence/model lineage | ✓ VERIFIED | ORM, worker persistence, qualification artifact, and evidence tests verify lineage. |
| 20 | D-10 append-only overrides survive/relink across reanalysis | ✓ VERIFIED | Override and PostgreSQL lifecycle tests passed. |
| 21 | D-11 immutable candidate/active/history/rollback lifecycle | ✓ VERIFIED | Stale CAS, failed candidate, manifest, journal, and rollback tests passed. |
| 22 | D-12 first entry triggers deep work; import does not | ✓ VERIFIED | API → background worker wiring and production worker tests passed. |
| 23 | D-13 chapter results/checkpoints publish progressively | ✓ VERIFIED | Chapter artifact, evidence, and checkpoint commit path is substantive and tested. |
| 24 | D-14 frozen balanced extraction and quality reconciliation | ✓ VERIFIED | Production runtime and strict gateway enforce separate deployments without fallback. |
| 25 | D-15 budget/cache identity includes actual contract lineage | ✓ VERIFIED | `reconciliation_contract_hashes()` feeds the cache key; prompt/schema mutation tests passed. |
| 26 | D-16 visible IDs precede overlays, edges, counts, aggregates, previews | ✓ VERIFIED | `query.py` visible-set-first flow and spoiler tests passed. |
| 27 | D-17 full-book disclosure is explicit and persisted per novel | ✓ VERIFIED | API preference and real browser confirmation flow passed. |
| 28 | D-18 fiction-only contracts and UI | ✓ VERIFIED | No history additions in Phase 08 artifacts. |
| 29 | D-19 relationship graph, reader AI, and clue tracking remain deferred | ✓ VERIFIED | No Phase 08 implementation/routes for these products. |
| 30 | D-20 missing progress exposes first chapter only | ✓ VERIFIED | `_chapter_cutoff` and spoiler tests enforce first chapter/empty novel behavior. |
| 31 | D-21 active and running-candidate data remain source-isolated | ✓ VERIFIED | API and frontend source-isolation tests passed. |
| 32 | D-22 unknown pricing pauses before provider transport | ✓ VERIFIED | Gateway/budget tests prove zero calls and `paused_budget`. |
| 33 | Release CLI independently re-reads PostgreSQL authority | ✓ VERIFIED | CLI imports `async_session_factory` internally (`run_timeline_qualification.py:703-710`), calls DB verifier (`635-640`), and a separate observer engine qualifies in `test_real_qualification.py:244-266`. |
| 34 | Release CLI executes fixed commands and hashes captured output bytes itself | ✓ VERIFIED | Code-owned `CommandSpec` set (`27-63`), direct `subprocess.run` (`66-85`), digest recomputation (`273-285`), and collector tests passed. |
| 35 | Real DB + successful commands qualify; command/DB/report failures block | ✓ VERIFIED | PostgreSQL positive, exit-9, and manifest-mismatch entry tests passed; forged digest, tampered report, missing report, and non-success policy tests fail closed. |

**Score:** 35/35 truths verified

## Previous Blocker Closure

| Required condition | Status | Independent evidence |
|---|---|---|
| `verify_release_evidence_from_db()` has an executable CLI caller and enters CI | ✓ VERIFIED | `run_release_verification()` calls it; `--verify-release` reaches that entry; CI integration command includes `test_real_qualification.py` at `.github/workflows/ci.yml:444`. |
| Positive production path creates its own DB observer | ✓ VERIFIED | CLI imports the production factory internally; positive integration creates a separate SQLAlchemy engine/session factory and re-reads rows. |
| Structured fixed commands are really executed and exact combined output is hashed | ✓ VERIFIED | No shell interpolation; fixed cwd/argv specs feed `subprocess.run`, stdout+stderr bytes are retained, SHA-256 is computed and recomputed before acceptance. |
| CLI cannot inject authority, command results, digest, or arbitrary command list | ✓ VERIFIED | Parser help exposes report/policy/output and qualification/E2E modes only; forbidden argument contract passed. |
| Positive test does not copy authority, use pseudo-digests, or monkeypatch success | ✓ VERIFIED | Positive PostgreSQL test passes only observer sessions and genuinely executed `CommandSpec`s. Copied authority and `'f' * 64` occur only in a negative test asserting `blocked_release`. |
| Non-zero command, DB mismatch, missing/tampered report fail closed | ✓ VERIFIED | Exit 9 and mutated manifest tests block; tamper contracts block; missing report CLI emitted `blocked_release`, `quality_comparable=false`, exit 1. |

## Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `backend/app/services/timeline/worker.py` | Production durable orchestration | ✓ VERIFIED | 606 substantive lines; API dispatches it; real PostgreSQL artifacts flow through it. |
| `backend/app/services/timeline/model_gateway.py` | Strict persistent model-call boundary | ✓ VERIFIED | 457 lines; budget, capability, repair, audit, exact-cache behavior tested. |
| `backend/app/services/timeline/reconcile.py` | Contract-bound reconciliation | ✓ VERIFIED | 240 lines; real prompt/schema hashes and explicit conflict handling. |
| `backend/app/services/timeline/query.py` | Spoiler-safe source-isolated projection | ✓ VERIFIED | 177 lines; DB events/evidence drive visible output. |
| `backend/app/api/timeline.py` | Owner-scoped lifecycle/query API | ✓ VERIFIED | 183 lines; routes dispatch/query/edit/preference/rollback services. |
| `frontend/src/app/analysis/page.tsx` | Global progressive timeline workspace | ✓ VERIFIED | 109 lines; real typed API state feeds version-isolated controls/chart. |
| `frontend/src/components/timeline/timeline-chart.tsx` | Dual-order accessible timeline | ✓ VERIFIED | 71 lines; required `source_start`, zoom, list, and interaction are wired. |
| `backend/scripts/run_timeline_qualification.py` | Executable self-observing release gate | ✓ VERIFIED | 911 lines; qualification, command collector, DB observer, CLI verdict all wired. |
| `tests/ci/test_timeline_release_gate.py` | Forgery and CLI contracts | ✓ VERIFIED | 204 lines; negative-only synthetic authority/digest use. |
| `backend/tests/integration/timeline/test_real_qualification.py` | PostgreSQL positive/fail-closed entry proof | ✓ VERIFIED | 323 lines; independent observer and real subprocesses. |
| `.github/workflows/ci.yml` | CI execution of PostgreSQL release entry tests | ✓ VERIFIED | Integration job runs the real qualification test after migration. |

`gsd-sdk verify.artifacts` independently reported 4/4 08-10 artifacts present and substantive. Its key-link parser returned `Source file not found` because PLAN `from` fields are descriptive labels rather than literal paths; the links were therefore verified manually by call-site and executed-test evidence above.

## Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| CLI `--verify-release` | `run_release_verification` | `_run_release_cli` with internally imported factory | ✓ WIRED | Machine-readable verdict; non-qualified returns exit 1. |
| Release entry | fixed command set | code-owned cwd/argv → direct subprocess | ✓ WIRED | No caller CLI command list or shell text. |
| Command collector | release checker | captured bytes + exit code + recomputed SHA-256 | ✓ WIRED | Fabricated 64-char strings cannot satisfy bytes/digest validation. |
| Release entry | PostgreSQL authority | `verify_release_evidence_from_db` → fresh session | ✓ WIRED | Run/version/pointer/attempt/evidence authority is independently reconstructed. |
| CI integration job | real release-entry tests | pytest against locked PostgreSQL service | ✓ WIRED | Workflow line 444; workflow policy suites passed. |
| API start/resume | production worker | background dispatch | ✓ WIRED | Production-worker and real browser paths passed. |
| Worker | Phase 07 hierarchy/model gateway/promotion | DB evidence → strict calls → persisted candidate → CAS | ✓ WIRED | Complete backend suite passed. |
| Evidence refs | API/frontend order | minimum `source_start` → required TS field → comparator | ✓ WIRED | Real authenticated response/order regression passed. |
| Reading progress | default/full projection | server cutoff before derived fields | ✓ WIRED | Spoiler and browser tests passed. |

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| Worker | chapter package/events/checkpoints | Phase 07 PostgreSQL hierarchy/evidence | Yes | ✓ FLOWING |
| Reconciliation | output/cache identity | persisted candidates + strict quality gateway + actual contract hashes | Yes | ✓ FLOWING |
| Timeline query/UI | events, people, edges, counts, `source_start` | PostgreSQL → API → typed frontend | Yes | ✓ FLOWING |
| Spoiler qualification | default/full IDs, edges, counts | two production queries + persisted cutoff | Yes | ✓ FLOWING |
| Release authority | observed authority | new PostgreSQL observer session | Yes | ✓ FLOWING |
| Release command evidence | exit/digest | internally executed fixed subprocess output bytes | Yes | ✓ FLOWING |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| PostgreSQL release authority | `backend/.venv/Scripts/python.exe -m pytest tests/integration/timeline/test_real_qualification.py -q` | 5 passed | ✓ PASS |
| Release/workflow/CI policy | `backend/.venv/Scripts/python.exe -m pytest tests/ci/test_timeline_release_gate.py tests/ci/test_workflow_security.py tests/ci/test_ci_gate.py -q` | 47 passed | ✓ PASS |
| Cancellation/cache/source/spoiler regressions | `backend/.venv/Scripts/python.exe -m pytest tests/integration/timeline/test_final_gaps.py -q` | 9 passed | ✓ PASS |
| Full Phase 08 backend | `backend/.venv/Scripts/python.exe -m pytest tests/unit/timeline tests/integration/timeline tests/adversarial/test_timeline_evidence.py -q` | 77 passed | ✓ PASS |
| Frontend unit/contracts | `npm test -- --run` | 68 passed | ✓ PASS |
| Frontend production build | `npm run build` | passed; `/analysis` and `/search` generated | ✓ PASS |
| Real desktop/mobile flow | `npm run test:e2e -- timeline-real.spec.ts` | exit 0; 2 Playwright projects | ✓ PASS |
| Missing report fail-closed | `python scripts/run_timeline_qualification.py --verify-release --report does-not-exist.json` | `blocked_release`, non-comparable, exit 1 | ✓ PASS |
| Migration head | `python -m alembic current` | `10analysistime01 (head)` | ✓ PASS |
| Schema drift | `python -m alembic check` | Phase 07 index drift remains | ⚠ WARNING |

Pytest emitted only the known unavailable `pytest-timeout` configuration/marker warnings; no tests were skipped or failed in the full Phase 08 suite.

## Probe Execution

No Phase 08 PLAN declares a probe and no `scripts/**/probe-*.sh` path exists. Step 7c is not applicable.

## Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|---|---|---|---|---|
| REQ-TIME-01 | 01, 04, 06, 07, 09, 10 | Durable/recoverable/cancellable/progressive jobs | ✓ SATISFIED | Worker, cancellation, checkpoint, API and release tests. |
| REQ-TIME-02 | 01, 03, 06, 07, 10 | Immutable version/promotion/rollback | ✓ SATISFIED | PostgreSQL lifecycle and qualification authority. |
| REQ-TIME-03 | 01, 04, 07 | First-entry deep analysis | ✓ SATISFIED | API-to-worker production tests. |
| REQ-TIME-04 | 02, 03, 06, 08, 09 | Dual order/four precision | ✓ SATISFIED | Schema, reconciliation, source offset, UI tests. |
| REQ-TIME-05 | 02, 03, 06, 07, 10 | Evidence/lineage/override | ✓ SATISFIED | Adversarial, persistence, override, release evidence. |
| REQ-TIME-06 | 03, 04, 05, 06, 08 | Person/order/causal UI/API | ✓ SATISFIED | Backend/frontend/browser coverage. |
| REQ-TIME-07 | 05, 06, 08, 09 | Progressive global workspace | ✓ SATISFIED | Build and real browser flow. |
| REQ-TIME-08 | 04, 05, 06, 08, 09 | Spoiler protection | ✓ SATISFIED | Visible-set tests and default/full production observation. |
| REQ-TIME-09 | 02, 03, 06, 07, 09, 10 | Routing/budget/cache | ✓ SATISFIED | Gateway, persistence, qualification, release checks. |
| REQ-TIME-10 | 05, 06, 08 | Fiction-only boundary | ✓ SATISFIED | Scope scan and UI/fixture evidence. |

All REQ-TIME-01..10 appear in PLAN frontmatter and `.planning/REQUIREMENTS.md`; no Phase 08 requirement is orphaned.

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---:|---|---|---|
| `backend/scripts/run_timeline_qualification.py` | 493 | `return {}` | ℹ INFO | Intentional missing-authority result; equality check fails closed. |
| `backend/app/services/timeline/reconcile.py` | 239 | `return {}, [conflict]` | ℹ INFO | Intentional contradictory chronology result; conflict is preserved instead of fabricating order. |
| Repository migration state | — | Phase 07 index drift | ⚠ WARNING | `ix_chunk_hierarchy_nodes_build_id` add and `idx_text_chunks_hierarchy_node` remove remain outside Phase 08 ownership; Phase 08 head and tests pass. |

No Phase 08-owned implementation file contains unreferenced `TBD`, `FIXME`, or `XXX`. No placeholder UI, console-only handler, orphaned Phase 08 artifact, or hardcoded empty dynamic source was found.

Disconfirmation pass:

- Partial requirement: none among the 35 final must-haves.
- Misleading green test: synthetic copied authority and `'f' * 64` remain only in a negative contract that now expects `blocked_release`; the positive test uses a fresh DB observer and executed subprocesses.
- Uncovered error path: malformed/missing/tampered report, non-zero command, DB mismatch, blocked live policy, spoiler failure, and missing production output all have code or executed test evidence.

## Human Verification Required

None. The visual/responsive and real-time progression requirements have deterministic desktop/mobile Playwright coverage against real Next.js, FastAPI, PostgreSQL, and timeline APIs.

## Deferred Items

No failed item required deferred filtering. Relationship graph, reader AI, clue tracking, and history support are explicit non-goals rather than Phase 08 gaps.

## Gaps Summary

No blocking or warning-class must-have gap remains. The sole previous blocker is closed. The pre-existing Phase 07 Alembic index drift is recorded as a repository warning and does not contradict a Phase 08 truth, artifact, or key link.

---

_Verified: 2026-07-13T07:01:25Z_
_Verifier: the agent (gsd-verifier)_
