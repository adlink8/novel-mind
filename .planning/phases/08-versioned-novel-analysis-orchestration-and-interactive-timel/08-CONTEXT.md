# Phase 8: Versioned Novel Analysis Orchestration and Interactive Timeline - Context

**Gathered:** 2026-07-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver one complete fiction-timeline vertical slice: persistent/versioned analysis orchestration, evidence-backed event extraction and reconciliation, and the global spoiler-aware interactive timeline. Do not implement the later relationship graph, reader AI or clue tracker.

</domain>

<spec_lock>
## Requirements (locked via SPEC.md)

**10 requirements are locked.** See `08-SPEC.md` for full requirements, boundaries and acceptance criteria.

Downstream agents MUST read `08-SPEC.md` before planning or implementing.

**In scope:** durable/versioned jobs, mixed-precision dual-order timeline data, evidence/participants/causality, automatic publication with manual overrides, global progressive timeline UI, spoiler protection and tiered budget.

**Out of scope:** relationship graph, reader AI, clue tracking, historical corpora, and direct UI exposure of analysis intermediates.

</spec_lock>

<decisions>
## Implementation Decisions

### Product surface
- **D-01:** The primary entry is one global `/analysis` workspace that selects a novel; `/search` remains an internal evidence lookup route.
- **D-02:** Phase 08 exposes only the timeline. Plot summaries, beats, character/theme clues, pace/scene length and chapter summaries remain backend intermediates.
- **D-03:** The timeline is a zoomable horizontal axis with a full-book overview and interval zoom; mobile preserves horizontal pan/zoom rather than becoming a different data model.

### Timeline semantics
- **D-04:** One unified timeline defaults to the main plot; selecting a person filters the same events into that person's timeline.
- **D-05:** Story chronology and chapter narrative order are both persisted and switchable.
- **D-06:** Time supports exact, relative, fuzzy and unknown precision. The system must not invent exact dates.
- **D-07:** Causal edges are hidden by default and toggled as an overlay with `causes`, `triggers`, `responds_to` and `blocks` types.

### Publication and corrections
- **D-08:** LLM-extracted timeline events publish automatically; no mandatory review queue is part of Phase 08.
- **D-09:** Every event must retain chapter/source offsets, evidence refs, confidence, extraction model/prompt/schema lineage and creation time.
- **D-10:** User edits form a protected manual override layer; reanalysis cannot overwrite manually corrected fields.
- **D-11:** Analysis versions are immutable candidates. A validated candidate moves the active pointer; old versions remain comparable and rollbackable.

### Execution and cost
- **D-12:** Import runs only deterministic hierarchy and low-cost structural preparation. First entry to analysis idempotently starts deep timeline analysis.
- **D-13:** Results publish chapter by chapter while work continues; progress, partial status, failure and last update are visible.
- **D-14:** Chapter extraction uses the low-cost/balanced model tier; cross-chapter ordering/conflict reconciliation uses the quality tier.
- **D-15:** Per-novel token, cost and call budgets pause deterministically and preserve resumable checkpoints; cache identity includes source/prompt/schema/model lineage.

### Spoiler policy
- **D-16:** Spoiler protection is on by default and filters events after persisted reading progress at the API boundary, not only in the browser.
- **D-17:** Users may explicitly enable full-book analysis per novel; this preference is persisted.
- **D-20:** With no reading progress, the spoiler cutoff is the first chapter only; the API never defaults to full-book visibility.
- **D-21:** A running candidate is displayed separately from the prior active version; their events and aggregates are never merged.
- **D-22:** Unknown provider pricing pauses as `paused_budget`; token/call limits alone cannot substitute for an unprovable cost reservation.

### Scope
- **D-18:** Product scope is fiction only. Do not add new history contracts, prompts, fixtures or UI.
- **D-19:** Person relationship graph is Phase 09; reader selected-text AI is Phase 10; clue/foreshadow tracking is Phase 11.

### the agent's Discretion
- Exact timeline visualization library, provided it supports accessible keyboard interaction, responsive horizontal zoom and deterministic browser tests.
- Exact numeric budget defaults and polling/SSE transport, provided the locked pause/resume and progressive behavior is preserved.
- Internal table/module naming consistent with current SQLAlchemy/Alembic patterns.

</decisions>

<canonical_refs>
## Canonical References

### Locked phase contracts
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-SPEC.md` - falsifiable Phase 08 scope and acceptance gates
- `.planning/REQUIREMENTS.md` - REQ-TIME-01..10 traceability

### Upstream evidence and analysis
- `.planning/phases/04-llm/04-AI-SPEC.md` - LLM semantic judgment and evidence discipline
- `.planning/phases/07-semantic-hierarchical-chunking/07-AI-SPEC.md` - bounded LLM, fallback and hierarchy lineage patterns
- `.planning/phases/07-semantic-hierarchical-chunking/07-VERIFICATION.md` - verified scene/evidence inputs

### Product code
- `backend/app/services/analysis_service.py` - current structural intermediates and optional LLM enrichment
- `backend/app/models/timeline.py` - current insufficient timeline row
- `backend/app/api/timeline.py` - current empty/501 routes
- `backend/app/services/knowledge/projection.py` - accepted judgment to timeline projection pattern
- `backend/app/services/chunking/pg_store.py` - active hierarchy and evidence lookup
- `backend/app/services/ai_router.py` - existing model-tier routing
- `frontend/src/app/novels/[id]/page.tsx` - reading progress behavior
- `frontend/src/components/app-shell.tsx` - navigation entry to replace after timeline is functional

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ChunkHierarchyNode` and `pg_store`: chapter/scene/evidence source truth and offsets.
- `KnowledgeProjectionService`: accepted semantic event projection and entity linkage patterns.
- `ai_router` and `ai_service`: model routing and LiteLLM calls.
- `Novel.reading_progress`: persisted chapter/progress spoiler boundary.
- Phase 06 quality job and Phase 05/07 candidate-pointer patterns: durable lease/checkpoint and immutable promotion precedents.

### Established Patterns
- PostgreSQL/SQLAlchemy is authoritative; vector/LLM outputs are candidates or projections.
- LLM returns strict schemas; scripts own validation, evidence, thresholds, state transitions and writes.
- Owner isolation returns 404 for inaccessible novel-scoped resources.
- Frontend uses Next.js App Router, Axios API adapters, Lucide icons and Playwright desktop/mobile projects.

### Integration Points
- Extend `analysis_results` or introduce dedicated run/version tables without preserving its unversioned semantics as authority.
- Replace `timeline.py` placeholder routes while retaining route prefix compatibility where useful.
- Build `/analysis` only after backend progressive APIs exist; then change AppShell navigation from `/search` to `/analysis`.
- Preserve `/search` for evidence drill-down and citations.

</code_context>

<specifics>
## Specific Ideas

- Timeline mode switch: Story time / Narrative order.
- Person filter modifies the same unified timeline rather than opening a separate page.
- Causal overlay is optional to avoid visual clutter.
- Partial chapter results appear immediately with a clear "analysis still updating" state.
- Default view is bounded by reading progress; full-book reveal requires explicit user action.

</specifics>

<deferred>
## Deferred Ideas

- Phase 09: dynamic person relationship graph with chapter/time slider and evidence side panel.
- Phase 10: reader text-selection toolbar, evidence-backed AI side panel and multiple per-novel conversations.
- Phase 11: automatic/manual clue and foreshadow tracking with five-state lifecycle.
- History support is removed rather than deferred.

</deferred>

---

*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Context gathered: 2026-07-13*
