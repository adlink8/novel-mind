"""Immutable typed contracts for character state / goal / motivation / knowledge.

Phase 27-02 / REQ-WM-02. Semantics locked by decisions D-01..D-06:

- D-01: ``Authority`` keeps canon_fact, probable_inference, literary_interpretation
  and user_interpretation as distinct labels. An epistemic claim can never be
  silently upgraded into ``canon_fact``; the gate rejects it without explicit
  approval.
- D-05: POV/disclosure timing controls what a character knows and what a reader
  may see. Every claim carries ``known_at`` (story-time the character holds it)
  and ``disclosure_cutoff`` (story-time a reader may see it). Hidden knowledge
  stays hidden until its disclosure_cutoff even though the character already
  knows it.
- D-06: Reader Chat is never a world-model fact source. A claim whose
  ``source_kind`` is ``reader_chat`` or ``user_conversation`` can never serialize
  as ``canon_fact``; the gate rejects such attempts. Human corrections are
  ``human_override`` and remain protective.

The model is append-only and history-preserving: mistaken beliefs, hidden
knowledge and contradictions are explicit ``EpistemicStatus`` labels, never
overwritten. State transitions are validated so a history never skips an
unevidenced node. Only immutable candidate projections
(``KnowledgeCandidateProjection``) cross the persistence seam.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.services.world_model.contracts import (
    Authority,
    Description,
    EvidenceRef,
    GateStatus,
    Key,
    PositiveInt,
    StrictModel,
)

KnowledgeHash64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegInt = Annotated[int, Field(ge=0)]

EPISTEMIC_SCHEMA_VERSION = "world-model-knowledge.v1"
EPISTEMIC_HASH_CLAIM = f"{EPISTEMIC_SCHEMA_VERSION}:claim"
EPISTEMIC_HASH_PROJECTION = f"{EPISTEMIC_SCHEMA_VERSION}:projection"
EPISTEMIC_HASH_IDEM = f"{EPISTEMIC_SCHEMA_VERSION}:idem"

OMNISCIENT_POV = "omniscient"


class EpistemicAspect(StrEnum):
    """The kind of character state captured by a claim."""

    STATE = "state"
    GOAL = "goal"
    MOTIVATION = "motivation"
    KNOWLEDGE = "knowledge"


class EpistemicStatus(StrEnum):
    """Explicit epistemic label; never overwritten by later claims (D-04/D-05)."""

    ASSERTED = "asserted"
    MISTAKEN_BELIEF = "mistaken_belief"
    HIDDEN_KNOWLEDGE = "hidden_knowledge"
    RETRACTED = "retracted"
    CONTRADICTION = "contradiction"
    CANDIDATE = "candidate"


class PovKind(StrEnum):
    CHARACTER = "character"
    OMNISCIENT = "omniscient"


class SourceKind(StrEnum):
    """D-06 source provenance: Reader Chat / user chat can never be canon."""

    CANON_SOURCE = "canon_source"
    READER_CHAT = "reader_chat"
    USER_CONVERSATION = "user_conversation"
    HUMAN_OVERRIDE = "human_override"


class KnowledgeResultStatus(StrEnum):
    """Return status for every epistemic query (no fabrication on abstention)."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    CANDIDATE_ONLY = "candidate_only"


class GateReason(StrEnum):
    GATE_PASSED = "gate_passed"
    NO_EVIDENCE = "no_evidence"
    STALE_EVIDENCE = "stale_evidence"
    WRONG_OWNER = "wrong_owner"
    STALE_VERSION = "stale_version"
    SPOILER_CUTOFF = "spoiler_cutoff"
    EVIDENCE_BEYOND_CUTOFF = "evidence_beyond_cutoff"
    MISSING_APPROVAL = "missing_approval"
    AUTHORITY_UPGRADE = "authority_upgrade"
    CHAT_NOT_FACT_SOURCE = "chat_not_fact_source"
    TRANSITION_GAP = "transition_gap"
    TRANSITION_UNKNOWN = "transition_unknown"
    LINEAGE_BROKEN = "lineage_broken"
    UNKNOWN_TRANSITION_SOURCE = "unknown_transition_source"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(component: str, body: str) -> str:
    return hashlib.sha256(f"{component}\n{body}".encode("utf-8")).hexdigest()


def claim_checksum(claim: "EpistemicClaim") -> str:
    return _sha256(
        EPISTEMIC_HASH_CLAIM, _canonical_json(claim.model_dump(mode="json"))
    )


def projection_checksum(projection: "KnowledgeCandidateProjection") -> str:
    body = _canonical_json(
        {
            "owner_id": projection.owner_id,
            "novel_id": projection.novel_id,
            "version_id": projection.version_id,
            "claims": [claim.model_dump(mode="json") for claim in projection.claims],
        }
    )
    return _sha256(EPISTEMIC_HASH_PROJECTION, body)


def row_idempotency_key(component: str, payload: dict[str, Any]) -> str:
    """Deterministic replay key over one row's canonical payload."""
    return _sha256(EPISTEMIC_HASH_IDEM, _canonical_json(payload))


class EpistemicClaim(StrictModel):
    """Immutable append-only character state/goal/motivation/knowledge claim.

    ``known_at`` is the story-time chapter at which the character holds the
    proposition. ``disclosure_cutoff`` is the reader-visibility gate (D-05):
    a claim is only visible at a reader cutoff >= ``disclosure_cutoff``,
    regardless of ``known_at``. ``pov`` names the perspective that produced the
    claim (``OMNISCIENT_POV`` for the omniscient narrator). ``lineage`` is the
    version chain of the same logical knowledge; ``transition_from`` links a
    state claim to its direct evidence-backed predecessor so history never skips
    an unevidenced node.
    """

    claim_kind: str = "character_knowledge"
    knowledge_key: Key
    subject: Key
    aspect: EpistemicAspect
    proposition: Description
    known_at: NonNegInt
    disclosure_cutoff: PositiveInt
    pov: Key
    pov_kind: PovKind = PovKind.CHARACTER
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    epistemic_status: EpistemicStatus
    transition_from: Key | None = None
    lineage: Annotated[tuple[Key, ...], Field(min_length=1)]
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    gate_status: GateStatus
    gate_reason: Annotated[str, StringConstraints(max_length=120)] | None = None
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt

    @field_validator("source_refs")
    @classmethod
    def _unique_evidence(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        ids = [ref.evidence_id for ref in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source_refs must be unique")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _reject_confidence_coercion(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("confidence must be a JSON number with fractional type")
        return value

    @model_validator(mode="after")
    def _lineage_ends_at_self(self) -> "EpistemicClaim":
        if not self.lineage or self.lineage[-1] != self.knowledge_key:
            raise ValueError("lineage must be a version chain ending at this claim")
        return self

    @model_validator(mode="after")
    def _known_before_disclosure(self) -> "EpistemicClaim":
        if self.known_at > self.disclosure_cutoff:
            raise ValueError("known_at must be <= disclosure_cutoff")
        return self

    @property
    def checksum(self) -> str:
        return claim_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(
            EPISTEMIC_HASH_CLAIM, self.model_dump(mode="json")
        )


class KnowledgeCandidateProjection(StrictModel):
    """The only durable output (D-02): a versioned immutable candidate set."""

    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    schema_version: str = EPISTEMIC_SCHEMA_VERSION
    claims: Annotated[tuple[EpistemicClaim, ...], Field(min_length=1)]
    projection_hash: KnowledgeHash64 = "0" * 64

    @model_validator(mode="after")
    def _scope_matches_rows(self) -> "KnowledgeCandidateProjection":
        for claim in self.claims:
            if (
                claim.owner_id != self.owner_id
                or claim.novel_id != self.novel_id
                or claim.version_id != self.version_id
            ):
                raise ValueError("claim scope must match the projection scope")
        return self

    @model_validator(mode="after")
    def _transitions_resolve_within_projection(self) -> "KnowledgeCandidateProjection":
        """No transition may skip an evidence-backed node (state continuity)."""
        claims_by_key = {claim.knowledge_key: claim for claim in self.claims}
        for claim in self.claims:
            if claim.transition_from is None:
                continue
            prior = claims_by_key.get(claim.transition_from)
            if prior is None:
                raise ValueError(
                    f"transition '{claim.transition_from}' is not projection-local"
                )
            if prior.subject != claim.subject or prior.aspect != claim.aspect:
                raise ValueError(
                    "transition source must share subject/aspect with the claim"
                )
            if prior.known_at >= claim.known_at:
                raise ValueError(
                    "transition source must be known strictly before the claim"
                )
            if prior.gate_status != GateStatus.PASSED:
                raise ValueError(
                    "transition source must be a passed evidence-backed node"
                )
        return self

    @property
    def idempotency_key(self) -> str:
        body = _canonical_json(
            {
                "owner_id": self.owner_id,
                "novel_id": self.novel_id,
                "version_id": self.version_id,
                "claims": [claim.model_dump(mode="json") for claim in self.claims],
            }
        )
        return _sha256(EPISTEMIC_HASH_IDEM, body)


def build_knowledge_projection(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    claims: list[EpistemicClaim],
) -> KnowledgeCandidateProjection:
    """Construct the immutable candidate with a sealed projection hash."""
    projection = KnowledgeCandidateProjection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        claims=tuple(claims),
        projection_hash="0" * 64,
    )
    checksum = projection_checksum(projection)
    return projection.model_copy(update={"projection_hash": checksum})


def projection_verified(projection: KnowledgeCandidateProjection) -> bool:
    """Recompute and compare the sealed projection hash (byte-equivalence)."""
    return projection_checksum(projection) == projection.projection_hash


# ---------------------------------------------------------------------------
# Epistemic gate: claim -> passed candidate or stable rejection verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpistemicVerdict:
    passed: bool
    reason_code: GateReason
    message: str


@dataclass(frozen=True)
class EpistemicGateResult:
    claim: EpistemicClaim | None
    verdicts: tuple[EpistemicVerdict, ...]

    @property
    def reason_codes(self) -> frozenset[GateReason]:
        return frozenset(verdict.reason_code for verdict in self.verdicts)


class EpistemicGate:
    """Scope-locked, fail-closed gate for one knowledge projection submission.

    Mirrors the 27-01 event gate: owner/novel/version scope, frozen
    ``source_snapshot_hash`` for evidence, authorized ``disclosure_cutoff``,
    explicit ``approvals`` (D-01 canon_fact / D-06 user_interpretation), plus
    the D-06 Reader Chat source check and transition continuity.
    """

    def __init__(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        source_snapshot_hash: str,
        disclosure_cutoff: int,
        approvals: frozenset[Authority] = frozenset(),
    ) -> None:
        self.owner_id = owner_id
        self.novel_id = novel_id
        self.version_id = version_id
        self.source_snapshot_hash = source_snapshot_hash
        self.disclosure_cutoff = disclosure_cutoff
        self.approvals = approvals

    def validate_claim(self, claim: EpistemicClaim) -> EpistemicGateResult:
        verdicts: list[EpistemicVerdict] = []

        if claim.owner_id != self.owner_id or claim.novel_id != self.novel_id:
            verdicts.append(
                EpistemicVerdict(
                    passed=False,
                    reason_code=GateReason.WRONG_OWNER,
                    message=(
                        f"claim scope {claim.owner_id}/{claim.novel_id} does not "
                        f"match gate scope {self.owner_id}/{self.novel_id}"
                    ),
                )
            )
            return EpistemicGateResult(None, tuple(verdicts))
        if claim.version_id != self.version_id:
            verdicts.append(
                EpistemicVerdict(
                    passed=False,
                    reason_code=GateReason.STALE_VERSION,
                    message=(
                        f"claim version {claim.version_id} is not the gated "
                        f"version {self.version_id}"
                    ),
                )
            )
            return EpistemicGateResult(None, tuple(verdicts))

        if not claim.source_refs:
            verdicts.append(
                EpistemicVerdict(
                    passed=False,
                    reason_code=GateReason.NO_EVIDENCE,
                    message="epistemic claim requires at least one evidence ref",
                )
            )

        for ref in claim.source_refs:
            if ref.source_snapshot_hash != self.source_snapshot_hash:
                verdicts.append(
                    EpistemicVerdict(
                        passed=False,
                        reason_code=GateReason.STALE_EVIDENCE,
                        message=(
                            f"evidence {ref.evidence_id} is stale: snapshot "
                            f"{ref.source_snapshot_hash[:8]}… does not match the "
                            f"frozen source package {self.source_snapshot_hash[:8]}…"
                        ),
                    )
                )
                break

        if claim.disclosure_cutoff > self.disclosure_cutoff:
            verdicts.append(
                EpistemicVerdict(
                    passed=False,
                    reason_code=GateReason.SPOILER_CUTOFF,
                    message=(
                        f"disclosure cutoff {claim.disclosure_cutoff} is beyond "
                        f"the authorized cutoff {self.disclosure_cutoff}"
                    ),
                )
            )
        for ref in claim.source_refs:
            if ref.chapter_number > claim.disclosure_cutoff:
                verdicts.append(
                    EpistemicVerdict(
                        passed=False,
                        reason_code=GateReason.EVIDENCE_BEYOND_CUTOFF,
                        message=(
                            f"evidence {ref.evidence_id} is at chapter "
                            f"{ref.chapter_number}, after the claim cutoff "
                            f"{claim.disclosure_cutoff}"
                        ),
                    )
                )
                break

        # D-06: Reader Chat and user conversations are never fact sources —
        # fail closed on any authority, not just canon_fact.
        if claim.source_kind in (
            SourceKind.READER_CHAT,
            SourceKind.USER_CONVERSATION,
        ):
            verdicts.append(
                EpistemicVerdict(
                    passed=False,
                    reason_code=GateReason.CHAT_NOT_FACT_SOURCE,
                    message=(
                        "Reader Chat / user conversation is never a world-model "
                        "fact source (D-06)"
                    ),
                )
            )

        if (
            claim.authority == Authority.CANON_FACT
            and Authority.CANON_FACT not in self.approvals
        ):
            verdicts.append(
                EpistemicVerdict(
                    passed=False,
                    reason_code=GateReason.AUTHORITY_UPGRADE,
                    message=(
                        "canon_fact requires explicit approval; inference / "
                        "interpretation must never serialize as canon_fact (D-01)"
                    ),
                )
            )
        if (
            claim.authority == Authority.USER_INTERPRETATION
            and Authority.USER_INTERPRETATION not in self.approvals
        ):
            verdicts.append(
                EpistemicVerdict(
                    passed=False,
                    reason_code=GateReason.MISSING_APPROVAL,
                    message=(
                        "user_interpretation requires explicit confirmation (D-06)"
                    ),
                )
            )

        if any(not verdict.passed for verdict in verdicts):
            return EpistemicGateResult(None, tuple(verdicts))

        verdicts.append(
            EpistemicVerdict(
                passed=True,
                reason_code=GateReason.GATE_PASSED,
                message="epistemic gate passed",
            )
        )
        passed = claim.model_copy(
            update={"gate_status": GateStatus.PASSED, "gate_reason": None}
        )
        return EpistemicGateResult(passed, tuple(verdicts))


def build_knowledge_candidate(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    claims: list[EpistemicClaim],
) -> KnowledgeCandidateProjection:
    """Gate-blessed immutable candidate projection with sealed hash."""
    return build_knowledge_projection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        claims=claims,
    )
