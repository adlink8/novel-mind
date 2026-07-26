"""Exact selection validation and immutable spoiler-safe context manifests.

Server authority: client selection text/offsets/hashes are claims. Visible
context is frozen at send time; retry reuses the original checksum-addressed
manifest rather than rebuilding under a newer reading progress.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.novel import Chapter, Novel
from app.schemas.reader_chat import SelectionCoordinate
from app.services.reader_chat.retrieval import (
    RelationshipObservationReader,
    SourceStatus,
    retrieve_visible_evidence,
)
from app.services.timeline.query import resolve_chapter_cutoff

MAX_SELECTION_CODE_POINTS = 8000
SELECTION_EVIDENCE_KEY = "selection:primary"
CHAPTER_EVIDENCE_KEY = "chapter:primary"

# Multi-chapter range context: total excerpt budget is a bounded multiple (2x)
# of the single-chapter budget, split evenly across chapters in the range.
MAX_RANGE_CONTEXT_CODE_POINTS = 2 * MAX_SELECTION_CODE_POINTS
CHAPTER_RANGE_ANCHOR_KIND = "chapter_range"


class SelectionValidationError(ValueError):
    """Stable selection rejection with machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedSelection:
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    selection_text: str
    selection_text_hash: str
    chapter_content_hash: str
    hierarchy_build_id: str
    hierarchy_checksum: str


@dataclass(frozen=True)
class ProgressSnapshot:
    chapter_id: int | None
    cutoff_chapter_number: int
    timeline_full_book: bool
    full_book: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "cutoff_chapter_number": self.cutoff_chapter_number,
            "timeline_full_book": self.timeline_full_book,
            "full_book": self.full_book,
        }


@dataclass(frozen=True)
class ValidatedChapterSegment:
    """One visible chapter's budgeted excerpt inside a validated range."""

    chapter_id: int
    chapter_number: int
    excerpt: str
    excerpt_hash: str
    chapter_content_hash: str


@dataclass(frozen=True)
class ValidatedChapterRange:
    """Cutoff-narrowed chapter interval with per-chapter budgeted excerpts."""

    chapter_start: int
    chapter_end: int  # effective end after cutoff narrowing
    requested_chapter_end: int
    segments: tuple[ValidatedChapterSegment, ...]
    hierarchy_build_id: str
    hierarchy_checksum: str
    progress: ProgressSnapshot

    def anchor_dict(self) -> dict[str, Any]:
        return {
            "kind": CHAPTER_RANGE_ANCHOR_KIND,
            "chapter_start": self.chapter_start,
            "chapter_end": self.chapter_end,
        }


def narrow_chapter_range(
    chapter_start: int,
    chapter_end: int,
    *,
    cutoff_chapter_number: int,
    full_book: bool,
) -> int:
    """Intersect the requested range with the spoiler cutoff.

    Returns the effective chapter_end. full_book skips truncation entirely;
    a range starting beyond the cutoff is a stable 422 (chapter_beyond_cutoff).
    """

    if chapter_start < 1 or chapter_end < chapter_start:
        raise SelectionValidationError(
            "invalid_chapter_range",
            "chapter_range requires 1 <= chapter_start <= chapter_end",
        )
    if full_book:
        return chapter_end
    if chapter_start > cutoff_chapter_number:
        raise SelectionValidationError(
            "chapter_beyond_cutoff",
            "chapter range starts beyond the visible reading cutoff",
        )
    return min(chapter_end, cutoff_chapter_number)


def chapter_range_budget(chapter_count: int) -> int:
    """Per-chapter excerpt budget: even split of the bounded total.

    Never exceeds the single-chapter budget per chapter and keeps the sum
    within MAX_RANGE_CONTEXT_CODE_POINTS (no unbounded concatenation).
    """

    if chapter_count < 1:
        raise SelectionValidationError(
            "invalid_chapter_range", "chapter range must contain at least one chapter"
        )
    per_chapter = MAX_RANGE_CONTEXT_CODE_POINTS // chapter_count
    return min(MAX_SELECTION_CODE_POINTS, max(per_chapter, 1))


@dataclass(frozen=True)
class ContextEvidenceEntry:
    evidence_key: str
    source_type: str
    source_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    excerpt: str
    sort_order: int
    version_lineage: dict[str, Any] = field(default_factory=dict)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "evidence_key": self.evidence_key,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "chapter_id": self.chapter_id,
            "chapter_number": self.chapter_number,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "content_hash": self.content_hash,
            "excerpt": self.excerpt,
            "sort_order": self.sort_order,
            "version_lineage": self.version_lineage,
        }


@dataclass(frozen=True)
class ContextManifest:
    reading_progress_snapshot: dict[str, Any]
    full_book: bool
    cutoff_chapter_number: int
    analysis_version_id: int | None
    hierarchy_build_id: str
    hierarchy_checksum: str
    evidence: tuple[ContextEvidenceEntry, ...]
    omitted_evidence_counts: dict[str, int]
    prompt_inputs: dict[str, Any]
    source_status: dict[str, str]
    manifest_checksum: str

    def allowed_evidence_ids(self) -> set[str]:
        return {entry.evidence_key for entry in self.evidence}

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "reading_progress_snapshot": self.reading_progress_snapshot,
            "full_book": self.full_book,
            "cutoff_chapter_number": self.cutoff_chapter_number,
            "analysis_version_id": self.analysis_version_id,
            "hierarchy_build_id": self.hierarchy_build_id,
            "hierarchy_checksum": self.hierarchy_checksum,
            "evidence": [e.canonical_dict() for e in self.evidence],
            "omitted_evidence_counts": self.omitted_evidence_counts,
            "prompt_inputs": self.prompt_inputs,
            "source_status": self.source_status,
        }


def code_point_len(text: str) -> int:
    """Python 3 str indices are Unicode code points."""

    return len(text)


def code_point_slice(text: str, start: int, end: int) -> str:
    """Half-open code-point slice matching persisted Chapter.content coordinates."""

    if start < 0 or end < start:
        raise SelectionValidationError(
            "invalid_bounds", "source offsets must form a non-empty half-open range"
        )
    length = code_point_len(text)
    if end > length:
        raise SelectionValidationError(
            "invalid_bounds", "source_end exceeds chapter content length"
        )
    return text[start:end]


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_manifest_checksum(payload: dict[str, Any]) -> str:
    """Checksum of the frozen canonical graph excluding the checksum field itself."""

    body = {k: v for k, v in payload.items() if k != "manifest_checksum"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


async def resolve_progress_snapshot(
    session: AsyncSession, novel: Novel
) -> ProgressSnapshot:
    """Persistable reading snapshot. Full-book only when timeline_full_book is true."""

    progress = dict(novel.reading_progress or {})
    timeline_full_book = bool(progress.get("timeline_full_book", False))
    full_book = timeline_full_book
    chapter_id_raw = progress.get("chapter_id")
    chapter_id: int | None
    try:
        chapter_id = int(chapter_id_raw) if chapter_id_raw is not None else None
    except (TypeError, ValueError):
        chapter_id = None

    cutoff = await resolve_chapter_cutoff(session, novel)
    if cutoff is None:
        # Novel with zero chapters: fail closed to chapter 1 placeholder for storage CHECKs.
        cutoff = 1

    if full_book:
        max_chapter = await session.scalar(
            select(Chapter.chapter_number)
            .where(Chapter.novel_id == novel.id)
            .order_by(Chapter.chapter_number.desc())
            .limit(1)
        )
        if max_chapter is not None:
            cutoff = int(max_chapter)

    return ProgressSnapshot(
        chapter_id=chapter_id,
        cutoff_chapter_number=int(cutoff),
        timeline_full_book=timeline_full_book,
        full_book=full_book,
    )


async def validate_selection(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    selection: SelectionCoordinate,
    hierarchy_build_id: str | None = None,
    hierarchy_checksum: str | None = None,
) -> ValidatedSelection:
    """Re-slice Chapter.content as authority; reject stale/forged client claims."""

    if novel.owner_id != owner_id:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")

    chapter = await session.scalar(
        select(Chapter)
        .where(
            Chapter.id == selection.chapter_id,
            Chapter.novel_id == novel.id,
        )
        .options(undefer(Chapter.content))
    )
    if chapter is None:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")

    content = chapter.content or ""
    chapter_hash = content_sha256(content)
    if chapter_hash != selection.chapter_content_hash:
        raise SelectionValidationError(
            "stale_chapter",
            "chapter content hash does not match server authority",
        )

    if selection.source_start < 0 or selection.source_end <= selection.source_start:
        raise SelectionValidationError(
            "invalid_bounds", "source offsets must form a non-empty half-open range"
        )

    length = code_point_len(content)
    if selection.source_end > length:
        raise SelectionValidationError(
            "invalid_bounds", "source_end exceeds chapter content length"
        )

    exact = code_point_slice(content, selection.source_start, selection.source_end)
    if code_point_len(exact) == 0:
        raise SelectionValidationError("empty_selection", "selection is empty")
    if code_point_len(exact) > MAX_SELECTION_CODE_POINTS:
        raise SelectionValidationError(
            "oversized_selection",
            f"selection exceeds {MAX_SELECTION_CODE_POINTS} code points",
        )

    if exact != selection.selection_text:
        raise SelectionValidationError(
            "stale_selection",
            "selection_text does not match exact chapter code-point slice",
        )

    exact_hash = content_sha256(exact)
    if exact_hash != selection.selection_text_hash:
        raise SelectionValidationError(
            "stale_selection",
            "selection_text_hash does not match exact slice hash",
        )

    build_id = hierarchy_build_id or ""
    build_checksum = hierarchy_checksum or ""
    if not build_id or not build_checksum:
        from app.services.reader_chat.retrieval import resolve_active_hierarchy

        meta = await resolve_active_hierarchy(session, novel_id=novel.id)
        if meta is not None:
            build_id, build_checksum = meta
        else:
            build_id = build_id or "none"
            build_checksum = build_checksum or ("0" * 64)

    return ValidatedSelection(
        chapter_id=int(chapter.id),
        chapter_number=int(chapter.chapter_number),
        source_start=int(selection.source_start),
        source_end=int(selection.source_end),
        selection_text=exact,
        selection_text_hash=exact_hash,
        chapter_content_hash=chapter_hash,
        hierarchy_build_id=build_id,
        hierarchy_checksum=build_checksum,
    )


async def validate_chapter_context(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    chapter_id: int,
) -> ValidatedSelection:
    """Resolve an owned, visible chapter and derive context only from server text."""

    if novel.owner_id != owner_id:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")
    chapter = await session.scalar(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.novel_id == novel.id)
        .options(undefer(Chapter.content))
    )
    if chapter is None:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")

    progress = await resolve_progress_snapshot(session, novel)
    if (
        not progress.full_book
        and int(chapter.chapter_number) > progress.cutoff_chapter_number
    ):
        raise SelectionValidationError(
            "chapter_beyond_cutoff",
            "chapter is beyond the visible reading cutoff",
        )

    content = chapter.content or ""
    if not content:
        raise SelectionValidationError("empty_chapter", "chapter content is empty")
    excerpt = content[:MAX_SELECTION_CODE_POINTS]

    from app.services.reader_chat.retrieval import resolve_active_hierarchy

    meta = await resolve_active_hierarchy(session, novel_id=novel.id)
    build_id, build_checksum = meta or ("none", "0" * 64)
    return ValidatedSelection(
        chapter_id=int(chapter.id),
        chapter_number=int(chapter.chapter_number),
        source_start=0,
        source_end=len(excerpt),
        selection_text=excerpt,
        selection_text_hash=content_sha256(excerpt),
        chapter_content_hash=content_sha256(content),
        hierarchy_build_id=build_id,
        hierarchy_checksum=build_checksum,
    )


async def validate_chapter_range_context(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    chapter_start: int,
    chapter_end: int,
    progress: ProgressSnapshot | None = None,
) -> ValidatedChapterRange:
    """Resolve an owned, cutoff-narrowed chapter interval into budgeted excerpts.

    Chapter-number semantics (inclusive), matching the timeline API. The range
    is intersected with the reading cutoff; full_book skips truncation.
    """

    if novel.owner_id != owner_id:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")

    if progress is None:
        progress = await resolve_progress_snapshot(session, novel)

    effective_end = narrow_chapter_range(
        chapter_start,
        chapter_end,
        cutoff_chapter_number=progress.cutoff_chapter_number,
        full_book=progress.full_book,
    )

    chapters = list(
        (
            await session.scalars(
                select(Chapter)
                .where(
                    Chapter.novel_id == novel.id,
                    Chapter.chapter_number >= chapter_start,
                    Chapter.chapter_number <= effective_end,
                )
                .order_by(Chapter.chapter_number.asc())
                .options(undefer(Chapter.content))
            )
        ).all()
    )
    if not chapters:
        raise SelectionValidationError(
            "not_found", "no chapters found in range for owner scope"
        )

    budget = chapter_range_budget(len(chapters))
    segments: list[ValidatedChapterSegment] = []
    for chapter in chapters:
        content = chapter.content or ""
        if not content:
            continue
        excerpt = content[:budget]
        segments.append(
            ValidatedChapterSegment(
                chapter_id=int(chapter.id),
                chapter_number=int(chapter.chapter_number),
                excerpt=excerpt,
                excerpt_hash=content_sha256(excerpt),
                chapter_content_hash=content_sha256(content),
            )
        )
    if not segments:
        raise SelectionValidationError(
            "empty_chapter", "all chapters in range have empty content"
        )

    from app.services.reader_chat.retrieval import resolve_active_hierarchy

    meta = await resolve_active_hierarchy(session, novel_id=novel.id)
    build_id, build_checksum = meta or ("none", "0" * 64)
    return ValidatedChapterRange(
        chapter_start=int(chapter_start),
        chapter_end=int(effective_end),
        requested_chapter_end=int(chapter_end),
        segments=tuple(segments),
        hierarchy_build_id=build_id,
        hierarchy_checksum=build_checksum,
        progress=progress,
    )


def _dialogue_framing(
    prior_dialogue: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Prior dialogue is conversational framing only — never allowed evidence."""

    window: list[dict[str, Any]] = []
    for turn in prior_dialogue or []:
        window.append(
            {
                "role": str(turn.get("role", "")),
                "body_hash": content_sha256(str(turn.get("body", ""))),
                "sequence": turn.get("sequence"),
            }
        )
    return {
        "label": "CONVERSATIONAL_FRAMING_NOT_EVIDENCE",
        "is_evidence": False,
        "turns": window,
    }


async def assemble_context_manifest(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    selection: ValidatedSelection,
    question: str = "",
    prior_dialogue: list[dict[str, Any]] | None = None,
    relationship_reader: RelationshipObservationReader | None = None,
    client_evidence_keys: list[str] | None = None,
    max_evidence: int = 24,
    selection_bound: bool = True,
) -> ContextManifest:
    """Build one immutable, deterministic context graph for atomic persistence.

    Client-supplied evidence IDs are ignored (never trusted). Optional sources may
    be unavailable without inventing content or widening the spoiler scope.
    """

    if client_evidence_keys:
        # Explicit rejection surface for forged allowlists.
        raise SelectionValidationError(
            "forged_evidence_refs",
            "client evidence refs are not authoritative and are rejected",
        )

    progress = await resolve_progress_snapshot(session, novel)
    full_book = progress.full_book
    cutoff = None if full_book else progress.cutoff_chapter_number

    # Selection chapter itself must be visible under the frozen cutoff.
    if not full_book and selection.chapter_number > progress.cutoff_chapter_number:
        raise SelectionValidationError(
            "selection_beyond_cutoff",
            "selection chapter is beyond the visible reading cutoff",
        )

    retrieval = await retrieve_visible_evidence(
        session,
        novel=novel,
        owner_id=owner_id,
        selection_chapter_id=selection.chapter_id,
        selection_start=selection.source_start,
        selection_end=selection.source_end,
        cutoff_chapter=cutoff,
        full_book=full_book,
        relationship_reader=relationship_reader,
        max_evidence=max(0, max_evidence - 1),
    )

    hierarchy_build_id = retrieval.hierarchy_build_id or selection.hierarchy_build_id
    hierarchy_checksum = retrieval.hierarchy_checksum or selection.hierarchy_checksum

    primary_entry = ContextEvidenceEntry(
        evidence_key=SELECTION_EVIDENCE_KEY
        if selection_bound
        else CHAPTER_EVIDENCE_KEY,
        source_type="selection" if selection_bound else "hierarchy",
        source_id=(
            f"{selection.chapter_id}:{selection.source_start}:{selection.source_end}"
            if selection_bound
            else f"chapter:{selection.chapter_id}"
        ),
        chapter_id=selection.chapter_id,
        chapter_number=selection.chapter_number,
        source_start=selection.source_start,
        source_end=selection.source_end,
        content_hash=selection.selection_text_hash,
        excerpt=selection.selection_text,
        sort_order=0,
        version_lineage={
            "hierarchy_build_id": hierarchy_build_id,
            "hierarchy_checksum": hierarchy_checksum,
            "chapter_content_hash": selection.chapter_content_hash,
        },
    )

    evidence_entries: list[ContextEvidenceEntry] = [primary_entry]
    sort_order = 1
    for item in retrieval.items:
        # Defensive: never admit future-chapter evidence into the canonical graph.
        if not full_book and item.chapter_number > progress.cutoff_chapter_number:
            continue
        evidence_entries.append(
            ContextEvidenceEntry(
                evidence_key=item.evidence_key,
                source_type=item.source_type,
                source_id=item.source_id,
                chapter_id=item.chapter_id,
                chapter_number=item.chapter_number,
                source_start=item.source_start,
                source_end=item.source_end,
                content_hash=item.content_hash,
                excerpt=item.excerpt,
                sort_order=sort_order,
                version_lineage=dict(item.version_lineage),
            )
        )
        sort_order += 1

    prompt_inputs: dict[str, Any] = {
        "question_hash": content_sha256(question or ""),
        "dialogue_framing": _dialogue_framing(prior_dialogue),
        "allowed_evidence_ids": [e.evidence_key for e in evidence_entries],
        "context_mode": "selection" if selection_bound else "chapter",
    }

    source_status = dict(retrieval.source_status)
    source_status.setdefault(
        "selection" if selection_bound else "chapter", SourceStatus.OK
    )

    draft = ContextManifest(
        reading_progress_snapshot=progress.as_dict(),
        full_book=full_book,
        cutoff_chapter_number=progress.cutoff_chapter_number,
        analysis_version_id=retrieval.analysis_version_id,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        evidence=tuple(evidence_entries),
        omitted_evidence_counts=dict(retrieval.omitted_counts),
        prompt_inputs=prompt_inputs,
        source_status=source_status,
        manifest_checksum="",  # filled below
    )
    checksum = canonical_manifest_checksum(draft.canonical_payload())
    return ContextManifest(
        reading_progress_snapshot=draft.reading_progress_snapshot,
        full_book=draft.full_book,
        cutoff_chapter_number=draft.cutoff_chapter_number,
        analysis_version_id=draft.analysis_version_id,
        hierarchy_build_id=draft.hierarchy_build_id,
        hierarchy_checksum=draft.hierarchy_checksum,
        evidence=draft.evidence,
        omitted_evidence_counts=draft.omitted_evidence_counts,
        prompt_inputs=draft.prompt_inputs,
        source_status=draft.source_status,
        manifest_checksum=checksum,
    )


async def assemble_range_context_manifest(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    chapter_range: ValidatedChapterRange,
    question: str = "",
    prior_dialogue: list[dict[str, Any]] | None = None,
    relationship_reader: RelationshipObservationReader | None = None,
    client_evidence_keys: list[str] | None = None,
    max_evidence: int = 24,
) -> ContextManifest:
    """Build one immutable context graph for a structure-anchored chapter range.

    Per-chapter budgeted excerpts are the primary evidence; retrieval evidence
    is aggregated strictly inside the (already cutoff-narrowed) interval.
    """

    if client_evidence_keys:
        raise SelectionValidationError(
            "forged_evidence_refs",
            "client evidence refs are not authoritative and are rejected",
        )

    progress = chapter_range.progress
    full_book = progress.full_book
    segments = chapter_range.segments
    if not segments:
        raise SelectionValidationError(
            "empty_chapter", "chapter range has no visible content"
        )

    # Defensive re-check: the frozen range must already respect the cutoff.
    if not full_book and chapter_range.chapter_start > progress.cutoff_chapter_number:
        raise SelectionValidationError(
            "chapter_beyond_cutoff",
            "chapter range starts beyond the visible reading cutoff",
        )

    pivot = segments[0]
    retrieval = await retrieve_visible_evidence(
        session,
        novel=novel,
        owner_id=owner_id,
        selection_chapter_id=pivot.chapter_id,
        selection_start=0,
        selection_end=code_point_len(pivot.excerpt),
        # Bound retrieval to the effective range end; the range is already
        # intersected with the cutoff (or full_book allows the requested end).
        cutoff_chapter=chapter_range.chapter_end,
        full_book=False,
        relationship_reader=relationship_reader,
        max_evidence=max(0, max_evidence - len(segments)),
    )

    hierarchy_build_id = (
        retrieval.hierarchy_build_id or chapter_range.hierarchy_build_id
    )
    hierarchy_checksum = (
        retrieval.hierarchy_checksum or chapter_range.hierarchy_checksum
    )

    evidence_entries: list[ContextEvidenceEntry] = []
    for sort_order, segment in enumerate(segments):
        evidence_entries.append(
            ContextEvidenceEntry(
                evidence_key=f"chapter:{segment.chapter_id}",
                source_type="hierarchy",
                source_id=f"chapter:{segment.chapter_id}",
                chapter_id=segment.chapter_id,
                chapter_number=segment.chapter_number,
                source_start=0,
                source_end=code_point_len(segment.excerpt),
                content_hash=segment.excerpt_hash,
                excerpt=segment.excerpt,
                sort_order=sort_order,
                version_lineage={
                    "hierarchy_build_id": hierarchy_build_id,
                    "hierarchy_checksum": hierarchy_checksum,
                    "chapter_content_hash": segment.chapter_content_hash,
                },
            )
        )

    primary_keys = {entry.evidence_key for entry in evidence_entries}
    sort_order = len(evidence_entries)
    for item in retrieval.items:
        # Aggregate strictly inside the effective interval; never widen scope.
        if item.chapter_number < chapter_range.chapter_start:
            continue
        if item.chapter_number > chapter_range.chapter_end:
            continue
        if not full_book and item.chapter_number > progress.cutoff_chapter_number:
            continue
        if item.evidence_key in primary_keys:
            continue
        evidence_entries.append(
            ContextEvidenceEntry(
                evidence_key=item.evidence_key,
                source_type=item.source_type,
                source_id=item.source_id,
                chapter_id=item.chapter_id,
                chapter_number=item.chapter_number,
                source_start=item.source_start,
                source_end=item.source_end,
                content_hash=item.content_hash,
                excerpt=item.excerpt,
                sort_order=sort_order,
                version_lineage=dict(item.version_lineage),
            )
        )
        sort_order += 1

    prompt_inputs: dict[str, Any] = {
        "question_hash": content_sha256(question or ""),
        "dialogue_framing": _dialogue_framing(prior_dialogue),
        "allowed_evidence_ids": [e.evidence_key for e in evidence_entries],
        "context_mode": CHAPTER_RANGE_ANCHOR_KIND,
        "anchor": chapter_range.anchor_dict(),
    }

    source_status = dict(retrieval.source_status)
    source_status.setdefault(CHAPTER_RANGE_ANCHOR_KIND, SourceStatus.OK)

    draft = ContextManifest(
        reading_progress_snapshot=progress.as_dict(),
        full_book=full_book,
        cutoff_chapter_number=progress.cutoff_chapter_number,
        analysis_version_id=retrieval.analysis_version_id,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        evidence=tuple(evidence_entries),
        omitted_evidence_counts=dict(retrieval.omitted_counts),
        prompt_inputs=prompt_inputs,
        source_status=source_status,
        manifest_checksum="",  # filled below
    )
    checksum = canonical_manifest_checksum(draft.canonical_payload())
    return ContextManifest(
        reading_progress_snapshot=draft.reading_progress_snapshot,
        full_book=draft.full_book,
        cutoff_chapter_number=draft.cutoff_chapter_number,
        analysis_version_id=draft.analysis_version_id,
        hierarchy_build_id=draft.hierarchy_build_id,
        hierarchy_checksum=draft.hierarchy_checksum,
        evidence=draft.evidence,
        omitted_evidence_counts=draft.omitted_evidence_counts,
        prompt_inputs=draft.prompt_inputs,
        source_status=draft.source_status,
        manifest_checksum=checksum,
    )


def assert_retry_uses_original_checksum(
    stored_checksum: str, rebuilt_under_new_progress: ContextManifest
) -> None:
    """Retry policy helper: callers must keep the stored checksum, not rebuild."""

    if stored_checksum != rebuilt_under_new_progress.manifest_checksum:
        # Document intentional divergence: rebuild under new progress is forbidden for retry.
        return
    raise AssertionError(
        "retry must not depend on rebuilt manifests matching by chance"
    )


def freeze_manifest_from_stored(
    *,
    reading_progress_snapshot: dict[str, Any],
    full_book: bool,
    cutoff_chapter_number: int,
    analysis_version_id: int | None,
    hierarchy_build_id: str,
    hierarchy_checksum: str,
    evidence: list[ContextEvidenceEntry] | list[dict[str, Any]],
    omitted_evidence_counts: dict[str, int],
    prompt_inputs: dict[str, Any],
    source_status: dict[str, str],
    expected_checksum: str,
) -> ContextManifest:
    """Rehydrate a previously persisted manifest and verify its checksum.

    Retry paths load this frozen graph instead of re-assembling against current progress.
    """

    entries: list[ContextEvidenceEntry] = []
    for idx, raw in enumerate(evidence):
        if isinstance(raw, ContextEvidenceEntry):
            entries.append(raw)
            continue
        entries.append(
            ContextEvidenceEntry(
                evidence_key=str(raw["evidence_key"]),
                source_type=str(raw["source_type"]),
                source_id=str(raw["source_id"]),
                chapter_id=int(raw["chapter_id"]),
                chapter_number=int(raw["chapter_number"]),
                source_start=int(raw["source_start"]),
                source_end=int(raw["source_end"]),
                content_hash=str(raw["content_hash"]),
                excerpt=str(raw["excerpt"]),
                sort_order=int(raw.get("sort_order", idx)),
                version_lineage=dict(raw.get("version_lineage") or {}),
            )
        )

    draft = ContextManifest(
        reading_progress_snapshot=dict(reading_progress_snapshot),
        full_book=bool(full_book),
        cutoff_chapter_number=int(cutoff_chapter_number),
        analysis_version_id=analysis_version_id,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        evidence=tuple(entries),
        omitted_evidence_counts=dict(omitted_evidence_counts),
        prompt_inputs=dict(prompt_inputs),
        source_status=dict(source_status),
        manifest_checksum="",
    )
    checksum = canonical_manifest_checksum(draft.canonical_payload())
    if checksum != expected_checksum:
        raise SelectionValidationError(
            "manifest_checksum_mismatch",
            "stored manifest checksum does not match canonical rehydrate",
        )
    return ContextManifest(
        reading_progress_snapshot=draft.reading_progress_snapshot,
        full_book=draft.full_book,
        cutoff_chapter_number=draft.cutoff_chapter_number,
        analysis_version_id=draft.analysis_version_id,
        hierarchy_build_id=draft.hierarchy_build_id,
        hierarchy_checksum=draft.hierarchy_checksum,
        evidence=draft.evidence,
        omitted_evidence_counts=draft.omitted_evidence_counts,
        prompt_inputs=draft.prompt_inputs,
        source_status=draft.source_status,
        manifest_checksum=checksum,
    )
