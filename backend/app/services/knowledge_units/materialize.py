"""Deterministically materialize narrative units from frozen accepted judgments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeRelationCandidate, KnowledgeRelationJudgment
from app.models.knowledge_unit import (
    NarrativeSourceSnapshot,
    NarrativeSourceSnapshotItem,
    NarrativeUnit,
    NarrativeUnitEvidenceLink,
)


class MaterializationError(ValueError):
    """Frozen source data cannot be represented as a grounded unit."""


@dataclass(frozen=True, slots=True)
class MaterializationReport:
    snapshot_id: int
    created: int
    reused: int
    rejected: tuple[str, ...]
    manifest_checksum: str


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def endpoint_key(kind: str, identifier: int) -> str:
    return f"{kind}:{identifier}"


def unit_text(
    candidate: KnowledgeRelationCandidate, judgment: KnowledgeRelationJudgment
) -> tuple[str, str]:
    source = endpoint_key(candidate.source_kind, candidate.source_id)
    target = endpoint_key(candidate.target_kind, candidate.target_id)
    relation = judgment.relation_type
    question = f"{source} 与 {target} 的 {relation} 关系是什么？"
    answer = f"{source} --{relation}--> {target}。"
    return question, answer


class NarrativeUnitMaterializer:
    async def materialize_snapshot(
        self,
        db: AsyncSession,
        *,
        snapshot_id: int,
        write: bool = True,
        judgment_ids: set[int] | None = None,
    ) -> MaterializationReport:
        snapshot = await db.get(NarrativeSourceSnapshot, snapshot_id)
        if snapshot is None or snapshot.status != "frozen":
            raise MaterializationError("snapshot is missing or not frozen")

        rows = (
            await db.execute(
                select(
                    NarrativeSourceSnapshotItem,
                    KnowledgeRelationJudgment,
                    KnowledgeRelationCandidate,
                )
                .join(
                    KnowledgeRelationJudgment,
                    KnowledgeRelationJudgment.id
                    == NarrativeSourceSnapshotItem.source_judgment_id,
                )
                .join(
                    KnowledgeRelationCandidate,
                    KnowledgeRelationCandidate.id
                    == NarrativeSourceSnapshotItem.source_candidate_id,
                )
                .where(
                    NarrativeSourceSnapshotItem.snapshot_id == snapshot_id,
                    *(
                        [
                            NarrativeSourceSnapshotItem.source_judgment_id.in_(
                                judgment_ids
                            )
                        ]
                        if judgment_ids is not None
                        else []
                    ),
                )
                .order_by(NarrativeSourceSnapshotItem.id)
            )
        ).all()
        created = reused = 0
        rejected: list[str] = []
        hashes: list[str] = []
        for item, judgment, candidate in rows:
            try:
                self._validate(snapshot, item, judgment, candidate)
            except MaterializationError as exc:
                rejected.append(f"judgment:{judgment.id}:{exc}")
                continue
            evidence = list(item.evidence_manifest or [])
            evidence_ids = [int(entry["source_evidence_id"]) for entry in evidence]
            evidence_checksum = stable_hash(
                [entry["content_hash"] for entry in evidence]
            )
            question, answer = unit_text(candidate, judgment)
            subject_key = endpoint_key(candidate.source_kind, candidate.source_id)
            content_hash = stable_hash(
                {
                    "owner_id": snapshot.owner_id,
                    "novel_id": snapshot.novel_id,
                    "domain_profile": snapshot.domain_profile,
                    "subject": subject_key,
                    "target": endpoint_key(candidate.target_kind, candidate.target_id),
                    "relation_type": judgment.relation_type,
                    "question": question,
                    "answer": answer,
                    "evidence": evidence_checksum,
                }
            )
            hashes.append(content_hash)
            existing = await db.scalar(
                select(NarrativeUnit).where(
                    NarrativeUnit.source_snapshot_id == snapshot_id,
                    NarrativeUnit.source_judgment_id == judgment.id,
                    NarrativeUnit.content_hash == content_hash,
                )
            )
            if existing is not None:
                reused += 1
                continue
            if not write:
                created += 1
                continue
            unit = NarrativeUnit(
                owner_id=snapshot.owner_id,
                novel_id=snapshot.novel_id,
                source_snapshot_id=snapshot.id,
                source_judgment_id=judgment.id,
                source_candidate_id=candidate.id,
                primary_evidence_id=evidence_ids[0],
                domain_profile=snapshot.domain_profile,
                ontology_profile=snapshot.ontology_profile,
                unit_stage="draft",
                status="draft",
                lifecycle_status="disputed" if judgment.risk_flags else "current",
                canonical_id=None,
                version=1,
                subject_key=subject_key,
                relation_type=judgment.relation_type,
                question=question,
                answer=answer,
                confidence=judgment.confidence,
                evidence_count=len(evidence_ids),
                content_hash=content_hash,
                evidence_manifest_checksum=evidence_checksum,
                prompt_hash=stable_hash(judgment.prompt_version),
                schema_hash=stable_hash("narrative-unit.v1"),
                model_hash=stable_hash(judgment.model_name),
            )
            db.add(unit)
            await db.flush()
            for entry in evidence:
                db.add(
                    NarrativeUnitEvidenceLink(
                        owner_id=snapshot.owner_id,
                        novel_id=snapshot.novel_id,
                        unit_id=unit.id,
                        source_evidence_id=int(entry["source_evidence_id"]),
                        ref_key=str(entry["ref_key"]),
                        content_hash=str(entry["content_hash"]),
                    )
                )
            created += 1
        if write:
            await db.flush()
        return MaterializationReport(
            snapshot_id=snapshot_id,
            created=created,
            reused=reused,
            rejected=tuple(rejected),
            manifest_checksum=stable_hash(sorted(hashes)),
        )

    @staticmethod
    def _validate(snapshot, item, judgment, candidate) -> None:
        if judgment.status != "accepted" or judgment.gate_status != "accepted":
            raise MaterializationError("judgment is not accepted")
        if candidate.status != "accepted":
            raise MaterializationError("candidate is not accepted")
        if not item.evidence_manifest:
            raise MaterializationError("evidence manifest is empty")
        if (
            snapshot.owner_id != judgment.owner_id
            or snapshot.novel_id != judgment.novel_id
        ):
            raise MaterializationError("owner/work lineage mismatch")
        if candidate.domain_profile != snapshot.domain_profile:
            raise MaterializationError("domain profile mismatch")


narrative_unit_materializer = NarrativeUnitMaterializer()
