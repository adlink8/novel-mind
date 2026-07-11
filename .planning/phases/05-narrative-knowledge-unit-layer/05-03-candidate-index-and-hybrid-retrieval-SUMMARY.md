---
phase: 05-narrative-knowledge-unit-layer
plan: 05-03-candidate-index-and-hybrid-retrieval
subsystem: narrative-retrieval
tags: [chroma, candidate-index, hybrid-search, owner-isolation]
key-files:
  - backend/app/services/knowledge_units/indexing.py
  - backend/app/services/knowledge_units/search.py
  - backend/app/services/vector_store.py
metrics:
  targeted_tests: 31
  ruff_errors: 0
status: complete
completed: 2026-07-11
---

# Phase 05 Plan 03 Summary

Added lazy Chroma client acquisition, immutable checksum-named candidate collections, actual-ID reconcile, owner-scoped unit retrieval, citation enrichment, and explicit chunks/units/hybrid API modes while preserving chunks as the default.

## Commits

| Commit | Description |
|---|---|
| `6078215` | Add immutable narrative candidate indexing and retrieval modes |

## Verification

- `pytest tests/test_knowledge_unit_indexing.py tests/test_knowledge_unit_search.py tests/test_hybrid_search.py -q`: 31 passed after the global-units coverage addition.
- Ruff over all changed Plan 03 files: passed.
- Fake Chroma verifies exact reuse and reconcile without requiring a live service.
- Existing hybrid search suite remains green; default request mode is `chunks`.

## Deviations

- The pre-existing `hybrid_search.py` SQLAlchemy rewrite was preserved and not included in this plan commit. Fusion for narrative units lives at the new strategy boundary.
- Live Chroma smoke remains blocked by the unhealthy local Chroma HTTP service and is retained for phase verification.

## Self-Check: PASSED

- Candidate build does not change active pointers.
- Candidate collection names are derived from build key and manifest checksum.
- Owner and novel scope are checked before and after vector retrieval.
- Ordinary API callers cannot supply collection names or candidate IDs.
