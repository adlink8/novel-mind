"""Phase 38-03 derivative asset candidate strict wire contracts (D-38-03).

REQ-FORK-04 / REQ-CRE-06: a generated derivative visual asset is stored as a
**write-only isolated candidate** — generated ``asset_id``, allowlisted
storage path, content checksum, full identity/source/generator lineage and a
deterministic cross-chapter consistency review signal. This module owns:

- strict ``extra="forbid"`` write contracts so the client can never inject an
  Original namespace, an approval flag, a scope, a raw path or an SSRF URL;
- the sealed derivative namespace (``fanfiction_visual``) and the candidate
  review-state / consistency-verdict vocabularies;
- ``PublishedDerivativeVisualAsset`` — the read envelope for the published
  query (owner/project/fork visible, approved-only);
- canonical hash helpers so the candidate lineage is byte-replayable.

Nothing here writes to the database and nothing promotes a candidate to canon;
approval is an append-only review event applied by ``assets.py``.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DERIVATIVE_ASSET_SCHEMA_VERSION = "derivative-visual-asset.v1"
# D-38-01: the derivative candidate asset lives in the sealed derivative
# namespace; an Original Canon asset can never be a storage target.
DERIVATIVE_ASSET_NAMESPACE = "fanfiction_visual"

# Closed candidate review vocabularies (mirror ``models/derivative_visual.py``).
DERIVATIVE_ASSET_STATES = (
    "candidate",
    "needs_review",
    "approved",
    "rejected",
    "superseded",
    "blocked",
)
DERIVATIVE_ASSET_ACTIONS = ("approve", "reject", "supersede")
DERIVATIVE_CONSISTENCY_VERDICTS = ("pass", "concern", "fail", "unavailable")
DERIVATIVE_CONSISTENCY_EVALUATOR_ID = "derivative-visual-consistency.cross_chapter.v1"
DERIVATIVE_CONSISTENCY_EVALUATOR_VERSION = "1.0.0"

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
# T-38-03-02 / SSRF metadata guard: no transport-level URL may enter the
# candidate lineage (identity/source/generator/divergence) or an untrusted
# metadata field could be echoed back to a client as a fetch target.
_URL_TOKEN_RE = re.compile(r"https?://", re.IGNORECASE)


class StrictDerivativeAssetModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeVisualAssetState(StrEnum):
    CANDIDATE = "candidate"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    BLOCKED = "blocked"


class DerivativeAssetReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    SUPERSEDE = "supersede"


class DerivativeConsistencyVerdict(StrEnum):
    PASS = "pass"
    CONCERN = "concern"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


class DerivativeAssetGateError(ValueError):
    """Fail-closed gate violation while validating a derivative asset."""


# ---------------------------------------------------------------------------
# Canonical hashing (byte-replayable candidate lineage)
# ---------------------------------------------------------------------------


def canonical_derivative_asset_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention)."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Lineage row contracts (identity / source / generator)
# ---------------------------------------------------------------------------


class DerivativeAssetIdentityRow(StrictDerivativeAssetModel):
    """One identity row pinned to the exact Original Visual Bible entity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = Field(min_length=1, max_length=180)
    entity_key: str = Field(min_length=1, max_length=180)
    entity_type: str = Field(pattern=r"^(character|place|item|faction|style)$")
    source_entity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DerivativeAssetSourceRef(StrictDerivativeAssetModel):
    """One Original asset reference (source asset id + bytes hash)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_key: str = Field(min_length=1, max_length=180)
    asset_id: str = Field(min_length=1, max_length=200)
    source_asset_id: str = Field(min_length=1, max_length=200)
    source_bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DerivativeAssetGeneratorLineage(StrictDerivativeAssetModel):
    """Frozen provider/model/prompt lineage; no transport URL may appear."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    provider_model: str = Field(min_length=1, max_length=120)
    prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_transport_urls(self) -> "DerivativeAssetGeneratorLineage":
        text = json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
        if _URL_TOKEN_RE.search(text):
            raise ValueError(
                "generator lineage must not carry transport URLs (SSRF metadata)"
            )
        return self


# ---------------------------------------------------------------------------
# Write contract (D-38-03: the provider only ever produces a candidate)
# ---------------------------------------------------------------------------


class DerivativeAssetCandidateWrite(StrictDerivativeAssetModel):
    """One derivative asset candidate the provider wants to store.

    The client can never supply an owner/novel/fork scope, a namespace, an
    approval flag or a storage path. ``content_hash`` is the claimed SHA-256
    over the bytes; the server always replays it from the uploaded payload and
    a mismatch fails closed. ``identity_lineage``/``source_refs``/
    ``generator_lineage``/``divergence_manifest_hash`` are verified against the
    frozen canonical Scene Spec before any byte is written.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_key: str = Field(min_length=1, max_length=180)
    chapter_number: int = Field(ge=1)
    mime_type: str = Field(min_length=1, max_length=100)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    divergence_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_lineage: list[DerivativeAssetIdentityRow] = Field(
        default_factory=list, max_length=256
    )
    source_refs: list[DerivativeAssetSourceRef] = Field(
        default_factory=list, max_length=256
    )
    generator_lineage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_lineage_and_no_ssrf(self) -> "DerivativeAssetCandidateWrite":
        if not self.identity_lineage:
            raise ValueError(
                "derivative asset candidate must declare identity lineage (D-38-03)"
            )
        if not self.source_refs:
            raise ValueError(
                "derivative asset candidate must pin Original source refs (REQ-FORK-04)"
            )
        if not self.generator_lineage:
            raise ValueError(
                "derivative asset candidate must declare generator lineage (D-38-03)"
            )
        text = json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
        if _URL_TOKEN_RE.search(text):
            raise ValueError(
                "derivative asset lineage must not carry transport URLs (SSRF metadata)"
            )
        return self


# ---------------------------------------------------------------------------
# Deterministic consistency review signal (D-38-03)
# ---------------------------------------------------------------------------


class ChapterConsistencyEvidence(StrictDerivativeAssetModel):
    """One chapter's frozen identity/style evidence for the cross-chapter gate.

    ``identity_source_hash`` pins the exact Original Visual Bible entity and
    ``style_hash`` replays the frozen style profile; ``declared_style_divergence``
    records whether the fork explicitly declared the style change (D-38-02).
    Missing evidence is explicit so the score can never silently pass.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter_number: int = Field(ge=1)
    identity_key: str = Field(min_length=1, max_length=180)
    identity_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    style_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    declared_style_divergence: bool = False
    missing_identity_evidence: bool = False
    missing_style_evidence: bool = False


class ChapterScoreView(StrictDerivativeAssetModel):
    """One chapter's deterministic identity/style score (0.0 or 1.0)."""

    chapter_number: int = Field(ge=1)
    identity_score: float = Field(ge=0, le=1)
    style_score: float = Field(ge=0, le=1)
    identity_consistent: bool
    style_consistent: bool


class DerivativeConsistencyReport(StrictDerivativeAssetModel):
    """Deterministic cross-chapter consistency review signal (never canon).

    The verdict/reasons only drive the candidate ``review_state`` chain —
    fail -> blocked, concern/unavailable -> needs_review, pass -> candidate.
    It can never approve a candidate and never touches the Original rows.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["derivative-visual-asset.v1"] = "derivative-visual-asset.v1"
    evaluator_id: str = Field(min_length=1, max_length=120)
    evaluator_version: str = Field(min_length=1, max_length=64)
    chapters: list[ChapterScoreView] = Field(default_factory=list, max_length=64)
    reasons: list[str] = Field(default_factory=list, max_length=64)
    verdict: DerivativeConsistencyVerdict
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Read envelopes (owner-scoped, approved-only for the published query)
# ---------------------------------------------------------------------------


class DerivativeVisualVersionRef(StrictDerivativeAssetModel):
    version_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=160)
    version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DerivativeSourceSnapshotRef(StrictDerivativeAssetModel):
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)


class DerivativeAssetReviewEventInput(StrictDerivativeAssetModel):
    """One append-only review action candidate; result state is server-derived.

    A ``blocked`` candidate (identity drift / undeclared divergence) has an
    empty legal transition set, so approve/reject/supersede all fail closed.
    """

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    candidate_id: int = Field(gt=0)
    action: DerivativeAssetReviewAction
    actor_source: str = Field(pattern=r"^(human|machine)$")
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    event_key: str = Field(min_length=1, max_length=160)
    from_review_state: DerivativeVisualAssetState
    details: dict[str, Any] = Field(default_factory=dict)


class DerivativeAssetReviewEventView(StrictDerivativeAssetModel):
    action: DerivativeAssetReviewAction
    actor_source: str = Field(pattern=r"^(human|machine)$")
    actor: str
    reason: str
    event_key: str
    from_review_state: DerivativeVisualAssetState
    to_review_state: DerivativeVisualAssetState


class DerivativeAssetReviewEnvelope(StrictDerivativeAssetModel):
    """The candidate review chain: state + consistency signal + events."""

    review_state: DerivativeVisualAssetState
    consistency_verdict: DerivativeConsistencyVerdict
    consistency_report: DerivativeConsistencyReport | None = None
    reasons: list[str] = Field(default_factory=list)
    review_events: list[DerivativeAssetReviewEventView] = Field(default_factory=list)


class DerivativeVisualAssetView(StrictDerivativeAssetModel):
    """Full candidate read envelope (owner-scoped; review state included).

    Exposed by the store/consistency/review seams. The published query uses the
    ``PublishedDerivativeVisualAsset`` projection below which only ever contains
    ``approved`` candidates — an Original asset or an unapproved candidate is
    never returned by that seam.
    """

    id: int
    owner_id: int
    novel_id: int
    project_id: int
    fork_id: int
    asset_id: str
    asset_key: str
    content_hash: str
    mime_type: str
    size_bytes: int
    namespace: str = DERIVATIVE_ASSET_NAMESPACE
    scene_spec_hash: str
    chapter_number: int
    visual_version: DerivativeVisualVersionRef
    source_snapshot: DerivativeSourceSnapshotRef
    approval: DerivativeVisualAssetState
    review: DerivativeAssetReviewEnvelope
    source_refs: list[DerivativeAssetSourceRef] = Field(default_factory=list)
    identity_lineage: list[DerivativeAssetIdentityRow] = Field(default_factory=list)
    generator_lineage: dict[str, Any] = Field(default_factory=dict)
    divergence_manifest_hash: str


class PublishedDerivativeVisualAsset(DerivativeVisualAssetView):
    """Published derivative asset read envelope (approved-only, owner-visible).

    owner_id/project_id/fork_id/asset_id/content_hash/visual_version/
    source_snapshot/namespace/approval/review/source_refs/scene_spec_hash/
    identity_lineage/generator_lineage/divergence_manifest_hash.
    """


# ---------------------------------------------------------------------------
# Review actions (append-only, explicit, idempotent) — mirror D-38-01 lineage
# ---------------------------------------------------------------------------

DERIVATIVE_ASSET_ACTION_TO_STATE: dict[
    DerivativeAssetReviewAction, DerivativeVisualAssetState
] = {
    DerivativeAssetReviewAction.APPROVE: DerivativeVisualAssetState.APPROVED,
    DerivativeAssetReviewAction.REJECT: DerivativeVisualAssetState.REJECTED,
    DerivativeAssetReviewAction.SUPERSEDE: DerivativeVisualAssetState.SUPERSEDED,
}

# ``blocked`` (identity drift / undeclared divergence) is terminal: a candidate
# that fails the deterministic consistency gate can never be published.
LEGAL_DERIVATIVE_ASSET_TRANSITIONS: dict[
    DerivativeVisualAssetState, frozenset[DerivativeAssetReviewAction]
] = {
    DerivativeVisualAssetState.CANDIDATE: frozenset(
        {
            DerivativeAssetReviewAction.APPROVE,
            DerivativeAssetReviewAction.REJECT,
            DerivativeAssetReviewAction.SUPERSEDE,
        }
    ),
    DerivativeVisualAssetState.NEEDS_REVIEW: frozenset(
        {
            DerivativeAssetReviewAction.APPROVE,
            DerivativeAssetReviewAction.REJECT,
            DerivativeAssetReviewAction.SUPERSEDE,
        }
    ),
    DerivativeVisualAssetState.APPROVED: frozenset(
        {
            DerivativeAssetReviewAction.REJECT,
            DerivativeAssetReviewAction.SUPERSEDE,
        }
    ),
    DerivativeVisualAssetState.REJECTED: frozenset(
        {DerivativeAssetReviewAction.SUPERSEDE}
    ),
    DerivativeVisualAssetState.SUPERSEDED: frozenset(),
    DerivativeVisualAssetState.BLOCKED: frozenset(),
}


def is_legal_derivative_asset_review_action(
    state: DerivativeVisualAssetState | str,
    action: DerivativeAssetReviewAction | str,
) -> bool:
    current = DerivativeVisualAssetState(state)
    requested = DerivativeAssetReviewAction(action)
    return requested in LEGAL_DERIVATIVE_ASSET_TRANSITIONS[current]


def derivative_asset_review_state_after(
    state: DerivativeVisualAssetState | str,
    action: DerivativeAssetReviewAction | str,
) -> DerivativeVisualAssetState:
    current = DerivativeVisualAssetState(state)
    requested = DerivativeAssetReviewAction(action)
    if not is_legal_derivative_asset_review_action(current, requested):
        raise DerivativeAssetGateError(
            f"illegal derivative asset review action {requested.value!r} from "
            f"state {current.value!r}"
        )
    return DERIVATIVE_ASSET_ACTION_TO_STATE[requested]


def review_state_from_consistency_verdict(
    verdict: DerivativeConsistencyVerdict | str,
) -> DerivativeVisualAssetState:
    """Deterministic review gate from the consistency signal (D-38-03).

    fail -> blocked (can never publish); concern/unavailable -> needs_review
    (explicit human review required); pass -> candidate.
    """
    resolved = DerivativeConsistencyVerdict(verdict)
    if resolved is DerivativeConsistencyVerdict.FAIL:
        return DerivativeVisualAssetState.BLOCKED
    if resolved in {
        DerivativeConsistencyVerdict.CONCERN,
        DerivativeConsistencyVerdict.UNAVAILABLE,
    }:
        return DerivativeVisualAssetState.NEEDS_REVIEW
    return DerivativeVisualAssetState.CANDIDATE


def divergence_manifest_hash_from_spec(spec: Any) -> str:
    """Canonical hash of the fork's explicit divergence declaration (D-38-02).

    Includes the version-level divergence and every per-identity divergence so
    a hidden style/identity drift can never replay a stale manifest.
    """
    identity_divergences = [
        {
            "stable_id": row.stable_id,
            "entity_key": row.entity_key,
            "divergence": dict(row.divergence or {}),
        }
        for row in spec.identity
    ]
    return canonical_derivative_asset_hash(
        {
            "kind": "derivative_visual_divergence_manifest",
            "schema_version": DERIVATIVE_ASSET_SCHEMA_VERSION,
            "divergence": dict(spec.divergence or {}),
            "identity_divergences": identity_divergences,
        }
    )


def _declares_style_divergence(spec: Any) -> bool:
    """Whether the fork explicitly declared a style/palette change (D-38-02)."""
    divergence = dict(spec.divergence or {})
    if "style" in divergence:
        return True
    for row in spec.identity:
        row_divergence = dict(row.divergence or {})
        if any(key in row_divergence for key in ("style", "palette", "color")):
            return True
    return False


def chapter_evidence_from_spec(
    spec: Any, chapter_number: int
) -> ChapterConsistencyEvidence:
    """Frozen per-chapter evidence derived deterministically from the spec."""
    identity_row = spec.identity[0]
    source_ref = dict(identity_row.source_entity_ref or {})
    style_profile = spec.style_profile
    if style_profile:
        style_hash = canonical_derivative_asset_hash({"style_profile": style_profile})
        missing_style = False
    else:
        style_hash = "0" * 64
        missing_style = True
    return ChapterConsistencyEvidence(
        chapter_number=chapter_number,
        identity_key=identity_row.stable_id,
        identity_source_hash=str(source_ref.get("source_entity_hash", "")),
        style_hash=style_hash,
        scene_spec_hash=spec.content_hash,
        declared_style_divergence=_declares_style_divergence(spec),
        missing_identity_evidence=not source_ref.get("source_entity_hash"),
        missing_style_evidence=missing_style,
    )


__all__ = [
    "DERIVATIVE_ASSET_ACTIONS",
    "DERIVATIVE_ASSET_NAMESPACE",
    "DERIVATIVE_ASSET_SCHEMA_VERSION",
    "DERIVATIVE_ASSET_STATES",
    "DERIVATIVE_CONSISTENCY_EVALUATOR_ID",
    "DERIVATIVE_CONSISTENCY_EVALUATOR_VERSION",
    "DERIVATIVE_CONSISTENCY_VERDICTS",
    "ChapterConsistencyEvidence",
    "ChapterScoreView",
    "DerivativeAssetCandidateWrite",
    "DerivativeAssetGateError",
    "DerivativeAssetGeneratorLineage",
    "DerivativeAssetIdentityRow",
    "DerivativeAssetReviewAction",
    "DerivativeAssetReviewEnvelope",
    "DerivativeAssetReviewEventInput",
    "DerivativeAssetReviewEventView",
    "DerivativeAssetSourceRef",
    "DerivativeConsistencyReport",
    "DerivativeConsistencyVerdict",
    "DerivativeSourceSnapshotRef",
    "DerivativeVisualAssetState",
    "DerivativeVisualAssetView",
    "DerivativeVisualVersionRef",
    "PublishedDerivativeVisualAsset",
    "canonical_derivative_asset_hash",
    "chapter_evidence_from_spec",
    "derivative_asset_review_state_after",
    "divergence_manifest_hash_from_spec",
    "is_legal_derivative_asset_review_action",
    "review_state_from_consistency_verdict",
]
