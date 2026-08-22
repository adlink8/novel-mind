# NovelMind GSD Roadmap

> Execution authority: `master`. Snapshot: `29be2fa`, 2026-08-07.
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
7. Phase 25.2+ uses the Novel Agent Runtime contract in
   `.planning/AGENT-RUNTIME-CONTRACT.md`: Pi orchestrates allowlisted Skills/Tools, while
   FastAPI and deterministic validators retain fact, permission, version and publication authority.

## Milestones

| Milestone | Phases | implementation_readiness | sample_data_coverage | quality_qualification |
|---|---|---|---|---|
| v1.1 execution baseline | 21–25.1 | partial | partial | blocked by Phase 22 |
| agent runtime foundation | 25.2–25.3 | **IMPLEMENTED & VERIFIED (2026-08-02)** | partial | blocked by Phase 22 3/3 |
| v1.2 trusted novel understanding | 26–29 | **26/27/28/29 VERIFIED (2026-08-03)** | planned | blocked by Phase 22 3/3 |
| v1.3 visual narrative | 30–34 | **30/31/32/33/34 VERIFIED (2026-08-04)** | planned | blocked by Phase 22 3/3 |
| v1.4 Canon Fork derivatives | 35–39 | **35/36/37/38/39 VERIFIED (2026-08-05)** | planned | blocked by Phase 22 3/3 |
| v1.5 Windows desktop runtime | 41–45 | evidence artifacts exist; state reconciliation required | partial | release/signing gates remain external |
| v1.6 provider protocol unification | 46 | foundation implemented locally; follow-up planned | credentials not supplied | blocked pending live provider qualification |

# Agent Runtime Foundation — Phase 25.2–25.3

> Source: Issue #29 architecture additions and reused Kimi planning artifacts. Planning is
> authorized while Phase 22 is blocked; execution remains fail-closed.

## Phase 25.2 — Embedded Novel Agent Runtime

**Status:** IMPLEMENTED & VERIFIED (2026-08-02, `25.2-VERIFICATION.md` passed at `6988ceb`)

**Goal:** qualify the pinned Pi SDK in a standalone Node Agent Service and establish controlled
Domain Tools, versioned Skills/SkillRuns, lineage-bound Artifacts and the Agent Workspace.

**Depends on:** Phase 22 and Phase 25.1.

### Plans

- **25.2-00 Execution Preflight:** fail closed until Phase 22 has 3/3 scheduled green
  evidence and Phase 25.1 qualification remains valid.
- **25.2-01 Agent Runtime Spike:** pin and exercise Pi session, streaming, cancellation,
  retry, model routing and ResourceLoader allowlist with all default coding tools disabled.
- **25.2-02 Domain Tool Contract:** expose only typed NovelMind read tools with server-side
  owner, cutoff, spoiler, budget, evidence, timeout, output-cap and stable-error enforcement.
- **25.2-03 Skill Runtime and Artifact Contract:** persist SkillVersion, SkillRun, ToolRun,
  Artifact/Revision and NovelAgentProfile; first Skill is `answer-reading-question`.
- **25.2-04 Agent Workspace:** stream run/tool state, Artifact previews, citations,
  cancel/retry/resume and server-authoritative approval status in `/analysis`.
- **25.2-05 Production Agent Service:** wire PostgreSQL session storage, registry loading,
  FastAPI Tool RPC and the production Next.js/FastAPI transport boundary.
- **25.2-06 Spike Decision Record:** consolidate experiment evidence into the authoritative
  go/no-go and storage-seam decision consumed by production plans.
- **25.2-07 First Skill Package:** own the `answer-reading-question` Skill assets, schemas,
  fixtures and Skill-local tests independently from persistence migrations.

**Non-goals:** no shell/filesystem/default coding tools; no Agent database access; no Canon
mutation, active-pointer movement, illustration publication or derivative publication; no
multi-Agent topology.

**Phase Verification:** the eight PLANs and 25.2-VALIDATION prove the machine entry gate,
pinned SDK loading, zero
ambient discovery, typed tools, cancellation-without-Artifact, replay lineage, legal citations
and responsive Agent Workspace behavior. Alembic chain:
`24idxjournal1 → 26agentrun01`.

## Phase 25.3 — Pi Package Compatibility and Governance

**Status:** IMPLEMENTED & VERIFIED (2026-08-02, `25.3-VERIFICATION.md` passed at `e4b1c95`)

**Goal:** qualify controlled Pi ecosystem reuse without allowing packages, MCP or renderers to
bypass NovelMind permissions, evidence, Canon, Artifact or approval boundaries.

**Depends on:** Phase 25.2.

### Plans

- **25.3-00 Governance Preflight:** fail closed until Phase 25.2 has a matching passed
  verification artifact and Phase 22 evidence remains qualified.
- **25.3-01 Package lock and audit:** exact versions/SHAs, lockfile, license/dependency/
  lifecycle review and adopt/fork/pattern-only/reject verdicts.
- **25.3-02 Tool Registry Manifest:** schema hashes, declared permissions and duplicate
  Tool/Skill collision fail-closed before server listen.
- **25.3-03 Isolated MCP Spike:** allowlisted external tools only; output is
  `external_evidence` with `prohibited_from_canon=true`.
- **25.3-04 Permission Policy and Approval Contract:** own the complete action vocabulary,
  authoritative ApprovalRequest schema and permanent Original Canon/active-pointer denies.
- **25.3-05 Web renderer feasibility:** reuse Artifact/Tool rendering patterns only; reject
  browser key storage, IndexedDB authority and replacement ChatPanel/session stores.
- **25.3-06 Web Approval UX and Transport:** own approval SSE frames, confirm/reject UX and
  owner-scoped transport without transferring authority to the browser.

**Non-goals:** no dynamic package installation, ambient global packages, general memory
replacement, direct database access, shell/host filesystem access or multi-Agent execution.

**Phase Verification:** reproducible `npm ci --ignore-scripts`, startup collision/permission
gates, isolated external evidence, complete policy matrix and removable optional packages.
Alembic chain continues `26agentrun01 → 27approval01`.

## Agent Consumption Map — Phase 26–39

| Phase | Agent consumption |
|---|---|
| 26 | `answer-reading-question` → CitedAnswerArtifact |
| 27 | Agent candidates → WorldModel Validator/Gate |
| 28 | `analyze-chapter` / `build-story-arc` → candidate narrative Artifacts |
| 29 | frozen SkillRun + Artifact qualification |
| 30 | `build-visual-bible` |
| 31 | `detect-key-scenes` |
| 32 | `compile-scene-spec` |
| 33 | `illustrate-scene` → proposal-ready IllustrationRevision |
| 34 | Agent proposes; user approves; deterministic service publishes anchor/asset |
| 35 | `create-canon-fork` → approved deterministic fork materialization |
| 36 | Agent is a branch-scoped editor collaborator; deterministic Revision Service applies |
| 37 | `continue-derivative-story` → DraftArtifact + ContinuityReport |
| 38 | `illustrate-derivative-scene` → BranchVisualBibleArtifact + BranchIllustrationRevision |
| 39 | `prepare-export` + deterministic exporter |

### Phase 25.2–39 Planning Artifact Status (2026-08-02)

| Range | CONTEXT / RESEARCH / PATTERNS / VALIDATION | PLAN files | Implementation status |
|---|---:|---:|---|
| Phase 25.2–25.3 | 8/8 | 15 | **VERIFIED 2026-08-02** |
| Phase 26 | 7/7 + shared Agent contract | 7 | **VERIFIED 2026-08-02/03** |
| Phase 27 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-03** |
| Phase 28 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-03** |
| Phase 29 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-03** |
| Phase 30 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-03** |
| Phase 31 | 4/4 + shared Agent contract | 4 | **VERIFIED 2026-08-03** |
| Phase 32 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-03** |
| Phase 33 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-04** |
| Phase 34 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-04** |
| Phase 35 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-04** |
| Phase 36 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-04** |
| Phase 37 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-04** |
| Phase 38 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-05** |
| Phase 39 | 5/5 + shared Agent contract | 5 | **VERIFIED 2026-08-05** |

The corrected portfolio contains 86 plans: 10 reused Kimi foundation plans, five corrective
foundation slices, 57 deterministic/domain/shared-integrity plans and 14 Agent-integration plans.
Phase 25.2–39 are implemented and verified. **The full roadmap through Phase 39 is delivered.**

## Baseline Reconciliation — Phase 21–25.1

| Phase | Status | Evidence / remaining gate |
|---|---|---|
| 21 branch recognition | COMPLETE | branch-delta spike; old branch is evidence only |
| 22 CI/Nightly authority | BLOCKED / DEFERRED BY USER | G2 awaits operator setup; 0/3 scheduled green; only Phase 26–39 planning is waived |
| 23 layer registry and NU/NM boundary | IMPLEMENTED | ADRs, facet read-only contract |
| 24 indexing journal, reconcile, retrieval router | IMPLEMENTED | merged on master; operational qualification remains Phase 29 |
| 25 facet/API/provenance/cost honesty | IMPLEMENTED | master contracts and tests |
| 25.1 Analysis Chat workspace/range anchor | IMPLEMENTED | default chat view and chapter-range wiring |
| 25.2 Embedded Novel Agent Runtime | **VERIFIED 2026-08-02** | `25.2-VERIFICATION.md` passed; agent-service 223p, backend 195p |
| 25.3 Pi Package Compatibility & Governance | **VERIFIED 2026-08-02** | `25.3-VERIFICATION.md` passed; MCP isolation, approval, renderer |
| 26 Question-Driven Retrieval & Evidence | **VERIFIED 2026-08-02/03** | `26-VERIFICATION.md` passed; QueryPlan, adapters/fusion, evidence manifest, consumers, skill integration, structured-output integrity |
| 27 Novel World Model | **VERIFIED 2026-08-03** | `27-VERIFICATION.md` passed; event/causal, epistemic history, entity/rule, authority, propose-world-model-candidates skill |
| 28 Whole-Book Narrative Memory | **VERIFIED 2026-08-03** | `28-VERIFICATION.md` passed; failure/recovery, chapter terminality, arc/volume/global, closure, analyze-chapter/build-story-arc skills |
| 29 Quality Qualification | **VERIFIED 2026-08-03** | `29-VERIFICATION.md` passed; gold set, bucket evaluation, browser UAT, three-dimension audit, evaluate-reading-skill-runs skill |
| 30 Visual Bible | **VERIFIED 2026-08-03** | `30-VERIFICATION.md` passed; candidate contract, evidence materialization, workspace UI, review/versioning, build-visual-bible skill |
| 31 Key Scene Detection | **VERIFIED 2026-08-03** | `31-VERIFICATION.md` passed; scene contract/boundaries, salience+diversity ranking, human review + frozen set, detect-key-scenes skill |
| 32 Scene Spec and Prompt Compiler | **VERIFIED 2026-08-03** | `32-VERIFICATION.md` passed; SceneSpec contract, evidence-to-spec compiler, provider adapters, validation/preview, compile-scene-spec skill |
| 33 Illustration Generation & Consistency | **VERIFIED 2026-08-04** | `33-VERIFICATION.md` passed; job/asset/budget contract, mock generation + storage, consistency scoring, review/compare/approval, illustrate-scene skill |
| 34 In-Text Anchors, Reader and Export | **VERIFIED 2026-08-04** | `34-VERIFICATION.md` passed; anchor contract, responsive reader, anchor repair, Markdown/HTML/EPUB export, propose-illustration-anchor skill + publish |
| 35 Triple Knowledge Spaces and Canon Fork | **VERIFIED 2026-08-04** | `35-VERIFICATION.md` passed; 3-space contract, fork snapshot/cutoff, isolated retrieval/citations, contamination guards, create-canon-fork skill + materializer |
| 36 Derivative Project and Editor | **VERIFIED 2026-08-04** | `36-VERIFICATION.md` passed; project CRUD, chapter plan + Markdown editor, autosave CAS/history/diff/rollback, browser UAT + gate, edit-derivative-story skill |
| 37 Constrained Generation | **VERIFIED 2026-08-04** | `37-VERIFICATION.md` passed; context package, constrained draft generation, consistency gates + BranchSuggestion, divergence override, continue-derivative-story skill |
| 38 Derivative Visual Consistency | **VERIFIED 2026-08-05** | `38-VERIFICATION.md` passed; forked Visual Bible, derivative Scene Specs, candidate assets + cross-chapter consistency, review/version lineage + UI, illustrate-derivative-scene skill |
| 39 Derivative Export Closeout | **VERIFIED 2026-08-05** | `39-VERIFICATION.md` passed; reproducible Markdown/EPUB export, provenance package, browser UAT, prepare-export skill + deterministic preparation/materialization, independent audit gate (no promotion path) |

Phase 22 remains blocked and unverified. The user authorized the Phase 25.2 through Phase 39
execution overrides (2026-08-02/03/04/05); each phase proceeded on the passed upstream
verification artifact. v1.2 milestone (Phases 26–29), v1.3 milestone (Phases 30–34) and the
v1.4 milestone (Phases 35–39) are all implemented and verified. The full roadmap through
Phase 39 is delivered under the overrides.

---

# v1.2 — Trusted Novel Understanding

### Phase 26: Question-Driven Retrieval and Evidence

**Status:** IMPLEMENTED & VERIFIED (2026-08-02/03, `26-VERIFICATION.md` passed at `cb071bc`)

**Goal:** turn a reader/analyst question into a typed retrieval plan, fuse the required
dimensions and materialize source-verified citations.

**Depends on:** Phase 22, Phase 24, Phase 25.1, Phase 25.2, Phase 25.3.

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

- **26-05 Agent integration:** extend `answer-reading-question` over the validated QueryPlan,
  ToolRegistryManifest, Frozen Manifest and CitedAnswerArtifact contracts.
- **26-06 Structured output integrity:** normalize only declared aliases/enums/container shapes,
  strictly revalidate, preserve raw/repaired hashes and warnings, and block any repair that would
  invent evidence, ownership, cutoff, authority, branch, fork or approval state.

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

- **27-05 Agent integration:** generate WorldModelCandidateArtifacts; only deterministic
  Validator/Gate may publish typed facts or inference layers.

## Phase 28 — Whole-Book Narrative Memory Convergence

**Status:** IMPLEMENTED & VERIFIED (2026-08-03, `28-VERIFICATION.md` passed at `a7414c5`)

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

- **28-05 Agent integration:** register `analyze-chapter` and `build-story-arc`; persist
  ChapterAnalysisArtifact/StoryArcArtifact with bounded context, continuity notes, non-indexed
  digests and candidate-only terminal-state validation. Outline/mainline outputs remain
  uncertainty-bearing candidates; progress reuses Agent SSE/Job notification over DB checkpoints.

## Phase 29 — Quality Qualification and v1.2 Closure

**Status:** IMPLEMENTED & VERIFIED (2026-08-03, `29-VERIFICATION.md` passed at `efa4f77`)

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

- **29-05 Agent integration:** evaluate frozen SkillRun, ToolRun, Artifact, Manifest, model,
  source and dataset lineage without re-running mutable Agent state.

# v1.3 — Visual Narrative

## Phase 30 — Visual Bible

**Status:** IMPLEMENTED & VERIFIED (2026-08-03, `30-VERIFICATION.md` passed at `67908b1`)

**Goal:** create a versioned, evidence-linked visual identity system for characters, places,
factions, items and style.

**Plans:** 30-01 visual entity schema and evidence; 30-02 character/place/item sheets;
30-03 style/negative constraints and reference assets; 30-04 Visual Bible review/versioning.

**Must-Haves:** canon vs interpretation labels; source/cutoff lineage; reusable IDs; no
generated asset silently becomes canon.

**Verification:** schema, consistency, rights/provenance, browser review; **Test, Fix, and
Confirm** after each plan with frozen entity fixtures.

- **30-05 Agent integration:** register `build-visual-bible`, produce VisualBibleArtifact and
  require evidence/rights validation plus user approval before accepted visual authority.

## Phase 31 — Key Scene Detection

**Status:** IMPLEMENTED & VERIFIED (2026-08-03, `31-VERIFICATION.md` passed at `fae6b68`)

**Goal:** identify illustration-worthy scenes without reducing importance to embedding
similarity.

**Plans:** 31-01 scene boundary/candidate contract; 31-02 narrative salience and diversity
ranking; 31-03 human review and frozen key-scene set.

**Must-Haves:** evidence range, cast/place/time/POV, spoiler cutoff, diversity and reasons;
speaker/dialogue heuristics expose offsets/confidence/warnings only as candidate signals.

**Verification:** precision/diversity/coverage review; **Test, Fix, and Confirm** against
high-action, quiet-emotional and visually ambiguous scenes.

- **31-04 Agent integration:** register `detect-key-scenes`; keep model proposals separate
  from deterministic score/diversity/spoiler validation and user selection.

## Phase 32 — Scene Spec and Prompt Compiler

**Status:** IMPLEMENTED & VERIFIED (2026-08-03, `32-VERIFICATION.md` passed at `ca06706`)

**Goal:** compile evidence and Visual Bible constraints into provider-neutral Scene Specs
and provider-specific prompts.

**Plans:** 32-01 Scene Spec schema; 32-02 evidence-to-spec compiler; 32-03 provider prompt
adapters; 32-04 validation, safety and prompt preview.

**Must-Haves:** deterministic lineage, character/location continuity, negative constraints,
prompt version and no unsupported detail disguised as canon.

**Verification:** golden compiler fixtures and adapter contract tests; **Test, Fix, and
Confirm** via prompt diffs and adversarial unsupported details.

- **32-05 Agent integration:** register `compile-scene-spec`; consume validated SceneCandidate
  and VisualBible versions and emit SceneSpecArtifact/PromptArtifact without unsupported Canon.

## Phase 33 — Illustration Generation and Consistency

**Status:** IMPLEMENTED & VERIFIED (2026-08-04, `33-VERIFICATION.md` passed at `1b8a658`)

**Goal:** generate reviewable illustration candidates with durable job, cost and consistency
evidence.

**Plans:** 33-01 provider/job/budget contract; 33-02 generation and asset storage;
33-03 identity/style consistency scoring; 33-04 retry, compare and approval workflow.

**Must-Haves:** idempotent jobs, immutable source/prompt/model lineage, explicit failures,
human approval and provider-neutral API.

**Verification:** mocked provider, optional hard-budget live canary and consistency set;
**Test, Fix, and Confirm** failure/retry/duplicate and identity drift.

- **33-05 Agent integration:** register `illustrate-scene`; generation ends at a validated,
  proposal-ready IllustrationRevision and does not publish.

## Phase 34 — In-Text Anchors, Reader and Export

**Status:** IMPLEMENTED & VERIFIED (2026-08-04, `34-VERIFICATION.md` passed at `68819ac`)

**Goal:** place approved illustrations at stable text anchors and preserve them in reading and
exports.

**Plans:** 34-01 source anchor/version contract; 34-02 responsive reader presentation;
34-03 anchor repair after text/version changes; 34-04 Markdown/EPUB export and UAT.

**Must-Haves:** no input/progress overlap; accessible captions; anchor hash verification;
graceful missing asset; export parity.

**Verification:** desktop/mobile/long-page/page-mode tests and export inspection; **Test,
Fix, and Confirm** on changed offsets and unavailable images.

---

- **34-05 Agent integration:** Agent proposes IllustrationAnchorProposal; Web ApprovalRequest
  authorizes deterministic asset/anchor publication and reader/export consumption.

# v1.4 — Canon Fork and Constrained Derivatives

## Phase 35 — Triple Knowledge Spaces and Canon Fork

**Status:** IMPLEMENTED & VERIFIED (2026-08-04, `35-VERIFICATION.md` passed at `5992c25`)

**Goal:** enforce independent Original Canon, User Interpretation and Fanfiction Canon
authorities.

**Plans:** 35-01 namespace/authority contracts; 35-02 Canon Fork snapshot/cutoff;
35-03 retrieval and citation isolation; 35-04 negative contamination tests.

**Must-Haves:** owner/version/cutoff lineage; original index is read-only; no derivative data
in original evaluation or facet production.

**Verification:** cross-space adversarial tests; **Test, Fix, and Confirm** with deliberate
contamination attempts.

- **35-05 Agent integration:** register `create-canon-fork`; approval precedes deterministic
  branch materialization and Original Canon remains immutable.

## Phase 36 — Derivative Project and Editor

**Status:** IMPLEMENTED & VERIFIED (2026-08-04, `36-VERIFICATION.md` passed at `a354a1e`)

**Goal:** provide owner-scoped derivative projects, plans, chapters and versioned editing.

**Plans:** 36-01 project/domain CRUD; 36-02 chapter plan and Markdown editor; 36-03 autosave,
history, diff and rollback; 36-04 editor browser UAT.

**Must-Haves:** owner isolation, optimistic concurrency, recoverable drafts and explicit
Canon Fork selection.

**Verification:** API/concurrency/recovery/browser tests; **Test, Fix, and Confirm** crash,
conflict and rollback paths.

- **36-05 Agent integration:** register branch-scoped `edit-derivative-story`; Agent proposes
  a patch and deterministic CAS Revision Service applies an approved proposal.

## Phase 37 — Constrained Generation

**Status:** IMPLEMENTED & VERIFIED (2026-08-04, `37-VERIFICATION.md` passed at `b8594e3`)

**Goal:** generate continuation/rewrites against an auditable story-state package.

**Plans:** 37-01 context package compiler; 37-02 constrained draft generation;
37-03 contradiction/character/timeline/clue checks; 37-04 explicit divergence override.

**Must-Haves:** cutoff state, evidence refs, unresolved clues, world rules and user intent;
no silent write-back to original canon.

**Verification:** frozen continuation set and contradiction tests; **Test, Fix, and Confirm**
with intentional canon violations and allowed divergences.

- **37-05 Agent integration:** register `continue-derivative-story`; separate ordinary draft
  approval, disabled-by-default BranchSuggestion, divergence approval and deterministic
  derivative revision publication; suggestions never auto-fork or reuse an approval.

## Phase 38 — Derivative Visual Consistency

**Status:** IMPLEMENTED & VERIFIED (2026-08-05, `38-VERIFICATION.md` passed at `fad8978`)

**Goal:** extend the Visual Bible and illustration pipeline without contaminating original
visual authority.

**Plans:** 38-01 forked Visual Bible; 38-02 derivative Scene Specs; 38-03 generation and
cross-chapter consistency; 38-04 review/version lineage; 38-05 branch-aware Agent skill.

**Must-Haves:** original references remain immutable; divergence is explicit; generated
assets belong to derivative namespace.

**Verification:** namespace and identity consistency tests; **Test, Fix, and Confirm** mixed
original/derivative asset scenarios.

- **38-05 Agent integration:** register branch-aware visual Skill (`illustrate-derivative-
  scene`); publish only validated and approved derivative assets without mutating Original
  visual authority.

## Phase 39 — Export, UAT and v1.4 Audit

**Status:** IMPLEMENTED & VERIFIED (2026-08-05, `39-VERIFICATION.md` passed at `c21c9e0`)

**Goal:** ship a reproducible derivative package and independently audit the complete flow.

**Plans:** 39-01 Markdown/EPUB derivative export; 39-02 manifest/assets/citation package;
39-03 end-to-end browser UAT; 39-04 security, quality and milestone audit; 39-05 Agent
integration (prepare-export).

**Must-Haves:** export-version parity, asset provenance, owner isolation, no original-space
mutation and three-dimensional status report.

**Verification:** import/export round trip, security tests, browser workflow and independent
audit; **Test, Fix, and Confirm** every failed end-to-end checkpoint.

---

- **39-05 Agent integration:** register `prepare-export`; Agent prepares a frozen manifest,
  user approves export, and deterministic exporter materializes the bundle.

# v1.5 — Windows Desktop Runtime

> This milestone adds a Windows-first Electron distribution around the verified React/Next
> product surface. The web app remains a development/test harness, not the formal release
> target. These phases do not restore web as the release target and do not rewrite verified
> business UI; they prove and qualify the desktop host, transport and local runtime.
>
> Phase 40 remains the ad-hoc `chat_backfill` record in `STATE.md`, not a numbered roadmap
> phase. Phase 22 remains independently blocked at 0/3 scheduled green observations; that
> fact is preserved and does not prevent planning this milestone.

## Phase Checklist

- [ ] **Phase 41: Electron Architecture and Packaging Proof** - Prove the standalone Next/local-loopback and bundled no-Docker architecture before downstream work.
- [ ] **Phase 42: Secure Desktop Shell** - Host the existing React/Next renderer behind a sandboxed, capability-limited Electron boundary.
- [ ] **Phase 43: Managed Local Runtime and Data Lifecycle** - Manage the local process graph, app data, migrations, crash recovery and lifecycle.
- [ ] **Phase 44: Desktop Transport, Credentials and Offline Behavior** - Establish dynamic local transport, OS-backed credentials, SSE and honest offline states.
- [ ] **Phase 45: Windows Packaging, Migration and Desktop Qualification** - Qualify installer, upgrade/uninstall preservation, clean-VM behavior and release security gates.

## REQ-DESK Primary Ownership

Each v1 requirement has exactly one primary phase. Cross-stage entries below are validation
only and do not create a second owner.

| Requirement | Primary phase | Cross-stage validation (non-owning) |
|---|---|---|
| REQ-DESK-01 | Phase 42 | Phase 41 route proof; Phase 45 critical-workflow qualification |
| REQ-DESK-02 | Phase 42 | Phase 45 IPC/security negative audit |
| REQ-DESK-03 | Phase 43 | Phase 41 process-graph feasibility; Phase 45 lifecycle qualification |
| REQ-DESK-04 | Phase 41 | Phase 43 managed adapters; Phase 45 clean-VM proof |
| REQ-DESK-05 | Phase 43 | Phase 45 upgrade/uninstall preservation |
| REQ-DESK-06 | Phase 44 | Phase 43 endpoint readiness; Phase 45 packaged startup qualification |
| REQ-DESK-07 | Phase 43 | Phase 44 blocked/offline presentation; Phase 45 crash/recovery qualification |
| REQ-DESK-08 | Phase 44 | Phase 45 clean-VM/offline qualification |
| REQ-DESK-09 | Phase 45 | — |
| REQ-DESK-10 | Phase 45 | — |

## v1.5 Progress

| Phase | Plans Complete | Status | Completed |
|---|---:|---|---|
| 41. Electron Architecture and Packaging Proof | decision artifact only | **NO-GO preserved**; no SUMMARY/VERIFICATION rewrite | 2026-08-10 evidence |
| 42. Secure Desktop Shell | 3/3 summaries | Implemented under explicit 42–45 override; no standalone phase VERIFICATION | 2026-08-10 artifacts |
| 43. Managed Local Runtime and Data Lifecycle | 4/4 summaries | Implemented under explicit 42–45 override; no standalone phase VERIFICATION | 2026-08-10/11 artifacts |
| 44. Desktop Transport, Credentials and Offline Behavior | 3/3 summaries | Implemented under explicit 42–45 override; no standalone phase VERIFICATION | 2026-08-10/11 artifacts |
| 45. Windows Packaging, Migration and Desktop Qualification | 4/4 summaries | `45-VERIFICATION.md` says evidence-supported verified but release-ready false; stale clean-VM wording remains an audit boundary | 2026-08-11 artifacts |

### Phase 41: Electron Architecture and Packaging Proof

**Goal:** establish a fail-closed architecture decision that the existing Next application can
run as a Windows Electron local-loopback desktop product with bundled dependencies, before any
comprehensive UI migration or downstream packaging work.

**Depends on:** Phase 39; Phase 22's independent 0/3 blocked gate remains unchanged.

**Requirements:** REQ-DESK-04

**Success Criteria** (what must be TRUE):

1. A proof harness builds and starts the existing Next `output:standalone` artifact through an
   Electron local-loopback path and verifies all 13 current routes respond and render through
   the existing React/Next surface.
2. The proof records the real process, asset, port, environment and shutdown assumptions for
   Next, FastAPI, Agent Service, PostgreSQL and vector storage, and marks bundled/no-Docker
   feasibility for each dependency with explicit evidence or a blocker.
3. The proof demonstrates that an installed target does not require Docker or a user-installed
   Node, Python, PostgreSQL or vector-service runtime, or records the exact failed prerequisite.
4. A fail-closed go/no-go artifact blocks Phase 42–45 readiness when standalone output,
   local-loopback, 13-route coverage or bundled no-Docker feasibility fails; it does not trigger
   a broad UI rewrite or silently downgrade a failed proof to a green plan.

**Plans:**

- **Wave 0**
  1. **41-01 — Disposable Electron/local-runtime proof skeleton and fail-closed topology contract** — Blocked on: none.
- **Wave 1**
  1. **41-02 — Next standalone local-loopback proof and 13-route parity** — Blocked on: `41-01`.
- **Wave 2**
  1. **41-03 — Bundled-runtime feasibility and Phase 41 GO/NO-GO decision** — Blocked on: `41-02`.

Phase 41 planning is ready; comprehensive UI migration remains gated on the Phase 41 decision.

**UI hint:** yes

### Phase 42: Secure Desktop Shell

**Goal:** let users operate the verified React/Next renderer inside an Electron shell while
exposing only an explicit, capability-specific desktop boundary.

**Depends on:** Phase 41 fail-closed GO.

**Requirements:** REQ-DESK-01, REQ-DESK-02

**Success Criteria** (what must be TRUE):

1. Users can open the desktop application and use all 13 existing routes and verified critical
   workflows through the React/Next renderer without a parallel business-UI rewrite or a new
   formal web-release target.
2. Renderer security checks prove `contextIsolation: true`, `sandbox: true`,
   `nodeIntegration: false`, and no renderer access to Node, filesystem, shell or arbitrary
   process APIs.
3. A restrictive CSP, allowlisted navigation policy and controlled window policy block
   unexpected origins, redirects, popups and new windows while preserving the approved local
   application flow.
4. The preload exposes a typed capability-specific `DesktopBridge`; IPC rejects unknown
   channels, invalid payloads and messages whose sender/webContents is not an approved renderer.

**Plans:**

- **Wave 0**
  1. **42-01 — Electron main/preload shell skeleton** — Blocked on: `41-03`.
- **Wave 1**
  1. **42-02 — CSP, navigation, window, permissions and sender-validated IPC security boundary** — Blocked on: `42-01`.
- **Wave 2**
  1. **42-03 — Electron 13-route, critical-workflow and renderer-privilege parity gates** — Blocked on: `42-02`.

**UI hint:** yes

### Phase 43: Managed Local Runtime and Data Lifecycle

**Goal:** give the desktop application a deterministic, observable local runtime and durable
data lifecycle for all managed services without changing backend domain authority.

**Depends on:** Phase 41 fail-closed GO and Phase 42's secure bridge boundary.

**Requirements:** REQ-DESK-03, REQ-DESK-05, REQ-DESK-07

**Success Criteria** (what must be TRUE):

1. A small deep-module `DesktopRuntime` interface exposes only `ensureReady`, `status`,
   `restart` and `shutdown`, with typed lifecycle/error states and deterministic readiness
   semantics; it does not become a second domain or persistence authority.
2. `PackagedProcessAdapter` and `DevelopmentProcessAdapter` manage the same contract for Next,
   FastAPI, Agent Service, PostgreSQL and vector storage, including dependency ordering,
   health checks, dynamic endpoints, clean process-tree shutdown and targeted restart.
3. First run and upgrade use a versioned `%APPDATA%/NovelMind` layout for mutable data, logs,
   migration state and backups; compatible upgrades preserve user data and migration failure
   leaves a recoverable backup/state rather than partial silent success.
4. A killed dependency, startup failure, port conflict or crash is visible as a typed degraded
   or failed state, offers a bounded recovery/restart path, and never appears as a successful
   empty novel/library state.

**Plans:**

- **Wave 0**
  1. **43-01 — DesktopRuntime deep module and development/packaged adapter contract** — Blocked on: `42-02`.
- **Wave 1**
  1. **43-02 — Five-component process graph, dynamic ports, readiness, logging and Windows process-tree ownership** — Blocked on: `43-01`.
  2. **43-03 — Versioned `%APPDATA%/NovelMind` layout, backup-first migration and recovery contract** — Blocked on: `43-01`.
- **Wave 2**
  1. **43-04 — Visible/recoverable runtime and data failure states with empty-success prevention** — Blocked on: `43-02`, `43-03`.

### Phase 44: Desktop Transport, Credentials and Offline Behavior

**Goal:** provide a secure desktop transport and honest connectivity model for local workflows,
provider calls and streaming without moving factual or domain authority into Electron.

**Depends on:** Phase 42 secure bridge and Phase 43 runtime endpoint/readiness contract.

**Requirements:** REQ-DESK-06, REQ-DESK-08

**Success Criteria** (what must be TRUE):

1. Startup injects the actual local endpoints and auth material at runtime without fixed-port
   assumptions; renderer code consumes typed capabilities rather than discovering services or
   storing gateway/provider credentials in renderer storage.
2. Local credentials use Electron `safeStorage`/the OS-backed protection boundary, are absent
   from renderer-accessible storage and logs, and invalid/expired credentials fail closed with
   a stable user-visible status.
3. Agent and long-running local operations stream through the existing SSE contract with
   reconnect/cancellation/error handling that preserves terminal failure states and does not
   manufacture a successful result.
4. Provider-independent reading, editing and local-data workflows start without internet;
   provider-dependent actions show explicit unavailable/blocked states when offline or
   misconfigured, while backend services remain the domain and authority boundary.

**Plans:**

- **Wave 0**
  1. **44-01 — Dynamic endpoint/bootstrap contract and unified API/SSE resolver** — Blocked on: `43-04`.
- **Wave 1**
  1. **44-02 — OS-protected credentials and fail-closed local session authentication** — Blocked on: `44-01`.
- **Wave 2**
  1. **44-03 — SSE recovery and honest offline/provider capability behavior** — Blocked on: `44-02`.

### Phase 45: Windows Packaging, Migration and Desktop Qualification

**Goal:** qualify a reproducible Windows desktop release candidate that installs, upgrades,
recovers and preserves data under real clean-machine conditions.

**Depends on:** Phases 41–44 and the Phase 39 verified critical workflow/export baseline.

**Requirements:** REQ-DESK-09, REQ-DESK-10

**Success Criteria** (what must be TRUE):

1. A Windows installer on a clean VM installs and first-runs without Docker or user-installed
   Node, Python, PostgreSQL or vector-service runtime, opens only one application instance,
   shows no console window and shuts down the managed process tree cleanly.
2. A compatible versioned upgrade preserves `%APPDATA%/NovelMind` data, logs and backups;
   uninstall behavior preserves user data according to the documented policy and a failed
   upgrade has a reversible recovery path.
3. Electron Playwright integration qualification covers first run, all existing critical
   workflows, local runtime recovery, offline/blocked states and data preservation; the
   security audit includes CSP/navigation/window, IPC sender and malformed-capability negatives.
4. The release gate records clean-VM, migration, crash-recovery and qualification evidence;
   code-signing certificate acquisition is an external publication gate and is not purchased
   or represented as complete by this roadmap.

**Plans:**

- **Wave 0**
  1. **45-01 — Windows installer build chain, bundled runtime, single instance and data/resource isolation** — Blocked on: `41-03`, `42-03`, `43-04`, `44-03`.
- **Wave 1**
  1. **45-02 — Version upgrade, failure recovery and data-preserving uninstall policy** — Blocked on: `45-01`.
- **Wave 2**
  1. **45-03 — Clean Windows VM install, first-run, workflow, offline-recovery and data-preservation UAT** — Blocked on: `45-02`.
- **Wave 3**
  1. **45-04 — Electron security audit, SBOM/evidence integrity and v1.5 closeout** — Blocked on: `45-03`.

# v1.6 — Provider Protocol Unification and Live Qualification

> This phase follows the user-confirmed Pi/Agent gateway and settings integration. The
> current dirty worktree already contains a tested five-provider discovery foundation and
> owner-bound Pi default-model resolution; Phase 46 plans only the remaining protocol,
> authority and live-qualification gaps. It does not rewrite Phase 42–45 summaries, alter
> the Phase 41 NO-GO record, satisfy Phase 22, or authorize paid provider calls.

## Phase Checklist

- [ ] **Phase 46: Provider Protocol Unification and Live Qualification** - Make all five configured provider formats complete, remove hardcoded runtime selection, and qualify real discovery plus Pi calls without exposing credentials.

## REQ-PROVIDER Primary Ownership

| Requirement | Primary plan | Cross-plan validation (non-owning) |
|---|---|---|
| REQ-PROVIDER-01 | 46-01 | 46-03 live catalog matrix |
| REQ-PROVIDER-02 | 46-02 | 46-03 Pi run matrix; 46-04 lineage audit |
| REQ-PROVIDER-03 | 46-03 | 46-04 closeout verdict |
| REQ-PROVIDER-04 | 46-04 | 46-02 resolver provenance |

### Phase 46: Provider Protocol Unification and Live Qualification

**Goal:** establish one owner-scoped deployment authority and evidence-backed protocol matrix
for OpenAI, Anthropic, Google AI Studio Gemini, Ollama and custom
OpenAI-compatible services across model discovery, connection testing and Pi/Agent execution.

**Depends on:** Phase 25.2 Agent gateway authority and Phase 44 credential/transport boundary.
Phase 45 evidence is reusable; Phase 41 NO-GO and Phase 22 0/3 remain unchanged.

**Requirements:** REQ-PROVIDER-01, REQ-PROVIDER-02, REQ-PROVIDER-03, REQ-PROVIDER-04

**Success Criteria** (what must be TRUE):

1. A single backend provider registry defines canonical IDs, aliases, credentials, catalog
   pagination, generation transport and declared capabilities for all five settings choices;
   create, update, discovery, test and invocation reject incompatible provider/config pairs.
2. Pi/Agent and every in-scope text-generation consumer resolve an active owner-scoped
   `AIModelConfig`; the legacy `ai_router.py` static catalog and global routing preference can
   no longer select a runtime model, and missing/unsafe configuration fails closed.
3. A credential-gated qualification matrix records discovery, direct test and Pi run evidence
   per provider without storing secrets. A provider without an operator-supplied credential or
   reachable local service is BLOCKED/PARTIAL, never simulated green.
4. Provider/model/usage/error/cost lineage is persisted and surfaced from backend truth;
   unknown capability or pricing remains explicit, and the closeout gate can independently
   reproduce its verdict from redacted evidence.

**Plans:**

- **Wave 0**
  1. **46-01 — Five-provider protocol registry, pagination and configuration validation** — no execution dependency.
- **Wave 1**
  1. **46-02 — Owner-scoped deployment authority and hardcoded router retirement** — Blocked on: `46-01`.
- **Wave 2**
  1. **46-03 — Credential-gated real provider and Pi qualification matrix** — Blocked on: `46-01`, `46-02`.
- **Wave 3**
  1. **46-04 — Usage/cost/capability truth, browser proof and closeout** — Blocked on: `46-03`.

**UI hint:** yes — reuse the existing settings layout; add only backend-derived capability,
qualification and failure states defined by `46-UI-SPEC.md`.

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

Phase 22 (CI/Nightly authority) remains the last unverified gate — 0/3 scheduled green.
Phase 25.2 through Phase 39 are all verified (2026-08-02/03/04/05), completing the v1.2
milestone (26–29), the v1.3 milestone (30–34) and the v1.4 milestone (35–39). The full
roadmap through Phase 39 is delivered under the user-authorized execution overrides. The
Phase 39 closeout audit gate honestly reports `blocked` (Phase 22 0/3 + REQ-SHIP-01 gaps)
and never promotes; neither blocker is hidden or downgraded.

Phase 40 remains the ad-hoc `chat_backfill` record in `STATE.md`, not a numbered roadmap
phase. Phase 41's NO-GO decision remains authoritative. Phases 42–45 have execution summaries
under the explicit override and Phase 45 has an evidence-supported verification artifact, but
those artifacts do not rewrite Phase 41 and their stale clean-VM/release wording is not silently
normalized here. Phase 22's independent 0/3 blocked state does not change.

Phase 46 is planned only. Execution requires a separate user instruction. Cloud-provider
qualification additionally requires operator-supplied credentials and may incur provider cost;
absence of those inputs is a valid BLOCKED result, not permission to substitute mocks.
