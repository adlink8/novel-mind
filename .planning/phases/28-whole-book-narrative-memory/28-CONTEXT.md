# Phase 28 Context — Whole-Book Narrative Memory Convergence

## Decisions

- D-01: Build strictly Chapter State → continuous Story Arc/Volume → Global Story Model.
- D-02: Every chapter ends completed, isolated, or blocked with reason code; no silent pending.
- D-03: Chapter failure blocks only dependent arc/global work, never unconditional whole-book restart.
- D-04: Checkpoints, cancellation, resume, exact-cache reuse, cost ledger, and source manifest are durable.
- D-05: Arc boundaries preserve uncertainty and continuous coverage; manifests are DB-recomputable.
- D-06: Timeline/relations/clues are optional traceable signals; Reader Chat is never a fact source.
- D-07: All output is immutable candidate-only; no active-pointer or production cutover.

## Agent Consumer Contract

- Skill / mode: analyze-chapter; build-story-arc.
- Inputs: source snapshot + validated chapter/world inputs.
- Official output: ChapterAnalysisArtifact; StoryArcArtifact, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: candidate-only; no promotion approval.
- Deterministic authority: terminal-state/evidence/coverage validators.
- Forbidden: active pointer or consumer cutover; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- Reason-code taxonomy and checkpoint schema.
- Arc boundary scoring and uncertainty representation.
- Progress/report API shape.

## Deferred Ideas (OUT OF SCOPE)

- Production promotion, active-pointer cutover, and A/B.
- Unbounded whole-book re-analysis.
- Chat-derived memory.

## Canonical refs

backend/app/services/narrative_memory/{builder_worker,builder_repository,builder_contracts,
builder_report,dependency_graph,change_oracle,carry_forward,global_builder}.py and
backend/tests/integration/narrative_memory/. [VERIFIED: repository grep]

Phase 22 remains 0/3 blocked; Phase 25.2 Runtime and Phase 25.3 governance remain
unverified Issue boundaries. [VERIFIED: repository grep]
