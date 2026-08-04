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
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
    authority: str = Field(pattern=r"^(canon_fact|probable_inference|literary_interpretation|user_interpretation)$")
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
            raise ValueError("source_entity_ref.source_entity_hash must be 64 hex chars")
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
    rights_status: DerivativeVisualRightsStatus = DerivativeVisualRightsStatus.UNREVIEWED
    provenance: dict[str, Any] = Field(default_factory=dict)
    source_asset_ref: dict[str, Any] = Field(default_factory=dict, min_length=1)

    @model_validator(mode="after")
    def require_source_asset_ref(self) -> "DerivativeVisualAssetContract":
        ref = self.source_asset_ref
        for key in ("source_asset_id", "source_bytes_hash"):
            if not ref.get(key):
                raise ValueError(f"source_asset_ref must carry {key!r} for the original asset")
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
    reference_assets: list[DerivativeVisualAssetContract] = Field(
        default_factory=list
    )
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
        raise DerivativeVisualGateError("duplicate entity stable_id in derivative version")
    entity_keys = [entity.entity_key for entity in version.entities]
    if len(set(entity_keys)) != len(entity_keys):
        raise DerivativeVisualGateError("duplicate entity_key in derivative version")
    asset_keys = [asset.asset_key for asset in version.reference_assets]
    if len(set(asset_keys)) != len(asset_keys):
        raise DerivativeVisualGateError("duplicate reference asset_key in derivative version")

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
