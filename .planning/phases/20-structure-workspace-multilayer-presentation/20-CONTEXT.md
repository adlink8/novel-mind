# Phase 20 Context — Structure Workspace & Multi-layer Presentation

## Why this phase

Phase 19 made the three analysis facets **honest** (timeline lanes, edge_kind, plant→payoff).  
Product UX is still **single-layer**: three parallel tabs on chapter-scoped pipelines. Users still experience “three similar summaries,” not multi-chapter multi-layer structure.

Research (multi-agent, 2026-07-16) locked the product model:

- **Compute bottom-up**, **present top-down**
- **Structure tree is the spine**; timeline / relationships / clues are **facets** mounted on a selected structure node
- **Do not reinvent** Chapter→Arc→Global — reuse Narrative Memory (Phase 13–17) as the L2–L4 skeleton
- Phase 07 hierarchy remains **L0/L1 structural coordinates** (evidence/scene), not semantic arcs

## Milestone framing

- Closes the product gap after v0.9 Phase 19 honesty
- Delivers **P0 Structure Workspace** + **read-only NM candidate surface**
- Does **not** promote narrative memory to production active pointer
- Does **not** reopen Phase 08/09/11 extraction algorithms

## Locked decisions (auto-discuss from research table)

| ID | Decision | Locked default |
|----|----------|----------------|
| D1 | Facet vs layer | Structure L0–L4 is the only layer spine; timeline/rel/clue are facets |
| D2 | Compute order | Hierarchy → (timeline ∥ rel ∥ clue) → NM chapter_state → arc/volume → global |
| D3 | Present order | Default land on highest available structure node (L4 if any, else chapter list); drill down |
| D4 | NM product role | Read-only candidate preview; always badge “预览·未发布”; no active pointer |
| D5 | No NM data | Structure shell uses chapter list (+ optional scene expand); honest empty for L3/L4 |
| D6 | Facet scope | Selected node’s `chapter_start..chapter_end` filters existing facet APIs (no new workers) |
| D7 | Arc boundary | Reuse existing `arc_planner` (volume metadata or deterministic window); no UI edit this phase |
| D8 | Promote / rebuild | **Forbidden** this phase (same as Phase 19 authorization boundary) |
| D9 | GraphRAG / Neo4j | Forbidden as second truth; PostgreSQL authority only |
| D10 | Route | Keep `/analysis`; product name Structure Workspace |
| D11 | Old plot/theme menus | Do not resurrect intermediate summary menus |
| D12 | Reader Chat cutover | Forbidden |

## Layer map (product)

```
L4 global_story     NM node_kind
L3 story_arc|volume NM node_kind
L2 chapter_state    NM node_kind
L1 scene            Phase 07 hierarchy scene (+ facet atoms)
L0 evidence         Phase 07 hierarchy evidence (+ source_links)
```

## Non-goals

- Narrative-memory promotion / active pointer
- Replacing timeline/relationship/clue production versions with NM-derived facts
- New LLM extraction quality pass for facets
- Full-book automatic NM build orchestration in UI (CLI/audit remains ops path; optional thin status only if already present)
- Reader Chat / hierarchical retrieval production cutover
- Arc boundary manual editor
- Changing `ChunkHierarchyNode.level` enum

## Depends on

- Phase 07 hierarchy (evidence coordinates)
- Phase 08/09/11 facet APIs (consume + scope filter)
- Phase 12–17 narrative_memory candidate authority + `candidate_reader`
- Phase 19 honesty presentation contracts

## Success (product, UAT-oriented)

1. Opening `/analysis` makes **structure** the primary axis (not three equal summary walls)
2. Facet tabs change content when structure selection changes chapter range
3. When NM candidate exists: tree L4→L3→L2 visible with “预览·未发布”
4. When NM absent: chapter structure still works; L3/L4 empty state is honest
5. Claim/node drill reaches leaf evidence ids or existing facet evidence paths within 3 clicks where data exists
6. No path creates/promotes NM active pointer
7. Spoiler: `through_chapter` filters structure nodes and continues to filter facets server-side
8. Phase 19 honesty badges remain on facet views

## Authorization (this user turn)

- User: GSD discuss/plan Phase 20 → subagent execute → test → docs → report
- Scope: Phase 20 plans 01–04 (Structure Workspace P0 + NM read-only)
- Does **not** authorize narrative-memory promotion
