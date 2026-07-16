"""
Phase 11 clue and foreshadow contracts.

Fiction-only strict schemas for version lineage, machine clues, evidence roles,
append-only lifecycle events, typed links, human overrides and visible API
envelopes. Current state is always derived by replaying lifecycle history —
there is no mutable authoritative current-state write contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Strict base
# ---------------------------------------------------------------------------


class StrictClueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Enums (D-01, D-03, D-04, D-05, D-06, D-11)
# ---------------------------------------------------------------------------


class ClueLifecycleState(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REINFORCED = "reinforced"
    PAID_OFF = "paid_off"
    DISMISSED = "dismissed"


class ClueEvidenceRole(StrEnum):
    CUE = "cue"
    REINFORCEMENT = "reinforcement"
    PAYOFF = "payoff"
    DISPOSITION = "disposition"


class ClueLinkTargetKind(StrEnum):
    CHARACTER = "character"
    TIMELINE_EVENT = "timeline_event"
    RELATIONSHIP_OBSERVATION = "relationship_observation"


class ClueLinkValidationStatus(StrEnum):
    VALID = "valid"
    UNRESOLVED = "unresolved"
    SOURCE_UNAVAILABLE = "source_unavailable"
    INVALID = "invalid"


class ClueActorSource(StrEnum):
    MACHINE = "machine"
    HUMAN = "human"


class ClueOverrideAction(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    ANNOTATE = "annotate"
    ADJUST_LINK = "adjust_link"


class ClueOverrideStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    NEEDS_RELINK = "needs_relink"


class ClueSemanticClassification(StrEnum):
    CUE_ONLY = "cue_only"
    REINFORCEMENT = "reinforcement"
    PAYOFF = "payoff"
    UNRELATED = "unrelated"
    AMBIGUOUS = "ambiguous"


class ClueConflictFlag(StrEnum):
    MOTIF_ONLY = "MOTIF_ONLY"
    ORDER_CONFLICT = "ORDER_CONFLICT"
    ENTITY_CONFLICT = "ENTITY_CONFLICT"
    UNRESOLVED_REFERENCE = "UNRESOLVED_REFERENCE"
    INSUFFICIENT_PAYOFF = "INSUFFICIENT_PAYOFF"


class ClueVersionSource(StrEnum):
    ACTIVE = "active"
    RUNNING_CANDIDATE = "running_candidate"
    HISTORY = "history"


class ClueVersionStatus(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class ClueRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED_BUDGET = "paused_budget"
    PAUSED_DEPENDENCY = "paused_dependency"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


# Legal append-only machine/human transitions (REQ-CLUE-02 / D-03).
LEGAL_TRANSITIONS: dict[ClueLifecycleState, frozenset[ClueLifecycleState]] = {
    ClueLifecycleState.CANDIDATE: frozenset(
        {ClueLifecycleState.ACTIVE, ClueLifecycleState.DISMISSED}
    ),
    ClueLifecycleState.ACTIVE: frozenset(
        {ClueLifecycleState.REINFORCED, ClueLifecycleState.DISMISSED}
    ),
    ClueLifecycleState.REINFORCED: frozenset(
        {
            ClueLifecycleState.REINFORCED,
            ClueLifecycleState.PAID_OFF,
            ClueLifecycleState.DISMISSED,
        }
    ),
    ClueLifecycleState.PAID_OFF: frozenset(),
    ClueLifecycleState.DISMISSED: frozenset(),
}

TERMINAL_STATES: frozenset[ClueLifecycleState] = frozenset(
    {ClueLifecycleState.PAID_OFF, ClueLifecycleState.DISMISSED}
)

# History domain values and non-evidence signal fields that must never appear.
_FORBIDDEN_DOMAIN_VALUES = frozenset(
    {
        "history",
        "historical",
        "nonfiction",
        "chat",
        "conversation",
        "message",
        "similarity",
        "vector_score",
        "bm25_score",
    }
)


def _reject_forbidden_token(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in _FORBIDDEN_DOMAIN_VALUES:
        raise ValueError(f"forbidden domain/signal value: {value!r}")
    return value


# ---------------------------------------------------------------------------
# Evidence and narrative coordinates
# ---------------------------------------------------------------------------


class ClueEvidenceRef(StrictClueModel):
    """Primary-text evidence locator; never chat text or similarity scores."""

    evidence_id: str = Field(min_length=1, max_length=80)
    role: ClueEvidenceRole
    chapter_id: int = Field(gt=0)
    narrative_chapter_number: int = Field(gt=0)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_offsets(self) -> "ClueEvidenceRef":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self

    def narrative_key(self) -> tuple[int, int]:
        """Stable narrative order key: (chapter, source_start)."""
        return (self.narrative_chapter_number, self.source_start)

    def identity_key(self) -> str:
        """Stable identity used to reject repeated reinforcement evidence."""
        return (
            f"{self.evidence_id}:{self.chapter_id}:"
            f"{self.source_start}:{self.source_end}:{self.content_hash}"
        )


# ---------------------------------------------------------------------------
# Lifecycle pure functions (replay authority)
# ---------------------------------------------------------------------------


class LifecycleTransitionError(ValueError):
    """Illegal lifecycle transition or evidence requirement failure."""


class LifecycleEventInput(StrictClueModel):
    """One append-only transition candidate for validation/replay."""

    from_status: ClueLifecycleState
    to_status: ClueLifecycleState
    actor_source: ClueActorSource
    reason: str = Field(min_length=1, max_length=500)
    evidence: list[ClueEvidenceRef] = Field(default_factory=list, max_length=16)
    event_key: str = Field(min_length=1, max_length=160)


def is_legal_transition(
    from_status: ClueLifecycleState | str,
    to_status: ClueLifecycleState | str,
) -> bool:
    src = ClueLifecycleState(from_status)
    dst = ClueLifecycleState(to_status)
    return dst in LEGAL_TRANSITIONS[src]


def validate_transition_legality(
    from_status: ClueLifecycleState | str,
    to_status: ClueLifecycleState | str,
) -> None:
    src = ClueLifecycleState(from_status)
    dst = ClueLifecycleState(to_status)
    if not is_legal_transition(src, dst):
        raise LifecycleTransitionError(
            f"illegal lifecycle transition: {src.value} → {dst.value}"
        )


def _require_role(evidence: list[ClueEvidenceRef], role: ClueEvidenceRole) -> list[ClueEvidenceRef]:
    return [e for e in evidence if e.role == role]


def validate_evidence_for_transition(
    from_status: ClueLifecycleState | str,
    to_status: ClueLifecycleState | str,
    evidence: list[ClueEvidenceRef],
    *,
    consumed_evidence_ids: frozenset[str] | set[str] | None = None,
) -> None:
    """Enforce role-correct evidence requirements (D-04 / REQ-CLUE-02)."""
    src = ClueLifecycleState(from_status)
    dst = ClueLifecycleState(to_status)
    validate_transition_legality(src, dst)
    consumed = set(consumed_evidence_ids or ())

    if dst == ClueLifecycleState.ACTIVE:
        cues = _require_role(evidence, ClueEvidenceRole.CUE)
        if not cues:
            raise LifecycleTransitionError("active requires at least one cue evidence")
        return

    if dst == ClueLifecycleState.REINFORCED:
        reinforcements = _require_role(evidence, ClueEvidenceRole.REINFORCEMENT)
        if not reinforcements:
            raise LifecycleTransitionError(
                "reinforced requires at least one reinforcement evidence"
            )
        for item in reinforcements:
            ident = item.identity_key()
            if ident in consumed:
                raise LifecycleTransitionError(
                    "reinforced requires a new reinforcement evidence identity"
                )
        return

    if dst == ClueLifecycleState.PAID_OFF:
        if src != ClueLifecycleState.REINFORCED:
            raise LifecycleTransitionError("paid_off requires from_status=reinforced")
        cues = _require_role(evidence, ClueEvidenceRole.CUE)
        payoffs = _require_role(evidence, ClueEvidenceRole.PAYOFF)
        if not cues:
            raise LifecycleTransitionError("paid_off requires cue evidence")
        if not payoffs:
            raise LifecycleTransitionError("paid_off requires payoff evidence")
        # Distinct evidence identities and strictly later narrative order.
        cue_ids = {c.identity_key() for c in cues}
        payoff_ids = {p.identity_key() for p in payoffs}
        if cue_ids & payoff_ids:
            raise LifecycleTransitionError(
                "paid_off requires distinct cue and payoff evidence identities"
            )
        earliest_cue = min(c.narrative_key() for c in cues)
        latest_payoff = max(p.narrative_key() for p in payoffs)
        if latest_payoff <= earliest_cue:
            raise LifecycleTransitionError(
                "paid_off requires payoff strictly later than cue in narrative order"
            )
        return

    if dst == ClueLifecycleState.DISMISSED:
        # Disposition evidence is optional for human reject; machine may attach it.
        return

    if dst == ClueLifecycleState.CANDIDATE:
        raise LifecycleTransitionError("candidate is not a valid transition target")


def validate_lifecycle_event(
    event: LifecycleEventInput,
    *,
    consumed_evidence_ids: frozenset[str] | set[str] | None = None,
) -> LifecycleEventInput:
    validate_evidence_for_transition(
        event.from_status,
        event.to_status,
        event.evidence,
        consumed_evidence_ids=consumed_evidence_ids,
    )
    return event


def replay_lifecycle(
    events: list[LifecycleEventInput],
    *,
    initial: ClueLifecycleState = ClueLifecycleState.CANDIDATE,
) -> ClueLifecycleState:
    """
    Replay append-only events into a derived current state.

    Rejects illegal transitions, evidence failures and out-of-order from_status.
    Never mutates prior events.
    """
    state = initial
    consumed: set[str] = set()
    for index, event in enumerate(events):
        if event.from_status != state:
            raise LifecycleTransitionError(
                f"event[{index}] from_status={event.from_status.value} "
                f"does not match current={state.value}"
            )
        validate_lifecycle_event(event, consumed_evidence_ids=consumed)
        for item in event.evidence:
            if item.role == ClueEvidenceRole.REINFORCEMENT:
                consumed.add(item.identity_key())
        state = event.to_status
    return state


# ---------------------------------------------------------------------------
# Version lineage and machine clue
# ---------------------------------------------------------------------------


class ClueVersionLineage(StrictClueModel):
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=64)
    parent_version_id: int | None = Field(default=None, gt=0)
    status: ClueVersionStatus = ClueVersionStatus.CANDIDATE
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hierarchy_build_id: str = Field(min_length=1, max_length=64)
    hierarchy_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeline_version_id: int | None = Field(default=None, gt=0)
    timeline_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decoding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    manifest_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class MachineClueContract(StrictClueModel):
    """Immutable version-scoped machine clue (no authoritative current_status)."""

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    logical_clue_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_snapshot: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    publication_status: Literal["provisional", "published"] = "provisional"
    evidence: list[ClueEvidenceRef] = Field(default_factory=list, max_length=16)

    @field_validator("logical_clue_id", "title")
    @classmethod
    def _no_forbidden(cls, value: str) -> str:
        return _reject_forbidden_token(value) if value.strip().lower() in _FORBIDDEN_DOMAIN_VALUES else value


class ClueLifecycleEventContract(StrictClueModel):
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    logical_clue_id: str = Field(min_length=1, max_length=80)
    from_status: ClueLifecycleState
    to_status: ClueLifecycleState
    actor_source: ClueActorSource
    reason: str = Field(min_length=1, max_length=500)
    evidence: list[ClueEvidenceRef] = Field(default_factory=list, max_length=16)
    event_key: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_event(self) -> "ClueLifecycleEventContract":
        validate_lifecycle_event(
            LifecycleEventInput(
                from_status=self.from_status,
                to_status=self.to_status,
                actor_source=self.actor_source,
                reason=self.reason,
                evidence=self.evidence,
                event_key=self.event_key,
            )
        )
        return self


# ---------------------------------------------------------------------------
# Typed links (exactly one target; never chat)
# ---------------------------------------------------------------------------


class ClueLinkContract(StrictClueModel):
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    logical_clue_id: str = Field(min_length=1, max_length=80)
    target_kind: ClueLinkTargetKind
    character_id: int | None = Field(default=None, gt=0)
    timeline_event_id: int | None = Field(default=None, gt=0)
    relationship_observation_ref: str | None = Field(default=None, min_length=1, max_length=160)
    supporting_evidence: list[ClueEvidenceRef] = Field(default_factory=list, max_length=8)
    validation_status: ClueLinkValidationStatus = ClueLinkValidationStatus.UNRESOLVED
    # Explicit rejection of chat/similarity as evidence carriers.
    chat_text: None = None
    similarity_score: None = None

    @model_validator(mode="after")
    def validate_exactly_one_target(self) -> "ClueLinkContract":
        targets = {
            ClueLinkTargetKind.CHARACTER: self.character_id,
            ClueLinkTargetKind.TIMELINE_EVENT: self.timeline_event_id,
            ClueLinkTargetKind.RELATIONSHIP_OBSERVATION: self.relationship_observation_ref,
        }
        present = [kind for kind, value in targets.items() if value is not None]
        if len(present) != 1:
            raise ValueError("link requires exactly one target kind payload")
        if present[0] != self.target_kind:
            raise ValueError(
                f"target_kind={self.target_kind.value} does not match provided payload"
            )
        if self.target_kind == ClueLinkTargetKind.RELATIONSHIP_OBSERVATION:
            if self.validation_status == ClueLinkValidationStatus.VALID and not self.supporting_evidence:
                raise ValueError(
                    "valid relationship observation links require supporting evidence"
                )
        return self


# ---------------------------------------------------------------------------
# Human overrides (append-only, never overwrite machine version)
# ---------------------------------------------------------------------------


class ClueOverrideContract(StrictClueModel):
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_id: int | None = Field(default=None, gt=0)
    logical_clue_id: str = Field(min_length=1, max_length=80)
    action: ClueOverrideAction
    field_name: str = Field(min_length=1, max_length=80)
    value: dict[str, Any] = Field(default_factory=dict)
    author: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    status: ClueOverrideStatus = ClueOverrideStatus.ACTIVE
    supersedes_id: int | None = Field(default=None, gt=0)
    needs_relink: bool = False
    evidence_signature: str | None = Field(default=None, min_length=8, max_length=160)

    @model_validator(mode="after")
    def validate_action_semantics(self) -> "ClueOverrideContract":
        if self.action == ClueOverrideAction.ANNOTATE and "note" not in self.value:
            raise ValueError("annotate override requires value.note")
        if self.action == ClueOverrideAction.ADJUST_LINK and "link" not in self.value:
            raise ValueError("adjust_link override requires value.link")
        if self.status == ClueOverrideStatus.NEEDS_RELINK and not self.needs_relink:
            raise ValueError("needs_relink status requires needs_relink=true")
        return self


# ---------------------------------------------------------------------------
# LLM-facing judgment (semantic only — no status/write/version authority)
# ---------------------------------------------------------------------------


class ClueSemanticJudgment(StrictClueModel):
    schema_version: Literal["clue-semantic-judgment.v1"] = "clue-semantic-judgment.v1"
    candidate_id: str = Field(min_length=1, max_length=80)
    classification: ClueSemanticClassification
    cue_evidence_ids: list[str] = Field(default_factory=list, max_length=3)
    later_evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1)
    conflict_flags: list[ClueConflictFlag] = Field(default_factory=list, max_length=8)
    rationale: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def validate_classification_shape(self) -> "ClueSemanticJudgment":
        if self.classification in {
            ClueSemanticClassification.CUE_ONLY,
            ClueSemanticClassification.REINFORCEMENT,
            ClueSemanticClassification.PAYOFF,
        }:
            if not self.cue_evidence_ids:
                raise ValueError(f"{self.classification.value} requires cue_evidence_ids")
        if self.classification == ClueSemanticClassification.PAYOFF:
            if not self.later_evidence_ids:
                raise ValueError("payoff classification requires later_evidence_ids")
        # No authority fields may be smuggled via rationale tokens is soft; schema forbids extras.
        return self


# ---------------------------------------------------------------------------
# Version diff and visible API envelopes
# ---------------------------------------------------------------------------


class ClueVersionDiff(StrictClueModel):
    from_version_id: int = Field(gt=0)
    to_version_id: int = Field(gt=0)
    added_logical_clue_ids: list[str] = Field(default_factory=list)
    removed_logical_clue_ids: list[str] = Field(default_factory=list)
    changed_logical_clue_ids: list[str] = Field(default_factory=list)
    lifecycle_differences: list[dict[str, Any]] = Field(default_factory=list)
    override_applications: list[dict[str, Any]] = Field(default_factory=list)


class ClueVisibleItem(StrictClueModel):
    """Derived visible clue row — state is replayed, never a write target."""

    logical_clue_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    derived_state: ClueLifecycleState
    narrative_chapter_number: int = Field(gt=0)
    source_start: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)
    link_count: int = Field(ge=0)
    provenance: dict[str, Literal["machine", "manual"]] = Field(default_factory=dict)
    # Spoiler-safe plant/payoff span for list projection (no full detail fetch).
    first_cue_chapter: int | None = Field(default=None, gt=0)
    # Null when unknown or when payoff chapter is beyond the spoiler cutoff.
    payoff_chapter: int | None = Field(default=None, gt=0)
    summary: str | None = Field(default=None, max_length=240)


class ClueVisibleEnvelope(StrictClueModel):
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    source: ClueVersionSource
    through_chapter: int = Field(gt=0)
    full_book: bool = False
    cutoff_chapter: int = Field(gt=0)
    clues: list[ClueVisibleItem] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    available_states: list[ClueLifecycleState] = Field(default_factory=list)
    available_character_ids: list[int] = Field(default_factory=list)


class ClueHumanActionRequest(StrictClueModel):
    action: ClueOverrideAction
    reason: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=2000)
    link: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ClueHumanActionRequest":
        if self.action == ClueOverrideAction.ANNOTATE and not self.note:
            raise ValueError("annotate requires note")
        if self.action == ClueOverrideAction.ADJUST_LINK and not self.link:
            raise ValueError("adjust_link requires link")
        return self


class ClueRunResponse(StrictClueModel):
    id: int
    novel_id: int
    version_id: int | None = None
    status: ClueRunStatus
    status_reason: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    cancel_requested: bool = False
    updated_at: datetime | None = None
