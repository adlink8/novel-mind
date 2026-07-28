---
phase: 11-clue-and-foreshadow-tracking
plan: "03"
subsystem: pipeline
tags: [clue, foreshadow, durable-worker, budget, versioning, overrides, spoiler-api]

requires:
  - phase: 11-clue-and-foreshadow-tracking
    provides: clue authority tables (11-01) and recall/judge/gates (11-02)
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: budget gate pattern, timeline_full_book spoiler preference, CAS pointer pattern
provides:
  - Durable run_clue_worker with lease/checkpoint/exact-cache and budget reservation before calls
  - Clue CAS promote/rollback with machine manifests and append-only pointer journal
  - Append-only lifecycle events and human confirm/reject/annotate/adjust_link overrides
  - Visible-set-first build_clue_version_view and /api/clues owner-scoped routes
affects:
  - 11-04 analysis workspace UI
  - 11-05 qualification and release gate

tech-stack:
  added: []
  patterns:
    - "Budget reservation + call attempt audit before provider I/O; unknown pricing pauses"
    - "Active pointer moves only after validated manifest + expected-revision CAS"
    - "Human overrides are INSERT supersession only; prior rows never UPDATE under PG triggers"
    - "Spoiler cutoff reuses Phase 08 resolve_chapter_cutoff + timeline_full_book"

key-files:
  created:
    - backend/app/services/clues/budget.py
    - backend/app/services/clues/versions.py
    - backend/app/services/clues/worker.py
    - backend/app/services/clues/lifecycle.py
    - backend/app/services/clues/overrides.py
    - backend/app/services/clues/query.py
    - backend/app/api/clues.py
    - backend/tests/integration/clues/test_worker_versions.py
    - backend/tests/integration/clues/test_override_reanalysis.py
    - backend/tests/integration/clues/test_spoiler_api.py
    - backend/tests/unit/clues/test_overrides.py
    - backend/tests/unit/clues/test_query_projection.py
  modified:
    - backend/app/main.py
    - backend/app/services/clues/__init__.py

key-decisions:
  - "Lifecycle evidence rows attach only to lifecycle_event_id (not machine_clue_id) to avoid unique-identity collisions rolling back machine clues"
  - "Latest-wins overrides by highest id per (logical_clue_id, field_name); never UPDATE prior override status on PostgreSQL"
  - "No clue-specific full-book preference; only timeline_full_book"
  - "Running candidate view skips reader cutoff (analysis job scope); active view always cutoff-first"

patterns-established:
  - "ClueCallRepository mirrors Phase 08 PostgresCallRepository against clue_* tables"
  - "derive_visible_state filters lifecycle events before replay so paid_off can appear as reinforced pre-cutoff"
  - "Human confirm/reject append lifecycle + disposition override; annotate/adjust_link do not change state"

requirements-completed: [REQ-CLUE-01, REQ-CLUE-02, REQ-CLUE-03, REQ-CLUE-04, REQ-CLUE-05]

duration: 95min
completed: 2026-07-15
---

# Phase 11 Plan 03: Durable Worker, Versioning, Overrides, Spoiler API Summary

**Durable clue worker with budget-safe exact-cache stages, CAS version promotion, append-only human overrides that survive reanalysis, and owner-scoped spoiler-safe `/api/clues` projection.**

## Performance

- **Duration:** 95 min
- **Started:** 2026-07-15T03:00:00Z
- **Completed:** 2026-07-15T04:35:00Z
- **Tasks:** 3
- **Files modified:** 14

## Accomplishments

- `run_clue_worker` claims leases, freezes hierarchy/timeline lineage, reserves budget before model calls, checkpoints completed stage keys, qualifies manifests, promotes via CAS only.
- Unknown pricing / budget exhaustion pauses with zero pointer movement; restart skips completed stages (cache_hit audits).
- `append_lifecycle_event` + human confirm/reject/annotate/adjust_link are append-only; reanalysis relinks on exactly one evidence identity match.
- `build_clue_version_view` filters by Phase 08 cutoff before state/counts/filters/links; machine paid_off projects as earlier visible state pre-payoff.
- Registered `/api/clues` routes: start/resume/cancel/reanalyze, list/version/detail, compare/rollback, human actions.

## Task Commits

1. **Tasks 1–3: worker/versions/budget + lifecycle/overrides + query/API** - `464f65c` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP docs commit follows)

## Files Created/Modified

- `backend/app/services/clues/budget.py` — ClueCallRepository + BudgetGate reuse
- `backend/app/services/clues/versions.py` — snapshot_manifest, promote/rollback CAS
- `backend/app/services/clues/worker.py` — `run_clue_worker` / `dispatch_clue_run`
- `backend/app/services/clues/lifecycle.py` — `append_lifecycle_event` + derived replay
- `backend/app/services/clues/overrides.py` — human actions + pure relink adapter
- `backend/app/services/clues/query.py` — visible-set-first projection
- `backend/app/api/clues.py` — owner-scoped API
- `backend/app/main.py` — router registration
- unit + integration tests listed in frontmatter

## Decisions Made

- Lifecycle evidence must not re-attach `machine_clue_id` when machine rows already hold the same identity (SQLite/PG unique collision was aborting the whole stage transaction).
- Override supersession is pure INSERT; query uses latest-wins heads.
- Chat remains non-source; no clue full-book preference endpoint.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule — Integrity / transaction safety] Lifecycle evidence dual-parent**
- **Found during:** Task 1 worker integration
- **Issue:** Machine + lifecycle evidence both used `machine_clue_id` + same identity → unique violation → session aborted → zero machine clues after "successful" stage.
- **Fix:** Lifecycle evidence attaches only to `lifecycle_event_id`.
- **Files modified:** `backend/app/services/clues/lifecycle.py`
- **Verification:** `test_worker_promotes_version_and_restart_is_idempotent` passed

---

**Total deviations:** 1 auto-fixed  
**Impact on plan:** Required for correctness; no scope creep.

## Issues Encountered

- SQLite greenlet expiry in tests when holding ORM attributes across commits; tests capture scalar IDs early.

## Commands and Test Results

```text
.\.venv\Scripts\python.exe -m pytest tests/unit/clues/test_overrides.py tests/unit/clues/test_query_projection.py -q -x
# 7 passed

.\.venv\Scripts\python.exe -m pytest tests/integration/clues/test_worker_versions.py tests/integration/clues/test_override_reanalysis.py tests/integration/clues/test_spoiler_api.py -q
# 8 passed

# Combined 11-03 suite:
# 15 passed (7 unit + 8 integration), 0 skip
```

## Verification Mapping

| Must-have | Evidence |
|---|---|
| Budget reservation before model call | `ClueCallRepository.reserve_and_start` + unknown pricing test |
| Active pointer only after qualification | `promote_version` CAS + worker complete path |
| Human actions append-only, survive reanalysis | override unit + override_reanalysis integration |
| Spoiler filtering precedes derived fields | query projection unit + spoiler API integration |
| No clue-specific full-book preference | API has no full-book write route; reuses `timeline_full_book` |

## Self-Check: PASSED

## Next

- Execute `11-04-PLAN.md` (analysis workspace clue UI).
