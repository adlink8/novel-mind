---
phase: 07-semantic-hierarchical-chunking
plan: 01
subsystem: rag-chunking
tags: [chunker, baseline, offsets, manifest, lineage]

requires:
  - phase: 06-automated-quality-ci
    provides: five-tuple quality lineage contract for future A/B
provides:
  - strict source snapshot / raw chunk / manifest contracts
  - deterministic node IDs and manifest checksums
  - offset-preserving baseline adapter over rule ChunkingService
  - legacy chunk API wrapper without moving active index
affects: [07-02, 07-03, 07-04, 07-05, 07-06]

tech-stack:
  added: []
  patterns:
    - "unicode_codepoint offsets with single-scan CRLF map"
    - "forward-only flexible whitespace match for paragraph-join gaps"
    - "baseline manifest is A-side evidence only (D-07 no active pointer)"

key-files:
  created:
    - backend/app/services/chunking/schemas.py
    - backend/app/services/chunking/manifests.py
    - backend/app/services/chunking/baseline.py
    - backend/tests/unit/chunking/test_manifests.py
    - backend/tests/unit/chunking/test_offsets.py
    - backend/tests/integration/chunking/test_deterministic_baseline.py
  modified:
    - backend/app/services/chunking_service.py

key-decisions:
  - "Chunk on CRLF-normalized text; source spans via norm_to_source map"
  - "Flexible match allows blank-line gaps when chunker joins with single \\n"
  - "legacy chunk_novel remains unchanged; optional chunk_novel_with_baseline_lineage"

patterns-established:
  - "ChunkManifest.v1 sorted nodes/edges + config/source lineage checksum"
  - "node_id derived from snapshot+chapter+index+content_hash+source span"

requirements-completed: [REQ-CHUNK-01]

duration: ~35min
completed: 2026-07-13
---

# Phase 07 Plan 01: Chunker Manifests and Deterministic Baseline

**Stable source snapshots, unicode offsets, and versioned baseline manifests over the existing rule chunker without touching active indexes.**

## Verification

```text
pytest tests/unit/chunking/test_manifests.py \
  tests/unit/chunking/test_offsets.py \
  tests/integration/chunking/test_deterministic_baseline.py \
  tests/test_chunking.py -q
# 41 passed
```

## Must-Haves

| Truth | Evidence |
|---|---|
| Repeat build same IDs/checksum | test_repeat_build_same_checksum_and_node_ids |
| Offsets reconstruct payload | test_baseline_chunks_reconstruct_from_normalized_offsets |
| CRLF / duplicates safe | test_offset_map_crlf_and_unicode, test_duplicate_sentences |
| No active mutation | pure in-memory manifest return only |

## Next

Execute **07-02** rule boundary confidence and candidate segmentation.
