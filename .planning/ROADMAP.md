# NovelMind GSD Roadmap

> Execution authority: `master`. Snapshot: `01503c2`, 2026-07-31.
> Progress is reported in three independent dimensions:
> `implementation_readiness`, `sample_data_coverage`, `quality_qualification`.

## Execution Rules

1. `.planning/STATE.md` is the single cursor; an old feature branch is never a cursor.
2. Every phase requires `CONTEXT`, `RESEARCH`, one or more `PLAN`, `SUMMARY`,
   `VALIDATION`, and `VERIFICATION` artifacts.
3. Every plan contains `Steps`, `Must-Haves`, `Verification`, and ends with
   **Test, Fix, and Confirm**.
4. `SUMMARY` is written only after implementation; `VERIFICATION` is written only after
   independent evidence. Future phases do not receive placeholder completion artifacts.
5. All final answers and citations resolve to raw source evidence; summaries and world
   models route retrieval but do not replace source authority.
6. NM remains candidate-only. Promotion, active-pointer cutover and production A/B are in
   `999.x` backlog and require explicit authorization.

## Milestones

| Milestone | Phases | implementation_readiness | sample_data_coverage | quality_qualification |
|---|---|---|---|---|
| v1.1 execution baseline | 21–25.1 | partial | partial | blocked by Phase 22 |
| v1.2 trusted novel understanding | 26–29 | planned | planned | planned |
| v1.3 visual narrative | 30–34 | planned | planned | planned |
| v1.4 Canon Fork derivatives | 35–39 | planned | planned | planned |

## Baseline Reconciliation — Phase 21–25.1

| Phase | Status | Evidence / remaining gate |
|---|---|---|
| 21 branch recognition | COMPLETE | branch-delta spike; old branch is evidence only |
| 22 CI/Nightly authority | ACTIVE / BLOCKED-OBSERVATION | gap plans G1–G3; 0/3 scheduled green |
| 23 layer registry and NU/NM boundary | IMPLEMENTED | ADRs, facet read-only contract |
| 24 indexing journal, reconcile, retrieval router | IMPLEMENTED | merged on master; operational qualification remains Phase 29 |
| 25 facet/API/provenance/cost honesty | IMPLEMENTED | master contracts and tests |
| 25.1 Analysis Chat workspace/range anchor | IMPLEMENTED | default chat view and chapter-range wiring |

Phase 22 is the active cursor. Phases 26+ cannot be reported `ACTIVE` until its three-run
gate closes.

---

# v1.2 — Trusted Novel Understanding

## Phase 26 — Question-Driven Retrieval and Evidence

**Goal:** turn a reader/analyst question into a typed retrieval plan, fuse the required
dimensions and materialize source-verified citations.

**Depends on:** Phase 22, Phase 24, Phase 25.1.

### Plans

- **26-01 QueryPlan contract and question parsing**
  - Steps: define intent, scope, spoiler cutoff, dimensions, fallback and answer constraints;
    parse representative reading/analysis questions; persist trace.
  - Must-Haves: deterministic schema validation; unknown intent fails to clarification;
    owner/novel/version/cutoff on every plan.
  - Verification: contract, adversarial prompt and parser fixture tests.
  - Test, Fix, and Confirm: run fixtures, repair ambiguity, freeze schema.
- **26-02 Retrieval adapters and fusion**
  - Steps: adapters for raw text, event/causal, character state-goal-motivation-knowledge,
    relation, timeline, clue, world rule/place/faction/item and NM chapter/arc/global;
    rank/fuse with honest availability.
  - Must-Haves: no empty-success adapter; dimension provenance; deterministic fallback.
  - Verification: per-adapter and fusion tests with missing/partial dimensions.
  - Test, Fix, and Confirm: compare fused results to single-source baselines and fix leakage.
- **26-03 Source lookup, EvidenceRef materialization and manifest freeze**
  - Steps: resolve all candidates to leaf/raw offsets and hashes; freeze retrieval manifest.
  - Must-Haves: stale hash rejection; citation source lookup; immutable lineage.
  - Verification: offset/hash mutation, owner, spoiler and version adversarial tests.
  - Test, Fix, and Confirm: invalidate sources and confirm fail-closed behavior.
- **26-04 Reader/Analysis Chat consumers and QA samples**
  - Steps: wire both consumers to QueryPlan; expose trace/citation level; add frozen samples.
  - Must-Haves: shared retrieval core, distinct anchors; no uncited factual assertion.
  - Verification: API, browser and frozen QA tests.
  - Test, Fix, and Confirm: run desktop/mobile question flows and fix citation jumps.

**Phase Verification:** implementation contracts pass; sample set covers every dimension and
fallback; answer quality is reported separately, not inferred from implementation.

## Phase 27 — World Model and Epistemic Layers

**Goal:** build versioned world-state projections while keeping fact, inference and
interpretation distinct.

**Depends on:** Phase 26.

### Plans

- **27-01 Shared Event Fact and Causal Edge**
  - Steps: canonical event fact, temporal interval and evidence-gated causal edge contracts.
  - Must-Haves: causality is not co-occurrence; every edge cites evidence.
  - Verification: causal false-positive and temporal-conflict fixtures.
  - Test, Fix, and Confirm: review sampled edges and fix unsupported promotion.
- **27-02 Character State, Goal, Motivation and Knowledge**
  - Steps: model state transitions and what each character knows at each cutoff.
  - Must-Haves: POV/spoiler scoped knowledge; contradiction-preserving history.
  - Verification: mistaken-belief, hidden-knowledge and state-transition tests.
  - Test, Fix, and Confirm: replay character histories and repair impossible transitions.
- **27-03 World Entity, Rule, Faction, Place and Item**
  - Steps: typed entities/rules, aliases, membership and spatial/item state.
  - Must-Haves: source lineage; rule exceptions retained; no chat-derived canon.
  - Verification: alias collision, rule exception and ownership tests.
  - Test, Fix, and Confirm: reconcile fixture worlds and fix false merges.
- **27-04 POV, disclosure and epistemic authority**
  - Steps: label claims as `canon_fact`, `probable_inference`, `literary_interpretation`,
    `user_interpretation`; expose disclosure timing.
  - Must-Haves: labels cannot silently upgrade; user interpretation isolated.
  - Verification: authority-transition and spoiler-boundary adversarial tests.
  - Test, Fix, and Confirm: inspect mixed-authority answers and fix label loss.

**Phase Verification:** world projections are queryable and versioned; coverage and
interpretive quality are measured independently.

## Phase 28 — Whole-Book Narrative Memory Convergence

**Goal:** converge every chapter and semantic arc into a candidate-only whole-book model.

**Depends on:** Phase 26–27.

### Plans

- **28-01 Failure classification and recovery**
  - Steps: stable reason codes, checkpoints, resume, isolation and cost ledger.
  - Must-Haves: no silent pending; no whole-book restart for one chapter.
  - Verification: crash/retry/budget/provider tests.
  - Test, Fix, and Confirm: inject failures at every stage and verify recovery.
- **28-02 Chapter State terminal convergence**
  - Steps: process all chapters; require final `completed|isolated|blocked`.
  - Must-Haves: manifest recomputable from DB; source snapshot frozen.
  - Verification: chapter coverage and terminal-state audit.
  - Test, Fix, and Confirm: rerun partial books and confirm idempotence.
- **28-03 Semantic Story Arc, Volume and Global**
  - Steps: infer evidence-backed boundaries; build arc/volume/global candidates.
  - Must-Haves: continuous coverage; boundary uncertainty preserved.
  - Verification: gap/overlap/boundary and hierarchy lineage tests.
  - Test, Fix, and Confirm: compare semantic arcs with chapter-only fallback.
- **28-04 Cross-dimension closure and one-click analysis**
  - Steps: converge timeline, relation, clue, character and world dimensions; expose aggregate
    progress and resume.
  - Must-Haves: dimension-specific failures visible; no active pointer/cutover.
  - Verification: full-analysis orchestration, progress and recovery tests.
  - Test, Fix, and Confirm: run one long-book candidate end to end and reconcile counts.

**Phase Verification:** all chapters have terminal states, semantic hierarchy is continuous,
cross-dimension manifests agree, and candidate-only invariants hold.

## Phase 29 — Quality Qualification and v1.2 Closure

**Goal:** prove that question-driven understanding is useful, faithful and usable.

**Depends on:** Phase 26–28.

### Plans

- **29-01 Reading QA gold set**
  - Steps: freeze local, cross-chapter, global, causal, character-knowledge, world-rule,
    no-answer and spoiler questions.
  - Must-Haves: source answers and cutoff labels; dataset/version fingerprint.
  - Verification: curator agreement and leakage audit.
  - Test, Fix, and Confirm: challenge ambiguous samples and repair the rubric.
- **29-02 Retrieval, citation and answer evaluation**
  - Steps: measure retrieval, citation correctness, faithfulness, answer relevance,
    latency/cost and abstention.
  - Must-Haves: bucket-level metrics; blocked is a valid verdict.
  - Verification: frozen evaluation and regression report.
  - Test, Fix, and Confirm: inspect worst cases and rerun after fixes.
- **29-03 Browser UAT**
  - Steps: Reader/Analysis Chat, citation jump, evidence panel, partial/failure states.
  - Must-Haves: desktop/mobile; accessible keyboard/focus; no spoiler metadata leak.
  - Verification: real Playwright and human spot-check.
  - Test, Fix, and Confirm: reproduce each UAT defect and confirm its original path.
- **29-04 v1.2 audit**
  - Steps: audit three status dimensions and unresolved risks.
  - Must-Haves: no single completion percentage; evidence links for every verdict.
  - Verification: independent GSD milestone audit.
  - Test, Fix, and Confirm: reconcile audit claims with live code/data/results.

---

# v1.3 — Visual Narrative

## Phase 30 — Visual Bible

**Goal:** create a versioned, evidence-linked visual identity system for characters, places,
factions, items and style.

**Plans:** 30-01 visual entity schema and evidence; 30-02 character/place/item sheets;
30-03 style/negative constraints and reference assets; 30-04 Visual Bible review/versioning.

**Must-Haves:** canon vs interpretation labels; source/cutoff lineage; reusable IDs; no
generated asset silently becomes canon.

**Verification:** schema, consistency, rights/provenance, browser review; **Test, Fix, and
Confirm** after each plan with frozen entity fixtures.

## Phase 31 — Key Scene Detection

**Goal:** identify illustration-worthy scenes without reducing importance to embedding
similarity.

**Plans:** 31-01 scene boundary/candidate contract; 31-02 narrative salience and diversity
ranking; 31-03 human review and frozen key-scene set.

**Must-Haves:** evidence range, cast/place/time/POV, spoiler cutoff, diversity and reasons.

**Verification:** precision/diversity/coverage review; **Test, Fix, and Confirm** against
high-action, quiet-emotional and visually ambiguous scenes.

## Phase 32 — Scene Spec and Prompt Compiler

**Goal:** compile evidence and Visual Bible constraints into provider-neutral Scene Specs
and provider-specific prompts.

**Plans:** 32-01 Scene Spec schema; 32-02 evidence-to-spec compiler; 32-03 provider prompt
adapters; 32-04 validation, safety and prompt preview.

**Must-Haves:** deterministic lineage, character/location continuity, negative constraints,
prompt version and no unsupported detail disguised as canon.

**Verification:** golden compiler fixtures and adapter contract tests; **Test, Fix, and
Confirm** via prompt diffs and adversarial unsupported details.

## Phase 33 — Illustration Generation and Consistency

**Goal:** generate reviewable illustration candidates with durable job, cost and consistency
evidence.

**Plans:** 33-01 provider/job/budget contract; 33-02 generation and asset storage;
33-03 identity/style consistency scoring; 33-04 retry, compare and approval workflow.

**Must-Haves:** idempotent jobs, immutable source/prompt/model lineage, explicit failures,
human approval and provider-neutral API.

**Verification:** mocked provider, optional hard-budget live canary and consistency set;
**Test, Fix, and Confirm** failure/retry/duplicate and identity drift.

## Phase 34 — In-Text Anchors, Reader and Export

**Goal:** place approved illustrations at stable text anchors and preserve them in reading and
exports.

**Plans:** 34-01 source anchor/version contract; 34-02 responsive reader presentation;
34-03 anchor repair after text/version changes; 34-04 Markdown/EPUB export and UAT.

**Must-Haves:** no input/progress overlap; accessible captions; anchor hash verification;
graceful missing asset; export parity.

**Verification:** desktop/mobile/long-page/page-mode tests and export inspection; **Test,
Fix, and Confirm** on changed offsets and unavailable images.

---

# v1.4 — Canon Fork and Constrained Derivatives

## Phase 35 — Triple Knowledge Spaces and Canon Fork

**Goal:** enforce independent Original Canon, User Interpretation and Fanfiction Canon
authorities.

**Plans:** 35-01 namespace/authority contracts; 35-02 Canon Fork snapshot/cutoff;
35-03 retrieval and citation isolation; 35-04 negative contamination tests.

**Must-Haves:** owner/version/cutoff lineage; original index is read-only; no derivative data
in original evaluation or facet production.

**Verification:** cross-space adversarial tests; **Test, Fix, and Confirm** with deliberate
contamination attempts.

## Phase 36 — Derivative Project and Editor

**Goal:** provide owner-scoped derivative projects, plans, chapters and versioned editing.

**Plans:** 36-01 project/domain CRUD; 36-02 chapter plan and Markdown editor; 36-03 autosave,
history, diff and rollback; 36-04 editor browser UAT.

**Must-Haves:** owner isolation, optimistic concurrency, recoverable drafts and explicit
Canon Fork selection.

**Verification:** API/concurrency/recovery/browser tests; **Test, Fix, and Confirm** crash,
conflict and rollback paths.

## Phase 37 — Constrained Generation

**Goal:** generate continuation/rewrites against an auditable story-state package.

**Plans:** 37-01 context package compiler; 37-02 constrained draft generation;
37-03 contradiction/character/timeline/clue checks; 37-04 explicit divergence override.

**Must-Haves:** cutoff state, evidence refs, unresolved clues, world rules and user intent;
no silent write-back to original canon.

**Verification:** frozen continuation set and contradiction tests; **Test, Fix, and Confirm**
with intentional canon violations and allowed divergences.

## Phase 38 — Derivative Visual Consistency

**Goal:** extend the Visual Bible and illustration pipeline without contaminating original
visual authority.

**Plans:** 38-01 forked Visual Bible; 38-02 derivative Scene Specs; 38-03 generation and
cross-chapter consistency; 38-04 review/version lineage.

**Must-Haves:** original references remain immutable; divergence is explicit; generated
assets belong to derivative namespace.

**Verification:** namespace and identity consistency tests; **Test, Fix, and Confirm** mixed
original/derivative asset scenarios.

## Phase 39 — Export, UAT and v1.4 Audit

**Goal:** ship a reproducible derivative package and independently audit the complete flow.

**Plans:** 39-01 Markdown/EPUB derivative export; 39-02 manifest/assets/citation package;
39-03 end-to-end browser UAT; 39-04 security, quality and milestone audit.

**Must-Haves:** export-version parity, asset provenance, owner isolation, no original-space
mutation and three-dimensional status report.

**Verification:** import/export round trip, security tests, browser workflow and independent
audit; **Test, Fix, and Confirm** every failed end-to-end checkpoint.

---

## Supersession and Backlog Map

| Old roadmap item | New owner |
|---|---|
| old Phase 26 NM whole-book build | Phase 28 |
| old Phase 27 semantic closure | Phase 27–28 |
| old Phase 28 quality evidence | Phase 29 |
| old Phase 29 consumer closure | Phase 29 |
| old Phase 30 NM promotion/cutover | `999.x` backlog; explicit authorization + frozen A/B |
| old Phase 31 triple spaces | Phase 35 |
| old Phase 32 editor | Phase 36 |
| old Phase 33 constrained continuation | Phase 37 |
| old Phase 34 export | Phase 34 and Phase 39 |

## Next

Execute Phase 22 G1–G3. After three consecutive scheduled green observations, begin
Phase 26 planning artifacts from the contracts above.
