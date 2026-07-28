---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 06
subsystem: qualification
tags: [fiction, timeline, deterministic-eval, live-qualification, release-gate, ci]
requires:
  - phase: 08-01..08-05
    provides: durable timeline lifecycle, strict extraction, reconciliation, spoiler-safe API, and interactive UI
provides:
  - Frozen fiction-only timeline corpus with deterministic quality and operational gates
  - Controlled balanced/quality dual-model qualification with fail-closed dependency semantics
  - Signed Phase 08 requirement/decision scorecard and local release evidence gate
affects: [phase-08-verification, release, timeline-quality]
tech-stack:
  added: []
  patterns: [canonical SHA-256 evidence reports, blocked dependencies are non-comparable, release evidence composition]
key-files:
  created: [backend/evals/timeline_fiction.v1.json, backend/tests/integration/timeline/test_e2e.py, backend/tests/live/test_timeline_dual_model.py, tests/ci/test_timeline_release_gate.py, .planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-QUALIFICATION.md]
  modified: [backend/scripts/run_timeline_qualification.py]
key-decisions:
  - "A blocked or unavailable live dependency always produces metrics=null and can never satisfy the release gate."
  - "Phase 08 release evidence is fiction-only and explicitly proves relationship graph, reader AI, clue tracking, and history contracts are absent."
patterns-established:
  - "Qualification reports bind fixture, source, hierarchy, prompt, schema, model, and version lineage with canonical SHA-256 digests."
requirements-completed: [REQ-TIME-01, REQ-TIME-02, REQ-TIME-04, REQ-TIME-05, REQ-TIME-06, REQ-TIME-07, REQ-TIME-08, REQ-TIME-09, REQ-TIME-10]
duration: 9min
completed: 2026-07-13
---

# Phase 08 Plan 06: Frozen Timeline Qualification and Release Gate Summary

**Fiction-only frozen qualification with deterministic replay, controlled balanced/quality model evidence, spoiler-safe release gating, and signed lineage-bound scorecards.**

## Performance

- **Duration:** 9 min
- **Started:** 2026-07-13T04:17:05Z
- **Completed:** 2026-07-13T04:25:47Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Froze 20 fiction timeline cases and 10 cross-chapter groups covering forward order, flashback/interlude, all four time precisions, participants, causality, evidence, cache, restart, budget, override, CAS, rollback, source separation, and spoiler gates.
- Added deterministic qualification output bound to exact source/prompt/schema/model/version lineage, with canonical report and fixture digests.
- Proved controlled balanced extraction plus quality reconciliation while forcing outage, invalid schema/evidence, budget pause, and spoiler leakage to remain non-comparable.
- Added a release gate requiring migration, API, frontend, fixture, signatures, qualified metrics, and spoiler-safe fiction scope.
- Generated `08-QUALIFICATION.md` with REQ-TIME and D-01..D-22 evidence without creating `08-VERIFICATION.md`.

## Task Commits

1. **Task 1 RED: frozen fiction qualification contract** - `fe7f05e`
2. **Task 1 GREEN: deterministic timeline qualification** - `33aeee2`
3. **Task 2 RED: live and release gate contracts** - `9b30ec3`
4. **Task 2 GREEN: dual-model release evidence** - `d7dacf4`
5. **Task 3: full Phase 08 qualification** - `e99ec66`

## Files Created/Modified

- `backend/evals/timeline_fiction.v1.json` - Frozen fiction cases, cross-chapter groups, lineage, and operational expectations.
- `backend/scripts/run_timeline_qualification.py` - Deterministic offline/live qualification, signatures, release verifier, CLI, and Markdown scorecard.
- `backend/tests/integration/timeline/test_e2e.py` - Repeatability, corpus coverage, metrics, and critical failure gates.
- `backend/tests/live/test_timeline_dual_model.py` - Controlled balanced/quality path and non-comparable outage/policy failures.
- `tests/ci/test_timeline_release_gate.py` - Migration/API/frontend/fixture/signature/spoiler release checks.
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-QUALIFICATION.md` - Signed qualification evidence and requirement/decision scorecards.

## Decisions Made

- Controlled live transport doubles are the repeatable release evidence; unavailable external providers are separately represented as `blocked_dependency`, never as a zero-score pass.
- Report integrity uses canonical SHA-256 over the complete unsigned report plus a direct SHA-256 of the frozen corpus.
- Release qualification fails closed if live evidence is required but blocked, paused, invalid, non-comparable, or missing metrics.

## Verification

- Backend Phase 08 unit/integration/adversarial: **56 passed**.
- Frozen E2E plus controlled live: **13 passed** (6 E2E, 7 live).
- Root release gate: **5 passed**.
- Frontend Vitest: **8 files, 66 tests passed**.
- Next.js production build: passed; `/analysis` generated.
- PostgreSQL lifecycle coverage ran and passed; no integration case was skipped.
- `08-VERIFICATION.md`: absent, as required.

## Deviations from Plan

None - plan executed exactly within the declared qualification/eval/CI ownership and GSD artifacts.

## Issues Encountered

- The first RED command used the wrong relative virtualenv path; rerunning with `backend/.venv` produced the intended missing-CLI failure. No file or dependency changes resulted.
- Existing pytest timeout-option warnings remain pre-existing and outside 08-06 ownership.

## Known Stubs

None. Controlled model results are explicit qualification fixtures; blocked external live dependencies remain non-comparable by contract.

## Threat Flags

None. This plan added no endpoint, authentication path, persistence schema, or file-access trust boundary.

## User Setup Required

None.

## Next Phase Readiness

- Phase 08 implementation and qualification artifacts are complete and ready for the independent verifier.
- The independent verifier exclusively owns `08-VERIFICATION.md`.

## Self-Check: PASSED

- All six declared qualification/eval/CI/report files exist.
- All five `08-06` task commits exist.
- All target backend, frontend, live, and CI gates passed.
- Unrelated dirty files remain unstaged and unmodified by this plan.

---
*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Completed: 2026-07-13*
