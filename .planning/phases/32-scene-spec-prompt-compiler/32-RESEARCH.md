# Phase 32: Scene Spec and Prompt Compiler - Research

**Researched:** 2026-08-01
**Domain:** Evidence-bounded provider-neutral prompt compilation
**Confidence:** MEDIUM

## Summary

Issue #29 requires a Scene Spec compiled from Canon + Visual Bible and provider-specific prompt adapters, with preview/edit support. [CITED: https://github.com/adlink8/novel-mind/issues/29] The roadmap adds deterministic lineage, continuity, negative constraints, prompt version, and adversarial unsupported-detail checks. [CITED: `.planning/ROADMAP.md` Phase 32]

The current AI layer provides routing and provider calls, while Reader Chat freezes context manifests and stores prompt/schema/model/config hashes. [VERIFIED: codebase grep] Current code has no image prompt compiler or image-generation business route. [VERIFIED: codebase grep] Therefore the compiler should be a pure service over an immutable SceneSpec Artifact and should not call providers in this phase. [ASSUMED]

**Primary recommendation:** compile typed, evidence-bounded Scene Specs into canonical prompt sections plus negative constraints; use adapters only to render provider syntax and retain a full diffable lineage envelope. [ASSUMED]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Scene Spec/canonical inputs | Database / Storage | API / Backend | later generation must replay exact inputs. [ASSUMED] |
| Evidence/Visual Bible validation | API / Backend | Database / Storage | server-side gate prevents unsupported detail leakage. [VERIFIED: codebase grep] |
| Prompt preview/diff | Browser / Client | API / Backend | user edits are UI actions; compiler remains server authority. [ASSUMED] |
| Provider prompt rendering | API / Backend | — | adapter converts canonical sections to provider request shape. [VERIFIED: `ai_router.py` analog] |

## User Constraints

- Honor `D-32-01` through `D-32-04` in `32-CONTEXT.md`.
- Do not invoke a live image provider in Phase 32; Phase 22 remains blocked/0 of 3. [CITED: `STATE.md`]

## Standard Stack

| Component | Version | Use | Provenance |
|---|---|---|---|
| Pydantic | `>=2.13` | strict SceneSpec and adapter contracts | [VERIFIED: `backend/requirements.txt`] |
| FastAPI | `>=0.115` | preview/diff API | [VERIFIED: `backend/requirements.txt`] |
| Existing `ai_router`/`ai_service` | current repository | provider configuration/routing seam only | [VERIFIED: codebase grep] |
| Existing SHA-256/canonical helpers | current repository | prompt/spec/config hashes | [VERIFIED: codebase grep] |

Do not add a prompt-templating or provider SDK package until a provider is explicitly selected and package legitimacy is checked. [ASSUMED]

## Architecture Patterns

- Build an immutable `SceneSpec` from `SceneCandidate` + Visual Bible + evidence manifest; reject missing source refs before rendering. [ASSUMED]
- Use ordered sections: `subject`, `action`, `setting`, `composition`, `style`, `continuity`, `negative_constraints`, `uncertainties`; hash the canonical serialization. [ASSUMED]
- Keep adapter output separate from canonical spec, with `adapter_id`, `adapter_version`, `prompt_hash`, and redacted preview. [ASSUMED]
- Let deterministic code enforce allowed evidence IDs and constraints; model text, if later used, may propose fields but cannot write them. [VERIFIED: codebase grep]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---|---|---|
| Provider selection | per-route provider conditionals | existing `ai_router.route_task` seam |
| Prompt lineage | text-only prompt logs | Reader Chat job hashes and immutable manifest pattern |
| Evidence validation | trust compiler caller | source hash/offset and allowed-ref gates |

## Common Pitfalls

- **Canon embellishment:** “cinematic”, age, clothing, lighting, or anatomy added by a template can become false canon; mark defaults as style/interpretation or reject. [CITED: `REQ-VIS-03`; ASSUMED examples]
- **Adapter leakage:** provider-specific syntax in Scene Spec prevents deterministic recompile to another provider. [ASSUMED]
- **Negative constraint omission:** continuity breaks are often caused by missing exclusions, not missing positive description. [CITED: `.planning/ROADMAP.md` Phase 32]
- **Hashing rendered text only:** identical text can come from different evidence/Visual Bible revisions; hash both inputs and output. [VERIFIED: Reader Chat lineage analog]
- **Prompt preview as authority:** edited preview must produce a new candidate revision and retain unsupported changes for review. [ASSUMED]

## Code Examples

```python
compiled = compile_prompt(
    scene_spec=spec,
    adapter=provider_adapter,
    lineage=PromptLineage(
        scene_spec_hash=spec.content_hash,
        visual_bible_revision=spec.visual_bible_revision,
        source_snapshot=spec.source_snapshot,
    ),
)
assert compiled.input_hash != compiled.prompt_hash
```

The separation of input lineage and output hash follows `ReaderGenerationJob` fields. [VERIFIED: codebase grep]

## Validation Architecture

| Req | Behavior | Type | Command | File |
|---|---|---|---|---|
| REQ-VIS-03 | valid evidence/Visual Bible compiles to canonical Scene Spec | unit/contract | `pytest tests/unit/scene_spec -q` | Wave 0 |
| REQ-VIS-03 | unsupported detail fails or is explicitly interpretation | adversarial | `pytest tests/adversarial/test_scene_spec_boundaries.py -q` | Wave 0 |
| REQ-VIS-03 | adapter output is deterministic and lineage-complete | contract | `pytest tests/unit/prompt_compiler -q` | Wave 0 |
| REQ-VIS-03 | preview diff and manual edit preserve source revision | browser/manual | `npm run test:e2e -- scene-spec` | Wave 0 |

Frameworks are the existing pytest/Vitest/Playwright stack. [VERIFIED: project docs]

### Wave 0 Gaps

- golden Scene Spec fixture with continuity and negative constraints;
- provider adapter snapshot fixtures without network calls;
- adversarial unsupported-detail and prompt-diff fixtures.

## Security Domain

V4 owner scope, V5 strict schemas/evidence allowlists, V6 existing hashes, and provider URL/key controls in `url_security.py` apply. [VERIFIED: codebase grep]

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| Python backend environment | pure compiler/contracts | ✓ by repository setup | requirements-managed | mock/in-memory fixtures |
| PostgreSQL 16 | prompt revisions | project-supported | 16 baseline | contract fixtures for Wave 0 |
| Node/npm + Playwright | preview/diff UAT | ✓ by manifest/scripts | manifest-managed | Vitest component coverage |
| Image provider/SDK | deferred to Phase 33 | intentionally not required | — | mock adapters; no network |

No provider dependency was installed or called during research. [VERIFIED: codebase/manifests; ASSUMED runtime availability]

## Sources

- HIGH: Issue #29; requirements/roadmap/state; `ai_router.py`, `ai_service.py`, Reader Chat and Narrative Memory lineage code.
- MEDIUM: architecture docs 03/08/10.

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Phase 32 should be pure/no-provider-call before Phase 33. | A different delivery split changes job boundaries. |
| A2 | Ordered prompt sections are sufficient as provider-neutral representation. | Provider requirements may demand richer typed blocks. |
| A3 | User-edited prompt is a candidate revision, not canon. | Authority contamination if omitted. |

## Open Questions (RESOLVED)

1. Scene Spec and prompt compilation use a provider-neutral adapter protocol; no first provider or production SDK is selected in this phase, and adapter-specific fields never alter the canonical Scene Spec.
2. User-authored visual defaults are stored as explicitly labeled interpretation constraints and versioned with the Scene Spec; they cannot become canon or bypass evidence/owner/version validation.
