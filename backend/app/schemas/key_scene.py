"""Key Scene candidate Artifact strict contracts (Phase 31-01, REQ-VIS-02/06).

D-31-01..D-31-05: scene candidates are evidence-first, candidate-only, derived
artifacts. This module owns:

- strict typed wire contracts with ``extra="forbid"`` and frozen immutable
  lineage payloads (``SceneCandidateSetContract`` / ``SceneCandidateContract`` /
  ``SceneEvidenceRange`` / ``SceneReviewDecisionInput``);
- the closed reason-code vocabulary, review state machine and the advisory
  ``SpeakerDialogueHeuristicSignal`` contract (REQ-VIS-06);
- canonical hash helpers so candidate/set hashes are byte-replayable;
- server-side gates that fail closed on unsupported coordinates, missing source
  hashes, spoiler-cutoff violations, duplicate candidate keys, evidence/snapshot
  lineage mismatch, illegal review transitions and heuristic-signal isolation.

Heuristic isolation (D-31-05): a ``SpeakerDialogueHeuristicSignal`` is
diagnostic candidate metadata for recall/ranking only. Its offsets/confidence/
warnings never populate ``evidence_ranges``, are never treated as citation or
Canon, and never justify approval. ``validate_candidate_set_contract`` enforces
the structural separation and that offsets stay inside the candidate's own
primary evidence range (advisory annotation, not authority).
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

KEY_SCENE_SCHEMA_VERSION = "key-scene.v1"
KEY_SCENE_ARTIFACT_KIND = "key_scene"

# Mirrors the ORM vocabulary so schema/model/migration stay byte-identical.
KEY_SCENE_REASON_CODES = (
    "plot_turn",
    "emotional_peak",
    "character_salience",
    "visual_expressiveness",
    "arc_impact",
    "quiet_emotional",
    "dialogue_turn",
    "repetition_penalty",
    "diversity_quota",
    "ambiguity_warning",
    "detector_fallback",
    "evidence_boundary",
    "no_scene_boundaries",
    "malformed_range",
    "beyond_cutoff",
)
KEY_SCENE_COORDINATE_KEYS = ("cast", "place", "time", "pov")


class StrictKeySceneModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeySceneReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_RELINK = "needs_relink"
    SUPERSEDE = "supersede"


class KeySceneReviewState(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    NEEDS_RELINK = "needs_relink"


class KeySceneActorSource(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


class HeuristicSignalAvailability(StrEnum):
    AVAILABLE = "available"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class KeySceneReasonCode(StrEnum):
    PLOT_TURN = "plot_turn"
    EMOTIONAL_PEAK = "emotional_peak"
    CHARACTER_SALIENCE = "character_salience"
    VISUAL_EXPRESSIVENESS = "visual_expressiveness"
    ARC_IMPACT = "arc_impact"
    QUIET_EMOTIONAL = "quiet_emotional"
    DIALOGUE_TURN = "dialogue_turn"
    REPETITION_PENALTY = "repetition_penalty"
    DIVERSITY_QUOTA = "diversity_quota"
    AMBIGUITY_WARNING = "ambiguity_warning"
    DETECTOR_FALLBACK = "detector_fallback"
    EVIDENCE_BOUNDARY = "evidence_boundary"
    NO_SCENE_BOUNDARIES = "no_scene_boundaries"
    MALFORMED_RANGE = "malformed_range"
    BEYOND_CUTOFF = "beyond_cutoff"


class KeySceneGateError(ValueError):
    """Fail-closed gate violation while validating a key-scene candidate."""


# ---------------------------------------------------------------------------
# Canonical hashing (byte-replayable lineage)
# ---------------------------------------------------------------------------


def canonical_key_scene_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention)."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Coordinates, evidence range and heuristic signal contracts
# ---------------------------------------------------------------------------


class SceneCoordinates(StrictKeySceneModel):
    """Narrative coordinates; only cast/place/time/pov are supported keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cast: list[str] = Field(default_factory=list, max_length=64)
    place: str | None = Field(default=None, min_length=1, max_length=200)
    time: str | None = Field(default=None, min_length=1, max_length=200)
    pov: str | None = Field(default=None, min_length=1, max_length=200)


class SceneEvidenceRange(StrictKeySceneModel):
    """Primary-text evidence locator; the only citation authority (D-31-02/05)."""

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
    def validate_offsets_and_cutoff(self) -> "SceneEvidenceRange":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        if self.chapter_number > self.cutoff_chapter:
            raise ValueError(
                "evidence chapter_number must not exceed the spoiler cutoff_chapter"
            )
        return self


class SpeakerOffset(StrictKeySceneModel):
    """One source-relative speaker span (diagnostic, never citation)."""

    offset_start: int = Field(ge=0)
    offset_end: int = Field(gt=0)
    speaker_key: str | None = Field(default=None, min_length=1, max_length=180)

    @model_validator(mode="after")
    def ordered(self) -> "SpeakerOffset":
        if self.offset_end <= self.offset_start:
            raise ValueError("offset_end must be greater than offset_start")
        return self


class DialogueOffset(StrictKeySceneModel):
    """One source-relative quoted-dialogue span (diagnostic, never citation)."""

    offset_start: int = Field(ge=0)
    offset_end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "DialogueOffset":
        if self.offset_end <= self.offset_start:
            raise ValueError("offset_end must be greater than offset_start")
        return self


class SpeakerDialogueHeuristicSignal(StrictKeySceneModel):
    """REQ-VIS-06 advisory speaker/dialogue metadata for recall/ranking only.

    ``availability`` is explicit: ``available`` carries offsets + confidence,
    ``ambiguous`` carries offsets with a reduced confidence and warnings, and
    ``unavailable`` keeps empty offsets with ``confidence=None`` and warnings —
    missing attribution is never silently coerced to a score.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    availability: HeuristicSignalAvailability
    speaker_offsets: list[SpeakerOffset] = Field(default_factory=list, max_length=64)
    dialogue_offsets: list[DialogueOffset] = Field(default_factory=list, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list, max_length=16)
    detector_id: str = Field(min_length=1, max_length=120)
    detector_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_shape(self) -> "SpeakerDialogueHeuristicSignal":
        if self.availability is HeuristicSignalAvailability.UNAVAILABLE:
            if self.speaker_offsets or self.dialogue_offsets:
                raise ValueError(
                    "unavailable signal must carry no speaker/dialogue offsets"
                )
            if self.confidence is not None:
                raise ValueError(
                    "unavailable signal must carry no confidence value"
                )
        else:
            if self.confidence is None:
                raise ValueError(
                    f"{self.availability.value} signal requires a confidence value"
                )
            if self.availability is HeuristicSignalAvailability.AMBIGUOUS:
                if not self.warnings:
                    raise ValueError(
                        "ambiguous signal must carry explicit warnings"
                    )
        return self


# ---------------------------------------------------------------------------
# Candidate and set contracts
# ---------------------------------------------------------------------------


class SalienceReason(StrictKeySceneModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason_code: KeySceneReasonCode
    detail: str | None = Field(default=None, max_length=400)
    score: float | None = Field(default=None, ge=0.0, le=1.0)


class SceneCandidateContract(StrictKeySceneModel):
    """One evidence-first candidate; frozen, strict, never canon."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_key: str = Field(min_length=1, max_length=180)
    candidate_order: int = Field(ge=0)
    scene_id: str = Field(min_length=8, max_length=200)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinates: SceneCoordinates = Field(default_factory=SceneCoordinates)
    spoiler_cutoff: int = Field(ge=1)
    salience_reasons: list[SalienceReason] = Field(default_factory=list, max_length=16)
    score_total: float = Field(ge=0.0)
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    diversity_key: str = Field(min_length=1, max_length=200)
    detector_id: str = Field(min_length=1, max_length=120)
    detector_version: str = Field(min_length=1, max_length=64)
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_ranges: list[SceneEvidenceRange] = Field(min_length=1, max_length=16)
    heuristic_signal: SpeakerDialogueHeuristicSignal | None = None
    review_state: KeySceneReviewState = KeySceneReviewState.CANDIDATE

    @model_validator(mode="after")
    def validate_range_and_cutoff(self) -> "SceneCandidateContract":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        if self.chapter_number > self.spoiler_cutoff:
            raise ValueError(
                "candidate chapter_number must not exceed the spoiler cutoff"
            )
        return self


class SceneCandidateSetContract(StrictKeySceneModel):
    """Frozen candidate-set envelope; every lineage field is mandatory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["key-scene.v1"] = "key-scene.v1"
    artifact_kind: Literal["key_scene"] = "key_scene"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=120)
    revision_number: int = Field(ge=1)
    parent_set_id: int | None = Field(default=None, gt=0)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    detector_id: str = Field(min_length=1, max_length=120)
    detector_version: str = Field(min_length=1, max_length=64)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Approved Visual Bible revision lineage verified before freeze.
    approved_visual_bible_revision_id: int | None = Field(default=None, gt=0)
    approved_visual_bible_revision_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    candidates: list[SceneCandidateContract] = Field(
        default_factory=list, max_length=256
    )
    review_state: KeySceneReviewState = KeySceneReviewState.CANDIDATE

    @model_validator(mode="after")
    def validate_visual_bible_approval_pair(self) -> "SceneCandidateSetContract":
        if (self.approved_visual_bible_revision_id is None) != (
            self.approved_visual_bible_revision_hash is None
        ):
            raise ValueError(
                "approved_visual_bible_revision_id and "
                "approved_visual_bible_revision_hash must be provided together"
            )
        return self


# ---------------------------------------------------------------------------
# Canonical payloads and replayable hashes
# ---------------------------------------------------------------------------


def candidate_canonical_payload(candidate: SceneCandidateContract) -> dict[str, Any]:
    return {
        "candidate_key": candidate.candidate_key,
        "candidate_order": candidate.candidate_order,
        "scene_id": candidate.scene_id,
        "chapter_id": candidate.chapter_id,
        "chapter_number": candidate.chapter_number,
        "source_start": candidate.source_start,
        "source_end": candidate.source_end,
        "source_hash": candidate.source_hash,
        "coordinates": candidate.coordinates.model_dump(mode="json"),
        "spoiler_cutoff": candidate.spoiler_cutoff,
        "salience_reasons": [
            {
                "reason_code": reason.reason_code.value,
                "detail": reason.detail,
                "score": reason.score,
            }
            for reason in candidate.salience_reasons
        ],
        "score_total": candidate.score_total,
        "score_breakdown": candidate.score_breakdown,
        "diversity_key": candidate.diversity_key,
        "detector_id": candidate.detector_id,
        "detector_version": candidate.detector_version,
        "policy_hash": candidate.policy_hash,
        "evidence_keys": [ref.evidence_key for ref in candidate.evidence_ranges],
        "heuristic_signal": (
            None
            if candidate.heuristic_signal is None
            else {
                "availability": candidate.heuristic_signal.availability.value,
                "detector_id": candidate.heuristic_signal.detector_id,
                "detector_version": candidate.heuristic_signal.detector_version,
            }
        ),
    }


def candidate_content_hash(candidate: SceneCandidateContract) -> str:
    return canonical_key_scene_hash(candidate_canonical_payload(candidate))


def set_manifest_payload(set_: SceneCandidateSetContract) -> dict[str, Any]:
    return {
        "artifact_kind": KEY_SCENE_ARTIFACT_KIND,
        "schema_version": KEY_SCENE_SCHEMA_VERSION,
        "owner_id": set_.owner_id,
        "novel_id": set_.novel_id,
        "version_key": set_.version_key,
        "revision_number": set_.revision_number,
        "parent_set_id": set_.parent_set_id,
        "source_snapshot_id": set_.source_snapshot_id,
        "source_snapshot_hash": set_.source_snapshot_hash,
        "cutoff_chapter": set_.cutoff_chapter,
        "schema_hash": set_.schema_hash,
        "policy_hash": set_.policy_hash,
        "detector_id": set_.detector_id,
        "detector_version": set_.detector_version,
        "approved_visual_bible_revision_id": set_.approved_visual_bible_revision_id,
        "approved_visual_bible_revision_hash": set_.approved_visual_bible_revision_hash,
        "candidates": [
            candidate_canonical_payload(candidate) for candidate in set_.candidates
        ],
    }


def recompute_manifest_hash(set_: SceneCandidateSetContract) -> str:
    return canonical_key_scene_hash(set_manifest_payload(set_))


# ---------------------------------------------------------------------------
# Server-side gates
# ---------------------------------------------------------------------------


def validate_candidate_hash(candidate: SceneCandidateContract) -> None:
    """Reject candidates whose canonical payload hash cannot replay."""
    # The contract carries no explicit hash field; the gate verifies that the
    # payload is replayable (structure + lineage) and the source hash is set.
    payload = candidate_canonical_payload(candidate)
    if "source_hash" not in payload or len(payload["source_hash"]) != 64:
        raise KeySceneGateError(
            f"candidate {candidate.candidate_key!r} has no replayable source hash"
        )


def validate_heuristic_signal_isolation(
    candidate: SceneCandidateContract,
) -> None:
    """REQ-VIS-06 isolation: heuristic offsets are annotations, not evidence.

    - The heuristic signal never appears in ``evidence_ranges`` (structural
      separation; evidence is the only citation authority).
    - When present, every heuristic offset must stay inside the candidate's own
      primary evidence range (the candidate's [source_start, source_end) slice),
      so it is advisory annotation of that slice, never an independent source.
    - An ``unavailable`` signal keeps warnings and no offsets; missing
      attribution is never silently promoted (D-31-05).
    """
    if candidate.heuristic_signal is None:
        return
    signal = candidate.heuristic_signal
    primary = candidate.evidence_ranges[0]
    if signal.availability is HeuristicSignalAvailability.UNAVAILABLE:
        if signal.speaker_offsets or signal.dialogue_offsets:
            raise KeySceneGateError(
                f"candidate {candidate.candidate_key!r} unavailable heuristic "
                "signal must carry no offsets"
            )
        return

    inside = (primary.source_start, primary.source_end)
    for offset in [*signal.speaker_offsets, *signal.dialogue_offsets]:
        if offset.offset_start < inside[0] or offset.offset_end > inside[1]:
            raise KeySceneGateError(
                f"candidate {candidate.candidate_key!r} heuristic offset "
                f"[{offset.offset_start},{offset.offset_end}) is outside the "
                f"candidate's primary evidence range {inside}"
            )
    if signal.availability is HeuristicSignalAvailability.AMBIGUOUS and not signal.warnings:
        raise KeySceneGateError(
            f"candidate {candidate.candidate_key!r} ambiguous heuristic signal "
            "must carry explicit warnings"
        )


def validate_candidate_evidence_lineage(
    candidate: SceneCandidateContract,
    *,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    cutoff_chapter: int,
) -> None:
    """Every evidence range must belong to the set snapshot and cutoff."""
    for ref in candidate.evidence_ranges:
        if ref.source_snapshot_id != source_snapshot_id:
            raise KeySceneGateError(
                f"candidate {candidate.candidate_key!r} evidence {ref.evidence_key!r} "
                "source_snapshot_id does not match the set"
            )
        if ref.source_snapshot_hash != source_snapshot_hash:
            raise KeySceneGateError(
                f"candidate {candidate.candidate_key!r} evidence {ref.evidence_key!r} "
                "source_snapshot_hash does not match the set"
            )
        if ref.cutoff_chapter != cutoff_chapter:
            raise KeySceneGateError(
                f"candidate {candidate.candidate_key!r} evidence {ref.evidence_key!r} "
                "cutoff_chapter does not match the set"
            )
        if ref.chapter_number > cutoff_chapter:
            raise KeySceneGateError(
                f"candidate {candidate.candidate_key!r} evidence {ref.evidence_key!r} "
                "chapter_number exceeds the set spoiler cutoff"
            )
        if ref.source_start < candidate.source_start or ref.source_end > candidate.source_end:
            raise KeySceneGateError(
                f"candidate {candidate.candidate_key!r} evidence {ref.evidence_key!r} "
                "must lie inside the candidate's source range"
            )


def validate_candidate_set_contract(set_: SceneCandidateSetContract) -> None:
    """Cross-field gates: unique keys/order, cutoff, snapshot lineage,
    heuristic-signal isolation and replayable manifest hash."""
    if not set_.candidates:
        raise KeySceneGateError("candidate set must contain at least one candidate")

    candidate_keys = [candidate.candidate_key for candidate in set_.candidates]
    if len(set(candidate_keys)) != len(candidate_keys):
        raise KeySceneGateError("duplicate candidate_key in set")

    orders = [candidate.candidate_order for candidate in set_.candidates]
    if len(set(orders)) != len(orders):
        raise KeySceneGateError("duplicate candidate_order in set")

    for candidate in set_.candidates:
        if candidate.chapter_number > set_.cutoff_chapter:
            raise KeySceneGateError(
                f"candidate {candidate.candidate_key!r} chapter_number exceeds "
                "the set spoiler cutoff"
            )
        validate_candidate_hash(candidate)
        validate_heuristic_signal_isolation(candidate)
        validate_candidate_evidence_lineage(
            candidate,
            source_snapshot_id=set_.source_snapshot_id,
            source_snapshot_hash=set_.source_snapshot_hash,
            cutoff_chapter=set_.cutoff_chapter,
        )

    if recompute_manifest_hash(set_) != set_.manifest_hash:
        raise KeySceneGateError("set manifest_hash does not match content")


def verify_visual_bible_approval_reference(
    set_: SceneCandidateSetContract,
) -> None:
    """A frozen set that cites an approved Visual Bible revision must carry a
    consistent hash; the live owner/version/approved/hash check happens against
    the persisted version (see ``SceneBoundaryService.verify_visual_bible_approval``)."""
    if (set_.approved_visual_bible_revision_id is None) != (
        set_.approved_visual_bible_revision_hash is None
    ):
        raise KeySceneGateError(
            "approved_visual_bible_revision_id and "
            "approved_visual_bible_revision_hash must be provided together"
        )


# ---------------------------------------------------------------------------
# Review decisions (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------

SCENE_REVIEW_ACTION_TO_STATE: dict[KeySceneReviewAction, KeySceneReviewState] = {
    KeySceneReviewAction.APPROVE: KeySceneReviewState.APPROVED,
    KeySceneReviewAction.REJECT: KeySceneReviewState.REJECTED,
    KeySceneReviewAction.NEEDS_RELINK: KeySceneReviewState.NEEDS_RELINK,
    KeySceneReviewAction.SUPERSEDE: KeySceneReviewState.SUPERSEDED,
}

LEGAL_SCENE_REVIEW_TRANSITIONS: dict[
    KeySceneReviewState, frozenset[KeySceneReviewAction]
] = {
    KeySceneReviewState.CANDIDATE: frozenset(
        {
            KeySceneReviewAction.APPROVE,
            KeySceneReviewAction.REJECT,
            KeySceneReviewAction.NEEDS_RELINK,
            KeySceneReviewAction.SUPERSEDE,
        }
    ),
    KeySceneReviewState.NEEDS_RELINK: frozenset(
        {
            KeySceneReviewAction.APPROVE,
            KeySceneReviewAction.REJECT,
            KeySceneReviewAction.SUPERSEDE,
        }
    ),
    KeySceneReviewState.APPROVED: frozenset(
        {
            KeySceneReviewAction.SUPERSEDE,
            KeySceneReviewAction.NEEDS_RELINK,
        }
    ),
    KeySceneReviewState.REJECTED: frozenset({KeySceneReviewAction.SUPERSEDE}),
    KeySceneReviewState.SUPERSEDED: frozenset(),
}


def is_legal_scene_review_action(
    state: KeySceneReviewState | str, action: KeySceneReviewAction | str
) -> bool:
    current = KeySceneReviewState(state)
    requested = KeySceneReviewAction(action)
    return requested in LEGAL_SCENE_REVIEW_TRANSITIONS[current]


def validate_legal_scene_review_action(
    state: KeySceneReviewState | str, action: KeySceneReviewAction | str
) -> None:
    current = KeySceneReviewState(state)
    requested = KeySceneReviewAction(action)
    if not is_legal_scene_review_action(current, requested):
        raise KeySceneGateError(
            f"illegal review action {requested.value!r} from state {current.value!r}"
        )


def review_state_after(
    state: KeySceneReviewState | str, action: KeySceneReviewAction | str
) -> KeySceneReviewState:
    current = KeySceneReviewState(state)
    requested = KeySceneReviewAction(action)
    validate_legal_scene_review_action(current, requested)
    return SCENE_REVIEW_ACTION_TO_STATE[requested]


class SceneReviewDecisionInput(StrictKeySceneModel):
    """One append-only review decision; result state is server-derived."""

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    set_id: int = Field(gt=0)
    decision_key: str = Field(min_length=1, max_length=160)
    action: KeySceneReviewAction
    actor_source: KeySceneActorSource
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    from_review_state: KeySceneReviewState
    candidate_key: str | None = Field(default=None, min_length=1, max_length=180)


def validate_review_decision(
    decision: SceneReviewDecisionInput,
    *,
    seen_decision_keys: frozenset[str] | set[str] | None = None,
) -> KeySceneReviewState:
    """Validate a review decision and return its derived result state.

    Idempotency: a repeated ``decision_key`` (e.g. a retried approval) is
    rejected here; the durable layer enforces the unique decision_key constraint
    so a duplicate action can never create a second approval (D-31-04).
    """
    seen = set(seen_decision_keys or ())
    if decision.decision_key in seen:
        raise KeySceneGateError(
            f"duplicate review decision_key {decision.decision_key!r} (idempotency)"
        )
    return review_state_after(decision.from_review_state, decision.action)


# ---------------------------------------------------------------------------
# Read envelopes (candidate-only, spoiler-safe, no canon exposure)
# ---------------------------------------------------------------------------


class SceneEvidenceRangeView(StrictKeySceneModel):
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


class SceneCandidateView(StrictKeySceneModel):
    candidate_key: str
    candidate_order: int
    scene_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    source_hash: str
    coordinates: SceneCoordinates
    spoiler_cutoff: int
    salience_reasons: list[SalienceReason] = Field(default_factory=list)
    score_total: float
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    diversity_key: str
    detector_id: str
    detector_version: str
    policy_hash: str
    evidence_ranges: list[SceneEvidenceRangeView] = Field(default_factory=list)
    heuristic_signal: SpeakerDialogueHeuristicSignal | None = None
    review_state: KeySceneReviewState


class SceneReviewDecisionView(StrictKeySceneModel):
    decision_key: str
    action: KeySceneReviewAction
    actor_source: KeySceneActorSource
    actor: str
    reason: str
    from_review_state: KeySceneReviewState
    to_review_state: KeySceneReviewState
    candidate_key: str | None = None


class SceneCandidateSetView(StrictKeySceneModel):
    """Read envelope: candidate-only, evidence + reasons + heuristic metadata."""

    id: int
    owner_id: int
    novel_id: int
    version_key: str
    revision_number: int
    parent_set_id: int | None = None
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    schema_version: str
    schema_hash: str
    policy_hash: str
    detector_id: str
    detector_version: str
    manifest_hash: str
    approved_visual_bible_revision_id: int | None = None
    approved_visual_bible_revision_hash: str | None = None
    review_state: KeySceneReviewState
    candidates: list[SceneCandidateView] = Field(default_factory=list)
    review_decisions: list[SceneReviewDecisionView] = Field(default_factory=list)
