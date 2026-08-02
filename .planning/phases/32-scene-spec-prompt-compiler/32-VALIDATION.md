# Phase 32: Scene Spec and Prompt Compiler - Validation

## Nyquist strategy

### Fixtures

- `spec-continuity`: two characters and one place with stable Visual Bible IDs.
- `spec-negative`: forbidden costume/era/identity details and style exclusions.
- `spec-unsupported`: detail absent from evidence, conflicting claim, future spoiler.
- `prompt-golden`: same input compiled twice and through two mock adapters.
- `prompt-edit`: user changes an interpretation field and diff is retained.

### Commands

|层|检查|命令|
|---|---|---|
|unit|schema, evidence gate, canonical serialization, prompt hash|`cd backend; pytest tests/unit/scene_spec tests/unit/prompt_compiler -q`|
|adversarial|unsupported canon, spoiler, provider-field injection|`cd backend; pytest tests/adversarial/test_scene_spec_boundaries.py -q`|
|integration|Visual Bible/source revision scope and preview persistence|`cd backend; pytest tests/integration/scene_spec -q`|
|browser|preview/diff/edit/validation error at desktop and 390px|`cd frontend; npm run test:e2e -- scene-spec --project=chromium-desktop --project=chromium-mobile-390`|

### Manual UAT

1. Open a candidate and inspect every positive/negative prompt clause back to evidence or Visual Bible.
2. Add an unsupported detail: it must be labeled interpretation or be rejected.
3. Change the Visual Bible revision: old prompt becomes stale and cannot be silently reused.
4. Confirm no provider/network call occurs during preview.

### Gate

Fail on any prompt whose lineage cannot reconstruct the Scene Spec, or any adapter that changes canonical meaning.
