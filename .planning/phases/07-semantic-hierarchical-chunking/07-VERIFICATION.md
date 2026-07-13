---
phase: 07-semantic-hierarchical-chunking
verified: 2026-07-13
status: passed_with_residuals
score: "8/8 requirements implemented; 2 residuals on production PG wiring"
---

# Phase 07 Verification — Semantic Hierarchical Chunking

**Goal:** 规则初切 + 低置信 LLM 裁决 + chapter→scene→evidence 层级 + 不可变 candidate 生命周期 + Phase 06 同源 A/B 资格门。

**Verified:** 2026-07-13 (re-verified after PG/indexing wiring)  
**Branch:** `feat/phase2-wave2-embedding`  
**Automated evidence:** 91+ related tests passed (includes `test_pg_hierarchy_wiring`)

```text
pytest tests/unit/chunking tests/integration/chunking \
  tests/integration/test_hierarchical_retrieval.py \
  tests/adversarial/test_chunking_prompt_boundary.py \
  tests/test_chunking.py -q
# 91 passed (includes PG wiring)
alembic upgrade head  # 09chunkhier01
```

## Requirement scorecard

| ID | Status | Evidence |
|---|---|---|
| REQ-CHUNK-01 | VERIFIED | `chunking/schemas|manifests|baseline`; unit/integration offset + deterministic checksum tests |
| REQ-CHUNK-02 | VERIFIED | `rules.py` + `segmentation.py`; proposal/segmentation unit tests |
| REQ-CHUNK-03 | VERIFIED | `adjudicator.py` strict `BoundaryDecision`; schema + fake-LLM tests |
| REQ-CHUNK-04 | VERIFIED | `hierarchy.py` + PG `chunk_hierarchy_nodes` + `hybrid_search` scene expand / raw fallback |
| REQ-CHUNK-05 | VERIFIED | immutable builds; PG `chunk_builds` + `chunk_active_pointers`; index path promotes hierarchy after raw index |
| REQ-CHUNK-06 | VERIFIED | `incremental.py` + PG persist path with chapter-hash journal; active pointer CAS; raw chunks retained |
| REQ-CHUNK-07 | VERIFIED | budget skip / malformed / timeout / tool-smuggle → audited `rule_fallback` |
| REQ-CHUNK-08 | VERIFIED | `eval.py` + `release_verifier.py` same-snapshot A/B; CLI `run_chunker_qualification.py` |

## Plan close-out

| Plan | SUMMARY | Status |
|---|---|---|
| 07-01 | 07-01-SUMMARY.md | complete |
| 07-02 | 07-02-SUMMARY.md | complete |
| 07-03 | 07-03-SUMMARY.md | complete |
| 07-04 | 07-04-SUMMARY.md | complete + PG/search wiring |
| 07-05 | 07-05-SUMMARY.md | complete + PG build/pointer tables |
| 07-06 | 07-06-SUMMARY.md | complete |

## PG / indexing wiring (2026-07-13)

| Component | Path |
|---|---|
| ORM | `app/models/chunk_build.py` (`ChunkBuild`, `ChunkActivePointer`, `ChunkHierarchyNode`) |
| Migration | `migrations/versions/09_chunk_hierarchy_builds.py` (`09chunkhier01`) |
| Store | `app/services/chunking/pg_store.py` |
| Index hook | `indexing_service._persist_hierarchy_build` after raw embed |
| Search hook | `hybrid_search._enrich_with_hierarchy` + BM25 hierarchy column attach |
| text_chunks | nullable `hierarchy_*` + `source_start/end` columns |

## Residuals (optional follow-ups)

1. Optional: commit remaining local BGE / reader UX WIP on the same branch (out of Phase 07 scope).
2. Optional: separate Chroma collection per hierarchical candidate build (current path links evidence onto existing novel collection via text_chunks).

## Commits (representative)

- `1d91292` feat(07-01) baseline manifests/offsets  
- `eb061bb` feat(07-02) rule proposals/segmentation  
- `2a4bdc5` feat(07) complete 03–06  
- `4365e2e` docs(07) mark phase complete  
