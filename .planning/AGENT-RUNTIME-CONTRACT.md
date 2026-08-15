# NovelMind Agent Runtime Consumption Contract

> Authority: Issue #29 architecture additions, 2026-08-01. This contract applies to every
> Phase 26–39 plan and is implemented only after Phase 25.2/25.3 qualification gates pass.

## Runtime Boundary

- `@earendil-works/pi-agent-core`, `@earendil-works/pi-ai` and the
  `@earendil-works/pi-coding-agent` SDK/ResourceLoader are pinned and qualified by Phase
  25.2/25.3; no ambient `~/.pi` discovery is allowed.
- Pi runs in the Node Agent Service between Next.js and FastAPI. Default bash, filesystem,
  file-edit and arbitrary-execution tools are disabled.
- The Agent selects versioned Skills and calls only allowlisted NovelMind Domain Tools.
  FastAPI enforces owner, branch, cutoff, spoiler, evidence, budget, timeout and stable errors.
- A Session is resumable execution context, never a long-term fact source. Official results
  are persisted as versioned Artifacts with SkillRun, model, input, source and evidence lineage.
- Validator/Gate owns legality and status transitions. `ask` creates a server-authoritative
  ApprovalRequest; the frontend renders approval but cannot grant it by itself.
- Original Canon mutation, active-pointer movement, direct database access, shell and host
  filesystem access are forbidden. External MCP output remains `external_evidence` and
  `prohibited_from_canon=true`.

## Structured Output Integrity

- Model-produced structured outputs pass a shared conservative normalizer followed by strict
  schema validation. Allowed repairs are limited to declared aliases, enum canonicalization and
  unambiguous container-shape normalization.
- A repair may never invent `evidence_refs`, owner, cutoff, authority, branch, fork or approval
  state. Unsafe or ambiguous repair terminates as `blocked` with stable warnings.
- Every normalized Artifact records `normalization_actions`, `raw_hash`, `repaired_hash` and
  `warnings`; raw model output remains immutable audit evidence and repaired output does not gain
  authority by normalization.
- Deterministic heuristics may improve candidate recall, but cannot become Canon, citations or
  factual claims without the existing evidence and Validator/Gate path.

## Phase Consumption Map

| Phase | Skill / Agent mode | Official Artifact | Deterministic authority / approval |
|---|---|---|---|
| 26 | `answer-reading-question` | `CitedAnswerArtifact` with Frozen Manifest | Evidence/citation validator; no factual answer without valid source evidence |
| 27 | Agent proposes typed world-model candidates | `WorldModelCandidateArtifact` | Validator/Gate alone publishes versioned facts or inference layers |
| 28 | `analyze-chapter`, `build-story-arc` | `ChapterAnalysisArtifact`, `StoryArcArtifact`, outline/mainline candidates | Coverage/terminal-state validators; digests are non-indexed and Narrative Memory stays candidate-only |
| 29 | frozen SkillRun evaluation | `SkillEvaluationArtifact` | Evaluator freezes Skill, model, source, Artifact and dataset versions |
| 30 | `build-visual-bible` | `VisualBibleArtifact` | Evidence/rights validator; user approval before accepted visual authority |
| 31 | `detect-key-scenes` | `SceneCandidateArtifact` | Speaker/dialogue heuristics are candidate signals only; deterministic scoring/diversity/spoiler gate and user selection retain authority |
| 32 | `compile-scene-spec` | `SceneSpecArtifact`, `PromptArtifact` | Canon/Visual Bible validator; no unsupported detail injection |
| 33 | `illustrate-scene` | `IllustrationRevision` | Budget/provenance/consistency gate; generation remains candidate-only |
| 34 | Agent proposes anchors | `IllustrationAnchorProposal` | User approval followed by deterministic anchor publication/export |
| 35 | `create-canon-fork` | `CanonForkProposal`, `CanonDeltaArtifact` | Approval required; Original Canon is immutable |
| 36 | Agent acts as editor collaborator | `DerivativeEditProposal` | Deterministic autosave/history/rollback; Agent never writes outside branch scope |
| 37 | `continue-derivative-story` | `DraftArtifact`, `ContinuityReport`, disabled-by-default `BranchSuggestion` | Suggestion, divergence and publication are separate transitions; no automatic fork |
| 38 | branch-aware visual Skills | `BranchVisualBibleArtifact`, `BranchIllustrationRevision` | Branch isolation and visual-continuity validators; no original authority mutation |
| 39 | `prepare-export` | `ExportPreparationArtifact` | Deterministic exporter builds the final bundle from approved versions only |

## Per-Phase Plan Contract

Every Phase 26–39 plan set must name its producing Skill or Agent mode, allowed Domain Tools,
input and Artifact schemas, SkillRun lineage, Validator/Gate, Approval transition and forbidden
authority crossings. Deterministic domain services may be built in separate plans, but the phase
is not complete until the Agent integration plan proves this end-to-end contract and includes a
final **Test, Fix, and Confirm** task.

Progress delivery reuses the existing Agent SSE/Job transport: SSE is notification only, the
database checkpoint is authoritative, and reconnect recovery never depends on browser memory.
The removed legacy `/analyze/stream` endpoint is not restored.
