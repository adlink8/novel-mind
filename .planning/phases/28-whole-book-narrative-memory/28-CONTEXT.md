# Phase 28 Context — Whole-Book Narrative Memory Convergence

## Decisions

- D-01: Build strictly Chapter State → continuous Story Arc/Volume → Global Story Model.
- D-02: Every chapter ends completed, isolated, or blocked with reason code; no silent pending.
- D-03: Chapter failure blocks only dependent arc/global work, never unconditional whole-book restart.
- D-04: Checkpoints, cancellation, resume, exact-cache reuse, cost ledger, and source manifest are durable.
- D-05: Arc boundaries preserve uncertainty and continuous coverage; manifests are DB-recomputable.
- D-06: Timeline/relations/clues are optional traceable signals; Reader Chat is never a fact source.
- D-07: All output is immutable candidate-only; no active-pointer or production cutover.
- D-08: ChapterAnalysisArtifact may carry bounded `chapter_digest` and chunk digests only as
  compressed payloads for context compaction; they are never retrieval-index inputs and never
  `EvidenceRef` authority. `previous_context_summary`, `next_context_hint`, and
  `continuity_notes` are likewise lineage-bound candidate context, not Canon.
- D-09: `OutlineCandidateArtifact` and `MainlineCandidateArtifact` are uncertainty-bearing,
  source-lineage-bound candidate outputs only. Generation never writes or promotes them to
  Canon.
- D-10: Progress delivery reuses the existing Agent SSE/Job transport. SSE is notification-only;
  the DB checkpoint is authoritative, and reconnect recovery rehydrates from the DB rather than
  browser memory. The removed `/analyze/stream` endpoint is not restored.

## Agent Consumer Contract

- Skill / mode: analyze-chapter; build-story-arc.
- Inputs: source snapshot + validated chapter/world inputs.
- Official output: ChapterAnalysisArtifact; StoryArcArtifact; and the candidate-only
  OutlineCandidateArtifact/MainlineCandidateArtifact, with SkillRun/ToolRun, runtime/model,
  source/input hash, evidence and owner/novel/branch lineage where applicable.
- ChapterAnalysisArtifact context contract: `chapter_digest`, chunk digests,
  `previous_context_summary`, `next_context_hint`, and `continuity_notes` are bounded by an
  explicit max length, cutoff and spoiler policy, and bind to both source and input hashes.
  Digests are compressed payloads only; they cannot enter the retrieval index or stand in for an
  EvidenceRef. `next_context_hint` is limited to disambiguation and must not disclose future
  facts; unsafe or unverifiable hints are omitted or blocked.
- OutlineCandidateArtifact/MainlineCandidateArtifact retain source snapshot/range, input hashes,
  evidence lineage where applicable, uncertainty and candidate status; they never become Canon
  by generation alone.
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
- Emotional memory, emotion-memory fields, and emotion-memory consumers.

## Canonical refs

backend/app/services/narrative_memory/{builder_worker,builder_repository,builder_contracts,
builder_report,dependency_graph,change_oracle,carry_forward,global_builder}.py and
backend/tests/integration/narrative_memory/. [VERIFIED: repository grep]

Phase 22 remains 0/3 blocked; Phase 25.2 Runtime and Phase 25.3 governance remain
unverified Issue boundaries. [VERIFIED: repository grep]
