"""Deterministic source selection and candidate packages for relationship observations.

Only Phase 04 accepted/accepted fiction judgments with character endpoints and the
five fiction edge labels may produce packages. Vector/BM25/same_entity/history/
causes/precedes never become observations on their own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis import AnalysisVersion
from app.models.character import Character
from app.models.knowledge import (
    KnowledgeEntityCandidate,
    KnowledgeEvidenceRef,
    KnowledgeRelationJudgment,
)
from app.models.novel import Chapter, Novel
from app.models.relationship import RELATIONSHIP_EDGE_TYPES
from app.models.text_chunk import TextChunk
from app.services.relationships.evidence import (
    RelationshipEvidencePackage,
    RelationshipEvidenceUnit,
    build_relationship_evidence_package,
    make_evidence_unit,
    sha256_json,
)

logger = logging.getLogger(__name__)

ALLOWED_RELATIONSHIP_EDGE_TYPES = frozenset(RELATIONSHIP_EDGE_TYPES)
# Explicit non-edges: timeline/identity signals must never produce packages.
NON_EDGE_RELATION_TYPES = frozenset(
    {
        "causes",
        "precedes",
        "same_entity",
        "caused",
        "preceded",
        "history",
        "allied_with",
        "conflicted_with",
        "ruled",
        "served",
        "succeeded",
    }
)
CHARACTER_ENDPOINT_KINDS = frozenset({"character", "entity_candidate"})


@dataclass(slots=True)
class IdentityReviewSignal:
    """Recall-only same_entity / alias signal; never creates an edge observation."""

    source_judgment_id: int
    source_relation_candidate_id: int
    left_ref: str
    right_ref: str
    recall_signals: dict[str, Any]
    reason: str = "same_entity_identity_review"


@dataclass(slots=True)
class RelationshipCandidateDraft:
    """Deterministic package draft prior to ORM persistence."""

    owner_id: int
    novel_id: int
    analysis_version_id: int
    source_judgment_id: int
    source_relation_candidate_id: int
    source_character_id: int
    target_character_id: int
    relation_type: str
    package: RelationshipEvidencePackage
    recall_signals: dict[str, Any] = field(default_factory=dict)
    rejection_reason: str | None = None

    @property
    def package_hash(self) -> str:
        return self.package.package_hash

    @property
    def evidence_refs(self) -> list[str]:
        return self.package.allowed_evidence_ids()


@dataclass(slots=True)
class CandidateSelectionResult:
    drafts: list[RelationshipCandidateDraft] = field(default_factory=list)
    identity_reviews: list[IdentityReviewSignal] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)


class RelationshipCandidateService:
    """Select accepted fiction judgments and materialize version-bound packages."""

    async def select_and_build(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int,
    ) -> CandidateSelectionResult:
        """Revalidate sources at build time and produce packages (no observation writes)."""

        version = await self._load_version(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=analysis_version_id,
        )
        novel = await self._load_novel(db, owner_id=owner_id, novel_id=novel_id)
        if getattr(novel, "domain_profile", None) not in (None, "fiction"):
            # Novels without domain_profile default to fiction product scope.
            if getattr(novel, "domain_profile", None) == "history":
                return CandidateSelectionResult(
                    rejections=[{"reason": "fiction_gate:novel_history_profile"}]
                )

        judgments = await self._load_accepted_judgments(
            db, owner_id=owner_id, novel_id=novel_id
        )
        result = CandidateSelectionResult()
        characters = await self._load_characters(db, novel_id=novel_id)
        characters_by_id = {c.id: c for c in characters}
        characters_by_name = {c.name.strip().lower(): c for c in characters if c.name}

        for judgment in judgments:
            candidate = judgment.candidate
            if candidate is None:
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": "missing_relation_candidate",
                    }
                )
                continue

            # Revalidate accepted/accepted at build time (source may have been revoked).
            if judgment.status != "accepted" or judgment.gate_status != "accepted":
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": "source_acceptance_gate:not_accepted",
                    }
                )
                continue

            if candidate.domain_profile != "fiction":
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": f"fiction_gate:domain_profile={candidate.domain_profile}",
                    }
                )
                continue

            relation_type = (judgment.relation_type or candidate.relation_type or "").strip()
            if relation_type in NON_EDGE_RELATION_TYPES:
                if relation_type == "same_entity":
                    result.identity_reviews.append(
                        IdentityReviewSignal(
                            source_judgment_id=judgment.id,
                            source_relation_candidate_id=candidate.id,
                            left_ref=f"{candidate.source_kind}:{candidate.source_id}",
                            right_ref=f"{candidate.target_kind}:{candidate.target_id}",
                            recall_signals=dict(candidate.recall_signals or {}),
                        )
                    )
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": f"non_edge_relation_type:{relation_type}",
                    }
                )
                continue

            if relation_type not in ALLOWED_RELATIONSHIP_EDGE_TYPES:
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": f"relation_type_not_allowed:{relation_type}",
                    }
                )
                continue

            if (
                candidate.source_kind not in CHARACTER_ENDPOINT_KINDS
                or candidate.target_kind not in CHARACTER_ENDPOINT_KINDS
            ):
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": (
                            f"endpoint_kind_rejected:"
                            f"{candidate.source_kind}/{candidate.target_kind}"
                        ),
                    }
                )
                continue

            try:
                source_char = await self._resolve_character_endpoint(
                    db,
                    novel_id=novel_id,
                    kind=candidate.source_kind,
                    endpoint_id=candidate.source_id,
                    characters_by_id=characters_by_id,
                    characters_by_name=characters_by_name,
                )
                target_char = await self._resolve_character_endpoint(
                    db,
                    novel_id=novel_id,
                    kind=candidate.target_kind,
                    endpoint_id=candidate.target_id,
                    characters_by_id=characters_by_id,
                    characters_by_name=characters_by_name,
                )
            except ValueError as exc:
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": f"unresolved_endpoint:{exc}",
                    }
                )
                continue

            if source_char.id == target_char.id:
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": "self_edge_forbidden",
                    }
                )
                continue

            try:
                units = await self._build_evidence_units(
                    db,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    run_id=judgment.run_id,
                    evidence_refs=list(judgment.evidence_refs or candidate.evidence_refs or []),
                )
            except ValueError as exc:
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": f"evidence_build_failed:{exc}",
                    }
                )
                continue

            source_judgment_checksum = sha256_json(
                {
                    "judgment_id": judgment.id,
                    "relation_type": relation_type,
                    "confidence": judgment.confidence,
                    "evidence_refs": list(judgment.evidence_refs or []),
                    "status": judgment.status,
                    "gate_status": judgment.gate_status,
                }
            )
            candidate_key = (
                f"sj:{judgment.id}:sc:{source_char.id}:tc:{target_char.id}:rt:{relation_type}"
            )
            try:
                package = build_relationship_evidence_package(
                    owner_id=owner_id,
                    novel_id=novel_id,
                    analysis_version_id=version.id,
                    candidate_key=candidate_key,
                    source_judgment_id=judgment.id,
                    source_relation_candidate_id=candidate.id,
                    source_character_id=source_char.id,
                    target_character_id=target_char.id,
                    source_ref=f"character:{source_char.id}",
                    target_ref=f"character:{target_char.id}",
                    relation_type=relation_type,
                    source_snapshot_hash=version.source_snapshot_hash,
                    hierarchy_build_id=version.hierarchy_build_id,
                    hierarchy_checksum=version.hierarchy_checksum,
                    source_judgment_checksum=source_judgment_checksum,
                    units=units,
                    recall_signals=dict(candidate.recall_signals or {}),
                )
            except ValueError as exc:
                result.rejections.append(
                    {
                        "source_judgment_id": judgment.id,
                        "reason": f"package_build_failed:{exc}",
                    }
                )
                continue

            result.drafts.append(
                RelationshipCandidateDraft(
                    owner_id=owner_id,
                    novel_id=novel_id,
                    analysis_version_id=version.id,
                    source_judgment_id=judgment.id,
                    source_relation_candidate_id=candidate.id,
                    source_character_id=source_char.id,
                    target_character_id=target_char.id,
                    relation_type=relation_type,
                    package=package,
                    recall_signals=dict(candidate.recall_signals or {}),
                )
            )

        return result

    def build_package_from_parts(
        self,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int,
        source_judgment_id: int,
        source_relation_candidate_id: int,
        source_character_id: int,
        target_character_id: int,
        relation_type: str,
        source_snapshot_hash: str,
        hierarchy_build_id: str,
        hierarchy_checksum: str,
        source_judgment_checksum: str,
        units: list[RelationshipEvidenceUnit],
        recall_signals: dict[str, Any] | None = None,
    ) -> RelationshipEvidencePackage:
        """Pure package builder for unit tests (no DB, no observation writes)."""

        if relation_type not in ALLOWED_RELATIONSHIP_EDGE_TYPES:
            raise ValueError(f"relation_type not allowed: {relation_type}")
        candidate_key = (
            f"sj:{source_judgment_id}:sc:{source_character_id}:"
            f"tc:{target_character_id}:rt:{relation_type}"
        )
        return build_relationship_evidence_package(
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=analysis_version_id,
            candidate_key=candidate_key,
            source_judgment_id=source_judgment_id,
            source_relation_candidate_id=source_relation_candidate_id,
            source_character_id=source_character_id,
            target_character_id=target_character_id,
            source_ref=f"character:{source_character_id}",
            target_ref=f"character:{target_character_id}",
            relation_type=relation_type,
            source_snapshot_hash=source_snapshot_hash,
            hierarchy_build_id=hierarchy_build_id,
            hierarchy_checksum=hierarchy_checksum,
            source_judgment_checksum=source_judgment_checksum,
            units=units,
            recall_signals=recall_signals,
        )

    async def _load_version(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int,
    ) -> AnalysisVersion:
        result = await db.execute(
            select(AnalysisVersion).where(
                AnalysisVersion.id == analysis_version_id,
                AnalysisVersion.owner_id == owner_id,
                AnalysisVersion.novel_id == novel_id,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise ValueError("analysis version not found for owner/novel scope")
        return version

    async def _load_novel(
        self, db: AsyncSession, *, owner_id: int, novel_id: int
    ) -> Novel:
        result = await db.execute(
            select(Novel).where(Novel.id == novel_id, Novel.owner_id == owner_id)
        )
        novel = result.scalar_one_or_none()
        if novel is None:
            raise ValueError("novel not found for owner scope")
        return novel

    async def _load_accepted_judgments(
        self, db: AsyncSession, *, owner_id: int, novel_id: int
    ) -> list[KnowledgeRelationJudgment]:
        result = await db.execute(
            select(KnowledgeRelationJudgment)
            .options(selectinload(KnowledgeRelationJudgment.candidate))
            .where(
                KnowledgeRelationJudgment.owner_id == owner_id,
                KnowledgeRelationJudgment.novel_id == novel_id,
                KnowledgeRelationJudgment.status == "accepted",
                KnowledgeRelationJudgment.gate_status == "accepted",
            )
            .order_by(KnowledgeRelationJudgment.id.asc())
        )
        return list(result.scalars().all())

    async def _load_characters(
        self, db: AsyncSession, *, novel_id: int
    ) -> list[Character]:
        result = await db.execute(
            select(Character).where(Character.novel_id == novel_id).order_by(Character.id)
        )
        return list(result.scalars().all())

    async def _resolve_character_endpoint(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        kind: str,
        endpoint_id: int,
        characters_by_id: dict[int, Character],
        characters_by_name: dict[str, Character],
    ) -> Character:
        if kind == "character":
            character = characters_by_id.get(endpoint_id)
            if character is None or character.novel_id != novel_id:
                raise ValueError(f"character_id={endpoint_id} not in novel")
            return character

        if kind == "entity_candidate":
            entity = await db.get(KnowledgeEntityCandidate, endpoint_id)
            if entity is None or entity.novel_id != novel_id:
                raise ValueError(f"entity_candidate_id={endpoint_id} missing")
            if entity.entity_type not in {"character", "person", "protagonist"}:
                raise ValueError(
                    f"entity_type={entity.entity_type} is not a character endpoint"
                )
            key = (entity.canonical_name or "").strip().lower()
            character = characters_by_name.get(key)
            if character is None:
                raise ValueError(
                    f"no Character row for entity name={entity.canonical_name!r}"
                )
            return character

        raise ValueError(f"unsupported endpoint kind={kind}")

    async def _build_evidence_units(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        run_id: int,
        evidence_refs: list[str],
    ) -> list[RelationshipEvidenceUnit]:
        refs = [str(r) for r in evidence_refs if str(r).strip()]
        if not refs:
            raise ValueError("empty evidence_refs")

        result = await db.execute(
            select(KnowledgeEvidenceRef).where(
                KnowledgeEvidenceRef.owner_id == owner_id,
                KnowledgeEvidenceRef.novel_id == novel_id,
                KnowledgeEvidenceRef.run_id == run_id,
                KnowledgeEvidenceRef.ref_key.in_(refs),
            )
        )
        evidence_rows = {row.ref_key: row for row in result.scalars().all()}
        missing = sorted(set(refs) - set(evidence_rows))
        if missing:
            raise ValueError(f"missing_evidence:{','.join(missing)}")

        units: list[RelationshipEvidenceUnit] = []
        for ref_key in refs:
            row = evidence_rows[ref_key]
            unit = await self._unit_from_evidence_row(db, row=row)
            units.append(unit)
            if len(units) >= 8:
                break
        return units

    async def _unit_from_evidence_row(
        self,
        db: AsyncSession,
        *,
        row: KnowledgeEvidenceRef,
    ) -> RelationshipEvidenceUnit:
        chapter_id = row.chapter_id
        text_chunk_id = row.text_chunk_id
        content = row.excerpt or ""
        source_start = int(row.char_start or 0)
        source_end = int(row.char_end or 0)

        if text_chunk_id:
            chunk = await db.get(TextChunk, text_chunk_id)
            if chunk is None or chunk.novel_id != row.novel_id:
                raise ValueError(f"text_chunk_scope_mismatch:{row.ref_key}")
            content = chunk.content or content
            chapter_id = chapter_id or chunk.chapter_id
            if source_end <= source_start:
                source_start = 0
                source_end = max(len(content), 1)

        if not chapter_id:
            raise ValueError(f"evidence_missing_chapter:{row.ref_key}")

        chapter = await db.get(Chapter, chapter_id)
        if chapter is None or chapter.novel_id != row.novel_id:
            raise ValueError(f"chapter_scope_mismatch:{row.ref_key}")

        if source_end <= source_start:
            # Fall back to excerpt as a bounded unit.
            content = row.excerpt or content or " "
            source_start = 0
            source_end = max(len(content), 1)

        narrative_index = 0
        if text_chunk_id:
            chunk = await db.get(TextChunk, text_chunk_id)
            if chunk is not None:
                narrative_index = int(chunk.chunk_index or 0)

        return make_evidence_unit(
            evidence_id=row.ref_key,
            chapter_id=chapter.id,
            chapter_number=int(chapter.chapter_number),
            narrative_index=narrative_index,
            text=content,
            source_start=source_start,
            source_end=source_end,
            text_chunk_id=text_chunk_id,
        )


relationship_candidate_service = RelationshipCandidateService()
