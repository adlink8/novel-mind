# Phase 8: Versioned Novel Analysis Orchestration and Interactive Timeline - Specification

**Created:** 2026-07-13
**Ambiguity score:** 0.07 (gate: <= 0.20)
**Requirements:** 10 locked

## Goal

Users can select a novel in a global analysis workspace, start or resume a versioned background analysis, and inspect an evidence-backed, spoiler-aware horizontal timeline while chapter results arrive progressively.

## Background

Phase 07 provides chapter -> scene -> evidence hierarchy, source offsets, active build lineage and retrieval fallback. `AnalysisResult` stores unversioned JSON rows and `analysis_service.py` can produce structural intermediates with optional LLM enrichment. `TimelineEvent` has only one float sort order and free-text time/characters fields. `/api/timeline` queries return empty arrays and extraction/edit/delete return HTTP 501. No durable analysis job, active result version, mixed-precision story time, spoiler gate or timeline workspace exists.

## Requirements

1. **Durable staged analysis jobs**: Timeline analysis runs as a persistent, owner-scoped job with lease, checkpoint, cancel, resume, progress and terminal states.
   - Current: Analysis executes synchronously inside one request and changes only `Novel.status`.
   - Target: Chapter extraction commits independently; a new worker resumes after process restart without repeating completed model calls.
   - Acceptance: A forced restart after N chapters resumes at N+1, preserves published chapter results, and issues zero duplicate calls for completed stage keys.

2. **Versioned immutable outputs**: Every run binds source snapshot, hierarchy build, prompt/schema/model and normalized configuration lineage.
   - Current: `AnalysisResult` rows have no immutable version or active pointer.
   - Target: Candidate versions are immutable; only a validated version becomes active; previous versions remain queryable and rollback restores the exact prior pointer.
   - Acceptance: Reanalysis creates a distinct version, failed candidates do not move active, and rollback restores byte-identical active manifests.

3. **Staged trigger policy**: Import performs only deterministic hierarchy and low-cost structural preparation; first entry to analysis starts deep timeline work.
   - Current: Analysis is manually triggered from a novel card and may call LLM synchronously.
   - Target: The global analysis page creates/resumes the deep job idempotently; repeat page entry does not create duplicate jobs.
   - Acceptance: Import causes zero timeline LLM calls; two simultaneous first-entry requests produce one active run.

4. **Dual sequence and mixed time precision**: Events retain narrative order and inferred story order with explicit time precision.
   - Current: `TimelineEvent.sort_order` represents one ambiguous order and `time_reference` is free text.
   - Target: Events support exact, relative, fuzzy and unknown time values, normalized ordering constraints, inference reason and confidence without inventing exact dates.
   - Acceptance: Fixtures covering flashback, "three years earlier", "next morning", fuzzy childhood and unknown dates sort correctly in both modes.

5. **Evidence-bound automatic publication**: Extracted events publish automatically but cannot exist without valid chapter/source evidence and model lineage.
   - Current: Knowledge projection may write events with marker text but timeline API has no evidence contract.
   - Target: Every event links to evidence refs/offsets, confidence and extraction lineage; user edits create an override layer protected from reanalysis.
   - Acceptance: Missing or mismatched evidence fails closed; reanalysis updates machine fields but leaves user-overridden fields unchanged.

6. **Character filters and causal links**: One timeline can filter by involved character and optionally render causal relations.
   - Current: Characters are a serialized string and no causal edge model/API exists.
   - Target: Structured participants and typed `causes`, `triggers`, `responds_to`, `blocks` edges are owner/novel isolated and evidence-backed.
   - Acceptance: Filtering returns only participating events; disabling causal overlay returns no edges; invalid cross-novel edges are rejected.

7. **Progressive global timeline workspace**: `/analysis` selects a novel and presents a zoomable horizontal timeline as chapter work completes.
   - Current: `/search` is the navigation entry and no timeline page exists.
   - Target: Navigation points to `/analysis`; the page exposes progress, partial events, status/error, last update, ordering toggle, character filter and causal overlay. `/search` remains available as a lower-level evidence lookup route.
   - Acceptance: Desktop and 390px browser tests cover empty, running, partial, completed and failed states without overlap or unreadable labels.

8. **Spoiler protection**: Timeline results default to the user's persisted reading position.
   - Current: Reading progress exists but timeline endpoints do not consume it.
   - Target: API and UI exclude future chapter events by default; explicit full-book mode is required to reveal them and is stored per novel.
   - Acceptance: A user at chapter 5 cannot receive chapter 6+ events in default responses; explicit full mode returns them.

9. **Tiered model routing and budget**: Chapter extraction and cross-chapter reconciliation use different model quality tiers under a per-novel budget.
   - Current: `ai_router` has task tiers but timeline has no budget ledger or cache identity.
   - Target: Chapter extraction uses balanced/low-cost routing; reconciliation uses quality routing; token, cost and call ceilings pause deterministically while preserving checkpoints.
   - Acceptance: Budget exhaustion makes zero further model calls, reports `paused_budget`, and resumes after an authorized budget/model change.

10. **Novel-only phase boundary**: Phase 08 does not expose intermediate summaries or build adjacent products.
   - Current: Historical fixtures/profiles and placeholder character endpoints still exist; analysis intermediates are tempting UI categories.
   - Target: This phase supports fiction timeline only. Plot beats, summaries, themes and pace remain backend inputs. History support is removed from new contracts. Relationship graph, reading AI and clue tracking remain later phases.
   - Acceptance: No Phase 08 frontend route exposes six intermediate analysis types, and no plan claims relationship graph, reading chat or clue lifecycle delivery.

## Boundaries

**In scope:**
- Persistent analysis run, checkpoint, budget and version/active-pointer contracts
- Chapter timeline extraction plus cross-chapter ordering/reconciliation
- Evidence-bound timeline event, participant and causal-edge contracts
- Automatic publication with protected user overrides
- Global `/analysis` novel selector and horizontal timeline
- Progressive results, dual ordering, character filter, causal overlay and spoiler gate
- Offline fixtures, integration tests, browser tests and live-model qualification path

**Out of scope:**
- Person relationship graph - Phase 09
- Reader selection AI and multi-session chat - Phase 10
- Clue/foreshadow lifecycle - Phase 11
- Historical corpus/domain profiles - removed from product scope
- Exposing plot summary, beats, theme, pace or chapter summaries as top-level frontend modes - backend intermediates only
- Streaming token-by-token prose analysis - progressive structured chapter results are sufficient

## Constraints

- PostgreSQL is the authoritative job, version, event, override and active-pointer store.
- LLM outputs strict schemas only; scripts enforce evidence, order, budget, state and persistence.
- Existing Phase 04 accepted judgments and Phase 07 hierarchy/evidence are reused rather than recomputed.
- `/search` remains available for evidence lookup even after navigation changes.
- Existing owner isolation and async `AsyncSession` patterns are mandatory.

## Acceptance Criteria

- [ ] Restart/resume and concurrent first-entry tests prove idempotent durable execution.
- [ ] Candidate/active/rollback tests prove immutable version lifecycle.
- [ ] Timeline fixtures pass both story-order and narrative-order assertions across four time precision classes.
- [ ] Every published event and causal edge validates against source evidence and owner/novel scope.
- [ ] User edits survive reanalysis and are visibly marked as manual corrections.
- [ ] Default API responses never expose events after reading progress.
- [ ] Budget exhaustion pauses without additional model calls and resumes from checkpoint.
- [ ] `/analysis` passes desktop and 390px Playwright states for empty/running/partial/completed/failed.
- [ ] Relationship graph, reading AI, clue tracking and history support are absent from Phase 08 deliverables.

## Ambiguity Report

| Dimension | Score | Min | Status | Notes |
|---|---:|---:|---|---|
| Goal Clarity | 0.98 | 0.75 | met | Global timeline vertical slice is explicit |
| Boundary Clarity | 0.98 | 0.70 | met | Phases 09-11 and history explicitly excluded |
| Constraint Clarity | 0.88 | 0.65 | met | Evidence, persistence, budget and ownership locked |
| Acceptance Criteria | 0.90 | 0.70 | met | Nine pass/fail gates |
| **Ambiguity** | **0.07** | **<=0.20** | **met** | Ready for context and planning |

## Interview Log

| Round | Perspective | Question summary | Decision locked |
|---|---|---|---|
| 1 | Researcher | What belongs in frontend vs backend? | Intermediates remain backend; frontend is time/relations/clues |
| 2 | Simplifier | First vertical slice and navigation? | Global analysis page; timeline first |
| 3 | Boundary Keeper | Adjacent capabilities? | Relations, reader AI and clues deferred; history removed |
| 4 | Failure Analyst | Ordering, spoilers and incorrect LLM facts? | Dual order, spoiler gate, evidence and protected manual overrides |
| 5 | Seed Closer | Jobs, versions and cost? | Progressive durable jobs, immutable versions, tiered budget |
| 6 | Seed Closer | Timeline interaction? | Horizontal zoom, character filter, optional causal overlay, mixed precision |

---

*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Spec created: 2026-07-13*
*Next step: gsd-discuss-phase 8 - implementation decisions*
