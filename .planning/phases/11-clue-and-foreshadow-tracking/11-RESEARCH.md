# Phase 11 Research — Clue and Foreshadow Tracking

**Research date:** 2026-07-13  
**Discovery level:** Level 0 — existing project patterns only  
**External dependencies:** none  
**Confidence:** HIGH for repository patterns; MEDIUM for future Phase 09/10 source shapes because they are defined but unimplemented

## Executive Finding

Phase 11 does not need a new framework. The repository already contains all required primitives:

1. Phase 04 separates deterministic recall/evidence/gates from LLM semantic judgment.
2. Phase 07 provides immutable hierarchy lineage and exact source offsets.
3. Phase 08 provides durable jobs, model-call audit, budget reservation, immutable candidate versions, protected overrides, visible-set-first spoiler filtering, a global analysis workspace, real browser qualification and a fail-closed release gate.

The correct implementation is a clue-owned domain package that composes those patterns. It must not reuse timeline tables as clue truth, because clue lifecycle, cue/payoff evidence roles and manual dispositions are different contracts. It also must not create direct foreign-key dependencies on Phase 09/10, because those implementations do not exist.

## Local Evidence Reviewed

| Area | Current artifact | Reusable pattern |
|---|---|---|
| Knowledge recall | services/knowledge/candidates.py | deterministic candidate drafts with recall_signals |
| Evidence package | services/knowledge/evidence.py; services/timeline/evidence.py | bounded allowed IDs, offsets, hashes and package checksum |
| LLM boundary | services/knowledge/llm_judge.py | strict semantic judgment and audited failures |
| Deterministic gates | services/knowledge/gates.py | schema/evidence/threshold/conflict routing |
| Hierarchy source | models/chunk_build.py; services/chunking/pg_store.py | active build, chapter/scene/evidence lineage |
| Version/runtime | models/analysis.py; services/timeline/worker.py | durable lease/checkpoint/attempt/budget/version pattern |
| Override | services/timeline/overrides.py | append-only field overrides and evidence-identity relink |
| Spoiler query | services/timeline/query.py | cutoff before overrides, counts, filters and edges |
| UI | frontend/src/app/analysis/page.tsx | single global novel selector and version-isolated analysis view |
| Release | scripts/run_timeline_qualification.py | fixed command execution + independent PostgreSQL authority |

## Architectural Responsibility Map

| Responsibility | Owner | Must not own |
|---|---|---|
| Candidate recall | deterministic clue candidate service | state transition or fact publication |
| Cross-chapter package | deterministic clue evidence service | semantic conclusion |
| Semantic candidate/judgment | LLM gateway with strict schema | writes, thresholds, versions, links, budget |
| Schema/evidence/threshold/conflict | deterministic gate service | prompt interpretation |
| Lifecycle transition | append-only lifecycle service | overwrite/delete prior events |
| Version/pointer/rollback | PostgreSQL version service | LLM decision |
| Human confirmation/rejection/note/link adjustment | append-only override service | mutate machine version |
| Character/timeline/relation refs | typed link validator | update relation graph/timeline/chat |
| Spoiler visibility | owner-scoped query service | browser-only hiding |
| Cost/retry/cache | durable worker/model-call boundary | provider hidden retry/fallback |
| Product rendering | /analysis clue components | expose analysis intermediates |
| Release authority | qualification CLI + PostgreSQL observer | accept caller-supplied success |

## Recommended Domain Model

Use clue-owned tables and strict schemas:

- ClueAnalysisRun: owner/novel/version, lease, checkpoint, progress, cancellation and frozen budget counters.
- ClueAnalysisVersion: source/hierarchy/timeline/prompt/schema/model/config/fixture policy lineage, immutable manifest and parent.
- MachineClue: immutable version-scoped logical clue with candidate package/audit metadata.
- ClueEvidenceRef: role cue|reinforcement|payoff|disposition, chapter/evidence ID, offsets/hash and source lineage.
- ClueLifecycleEvent: append-only from_status/to_status, evidence set, actor source, reason and version.
- ClueLink: exactly one character/timeline/relationship-observation target plus supporting evidence and validation status.
- ClueOverride: confirm/reject/annotation/link adjustment with supersedes chain and needs_relink.
- ClueModelCallAttempt and ClueBudgetReservation: exact stage identity, request/response hashes, usage/cost/latency and fail-closed status.
- ClueActivePointer and CluePointerJournal: expected-revision CAS, compare and rollback.

The current state is derived by replaying lifecycle events plus valid human overrides. No mutable current_status column may be authoritative; if a denormalized value exists for query speed, tests must prove it equals replay.

## Lifecycle Gate Design

The hardest constraint is paid_off correctness. A semantically similar later passage is not a payoff. The script must prove:

1. cue evidence exists and passed owner/novel/build/offset/hash validation;
2. payoff evidence is distinct and later in narrative chapter/source order;
3. the LLM judgment references both allowed evidence IDs and classifies their semantic relation;
4. threshold/conflict gates pass;
5. no active human rejection protects the clue;
6. the append-only transition starts from reinforced.

Dismissal is a terminal disposition available from candidate, active or reinforced. Confirmation maps candidate to active. Repeated reinforcement is allowed only with evidence not already consumed by prior lifecycle events.

## Candidate Recall Strategy

Recall should combine deterministic signals without promoting any one signal:

- repeated entity/object/phrase anchors across chapters;
- Phase 07 scene/evidence lexical/BM25 and vector candidates;
- narrative distance and order;
- Phase 08 timeline participant/event references;
- optional versioned relationship-observation references;

The candidate package should contain a bounded early window and one or more later windows, stable IDs, offsets/hashes, source/hierarchy/timeline versions, recall reason codes and allowed evidence IDs. Chat text, selections, citations, messages, manifests and suggestion candidates are excluded from clue recall and evidence.

## Phase 09 Compatibility

Define protocols, not implementations:

- VersionedRelationshipObservationSource.resolve(owner_id, novel_id, observation_ref, version_ref) → evidence-bound reference or unavailable.

The source binds only to the completed Phase 09 public reader. If unavailable, it records `source_unavailable` and emits no relationship-derived signal; it never creates synthetic rows, calls future APIs, or weakens clue qualification. Phase 10 is deliberately not a source integration.

## Spoiler-Safe Projection

Follow Phase 08 visible-set-first order:

1. resolve owner/novel and selected clue version;
2. resolve cutoff from Novel.reading_progress;
3. accept full-book only when timeline_full_book is already true;
4. filter evidence/lifecycle events by chapter/source cutoff;
5. derive the visible current state from the filtered log;
6. derive counts, status/person filters, links, evidence panels and payoff chains;
7. overlay only overrides attached to already-visible clue IDs.

This prevents paid_off, hidden evidence count, people options or link metadata from leaking future chapters.

## UI Integration

Retain one /analysis page and one novel selector. Add an internal workbench switch:

- Timeline
- Clues & foreshadowing

The clue view owns a horizontal narrative band, state/person controls, keyboard list, evidence drawer and payoff chain. It reuses the page's existing fullBook state and confirmation dialog. It does not add navigation or summary categories.

## Verification Strategy

### Deterministic and PostgreSQL

- transition graph and replay invariants;
- evidence scope/order/hash rules;
- concurrent start/lease/restart/exact cache;
- budget reserve before provider call;
- immutable version, stale CAS, rollback;
- override supersession and ambiguous relink;
- owner/novel/source-version isolation.

### Semantic fixtures

Use fiction-only frozen examples. Keep dev and frozen sets separate. Report candidate recall and accepted lifecycle quality separately so broad recall cannot hide false publication.

### Adversarial

- recurring motif with no payoff;
- similar language describing different objects/events;
- payoff preceding cue;
- same-chapter repetition;
- forged evidence ID/offset/hash;
- prompt injection in novel text;
- chat assertion without primary citation;
- relationship observation without valid evidence/version;
- cross-owner/novel/version link;
- future payoff hidden by cutoff;
- reanalysis attempting to override human dismissal;
- multiple new-version matches for one override.

### Operational and release

The release report binds every relevant hash and reports null metrics when live dependencies are blocked. The release CLI owns fixed commands and performs a fresh PostgreSQL authority read, following Phase 08 rather than trusting a self-reported JSON success.

## Package Legitimacy Audit

No npm, pip or cargo package installation is planned. Existing Pydantic, SQLAlchemy, LiteLLM, ECharts, Vitest, Playwright and pytest dependencies are sufficient.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Similarity is treated as payoff | paid_off requires distinct early/later evidence, temporal order and deterministic gate |
| Future state leaks through counts/filter values | visible-set-first derivation |
| Reanalysis erases manual decision | separate append-only override journal and needs_relink |
| Phase 09 relationship source unavailable | record `source_unavailable`; primary Phase 07/08 evidence may continue, but no relationship-derived signal is silently emitted |
| Duplicate provider cost after restart | persisted stage key, pre-call reservation and exact-cache audit |
| Timeline/clue job uniqueness conflicts | clue-owned run/version tables |
| Dirty existing timeline/frontend API changes collide | do not modify timeline worker/API; use dedicated frontend clue-api module |

## Pre-Mortem

1. **Failure:** paid_off precision is green only because fixture lacks hard negatives.  
   **Mitigation:** frozen fixture requires no-payoff motifs, similar-but-unrelated passages and payoff-before-cue adversarial cases with zero critical false paid_off.

2. **Failure:** API hides event rows but leaks future state through counts or filters.  
   **Mitigation:** validation explicitly tests every derived field under cutoff and compares default/full-book responses.

3. **Failure:** Phase 09/10 references become accidental hard dependencies.  
   **Mitigation:** CI proves a missing/unavailable Phase 09 public reader is explicit `source_unavailable`, while primary Phase 07/08 evidence still works. The source protocol imports no relationship ORM or chat implementation.

## Research Conclusion

Proceed with five sequential waves. No phase split or external research is required. The irreversible decisions—append-only lifecycle, clue-owned authority tables and Phase 08 full-book preference reuse—are locked before implementation.
