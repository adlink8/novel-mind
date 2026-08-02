# Phase 26: Question-Driven Retrieval and Evidence — Research

**Researched:** 2026-08-01
**Domain:** typed question planning, multi-dimension retrieval, evidence materialization
**Confidence:** HIGH for existing contracts; MEDIUM for the new QueryPlan boundary

## User Constraints

See 26-CONTEXT.md: D-01..D-14 are binding; discretion and deferred ideas are copied there
from the supplied Kimi source and corrected for current repository facts.

## Phase Requirements

| ID | Research support |
|---|---|
| REQ-QP-01 | strict reader-chat/narrative-memory schemas plus deterministic routing analog |
| REQ-QP-02 | adapter inventory; character/world currently report unavailable |
| REQ-QP-03 | narrative-memory leaf citation and reader-chat manifest analogs |
| REQ-QP-04 | shared ContextBuilder seam with selection/ChapterRange anchors |

## Summary

Use one backend QueryPlan service before both chat consumers. Existing code supplies layer
routing/fusion, frozen manifests, answer allowlisting, and candidate-only NM readers.
[VERIFIED: repository grep]

Raw text, timeline/causality, relationships, clues, Narrative Units, and candidate NM have
read analogs. Character-state and world-entity production readers are absent; return
unavailable until Phase 27 rather than empty-success. [VERIFIED: repository grep]

Primary recommendation: queryplan service performs deterministic parse/validate, persisted
trace, honest adapters, provenance-preserving fusion, leaf/hash recheck, and frozen
manifest; both chats inject it and vary only their anchors.

## Architectural Responsibility Map

| Capability | Primary tier | Secondary tier | Rationale |
|---|---|---|---|
| Plan parse/scope/spoiler | API / Backend | Database | server owns scope |
| Adapters/fusion | API / Backend | Database | wraps existing reads |
| Evidence/hash | API / Backend | Database | frozen source authority |
| Manifest/citation | API / Backend | Browser | server allowlist |
| Anchor/trace display | Browser / Client | API | anchors differ |

All rows: [VERIFIED: repository grep]

## Standard Stack

No new external packages. Reuse existing pinned FastAPI/Pydantic, SQLAlchemy/Alembic,
PostgreSQL, pytest, Vitest, and Playwright. [VERIFIED: repository grep]

| Component | Prescriptive use |
|---|---|
| knowledge_units/search.py | layer routing and deterministic fusion |
| timeline/query.py | cutoff events/causal edges |
| relationships/query.py | effective interval relations |
| clues/query.py | spoiler-aware clue reads |
| narrative_memory/candidate_reader.py | candidate-only NM |
| narrative_memory/citations.py and source_snapshot.py | leaf/hash lineage |
| reader_chat/context.py, retrieval.py, conversations.py, worker.py | manifest, status, freeze, retry |
| schemas/reader_chat.py | citation allowlist |

No installation; package audit not applicable.

## Architecture Patterns

Flow: reader selection / analysis range / Skill → strict QueryPlan → owner/novel/version/
cutoff → adapters with available/partial/unavailable → deterministic fusion with
provenance → leaf/raw re-slice and hash → frozen manifest → existing cited-answer gateway.
[VERIFIED: repository grep]

Recommended new package: backend/app/services/queryplan with schemas, parser, adapters,
fusion, evidence, and service modules plus unit/integration/adversarial tests.
[VERIFIED: repository grep]

Rules: ambiguity rejects before adapter calls; unavailable differs from zero rows; summaries
organize recall only; freeze once and retry by checksum.

## Don't Hand-Roll

| Problem | Reuse |
|---|---|
| citation legality | reader_chat manifest and validator |
| hash/offset slicing | narrative_memory citations/source snapshot |
| chat budget/worker | reader_chat budget/worker |
| candidate NM access | candidate_reader |
| ranking baseline | knowledge_units deterministic fusion |

## Common Pitfalls

- Empty-success hides missing dimensions; retain status and reason. [VERIFIED: repository grep]
- Candidate NM needs explicit through_chapter; readiness is not visibility. [VERIFIED: repository grep]
- Scores, summaries, routing metadata and chat text are not citations. [VERIFIED: repository grep]
- Analysis Chat must not create a second retrieval stack. [VERIFIED: repository grep]
- Phase 25.2 Runtime and Phase 25.3 governance remain planning boundaries until separately verified. [VERIFIED: repository grep]

## Code Examples

Planning sketch: a QueryPlan must carry intent, owner_id, novel_id, version_id, spoiler_cutoff,
dimension tuple, fallback, and must-cite-facts constraint. Materialization must re-slice the
frozen chapter and reject a changed sha256. These are patterns, not current code.
[VERIFIED: repository grep]

## Validation Architecture

### Test Framework

Backend pytest has unit/integration/adversarial tiers; frontend uses Vitest and Playwright.
[VERIFIED: repository grep]

| Level | Command |
|---|---|
| Quick | cd backend; pytest tests/unit/narrative_memory tests/unit/reader_chat -q |
| Phase | cd backend; pytest tests/unit/queryplan tests/integration/queryplan tests/adversarial/test_queryplan* -q |

### Requirement → test map

| Requirement | Test proof |
|---|---|
| REQ-QP-01/02 | parser and adapter contracts: strict fields and unavailable semantics |
| REQ-QP-03 | stale hash/offset adversarial: leaf-only fail-closed |
| REQ-QP-04 | consumer integration: shared core, distinct anchors |
| D-06/09 | fusion baseline and answer abstention |

Nyquist: unit tests per task, manifest integration per wave, adversarial/browser gate at
phase close. Human UAT covers local/cross-chapter/future questions, citation jump,
cancel/retry, loading/error, keyboard/focus, and no future metadata leak.

### Wave 0 gaps

queryplan test directories and phase-26 question fixtures are absent and must precede
implementation. [VERIFIED: repository grep]

## Security Domain

V2/V3/V4/V5 apply through existing authentication, session, owner checks and strict schemas;
V6 uses existing hash/checksum primitives, no custom crypto. Threat controls are scope
denial, cutoff at each adapter, stale-hash rejection, and non-authoritative chat text.
[VERIFIED: repository grep]

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Phase 25.2 Skill lands before execution | seam may need later adapter |
| A2 | Phase 27 exposes character/world readers | keep adapters unavailable until verified |

## Sources

Current repository service/test/docs files [VERIFIED: repository grep]; supplied Kimi Phase 26
CONTEXT/RESEARCH/PATTERNS [VERIFIED: repository grep]. Standard stack HIGH, architecture
HIGH for analogs/MEDIUM for QueryPlan, pitfalls HIGH. Valid until 2026-09-01.
