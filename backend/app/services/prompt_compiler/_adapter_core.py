"""Provider Prompt Adapter core: contract, deterministic implementations, and
pure compile (Phase 32-03, REQ-VIS-03).

Leaf of the ``adapters.py`` stack — no DB access and no import of the service
seam, so ``compile_prompt`` is exactly reproducible from a canonical
``SceneSpecContract`` alone (D-32-01..D-32-03).

This module owns:

- the ``PromptAdapter`` protocol — the provider-neutral → provider-specific
  adapter contract. Input is only a canonical ``SceneSpecContract``; output
  carries the ordered canonical sections, negative constraints, adapter id /
  version and replayable ``input_hash``/``prompt_hash``. Adapter branching is
  never written back into the SceneSpec (D-32-01) and unsupported detail fails
  closed (D-32-02);
- deterministic adapter implementations: the default mock adapter (byte-identical
  to the Phase 32-02 derivation) and a configured block adapter. Provider tokens
  stay in the rendered prompt string only;
- ``adapter_config_hash`` — deterministic adapter config hash pinned by adapter
  id/version — and the adapter registry/``get_adapter`` lookup;
- ``compile_prompt`` — the pure, replayable compilation. It revalidates the
  SceneSpec contract and the derived revision so a prompt is exactly
  reproducible from its SceneSpec;
- ``PromptArtifact`` — the immutable compile result (revision + deterministic
  lineage envelope, D-32-03).

Errors live in ``_adapter_errors`` (leaf) so the service seam shares them
without a cycle. Split note: extracted from ``adapters.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.schemas.scene_spec import (
    PROMPT_SCHEMA_VERSION,
    SPEC_SECTION_ORDER,
    PromptArtifactLineage,
    PromptRevisionContract,
    SceneSpecContract,
    SceneSpecGateError,
    SpecReviewState,
    build_prompt_lineage,
    build_prompt_sections,
    canonical_scene_spec_hash,
    recompute_prompt_hash,
    recompute_prompt_input_hash,
    spec_negative_constraint_texts,
    spec_uncertainty_texts,
    validate_prompt_revision_contract,
    validate_scene_spec_contract,
)
from app.services.prompt_compiler._adapter_errors import PromptCompileError

# Deterministic adapter lineage (D-32-03). The mock adapter ids/version match
# the Phase 32-02 derivation so the whole chain replays byte-identically.
MOCK_PROMPT_ADAPTER_ID = "mock-provider"
MOCK_PROMPT_ADAPTER_VERSION = "1.0.0"
PROMPT_SCHEMA_HASH = canonical_scene_spec_hash(
    {
        "kind": "prompt_revision.schema",
        "schema_version": PROMPT_SCHEMA_VERSION,
    }
)


def adapter_config_hash(
    adapter_id: str, adapter_version: str, *, options: Mapping[str, Any] | None = None
) -> str:
    """Deterministic adapter config hash; pinned by adapter id/version."""
    payload: dict[str, Any] = {
        "kind": "prompt_revision.config",
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
    }
    if options:
        payload["options"] = options
    return canonical_scene_spec_hash(payload)


# ---------------------------------------------------------------------------
# PromptAdapter contract (provider-neutral → provider-specific)
# ---------------------------------------------------------------------------


@runtime_checkable
class PromptAdapter(Protocol):
    """Adapter contract (D-32-01/02).

    Input is only the canonical ``SceneSpecContract``; ``render`` receives the
    deterministic canonical sections and returns a provider-specific prompt
    string. The adapter never adds, drops or reorders canon and never writes
    back into the SceneSpec; unsupported detail is already fail-closed by the
    compile gates.
    """

    adapter_id: str
    adapter_version: str

    def render(self, sections: Mapping[str, str]) -> str:
        """Render the canonical sections into a provider-specific prompt."""
        ...

    def redact(self, prompt_text: str, *, max_chars: int = 20000) -> str:
        """Return a redacted preview (never secrets/credentials)."""
        ...


class _RedactMixin:
    def redact(self, prompt_text: str, *, max_chars: int = 20000) -> str:
        if max_chars is None or len(prompt_text) <= max_chars:
            return prompt_text
        return prompt_text[:max_chars]


class MockPromptAdapter(_RedactMixin):
    """Default provider-neutral adapter (Phase 32-02 compatible).

    Renders ``SPEC_SECTION_ORDER`` as ``[section]\\ntext`` blocks — byte-identical
    to the Phase 32-02 mock derivation so ``compile_prompt`` with this adapter
    equals ``build_prompt_revision_from_spec`` for the same spec.
    """

    adapter_id = MOCK_PROMPT_ADAPTER_ID
    adapter_version = MOCK_PROMPT_ADAPTER_VERSION

    def render(self, sections: Mapping[str, str]) -> str:
        parts: list[str] = []
        for key in SPEC_SECTION_ORDER:
            if key in sections:
                parts.append(f"[{key}]\n{sections[key]}")
        return "\n\n".join(parts)


class BlockPromptAdapter(_RedactMixin):
    """Configured provider adapter: ``== section ==`` blocks in canonical order.

    Provider tokens live in the rendered prompt string only; canonical meaning
    is preserved because the revision sections/negative constraints still equal
    the SceneSpec's canonical rendering (enforced by the contract gate).
    """

    adapter_id = "prompt-blocks.v1"
    adapter_version = "1.0.0"

    def render(self, sections: Mapping[str, str]) -> str:
        lines: list[str] = []
        for key in SPEC_SECTION_ORDER:
            if key in sections:
                lines.append(f"== {key} ==")
                lines.append(sections[key])
        return "\n".join(lines)


ADAPTER_REGISTRY: dict[str, PromptAdapter] = {
    MockPromptAdapter.adapter_id: MockPromptAdapter(),
    BlockPromptAdapter.adapter_id: BlockPromptAdapter(),
}


def get_adapter(adapter_id: str) -> PromptAdapter:
    try:
        return ADAPTER_REGISTRY[adapter_id]
    except KeyError as exc:
        raise PromptCompileError(
            f"unknown prompt adapter {adapter_id!r}; configured adapters: "
            f"{sorted(ADAPTER_REGISTRY)}"
        ) from exc


# ---------------------------------------------------------------------------
# Pure provider-neutral → provider-specific compilation
# ---------------------------------------------------------------------------


def compile_prompt(
    spec: SceneSpecContract,
    *,
    adapter: PromptAdapter,
    prompt_key: str,
    revision_number: int = 1,
    parent_prompt_revision_id: int | None = None,
) -> PromptRevisionContract:
    """Deterministically compile one provider-specific prompt candidate.

    The canonical sections/negative constraints/uncertainties are derived from
    the SceneSpec only; the adapter renders provider syntax around them and the
    ``input_hash`` (adapter-independent) / ``prompt_hash`` (rendered output)
    replay. No provider is called. Fail-closed gates:
    - the SceneSpec must pass its own contract gate;
    - the derived revision must replay exactly from the SceneSpec (provider-
      specific sections and dropped negative constraints are rejected).
    """
    validate_scene_spec_contract(spec)
    sections = build_prompt_sections(spec)
    prompt_text = adapter.render(sections)
    revision = PromptRevisionContract(
        schema_version=PROMPT_SCHEMA_VERSION,
        artifact_kind="prompt_revision",
        owner_id=spec.owner_id,
        novel_id=spec.novel_id,
        prompt_key=prompt_key,
        revision_number=revision_number,
        parent_prompt_revision_id=parent_prompt_revision_id,
        scene_spec_hash=spec.content_hash,
        visual_bible_revision_hash=spec.visual_bible_revision_hash,
        source_snapshot_id=spec.source_snapshot_id,
        source_snapshot_hash=spec.source_snapshot_hash,
        cutoff_chapter=spec.cutoff_chapter,
        schema_hash=spec.schema_hash,
        prompt_schema_hash=PROMPT_SCHEMA_HASH,
        compiler_version=spec.compiler_version,
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        config_hash=adapter_config_hash(adapter.adapter_id, adapter.adapter_version),
        input_hash="0" * 64,
        prompt_hash="0" * 64,
        sections=dict(sections),
        negative_constraints=spec_negative_constraint_texts(spec),
        uncertainties=spec_uncertainty_texts(spec),
        prompt_text=prompt_text,
        redacted_preview=adapter.redact(prompt_text),
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
    except SceneSpecGateError as exc:
        raise PromptCompileError(
            f"compiled prompt failed its own contract gate: {exc}"
        ) from exc
    return revision


@dataclass(frozen=True)
class PromptArtifact:
    """Immutable compile result: revision + deterministic lineage envelope.

    ``input_hash`` (provider-neutral) always differs from ``prompt_hash``
    (rendered output); the lineage envelope records every input needed to
    replay the prompt (D-32-03).
    """

    revision: PromptRevisionContract
    lineage: PromptArtifactLineage
    provider_calls: int = 0

    @classmethod
    def build(
        cls,
        revision: PromptRevisionContract,
        spec: SceneSpecContract,
        *,
        provider_calls: int = 0,
    ) -> "PromptArtifact":
        return cls(
            revision=revision,
            lineage=build_prompt_lineage(revision, spec),
            provider_calls=provider_calls,
        )
