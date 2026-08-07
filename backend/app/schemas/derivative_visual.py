"""Phase 38-01 forked Visual Bible strict wire contracts (D-38-01/D-38-02).

The derivative Visual Bible is a **separate namespace/version/owner/provenance**
copy of the Original Visual Bible snapshot, produced only by an explicit fork
transaction (``fork.py``). This module owns:

- strict ``extra="forbid"`` frozen write contracts for a derivative version,
  its identity/style rows and its reference assets — the client can never
  inject an original ``visual_bible_versions`` row, an Original Canon asset,
  an approval flag or an un-declared divergence;
- the closed derivative namespace (``fanfiction_visual``) and review state
  vocabularies;
- canonical hash helpers so the fork lineage / manifest is byte-replayable;
- fail-closed gates: a non-empty explicit ``divergence`` declaration, sealed
  namespace, source snapshot hash / manifest hash matching the Original Visual
  Bible snapshot, and unique identity/style/asset keys.

Nothing in this module writes to the database and nothing promotes a
derivative candidate to canon; approval is an append-only review event applied
by ``lineage.py``.
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DERIVATIVE_VISUAL_SCHEMA_VERSION = "derivative-visual.v1"
# D-38-01: the derivative Visual Bible lives in its own namespace; it can
# never reuse the Original Canon namespace or a plain file path as identity.
DERIVATIVE_VISUAL_NAMESPACE = "fanfiction_visual"

# Phase 38-02 canonical derivative Scene Spec envelope (REQ-FORK-04/REQ-CRE-06).
DERIVATIVE_SCENE_SPEC_SCHEMA_VERSION = "derivative-scene-spec.v1"
DERIVATIVE_SCENE_SPEC_ARTIFACT_KIND = "derivative_scene_spec"
DERIVATIVE_SCENE_SPEC_COMPILER_ID = "derivative-scene-spec.v1"
DERIVATIVE_SCENE_SPEC_COMPILER_VERSION = "1.0.0"
# A derivative spec only carries negative constraints sourced from the sealed
# story context (``scene_spec``) or the explicit branch fork (``derivative``).
DERIVATIVE_NEGATIVE_SOURCES = ("scene_spec", "derivative")


class StrictDerivativeVisualModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeVisualState(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    NEEDS_RELINK = "needs_relink"


class DerivativeVisualAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    SUPERSEDE = "supersede"
    NEEDS_RELINK = "needs_relink"


class DerivativeVisualEntityType(StrEnum):
    CHARACTER = "character"
    PLACE = "place"
    ITEM = "item"
    FACTION = "faction"
    STYLE = "style"


class DerivativeVisualRightsStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    CLEARED = "cleared"
    PENDING = "pending"
    DENIED = "denied"


class DerivativeVisualActorSource(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


class DerivativeVisualGateError(ValueError):
    """Fail-closed gate violation while validating a derivative visual fork."""


# ---------------------------------------------------------------------------
# Canonical hashing (byte-replayable fork lineage)
# ---------------------------------------------------------------------------


def canonical_derivative_visual_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention)."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Identity / style / reference / divergence row contracts
# ---------------------------------------------------------------------------


class DerivativeVisualEntityContract(StrictDerivativeVisualModel):
    """One identity/style row in a derivative version.

    ``source_entity_ref`` pins the exact Original Visual Bible entity this row
    derives from (stable id + entity id + content hash), so a derivative row
    can never be silently repointed onto another original entity and the
    Original rows stay immutable (REQ-FORK-04).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = Field(min_length=1, max_length=180)
    entity_key: str = Field(min_length=1, max_length=180)
    entity_type: DerivativeVisualEntityType
    description: str = Field(min_length=1, max_length=8000)
    authority: str = Field(
        pattern=r"^(canon_fact|probable_inference|literary_interpretation|user_interpretation)$"
    )
    divergence: dict[str, Any] = Field(default_factory=dict, min_length=1)
    source_entity_ref: dict[str, Any] = Field(default_factory=dict, min_length=1)
    disclosure_cutoff: int = Field(ge=1)

    @model_validator(mode="after")
    def require_source_entity_ref(self) -> "DerivativeVisualEntityContract":
        ref = self.source_entity_ref
        for key in ("source_entity_id", "source_entity_key", "source_entity_hash"):
            if not ref.get(key):
                raise ValueError(
                    f"source_entity_ref must carry {key!r} for the original entity"
                )
        if len(str(ref.get("source_entity_hash"))) != 64:
            raise ValueError(
                "source_entity_ref.source_entity_hash must be 64 hex chars"
            )
        return self


class DerivativeVisualAssetContract(StrictDerivativeVisualModel):
    """Immutable derivative reference-asset metadata.

    ``source_asset_ref`` pins the exact Original Visual Bible asset
    (``source_asset_id`` + ``source_bytes_hash``); a fork that reuses an
    original asset path must always carry the source hash so the derivative
    cannot mutate the Original reference (REQ-FORK-04).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_key: str = Field(min_length=1, max_length=180)
    asset_id: str = Field(min_length=1, max_length=200)
    mime_type: str = Field(min_length=1, max_length=100)
    bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rights_status: DerivativeVisualRightsStatus = (
        DerivativeVisualRightsStatus.UNREVIEWED
    )
    provenance: dict[str, Any] = Field(default_factory=dict)
    source_asset_ref: dict[str, Any] = Field(default_factory=dict, min_length=1)

    @model_validator(mode="after")
    def require_source_asset_ref(self) -> "DerivativeVisualAssetContract":
        ref = self.source_asset_ref
        for key in ("source_asset_id", "source_bytes_hash"):
            if not ref.get(key):
                raise ValueError(
                    f"source_asset_ref must carry {key!r} for the original asset"
                )
        if len(str(ref.get("source_bytes_hash"))) != 64:
            raise ValueError("source_asset_ref.source_bytes_hash must be 64 hex chars")
        return self


# ---------------------------------------------------------------------------
# Immutable derivative version contract (explicit fork envelope)
# ---------------------------------------------------------------------------


class DerivativeVisualVersionContract(StrictDerivativeVisualModel):
    """Frozen explicit-fork envelope with full source/owner/provenance lineage.

    ``namespace`` is sealed to ``fanfiction_visual`` at the contract level so a
    derivative write can never target the Original Visual Bible namespace.
    ``divergence`` and ``provenance`` must be declared explicitly (D-38-02):
    any deviation from the Original snapshot is a first-class recorded fact.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["derivative-visual.v1"] = "derivative-visual.v1"
    namespace: Literal["fanfiction_visual"] = "fanfiction_visual"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    fork_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=160)
    revision_number: int = Field(ge=1)
    parent_version_id: int | None = Field(default=None, gt=0)
    # Immutable Original Visual Bible snapshot this derivative is forked from.
    source_version_id: int = Field(gt=0)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    divergence: dict[str, Any] = Field(default_factory=dict, min_length=1)
    provenance: dict[str, Any] = Field(default_factory=dict)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    style_profile: dict[str, Any] | None = None
    constraints: list[dict[str, Any]] | None = None
    entities: list[DerivativeVisualEntityContract] = Field(default_factory=list)
    reference_assets: list[DerivativeVisualAssetContract] = Field(default_factory=list)
    review_state: DerivativeVisualState = DerivativeVisualState.CANDIDATE

    @model_validator(mode="after")
    def require_explicit_divergence(self) -> "DerivativeVisualVersionContract":
        if not self.divergence:
            raise ValueError(
                "divergence must be declared explicitly (D-38-02); "
                "an empty divergence cannot be silently forked"
            )
        if not self.provenance:
            raise ValueError("provenance must be declared explicitly (D-38-01)")
        return self


def derivative_visual_manifest_payload(
    version: DerivativeVisualVersionContract,
) -> dict[str, Any]:
    """Canonical manifest payload used for manifest_hash verification."""
    return {
        "artifact_kind": "derivative_visual",
        "schema_version": DERIVATIVE_VISUAL_SCHEMA_VERSION,
        "namespace": DERIVATIVE_VISUAL_NAMESPACE,
        "owner_id": version.owner_id,
        "novel_id": version.novel_id,
        "project_id": version.project_id,
        "fork_id": version.fork_id,
        "version_key": version.version_key,
        "revision_number": version.revision_number,
        "parent_version_id": version.parent_version_id,
        "source_version_id": version.source_version_id,
        "source_snapshot_id": version.source_snapshot_id,
        "source_snapshot_hash": version.source_snapshot_hash,
        "source_manifest_hash": version.source_manifest_hash,
        "cutoff_chapter": version.cutoff_chapter,
        "divergence": version.divergence,
        "provenance": version.provenance,
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
                "authority": entity.authority,
                "disclosure_cutoff": entity.disclosure_cutoff,
                "divergence": entity.divergence,
                "source_entity_ref": entity.source_entity_ref,
            }
            for entity in version.entities
        ],
        "reference_assets": [
            {
                "asset_key": asset.asset_key,
                "asset_id": asset.asset_id,
                "mime_type": asset.mime_type,
                "bytes_hash": asset.bytes_hash,
                "rights_status": asset.rights_status.value,
                "source_asset_ref": asset.source_asset_ref,
            }
            for asset in version.reference_assets
        ],
    }


def recompute_derivative_visual_manifest_hash(
    version: DerivativeVisualVersionContract,
) -> str:
    return canonical_derivative_visual_hash(derivative_visual_manifest_payload(version))


def validate_derivative_visual_fork_contract(
    version: DerivativeVisualVersionContract,
) -> None:
    """Cross-field gates for an explicit derivative visual fork.

    Fails closed before any row is written when the namespace is not the
    sealed derivative namespace, the divergence is empty, identity/style/asset
    keys are duplicated, a source ref is missing or the manifest hash does not
    match the declared content.
    """
    if version.namespace != DERIVATIVE_VISUAL_NAMESPACE:
        raise DerivativeVisualGateError(
            "derivative visual version must live in the sealed "
            f"{DERIVATIVE_VISUAL_NAMESPACE!r} namespace"
        )
    if not version.divergence:
        raise DerivativeVisualGateError(
            "an explicit divergence declaration is required (D-38-02)"
        )

    stable_ids = [entity.stable_id for entity in version.entities]
    if len(set(stable_ids)) != len(stable_ids):
        raise DerivativeVisualGateError(
            "duplicate entity stable_id in derivative version"
        )
    entity_keys = [entity.entity_key for entity in version.entities]
    if len(set(entity_keys)) != len(entity_keys):
        raise DerivativeVisualGateError("duplicate entity_key in derivative version")
    asset_keys = [asset.asset_key for asset in version.reference_assets]
    if len(set(asset_keys)) != len(asset_keys):
        raise DerivativeVisualGateError(
            "duplicate reference asset_key in derivative version"
        )

    if recompute_derivative_visual_manifest_hash(version) != version.manifest_hash:
        raise DerivativeVisualGateError(
            "derivative version manifest_hash does not match declared content"
        )


# ---------------------------------------------------------------------------
# Review actions (append-only, explicit, idempotent) — mirror D-30-04
# ---------------------------------------------------------------------------

# Action → resulting review state. ``edit`` records the human intent; the
# caller creates a child derivative version and the edited version keeps state.
DERIVATIVE_VISUAL_ACTION_TO_STATE: dict[
    DerivativeVisualAction, DerivativeVisualState | None
] = {
    DerivativeVisualAction.APPROVE: DerivativeVisualState.APPROVED,
    DerivativeVisualAction.REJECT: DerivativeVisualState.REJECTED,
    DerivativeVisualAction.SUPERSEDE: DerivativeVisualState.SUPERSEDED,
    DerivativeVisualAction.NEEDS_RELINK: DerivativeVisualState.NEEDS_RELINK,
    DerivativeVisualAction.EDIT: None,
}

LEGAL_DERIVATIVE_VISUAL_TRANSITIONS: dict[
    DerivativeVisualState, frozenset[DerivativeVisualAction]
] = {
    DerivativeVisualState.CANDIDATE: frozenset(
        {
            DerivativeVisualAction.APPROVE,
            DerivativeVisualAction.REJECT,
            DerivativeVisualAction.EDIT,
            DerivativeVisualAction.SUPERSEDE,
            DerivativeVisualAction.NEEDS_RELINK,
        }
    ),
    DerivativeVisualState.NEEDS_RELINK: frozenset(
        {
            DerivativeVisualAction.APPROVE,
            DerivativeVisualAction.REJECT,
            DerivativeVisualAction.EDIT,
            DerivativeVisualAction.SUPERSEDE,
        }
    ),
    DerivativeVisualState.APPROVED: frozenset(
        {DerivativeVisualAction.SUPERSEDE, DerivativeVisualAction.NEEDS_RELINK}
    ),
    DerivativeVisualState.REJECTED: frozenset(
        {DerivativeVisualAction.EDIT, DerivativeVisualAction.SUPERSEDE}
    ),
    DerivativeVisualState.SUPERSEDED: frozenset(),
}


def is_legal_derivative_visual_review_action(
    state: DerivativeVisualState | str,
    action: DerivativeVisualAction | str,
) -> bool:
    current = DerivativeVisualState(state)
    requested = DerivativeVisualAction(action)
    return requested in LEGAL_DERIVATIVE_VISUAL_TRANSITIONS[current]


def derivative_visual_review_state_after(
    state: DerivativeVisualState | str,
    action: DerivativeVisualAction | str,
) -> DerivativeVisualState:
    current = DerivativeVisualState(state)
    requested = DerivativeVisualAction(action)
    if not is_legal_derivative_visual_review_action(current, requested):
        raise DerivativeVisualGateError(
            f"illegal review action {requested.value!r} from state {current.value!r}"
        )
    target = DERIVATIVE_VISUAL_ACTION_TO_STATE[requested]
    return current if target is None else target


class DerivativeVisualReviewEventInput(StrictDerivativeVisualModel):
    """One append-only review action candidate; result state is server-derived."""

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    action: DerivativeVisualAction
    actor_source: DerivativeVisualActorSource
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    event_key: str = Field(min_length=1, max_length=160)
    from_review_state: DerivativeVisualState


def validate_derivative_visual_review_event(
    event: DerivativeVisualReviewEventInput,
    *,
    seen_event_keys: frozenset[str] | set[str] | None = None,
) -> DerivativeVisualState:
    """Validate a review action and return its derived result state."""
    seen = set(seen_event_keys or ())
    if event.event_key in seen:
        raise DerivativeVisualGateError(
            f"duplicate review event_key {event.event_key!r} (idempotency)"
        )
    return derivative_visual_review_state_after(event.from_review_state, event.action)


# ---------------------------------------------------------------------------
# Read envelopes (owner-scoped, no original-namespace leakage)
# ---------------------------------------------------------------------------


class DerivativeVisualEntityView(StrictDerivativeVisualModel):
    stable_id: str
    entity_key: str
    entity_type: DerivativeVisualEntityType
    description: str
    authority: str
    divergence: dict[str, Any]
    source_entity_ref: dict[str, Any]
    disclosure_cutoff: int


class DerivativeVisualAssetView(StrictDerivativeVisualModel):
    asset_key: str
    asset_id: str
    mime_type: str
    bytes_hash: str
    rights_status: DerivativeVisualRightsStatus
    source_asset_ref: dict[str, Any]
    approved: bool = False


class DerivativeVisualReviewEventView(StrictDerivativeVisualModel):
    action: DerivativeVisualAction
    actor_source: DerivativeVisualActorSource
    actor: str
    reason: str
    event_key: str
    from_review_state: DerivativeVisualState
    to_review_state: DerivativeVisualState


class DerivativeVisualVersionView(StrictDerivativeVisualModel):
    """Read envelope: candidate review state + source + divergence lineage."""

    id: int
    owner_id: int
    novel_id: int
    project_id: int
    fork_id: int
    namespace: str = DERIVATIVE_VISUAL_NAMESPACE
    version_key: str
    revision_number: int
    parent_version_id: int | None = None
    source_version_id: int
    source_snapshot_id: str
    source_snapshot_hash: str
    source_manifest_hash: str
    cutoff_chapter: int
    divergence: dict[str, Any]
    provenance: dict[str, Any]
    schema_version: str
    schema_hash: str
    policy_hash: str
    manifest_hash: str
    review_state: DerivativeVisualState
    style_profile: dict[str, Any] | None = None
    constraints: list[dict[str, Any]] | None = None
    entities: list[DerivativeVisualEntityView] = Field(default_factory=list)
    reference_assets: list[DerivativeVisualAssetView] = Field(default_factory=list)
    review_events: list[DerivativeVisualReviewEventView] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 38-02: canonical derivative Scene Spec contract (D-38-01/D-38-02/D-38-03)
# ---------------------------------------------------------------------------
#
# The provider never receives an Original Visual Bible row or a raw file path:
# it receives one frozen ``DerivativeSceneSpecContract`` that binds the approved
# derivative visual fork (identity/style/divergence), the sealed story context
# (approved original SceneSpec hash + evidence refs) and the existing
# AssetRevision/anchor/export-manifest lineage. Every field is replayable and
# every reference is hash-pinned; unsupported detail and mixed authority are
# rejected by the deterministic gates before any compile or provider call.


class DerivativeIdentityRow(StrictDerivativeVisualModel):
    """One derivative identity/style row pinned to the exact Original entity.

    ``divergence`` and ``source_entity_ref`` are mandatory on every row so an
    identity can never silently drift from the Original Visual Bible entity it
    derives from (REQ-FORK-04 / D-38-02).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = Field(min_length=1, max_length=180)
    entity_key: str = Field(min_length=1, max_length=180)
    entity_type: DerivativeVisualEntityType
    description: str = Field(min_length=1, max_length=8000)
    authority: str = Field(
        pattern=r"^(canon_fact|probable_inference|literary_interpretation|user_interpretation)$"
    )
    divergence: dict[str, Any] = Field(default_factory=dict, min_length=1)
    source_entity_ref: dict[str, Any] = Field(default_factory=dict, min_length=1)
    disclosure_cutoff: int = Field(ge=1)


class DerivativeReferenceAssetRow(StrictDerivativeVisualModel):
    """Derivative reference-asset metadata; never silently canon (D-38-03)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_key: str = Field(min_length=1, max_length=180)
    asset_id: str = Field(min_length=1, max_length=200)
    mime_type: str = Field(min_length=1, max_length=100)
    bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rights_status: DerivativeVisualRightsStatus
    source_asset_ref: dict[str, Any] = Field(default_factory=dict, min_length=1)
    approved: bool = False


class DerivativeSceneSpecEvidenceRef(StrictDerivativeVisualModel):
    """One re-verifiable citation from the sealed scene spec (D-38-03)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_key: str = Field(min_length=1, max_length=180)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_number: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)


class DerivativeAssetLineageRow(StrictDerivativeVisualModel):
    """One approved AssetRevision lineage ref bound to the scene spec."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_revision_id: int = Field(gt=0)
    asset_id: str = Field(min_length=1, max_length=200)
    bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    approval_state: str = Field(
        pattern=r"^(candidate|proposal_ready|rejected|superseded)$"
    )
    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1, max_length=64)
    provider_model: str = Field(min_length=1, max_length=120)


class DerivativeAnchorRef(StrictDerivativeVisualModel):
    """One published illustration-anchor / export-manifest reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor_id: int = Field(gt=0)
    anchor_key: str = Field(min_length=1, max_length=160)
    chapter_number: int = Field(ge=1)
    status: str = Field(pattern=r"^(valid|needs_repair|invalid)$")
    asset_revision_id: int = Field(gt=0)
    publish_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DerivativeSceneSpecUncertainty(StrictDerivativeVisualModel):
    """An explicit unresolved item carried from the sealed scene spec."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uncertainty_key: str = Field(min_length=1, max_length=180)
    reason: str = Field(
        pattern=r"^(missing_evidence|conflicting_claim|future_spoiler|ambiguous_reference)$"
    )
    detail: str = Field(min_length=1, max_length=4000)


class DerivativeNegativeConstraint(StrictDerivativeVisualModel):
    """One explicit negative constraint (scene-spec or branch-fork source)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    constraint_key: str = Field(min_length=1, max_length=180)
    scope: str = Field(pattern=r"^(costume|era|identity|style|physical|continuity)$")
    source: str = Field(pattern=r"^(scene_spec|derivative)$")
    text: str = Field(min_length=1, max_length=4000)
    rationale: str | None = Field(default=None, max_length=2000)


class DerivativeSceneSpecContract(StrictDerivativeVisualModel):
    """Frozen canonical derivative Scene Spec; the only provider input.

    Every reference is an id/hash pin — never an Original row and never a file
    path. ``divergence``/``provenance`` are mandatory, ``content_hash`` is
    byte-replayable and the whole envelope is ``extra="forbid"``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["derivative-scene-spec.v1"] = "derivative-scene-spec.v1"
    artifact_kind: Literal["derivative_scene_spec"] = "derivative_scene_spec"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    fork_id: int = Field(gt=0)
    # D-38-01: the derivative Scene Spec lives in the sealed derivative namespace.
    visual_namespace: Literal["fanfiction_visual"] = "fanfiction_visual"
    spec_key: str = Field(min_length=1, max_length=160)
    revision_number: int = Field(ge=1)
    # Approved derivative visual fork revision (38-01) this spec is bound to.
    visual_fork_version_id: int = Field(gt=0)
    visual_fork_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Sealed story context: the approved original SceneSpec the fork is bound to.
    scene_spec_id: int | None = Field(default=None, gt=0)
    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Approved Original Visual Bible revision (read-only, REQ-FORK-04).
    visual_bible_revision_id: int | None = Field(default=None, gt=0)
    visual_bible_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    # D-38-02: explicit divergence and provenance are first-class facts.
    divergence: dict[str, Any] = Field(default_factory=dict, min_length=1)
    provenance: dict[str, Any] = Field(default_factory=dict, min_length=1)
    identity: list[DerivativeIdentityRow] = Field(default_factory=list, max_length=256)
    style_profile: dict[str, Any] | None = None
    negative_constraints: list[DerivativeNegativeConstraint] = Field(
        default_factory=list, max_length=128
    )
    reference_assets: list[DerivativeReferenceAssetRow] = Field(
        default_factory=list, max_length=256
    )
    asset_lineage: list[DerivativeAssetLineageRow] = Field(
        default_factory=list, max_length=256
    )
    anchors: list[DerivativeAnchorRef] = Field(default_factory=list, max_length=256)
    evidence_refs: list[DerivativeSceneSpecEvidenceRef] = Field(
        default_factory=list, max_length=128
    )
    uncertainties: list[DerivativeSceneSpecUncertainty] = Field(
        default_factory=list, max_length=64
    )
    export_manifest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_state: Literal["candidate"] = "candidate"

    @model_validator(mode="after")
    def explicit_divergence_and_replayable_hash(self) -> "DerivativeSceneSpecContract":
        if not self.divergence:
            raise ValueError(
                "derivative Scene Spec must declare explicit divergence (D-38-02); "
                "an empty divergence is a gate error"
            )
        if not self.provenance:
            raise ValueError(
                "derivative Scene Spec must declare explicit provenance (D-38-01)"
            )
        if recompute_derivative_scene_spec_hash(self) != self.content_hash:
            raise ValueError(
                "derivative Scene Spec content_hash does not replay from its "
                "canonical payload"
            )
        return self


def derivative_scene_spec_content_payload(
    spec: DerivativeSceneSpecContract,
) -> dict[str, Any]:
    """Canonical content payload (excludes ``content_hash`` for replay)."""
    return {
        "artifact_kind": DERIVATIVE_SCENE_SPEC_ARTIFACT_KIND,
        "schema_version": DERIVATIVE_SCENE_SPEC_SCHEMA_VERSION,
        "owner_id": spec.owner_id,
        "novel_id": spec.novel_id,
        "project_id": spec.project_id,
        "fork_id": spec.fork_id,
        "visual_namespace": spec.visual_namespace,
        "spec_key": spec.spec_key,
        "revision_number": spec.revision_number,
        "visual_fork_version_id": spec.visual_fork_version_id,
        "visual_fork_version_hash": spec.visual_fork_version_hash,
        "scene_spec_id": spec.scene_spec_id,
        "scene_spec_hash": spec.scene_spec_hash,
        "scene_candidate_hash": spec.scene_candidate_hash,
        "visual_bible_revision_id": spec.visual_bible_revision_id,
        "visual_bible_revision_hash": spec.visual_bible_revision_hash,
        "source_snapshot_id": spec.source_snapshot_id,
        "source_snapshot_hash": spec.source_snapshot_hash,
        "source_manifest_hash": spec.source_manifest_hash,
        "cutoff_chapter": spec.cutoff_chapter,
        "divergence": spec.divergence,
        "provenance": spec.provenance,
        "identity": [
            {
                "stable_id": row.stable_id,
                "entity_key": row.entity_key,
                "entity_type": row.entity_type.value,
                "authority": row.authority,
                "divergence": row.divergence,
                "source_entity_ref": row.source_entity_ref,
                "disclosure_cutoff": row.disclosure_cutoff,
            }
            for row in spec.identity
        ],
        "style_profile": spec.style_profile,
        "negative_constraints": [
            {
                "constraint_key": c.constraint_key,
                "scope": c.scope,
                "source": c.source,
                "text": c.text,
                "rationale": c.rationale,
            }
            for c in spec.negative_constraints
        ],
        "reference_assets": [
            {
                "asset_key": asset.asset_key,
                "asset_id": asset.asset_id,
                "mime_type": asset.mime_type,
                "bytes_hash": asset.bytes_hash,
                "rights_status": asset.rights_status.value,
                "source_asset_ref": asset.source_asset_ref,
                "approved": asset.approved,
            }
            for asset in spec.reference_assets
        ],
        "asset_lineage": [
            {
                "asset_revision_id": row.asset_revision_id,
                "asset_id": row.asset_id,
                "bytes_hash": row.bytes_hash,
                "mime_type": row.mime_type,
                "approval_state": row.approval_state,
                "scene_spec_hash": row.scene_spec_hash,
                "provider": row.provider,
                "provider_model": row.provider_model,
            }
            for row in spec.asset_lineage
        ],
        "anchors": [
            {
                "anchor_id": ref.anchor_id,
                "anchor_key": ref.anchor_key,
                "chapter_number": ref.chapter_number,
                "status": ref.status,
                "asset_revision_id": ref.asset_revision_id,
                "publish_manifest_hash": ref.publish_manifest_hash,
            }
            for ref in spec.anchors
        ],
        "evidence_refs": [
            {
                "evidence_key": ref.evidence_key,
                "source_snapshot_id": ref.source_snapshot_id,
                "source_snapshot_hash": ref.source_snapshot_hash,
                "chapter_number": ref.chapter_number,
                "source_start": ref.source_start,
                "source_end": ref.source_end,
                "content_hash": ref.content_hash,
                "cutoff_chapter": ref.cutoff_chapter,
            }
            for ref in spec.evidence_refs
        ],
        "uncertainties": [
            {
                "uncertainty_key": u.uncertainty_key,
                "reason": u.reason,
                "detail": u.detail,
            }
            for u in spec.uncertainties
        ],
        "export_manifest_hash": spec.export_manifest_hash,
    }


def recompute_derivative_scene_spec_hash(
    spec: DerivativeSceneSpecContract,
) -> str:
    """Byte-replayable canonical hash of a derivative Scene Spec."""
    return canonical_derivative_visual_hash(derivative_scene_spec_content_payload(spec))


# ---------------------------------------------------------------------------
# Phase 38-02 API envelopes (compile request / gate report / compile response)
# ---------------------------------------------------------------------------


class DerivativeSceneSpecCompileRequest(StrictDerivativeVisualModel):
    """Compile request: bind one approved fork version to one sealed scene spec.

    owner/novel/fork/project scope is server-derived; the client can never
    inject an Original namespace, an approval flag or a divergence.
    """

    version_id: int = Field(gt=0)
    scene_spec_id: int = Field(gt=0)
    spec_key: str = Field(min_length=1, max_length=160)


class DerivativeSceneSpecGateCheckView(StrictDerivativeVisualModel):
    """One deterministic gate check with a stable machine reason code."""

    gate: str
    code: str
    ok: bool
    detail: str = ""


class DerivativeSceneSpecCompileResponse(StrictDerivativeVisualModel):
    """Compile result: the canonical spec or an auditable blocked report."""

    spec: DerivativeSceneSpecContract | None = None
    content_hash: str | None = None
    gate_checks: list[DerivativeSceneSpecGateCheckView] = Field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None
