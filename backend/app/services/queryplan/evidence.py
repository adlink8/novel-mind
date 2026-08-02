"""Leaf EvidenceRef materialization and immutable Frozen Manifest freeze.

Phase 26-03 / REQ-QP-03 (D-07, D-08, D-09, D-12, D-14).

- Every candidate resolves to a leaf/raw chapter, Unicode offsets and a content
  hash re-sliced from the frozen snapshot; stale hashes reject (D-07).
- The manifest freezes before any answer generation; citations may only use
  leaf/raw ``EvidenceRef`` keys, never summaries, scores, routing metadata or
  chat text (D-08).
- No uncited factual assertion: an evidence-less answer abstains and records
  omitted / fallback entries (D-09).
- The frozen manifest is immutable and content-addressed: replay is by checksum
  and any mutation of text / hash / offset / owner / spoiler / version fails
  closed. No NM promotion / active-pointer / consumer cutover write exists
  (D-14).

This module is pure: it re-slices frozen chapter text using Python ``str``
indices (Unicode code points) and never touches the database or the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.services.queryplan.adapters import (
    SourceSnapshot,
    chapter_content_hash,
)
from app.services.queryplan.fusion import FusionResult
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    CutoffMode,
    EvidenceRef,
    QueryPlan,
    canonical_json,
    sha256_hex,
)

MANIFEST_SCHEMA_VERSION = "queryplan.manifest.v1"
MANIFEST_HASH_COMPONENT = "queryplan:manifest:v1"


class EvidenceError(ValueError):
    """Fail-closed evidence / manifest error carrying a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManifestEvidenceEntry:
    """One materialized leaf/raw EvidenceRef inside a frozen manifest."""

    evidence_key: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    source_snapshot_hash: str
    excerpt: str

    def canonical_dict(self) -> dict:
        return {
            "evidence_key": self.evidence_key,
            "chapter_id": self.chapter_id,
            "chapter_number": self.chapter_number,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "content_hash": self.content_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class OmittedEntry:
    """One omitted / fallback record; never evidence (D-05/D-09/D-15)."""

    kind: str
    reason: str
    provenance: str
    count: int
    dimension: str | None = None
    status: str | None = None
    chapter_number: int | None = None

    def canonical_dict(self) -> dict:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "provenance": self.provenance,
            "count": self.count,
            "dimension": self.dimension,
            "status": self.status,
            "chapter_number": self.chapter_number,
        }


def evidence_key(ref: EvidenceRef) -> str:
    """Deterministic leaf-only allowlist key.

    Composed only of leaf fields (chapter + Unicode offsets + content hash);
    a summary, score, routing id or chat-text id can never produce this shape.
    """
    return f"qp:{ref.chapter_id}:{ref.source_start}:{ref.source_end}:{ref.content_hash}"


def effective_through_chapter(plan: QueryPlan, source: SourceSnapshot) -> int:
    """Effective spoiler cutoff for the plan/snapshot pair (D-12)."""
    if plan.spoiler_cutoff.mode == CutoffMode.WHOLE_BOOK:
        return max((int(c.chapter_number) for c in source.chapters), default=10**9)
    return int(plan.spoiler_cutoff.through_chapter)


def materialize_evidence_ref(
    ref: EvidenceRef,
    *,
    source: SourceSnapshot,
    through_chapter: int,
    cutoff_mode: CutoffMode = CutoffMode.READING_PROGRESS,
) -> ManifestEvidenceEntry:
    """Re-slice the frozen chapter as authority; every mismatch fails closed.

    Raises ``EvidenceError`` when the snapshot lineage, chapter number, chapter
    integrity, spoiler cutoff, Unicode offset bounds or the slice content hash
    do not match the frozen snapshot (D-07).
    """
    if ref.source_snapshot_hash != source.snapshot_hash:
        raise EvidenceError(
            "stale_snapshot_lineage",
            "evidence ref escapes the frozen snapshot lineage "
            "(owner/novel/version/snapshot boundary)",
        )

    chapter = next(
        (c for c in source.chapters if c.chapter_id == ref.chapter_id), None
    )
    if chapter is None:
        raise EvidenceError(
            "chapter_missing",
            f"chapter {ref.chapter_id} is absent from the frozen snapshot",
        )
    if chapter.chapter_number != ref.chapter_number:
        raise EvidenceError(
            "chapter_number_mismatch",
            f"chapter {ref.chapter_id} number {chapter.chapter_number} "
            f"does not match ref chapter_number {ref.chapter_number}",
        )
    if chapter_content_hash(chapter.content) != chapter.content_hash:
        raise EvidenceError(
            "chapter_hash_mismatch",
            "frozen chapter content does not match its recorded content hash",
        )
    if (
        cutoff_mode == CutoffMode.READING_PROGRESS
        and chapter.chapter_number > through_chapter
    ):
        raise EvidenceError(
            "beyond_cutoff",
            f"chapter {ref.chapter_number} exceeds the spoiler cutoff "
            f"{through_chapter}",
        )

    content = chapter.content
    if (
        ref.source_start < 0
        or ref.source_end > len(content)
        or ref.source_end <= ref.source_start
    ):
        raise EvidenceError(
            "invalid_offsets",
            f"offsets [{ref.source_start},{ref.source_end}) are not a valid "
            "half-open range inside the frozen chapter",
        )
    excerpt = content[ref.source_start : ref.source_end]
    if chapter_content_hash(excerpt) != ref.content_hash:
        raise EvidenceError(
            "stale_content_hash",
            "evidence content hash does not match the exact frozen chapter "
            "code-point slice",
        )

    return ManifestEvidenceEntry(
        evidence_key=evidence_key(ref),
        chapter_id=ref.chapter_id,
        chapter_number=ref.chapter_number,
        source_start=ref.source_start,
        source_end=ref.source_end,
        content_hash=ref.content_hash,
        source_snapshot_hash=ref.source_snapshot_hash,
        excerpt=excerpt,
    )


def manifest_checksum(payload: dict) -> str:
    """Content address of the canonical manifest payload (checksum excluded)."""
    return sha256_hex(MANIFEST_HASH_COMPONENT, canonical_json(payload))


@dataclass(frozen=True)
class FrozenManifest:
    """Immutable, content-addressed retrieval manifest (D-08/D-14)."""

    schema_version: str
    manifest_id: str
    owner_id: int
    novel_id: int
    version_id: int
    through_chapter: int
    cutoff_mode: str
    full_book_authorized: bool
    snapshot_hash: str
    plan_trace_id: str
    plan_hash: str
    evidence: tuple[ManifestEvidenceEntry, ...]
    omitted: tuple[OmittedEntry, ...]
    manifest_checksum: str

    def allowed_evidence_ids(self) -> set[str]:
        """The allowlist the cited-answer gateway accepts (D-08)."""
        return {entry.evidence_key for entry in self.evidence}

    def canonical_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "owner_id": self.owner_id,
            "novel_id": self.novel_id,
            "version_id": self.version_id,
            "through_chapter": self.through_chapter,
            "cutoff_mode": self.cutoff_mode,
            "full_book_authorized": self.full_book_authorized,
            "snapshot_hash": self.snapshot_hash,
            "plan_trace_id": self.plan_trace_id,
            "plan_hash": self.plan_hash,
            "evidence": [entry.canonical_dict() for entry in self.evidence],
            "omitted": [entry.canonical_dict() for entry in self.omitted],
        }


def freeze_manifest(
    *,
    plan: QueryPlan,
    source: SourceSnapshot,
    evidence: Sequence[ManifestEvidenceEntry],
    omitted: Sequence[OmittedEntry],
) -> FrozenManifest:
    """Freeze a deterministic, content-addressed manifest before generation."""
    draft = FrozenManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        manifest_id="",
        owner_id=int(source.owner_id),
        novel_id=int(source.novel_id),
        version_id=int(source.version_id),
        through_chapter=effective_through_chapter(plan, source),
        cutoff_mode=plan.spoiler_cutoff.mode.value,
        full_book_authorized=bool(plan.spoiler_cutoff.full_book_authorized),
        snapshot_hash=source.snapshot_hash,
        plan_trace_id=plan.trace.trace_id,
        plan_hash=plan.trace.canonical_payload_hash,
        evidence=tuple(evidence),
        omitted=tuple(omitted),
        manifest_checksum="",
    )
    checksum = manifest_checksum(draft.canonical_payload())
    return FrozenManifest(
        schema_version=draft.schema_version,
        manifest_id=checksum,
        owner_id=draft.owner_id,
        novel_id=draft.novel_id,
        version_id=draft.version_id,
        through_chapter=draft.through_chapter,
        cutoff_mode=draft.cutoff_mode,
        full_book_authorized=draft.full_book_authorized,
        snapshot_hash=draft.snapshot_hash,
        plan_trace_id=draft.plan_trace_id,
        plan_hash=draft.plan_hash,
        evidence=draft.evidence,
        omitted=draft.omitted,
        manifest_checksum=checksum,
    )


def verify_manifest(manifest: FrozenManifest) -> None:
    """Recompute the content address; any mutation fails closed (D-08)."""
    if manifest.manifest_id != manifest.manifest_checksum:
        raise EvidenceError(
            "manifest_mutated",
            "manifest id drift: content address does not match the stored checksum",
        )
    recomputed = manifest_checksum(manifest.canonical_payload())
    if recomputed != manifest.manifest_checksum:
        raise EvidenceError(
            "manifest_mutated",
            "frozen manifest checksum mismatch: text/hash/offset/owner/spoiler/"
            "version drift detected",
        )


def build_omitted_records(fused: FusionResult) -> tuple[OmittedEntry, ...]:
    """Record every omitted / fallback reason; never fabricate evidence (D-05/D-09)."""
    records: list[OmittedEntry] = []
    for result in fused.dimension_results:
        if result.status != AvailabilityStatus.AVAILABLE:
            records.append(
                OmittedEntry(
                    kind="dimension",
                    reason=result.reason,
                    provenance=result.provenance,
                    count=1,
                    dimension=result.dimension.value,
                    status=result.status.value,
                )
            )
    for candidate in fused.candidate_recall:
        records.append(
            OmittedEntry(
                kind="heuristic_candidate",
                reason="candidate_recall_only_not_evidence",
                provenance="deterministic_heuristic_v1",
                count=1,
                dimension=candidate.dimension.value,
                chapter_number=candidate.chapter_number,
            )
        )
    if fused.exceeded_budget and fused.budget is not None:
        records.append(
            OmittedEntry(
                kind="evidence_budget",
                reason="budget_truncation",
                provenance="fusion_v1",
                count=max(0, fused.evidence_count - len(fused.fused_evidence)),
            )
        )
    return tuple(records)
