"""Freeze accepted Phase 04 judgments into deterministic source manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.knowledge import (
    KnowledgeEvidenceRef,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.knowledge_unit import (
    NarrativeSourceSnapshot,
    NarrativeSourceSnapshotItem,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk


class SourceSnapshotError(ValueError):
    """Base error for a source set that cannot be frozen safely."""


class NoAcceptedJudgmentsError(SourceSnapshotError):
    """No doubly accepted Phase 04 judgments exist for the requested scope."""


class InvalidSourceLineageError(SourceSnapshotError):
    """An accepted row has missing, projected, or cross-owner lineage."""


class MovingSourceInputsError(SourceSnapshotError):
    """Source rows changed while the immutable snapshot was being created."""


@dataclass(frozen=True, slots=True)
class FrozenSnapshotItem:
    judgment_id: int
    candidate_id: int
    judgment_content_hash: str
    candidate_content_hash: str
    evidence_content_hash: str
    item_content_hash: str
    evidence_manifest: tuple[dict[str, Any], ...]
    source_version_hash: str

    def checksum_payload(self) -> dict[str, Any]:
        return {
            "judgment_id": self.judgment_id,
            "candidate_id": self.candidate_id,
            "judgment_content_hash": self.judgment_content_hash,
            "candidate_content_hash": self.candidate_content_hash,
            "evidence_content_hash": self.evidence_content_hash,
            "item_content_hash": self.item_content_hash,
            "evidence_manifest": list(self.evidence_manifest),
        }


@dataclass(frozen=True, slots=True)
class FrozenSourceManifest:
    owner_id: int
    novel_id: int
    domain_profile: str
    ontology_profile: str
    items: tuple[FrozenSnapshotItem, ...]
    manifest_checksum: str
    source_watermark: str


class SourceSnapshotService:
    """Create idempotent snapshots without coupling to Phase 04 gates."""

    async def create_snapshot(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        domain_profile: str,
    ) -> NarrativeSourceSnapshot:
        if domain_profile not in {"fiction", "history"}:
            raise InvalidSourceLineageError("unsupported domain profile")
        await self._assert_owned_novel(db, owner_id=owner_id, novel_id=novel_id)
        await self._reject_candidate_only_rows(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            domain_profile=domain_profile,
        )

        initial = await self._build_manifest(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            domain_profile=domain_profile,
        )
        stable = await self._build_manifest(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            domain_profile=domain_profile,
        )
        self._assert_stable(initial, stable)

        existing = await self._find_existing(db, initial)
        if existing is not None:
            return existing

        snapshot: NarrativeSourceSnapshot | None = None
        async with db.begin_nested():
            snapshot = NarrativeSourceSnapshot(
                owner_id=owner_id,
                novel_id=novel_id,
                domain_profile=domain_profile,
                ontology_profile=initial.ontology_profile,
                status="frozen",
                source_watermark=initial.source_watermark,
                manifest_checksum=initial.manifest_checksum,
                item_count=len(initial.items),
            )
            db.add(snapshot)
            await db.flush()
            db.add_all(
                [
                    NarrativeSourceSnapshotItem(
                        owner_id=owner_id,
                        novel_id=novel_id,
                        snapshot_id=snapshot.id,
                        source_judgment_id=item.judgment_id,
                        source_candidate_id=item.candidate_id,
                        judgment_content_hash=item.judgment_content_hash,
                        candidate_content_hash=item.candidate_content_hash,
                        evidence_content_hash=item.evidence_content_hash,
                        item_content_hash=item.item_content_hash,
                        evidence_manifest=list(item.evidence_manifest),
                    )
                    for item in initial.items
                ]
            )
            await db.flush()
            final = await self._build_manifest(
                db,
                owner_id=owner_id,
                novel_id=novel_id,
                domain_profile=domain_profile,
            )
            self._assert_stable(initial, final)

        if snapshot is None:  # pragma: no cover - defensive type narrowing
            raise RuntimeError("snapshot savepoint completed without a snapshot")
        return snapshot

    async def _build_manifest(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        domain_profile: str,
    ) -> FrozenSourceManifest:
        result = await db.execute(
            select(KnowledgeRelationJudgment)
            .options(
                selectinload(KnowledgeRelationJudgment.candidate),
                selectinload(KnowledgeRelationJudgment.run),
            )
            .where(
                KnowledgeRelationJudgment.owner_id == owner_id,
                KnowledgeRelationJudgment.novel_id == novel_id,
                KnowledgeRelationJudgment.status == "accepted",
                KnowledgeRelationJudgment.gate_status == "accepted",
            )
            .order_by(KnowledgeRelationJudgment.id.asc())
        )
        judgments = result.scalars().unique().all()
        if not judgments:
            raise NoAcceptedJudgmentsError(
                "no status=accepted, gate_status=accepted judgments in owner/work scope"
            )

        items: list[FrozenSnapshotItem] = []
        ontology_profiles: set[str] = set()
        for judgment in judgments:
            candidate = judgment.candidate
            if candidate is None:
                raise InvalidSourceLineageError(
                    f"judgment {judgment.id} has no source candidate"
                )
            self._validate_candidate_lineage(
                judgment,
                candidate,
                owner_id=owner_id,
                novel_id=novel_id,
                domain_profile=domain_profile,
            )
            ontology_profiles.add(judgment.run.ontology_profile)
            evidence = await self._load_evidence_manifest(
                db,
                judgment=judgment,
                candidate=candidate,
                owner_id=owner_id,
                novel_id=novel_id,
            )
            items.append(self._freeze_item(judgment, candidate, evidence))

        if len(ontology_profiles) != 1:
            raise InvalidSourceLineageError(
                "accepted source set spans multiple ontology profiles"
            )
        ordered_items = tuple(sorted(items, key=lambda item: item.judgment_id))
        manifest_checksum = _content_hash(
            [item.checksum_payload() for item in ordered_items]
        )
        source_watermark = _content_hash(
            {
                "manifest_checksum": manifest_checksum,
                "source_versions": [
                    item.source_version_hash for item in ordered_items
                ],
            }
        )
        return FrozenSourceManifest(
            owner_id=owner_id,
            novel_id=novel_id,
            domain_profile=domain_profile,
            ontology_profile=ontology_profiles.pop(),
            items=ordered_items,
            manifest_checksum=manifest_checksum,
            source_watermark=source_watermark,
        )

    async def _assert_owned_novel(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
    ) -> None:
        owned = await db.scalar(
            select(Novel.id).where(Novel.id == novel_id, Novel.owner_id == owner_id)
        )
        if owned is None:
            raise InvalidSourceLineageError("novel is not owned by requested owner")

    async def _reject_candidate_only_rows(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        domain_profile: str,
    ) -> None:
        accepted_judgment = exists(
            select(KnowledgeRelationJudgment.id).where(
                KnowledgeRelationJudgment.relation_candidate_id
                == KnowledgeRelationCandidate.id,
                KnowledgeRelationJudgment.owner_id == owner_id,
                KnowledgeRelationJudgment.novel_id == novel_id,
                KnowledgeRelationJudgment.status == "accepted",
                KnowledgeRelationJudgment.gate_status == "accepted",
            )
        )
        candidate_id = await db.scalar(
            select(KnowledgeRelationCandidate.id)
            .where(
                KnowledgeRelationCandidate.owner_id == owner_id,
                KnowledgeRelationCandidate.novel_id == novel_id,
                KnowledgeRelationCandidate.domain_profile == domain_profile,
                KnowledgeRelationCandidate.status == "accepted",
                ~accepted_judgment,
            )
            .order_by(KnowledgeRelationCandidate.id.asc())
            .limit(1)
        )
        if candidate_id is not None:
            raise InvalidSourceLineageError(
                f"accepted candidate {candidate_id} has no doubly accepted judgment"
            )

    def _validate_candidate_lineage(
        self,
        judgment: KnowledgeRelationJudgment,
        candidate: KnowledgeRelationCandidate,
        *,
        owner_id: int,
        novel_id: int,
        domain_profile: str,
    ) -> None:
        if (
            judgment.owner_id != owner_id
            or judgment.novel_id != novel_id
            or candidate.owner_id != owner_id
            or candidate.novel_id != novel_id
            or candidate.run_id != judgment.run_id
        ):
            raise InvalidSourceLineageError(
                f"judgment {judgment.id} has cross-owner/work/run candidate lineage"
            )
        if candidate.domain_profile != domain_profile:
            raise InvalidSourceLineageError(
                f"judgment {judgment.id} is outside requested domain profile"
            )
        if candidate.status != "accepted":
            raise InvalidSourceLineageError(
                f"judgment {judgment.id} candidate is not accepted"
            )
        judgment_refs = set(judgment.evidence_refs or [])
        candidate_refs = set(candidate.evidence_refs or [])
        if not judgment_refs or not candidate_refs:
            raise InvalidSourceLineageError(
                f"judgment {judgment.id} has missing evidence refs"
            )
        if not judgment_refs <= candidate_refs:
            raise InvalidSourceLineageError(
                f"judgment {judgment.id} cites evidence outside candidate package"
            )

    async def _load_evidence_manifest(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
        candidate: KnowledgeRelationCandidate,
        owner_id: int,
        novel_id: int,
    ) -> tuple[dict[str, Any], ...]:
        ref_keys = sorted(
            set(judgment.evidence_refs or []) | set(candidate.evidence_refs or [])
        )
        result = await db.execute(
            select(KnowledgeEvidenceRef)
            .where(
                KnowledgeEvidenceRef.run_id == judgment.run_id,
                KnowledgeEvidenceRef.ref_key.in_(ref_keys),
            )
            .order_by(KnowledgeEvidenceRef.ref_key.asc())
        )
        refs = result.scalars().all()
        refs_by_key = {ref.ref_key: ref for ref in refs}
        missing = [ref_key for ref_key in ref_keys if ref_key not in refs_by_key]
        if missing:
            raise InvalidSourceLineageError(
                f"judgment {judgment.id} has missing evidence rows: {missing}"
            )

        manifest: list[dict[str, Any]] = []
        for ref_key in ref_keys:
            ref = refs_by_key[ref_key]
            if ref.owner_id != owner_id or ref.novel_id != novel_id:
                raise InvalidSourceLineageError(
                    f"evidence {ref.ref_key} is outside owner/work scope"
                )
            if ref.source_type == "accepted_relation" or ref.accepted_relation_id:
                raise InvalidSourceLineageError(
                    f"evidence {ref.ref_key} is a projected graph row"
                )
            source = await self._load_source_content(
                db,
                ref=ref,
                novel_id=novel_id,
            )
            payload = {
                "source_evidence_id": ref.id,
                "ref_key": ref.ref_key,
                "source_type": ref.source_type,
                "text_chunk_id": ref.text_chunk_id,
                "chapter_id": ref.chapter_id,
                "source_locator": ref.source_locator or {},
                "excerpt": ref.excerpt,
                "char_start": ref.char_start,
                "char_end": ref.char_end,
                "metadata": ref.metadata_json or {},
                "source_content": source["content"],
                "source_content_hash": _content_hash(source["content"]),
                "content_hash": "",
                "source_updated_at": source["updated_at"],
                "evidence_updated_at": ref.updated_at,
            }
            payload["content_hash"] = _content_hash(
                {key: value for key, value in payload.items() if key != "content_hash"}
            )
            manifest.append(payload)
        return tuple(manifest)

    async def _load_source_content(
        self,
        db: AsyncSession,
        *,
        ref: KnowledgeEvidenceRef,
        novel_id: int,
    ) -> dict[str, Any]:
        if ref.source_type == "text_chunk" and ref.text_chunk_id is not None:
            row = (
                await db.execute(
                    select(
                        TextChunk.content,
                        TextChunk.updated_at,
                        TextChunk.novel_id,
                        TextChunk.chapter_id,
                    ).where(TextChunk.id == ref.text_chunk_id)
                )
            ).one_or_none()
            if row is None or row.novel_id != novel_id:
                raise InvalidSourceLineageError(
                    f"evidence {ref.ref_key} has missing/out-of-work text chunk"
                )
            if ref.chapter_id is not None and row.chapter_id != ref.chapter_id:
                raise InvalidSourceLineageError(
                    f"evidence {ref.ref_key} chapter does not match text chunk"
                )
            return {"content": row.content, "updated_at": row.updated_at}
        if ref.source_type == "chapter" and ref.chapter_id is not None:
            row = (
                await db.execute(
                    select(
                        Chapter.content,
                        Chapter.updated_at,
                        Chapter.novel_id,
                    ).where(Chapter.id == ref.chapter_id)
                )
            ).one_or_none()
            if row is None or row.novel_id != novel_id:
                raise InvalidSourceLineageError(
                    f"evidence {ref.ref_key} has missing/out-of-work chapter"
                )
            return {"content": row.content, "updated_at": row.updated_at}
        raise InvalidSourceLineageError(
            f"evidence {ref.ref_key} has unsupported or missing source lineage"
        )

    def _freeze_item(
        self,
        judgment: KnowledgeRelationJudgment,
        candidate: KnowledgeRelationCandidate,
        evidence: tuple[dict[str, Any], ...],
    ) -> FrozenSnapshotItem:
        judgment_payload = {
            "relation_candidate_id": judgment.relation_candidate_id,
            "prompt_version": judgment.prompt_version,
            "model_name": judgment.model_name,
            "relation_type": judgment.relation_type,
            "confidence": judgment.confidence,
            "evidence_refs": sorted(judgment.evidence_refs or []),
            "risk_flags": sorted(judgment.risk_flags or []),
            "structured_output": judgment.structured_output or {},
            "status": judgment.status,
            "gate_status": judgment.gate_status,
        }
        candidate_payload = {
            "domain_profile": candidate.domain_profile,
            "relation_type": candidate.relation_type,
            "source_kind": candidate.source_kind,
            "source_id": candidate.source_id,
            "target_kind": candidate.target_kind,
            "target_id": candidate.target_id,
            "recall_signals": candidate.recall_signals or {},
            "package_snapshot": candidate.package_snapshot or {},
            "evidence_refs": sorted(candidate.evidence_refs or []),
            "status": candidate.status,
        }
        judgment_hash = _content_hash(judgment_payload)
        candidate_hash = _content_hash(candidate_payload)
        evidence_hash = _content_hash(
            [entry["content_hash"] for entry in evidence]
        )
        item_hash = _content_hash(
            {
                "judgment_id": judgment.id,
                "candidate_id": candidate.id,
                "judgment_content_hash": judgment_hash,
                "candidate_content_hash": candidate_hash,
                "evidence_content_hash": evidence_hash,
            }
        )
        source_version_hash = _content_hash(
            {
                "judgment_id": judgment.id,
                "candidate_id": candidate.id,
                "judgment_content_hash": judgment_hash,
                "candidate_content_hash": candidate_hash,
                "evidence_content_hash": evidence_hash,
            }
        )
        persisted_evidence = tuple(
            {
                key: value
                for key, value in entry.items()
                if key not in {"evidence_updated_at", "source_updated_at"}
            }
            for entry in evidence
        )
        return FrozenSnapshotItem(
            judgment_id=judgment.id,
            candidate_id=candidate.id,
            judgment_content_hash=judgment_hash,
            candidate_content_hash=candidate_hash,
            evidence_content_hash=evidence_hash,
            item_content_hash=item_hash,
            evidence_manifest=persisted_evidence,
            source_version_hash=source_version_hash,
        )

    async def _find_existing(
        self,
        db: AsyncSession,
        manifest: FrozenSourceManifest,
    ) -> NarrativeSourceSnapshot | None:
        return await db.scalar(
            select(NarrativeSourceSnapshot)
            .where(
                NarrativeSourceSnapshot.owner_id == manifest.owner_id,
                NarrativeSourceSnapshot.novel_id == manifest.novel_id,
                NarrativeSourceSnapshot.domain_profile == manifest.domain_profile,
                NarrativeSourceSnapshot.manifest_checksum
                == manifest.manifest_checksum,
                NarrativeSourceSnapshot.source_watermark == manifest.source_watermark,
            )
            .order_by(NarrativeSourceSnapshot.id.asc())
            .limit(1)
        )

    def _assert_stable(
        self,
        before: FrozenSourceManifest,
        after: FrozenSourceManifest,
    ) -> None:
        if (
            before.manifest_checksum != after.manifest_checksum
            or before.source_watermark != after.source_watermark
        ):
            raise MovingSourceInputsError(
                "accepted judgments, candidates, or evidence changed during snapshot"
            )


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported hash payload type: {type(value).__name__}")


def _content_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


source_snapshot_service = SourceSnapshotService()
