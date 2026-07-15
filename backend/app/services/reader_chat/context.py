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

    selection_entry = ContextEvidenceEntry(
        evidence_key=SELECTION_EVIDENCE_KEY,
        source_type="selection",
        source_id=f"{selection.chapter_id}:{selection.source_start}:{selection.source_end}",
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

    evidence_entries: list[ContextEvidenceEntry] = [selection_entry]
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
    }

    source_status = dict(retrieval.source_status)
    source_status.setdefault("selection", SourceStatus.OK)

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
    raise AssertionError("retry must not depend on rebuilt manifests matching by chance")


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
