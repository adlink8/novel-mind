"""Shared authority / disclosure / EvidenceRef contract consumed by world projection.

Phase 27-04 / REQ-WM-04 (D-01, D-02, D-05, D-06).

This module is the single serialization contract that the backend world-model /
queryplan adapters and the frontend evidence panel both consume:

- ``WorldProjectionItem`` carries the four distinct epistemic authority labels
  (canon_fact / probable_inference / literary_interpretation /
  user_interpretation) together with disclosure timing (known_at /
  disclosure_cutoff) next to an allowlisted leaf ``EvidenceRef`` (chapter +
  Unicode offsets + content hash + frozen snapshot), plus the durable lineage.
- ``WorldProjectionView`` is the serializable projection: candidate items,
  isolated user-interpretation overrides, preserved authority labels and the
  frozen manifest checksum binding (FrozenManifest freezes the owner/version/
  cutoff/hash-bound EvidenceRefs).

Fail-closed guarantees:

- An item's authority is validated against the four canonical labels; a relabeled
  or unknown label can never serialize (no silent upgrades, D-01).
- ``evidence_key`` must be leaf-allowlist-shaped (``qp:chapter:start:end:hash``);
  a summary / score / routing / chat-text key is rejected (D-08).
- Candidate items (``gate_status != passed``) serialize as candidate-only; the
  view never promotes them and never claims availability.
- ``user_interpretation`` overrides are never merged into the candidate items
  (D-06 isolation).
- There is no active-pointer / promotion / cutover field (D-02).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from app.services.queryplan.schemas import (
    Hash64,
    NonNegInt,
    PositiveInt,
    StrictQueryPlanModel,
)

Key = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]

WORLD_PROJECTION_CONTRACT_VERSION = "world-model-projection.v1"

AUTHORITY_CANON_FACT = "canon_fact"
AUTHORITY_PROBABLE_INFERENCE = "probable_inference"
AUTHORITY_LITERARY_INTERPRETATION = "literary_interpretation"
AUTHORITY_USER_INTERPRETATION = "user_interpretation"

AUTHORITY_LABELS: tuple[str, ...] = (
    AUTHORITY_CANON_FACT,
    AUTHORITY_PROBABLE_INFERENCE,
    AUTHORITY_LITERARY_INTERPRETATION,
    AUTHORITY_USER_INTERPRETATION,
)

_LEAF_EVIDENCE_KEY_RE = re.compile(
    r"^qp:[0-9]+:[0-9]+:[0-9]+:[0-9a-f]{64}$"
)


def leaf_evidence_key(
    *, chapter_id: int, source_start: int, source_end: int, content_hash: str
) -> str:
    """Deterministic leaf-only allowlist key (D-07/D-08).

    Composed only of leaf fields (chapter + Unicode offsets + content hash);
    a summary, score, routing id or chat-text id can never produce this shape.
    """
    return f"qp:{chapter_id}:{source_start}:{source_end}:{content_hash}"


def is_leaf_evidence_key(value: str) -> bool:
    """Fail-closed shape check for a citation key (D-08)."""
    return _LEAF_EVIDENCE_KEY_RE.match(value) is not None


class WorldProjectionItem(StrictQueryPlanModel):
    """One authority-labeled world projection item (serializable contract).

    ``kind`` distinguishes character epistemic claims from world facts
    (entities / rules / events). ``approved`` reflects an explicit gate
    approval; ``is_override`` marks an isolated user interpretation (D-06).
    """

    claim_key: Key
    kind: Literal["character", "world"]
    subject: Key
    aspect: Key
    proposition: Annotated[str, Field(min_length=1, max_length=1000)]
    authority: str
    known_at: NonNegInt
    disclosure_cutoff: PositiveInt
    pov: str | None = None
    gate_status: Literal["pending", "passed", "rejected"]
    approved: bool = False
    is_override: bool = False
    evidence_key: Key
    chapter_id: PositiveInt
    chapter_number: PositiveInt
    source_start: NonNegInt
    source_end: PositiveInt
    content_hash: Hash64
    source_snapshot_hash: Hash64
    lineage: tuple[Key, ...] = ()

    @field_validator("authority")
    @classmethod
    def _authority_must_be_canonical(cls, value: str) -> str:
        if value not in AUTHORITY_LABELS:
            raise ValueError(
                "authority must be one of the four canonical labels "
                "(canon_fact/probable_inference/literary_interpretation/"
                "user_interpretation); a relabeled or unknown label can never "
                "serialize (D-01)"
            )
        return value

    @field_validator("evidence_key")
    @classmethod
    def _evidence_key_must_be_leaf(cls, value: str) -> str:
        if not is_leaf_evidence_key(value):
            raise ValueError(
                "evidence_key must be a leaf allowlist key "
                "qp:<chapter_id>:<start>:<end>:<content_hash>; summaries, "
                "scores, routing metadata and chat text are never evidence (D-08)"
            )
        return value

    @field_validator("source_end")
    @classmethod
    def _half_open_range(cls, value: int, info) -> int:
        start = info.data.get("source_start")
        if start is not None and value <= start:
            raise ValueError("source_end must be greater than source_start")
        return value


class WorldProjectionView(StrictQueryPlanModel):
    """Durable, serializable world projection exposed to the browser.

    ``available`` is True only when approved candidate evidence exists. Hidden
    or missing projections are explicit ``unavailable`` — never an empty success
    (D-05). ``manifest_checksum`` binds the projection to the frozen manifest so
    evidence lineage stays durable and replayable.
    """

    schema_version: str = WORLD_PROJECTION_CONTRACT_VERSION
    available: bool
    status: Literal["available", "candidate_only", "unavailable"]
    cutoff: NonNegInt
    items: tuple[WorldProjectionItem, ...]
    overrides: tuple[WorldProjectionItem, ...]
    authorities: tuple[str, ...]
    manifest_checksum: Hash64 | None = None
    snapshot_hash: Hash64 | None = None

    @field_validator("authorities")
    @classmethod
    def _authorities_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for authority in value:
            if authority not in AUTHORITY_LABELS:
                raise ValueError(
                    f"unknown authority label '{authority}' cannot serialize (D-01)"
                )
        return tuple(dict.fromkeys(value))
