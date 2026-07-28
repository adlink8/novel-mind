# 15-02 Summary: Multi-level Descent and Revalidated Leaf Citations

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-RETR-02, V08-RETR-03, V08-RETR-04

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/services/narrative_memory/descent.py` | Bounded local/arc/global/mixed descent, collapse, raw fallback, leaf dedupe |
| `backend/app/services/narrative_memory/citations.py` | Fresh Chapter Unicode re-slice + lineage validation → `LeafCitation` only |
| `backend/app/services/narrative_memory/retrieval_manifests.py` | Canonical retrieval manifest + safe trace builders |
| `backend/tests/unit/narrative_memory/test_retrieval_descent.py` | Route path / fallback / dedupe matrix (6) |
| `backend/tests/unit/narrative_memory/test_retrieval_manifests.py` | Manifest determinism + leak-free (4) |
| `backend/tests/integration/narrative_memory/test_retrieval_leaf_pg.py` | Unicode re-slice, tamper reject, end-to-end local path (3) |

## Traversal paths

```text
local  → chapter_state → claim → source_link → (re-slice)
arc    → story_arc|volume → contains → chapter_state → claim → source_link
global → global_story → arc/volume → chapter_state → claim → source_link
mixed  → bounded union(local chapters ∪ upper→chapter) → claim → source_link
         ↓ on empty/invalid
         Phase 07 evidence leaves under same build+cutoff (raw_fallback)
```

Every expansion reuses the same immutable `RetrievalScope`. Budgets: max_depth/fanout/nodes/claims/leaves.

## Fallback matrix

| Condition | Code | Behavior |
| --- | --- | --- |
| Start level empty | `upper_absent` | Collapse to next visible level |
| Parent has no visible child | `no_visible_child` / `upper_partial` | Collapse or raw |
| Invalid proposed leaves | `invalid_leaf` | Drop; raw if none remain |
| Depth/fanout/leaf cap | `budget_exhausted` | Omit after budget only |
| No memory leaf remains | `raw_fallback` | Phase 07 evidence under same scope |
| Empty after raw | `no_answer` | Safe empty/blocked |

## Citation proof fields

Reload: version+seal+completed build run, source link (if any), `ChunkHierarchyNode(level=evidence)`, `Chapter.content`.  
Require: scope match, offsets, `Chapter.content[start:end] == evidence.content`, `content_hash(excerpt) == link/leaf hash`.  
Reject: wrong owner/version/build/snapshot/chapter/offset/hash, non-evidence nodes, future chapters.  
Summaries/scores/chat text cannot construct `LeafCitation`.

## Manifest

`narrative-memory-retrieval-manifest.v1` checksum over scope/query/policy hashes, route, traversal, citations, fallback, omitted_after_budget, run_status. No display labels, cache keys, raw questions, or hidden-future counts.

## Verification

```text
pytest tests/unit/narrative_memory/test_retrieval_descent.py \
       tests/unit/narrative_memory/test_retrieval_manifests.py \
       tests/integration/narrative_memory/test_retrieval_leaf_pg.py -q
# 13 passed
ruff clean
```
