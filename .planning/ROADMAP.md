# NovelMind GSD Roadmap

> Execution authority: `master`. Snapshot: `912ca6b`, 2026-08-01.
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
| agent runtime foundation | 25.2–25.3 | planned | planned | planned |
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

Phase 22 is the active cursor. Phases 25.2+ cannot be reported `ACTIVE` until its three-run
gate closes.

---

# Agent Runtime Foundation — Phase 25.2–25.3

> Source: Issue #29 architecture appendix (2026-08-01). The product direction shifts from
> "software + isolated AI buttons" to an agent-driven workspace: a Novel Agent Runtime
> orchestrates Skills and controlled domain tools over the trusted world model, while
> deterministic code keeps facts, permissions, memory, versions and publication authority.
> The agent is an orchestrator — never a fact source or a database administrator.

## Phase 25.2 — Embedded Novel Agent Runtime

**Goal:** prove an embeddable agent runtime (Pi SDK or equivalent) and establish the
NovelMind Tool / Skill / Artifact / Approval foundation; first stage allows read-only
analysis and candidate Artifacts only — no Canon mutation, no illustration publication, no
derivative text writes.

**Depends on:** Phase 22, Phase 25.1.

### Plans

- **25.2-01 Agent Runtime Spike**
  - Steps: embed the Pi SDK in a standalone Node agent-service; custom system prompt with
    default coding tools (bash, file editing, arbitrary execution) disabled; call FastAPI
    tool APIs; stream SSE/WebSocket events; verify session create/resume/cancel/retry,
    model-routing compatibility with the existing AI configuration, and tool
    timeout/error/cancellation propagation.
  - Must-Haves: `.planning/spikes/embedded-novel-agent/` with `CONTEXT.md`,
    `EXPERIMENTS.md`, `FINDINGS.md`, `DECISION.md`; no default coding tools enabled; no
    write path exercised.
  - Verification: one recorded experiment per required capability; explicit go/no-go
    decision.
  - Test, Fix, and Confirm: replay session scenarios, fix propagation gaps, freeze the
    decision.
- **25.2-02 Domain Tool Contract**
  - Steps: implement the first read-only tools — `get_novel`, `get_chapter`,
    `search_novel_text`, `get_timeline`, `get_relationships`, `get_clues`,
    `get_narrative_memory` — with owner check, reading cutoff/spoiler boundary, query
    budget, timeout, output size limit, evidence lineage and stable error codes enforced
    server-side by FastAPI.
  - Must-Haves: enforcement is server-side, never prompt-side; every tool has a typed
    schema and an error-code table.
  - Verification: per-tool contract tests for owner/spoiler/budget/timeout/lineage.
  - Test, Fix, and Confirm: adversarial calls (cross-owner, beyond-cutoff, over-budget)
    fail closed.
- **25.2-03 Skill Runtime and Artifact Contract**
  - Steps: `SkillRegistry` / `SkillVersion` / `SkillRun`; allowed-tools allowlist; input /
    output JSON schemas; budget and approval policies; `Artifact` / `ArtifactRevision`
    persistence carrying type, schema version, owner/novel/branch, producing skill and
    version, agent runtime and model lineage, source versions, input hash, evidence refs,
    status (`candidate` / `validated` / `approved` / `published` / `rejected`) and parent
    revision; first skill `answer-reading-question`:
    Question → QueryPlan → Tool calls → EvidenceRef materialization → Frozen Manifest →
    Cited Answer Artifact.
  - Must-Haves: an agent session is never a long-term fact source; artifacts are replayable
    and version-traceable; `skill.yaml` declares `allowed_tools`, `read_permissions`,
    `write_permissions`, `forbidden_spaces`, `budget` and `approval_required_for`.
  - Verification: artifact schema, lineage and replay tests; a cancelled run writes no
    artifact.
  - Test, Fix, and Confirm: run the first skill end-to-end on fixtures and repair schema
    gaps.
- **25.2-04 Agent Workspace**
  - Steps: `/analysis` agent streaming answers, current-phase and tool-call summaries,
    artifact preview, citation jump-to-source, cancel/retry, user approve/reject of
    candidate results, session restore.
  - Must-Haves: approval state round-trips to the artifact store; no candidate is
    published without explicit approval.
  - Verification: desktop + 390px browser tests for streaming, approval and restore flows.
  - Test, Fix, and Confirm: run real question flows and fix citation jumps/state loss.

**Non-goals:** no shell / file-editing / arbitrary-command tools; no Original Canon
mutation; no multi-agent; no automatic illustration publication; no derivative text
publication; does not replace the stable Reader Chat chain — it reuses its evidence,
budget, citation and session foundation.

**Phase Verification:** the agent answers a real novel question inside `/analysis` using
only NovelMind tools; every citation resolves to a legal leaf EvidenceRef; tool calls
respect owner/spoiler/budget; a cancelled agent writes no artifact; identical input
replays with traceable skill, model and data versions; the runtime is not a new fact
source.

## Phase 25.3 — Pi Package Compatibility and Governance

**Goal:** establish a controlled reuse mechanism for Pi ecosystem packages and prove that
third-party extensions cannot bypass NovelMind's permission, evidence, Canon and Artifact
boundaries.

**Depends on:** Phase 25.2.

### Plans

- **25.3-01 Package lock, source audit and lifecycle scanning**
  - Steps: pin exact npm versions or Git SHAs; vendor or internal-registry after code
    review; CI consumes a lockfile; `npm ci --ignore-scripts`; scan license, dependency
    tree and lifecycle scripts; record an `adopt` / `fork` / `pattern-only` / `reject`
    verdict per package.
  - Must-Haves: no dynamic `pi install` / `pi update` inside the formal agent service;
    every loaded package has a recorded verdict.
  - Verification: governance policy tests; lockfile reproducibility check.
  - Test, Fix, and Confirm: attempt unpinned/lifecycle-script packages and confirm blocks.
- **25.3-02 Tool Registry Manifest and collision gate**
  - Steps: startup `ToolRegistryManifest` with `tool_name`, `provider_package`,
    `schema_hash`, `permission`, `domain`, `enabled`; same-name tools fail closed at
    startup instead of silently overriding by load order.
  - Must-Haves: collisions block startup; extensions with undeclared permissions cannot
    start.
  - Verification: collision and shadowing adversarial startup tests.
  - Test, Fix, and Confirm: inject duplicate tool names and repair detection gaps.
- **25.3-03 pi-mcp-adapter external-tool isolation spike**
  - Steps: allowlisted external MCP servers only (web research, external documents, image
    generation services); lazy discovery through the proxy tool; no ambient user-machine
    MCP configuration; external results materialize as `external_evidence` artifacts and
    are never mixed with `original_text_evidence`.
  - Must-Haves: MCP never touches NovelMind PostgreSQL, Original Canon writes, evidence
    validation or core Reader Chat retrieval.
  - Verification: isolation negative tests; external-evidence labeling tests.
  - Test, Fix, and Confirm: run one allowlisted server and confirm boundary enforcement.
- **25.3-04 Permission policy core and Web Approval adapter**
  - Steps: adopt or fork the permission-system policy core into `allow` / `ask` / `deny`
    over domain actions (not file paths); `ask` creates a Web `ApprovalRequest`; `deny`
    blocks deterministically (`modify_original_canon`, `move_active_pointer`).
  - Must-Haves: rule precedence, fail-closed defaults, tool-visibility filtering and
    session-scoped approval; approval UX lives in the web app, not a TUI.
  - Verification: policy-decision matrix and approval round-trip tests.
  - Test, Fix, and Confirm: exercise the allow/ask/deny matrix and fix precedence bugs.
- **25.3-05 pi-web-ui Artifact/Tool renderer feasibility**
  - Steps: evaluate borrowing the Artifact / Tool renderer designs (or wrapping selected
    web components) inside the existing Analysis Chat without adopting its ChatPanel,
    IndexedDB session authority or browser-held provider keys.
  - Must-Haves: NovelMind Next.js and PostgreSQL remain session and rendering authority.
  - Verification: feasibility note plus a prototype render of one artifact type.
  - Test, Fix, and Confirm: render a cited-answer artifact and fix integration gaps.

**Non-goals:** no arbitrary package installation; no multi-agent; no third-party memory
replacing Narrative Memory; no extension receives shell, host-filesystem or direct
database permissions; no third-party tool may modify Original Canon.

**Phase Verification:** all loaded resources come from a fixed allowlist and lock
manifest; same-name tools/skills block at startup; undeclared-permission extensions
cannot start; MCP external results are labeled external evidence; confirmation-required
actions create Web ApprovalRequests; removing any community package keeps core reading QA
functional; every package has a recorded adoption verdict.

### Agent Consumption Map — Phase 26–39

The existing roadmap is not replaced; phases are delivered through Skills and controlled
tools over the same world model:

| Phase | Agent consumption |
|---|---|
| 26 question-driven retrieval | `answer-reading-question` Skill |
| 27 world model | agent produces candidates; Validator/Gate publishes facts |
| 28 whole-book narrative memory | `analyze-chapter` / `build-story-arc` Skills |
| 29 QA quality | frozen evaluation over Skill Runs and Artifacts |
| 30 Visual Bible | `build-visual-bible` Skill |
| 31 key scenes | `detect-key-scenes` Skill |
| 32 Scene Spec | `compile-scene-spec` Skill |
| 33 illustrations | `illustrate-scene` Skill |
| 34 in-text anchors | agent proposes; user approves; deterministic service publishes |
| 35 Canon Fork | `create-canon-fork` Skill |
| 36 derivative editor | agent as in-editor collaborator |
| 37 constrained generation | `continue-derivative-story` Skill |
| 38 derivative visual | branch-aware visual Skills |
| 39 export/closeout | `prepare-export` Skill + deterministic exporter |

---

# v1.2 — Trusted Novel Understanding

## Phase 26 — Question-Driven Retrieval and Evidence

**Goal:** turn a reader/analyst question into a typed retrieval plan, fuse the required
dimensions and materialize source-verified citations.

**Depends on:** Phase 22, Phase 24, Phase 25.1, Phase 25.2.

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
Phase 25.2 (Embedded Novel Agent Runtime) spike and contracts, then Phase 26 planning
artifacts from the contracts above.
