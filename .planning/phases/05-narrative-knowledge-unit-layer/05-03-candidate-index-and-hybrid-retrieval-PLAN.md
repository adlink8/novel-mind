---
phase: 05-narrative-knowledge-unit-layer
plan: 05-03-candidate-index-and-hybrid-retrieval
type: implementation
wave: 3
depends_on: [05-02-canonicalization-and-lifecycle-gates]
files_modified:
  - backend/app/services/knowledge_units/indexing.py
  - backend/app/services/knowledge_units/search.py
  - backend/app/services/vector_store.py
  - backend/app/services/hybrid_search.py
  - backend/app/api/search.py
  - backend/app/schemas/search.py
  - backend/scripts/build_narrative_unit_index.py
  - backend/tests/test_knowledge_unit_indexing.py
  - backend/tests/test_knowledge_unit_search.py
autonomous: true
requirements_addressed: [REQ-NU-04, REQ-NU-05]
truths:
  - "D-03: Chroma is an immutable replayable projection."
  - "D-06: chunks, units, and hybrid remain explicit retrieval modes with raw fallback."
---

# 05-03 - Candidate Index and Hybrid Retrieval

## Objective

Build immutable candidate collections from canonical units and expose owner-safe units/chunks/hybrid search without changing the active default.

## Steps

1. Refactor Chroma client acquisition behind a lazy service boundary so imports and deterministic tests do not require a healthy Chroma server. Preserve existing chunk collection behavior.
2. Build immutable candidate collections named from build/checksum. Index stable canonical IDs, QA/claim text, lifecycle, domain, novel, evidence refs, and build metadata; never mutate an active collection in place.
3. Read actual collection IDs after indexing and reconcile them against the PostgreSQL manifest. Any missing, orphan, duplicate, wrong-owner, deleted, or deprecated residue fails the build.
4. Add a narrative-unit retrieval strategy and a fusion layer for `chunks`, `units`, and `hybrid`. Keep configurable per-source top-k/weights and deduplicate by evidence lineage.
5. Enrich unit results with source chunks for citations. Enforce owner/novel scope before and after vector retrieval.
6. Extend API schemas with an explicit retrieval mode while preserving current default and response compatibility. Candidate collections are addressable only by evaluation/admin code, not ordinary user-selected IDs.
7. Add fake-store tests plus a real Chroma smoke test marker for collection immutability, reconcile, mode behavior, fallback, citation enrichment, and owner isolation.
8. Test, Fix, and Confirm: verify existing chunk-search regression tests alongside new modes.

## Must-Haves

- Index build success does not update the active pointer.
- Existing chunk mode and collections remain unchanged.
- Hybrid search reports source type and preserves citations.
- Real or fake Chroma outage yields a blocked/error state, never a fabricated successful build.
- Covers D-03, D-06 and REQ-NU-04/05.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_unit_indexing.py tests/test_knowledge_unit_search.py tests/test_hybrid_search.py -v
python scripts/build_narrative_unit_index.py --build-id TEST --dry-run
ruff check app/services/knowledge_units/indexing.py app/services/knowledge_units/search.py app/services/vector_store.py app/services/hybrid_search.py app/api/search.py
```

