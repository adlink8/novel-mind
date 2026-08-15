"""Scope-locked, fail-closed gate for one entity / link claim submission.

Extracted from ``entities.py`` (refactor split): ``EntityVerdict`` /
``EntityGateResult`` / ``EntityLinkGateResult`` and ``EntityGate``. The gate
rejects wrong-owner / stale-version claims, stale or missing evidence,
beyond-cutoff disclosure, D-01 authority upgrades and D-06 chat sources, then
blesses the immutable ``WorldEntity`` / ``EntityLink`` candidate. Depends only
on ``_entity_models`` and the ``world_model`` contract primitives — never on
the ``entities`` facade.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.world_model.contracts import (
    Authority,
    EvidenceRef,
    GateStatus,
)
from app.services.world_model.rules import GateReason, SourceKind

from ._entity_models import (
    EntityClaim,
    EntityLink,
    EntityLinkClaim,
    WorldEntity,
)


@dataclass(frozen=True)
class EntityVerdict:
    passed: bool
    reason_code: GateReason
    message: str


@dataclass(frozen=True)
class EntityGateResult:
    entity: WorldEntity | None
    verdicts: tuple[EntityVerdict, ...]

    @property
    def reason_codes(self) -> frozenset[GateReason]:
        return frozenset(verdict.reason_code for verdict in self.verdicts)


@dataclass(frozen=True)
class EntityLinkGateResult:
    link: EntityLink | None
    verdicts: tuple[EntityVerdict, ...]

    @property
    def reason_codes(self) -> frozenset[GateReason]:
        return frozenset(verdict.reason_code for verdict in self.verdicts)


class EntityGate:
    """Scope-locked, fail-closed gate for one entity/link submission."""

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
    ) -> list[EntityVerdict]:
        verdicts: list[EntityVerdict] = []
        if owner_id != self.owner_id or novel_id != self.novel_id:
            verdicts.append(
                EntityVerdict(
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
                EntityVerdict(
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
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.NO_EVIDENCE,
                    message="entity/link requires at least one evidence ref",
                )
            )
        for ref in source_refs:
            if ref.source_snapshot_hash != self.source_snapshot_hash:
                verdicts.append(
                    EntityVerdict(
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
                EntityVerdict(
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
                    EntityVerdict(
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
        if source_kind in {
            SourceKind.READER_CHAT,
            SourceKind.USER_CONVERSATION,
        }:
            verdicts.append(
                EntityVerdict(
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
                EntityVerdict(
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
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.MISSING_APPROVAL,
                    message=(
                        "user_interpretation requires explicit confirmation (D-06)"
                    ),
                )
            )
        return verdicts

    def validate_entity(self, claim: EntityClaim) -> EntityGateResult:
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
            return EntityGateResult(None, tuple(verdicts))
        verdicts.append(
            EntityVerdict(
                passed=True,
                reason_code=GateReason.GATE_PASSED,
                message="entity gate passed",
            )
        )
        entity = WorldEntity(
            entity_key=claim.entity_key,
            entity_type=claim.entity_type,
            primary_name=claim.primary_name,
            description=claim.description,
            aliases=claim.aliases,
            source_kind=claim.source_kind,
            authority=claim.authority,
            confidence=claim.confidence,
            disclosure_cutoff=claim.disclosure_cutoff,
            lineage=(claim.entity_key,),
            source_refs=claim.source_refs,
            gate_status=GateStatus.PASSED,
            gate_reason=None,
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
        )
        return EntityGateResult(entity, tuple(verdicts))

    def validate_link(self, claim: EntityLinkClaim) -> EntityLinkGateResult:
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
            return EntityLinkGateResult(None, tuple(verdicts))
        verdicts.append(
            EntityVerdict(
                passed=True,
                reason_code=GateReason.GATE_PASSED,
                message="entity link gate passed",
            )
        )
        link = EntityLink(
            link_key=claim.link_key,
            link_kind=claim.link_kind,
            source_key=claim.source_key,
            target_key=claim.target_key,
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
        return EntityLinkGateResult(link, tuple(verdicts))
