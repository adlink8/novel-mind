---
phase: 04-llm
plan: 04-03-evidence-gates-and-projection
subsystem: api
tags: [knowledge-graph, evidence-gates, fastapi, sqlalchemy, projection, neo4j]
requires:
  - phase: 04-02-candidate-packages-and-llm-judgment
    provides: Evidence-bounded relation candidates and LLM judgment audit rows
provides:
  - Deterministic schema/evidence/threshold/conflict gates for relation judgments
  - Accepted PostgreSQL judgment rows as source-of-truth projection input
  - Idempotent projection into CharacterRelation and TimelineEvent when entity/event resolution is sufficient
  - Optional Neo4j sync boundary that is disabled by default and reads accepted PostgreSQL rows only
  - Knowledge API for runs, candidates, judgments, review actions, gating, and accepted graph neighborhood queries
affects: [04-04-evaluation-and-domain-fixtures, knowledge-api, graph-projection]
tech-stack:
  added: []
  patterns:
    - Deterministic gate service owns accepted/rejected/review state transitions after LLM judgment
    - Projection services read only accepted PostgreSQL judgments and use judgment markers for idempotency
    - API endpoints enforce require_user and novel owner isolation before exposing graph data
key-files:
  created:
    - backend/app/services/knowledge/gates.py
    - backend/app/services/knowledge/projection.py
    - backend/app/services/knowledge/graph_sync.py
    - backend/app/api/knowledge.py
    - backend/tests/test_knowledge_gates.py
    - backend/tests/test_knowledge_projection.py
    - backend/tests/test_knowledge_api.py
  modified:
    - backend/app/main.py
    - backend/app/schemas/knowledge.py
    - backend/app/services/knowledge/__init__.py
key-decisions:
  - "Used KnowledgeRelationJudgment(status='accepted', gate_status='accepted') as the PostgreSQL accepted row because this plan did not add a new accepted-edge table or migration."
  - "Projection skips text_chunk-only relation candidates until entity/event resolution is sufficient, so recall signals cannot become graph facts."
  - "Neo4j sync is an optional disabled-by-default boundary; failed or skipped sync never changes PostgreSQL accepted state."
patterns-established:
  - "Gate routing: invalid schema/evidence rejects; low confidence, risk flags, LLM review requests, and conflicts enter KnowledgeReviewQueue."
  - "Projection idempotency: CharacterRelation and TimelineEvent rows carry kg_judgment_id or kg_event_candidate_id markers."
  - "Owner isolation: every knowledge API resource lookup joins or checks the owning Novel before returning data."
requirements-addressed: [REQ-KG-02, REQ-KG-03, REQ-KG-05]
duration: 16min
completed: 2026-07-02
---

# Phase 04 Plan 03: Evidence Gates and Projection Summary

**Deterministic evidence gates and accepted-judgment projection with owner-isolated FastAPI endpoints and a disabled-by-default Neo4j sync boundary.**

## Performance

- **Duration:** 16 min
- **Started:** 2026-07-02T15:29:20Z
- **Completed:** 2026-07-02T15:45:42Z
- **Tasks:** 1 implementation slice
- **Files modified:** 10

## Accomplishments

- Added `KnowledgeGateService` for schema, evidence, threshold, and conflict gates; accepted facts require in-package, same-owner evidence refs.
- Added review routing for low-confidence, risk-flagged, LLM-review, and conflicting judgments, with explicit accept/reject transitions.
- Added idempotent projection from accepted judgments into `CharacterRelation` for resolved fiction entities and `TimelineEvent` for resolved history events.
- Added optional `KnowledgeGraphSyncService`; the default disabled path is testable and reads accepted PostgreSQL rows without mutating them.
- Added `/api/knowledge` endpoints for run creation/listing, candidate/judgment/review listing, run gating, manual review actions, and accepted graph neighborhood queries.

## Task Commits

1. **Task 1: Evidence gates and graph projection** - `19900b7` (feat)

## Files Created/Modified

- `backend/app/services/knowledge/gates.py` - Deterministic gate evaluation, persisted routing, review queue creation, and run counters.
- `backend/app/services/knowledge/projection.py` - Accepted-judgment replay into existing character relation and timeline tables.
- `backend/app/services/knowledge/graph_sync.py` - Optional Neo4j sync boundary with disabled/default no-op behavior.
- `backend/app/api/knowledge.py` - Authenticated owner-isolated knowledge graph API.
- `backend/app/schemas/knowledge.py` - API-specific run start and review action request schemas.
- `backend/app/services/knowledge/__init__.py` - Exports gate, projection, and sync services.
- `backend/app/main.py` - Registers the knowledge API router.
- `backend/tests/test_knowledge_gates.py` - Gate acceptance/rejection/review/conflict/cross-owner tests.
- `backend/tests/test_knowledge_projection.py` - Projection idempotency and Neo4j disabled-path tests.
- `backend/tests/test_knowledge_api.py` - API authentication, review flow, and endpoint owner-isolation tests.

## Decisions Made

- Accepted graph source-of-truth is represented by accepted `KnowledgeRelationJudgment` rows, not a new table, because 04-03 did not include a migration.
- Text chunk relation candidates can pass gates but are not projected into final relation/timeline tables until entity or event candidate endpoints exist.
- Existing `CharacterRelation.description` and `TimelineEvent.event_description` store replay markers and evidence refs because those legacy tables do not have structured evidence metadata columns.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Scoped projection evidence lookup to owner/novel/run and first matching ref**
- **Found during:** Task 1 implementation.
- **Issue:** The first version of projection chapter lookup used only `ref_key`, which could cross wires across runs with the same evidence key and could raise if a judgment cited multiple evidence refs.
- **Fix:** Added owner, novel, and run filters and `limit(1)` to the projection evidence lookup.
- **Files modified:** `backend/app/services/knowledge/projection.py`
- **Verification:** `tests/test_knowledge_projection.py` and the full knowledge pytest set passed.
- **Committed in:** `19900b7`

**Total deviations:** 1 auto-fixed bug.
**Impact on plan:** Correctness fix inside the planned projection scope; no scope expansion.

## Issues Encountered

- The worktree had substantial pre-existing uncommitted changes, including an eval router change in `backend/app/main.py`. The 04-03 task commit staged only the knowledge router lines and left unrelated diffs untouched.

## Verification

| Command | Result |
|---|---|
| `.\.venv\Scripts\python.exe -m py_compile app/services/knowledge/gates.py app/services/knowledge/projection.py app/services/knowledge/graph_sync.py app/api/knowledge.py app/schemas/knowledge.py tests/test_knowledge_gates.py tests/test_knowledge_projection.py tests/test_knowledge_api.py` | Passed |
| `.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_gates.py tests/test_knowledge_projection.py tests/test_knowledge_api.py -v` | Passed, 12 tests |
| `.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py tests/test_knowledge_candidates.py tests/test_knowledge_llm_judge.py tests/test_knowledge_gates.py tests/test_knowledge_projection.py tests/test_knowledge_api.py -v` | Passed, 29 tests |
| `.\.venv\Scripts\python.exe -m ruff check app/services/knowledge/gates.py app/services/knowledge/projection.py app/services/knowledge/graph_sync.py app/api/knowledge.py app/schemas/knowledge.py tests/test_knowledge_gates.py tests/test_knowledge_projection.py tests/test_knowledge_api.py` | Passed |
| `rg -n 'TODO\|FIXME\|placeholder\|coming soon\|not available' ...` | No product-code matches |

## Known Stubs

None. Empty dict/list matches were test fixtures for persisted LLM output and candidate aliases, not product stubs.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: api-surface | `backend/app/api/knowledge.py` | New authenticated knowledge graph endpoints expose run, judgment, review, and accepted graph data; mitigated with `require_user`, Novel owner checks, and API owner-isolation regression tests. |
| threat_flag: graph-projection | `backend/app/services/knowledge/projection.py` | Accepted judgments are replayed into existing graph-facing tables; mitigated by requiring accepted gate status and skipping unresolved text-chunk endpoints. |

## Auth Gates

None.

## User Setup Required

None for default operation. Neo4j sync remains disabled by default; enabling it later will require explicit Neo4j configuration and driver wiring.

## Remaining Risks

- Live PostgreSQL and Neo4j end-to-end projection were not verified; tests used the existing SQLite async fixture.
- Evidence metadata in projected legacy graph tables is marker-based text because no migration added structured evidence columns to `CharacterRelation` or `TimelineEvent`.
- Existing pre-04-03 dirty worktree changes remain outside this plan and were not altered.

## Next Phase Readiness

04-04 can build fixture/eval coverage against deterministic gate outcomes, review rates, projection idempotency, and owner isolation. It should also decide whether a dedicated accepted-edge table or structured evidence metadata columns are needed before production graph querying.

---
*Phase: 04-llm*
*Completed: 2026-07-02*

## Self-Check: PASSED

- Found SUMMARY file.
- Found created gate, projection, graph sync, API, and test files.
- Found implementation commit `19900b7`.
