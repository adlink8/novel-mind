# Phase 27 Context — Novel World Model

## Decisions

- D-01: Keep canon fact, probable inference, literary interpretation, and user interpretation as distinct authorities.
- D-02: World projections are versioned immutable candidates; no active-pointer cutover.
- D-03: Every event, causal edge, character state/goal/motivation/knowledge claim, entity, rule, faction, place, and item carries source lineage and owner/novel/version/cutoff.
- D-04: Causality requires evidence and is not inferred from co-occurrence; rule exceptions and mistaken beliefs remain explicit.
- D-05: POV/disclosure timing controls what a character knows and what a reader may see.
- D-06: Reader Chat is never a world-model fact source; human corrections are protective overrides.

## Agent Consumer Contract

- Skill / mode: propose-world-model-candidates.
- Inputs: validated evidence and Phase 26 retrieval artifacts.
- Official output: WorldModelCandidateArtifact, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: user interpretation confirmation where applicable.
- Deterministic authority: WorldModel Validator/Gate publishes typed projections.
- Forbidden: direct Canon fact publication by Agent; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- Strict schema decomposition and relation names.
- Storage projection shape and replay strategy.
- Evidence thresholds and review sampling.

## Deferred Ideas (OUT OF SCOPE)

- Production promotion or active-pointer cutover.
- Fanfiction/user canon integration.
- Unbounded automatic ontology discovery.

## Canonical refs

docs/architecture/03-data-model.md; backend/app/services/timeline/{query,reconcile,evidence}.py;
relationships/{query,gates,overrides}.py; clues/{query,gates,lifecycle}.py;
narrative_memory/{contracts,provenance,authority,citations}.py; Phase 26 D-04/D-05.
[VERIFIED: repository grep]

Phase 25.2 Runtime and Phase 25.3 governance are required Issue dependency boundaries;
current implementation is not claimed. Phase 22 remains 0/3 Nightly and blocked.
[VERIFIED: repository grep]
