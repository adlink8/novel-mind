---
phase: 14-durable-bottom-up-candidate-builder
status: passed
verified_at: 2026-07-16
requirements: [V08-BUILD-01, V08-BUILD-02, V08-BUILD-03, V08-BUILD-04, V08-BUILD-05]
---

# Phase 14 Verification

**status:** `passed`

## Must-Haves vs Requirements

| ID | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| V08-BUILD-01 | Durable bottom-up Chapter→Arc→Global candidate builder with explicit version only | **pass** | Worker stages + packages; no active pointer |
| V08-BUILD-02 | Phase 12 eligibility + budget reservation + exact cache + cancel/resume | **pass** | Gateway/budget PG tests; unknown price zero transport |
| V08-BUILD-03 | Chapter failure isolates siblings; blocked parents only | **pass** | `test_chapter_failure_isolates_siblings`, `test_arc_worker_pg`, `test_builder_failure_isolation_pg` |
| V08-BUILD-04 | Optional timeline/relationship/clue enrichment with lineage statuses | **pass** | `optional_sources.py` + unit/PG tests |
| V08-BUILD-05 | No Reader Chat; no promotion/pointer; report is structural not Phase 17 quality | **pass** | static scans, CI contract, report outcomes |

## Plan Completion

| Plan | Status | Summary |
| --- | --- | --- |
| 14-01 | complete | Control plane + Chapter State |
| 14-02 | complete | Arc/Volume planner + parent stages |
| 14-03 | complete | Global + manifest + report |
| 14-04 | complete | Optional sources + CLI + isolation |

## Commands and Results

```text
pytest tests/unit/narrative_memory/test_builder_*.py \
       tests/unit/narrative_memory/test_arc_*.py \
       tests/unit/narrative_memory/test_global_packages.py \
       tests/unit/narrative_memory/test_optional_sources.py \
       tests/integration/narrative_memory/test_builder_*.py \
       tests/integration/narrative_memory/test_chapter_state_worker_pg.py \
       tests/integration/narrative_memory/test_arc_worker_pg.py \
       tests/integration/narrative_memory/test_global_worker_pg.py \
       tests/integration/narrative_memory/test_optional_sources_pg.py \
       tests/ci/test_narrative_memory_builder_contract.py -q
→ 37 passed, 0 failed, 0 skip

ruff check (Phase 14 production + test paths)
→ All checks passed

python -m alembic heads
→ 14membuild01 (head)
```

## Candidate-Only Boundary

- Builder tables are execution/audit only; candidate content remains Phase 13 tables.
- Absent: narrative-memory active pointer, promote/rollback CLI, Reader Chat imports.
- Fresh observer: chunk_active_pointers count unchanged after controlled build.

## Residual Risks

- Full-graph structural seal (Phase 13 provenance) for multi-window arcs depends on complete parent/global node graphs; partial runs intentionally leave Global blocked/unsealed.
- Production CLI transport is noop by default; operator dry-runs in CI inject controlled transport via worker tests.
- Host `alembic check` against non-test DB may report not-up-to-date until operators upgrade that environment separately (migration itself is single-head and round-trips in PG tests).
