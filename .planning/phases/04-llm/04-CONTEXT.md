# Phase 04: LLM 语义判定与证据门控知识图谱链路 - Context

**Gathered:** 2026-07-02
**Status:** Ready for planning
**Source:** User direction + local project comparison

<domain>
## Phase Boundary

Build an auditable knowledge graph construction pipeline for NovelMind.

The core design is:

```text
TextChunk / Chapter
  -> script recall package
     (BM25, vector top-k, chapter adjacency, same entity, temporal window)
  -> LLM semantic proposal / judgment
  -> script schema gate + evidence gate + threshold gate + conflict gate
  -> accepted projection into PostgreSQL graph tables
  -> optional Neo4j sync and graph query API
```

The phase must cover both fiction and history-style corpora. The current product may still expose `Novel` as the primary resource name, but the internal graph contracts must not assume every text is fictional.

</domain>

<decisions>
## Implementation Decisions

- **D-01:** LLM handles semantic understanding and judgment only; scripts handle deterministic candidate generation, evidence package construction, JSON schema validation, evidence ID validation, thresholding, conflict detection, database writes, and graph sync.
- **D-02:** Every candidate, judgment, accepted relation, and graph edge must reference real source evidence; missing or out-of-package evidence IDs must be rejected or routed to human review.
- **D-03:** Vector top-k, BM25, same-chapter, same-entity, and time-window signals are recall signals only; they may create candidate packages but may not directly create accepted graph edges.
- **D-04:** Fiction and history share the same core graph pipeline, separated by `domain_type` / `ontology_profile` rather than hard-coded fiction-only labels.
- **D-05:** PostgreSQL is the authoritative source for candidates, judgments, evidence refs, accepted relations, review status, and owner isolation; Neo4j is an optional, replayable projection.
- **D-06:** Graph extraction and LLM judging must be persisted jobs or CLI runs with status and retry semantics, not hidden synchronous HTTP work.
- **D-07:** Exact table names, service class names, endpoint paths, and whether candidates use one table or multiple tables are at the agent's discretion if evidence, owner isolation, reviewability, and deterministic gates are preserved.

</decisions>

<canonical_refs>
## Canonical References

Downstream agents MUST read these before planning or implementing.

### NovelMind Current Architecture
- `.planning/STATE.md` - current execution cursor and v0.3 gap status.
- `.planning/REQUIREMENTS.md` - verified requirements and remaining RAG eval gaps.
- `.planning/codebase/ARCHITECTURE.md` - actual backend, DB, Chroma, AI provider topology.
- `docs/architecture/01-system-overview.md` - current implemented and missing capability boundary.
- `docs/architecture/02-module-map.md` - module ownership and integration paths.
- `docs/architecture/03-data-model.md` - PostgreSQL entities, existing character/timeline skeletons, Neo4j status.
- `docs/architecture/06-rag-pipeline.md` - chunking, embedding, Chroma, hybrid search, eval baseline.
- `docs/architecture/08-ai-model-layer.md` - LiteLLM, ai_router, ai_service, usage logging.

### Existing Code Contracts
- `backend/app/models/character.py` - existing Character and CharacterRelation skeleton.
- `backend/app/models/timeline.py` - existing TimelineEvent skeleton.
- `backend/app/models/analysis.py` - existing AnalysisResult skeleton.
- `backend/app/services/ai_service.py` - only approved LLM call wrapper.
- `backend/app/services/ai_router.py` - task-tier routing pattern.
- `backend/app/services/hybrid_search.py` - current BM25 + vector retrieval pattern.
- `backend/app/services/eval_service.py` - eval result persistence and metric pattern.

### Local Reference Project
- `C:\Users\li\Desktop\数据分析\.planning\codebase\ARCHITECTURE.md` - layered SQLite + Chroma + graph relation pipeline.
- `C:\Users\li\Desktop\数据分析\integration\db\DEPRECATED.md` - explicit rule that vector similarity is not graph truth.
- `C:\Users\li\Desktop\数据分析\integration\scripts\build_graph_relation_candidates_v2.py` - LLM candidate proposal after coarse recall.
- `C:\Users\li\Desktop\数据分析\integration\scripts\judge_graph_relations.py` - LLM relation judgment with structured output.
- `C:\Users\li\Desktop\数据分析\integration\scripts\evaluate_graph_relation_judgments.py` - deterministic evidence gate before accepted graph edges.
- `C:\Users\li\Desktop\数据分析\integration\scripts\build_conversation_graph.py` - accepted-edge graph projection.

</canonical_refs>

<specifics>
## Specific Ideas

- Add candidate and judgment tables before changing user-facing graph behavior.
- Reuse existing `text_chunks` as evidence units for the first implementation slice.
- Add ontology profiles: `fiction` and `history`.
- Use Pydantic schemas for LLM structured output.
- Store raw LLM outputs only as audit data, never as accepted business records.
- Introduce review statuses: `candidate`, `proposed`, `rejected`, `needs_human_review`, `accepted`.
- Keep accepted relation projection idempotent and replayable.

</specifics>

<deferred>
## Deferred Ideas

- Full graph UI polish is deferred until accepted relation data exists.
- Large-scale graph algorithms, community detection, and Neo4j Bloom-like workflows are deferred.
- Full rename from `Novel` to `Corpus` is deferred; only minimal domain metadata is in scope if needed.
- Fanfiction generation using graph context is deferred.

</deferred>

<scope_fence>
## Scope Fence

This phase does not close v0.3 RAG eval gaps. Phase 04 execution remains blocked until Phase 03 closure is accepted or explicitly overridden.
</scope_fence>

---

*Phase: 04-llm*
*Context gathered: 2026-07-02*
