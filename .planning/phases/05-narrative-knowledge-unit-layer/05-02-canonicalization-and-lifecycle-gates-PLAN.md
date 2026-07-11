---
phase: 05-narrative-knowledge-unit-layer
plan: 05-02-canonicalization-and-lifecycle-gates
type: implementation
wave: 2
depends_on: [05-01-narrative-unit-contracts-and-source-snapshot]
files_modified:
  - backend/app/services/knowledge_units/materialize.py
  - backend/app/services/knowledge_units/canonicalize.py
  - backend/app/services/knowledge_units/lifecycle.py
  - backend/scripts/build_narrative_units.py
  - backend/evals/narrative_unit_merge_cases.json
  - backend/tests/test_knowledge_unit_materialize.py
  - backend/tests/test_knowledge_unit_canonicalize.py
autonomous: true
requirements_addressed: [REQ-NU-01, REQ-NU-02, REQ-NU-03]
truths:
  - "D-04: materialized units retain full lineage."
  - "D-05: canonicalization is deterministic-first and conservative."
  - "D-10: scripts own unit construction, merge gates, and lifecycle decisions."
---

# 05-02 - Canonicalization and Lifecycle Gates

## Objective

Materialize evidence-backed draft units from frozen accepted judgments and conservatively canonicalize duplicates without collapsing conflicts, direction, identity, or time.

## Steps

1. Build deterministic unit text from accepted relation type, source/target entities or events, rationale-safe fields, confidence, and evidence refs. Do not call a new LLM and do not invent facts absent from the accepted judgment.
2. Define stable canonical keys scoped by owner, novel, domain, unit type, normalized subject/object, relation direction, and temporal window. Exact matches may merge automatically; semantic similarity only produces merge proposals.
3. Implement gates for contradictory relation types, reversed direction, entity/event mismatch, disputed history claims, non-overlapping time, missing evidence, and lifecycle incompatibility.
4. Add explicit `current`, `disputed`, `deprecated`, and `deleted` propagation. Preserve all source unit links and never physically discard audit lineage.
5. Add fiction/history merge-positive and hard-negative fixtures, including aliases, same-name different people, sequence-vs-causality, superseded facts, and disputed sources. Require zero hard-negative false merges.
6. Add a dry-run/write CLI that emits manifest, yield, reject/review reasons, merge groups, and checksum without publishing an index.
7. Test, Fix, and Confirm: run fixtures twice and assert deterministic output/checksum.

## Must-Haves

- Unit construction remains a script-owned transformation of accepted semantic truth.
- Similarity is never sufficient for automatic merge.
- Conflict and history disputed states remain visible and retrievable as qualified knowledge.
- Hard-negative false merges equal zero.
- Covers D-04, D-05, D-10 and REQ-NU-01/02/03.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_unit_materialize.py tests/test_knowledge_unit_canonicalize.py -v
python scripts/build_narrative_units.py --snapshot-id TEST --dry-run
ruff check app/services/knowledge_units/materialize.py app/services/knowledge_units/canonicalize.py app/services/knowledge_units/lifecycle.py scripts/build_narrative_units.py
```

