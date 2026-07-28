---
phase: 09-dynamic-character-relationship-graph
plan: 03
subsystem: api
tags: [relationship-graph, spoiler, version-isolation, fold, overrides, neo4j-projection, fastapi, postgresql]
requires:
  - phase: 09-dynamic-character-relationship-graph
    provides: append-only observation authority and pipeline accepted writers
  - phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
    provides: reading_progress.timeline_full_book, TimelineActivePointer, version lineage
provides:
  - Owner/version/spoiler-scoped RelationshipGraphQueryService with narrative fold
  - Append-only character-merge and relationship field overrides with unique-signature relink
  - Replayable one-way projection manifest + audit (Neo4j optional, non-authoritative)
  - Authenticated /api/relationships graph, evidence, override, and projection routes
  - Phase 10 load_filtered_relationship_graph and Phase 11 list_accepted_observation_refs contracts
affects: [09-04, 09-05, 10-reader-ai, 11-clue-tracking]
tech-stack:
  added: []
  patterns:
    - visible-set-first fold then derive nodes/filters/counts/evidence
    - append-only override supersession via INSERT + supersedes_id (no prior UPDATE)
    - projection audit only; observation status immutable under adapter failure
key-files:
  created:
    - backend/app/services/relationships/query.py
    - backend/app/services/relationships/overrides.py
    - backend/app/services/relationships/projection.py
    - backend/app/api/relationships.py
    - backend/tests/integration/relationships/test_api.py
    - backend/tests/integration/relationships/test_projection.py
  modified:
    - backend/app/main.py
    - backend/app/services/relationships/__init__.py
key-decisions:
  - "Cutoff reuses only Phase 08 timeline_full_book; missing progress defaults to chapter one."
  - "Latest-wins overrides treat highest id per logical key/field as active without mutating prior rows."
  - "Hard cap responses empty nodes/edges with filters_required while preserving spoiler-safe counts."
  - "Phase 10/11 get documented read-only service functions only; no chat/clue tables or routes."
patterns-established:
  - "require_owned_novel + server-proven version; client never supplies owner_id."
  - "CharacterRelation is never read for graph truth."
requirements-completed: [REQ-REL-02, REQ-REL-03, REQ-REL-05, REQ-REL-06]
duration: 55min
completed: 2026-07-15
---

# Phase 09 Plan 03: Graph Read Model, Overrides, and Projection Summary

**Owner/version/spoiler graph API with narrative fold, append-only protective overrides, and deterministic non-authoritative Neo4j projection replay over PostgreSQL accepted observations.**

## Performance

- **Duration:** 55 min
- **Started:** 2026-07-15T02:00:00Z
- **Completed:** 2026-07-15T02:55:00Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments

- Built `RelationshipGraphQueryService` that proves active/running_candidate/history versions inside owner/novel scope, folds transition chains at a narrative position, applies eligible overrides, and derives every response field from one visible set.
- Enforced D-09/D-10 cutoffs (missing progress → chapter one; full-book only via persisted `timeline_full_book`) and D-22 degradation (`normal` / `large` / `filters_required` with empty elements over hard caps).
- Delivered append-only character-merge and relationship overrides with supersession INSERT semantics and unique evidence-signature relink (`needs_relink` on 0 or >1 matches).
- Added optional projection manifest/replay that writes only `RelationshipProjectionAudit`; adapter failure cannot mutate accepted observations.
- Mounted authenticated `/api/relationships/{novel_id}/…` routes; exposed Phase 10/11 read-only service contracts without product tables/routes.

## Task Commits

1. **Tasks 1–3: query, overrides, projection, API, integration suite** - `4888ffc` (feat)

**Plan metadata:** (this SUMMARY commit follows)

## Files Created/Modified

- `backend/app/services/relationships/query.py` — version proof, cutoff, fold, degradation, Phase 10/11 readers
- `backend/app/services/relationships/overrides.py` — append-only merge/field overrides and relink
- `backend/app/services/relationships/projection.py` — deterministic manifest + optional adapter replay
- `backend/app/api/relationships.py` — graph, evidence, overrides, projection endpoints
- `backend/app/main.py` — mount single relationship router at `/api/relationships`
- `backend/app/services/relationships/__init__.py` — export query/override/projection surfaces
- `backend/tests/integration/relationships/test_api.py` — spoiler, version, fold, override, HTTP 404 proofs
- `backend/tests/integration/relationships/test_projection.py` — checksum stability and failure isolation

## Decisions Made

- Active version resolution prefers `TimelineActivePointer`, then `AnalysisVersion.status=active`; running candidate uses non-completed `AnalysisRun`; history requires explicit owned `version_id`.
- Override supersession never updates prior rows (physical append-only triggers); query applies latest-id wins for status=`active`.
- Projection default is disabled; enabled without driver records failed audit only.

## Deviations from Plan

None - plan executed exactly as written within declared deliverables.

## Issues Encountered

None.

## Commands and Test Results

```text
cd backend

.\.venv\Scripts\python.exe -m pytest tests/integration/relationships/test_api.py -k "query or spoiler or version or fold" -q
# 3 passed, 2 deselected

.\.venv\Scripts\python.exe -m pytest tests/integration/relationships/test_api.py tests/integration/relationships/test_projection.py -q
# 10 passed, 0 skipped

.\.venv\Scripts\python.exe -c "from app.main import app; assert '/api/relationships/{novel_id}/graph' in app.openapi()['paths']"
# openapi_ok
```

Coverage highlights:

- Spoiler: future names/labels/evidence absent; legacy `CharacterRelation` does not change response.
- Version isolation: active vs running candidate never merge; cross-owner/cross-version → 404.
- Fold: chapter-1 ally vs chapter-2 enemy without mutating observation rows.
- Over-cap: empty nodes/edges, `degradation.mode=filters_required`, spoiler-safe counts retained.
- Overrides: prior row byte-identical after supersession; ambiguous relink → `needs_relink`.
- Projection: identical manifest checksums; boom adapter leaves observation status `accepted`.

### Endpoint schema baseline (for Plan 05)

OpenAPI paths registered:

- `GET /api/relationships/{novel_id}/graph`
- `GET /api/relationships/{novel_id}/observations/{observation_id}/evidence`
- `POST /api/relationships/{novel_id}/overrides/character-merge`
- `POST /api/relationships/{novel_id}/overrides/relationship`
- `POST /api/relationships/{novel_id}/overrides/relationship/{override_id}/relink`
- `GET /api/relationships/{novel_id}/projection/manifest`
- `POST /api/relationships/{novel_id}/projection/replay`

Envelope fields include `version_id`, `source`, `through_chapter`, `cutoff_chapter`, `full_book`, `nodes`, `edges`, `counts`, `available_*`, `degradation`, edge `provenance`.

## Self-Check: PASSED

- All plan `files_modified` exist on disk.
- Production commit `4888ffc` present with `feat(09-03)` message.
- Targeted suite: **10 passed**, 0 skipped.
- OpenAPI assertion for `/api/relationships/{novel_id}/graph` passes.
- No `CharacterRelation` import in query service; no client `owner_id` on routes.
- Response envelope carries version, cutoff, provenance, degradation.

## Next Phase Readiness

- Ready for **09-04**: Cytoscape analysis workspace consuming the graph envelope, evidence panel, timeline linking, and client large-graph degradation.
- Phase 10 can depend on `load_filtered_relationship_graph`; Phase 11 on `list_accepted_observation_refs`.

---
*Phase: 09-dynamic-character-relationship-graph*
*Completed: 2026-07-15*
