---
phase: 05-narrative-knowledge-unit-layer
plan: 05-01-narrative-unit-contracts-and-source-snapshot
type: implementation
wave: 1
depends_on: []
files_modified:
  - backend/app/models/knowledge_unit.py
  - backend/app/schemas/knowledge_unit.py
  - backend/app/models/__init__.py
  - backend/app/schemas/__init__.py
  - backend/migrations/versions/*
  - backend/app/services/knowledge_units/source_snapshot.py
  - backend/tests/test_knowledge_unit_models.py
  - backend/tests/test_knowledge_unit_source_snapshot.py
autonomous: true
requirements_addressed: [REQ-NU-01, REQ-NU-02, REQ-NU-04]
truths:
  - "D-01: only accepted Phase 04 judgments may seed first-generation narrative units."
  - "D-02: acceptance and retrieval publication are separate runs and transactions."
  - "D-03: PostgreSQL owns unit/build/version/pointer/journal truth."
  - "D-04: every unit preserves complete owner/work/domain/evidence lineage."
---

# 05-01 - Narrative Unit Contracts and Source Snapshot

## Objective

Create the source-of-truth contracts and freeze accepted Phase 04 judgments into reproducible, owner-scoped build inputs. This plan does not write Chroma or change search behavior.

## Steps

1. Define ORM and Pydantic contracts for source snapshots, draft/canonical narrative units, unit-evidence links, index builds, active pointers, and promotion journals. Use explicit lifecycle and build statuses; keep raw LLM audit fields out of accepted unit truth.
2. Add an Alembic migration with owner/novel/build/status/canonical indexes, uniqueness for source judgment per snapshot, and foreign keys that preserve traceability without allowing cross-owner joins.
3. Implement deterministic source snapshot creation from `KnowledgeRelationJudgment(status="accepted", gate_status="accepted")`. Include judgment/candidate/evidence content hashes, domain profile, source watermark, and a sorted manifest checksum.
4. Reject projected graph rows, candidate-only rows, out-of-owner evidence, missing evidence, and moving inputs. Re-running the same accepted set must return the same snapshot checksum.
5. Add model, migration-shape, owner-isolation, content-hash, idempotency, and invalid-lineage tests.
6. Test, Fix, and Confirm: run targeted tests, `py_compile`, Ruff, and PostgreSQL Alembic upgrade/current/check when the service is available.

## Must-Haves

- No modification to `gates.py` acceptance transaction.
- A narrative unit cannot exist without source judgment and evidence lineage.
- PostgreSQL contracts represent draft, candidate, active, failed, deprecated, and rolled-back states.
- Source snapshots are immutable and reproducible.
- Covers D-01, D-02, D-03, D-04 and REQ-NU-01/02/04.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_unit_models.py tests/test_knowledge_unit_source_snapshot.py -v
ruff check app/models/knowledge_unit.py app/schemas/knowledge_unit.py app/services/knowledge_units/source_snapshot.py tests/test_knowledge_unit_models.py tests/test_knowledge_unit_source_snapshot.py
alembic upgrade head
alembic current
alembic check
```

