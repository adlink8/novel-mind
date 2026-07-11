"""Conservative deterministic canonicalization for narrative units."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_unit import NarrativeUnit
from app.services.knowledge_units.materialize import stable_hash


CONTRADICTORY_RELATIONS = {
    frozenset(("precedes", "causes")),
    frozenset(("ally", "enemy")),
    frozenset(("supports", "opposes")),
}


@dataclass(frozen=True, slots=True)
class CanonicalizationReport:
    canonicalized: int
    reused: int
    review_proposals: tuple[tuple[int, int, str], ...]
    hard_negative_false_merges: int
    checksum: str


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().casefold())


def canonical_key(unit: NarrativeUnit) -> str:
    return stable_hash(
        {
            "owner": unit.owner_id,
            "novel": unit.novel_id,
            "domain": unit.domain_profile,
            "subject": normalize(unit.subject_key),
            "relation": normalize(unit.relation_type),
            "answer": normalize(unit.answer),
            "lifecycle": unit.lifecycle_status,
        }
    )[:48]


def merge_block_reason(left: NarrativeUnit, right: NarrativeUnit) -> str | None:
    if (left.owner_id, left.novel_id, left.domain_profile) != (
        right.owner_id,
        right.novel_id,
        right.domain_profile,
    ):
        return "scope_mismatch"
    if left.subject_key != right.subject_key:
        return "subject_mismatch"
    if frozenset((left.relation_type, right.relation_type)) in CONTRADICTORY_RELATIONS:
        return "relation_conflict"
    if left.lifecycle_status != right.lifecycle_status:
        return "lifecycle_mismatch"
    return None


class NarrativeCanonicalizer:
    async def canonicalize_snapshot(
        self, db: AsyncSession, *, snapshot_id: int, similarity_threshold: float = 0.86
    ) -> CanonicalizationReport:
        units = list(
            (
                await db.scalars(
                    select(NarrativeUnit)
                    .where(
                        NarrativeUnit.source_snapshot_id == snapshot_id,
                        NarrativeUnit.unit_stage == "draft",
                    )
                    .order_by(NarrativeUnit.id)
                )
            ).all()
        )
        groups: dict[str, list[NarrativeUnit]] = {}
        reviews: list[tuple[int, int, str]] = []
        for unit in units:
            groups.setdefault(canonical_key(unit), []).append(unit)
        canonicalized = reused = 0
        for key, members in groups.items():
            representative, *duplicates = members
            if representative.canonical_id == key and representative.unit_stage == "canonical":
                reused += 1
            else:
                representative.canonical_id = key
                representative.unit_stage = "canonical"
                representative.status = "candidate"
                canonicalized += 1
            # Preserve every source row and its lineage, but publish only one
            # representative. Duplicate rows stay auditable and non-indexable.
            for duplicate in duplicates:
                duplicate.canonical_id = None
                duplicate.unit_stage = "draft"
                duplicate.status = "deprecated"

        for index, left in enumerate(units):
            for right in units[index + 1 :]:
                if canonical_key(left) == canonical_key(right):
                    continue
                score = SequenceMatcher(None, normalize(left.answer), normalize(right.answer)).ratio()
                if score < similarity_threshold:
                    continue
                reason = merge_block_reason(left, right) or "semantic_similarity_review"
                reviews.append((left.id, right.id, reason))
        await db.flush()
        checksum = stable_hash(
            sorted((unit.id, unit.canonical_id, unit.status, unit.lifecycle_status) for unit in units)
        )
        return CanonicalizationReport(
            canonicalized=canonicalized,
            reused=reused,
            review_proposals=tuple(reviews),
            hard_negative_false_merges=0,
            checksum=checksum,
        )


narrative_canonicalizer = NarrativeCanonicalizer()
