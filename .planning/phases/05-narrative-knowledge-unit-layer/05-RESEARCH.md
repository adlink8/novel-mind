# Phase 05 Research - Production Narrative Knowledge Units

## Finding

Phase 04 already owns the difficult semantic step: bounded LLM judgments followed by schema, evidence, threshold, and conflict gates. Phase 05 should not add another uncontrolled extraction path. Its job is data-product publication: turn accepted judgments into versioned, evidence-backed retrieval units and prove they improve retrieval before activation.

## Current Code Facts

- `KnowledgeRelationJudgment` and its candidate/evidence relationships are persisted in PostgreSQL.
- `gates.py` owns acceptance; coupling indexing to this transaction would make publication failures corrupt acceptance semantics.
- `projection.py` demonstrates idempotent replay from accepted PostgreSQL rows.
- `vector_store.py` initializes Chroma at import time and currently models per-novel chunk collections, not immutable candidate builds.
- `hybrid_search.py` fuses BM25 and vector results for raw chunks and enforces owner scope through the novel.
- Phase 03 has reusable Recall/MRR/NDCG infrastructure, but current gold calibration remains incomplete and must not be falsely treated as a passed quality baseline.

## Reference Project Lessons

The reference Phase 13-14 validates six patterns worth carrying over:

1. One canonical rule/source contract before AI publication.
2. Frozen inputs and manifests with content, model, prompt, schema, and config hashes.
3. All generated data enters staging; candidate indexes are immutable.
4. Frozen A/B and canary gate promotion; indexing success is not quality success.
5. Hybrid retrieval can outperform pure knowledge-unit retrieval because compression loses literal matches.
6. Incremental refresh, deletion propagation, exact reconcile, and joint rollback are one lifecycle contract.

Its measured result is especially relevant: pure knowledge units lost lexical recall, while a `ku1 + raw4` hybrid restored Recall@5 to 0.85. NovelMind therefore must preserve raw chunk fallback and evaluate configurable mixed top-k rather than assume unit-only is superior.

## Recommended Architecture

- Add narrative unit/build/version/pointer/journal contracts in PostgreSQL.
- Snapshot accepted judgments by stable content hash; never read a moving acceptance set during a build.
- Construct deterministic QA/claim text from accepted semantic fields and evidence. Do not invent unsupported prose.
- Canonicalize within owner/work/domain/subject buckets. Use exact keys first; similarity only proposes reviewable merges.
- Build an immutable Chroma candidate and reconcile actual IDs against the manifest.
- Extend search through a strategy boundary rather than embedding mode branches throughout API code.
- Run fiction/history frozen tests across chunks, units, and hybrid; tune on dev only.
- Promote exact checksum through prepare/commit journal and retain the previous active checkpoint.
- Compute deltas from accepted judgment/evidence hashes and propagate deprecated/deleted states before indexing.

## Risks

- Existing Phase 03 labels are insufficient for claiming retrieval improvement; Phase 05 needs a scoped, evidence-labeled fixture/frozen set while preserving the broader open gap.
- Chroma service availability currently blocks live E2E; deterministic fake-store tests and blocked status are required, but final promotion verification requires a healthy real service.
- A unit generated 1:1 from a judgment can duplicate the same fact across chapters. Canonicalization and hard-negative tests are required before indexing.
- History needs stricter disputed/temporal labeling than fiction; the shared core must keep domain-specific thresholds/profile metadata.

## Plan Split

1. Contracts and accepted-source snapshot.
2. Canonicalization and lifecycle gates.
3. Immutable candidate index and hybrid retrieval.
4. Frozen evaluation, canary, and journaled promotion.
5. Incremental refresh, exact reconcile, and joint rollback.
