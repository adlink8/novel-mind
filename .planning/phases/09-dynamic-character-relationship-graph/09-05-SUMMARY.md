---
phase: 09-dynamic-character-relationship-graph
plan: 05
subsystem: testing
tags: [qualification, release-gate, adversarial, performance, playwright, postgresql, cytoscape]

# Dependency graph
requires:
  - phase: 09-dynamic-character-relationship-graph
    provides: observation pipeline, graph API, overrides, projection, Cytoscape workspace
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: executable release CLI pattern, real browser qualification authority
provides:
  - Frozen fiction relationship qualification corpus (30+ cases, 15+ adversarial)
  - Independent PostgreSQL adversarial/performance/release evidence
  - Executable relationship release CLI with blocked_release fail-closed verdicts
  - Real desktop/mobile Playwright journeys (spoiler, filters, evidence, over-cap)
  - CI relationship qualification job + artifacts without weakening Phase 08 gates
affects: [09-verify, 10-reader-ai, 11-clue-tracking]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Phase 08-style release authority: fresh DB session + internally executed command digests
    - Critical security/spoiler failures are boolean blockers, not averaged scores
    - Indexed character prefilter on graph query for 10k-observation p95 budget

key-files:
  created:
    - backend/evals/relationship_fiction.v1.json
    - backend/scripts/run_relationship_qualification.py
    - backend/tests/adversarial/test_relationship_boundaries.py
    - backend/tests/integration/relationships/test_boundaries_pg.py
    - backend/tests/integration/relationships/test_performance.py
    - backend/tests/integration/relationships/test_release_gate.py
    - frontend/e2e/relationships-real.spec.ts
  modified:
    - backend/app/services/relationships/query.py
    - .github/workflows/ci.yml

key-decisions:
  - "Release CLI owns fixed argv/cwd commands and recomputes digests from combined output bytes."
  - "PostgreSQL PG boundary attacks live under integration/relationships; unit adversarial stays fixture/gate-only."
  - "Character filter pushes endpoint prefilter into SQL so 10k seeds meet p95<=300ms after warmup."
  - "Browser E2E seeds via qualification CLI only; no route mocks; over-cap uses bulk insert."

patterns-established:
  - "relationship-production-qualification.v1 report + blocked_release on any authority/command failure"
  - "scope_scan proves load_filtered_relationship_graph + list_accepted_observation_refs without chat/clue modules"
  - "CI adds relationship-junit + scope-scan artifacts while keeping timeline real_qualification job intact"

requirements-completed: [REQ-REL-01, REQ-REL-02, REQ-REL-03, REQ-REL-04, REQ-REL-05, REQ-REL-06]

# Metrics
duration: 95min
completed: 2026-07-15
---

# Phase 09 Plan 05: Relationship Qualification and Release Gate Summary

**Frozen fiction corpus, PostgreSQL adversarial/performance suites, real two-viewport Playwright, and fail-closed executable release authority for the Phase 09 relationship graph.**

## Performance

- **Duration:** 95 min
- **Started:** 2026-07-15T08:00:00Z
- **Completed:** 2026-07-15T09:00:00Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- Added `relationship_fiction.v1.json` with 35 labeled spans, 17 adversarial cases, five edge types, establish/change/end transitions, and deferred product absences.
- PostgreSQL trust-boundary tests prove cross-owner/version isolation, future metadata non-leak, and legacy `character_relations` exclusion.
- Performance seed of 10,000 accepted observations asserts hard caps (500/1500), `filters_required` empty elements, and measured p95 ≤ 300 ms on filtered queries after warmup.
- `run_relationship_qualification.py` binds independent DB authority, projection replay checksum, cytoscape@3.34.0 lock, scope scan, and command digests; missing/tampered evidence yields `blocked_release`.
- Playwright `relationships-real.spec.ts` passes chromium-desktop and chromium-mobile-390 with real Next/FastAPI/PostgreSQL (spoiler, filters, zoom, evidence, over-cap degradation).
- CI retains Phase 08 timeline integration and adds relationship qualification suite + artifact upload.

## Task Commits

1. **Tasks 1–3: frozen corpus, adversarial/performance/release, browser E2E, CI, query prefilter** - `98fc529` (feat)

**Plan metadata:** (this SUMMARY commit follows)

## Verification Digests

| Command | Result |
|---|---|
| `pytest tests/unit/relationships tests/integration/relationships tests/adversarial/test_relationship_boundaries.py -q` | **60 passed**, 0 skip |
| `scripts/run_relationship_qualification.py --scope-scan` | `scope_clean=true`, phase10/11 contracts present |
| Production qualification report | `status=qualified`, `report_sha256=b424a89eef6597a6d2222c07d046de6f9e5d348800b92b1a9713bed8360312ef` |
| Release verification (mock command specs) | `status=qualified`, all checks true |
| `npm test -- --run` | **85 passed** |
| `npm run lint` | 0 errors (4 pre-existing warnings outside Phase 09) |
| `npm run build` | Next production build OK |
| `npm run test:e2e -- relationships-real.spec.ts` | **4 passed** (2 tests × desktop + mobile-390) |

## Files Created/Modified

- `backend/evals/relationship_fiction.v1.json` — frozen fiction-only qualification corpus
- `backend/scripts/run_relationship_qualification.py` — production qualify / release verify / E2E seed CLI
- `backend/tests/adversarial/test_relationship_boundaries.py` — unit gate/fixture adversarial surface
- `backend/tests/integration/relationships/test_boundaries_pg.py` — PostgreSQL owner/version/spoiler/legacy attacks
- `backend/tests/integration/relationships/test_performance.py` — 10k seed, tiers, p95 budget
- `backend/tests/integration/relationships/test_release_gate.py` — executable release authority contracts
- `frontend/e2e/relationships-real.spec.ts` — real-stack browser journeys
- `backend/app/services/relationships/query.py` — character-id SQL prefilter for D-22 budget
- `.github/workflows/ci.yml` — relationship qualification job + artifacts

## Decisions Made

- Follow Phase 08 release authority model (fresh session factory + internal command capture).
- Split unit vs PostgreSQL adversarial tests to avoid pytest plugin double-registration.
- Bulk-insert over-cap browser seed so E2E stays under timeout while still proving filters_required.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Character-filtered 10k query exceeded p95 budget**
- **Found during:** Task 1 performance verification
- **Issue:** `build_graph` loaded all 10k rows before character filter; p95 ≈ 583 ms
- **Fix:** SQL prefilter by related character endpoints (including identity aliases) before fold
- **Files modified:** `backend/app/services/relationships/query.py`
- **Verification:** performance test p95 ≤ 300 ms; existing API/spoiler tests still pass
- **Committed in:** `98fc529`

**2. [Rule 2 - Missing Critical] pytest_plugins collision for adversarial PG fixtures**
- **Found during:** Task 1 adversarial collection with integration suite
- **Issue:** `pytest_plugins = ["tests.integration.conftest"]` double-registers when both trees collected
- **Fix:** unit adversarial stays fixture/gate-only; PG attacks moved to `test_boundaries_pg.py`
- **Files modified:** adversarial + integration boundary test files
- **Verification:** 18 boundary+release tests pass together
- **Committed in:** `98fc529`

---

**Total deviations:** 2 auto-fixed (1 performance, 1 test layout)
**Impact on plan:** Required for acceptance criteria; no scope creep into Phase 10/11 product surfaces.

## Issues Encountered

- Local E2E requires `NOVELMIND_DATABASE_URL` pointing at reachable PostgreSQL (CI service on `:5433` used in this run). Default `.env` port 5432 was down on the qualification host.

## User Setup Required

None - no external service configuration required beyond existing PostgreSQL for integration/E2E.

## Next Phase Readiness

- Phase 09 plans 5/5 implement-complete; ready for `$gsd-verify-work` / Phase 09 verification.
- Phase 10 may depend on `load_filtered_relationship_graph`; Phase 11 on `list_accepted_observation_refs` (both present, no chat/clue implementation).

## Self-Check: PASSED

- All plan files exist on disk
- `git log --grep=09-05` returns feat commit
- Acceptance criteria exercised via commands above
- REQ-REL-01..06 and D-01..D-24 validation rows remain covered by 09-01..09-05 artifacts

---
*Phase: 09-dynamic-character-relationship-graph*
*Completed: 2026-07-15*
