---
phase: 04-llm
plan: 04-03-evidence-gates-and-projection
type: implementation
wave: 3
depends_on:
  - 04-02-candidate-packages-and-llm-judgment
files_modified:
  - backend/app/services/knowledge/gates.py
  - backend/app/services/knowledge/projection.py
  - backend/app/services/knowledge/graph_sync.py
  - backend/app/api/knowledge.py
  - backend/tests/test_knowledge_gates.py
  - backend/tests/test_knowledge_projection.py
autonomous: false
requirements_addressed:
  - REQ-KG-02
  - REQ-KG-03
  - REQ-KG-05
truths:
  - "D-01: LLM judgments never write final graph facts directly; scripts own deterministic gates and projection."
  - "D-02: accepted graph facts require valid source evidence refs."
  - "D-03: recall signals may explain candidate generation but cannot create accepted edges."
  - "D-05: PostgreSQL remains source of truth; Neo4j sync reads accepted PostgreSQL rows only."
  - "D-06: graph extraction and projection must expose run status and retry semantics."
  - "D-07: exact API/service/table naming is flexible if evidence, owner isolation, reviewability, and gates are preserved."
---

# 04-03 - Evidence Gates and Graph Projection

## Objective

Turn LLM judgments into accepted, reviewable graph facts only after deterministic gates pass.

## Steps

1. Implement schema and evidence gates.
   - Schema gate: Pydantic validation already passed and required fields present.
   - Evidence gate: every evidence ref exists, belongs to the same owner/work, and was in the package.
   - Threshold gate: confidence and risk flags meet configured policy.
   - Conflict gate: contradictory accepted judgments route to review.

2. Implement review routing.
   - `accepted`: deterministic gates pass.
   - `needs_human_review`: low confidence, risk flags, conflict, or ambiguous domain semantics.
   - `rejected`: invalid evidence, unsupported relation, or schema failure.

3. Implement accepted projection.
   - For fiction, project accepted person/character relations into `Character` / `CharacterRelation` only after entity resolution is sufficient.
   - For history, project accepted events into timeline-compatible records with source/evidence metadata.
   - Keep projection idempotent and replayable from accepted judgments.

4. Implement optional Neo4j sync boundary.
   - Add sync service that reads accepted PostgreSQL rows.
   - Keep Neo4j optional and disabled by default.
   - Failed sync must not change accepted PostgreSQL state.

5. Add API endpoints.
   - Start/list extraction runs.
   - List candidates and judgments.
   - Review accept/reject endpoints.
   - Query accepted graph neighborhood for a work.

6. Test, Fix, and Confirm.
   - Unit-test each gate.
   - Test owner isolation on every API.
   - Test projection idempotency.
   - Test Neo4j disabled path.

## Must-Haves

- No accepted relation without valid evidence.
- No LLM path writes directly into final relation/timeline tables.
- Review transitions are explicit and auditable.
- Projection can be re-run without duplicating accepted graph facts.
- Neo4j is a projection only; PostgreSQL remains source of truth.
- Cross-owner evidence packages and graph queries fail closed.
- Covers context decisions: D-01: LLM/script split; D-02: evidence-first persistence; D-03: recall signals are not truth; D-05: PostgreSQL source of truth; D-06: persisted jobs; D-07: implementation discretion.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_gates.py tests/test_knowledge_projection.py tests/test_knowledge_api.py -v
```

Manual verification:

- Create one valid and one invalid judgment fixture.
- Confirm valid fixture reaches `accepted`.
- Confirm invalid evidence fixture reaches `rejected`.
- Confirm review fixture is visible in review queue.
