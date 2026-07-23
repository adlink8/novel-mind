"""Bounded Phase 07 evidence packages and deterministic timeline scope gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.schemas.timeline import TimelineExtraction


class EvidenceScopeError(ValueError):
    """Candidate evidence does not belong to its frozen chapter package."""


@dataclass(frozen=True)
class EvidenceUnit:
    evidence_id: str
    source_start: int
    source_end: int
    text: str
    content_hash: str

    @classmethod
    def create(
        cls, evidence_id: str, source_start: int, source_end: int, text: str
    ) -> "EvidenceUnit":
        if source_start < 0 or source_end <= source_start:
            raise EvidenceScopeError("invalid evidence offsets")
        return cls(
            evidence_id,
            source_start,
            source_end,
            text,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class EvidencePackage:
    owner_id: int
    novel_id: int
    chapter_id: int
    unit_id: str
    source_snapshot_hash: str
    hierarchy_build_id: str
    hierarchy_checksum: str
    units: tuple[EvidenceUnit, ...]
    package_hash: str

    @classmethod
    def create(
        cls,
        *,
        owner_id: int,
        novel_id: int,
        chapter_id: int,
        unit_id: str,
        source_snapshot_hash: str,
        hierarchy_build_id: str,
        hierarchy_checksum: str,
        units: list[EvidenceUnit],
    ) -> "EvidencePackage":
        if owner_id <= 0 or novel_id <= 0 or chapter_id <= 0 or not units:
            raise EvidenceScopeError("evidence package scope and units are required")
        if len(source_snapshot_hash) != 64 or len(hierarchy_checksum) != 64:
            raise EvidenceScopeError("frozen lineage hashes must be SHA-256 values")
        identities = [unit.evidence_id for unit in units]
        if len(set(identities)) != len(identities):
            raise EvidenceScopeError("evidence IDs must be unique within a package")
        payload = {
            "owner_id": owner_id,
            "novel_id": novel_id,
            "chapter_id": chapter_id,
            "unit_id": unit_id,
            "source_snapshot_hash": source_snapshot_hash,
            "hierarchy_build_id": hierarchy_build_id,
            "hierarchy_checksum": hierarchy_checksum,
            "units": [unit.__dict__ for unit in units],
        }
        package_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return cls(
            owner_id,
            novel_id,
            chapter_id,
            unit_id,
            source_snapshot_hash,
            hierarchy_build_id,
            hierarchy_checksum,
            tuple(units),
            package_hash,
        )


def rebind_extraction_to_package(
    package: EvidencePackage, extraction: TimelineExtraction
) -> TimelineExtraction:
    """Script-owned rebind: LLM 只负责选 evidence_id，offsets/hash/chapter 由包权威覆写。

    Vertex 很难逐字节复述 content_hash 与 offsets；若 evidence_id 属于本包，
    一律用 Phase 07 冻结字段替换。未知 evidence_id 丢弃；无有效证据的事件丢弃。
    """
    from app.schemas.timeline import EvidenceRef

    units = {unit.evidence_id: unit for unit in package.units}
    rebound_events = []
    for event in extraction.events:
        unique_refs: list[EvidenceRef] = []
        seen: set[str] = set()
        for ref in event.evidence:
            unit = units.get(ref.evidence_id)
            if unit is None:
                continue
            if unit.evidence_id in seen:
                continue
            seen.add(unit.evidence_id)
            unique_refs.append(
                EvidenceRef(
                    chapter_id=package.chapter_id,
                    evidence_id=unit.evidence_id,
                    source_start=unit.source_start,
                    source_end=unit.source_end,
                    content_hash=unit.content_hash,
                )
            )
        if not unique_refs:
            continue
        rebound_events.append(
            event.model_copy(
                update={
                    "narrative_chapter_number": package.chapter_id,
                    "evidence": unique_refs,
                }
            )
        )
    valid_ids = {event.candidate_id for event in rebound_events}
    constraints = []
    for constraint in extraction.story_time_constraints:
        if (
            constraint.source_candidate_id not in valid_ids
            or constraint.target_candidate_id not in valid_ids
        ):
            continue
        eids = [eid for eid in constraint.evidence_ids if eid in units]
        if not eids:
            continue
        constraints.append(constraint.model_copy(update={"evidence_ids": eids}))
    return TimelineExtraction(events=rebound_events, story_time_constraints=constraints)


def validate_extraction(
    package: EvidencePackage, extraction: TimelineExtraction
) -> None:
    """Validate all model refs against script-owned scope, offsets and source hashes."""
    units = {unit.evidence_id: unit for unit in package.units}
    candidate_ids: set[str] = set()
    for event in extraction.events:
        if event.candidate_id in candidate_ids:
            raise EvidenceScopeError("candidate IDs must be unique within a chapter")
        candidate_ids.add(event.candidate_id)
        if event.narrative_chapter_number != package.chapter_id:
            raise EvidenceScopeError(
                "candidate narrative chapter is outside the package"
            )
        for ref in event.evidence:
            unit = units.get(ref.evidence_id)
            if ref.chapter_id != package.chapter_id or unit is None:
                raise EvidenceScopeError("cross-chapter or unknown evidence reference")
            if (ref.source_start, ref.source_end, ref.content_hash) != (
                unit.source_start,
                unit.source_end,
                unit.content_hash,
            ):
                raise EvidenceScopeError("evidence offset or content hash mismatch")
    for constraint in extraction.story_time_constraints:
        if (
            constraint.source_candidate_id not in candidate_ids
            or constraint.target_candidate_id not in candidate_ids
        ):
            raise EvidenceScopeError(
                "story-time constraint references an unknown candidate"
            )
        if any(evidence_id not in units for evidence_id in constraint.evidence_ids):
            raise EvidenceScopeError(
                "story-time constraint references unknown evidence"
            )
