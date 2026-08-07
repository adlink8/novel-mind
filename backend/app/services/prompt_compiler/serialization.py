"""Replayable prompt artifact serialization (Phase 32-03, REQ-VIS-03).

Pure helpers that make a compiled PromptArtifact byte-replayable and audit the
prompt-edit lineage (D-32-03/D-32-04):

- canonical, sorted-JSON serialization of a PromptRevision and a PromptArtifact
  (revision + lineage envelope) with a stable SHA-256 artifact hash;
- replay gates that fail closed when the serialized revision or lineage does
  not re-derive from the given SceneSpec (deterministic lineage);
- ``diff_prompt_revisions`` — the deterministic edit diff (changed canonical
  sections, negative constraints, uncertainties and prompt text) so a manual
  edit is fully auditable;
- ``edited_spec_with_interpretation`` — the fail-closed human-edit seam: only
  ``user_interpretation`` details may change through the prompt path, evidence/
  Visual Bible canon is never editable here, and a no-op edit is rejected.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.schemas.scene_spec import (
    PROMPT_SCHEMA_VERSION,
    PromptArtifactLineage,
    PromptRevisionContract,
    SceneDetail,
    SceneSpecContract,
    SceneSpecGateError,
    SpecDetailKind,
    SpecSource,
    build_prompt_lineage,
    canonical_scene_spec_hash,
    recompute_prompt_hash,
    recompute_scene_spec_hash,
    scene_spec_content_payload,
    validate_prompt_revision_contract,
    validate_scene_spec_contract,
)


def _revision_payload(revision: PromptRevisionContract) -> dict[str, Any]:
    """Full, deterministic wire payload of a compiled prompt revision."""
    return revision.model_dump(mode="json")


def serialize_prompt_revision(revision: PromptRevisionContract) -> str:
    """Byte-replayable canonical JSON of a compiled prompt revision."""
    return json.dumps(
        _revision_payload(revision),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def prompt_artifact_payload(artifact: Any) -> dict[str, Any]:
    """Canonical payload of a PromptArtifact (revision + lineage envelope)."""
    return {
        "kind": "prompt_artifact",
        "schema_version": PROMPT_SCHEMA_VERSION,
        "revision": _revision_payload(artifact.revision),
        "lineage": artifact.lineage.model_dump(mode="json"),
    }


def serialize_prompt_artifact(artifact: Any) -> str:
    """Byte-replayable canonical JSON of a PromptArtifact."""
    return json.dumps(
        prompt_artifact_payload(artifact),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def prompt_artifact_hash(artifact: Any) -> str:
    """Stable SHA-256 over the canonical PromptArtifact payload."""
    return canonical_scene_spec_hash(prompt_artifact_payload(artifact))


def replay_prompt_revision(payload: Mapping[str, Any]) -> PromptRevisionContract:
    """Reconstruct a PromptRevision from its canonical payload and fail closed
    if ``prompt_hash`` does not replay (deterministic output lineage)."""
    revision = PromptRevisionContract.model_validate(payload)
    if recompute_prompt_hash(revision) != revision.prompt_hash:
        raise SceneSpecGateError("serialized prompt does not replay its prompt_hash")
    return revision


def replay_prompt_artifact(payload: Mapping[str, Any], spec: SceneSpecContract) -> Any:
    """Reconstruct a PromptArtifact from its canonical serialization and fail
    closed on spec/lineage/hash drift.

    Revalidates the full prompt contract against the SceneSpec (stale spec hash,
    Visual Bible revision drift, snapshot/cutoff drift, provider-specific
    sections, dropped negative constraints) and verifies the lineage envelope.
    """
    from app.services.prompt_compiler.adapters import PromptArtifact

    payload = dict(payload)
    revision = PromptRevisionContract.model_validate(payload["revision"])
    lineage = PromptArtifactLineage.model_validate(payload["lineage"])
    try:
        validate_prompt_revision_contract(revision, spec)
    except SceneSpecGateError as exc:
        raise SceneSpecGateError(
            f"serialized prompt does not replay from the SceneSpec: {exc}"
        ) from exc
    if recompute_prompt_hash(revision) != revision.prompt_hash:
        raise SceneSpecGateError("serialized prompt does not replay its prompt_hash")
    expected_lineage = build_prompt_lineage(revision, spec)
    if lineage != expected_lineage:
        raise SceneSpecGateError(
            "serialized lineage does not replay from the revision and spec"
        )
    return PromptArtifact.build(revision, spec, provider_calls=0)


def recompute_hash_from_canonical_payload(payload: Mapping[str, Any]) -> str:
    """Recompute the canonical content hash of a stored canonical payload.

    ``scene_spec_content_payload`` is itself canonical, so the replay hash of a
    stored spec payload is exactly the canonical hash of that payload. Used to
    verify an edited candidate spec stored inside a prompt revision.
    """
    return canonical_scene_spec_hash(dict(payload))


# ---------------------------------------------------------------------------
# Deterministic prompt-edit diff (auditable lineage, D-32-03/04)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptDiffSection:
    """One canonical section whose rendering changed between two revisions."""

    section_key: str
    original: str | None = None
    current: str | None = None


@dataclass(frozen=True)
class PromptDiff:
    """Deterministic diff between a prompt revision and its edited child."""

    original_prompt_hash: str
    current_prompt_hash: str
    parent_prompt_revision_id: int | None
    revision_number: int
    same: bool
    changed_sections: tuple[PromptDiffSection, ...] = ()
    # (item, original_count, current_count) for the separated lists.
    changed_negative_constraints: tuple[tuple[str, int | None, int | None], ...] = ()
    changed_uncertainties: tuple[tuple[str, int | None, int | None], ...] = ()
    prompt_text_changed: bool = False


def _string_list_diff(
    original: Sequence[str], current: Sequence[str]
) -> tuple[tuple[str, int | None, int | None], ...]:
    original_counts = Counter(original)
    current_counts = Counter(current)
    result: list[tuple[str, int | None, int | None]] = []
    for item in sorted(set(original_counts) | set(current_counts)):
        if original_counts[item] != current_counts[item]:
            result.append((item, original_counts.get(item), current_counts.get(item)))
    return tuple(result)


def diff_prompt_revisions(
    original: PromptRevisionContract, current: PromptRevisionContract
) -> PromptDiff:
    """Deterministic section/list/text diff between two prompt revisions."""
    original_sections = original.sections
    current_sections = current.sections
    changed: list[PromptDiffSection] = []
    for key in sorted(set(original_sections) | set(current_sections)):
        if original_sections.get(key) != current_sections.get(key):
            changed.append(
                PromptDiffSection(
                    section_key=key,
                    original=original_sections.get(key),
                    current=current_sections.get(key),
                )
            )
    negative = _string_list_diff(
        original.negative_constraints, current.negative_constraints
    )
    uncertainties = _string_list_diff(original.uncertainties, current.uncertainties)
    prompt_text_changed = original.prompt_text != current.prompt_text
    return PromptDiff(
        original_prompt_hash=original.prompt_hash,
        current_prompt_hash=current.prompt_hash,
        parent_prompt_revision_id=current.parent_prompt_revision_id,
        revision_number=current.revision_number,
        same=(
            not changed
            and not negative
            and not uncertainties
            and not prompt_text_changed
        ),
        changed_sections=tuple(changed),
        changed_negative_constraints=negative,
        changed_uncertainties=uncertainties,
        prompt_text_changed=prompt_text_changed,
    )


# ---------------------------------------------------------------------------
# Human edit seam (D-32-04): explicit candidate revision, unsupported fails closed
# ---------------------------------------------------------------------------


class PromptEditLike(Protocol):
    detail_key: str
    kind: SpecDetailKind
    text: str
    author: str
    rationale: str


def edited_spec_with_interpretation(
    spec: SceneSpecContract, *, edit: PromptEditLike
) -> SceneSpecContract:
    """Apply a human edit to a ``user_interpretation`` detail and return a new
    SceneSpec candidate.

    Fail-closed rules (D-32-02/D-32-04):
    - only ``user_interpretation`` details can change through the prompt seam;
      evidence/Visual Bible canon is never editable here;
    - a missing ``detail_key`` adds a new labeled interpretation detail (with
      author + rationale) scoped to the spec's spoiler cutoff;
    - a no-op edit that leaves the canonical content unchanged is rejected so
      an empty edit can never masquerade as a new revision.
    """
    details = list(spec.details)
    found_index: int | None = None
    for index, detail in enumerate(details):
        if detail.detail_key == edit.detail_key:
            found_index = index
            break

    if found_index is not None:
        detail = details[found_index]
        if detail.source is not SpecSource.USER_INTERPRETATION:
            raise SceneSpecGateError(
                f"detail {edit.detail_key!r} is {detail.source.value}-sourced and "
                "cannot be edited through the prompt seam; only user_interpretation "
                "details are editable"
            )
        details[found_index] = detail.model_copy(
            update={
                "text": edit.text,
                "author": edit.author,
                "rationale": edit.rationale,
            }
        )
    else:
        details.append(
            SceneDetail(
                detail_key=edit.detail_key,
                kind=edit.kind,
                source=SpecSource.USER_INTERPRETATION,
                text=edit.text,
                author=edit.author,
                rationale=edit.rationale,
                spoiler_cutoff=spec.cutoff_chapter,
            )
        )

    edited = spec.model_copy(
        update={
            "revision_number": spec.revision_number + 1,
            "details": details,
        }
    )
    edited = edited.model_copy(
        update={"content_hash": recompute_scene_spec_hash(edited)}
    )
    # A no-op edit must not masquerade as a new revision. revision_number is part
    # of the canonical content hash, so compare the content without it.
    base_content = dict(scene_spec_content_payload(spec))
    base_content.pop("revision_number", None)
    edited_content = dict(scene_spec_content_payload(edited))
    edited_content.pop("revision_number", None)
    if canonical_scene_spec_hash(base_content) == canonical_scene_spec_hash(
        edited_content
    ):
        raise SceneSpecGateError(
            "edit produced no change; an empty edit cannot create a new revision"
        )
    try:
        validate_scene_spec_contract(edited)
    except SceneSpecGateError as exc:
        raise SceneSpecGateError(
            f"edited spec failed its own contract gate: {exc}"
        ) from exc
    return edited


def redacted_preview(prompt_text: str, *, max_chars: int = 20000) -> str:
    """Redacted preview; never exposes provider secrets/credentials."""
    if max_chars is None or len(prompt_text) <= max_chars:
        return prompt_text
    return prompt_text[:max_chars]
