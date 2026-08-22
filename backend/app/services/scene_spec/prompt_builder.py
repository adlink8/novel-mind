"""Deterministic PromptRevision derivation (provider-neutral, D-32-01/03).

This module owns the pure ``build_prompt_revision_from_spec`` seam extracted
from the scene-spec compiler: the prompt string is never the authority — the
canonical sections render in ``SPEC_SECTION_ORDER``, negative constraints and
uncertainties stay separated, and both ``input_hash`` (adapter-neutral) and
``prompt_hash`` (rendered output) replay. No provider is called.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.schemas.scene_spec import (
    PROMPT_SCHEMA_VERSION,
    PromptRevisionContract,
    SceneSpecContract,
    SpecReviewState,
    build_prompt_sections,
    canonical_scene_spec_hash,
    recompute_prompt_hash,
    recompute_prompt_input_hash,
    spec_negative_constraint_texts,
    spec_uncertainty_texts,
    validate_prompt_revision_contract,
)

from .errors import SceneSpecCompileError

PROMPT_SCHEMA_HASH = canonical_scene_spec_hash(
    {
        "kind": "prompt_revision.schema",
        "schema_version": PROMPT_SCHEMA_VERSION,
    }
)
# Default provider-neutral adapter lineage for the mock derivation.
MOCK_PROMPT_ADAPTER_ID = "mock-provider"
MOCK_PROMPT_ADAPTER_VERSION = "1.0.0"
MOCK_PROMPT_CONFIG_HASH = canonical_scene_spec_hash(
    {
        "kind": "prompt_revision.config",
        "adapter_id": MOCK_PROMPT_ADAPTER_ID,
        "adapter_version": MOCK_PROMPT_ADAPTER_VERSION,
    }
)


def _render_prompt_text(sections: Mapping[str, str]) -> str:
    from app.schemas.scene_spec import SPEC_SECTION_ORDER

    parts: list[str] = []
    for key in SPEC_SECTION_ORDER:
        if key in sections:
            parts.append(f"[{key}]\n{sections[key]}")
    return "\n\n".join(parts)


def build_prompt_revision_from_spec(
    spec: SceneSpecContract,
    *,
    prompt_key: str,
    revision_number: int = 1,
    adapter_id: str = MOCK_PROMPT_ADAPTER_ID,
    adapter_version: str = MOCK_PROMPT_ADAPTER_VERSION,
    config_hash: str = MOCK_PROMPT_CONFIG_HASH,
) -> PromptRevisionContract:
    """Deterministically derive the provider-neutral PromptRevision candidate.

    The prompt string is never the authority (D-32-01): this renders the
    canonical sections in SPEC_SECTION_ORDER, keeps negative constraints and
    uncertainties separated, and replays both ``input_hash`` (adapter-neutral)
    and ``prompt_hash`` (rendered output). No provider is called.
    """
    sections = build_prompt_sections(spec)
    negative = spec_negative_constraint_texts(spec)
    uncertainties = spec_uncertainty_texts(spec)
    prompt_text = _render_prompt_text(sections)
    revision = PromptRevisionContract(
        schema_version=PROMPT_SCHEMA_VERSION,
        artifact_kind="prompt_revision",
        owner_id=spec.owner_id,
        novel_id=spec.novel_id,
        prompt_key=prompt_key,
        revision_number=revision_number,
        parent_prompt_revision_id=None,
        scene_spec_hash=spec.content_hash,
        visual_bible_revision_hash=spec.visual_bible_revision_hash,
        source_snapshot_id=spec.source_snapshot_id,
        source_snapshot_hash=spec.source_snapshot_hash,
        cutoff_chapter=spec.cutoff_chapter,
        schema_hash=spec.schema_hash,
        prompt_schema_hash=PROMPT_SCHEMA_HASH,
        compiler_version=spec.compiler_version,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        config_hash=config_hash,
        input_hash="0" * 64,
        prompt_hash="0" * 64,
        sections=dict(sections),
        negative_constraints=negative,
        uncertainties=uncertainties,
        prompt_text=prompt_text,
        redacted_preview=prompt_text,
        review_state=SpecReviewState.CANDIDATE,
    )
    revision = revision.model_copy(
        update={
            "input_hash": recompute_prompt_input_hash(revision, spec),
            "prompt_hash": recompute_prompt_hash(revision),
        }
    )
    try:
        validate_prompt_revision_contract(revision, spec)
    except ValueError as exc:
        raise SceneSpecCompileError(
            f"derived prompt failed its own contract gate: {exc}"
        ) from exc
    return revision
