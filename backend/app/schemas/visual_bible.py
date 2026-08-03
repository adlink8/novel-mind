"""Visual Bible candidate Artifact strict contracts (Phase 30-01, REQ-VIS-01).

The Visual Bible is an evidence-linked, candidate-only, versioned Artifact
(D-30-01..D-30-04). This module owns:

- strict typed wire contracts with ``extra="forbid"`` and frozen immutable
  lineage payloads,
- the four-label authority vocabulary (canon_fact / probable_inference /
  literary_interpretation / user_interpretation),
- canonical hash helpers so claim/version hashes are byte-replayable,
- server-side gates that fail closed on missing/illegal evidence, spoiler
  cutoffs, wrong hashes, duplicate stable IDs and illegal review transitions.

No code in this module writes to the database and nothing promotes a
generated or unreviewed visual to canon; approval is an append-only review
event applied by the owning service (Phase 30-04).
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VISUAL_SCHEMA_VERSION = "visual-bible.v1"
VISUAL_ARTIFACT_KIND = "visual_bible"


class StrictVisualBibleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualAuthority(StrEnum):
    CANON_FACT = "canon_fact"
    PROBABLE_INFERENCE = "probable_inference"
    LITERARY_INTERPRETATION = "literary_interpretation"
    USER_INTERPRETATION = "user_interpretation"


class VisualEntityType(StrEnum):
    CHARACTER = "character"
    PLACE = "place"
    ITEM = "item"
    FACTION = "faction"
    STYLE = "style"


class VisualReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    SUPERSEDE = "supersede"
    NEEDS_RELINK = "needs_relink"


class VisualReviewState(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    NEEDS_RELINK = "needs_relink"


class VisualRightsStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    CLEARED = "cleared"
    PENDING = "pending"
    DENIED = "denied"


class VisualActorSource(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


class VisualBibleGateError(ValueError):
    """Fail-closed gate violation while validating a Visual Bible candidate."""


# ---------------------------------------------------------------------------
# Canonical hashing (byte-replayable lineage)
# ---------------------------------------------------------------------------


def canonical_visual_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention)."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def canonical_claim_payload(claim: "VisualClaimContract") -> dict[str, Any]:
    return {
        "claim_key": claim.claim_key,
        "entity_stable_id": claim.entity_stable_id,
        "authority": claim.authority.value,
        "description": claim.description,
        "author": claim.author,
        "rationale": claim.rationale,
        "cutoff_chapter": claim.cutoff_chapter,
        "evidence_keys": [ref.evidence_key for ref in claim.evidence_refs],
    }


def claim_content_hash(claim: "VisualClaimContract") -> str:
    return canonical_visual_hash(canonical_claim_payload(claim))


# ---------------------------------------------------------------------------
# Evidence and claim contracts
# ---------------------------------------------------------------------------


class VisualEvidenceRef(StrictVisualBibleModel):
    """Primary-text evidence locator; offsets/hash/cutoff are server-verified."""

    evidence_key: str = Field(min_length=1, max_length=180)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt: str | None = Field(default=None, max_length=2000)
    cutoff_chapter: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_offsets_and_cutoff(self) -> "VisualEvidenceRef":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        if self.chapter_number > self.cutoff_chapter:
            raise ValueError(
                "evidence chapter_number must not exceed the spoiler cutoff_chapter"
            )
        return self


class VisualClaimContract(StrictVisualBibleModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_key: str = Field(min_length=1, max_length=180)
    entity_stable_id: str = Field(min_length=1, max_length=180)
    authority: VisualAuthority
    description: str = Field(min_length=1, max_length=4000)
    author: str | None = Field(default=None, min_length=1, max_length=200)
    rationale: str | None = Field(default=None, max_length=2000)
    cutoff_chapter: int = Field(ge=1)
    claim_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[VisualEvidenceRef] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_authority_shape(self) -> "VisualClaimContract":
        if self.authority is VisualAuthority.CANON_FACT:
            if not self.evidence_refs:
                raise ValueError("canon_fact requires at least one evidence ref")
        else:
            if not self.author:
                raise ValueError(
                    f"{self.authority.value} claims require an author"
                )
            if not self.rationale:
                raise ValueError(
                    f"{self.authority.value} claims require a rationale"
                )
        return self


class VisualEntityContract(StrictVisualBibleModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = Field(min_length=1, max_length=180)
    entity_key: str = Field(min_length=1, max_length=180)
    entity_type: VisualEntityType
    description: str = Field(min_length=1, max_length=8000)
    authority: VisualAuthority
    disclosure_cutoff: int = Field(ge=1)


class VisualReferenceAssetContract(StrictVisualBibleModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_key: str = Field(min_length=1, max_length=180)
    asset_id: str = Field(min_length=1, max_length=200)
    mime_type: str = Field(min_length=1, max_length=100)
    bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rights_status: VisualRightsStatus = VisualRightsStatus.UNREVIEWED
    provenance: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Immutable version contract (candidate envelope with full lineage)
# ---------------------------------------------------------------------------


class VisualBibleVersionContract(StrictVisualBibleModel):
    """Frozen candidate envelope; every lineage field is mandatory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["visual-bible.v1"] = "visual-bible.v1"
    artifact_kind: Literal["visual_bible"] = "visual_bible"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=120)
    revision_number: int = Field(ge=1)
    parent_version_id: int | None = Field(default=None, gt=0)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    style_profile: dict[str, Any] | None = None
    constraints: list[dict[str, Any]] | None = None
    entities: list[VisualEntityContract] = Field(default_factory=list)
    claims: list[VisualClaimContract] = Field(default_factory=list)
    reference_assets: list[VisualReferenceAssetContract] = Field(
        default_factory=list
    )
    review_state: VisualReviewState = VisualReviewState.CANDIDATE


def version_manifest_payload(
    version: VisualBibleVersionContract,
) -> dict[str, Any]:
    """Canonical manifest payload used for manifest_hash verification."""
    return {
        "artifact_kind": VISUAL_ARTIFACT_KIND,
        "schema_version": VISUAL_SCHEMA_VERSION,
        "owner_id": version.owner_id,
        "novel_id": version.novel_id,
        "version_key": version.version_key,
        "revision_number": version.revision_number,
        "parent_version_id": version.parent_version_id,
        "source_snapshot_id": version.source_snapshot_id,
        "source_snapshot_hash": version.source_snapshot_hash,
        "cutoff_chapter": version.cutoff_chapter,
        "schema_hash": version.schema_hash,
        "policy_hash": version.policy_hash,
        "prompt_hash": version.prompt_hash,
        "model_hash": version.model_hash,
        "config_hash": version.config_hash,
        "style_profile": version.style_profile,
        "constraints": version.constraints,
        "entities": [
            {
                "stable_id": entity.stable_id,
                "entity_key": entity.entity_key,
                "entity_type": entity.entity_type.value,
                "description": entity.description,
                "authority": entity.authority.value,
                "disclosure_cutoff": entity.disclosure_cutoff,
            }
            for entity in version.entities
        ],
        "claims": [
            {
                "claim_key": claim.claim_key,
                "entity_stable_id": claim.entity_stable_id,
                "authority": claim.authority.value,
                "description": claim.description,
                "cutoff_chapter": claim.cutoff_chapter,
                "evidence_keys": [ref.evidence_key for ref in claim.evidence_refs],
            }
            for claim in version.claims
        ],
        "reference_assets": [
            {
                "asset_key": asset.asset_key,
                "asset_id": asset.asset_id,
                "mime_type": asset.mime_type,
                "bytes_hash": asset.bytes_hash,
                "rights_status": asset.rights_status.value,
            }
            for asset in version.reference_assets
        ],
    }


def recompute_manifest_hash(version: VisualBibleVersionContract) -> str:
    return canonical_visual_hash(version_manifest_payload(version))


# ---------------------------------------------------------------------------
# Server-side gates
# ---------------------------------------------------------------------------


def validate_claim_hash(claim: VisualClaimContract) -> None:
    """Reject claims whose claim_hash does not match canonical content."""
    if claim.claim_hash != claim_content_hash(claim):
        raise VisualBibleGateError(
            f"claim {claim.claim_key!r} content hash mismatch"
        )


def validate_evidence_against_source(
    source_text: str, ref: VisualEvidenceRef
) -> None:
    """Revalidate an evidence ref against the authoritative source slice."""
    if ref.source_start < 0 or ref.source_end > len(source_text):
        raise VisualBibleGateError(
            f"evidence {ref.evidence_key!r} offsets out of range"
        )
    sliced = source_text[ref.source_start : ref.source_end]
    if sha256(sliced.encode("utf-8")).hexdigest() != ref.content_hash:
        raise VisualBibleGateError(
            f"evidence {ref.evidence_key!r} content hash mismatch"
        )


def validate_claim_evidence(
    claim: VisualClaimContract,
    *,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    cutoff_chapter: int,
) -> None:
    """Every evidence ref must belong to the version snapshot and cutoff."""
    if claim.authority is not VisualAuthority.CANON_FACT:
        return
    if not claim.evidence_refs:
        raise VisualBibleGateError(
            f"canon_fact claim {claim.claim_key!r} has no evidence refs"
        )
    for ref in claim.evidence_refs:
        if ref.source_snapshot_id != source_snapshot_id:
            raise VisualBibleGateError(
                f"claim {claim.claim_key!r} evidence {ref.evidence_key!r} "
                "source_snapshot_id does not match the version"
            )
        if ref.source_snapshot_hash != source_snapshot_hash:
            raise VisualBibleGateError(
                f"claim {claim.claim_key!r} evidence {ref.evidence_key!r} "
                "source_snapshot_hash does not match the version"
            )
        if ref.cutoff_chapter != cutoff_chapter:
            raise VisualBibleGateError(
                f"claim {claim.claim_key!r} evidence {ref.evidence_key!r} "
                "cutoff_chapter does not match the version"
            )
        if ref.chapter_number > cutoff_chapter:
            raise VisualBibleGateError(
                f"claim {claim.claim_key!r} evidence {ref.evidence_key!r} "
                "chapter_number exceeds the version spoiler cutoff"
            )


def validate_version_contract(version: VisualBibleVersionContract) -> None:
    """Cross-field gates: unique stable IDs, entity refs, manifest hash."""
    stable_ids = [entity.stable_id for entity in version.entities]
    if len(set(stable_ids)) != len(stable_ids):
        raise VisualBibleGateError("duplicate entity stable_id in version")

    entity_keys = [entity.entity_key for entity in version.entities]
    if len(set(entity_keys)) != len(entity_keys):
        raise VisualBibleGateError("duplicate entity_key in version")

    known_stable_ids = set(stable_ids)
    known_entity_keys = set(entity_keys)
    claim_keys = [claim.claim_key for claim in version.claims]
    if len(set(claim_keys)) != len(claim_keys):
        raise VisualBibleGateError("duplicate claim_key in version")

    asset_keys = [asset.asset_key for asset in version.reference_assets]
    if len(set(asset_keys)) != len(asset_keys):
        raise VisualBibleGateError("duplicate reference asset_key in version")

    for claim in version.claims:
        if claim.entity_stable_id not in known_stable_ids:
            raise VisualBibleGateError(
                f"claim {claim.claim_key!r} references unknown entity stable_id "
                f"{claim.entity_stable_id!r}"
            )
        validate_claim_hash(claim)
        validate_claim_evidence(
            claim,
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
        )

    if recompute_manifest_hash(version) != version.manifest_hash:
        raise VisualBibleGateError("version manifest_hash does not match content")


# ---------------------------------------------------------------------------
# Review actions (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------

# Action → resulting review state. ``edit`` records the human intent; the
# caller creates a child version and the edited version keeps its state.
REVIEW_ACTION_TO_STATE: dict[VisualReviewAction, VisualReviewState | None] = {
    VisualReviewAction.APPROVE: VisualReviewState.APPROVED,
    VisualReviewAction.REJECT: VisualReviewState.REJECTED,
    VisualReviewAction.SUPERSEDE: VisualReviewState.SUPERSEDED,
    VisualReviewAction.NEEDS_RELINK: VisualReviewState.NEEDS_RELINK,
    VisualReviewAction.EDIT: None,
}

LEGAL_REVIEW_TRANSITIONS: dict[
    VisualReviewState, frozenset[VisualReviewAction]
] = {
    VisualReviewState.CANDIDATE: frozenset(
        {
            VisualReviewAction.APPROVE,
            VisualReviewAction.REJECT,
            VisualReviewAction.EDIT,
            VisualReviewAction.SUPERSEDE,
            VisualReviewAction.NEEDS_RELINK,
        }
    ),
    VisualReviewState.NEEDS_RELINK: frozenset(
        {
            VisualReviewAction.APPROVE,
            VisualReviewAction.REJECT,
            VisualReviewAction.EDIT,
            VisualReviewAction.SUPERSEDE,
        }
    ),
    VisualReviewState.APPROVED: frozenset(
        {VisualReviewAction.SUPERSEDE, VisualReviewAction.NEEDS_RELINK}
    ),
    VisualReviewState.REJECTED: frozenset(
        {VisualReviewAction.EDIT, VisualReviewAction.SUPERSEDE}
    ),
    VisualReviewState.SUPERSEDED: frozenset(),
}


def is_legal_review_action(
    state: VisualReviewState | str, action: VisualReviewAction | str
) -> bool:
    current = VisualReviewState(state)
    requested = VisualReviewAction(action)
    return requested in LEGAL_REVIEW_TRANSITIONS[current]


def validate_legal_review_action(
    state: VisualReviewState | str, action: VisualReviewAction | str
) -> None:
    current = VisualReviewState(state)
    requested = VisualReviewAction(action)
    if not is_legal_review_action(current, requested):
        raise VisualBibleGateError(
            f"illegal review action {requested.value!r} from state {current.value!r}"
        )


def review_state_after(
    state: VisualReviewState | str, action: VisualReviewAction | str
) -> VisualReviewState:
    current = VisualReviewState(state)
    requested = VisualReviewAction(action)
    validate_legal_review_action(current, requested)
    target = REVIEW_ACTION_TO_STATE[requested]
    return current if target is None else target


class VisualReviewEventInput(StrictVisualBibleModel):
    """One append-only review action candidate; result state is server-derived."""

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    action: VisualReviewAction
    actor_source: VisualActorSource
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    event_key: str = Field(min_length=1, max_length=160)
    from_review_state: VisualReviewState


def validate_review_event(
    event: VisualReviewEventInput,
    *,
    seen_event_keys: frozenset[str] | set[str] | None = None,
) -> VisualReviewState:
    """Validate a review action and return its derived result state.

    Idempotency: a repeated ``event_key`` (e.g. a retried approval) is
    rejected here; the durable layer enforces the unique event_key constraint
    so a duplicate action can never create a second approval.
    """
    seen = set(seen_event_keys or ())
    if event.event_key in seen:
        raise VisualBibleGateError(
            f"duplicate review event_key {event.event_key!r} (idempotency)"
        )
    return review_state_after(event.from_review_state, event.action)


# ---------------------------------------------------------------------------
# Visible envelopes (evidence + authority labels for the review workspace)
# ---------------------------------------------------------------------------


class VisualEvidenceRefView(StrictVisualBibleModel):
    evidence_key: str
    source_snapshot_id: str
    source_snapshot_hash: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    excerpt: str | None = None
    cutoff_chapter: int


class VisualClaimView(StrictVisualBibleModel):
    claim_key: str
    entity_stable_id: str
    authority: VisualAuthority
    description: str
    author: str | None = None
    rationale: str | None = None
    cutoff_chapter: int
    claim_hash: str
    evidence_refs: list[VisualEvidenceRefView] = Field(default_factory=list)


class VisualEntityView(StrictVisualBibleModel):
    stable_id: str
    entity_key: str
    entity_type: VisualEntityType
    description: str
    authority: VisualAuthority
    disclosure_cutoff: int
    claims: list[VisualClaimView] = Field(default_factory=list)


class VisualReferenceAssetView(StrictVisualBibleModel):
    asset_key: str
    asset_id: str
    mime_type: str
    bytes_hash: str
    rights_status: VisualRightsStatus
    approved: bool = False


class VisualReviewEventView(StrictVisualBibleModel):
    action: VisualReviewAction
    actor_source: VisualActorSource
    actor: str
    reason: str
    event_key: str
    from_review_state: VisualReviewState
    to_review_state: VisualReviewState


class VisualBibleVersionView(StrictVisualBibleModel):
    """Read envelope: candidate review state + evidence + authority labels."""

    id: int
    owner_id: int
    novel_id: int
    version_key: str
    revision_number: int
    parent_version_id: int | None = None
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    schema_version: str
    schema_hash: str
    policy_hash: str
    manifest_hash: str
    review_state: VisualReviewState
    style_profile: dict[str, Any] | None = None
    constraints: list[dict[str, Any]] | None = None
    entities: list[VisualEntityView] = Field(default_factory=list)
    reference_assets: list[VisualReferenceAssetView] = Field(default_factory=list)
    review_events: list[VisualReviewEventView] = Field(default_factory=list)
