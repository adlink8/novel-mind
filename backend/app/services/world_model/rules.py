"""Immutable typed contracts and gates for world rules and rule exceptions.

Phase 27-03 / REQ-WM-03. Semantics locked by decisions D-01..D-06:

- D-01: ``Authority`` keeps canon_fact, probable_inference, literary_interpretation
  and user_interpretation as distinct labels. A rule/exception can never be
  silently upgraded into ``canon_fact``; the gate rejects it without explicit
  approval.
- D-03: Every rule and rule exception carries owner/novel/version/cutoff, source
  EvidenceRefs, authority, confidence and gate status. ``lineage`` is the
  version chain of the same logical rule.
- D-04: Rule exceptions are first-class records. They are never folded into the
  rule statement and are never discarded by any normalization; an exception
  always names its ``rule_key`` (and optionally the ``applies_to`` entity).
- D-06: Reader Chat / user conversation is never a world-model fact source. A
  rule/exception claim whose ``source_kind`` is ``reader_chat`` or
  ``user_conversation`` can never pass the gate.

Only immutable candidate objects (``WorldRule`` / ``RuleException``) cross the
persistence seam; claims are gate inputs and are never persisted by themselves.
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

RuleHash64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

RULE_SCHEMA_VERSION = "world-model-rule.v1"
RULE_HASH_RULE = f"{RULE_SCHEMA_VERSION}:rule"
RULE_HASH_EXCEPTION = f"{RULE_SCHEMA_VERSION}:exception"
RULE_HASH_IDEM = f"{RULE_SCHEMA_VERSION}:idem"


class SourceKind(StrEnum):
    """D-06 source provenance: Reader Chat / user chat can never be canon."""

    CANON_SOURCE = "canon_source"
    READER_CHAT = "reader_chat"
    USER_CONVERSATION = "user_conversation"
    HUMAN_OVERRIDE = "human_override"


CHAT_SOURCE_KINDS = frozenset({SourceKind.READER_CHAT, SourceKind.USER_CONVERSATION})


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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(component: str, body: str) -> str:
    return hashlib.sha256(f"{component}\n{body}".encode("utf-8")).hexdigest()


def rule_checksum(rule: "WorldRule") -> str:
    return _sha256(RULE_HASH_RULE, _canonical_json(rule.model_dump(mode="json")))


def exception_checksum(exception: "RuleException") -> str:
    return _sha256(
        RULE_HASH_EXCEPTION, _canonical_json(exception.model_dump(mode="json"))
    )


def row_idempotency_key(component: str, payload: dict[str, Any]) -> str:
    """Deterministic replay key over one row's canonical payload."""
    return _sha256(RULE_HASH_IDEM, _canonical_json(payload))


class WorldRule(StrictModel):
    """Immutable append-only world rule candidate (D-03/D-04)."""

    claim_kind: str = "world_rule"
    rule_key: Key
    rule_name: Key
    statement: Description
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
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
    def _lineage_ends_at_self(self) -> "WorldRule":
        if not self.lineage or self.lineage[-1] != self.rule_key:
            raise ValueError("lineage must be a version chain ending at this rule")
        return self

    @property
    def checksum(self) -> str:
        return rule_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(RULE_HASH_RULE, self.model_dump(mode="json"))


class RuleException(StrictModel):
    """First-class exception to a world rule (D-04).

    ``rule_key`` always names the rule it refines; ``applies_to`` optionally
    names the entity the exception binds. Exceptions are durable records of
    their own: they are never folded into the rule statement, never dropped by
    normalization, and they keep their own evidence and authority.
    """

    exception_key: Key
    rule_key: Key
    applies_to: Key | None = None
    statement: Description
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
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

    @property
    def checksum(self) -> str:
        return exception_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(RULE_HASH_EXCEPTION, self.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Claims (gate inputs, never persisted)
# ---------------------------------------------------------------------------


class RuleClaim(StrictModel):
    """Gate input proposing one world rule."""

    claim_kind: str = "rule"
    rule_key: Key
    rule_name: Key
    statement: Description
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt


class RuleExceptionClaim(StrictModel):
    """Gate input proposing one first-class rule exception."""

    claim_kind: str = "rule_exception"
    exception_key: Key
    rule_key: Key
    applies_to: Key | None = None
    statement: Description
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleVerdict:
    passed: bool
    reason_code: GateReason
    message: str


@dataclass(frozen=True)
class RuleGateResult:
    rule: WorldRule | None
    verdicts: tuple[RuleVerdict, ...]

    @property
    def reason_codes(self) -> frozenset[GateReason]:
        return frozenset(verdict.reason_code for verdict in self.verdicts)


@dataclass(frozen=True)
class ExceptionGateResult:
    exception: RuleException | None
    verdicts: tuple[RuleVerdict, ...]

    @property
    def reason_codes(self) -> frozenset[GateReason]:
        return frozenset(verdict.reason_code for verdict in self.verdicts)


class RuleGate:
    """Scope-locked, fail-closed gate for one rule/exception submission.

    Mirrors the 27-01/27-02 gates: owner/novel/version scope, frozen
    ``source_snapshot_hash`` for evidence, authorized ``disclosure_cutoff``,
    explicit ``approvals`` (D-01 canon_fact / D-06 user_interpretation), and the
    D-06 Reader Chat source check.
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

    def _base_verdicts(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        source_kind: SourceKind,
        authority: Authority,
        disclosure_cutoff: int,
        source_refs: tuple[EvidenceRef, ...],
    ) -> list[RuleVerdict]:
        verdicts: list[RuleVerdict] = []
        if owner_id != self.owner_id or novel_id != self.novel_id:
            verdicts.append(
                RuleVerdict(
                    passed=False,
                    reason_code=GateReason.WRONG_OWNER,
                    message=(
                        f"claim scope {owner_id}/{novel_id} does not match gate "
                        f"scope {self.owner_id}/{self.novel_id}"
                    ),
                )
            )
            return verdicts
        if version_id != self.version_id:
            verdicts.append(
                RuleVerdict(
                    passed=False,
                    reason_code=GateReason.STALE_VERSION,
                    message=(
                        f"claim version {version_id} is not the gated version "
                        f"{self.version_id}"
                    ),
                )
            )
            return verdicts

        if not source_refs:
            verdicts.append(
                RuleVerdict(
                    passed=False,
                    reason_code=GateReason.NO_EVIDENCE,
                    message="rule/exception requires at least one evidence ref",
                )
            )
        for ref in source_refs:
            if ref.source_snapshot_hash != self.source_snapshot_hash:
                verdicts.append(
                    RuleVerdict(
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

        if disclosure_cutoff > self.disclosure_cutoff:
            verdicts.append(
                RuleVerdict(
                    passed=False,
                    reason_code=GateReason.SPOILER_CUTOFF,
                    message=(
                        f"disclosure cutoff {disclosure_cutoff} is beyond the "
                        f"authorized cutoff {self.disclosure_cutoff}"
                    ),
                )
            )
        for ref in source_refs:
            if ref.chapter_number > disclosure_cutoff:
                verdicts.append(
                    RuleVerdict(
                        passed=False,
                        reason_code=GateReason.EVIDENCE_BEYOND_CUTOFF,
                        message=(
                            f"evidence {ref.evidence_id} is at chapter "
                            f"{ref.chapter_number}, after the claim cutoff "
                            f"{disclosure_cutoff}"
                        ),
                    )
                )
                break

        # D-06: Reader Chat / user conversation is never a fact source.
        if source_kind in CHAT_SOURCE_KINDS:
            verdicts.append(
                RuleVerdict(
                    passed=False,
                    reason_code=GateReason.CHAT_NOT_FACT_SOURCE,
                    message=(
                        "Reader Chat / user conversation is never a world-model "
                        "fact source (D-06)"
                    ),
                )
            )

        if (
            authority == Authority.CANON_FACT
            and Authority.CANON_FACT not in self.approvals
        ):
            verdicts.append(
                RuleVerdict(
                    passed=False,
                    reason_code=GateReason.AUTHORITY_UPGRADE,
                    message=(
                        "canon_fact requires explicit approval; inference / "
                        "interpretation must never serialize as canon_fact (D-01)"
                    ),
                )
            )
        if (
            authority == Authority.USER_INTERPRETATION
            and Authority.USER_INTERPRETATION not in self.approvals
        ):
            verdicts.append(
                RuleVerdict(
                    passed=False,
                    reason_code=GateReason.MISSING_APPROVAL,
                    message=(
                        "user_interpretation requires explicit confirmation (D-06)"
                    ),
                )
            )
        return verdicts

    def validate_rule(self, claim: RuleClaim) -> RuleGateResult:
        verdicts = self._base_verdicts(
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
            source_kind=claim.source_kind,
            authority=claim.authority,
            disclosure_cutoff=claim.disclosure_cutoff,
            source_refs=claim.source_refs,
        )
        if any(not verdict.passed for verdict in verdicts):
            return RuleGateResult(None, tuple(verdicts))
        verdicts.append(
            RuleVerdict(
                passed=True,
                reason_code=GateReason.GATE_PASSED,
                message="rule gate passed",
            )
        )
        rule = WorldRule(
            rule_key=claim.rule_key,
            rule_name=claim.rule_name,
            statement=claim.statement,
            source_kind=claim.source_kind,
            authority=claim.authority,
            confidence=claim.confidence,
            disclosure_cutoff=claim.disclosure_cutoff,
            lineage=(claim.rule_key,),
            source_refs=claim.source_refs,
            gate_status=GateStatus.PASSED,
            gate_reason=None,
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
        )
        return RuleGateResult(rule, tuple(verdicts))

    def validate_exception(
        self, claim: RuleExceptionClaim, rule_keys: set[str]
    ) -> ExceptionGateResult:
        verdicts = self._base_verdicts(
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
            source_kind=claim.source_kind,
            authority=claim.authority,
            disclosure_cutoff=claim.disclosure_cutoff,
            source_refs=claim.source_refs,
        )
        if any(not verdict.passed for verdict in verdicts):
            return ExceptionGateResult(None, tuple(verdicts))
        if claim.rule_key not in rule_keys:
            verdicts.append(
                RuleVerdict(
                    passed=False,
                    reason_code=GateReason.NO_EVIDENCE,
                    message=(
                        f"exception '{claim.exception_key}' references unknown "
                        f"rule '{claim.rule_key}' — exceptions are first-class "
                        f"but always bound to a projection-local rule"
                    ),
                )
            )
            return ExceptionGateResult(None, tuple(verdicts))
        verdicts.append(
            RuleVerdict(
                passed=True,
                reason_code=GateReason.GATE_PASSED,
                message="rule exception gate passed",
            )
        )
        exception = RuleException(
            exception_key=claim.exception_key,
            rule_key=claim.rule_key,
            applies_to=claim.applies_to,
            statement=claim.statement,
            source_kind=claim.source_kind,
            authority=claim.authority,
            confidence=claim.confidence,
            disclosure_cutoff=claim.disclosure_cutoff,
            source_refs=claim.source_refs,
            gate_status=GateStatus.PASSED,
            gate_reason=None,
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
        )
        return ExceptionGateResult(exception, tuple(verdicts))
