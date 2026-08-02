# Phase 29: Reading QA Quality Gate — Research

**Researched:** 2026-08-01
**Domain:** frozen QA datasets, retrieval/citation evaluation, browser qualification
**Confidence:** HIGH for evaluation infrastructure; MEDIUM for the new gold rubric

## User Constraints

D-01..D-06 from 29-CONTEXT.md are binding. Quality qualification is separate from
implementation readiness and sample coverage; Phase 22 remains 0/3 and blocked.

## Summary

Reuse EvalDataset/EvalRun/EvalResult and narrative-memory qualification metrics/fixtures,
extending the fixture taxonomy to Issue #29 buckets. Reports must be reproducible and
blocked is valid. [VERIFIED: repository grep]

Compare QueryPlan/NM candidates against leaf baseline with identical source, cutoff and
budget. Citation correctness and faithfulness remain separate from recall; browser UAT is
separate evidence, not hidden in one score.

## Architectural Responsibility Map

| Capability | Primary tier | Secondary | Rationale |
|---|---|---|---|
| Gold data/versioning | Database / Storage | API | immutable fingerprint |
| Runner/metrics | API / Backend | Database | deterministic reports |
| Browser UAT | Browser / Client | API | real path/accessibility |
| Verdict/audit | API / Backend | — | blocked/qualified_candidate only |

[VERIFIED: repository grep]

## Standard Stack

No new packages. Use existing eval models/service, narrative-memory qualification services,
pytest, Vitest, Playwright, PostgreSQL and report artifacts. [VERIFIED: repository grep]

## Architecture Patterns

Freeze dataset/source manifest before run. Store per-question bucket, expected answer/leaf
refs/cutoff, retrieved refs, citation validation, faithfulness/relevance labels,
latency/cost/fallback and error. Aggregate by bucket and retain worst cases. Bind every
report to DB fingerprint, dataset, source snapshot, commit, model/prompt/schema/config.
[VERIFIED: repository grep]

## Don't Hand-Roll

Reuse EvalDataset/EvalRun/EvalResult, qualification_fixtures/metrics/verifier/verdict,
reader-chat browser fixtures and existing citation validators. Do not create another QA
store or infer quality from implementation tests. [VERIFIED: repository grep]

## Common Pitfalls

Gold leakage, mixed cutoff, different source snapshots, one aggregate hiding spoiler or
no-answer failures, fluent uncited answers, live-provider dependence, and calling blocked
a pass. Older eval docs reserve some faithfulness/cost fields rather than computing them;
verify before reporting. [VERIFIED: repository grep]

## Code Examples

Report pattern: header has db_fingerprint, dataset_version, source_snapshot, commit,
model/prompt/schema and budget; each bucket has metrics and blocked reasons. Verdict permits
only qualified_candidate or blocked. [VERIFIED: repository grep]

## Validation Architecture

Backend pytest, frontend Vitest and Playwright. [VERIFIED: repository grep]

| Requirement | Test/UAT | Command |
|---|---|---|
| D-01 | fixture schema, curator agreement, leakage audit | cd backend; pytest tests/unit/qualification/test_gold_set.py -q |
| D-02/04 | lineage mismatch and baseline parity | cd backend; pytest tests/adversarial/test_qualification_lineage.py -q |
| D-03/05 | bucket metrics and verdict | cd backend; pytest tests/integration/qualification/test_report.py -q |
| D-06 | browser chat/citation/failure | cd frontend; npx playwright test e2e/reader-chat*.spec.ts |

Nyquist: fixture tests per task; deterministic report per wave; full bucket/lineage/
negative/browser gate before audit. Human UAT samples worst cases on desktop/390px with
keyboard/focus and reduced-motion checks.

Wave 0 gaps: exact bucketed gold set, curator rubric, leakage audit, and report-binding
fixture are not verified present. [VERIFIED: repository grep]

## Security Domain

V2/V3/V4 owner-isolated datasets/reports; V5 question/answer schema; V6 hashes/fingerprints.
Threats are cross-owner access, spoiler leakage, lineage spoofing, and metric tampering.
[VERIFIED: repository grep]

## Sources and confidence

Current eval models/service, narrative-memory qualification tree, reader-chat e2e/tests,
architecture docs and roadmap. [VERIFIED: repository grep] Stack/architecture HIGH; gold
rubric MEDIUM. Valid until 2026-09-01.
