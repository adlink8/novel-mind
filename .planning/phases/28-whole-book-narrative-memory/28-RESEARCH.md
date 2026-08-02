# Phase 28: Whole-Book Narrative Memory Convergence — Research

**Researched:** 2026-08-01
**Domain:** durable hierarchical orchestration, recovery, candidate convergence
**Confidence:** HIGH

## User Constraints

D-01..D-07 from 28-CONTEXT.md are binding. Candidate-only and no active pointer are
non-negotiable; Phase 22 is still 0/3.

## Summary

The repository has builder_worker/repository/contracts, dependency graph, change oracle,
carry-forward, global builder, reports, budgets, and PostgreSQL integration tests.
[VERIFIED: repository grep]

Plan orchestration hardening and real long-book qualification: freeze source manifest,
process chapters independently, persist terminal states/checkpoints, build ready continuous
arcs/volumes, then global candidate, with explicit isolation/block reasons and DB-recomputed
manifests.

## Architectural Responsibility Map

| Capability | Primary tier | Secondary | Rationale |
|---|---|---|---|
| Worker/checkpoint/recovery | API / Backend | Database | durable control flow |
| Chapter/arc/global artifacts | Database / Storage | API | immutable candidates |
| Progress/report | API / Backend | Browser | aggregate status |
| Candidate visualization | Browser | API | no authority change |

[VERIFIED: repository grep]

## Standard Stack

No new packages. Reuse narrative_memory services, PostgreSQL/Alembic, AI gateway/budget,
pytest and Playwright. [VERIFIED: repository grep]

## Architecture Patterns

Use stable stage/idempotency keys; exact cache requires checksum-identical inputs. Compute
dirty closure before work. Isolate chapter errors and block only downstream nodes. Require
terminal audit and DB manifest recomputation. [VERIFIED: repository grep]

## Don't Hand-Roll

Reuse builder control/repository, dependency_graph/change_oracle, carry_forward, budget,
report, source manifests, and existing integration fixtures. Do not create another queue,
cache, or pointer mechanism. [VERIFIED: repository grep]

## Common Pitfalls

Silent pending, restart-all-on-one-error, stale source manifest, cache reuse across model/
prompt/schema changes, arc gaps/overlaps, and accidental pointer writes. [VERIFIED: repository grep]

## Code Examples

Checkpoint pattern: completed stage keys, artifacts, source checksum, model/prompt/schema
lineage, retry count, and reason code; resume validates inputs before reuse. [VERIFIED: repository grep]

## Validation Architecture

Backend command analog: cd backend; pytest tests/unit/narrative_memory
tests/integration/narrative_memory -q. [VERIFIED: repository grep]

| Requirement | Test proof |
|---|---|
| REQ-BOOK-01 | terminal chapter audit and no-pending fixture |
| REQ-BOOK-02 | continuous hierarchy and manifest recompute |
| REQ-BOOK-03 | injected chapter failure scopes downstream and reports calls/tokens/cost/cache |
| D-04 | crash/cancel/resume/idempotence/budget |
| D-07 | negative pointer/cutover tests |

Wave 0 gaps: real long-book fixture, failure injection matrix, aggregate browser progress
fixture are not verified present. [VERIFIED: repository grep]

## Security Domain

V4 owner/version, V5 strict job inputs, V6 checksum/lineage. Threats: cross-owner reuse,
stale cache, source drift, candidate-to-active promotion. [VERIFIED: repository grep]

## Sources and confidence

Current narrative_memory services/tests, architecture docs and roadmap. [VERIFIED: repository grep]
Stack/architecture/pitfalls HIGH. Valid until 2026-09-01.
