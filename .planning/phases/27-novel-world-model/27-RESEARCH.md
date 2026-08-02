# Phase 27: Novel World Model — Research

**Researched:** 2026-08-01
**Domain:** evidence-gated narrative facts, epistemic state, versioned world projections
**Confidence:** HIGH for existing pipeline analogs; MEDIUM for new world schemas

## User Constraints

D-01..D-06 from 27-CONTEXT.md are binding. Phase 22 remains blocked; Phase 25.2 is an
Issue boundary, not verified current-branch implementation.

## Summary

Build typed candidate projections over existing evidence and version patterns. Timeline,
relationships, clues, and narrative-memory services already separate candidate/judgment/
gate/projection concerns and persist evidence, overrides, checkpoints, and lineage.
[VERIFIED: repository grep]

Use one epistemic envelope for canon_fact, probable_inference, literary_interpretation,
and user_interpretation. Character knowledge is cutoff/POV scoped and history-preserving;
world rules retain exceptions. Reader Chat and chat-derived content stay outside facts.

Primary recommendation: evidence package → strict typed claim → evidence/conflict/authority
gates → candidate projection → cutoff-aware query.

## Architectural Responsibility Map

| Capability | Primary tier | Secondary | Rationale |
|---|---|---|---|
| Claim extraction/gating | API / Backend | AI worker | server gates model output |
| Versioned projection | Database / Storage | API | immutable candidate lineage |
| Cutoff/POV query | API / Backend | Database | visibility authority |
| Evidence display | Browser | API | display only |

[VERIFIED: repository grep]

## Standard Stack

No new package. Reuse Pydantic/SQLAlchemy/Alembic/PostgreSQL, existing timeline,
relationships/clues workers, narrative-memory contracts, pytest, Vitest and Playwright.
[VERIFIED: repository grep]

## Architecture Patterns

Use append-only observations and effective intervals; preserve contradictions. A causal
edge requires evidence and a gate distinct from temporal adjacency. Character knowledge
has subject, proposition, known_at/cutoff, POV/source, and epistemic label. Alias collisions
need review; rule exceptions are first-class. [VERIFIED: repository grep]

Recommended modules: backend/app/services/world_model/{contracts,claims,gates,queries,
overrides,provenance}.py and matching models/migration/tests, following current module
boundaries. [VERIFIED: repository grep]

## Don't Hand-Roll

| Problem | Reuse |
|---|---|
| evidence lineage | existing evidence refs and narrative_memory provenance |
| durable jobs/checkpoints | timeline/clues/narrative-memory workers |
| interval visibility | relationships/timeline query helpers |
| crypto/hash | source snapshot/citation utilities |
| structured judgment | existing AI gateway and deterministic gates |

## Common Pitfalls

- Co-occurrence is not causality; require cited cause and gate status.
- Character knowledge is not global canon; enforce POV/disclosure cutoff.
- Inference/interpretation must not serialize as canon_fact.
- Alias similarity must not silently merge entities.
- Rule exceptions cannot be discarded by normalization.
- Reader Chat/user interpretation must not write original-canon facts.

Controls follow current contracts and locked decisions; new schema details are MEDIUM
confidence. [VERIFIED: repository grep]

## Code Examples

Pattern sketch: Claim = subject, predicate, object/value, authority, source_refs,
owner/novel/version, valid interval, disclosure cutoff, confidence, and gate status.
Query filters scope/cutoff, then authority, then returns evidence refs. [VERIFIED: repository grep]

## Validation Architecture

Backend pytest unit/integration/adversarial; frontend Vitest/Playwright. [VERIFIED: repository grep]

| Requirement | Fixture/test | Command |
|---|---|---|
| D-03/04 | causal false positive + temporal conflict | cd backend; pytest tests/unit/world_model/test_gates.py tests/adversarial/test_world_model_authority.py -q |
| D-05 | mistaken/hidden knowledge replay | cd backend; pytest tests/unit/world_model/test_knowledge.py -q |
| D-06 | chat contamination/override | cd backend; pytest tests/adversarial/test_world_model_contamination.py -q |

Wave 0 gaps: world-model fixtures, conflict corpus, POV/disclosure examples, and browser
inspection fixtures are absent. [VERIFIED: repository grep]

Human UAT: inspect rule exception, mistaken belief, undisclosed future fact, and override;
verify labels, cutoff, evidence jump, and no active pointer.

## Security Domain

V4 access control, V5 schema validation, V6 hash lineage apply; V2/V3 apply to owner
sessions. Threats: cross-owner claims, spoiler metadata, authority upgrade, prompt
injection, alias poisoning. [VERIFIED: repository grep]

## Sources and confidence

Current timeline, relationships, clues, narrative-memory services/tests and architecture
docs. [VERIFIED: repository grep] Stack HIGH; architecture MEDIUM; pitfalls HIGH.
Valid until 2026-09-01.
