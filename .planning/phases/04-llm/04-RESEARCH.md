# Phase 04 Research - LLM-Gated Knowledge Graph Pipeline

## Local Reference Findings

The local `C:\Users\li\Desktop\数据分析` project already implements the key pattern this phase should port:

```text
structured events
  -> semantic/vector recall
  -> candidate packages
  -> LLM proposal / judgment
  -> deterministic schema and evidence gates
  -> accepted graph edges
  -> graph projection
```

Verified local facts:

| Item | Evidence |
|---|---|
| Main structured DB | `integration/db/personal_system.sqlite`, 26 tables |
| Structured events | `unified_events` 8136 rows; `unified_events_rich` 8136 rows |
| Vector layer | Chroma collections documented as `personal_events` and `conversation_turns`; Chroma service was not running during inspection |
| Candidate layer | `graph_relation_candidates` 4652 rows |
| Judgment layer | `graph_relation_judgments` 4652 rows |
| Review queue | `graph_relation_review_queue` 4 rows |
| Graph projection | `conversation_graph.duckdb`, 7 tables, `e_relation` 19 accepted edges |

Most important carry-over rule:

> Vector similarity is a recall signal, not graph truth.

## External Research Snapshot

External references were checked only to validate the architecture direction, not to replace local project facts.

| Source | Useful Point | Fit |
|---|---|---|
| Microsoft GraphRAG docs: https://microsoft.github.io/graphrag/ | GraphRAG extracts a knowledge graph from text and uses graph/community structure for retrieval. | Supports adding graph structure on top of existing chunk RAG. |
| Microsoft GraphRAG GitHub: https://github.com/microsoft/graphrag | Microsoft positions the repo as a methodology/demo and warns indexing can be expensive. | Reinforces small-batch, persisted-job, cost-aware implementation. |
| Neo4j GraphRAG overview: https://neo4j.com/labs/genai-ecosystem/graphrag/ | Combines entity/relation extraction with graph algorithms and vector retrieval. | Supports hybrid vector + graph retrieval design. |
| Neo4j GraphRAG Python package: https://github.com/neo4j/neo4j-graphrag-python | First-party Neo4j package exists for GraphRAG applications. | Useful later; not required for this phase's MVP. |

## Planning Conclusions

1. Implement the evidence-gated pipeline before graph UI.
2. Keep PostgreSQL as the source of truth and Neo4j as a replayable projection.
3. Use LLMs for semantic operations that deterministic scripts cannot do reliably.
4. Use scripts for every decision that must be reproducible: allowed relation types, evidence refs, thresholds, schema, owner isolation, retry state, and writes.
5. Treat fiction and history as ontology profiles over the same core graph pipeline.

## Risks

| Risk | Mitigation |
|---|---|
| LLM hallucinates relationships | Evidence gate rejects refs outside package; unsupported claims become review items. |
| Vector similarity becomes fake graph truth | Candidate package records recall signals separately from accepted judgments. |
| Cost spikes on long novels/history books | Batch jobs, per-run budget limits, sampling, and resumable status. |
| Neo4j adds operational complexity | Delay Neo4j until accepted PostgreSQL projection works; sync must be optional and idempotent. |
| Existing fiction-specific names leak into history | Use `domain_type` and ontology profiles in new graph contracts; defer full `Novel` rename. |
