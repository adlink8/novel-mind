"""Immutable context-manifest assembly + frozen rehydration.

Builds the deterministic evidence graph (primary entry + retrieval entries),
folds the QueryPlan consumer view into ``prompt_inputs``, canonical-checksums
the frozen graph, and rehydrates previously persisted manifests on retry
without rebuilding under newer reading progress.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
from app.services.queryplan.service import ConsumerQueryPlanView
from app.services.reader_chat.context_queryplan import resolve_progress_snapshot
from app.services.reader_chat.context_types import (
    CHAPTER_EVIDENCE_KEY,
    CHAPTER_RANGE_ANCHOR_KIND,
    SELECTION_EVIDENCE_KEY,
    ContextEvidenceEntry,
    ContextManifest,
    SelectionValidationError,
    ValidatedChapterRange,
    ValidatedSelection,
    canonical_manifest_checksum,
    code_point_len,
    content_sha256,
)
from app.services.reader_chat.retrieval import (
    RelationshipObservationReader,
    SourceStatus,
    retrieve_visible_evidence,
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
    queryplan_view: ConsumerQueryPlanView | None = None,
) -> ContextManifest:
    """Build one immutable, deterministic context graph for atomic persistence.

    Client-supplied evidence IDs are ignored (never trusted). Optional sources may
    be unavailable without inventing content or widening the spoiler scope.

    ``queryplan_view`` (Phase 26-04) embeds the shared QueryPlan trace /
    availability / fallback / citation-jump record into ``prompt_inputs`` so the
    consumer can expose trace-level detail; it is never used as evidence.
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
    if queryplan_view is not None:
        prompt_inputs["queryplan"] = queryplan_view.canonical_dict()

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
    queryplan_view: ConsumerQueryPlanView | None = None,
) -> ContextManifest:
    """Build one immutable context graph for a structure-anchored chapter range.

    Per-chapter budgeted excerpts are the primary evidence; retrieval evidence
    is aggregated strictly inside the (already cutoff-narrowed) interval.
    ``queryplan_view`` (Phase 26-04) embeds the shared QueryPlan trace record
    into ``prompt_inputs`` for trace-level exposure; never evidence.
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
    if queryplan_view is not None:
        prompt_inputs["queryplan"] = queryplan_view.canonical_dict()

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
