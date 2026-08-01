# NovelMind GSD Project

## Core Value

把小说原文转化为可核验、可追溯、可分层解释的理解能力，并在不污染原作权威的
前提下支持视觉化与受约束二创。

## Execution Baseline

- authority branch: `master`
- evidence snapshot: `912ca6b` on 2026-08-01
- single cursor: `.planning/STATE.md`
- old feature branches: evidence only unless a new delta decision explicitly selects code

## Current Milestone

**v1.1 execution baseline reconciliation**. Phase 21 and Phase 23–25.1 are recognized as
implemented on `master`; Phase 22 is still active because scheduled Nightly has not completed
three consecutive green observations.

## Product Direction

0. **Agent runtime foundation (Phase 25.2–25.3):** embedded Novel Agent Runtime with
   controlled domain tools, versioned Skills, lineage-bound Artifacts and Web Approval;
   Pi package governance with allowlist/lock, collision gates and external-evidence
   isolation. The agent orchestrates; deterministic services keep facts, permissions,
   versions and publication authority.
1. **v1.2 trusted novel understanding (Phase 26–29):** question-driven QueryPlan,
   multi-dimensional retrieval, source citations, epistemic world model, whole-book NM and
   reading QA.
2. **v1.3 visual narrative (Phase 30–34):** Visual Bible, key-scene detection, Scene Spec,
   prompt compilation, consistent illustration generation, in-text anchors and export.
3. **v1.4 Canon Fork derivatives (Phase 35–39):** isolated knowledge spaces, derivative
   editor, constrained generation, derivative visual consistency and audited export.

## Authority Boundaries

- Raw source evidence is the final factual authority.
- NM/world models route and organize; they do not replace leaf/raw citation.
- `canon_fact`, `probable_inference`, `literary_interpretation` and
  `user_interpretation` are distinct.
- Original Canon, User Interpretation and Fanfiction Canon remain isolated.
- Generated text/images never silently become Original Canon.
- NM promotion/cutover remains in `999.x` backlog pending explicit authorization.

## Status Contract

Every phase and milestone reports:

- `implementation_readiness`
- `sample_data_coverage`
- `quality_qualification`

These dimensions may disagree. A green build does not imply data coverage or model quality.

## Planning Sources

- `.planning/ROADMAP.md`: phase contracts and dependencies.
- `.planning/STATE.md`: current execution cursor.
- `.planning/REQUIREMENTS.md`: requirement ownership and status.
- `.planning/spikes/phase21-branch-delta/`: branch-delta evidence and decision.
- `.planning/phases/22-ci-nightly-gap-closure/`: active gap plans and validation ledger.
- `IMPLEMENTATION-STATUS.md`: human-facing implementation facts, updated only after verified
  implementation changes.

*Last updated: 2026-08-01.*
