---
phase: 05
name: narrative-knowledge-unit-layer
status: Ready for planning
created: 2026-07-11
depends_on: [04-llm]
---

# Phase 05: Narrative Knowledge Unit Layer - Context

<domain>
## Phase Boundary

Transform Phase 04 accepted, evidence-gated judgments into a production retrieval surface. The phase owns narrative-unit lineage, canonicalization, immutable candidate indexes, hybrid retrieval, frozen evaluation, canary promotion, incremental refresh, reconcile, and joint rollback. It supports fiction and history through the existing domain/ontology profiles.

```text
accepted KnowledgeRelationJudgment + evidence refs
  -> versioned source snapshot
  -> draft narrative units
  -> deterministic validation + canonicalization
  -> immutable candidate Chroma collection
  -> chunks/units/hybrid frozen A/B + canary
  -> prepare/commit promotion
  -> active pointer
  -> affected-subject incremental refresh + lifecycle
```

</domain>

<decisions>
## Implementation Decisions

- **D-01:** Phase 04 accepted judgments are the only semantic source for first-generation narrative units. Recall candidates, raw LLM output, and projected graph rows cannot bypass accepted judgment lineage.
- **D-02:** Do not call promotion from `gates.py`. Acceptance and retrieval publication are separate transactions and separate runs.
- **D-03:** PostgreSQL is authoritative for unit drafts, canonical units, build manifests, lifecycle, active pointer, promotion journal, and source watermarks. Chroma is an immutable, replayable retrieval projection.
- **D-04:** Every unit preserves owner, work/novel, domain profile, source judgment ID, evidence refs, prompt/schema/model hashes where applicable, and build/run lineage.
- **D-05:** Canonicalization is deterministic-first and conservative. Similarity may propose merges, but conflicts, direction, subject, temporal validity, and hard negatives block unsafe merges.
- **D-06:** Retrieval exposes `chunks`, `units`, and `hybrid`. Existing chunk search remains available and is the fallback; no candidate becomes default merely because indexing succeeded.
- **D-07:** Candidate promotion requires frozen fiction/history A/B, evidence faithfulness, owner isolation, latency budget, exact collection reconcile, and a canary with zero critical wrong/stale results.
- **D-08:** Promotion uses an exact candidate checksum and prepare/commit journal. DB state, Chroma collection, active pointer, manifest, and watermark roll back together.
- **D-09:** Incremental refresh is driven by evidence/judgment content hashes and lifecycle changes. It rebuilds affected subjects only; no-change is a true zero-write path.
- **D-10:** LLM remains limited to semantic extraction/judgment already owned by Phase 04 and optional calibrated faithfulness judging. Scripts own unit construction, validation, merge gates, evaluation thresholds, publication, and rollback.

### the agent's Discretion

- Exact table and service names if contracts and ownership boundaries remain intact.
- Candidate collection naming and batch sizes.
- Hybrid fusion weights, provided they are configured and selected using dev data rather than frozen test tuning.

</decisions>

<canonical_refs>
## Canonical References

- `.planning/phases/04-llm/04-CONTEXT.md` - LLM/script boundary and evidence rules.
- `.planning/phases/04-llm/04-VERIFICATION.md` - verified Phase 04 behavior and residual risks.
- `backend/app/models/knowledge.py` - accepted judgment and evidence lineage contracts.
- `backend/app/services/knowledge/gates.py` - deterministic acceptance state machine.
- `backend/app/services/knowledge/projection.py` - replayable projection pattern.
- `backend/app/services/vector_store.py` - current Chroma boundary.
- `backend/app/services/hybrid_search.py` - current BM25/vector fusion and owner isolation.
- `backend/app/services/eval_service.py` - current eval persistence and metric implementation.
- `C:/Users/li/Desktop/数据分析/.planning/phases/14-knowledge-unit-layer/14-CONTEXT.md` - training-style RAG decisions.
- `C:/Users/li/Desktop/数据分析/.planning/phases/14-knowledge-unit-layer/14-AI-SPEC.md` - production gates and evaluation contract.

</canonical_refs>

<deferred>
## Deferred Ideas

- New LLM extraction prompts beyond Phase 04 judgment output.
- Full GraphRAG community summaries and graph algorithms.
- Replacing the `Novel` product resource with a generic corpus model.
- Automatically rewriting the Phase 03 frozen/gold dataset from production feedback.

</deferred>

---
*Phase: 05-narrative-knowledge-unit-layer*
*Context rebuilt: 2026-07-11*
