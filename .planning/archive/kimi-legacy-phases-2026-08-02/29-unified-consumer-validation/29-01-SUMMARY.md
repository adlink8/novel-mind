# Phase 29-01 Summary: Real Workspace and Fallback

## Status

PARTIAL — the formal candidate structure and read-only consumer contracts are verified; a real browser session authenticated as owner 2 is still required for the final `/analysis?novel=91` consumer proof.

## Formal PostgreSQL evidence

- Narrative Memory version 1 for novel 91 is `sealed_candidate` with `candidate_preview` publication status and validation verdict `qualified_candidate`.
- Counts: 515 `chapter_state`, 172 `story_arc`, 1 `global_story`; 2,538 claims and 2,835 source links.
- `through_chapter=50` returned 66 visible nodes with no node beyond chapter 50; an Arc node returned 3 claims and 3 leaf source links, all no later than chapter 3.
- Node count was unchanged before/after the query, proving the consumer path is read-only.

## Verification

- `pytest tests/unit/narrative_memory/test_structure_query.py tests/test_search_router_fallback.py tests/contract/test_facet_readonly_contract.py -q` — 55 passed.
- Frontend analysis/structure/view-switch tests — 39 passed.

## Remaining

The API enforces owner-scoped access. Final browser acceptance needs an authenticated owner-2 session or an equivalent authorized formal-DB browser harness; no ownership or pointer mutation was performed to bypass that boundary.
