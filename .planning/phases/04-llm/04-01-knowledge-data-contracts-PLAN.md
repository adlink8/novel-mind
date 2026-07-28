---
phase: 04-llm
plan: 04-01-knowledge-data-contracts
type: implementation
wave: 1
depends_on: []
files_modified:
  - backend/app/models/knowledge.py
  - backend/app/schemas/knowledge.py
  - backend/migrations/versions/*
  - backend/tests/test_knowledge_models.py
autonomous: false
requirements_addressed:
  - REQ-KG-01
  - REQ-KG-02
truths:
  - "D-01: LLM handles semantic judgment only; scripts own deterministic candidates, evidence, gates, writes, and graph sync."
  - "D-02: candidates, judgments, accepted relations, and graph edges require real evidence refs."
  - "D-04: fiction and history share the core pipeline through domain profiles."
  - "D-05: PostgreSQL is authoritative; Neo4j is only a replayable projection."
  - "D-07: implementation naming details are discretionary if evidence, isolation, reviewability, and gates hold."
---

# 04-01 - Knowledge Graph Data Contracts

## Objective

Create the PostgreSQL source-of-truth contract for evidence-gated knowledge graph construction.

This plan does not call LLMs and does not sync Neo4j. It creates the tables and schemas that make later LLM output auditable.

## Steps

1. Define ontology profile constants.
   - `fiction`
   - `history`
   - relation type enums grouped by profile
   - entity/event type enums grouped by profile

2. Add source-of-truth ORM models.
   - `KnowledgeExtractionRun`: owner, novel/work, domain profile, status, counters, cost/latency summary.
   - `KnowledgeEntityCandidate`: normalized entity candidate, aliases, profile type, source refs.
   - `KnowledgeEventCandidate`: event candidate, time refs, location refs, source refs.
   - `KnowledgeRelationCandidate`: source/target refs, recall signals, package snapshot, status.
   - `KnowledgeRelationJudgment`: LLM structured judgment, prompt version, model, confidence, risk flags, gate status.
   - `KnowledgeEvidenceRef`: normalized refs to `text_chunks`, `chapters`, and future accepted graph rows.
   - `KnowledgeReviewQueue`: rows requiring human review.

3. Add Alembic migration.
   - Include owner/novel foreign keys.
   - Include indexes for `novel_id`, `run_id`, `status`, `gate_status`, `relation_type`, and `domain_profile`.
   - Preserve cascade behavior and owner isolation through Novel ownership.

4. Add Pydantic schemas.
   - Request/response schemas for run, candidate, judgment, review queue, and evidence refs.
   - LLM output schemas must be strict and independent from API response schemas.

5. Add model tests.
   - SQLite-compatible tests for relationships and JSON fields.
   - PostgreSQL migration check remains required for final verification.

6. Test, Fix, and Confirm.
   - Run targeted model/schema tests.
   - Run Alembic upgrade/current/check on PostgreSQL if services are available.
   - Confirm no graph facts can be represented without evidence refs.

## Must-Haves

- PostgreSQL is authoritative; Neo4j is not introduced in this plan.
- LLM output fields are stored as judgments or audit metadata, not accepted facts.
- Every candidate and accepted judgment can reference evidence IDs.
- Owner isolation must be enforceable through `Novel.owner_id`.
- Fiction and history are both represented through `domain_profile`.
- Existing `CharacterRelation` and `TimelineEvent` tables are not silently repurposed before acceptance/projection logic exists.
- Covers context decisions: D-01: LLM/script split; D-02: evidence-first persistence; D-04: fiction/history profiles; D-05: PostgreSQL source of truth; D-07: implementation discretion.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_models.py -v
alembic upgrade head
alembic current
alembic check
```

Manual verification:

- Inspect generated migration for indexes and foreign keys.
- Confirm rejected/missing evidence states are representable.
- Confirm relation candidates cannot bypass judgment/gate status.
