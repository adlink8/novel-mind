# Phase 32: Scene Spec and Prompt Compiler - Context

**Scope authority:** Issue #29 (`https://github.com/adlink8/novel-mind/issues/29`)

## Decisions

### D-32-01 — Provider-neutral Scene Spec
- `SceneSpec` is the canonical candidate Artifact; provider prompts are derived revisions and never become source truth.

### D-32-02 — Evidence-bounded details
- Every character, place, action, time, composition constraint, and style constraint is either linked to evidence/Visual Bible or labeled as user interpretation.
- Unsupported details must be rejected or surfaced as unresolved; they cannot be disguised as canon.

### D-32-03 — Deterministic compiler lineage
- A compiled prompt records Scene Spec hash, Visual Bible revision, source snapshot, schema/prompt/compiler version, provider adapter version, negative constraints, and config hash.

### D-32-04 — Preview before generation
- Prompt preview/diff and human edits are explicit candidate revisions; Phase 32 does not invoke image providers.

## Agent Consumer Contract

- Skill / mode: compile-scene-spec.
- Inputs: validated SceneCandidateArtifact + VisualBibleArtifact.
- Official output: SceneSpecArtifact; PromptArtifact, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: approval authorizes Phase 33 consumption only.
- Deterministic authority: Canon/Visual Bible/unsupported-detail validator.
- Forbidden: write-back to Canon or Visual Bible; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- Provider adapter interface, prompt section ordering, and serialization format may follow existing AI router conventions.
- Initial adapters may target one configured provider plus a mock contract, but the Scene Spec must remain provider-neutral.

## Deferred Ideas (OUT OF SCOPE)

- Provider calls, durable image jobs, consistency scoring, and asset approval: Phase 33.
- Reader anchors/export: Phase 34.
- Derivative Visual Bible and fork isolation: Phase 38.

## Canonical References

- Issue #29; `.planning/REQUIREMENTS.md` `REQ-VIS-03`; `.planning/ROADMAP.md` Phase 32.
- `backend/app/services/ai_router.py`, `ai_service.py`, `vertex_gemini.py` for model routing/call analogs.
- `backend/app/services/reader_chat/worker.py`, `context.py`, `schemas/reader_chat.py` for frozen inputs/hash lineage.
- `backend/app/services/narrative_memory/provenance.py`, `manifests.py` for candidate/source lineage.
