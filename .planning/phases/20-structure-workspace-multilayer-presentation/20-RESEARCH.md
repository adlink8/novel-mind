> Layer numbering superseded by docs/adr/0001-layer-registry.md

# Phase 20 Research — Structure Workspace

## Summary

Three research agents (external, codebase, product design) agreed:

1. Industry: bottom-up build + top-down present (RAPTOR, GraphRAG TextUnit, Minto, narrative beat/arc)
2. Codebase: only Narrative Memory implements real L1→L2→L3 aggregation; `/analysis` is three parallel facets
3. Design: Structure Workspace + facet mount; P0 shell without promote

## Code anchors

| Asset | Path | Reuse |
|-------|------|-------|
| Candidate loaders | `backend/app/services/narrative_memory/candidate_reader.py` | nodes/claims/edges with through_chapter |
| Authority (read-only) | `backend/app/services/narrative_memory/authority.py` | do not mutate for product API |
| Descent | `backend/app/services/narrative_memory/descent.py` | optional claim→leaf; not required for P0 if source_links listed |
| ORM | `backend/app/models/narrative_memory.py` | versions/nodes/claims/edges/source_links |
| Analysis page | `frontend/src/app/analysis/page.tsx` | today `timeline \| relationships \| clues` only |
| Facet APIs | `/api/timeline`, `/api/relationships`, `/api/clues` | scope via chapter / through_chapter |

## Gaps

- No product HTTP surface for NM
- No structure tree UI
- Facet workers not bottom-up parents of each other
- Sample novels often have zero NM rows

## Approach

Minimal path:

1. Read-only API over explicit `version_id` (never default/current active)
2. FE structure panel: chapters always; NM tree when versions exist
3. Pass `chapter_start/end` into facet panels as filters
4. Always badge candidate preview

## Risks

- Strict `load_eligible_version` may reject incomplete candidates → product list should show versions with `readiness` enum; tree may use softer owner-scoped node query for preview with honesty flag
- Large timeline event counts at L4 → server or client top-k / range filter only (do not dump 1933 events unscoped)
