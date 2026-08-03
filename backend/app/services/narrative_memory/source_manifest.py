"""Frozen source manifest and DB recomputation for the whole-book builder.

Phase 28-02 (REQ-NM-01/05, D-04/D-05/D-08): the frozen source snapshot drives
chapter execution, and the manifest is DB-recomputable. Recomputing from the
current PostgreSQL authority rows must produce a byte-identical checksum while
the source is unchanged; any drift between the frozen snapshot and the current
chapters/evidence fails closed (``source_snapshot_drift``) instead of silently
reusing stale evidence. A drifted chapter is blocked — never re-run against
stale inputs — and never triggers an unconditional whole-book restart (D-03).

The per-chapter content/evidence hashes are compressed payload digests for
context compaction and identity only; they are not retrieval-index inputs and
not ``EvidenceRef`` authority (D-08).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.novel import Chapter
from app.services.chunking.manifests import content_hash

SOURCE_MANIFEST_SCHEMA_VERSION = "source-manifest.v1"


class SourceManifestError(ValueError):
    """Fail-closed error while computing or recomputing a source manifest."""


@dataclass(frozen=True)
class ChapterSourceDigest:
    """Compressed per-chapter digest: content hash + evidence leaf hashes."""

    chapter_id: int
    chapter_number: int
    content_hash: str
    evidence_hashes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "chapter_number": self.chapter_number,
            "content_hash": self.content_hash,
            "evidence_hashes": list(self.evidence_hashes),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChapterSourceDigest":
        return cls(
            chapter_id=int(value["chapter_id"]),
            chapter_number=int(value["chapter_number"]),
            content_hash=str(value["content_hash"]),
            evidence_hashes=tuple(str(h) for h in value.get("evidence_hashes") or ()),
        )


@dataclass(frozen=True)
class SourceManifest:
    """Frozen snapshot of the source authority that drives chapter execution."""

    schema_version: str
    novel_id: int
    source_snapshot_hash: str
    hierarchy_build_id: str
    hierarchy_checksum: str
    eligibility_report_checksum: str
    chapters: tuple[ChapterSourceDigest, ...]
    manifest_checksum: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "novel_id": self.novel_id,
            "source_snapshot_hash": self.source_snapshot_hash,
            "hierarchy_build_id": self.hierarchy_build_id,
            "hierarchy_checksum": self.hierarchy_checksum,
            "eligibility_report_checksum": self.eligibility_report_checksum,
            "chapters": [chapter.as_dict() for chapter in self.chapters],
            "manifest_checksum": self.manifest_checksum,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceManifest":
        return cls(
            schema_version=str(value["schema_version"]),
            novel_id=int(value["novel_id"]),
            source_snapshot_hash=str(value["source_snapshot_hash"]),
            hierarchy_build_id=str(value["hierarchy_build_id"]),
            hierarchy_checksum=str(value["hierarchy_checksum"]),
            eligibility_report_checksum=str(value["eligibility_report_checksum"]),
            chapters=tuple(
                ChapterSourceDigest.from_dict(item)
                for item in value.get("chapters") or ()
            ),
            manifest_checksum=str(value["manifest_checksum"]),
        )


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _sha(component: str, value: Any) -> str:
    encoded = f"narrative-memory.v1:{component}\n{_canonical(value)}"
    return sha256(encoded.encode("utf-8")).hexdigest()


def _assemble_manifest(
    *,
    novel_id: int,
    source_snapshot_hash: str,
    hierarchy_build_id: str,
    hierarchy_checksum: str,
    eligibility_report_checksum: str,
    digests: Sequence[ChapterSourceDigest],
) -> SourceManifest:
    ordered = tuple(sorted(digests, key=lambda d: (d.chapter_number, d.chapter_id)))
    body = {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "novel_id": novel_id,
        "source_snapshot_hash": source_snapshot_hash,
        "hierarchy_build_id": hierarchy_build_id,
        "hierarchy_checksum": hierarchy_checksum,
        "eligibility_report_checksum": eligibility_report_checksum,
        "chapters": [chapter.as_dict() for chapter in ordered],
    }
    checksum = _sha("source-manifest", body)
    return SourceManifest(
        schema_version=SOURCE_MANIFEST_SCHEMA_VERSION,
        novel_id=novel_id,
        source_snapshot_hash=source_snapshot_hash,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        eligibility_report_checksum=eligibility_report_checksum,
        chapters=ordered,
        manifest_checksum=checksum,
    )


async def _load_evidence_hashes(
    session: AsyncSession,
    *,
    hierarchy_build_id: str,
    novel_id: int,
    chapter_id: int,
) -> tuple[str, ...]:
    if not hierarchy_build_id:
        return ()
    rows = (
        await session.scalars(
            select(ChunkHierarchyNode)
            .where(
                ChunkHierarchyNode.build_id == hierarchy_build_id,
                ChunkHierarchyNode.novel_id == novel_id,
                ChunkHierarchyNode.chapter_id == chapter_id,
                ChunkHierarchyNode.level == "evidence",
            )
            .order_by(
                ChunkHierarchyNode.order_index,
                ChunkHierarchyNode.source_start,
                ChunkHierarchyNode.node_id,
            )
        )
    ).all()
    return tuple(sorted(row.content_hash for row in rows))


async def compute_source_manifest(
    session: AsyncSession,
    *,
    version: NarrativeMemoryVersion,
    chapters: Sequence[Chapter],
) -> SourceManifest:
    """Build the full frozen manifest from an already-resolved chapter set."""
    ordered = tuple(sorted(chapters, key=lambda ch: (ch.chapter_number, ch.id)))
    # Chapter.content is a deferred column; load it eagerly for the digest.
    content_by_id: dict[int, str] = {}
    if ordered:
        rows = (
            await session.execute(
                select(Chapter.id, Chapter.content)
                .where(Chapter.id.in_(tuple(ch.id for ch in ordered)))
                .order_by(Chapter.id)
            )
        ).all()
        content_by_id = {int(row[0]): (row[1] or "") for row in rows}
    digests: list[ChapterSourceDigest] = []
    for chapter in ordered:
        text = content_by_id.get(int(chapter.id), "")
        evidence_hashes = await _load_evidence_hashes(
            session,
            hierarchy_build_id=version.hierarchy_build_id,
            novel_id=int(version.novel_id),
            chapter_id=int(chapter.id),
        )
        digests.append(
            ChapterSourceDigest(
                chapter_id=int(chapter.id),
                chapter_number=int(chapter.chapter_number),
                content_hash=content_hash(text),
                evidence_hashes=evidence_hashes,
            )
        )
    return _assemble_manifest(
        novel_id=int(version.novel_id),
        source_snapshot_hash=version.source_snapshot_hash,
        hierarchy_build_id=version.hierarchy_build_id,
        hierarchy_checksum=version.hierarchy_checksum,
        eligibility_report_checksum=version.eligibility_report_checksum,
        digests=digests,
    )


async def recompute_source_manifest(
    session: AsyncSession,
    *,
    version: NarrativeMemoryVersion,
) -> SourceManifest:
    """Recompute the source manifest from current DB authority rows."""
    chapters = tuple(
        (
            await session.scalars(
                select(Chapter)
                .where(Chapter.novel_id == version.novel_id)
                .options(undefer(Chapter.content))
                .order_by(Chapter.chapter_number.asc(), Chapter.id.asc())
            )
        ).all()
    )
    return await compute_source_manifest(session, version=version, chapters=chapters)


def frozen_manifest_from_progress(
    progress: dict[str, Any] | None,
) -> SourceManifest | None:
    """Parse the frozen manifest stored on the run progress JSONB."""
    payload = (progress or {}).get("source_manifest")
    if not payload:
        return None
    try:
        return SourceManifest.from_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceManifestError(f"stored source manifest is unreadable: {exc}") from exc


def store_frozen_manifest(
    progress: dict[str, Any], manifest: SourceManifest
) -> dict[str, Any]:
    """Return an updated progress mapping carrying the frozen manifest."""
    progress = dict(progress or {})
    progress["source_manifest"] = manifest.as_dict()
    progress["source_manifest_checksum"] = manifest.manifest_checksum
    return progress


def detect_chapter_drift(
    frozen: SourceManifest,
    recomputed: SourceManifest,
) -> dict[int, str]:
    """Per-chapter drift reasons keyed by chapter number (empty = clean)."""
    drift: dict[int, str] = {}
    frozen_by_number = {d.chapter_number: d for d in frozen.chapters}
    recomputed_by_number = {d.chapter_number: d for d in recomputed.chapters}
    for number, fresh in recomputed_by_number.items():
        original = frozen_by_number.get(number)
        if original is None:
            drift[number] = "chapter_added_after_freeze"
            continue
        if original.content_hash != fresh.content_hash:
            drift[number] = "chapter_content_drift"
            continue
        if original.evidence_hashes != fresh.evidence_hashes:
            drift[number] = "chapter_evidence_drift"
    return drift


def source_manifest_drift_reasons(
    frozen: SourceManifest,
    recomputed: SourceManifest,
) -> list[str]:
    """Whole-manifest drift verdict. Fail closed on any difference."""
    reasons: list[str] = []
    if frozen.manifest_checksum != recomputed.manifest_checksum:
        reasons.append("source_manifest_drift")
    for number, reason in sorted(detect_chapter_drift(frozen, recomputed).items()):
        reasons.append(f"chapter:{number}:{reason}")
    return reasons
