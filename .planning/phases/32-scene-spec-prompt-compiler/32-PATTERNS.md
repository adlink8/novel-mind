# Phase 32: Scene Spec and Prompt Compiler - Patterns

|拟改/新增边界|当前代码 analog|映射规则|
|---|---|---|
|`backend/app/schemas/scene_spec.py`|`schemas/reader_chat.py`, `schemas/knowledge.py`|strict nested contract, hashes, source refs, no arbitrary provider fields.|
|`backend/app/services/scene_spec/compiler.py`|`narrative_memory/manifests.py`, `citations.py`|pure compile, evidence allowlist, canonical serialization.|
|`backend/app/services/prompt_compiler/adapters.py`|`services/ai_router.py`, `vertex_gemini.py`|adapter interface; no provider branching in Scene Spec.|
|`backend/app/models/prompt_revision.py`|`models/reader_chat.py`|immutable lineage, input/output hashes, error status, owner/novel scope.|
|`backend/app/api/scene_specs.py`|`api/reader_chat.py`, `api/narrative_memory.py`|preview/diff endpoints and explicit edit action.|
|`frontend/src/components/scene-spec/*`|`components/structure/*`, `reader-chat-panel.tsx`|side-by-side evidence/spec/prompt preview, diff and validation messages.|

## Artifact flow

`SceneCandidateSet + VisualBibleRevision → SceneSpec(candidate) → PromptRevision(candidate) → human approval → Phase 33 job input`

The prompt string is never the only persisted authority; store canonical inputs and hashes.

## Avoid

- Injecting provider-specific tokens into `SceneSpec`.
- Passing raw LLM/model output directly to a provider.
- Treating a prompt preview as approved generation input without an explicit action.
