"""QueryPlan consumer adaptation for Reader Chat (Phase 26-04 / REQ-QP-04).

Shared QueryPlan core with a ``selection`` anchor: the frozen progress
snapshot, the consumer request payload builder, the world-projection resolver
(Phase 40), and the ``run_reader_queryplan`` seam that re-validates owner /
cutoff / spoiler / evidence against the frozen snapshot. A blocked plan raises
the stable ``SelectionValidationError`` (D-02/D-03/D-12) and never produces an
answer or trace.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Chapter, Novel
from app.services.queryplan.schemas import (
    QueryDimension,
    QueryPlanIntent,
    SelectionAnchor,
)
from app.services.queryplan.service import (
    ConsumerManifestResult,
    ConsumerPlanBlocked,
    ConsumerQueryPlanView,
    QueryPlanService,
)
from app.services.reader_chat.context_types import (
    ProgressSnapshot,
    SelectionValidationError,
    ValidatedSelection,
)
from app.services.reader_chat.retrieval import (
    RelationshipObservationReader,
    build_source_snapshot,
    chat_retrieval_dimension_results,
    retrieve_visible_evidence,
)
from app.services.timeline.query import resolve_chapter_cutoff


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


_CONSUMER_BLOCKED_CODES: dict[str, str] = {
    "unknown_intent": "unknown_intent",
    "ambiguous_intent": "ambiguous_intent",
    "scope_escape": "scope_escape",
    "future_probing": "future_probing",
    "contradictory_constraints": "contradictory_constraints",
    "whole_book_unauthorized": "whole_book_unauthorized",
    "invalid_input": "invalid_input",
}


def _consumer_blocked_code(reason_code: str) -> str:
    return _CONSUMER_BLOCKED_CODES.get(reason_code, "queryplan_blocked")


def build_reader_consumer_request(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    question_text: str,
    through_chapter: int,
    snapshot_hash: str,
    full_book_authorized: bool = False,
    whole_book: bool = False,
    selection: ValidatedSelection,
    source: str = "reader_chat",
    dimensions: Sequence[QueryDimension] | None = None,
) -> dict[str, Any]:
    """Reader-consumer plan payload (selection anchor; D-10).

    The anchor's ``chapter_id`` carries the *chapter ordinal* the reader
    anchored on: the QueryPlan parser's scope check compares the anchor chapter
    against the reading cutoff (chapter-number semantics), while leaf evidence
    is always re-sliced from the frozen snapshot via evidence refs — never from
    the anchor itself (D-07). Whole-book still requires the per-novel switch
    (D-12), enforced by the parser.
    """
    return QueryPlanService.build_consumer_request(
        intent=QueryPlanIntent.READER,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        question_text=question_text,
        through_chapter=through_chapter,
        snapshot_hash=snapshot_hash,
        full_book_authorized=full_book_authorized,
        whole_book=whole_book,
        selection=SelectionAnchor(
            kind="selection",
            chapter_id=int(selection.chapter_number),
            source_start=int(selection.source_start),
            source_end=int(selection.source_end),
            chapter_content_hash=selection.chapter_content_hash,
        ),
        source=source,
        dimensions=dimensions,
    )


def _build_world_projection_resolver(
    session: AsyncSession,
):
    """Phase 40：从 world_model_knowledge 域表读 claims，构造 world_projection
    reader resolver（closure 捕获 session，作用域仅限当前请求）。

    reader 内部 fail-closed：无 claims → None（unavailable）；快照不匹配 →
    WorldProjectionUnavailableError → adapter 转 unavailable；有 passed
    candidate → candidate_only（partial）；有 approved → available。
    """
    from app.models.world_model_knowledge import WorldModelKnowledge
    from app.services.queryplan.adapters import READER_WORLD_PROJECTION
    from app.services.world_model.knowledge import EpistemicClaim
    from app.services.world_model.queries import world_projection_reader

    async def resolver(reader_id: str):
        if reader_id != READER_WORLD_PROJECTION:
            return None

        async def reader(context):
            rows = (
                await session.scalars(
                    select(WorldModelKnowledge).where(
                        WorldModelKnowledge.owner_id == context.owner_id,
                        WorldModelKnowledge.novel_id == context.novel_id,
                        WorldModelKnowledge.version_id == context.version_id,
                    )
                )
            ).all()
            claims = [
                EpistemicClaim.model_validate(dict(r.canonical_payload or {}))
                for r in rows
            ]
            return await world_projection_reader(claims, context=context)

        return reader

    return resolver


async def run_reader_queryplan(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    version_id: int,
    question: str,
    selection: ValidatedSelection,
    relationship_reader: RelationshipObservationReader | None = None,
    whole_book: bool = False,
) -> tuple[ConsumerManifestResult, ConsumerQueryPlanView]:
    """Reader consumer seam: shared QueryPlan core with a selection anchor.

    Server re-validates owner / cutoff / spoiler / evidence against the frozen
    snapshot and returns the consumer view (trace / availability / fallback /
    citation jump). A blocked plan raises ``SelectionValidationError`` with the
    stable reason code (D-02/D-03/D-12) and never produces an answer or trace.
    """
    progress = await resolve_progress_snapshot(session, novel)
    snapshot = await build_source_snapshot(
        session,
        novel=novel,
        owner_id=owner_id,
        version_id=version_id,
        full_book_authorized=progress.full_book,
    )
    payload = build_reader_consumer_request(
        owner_id=owner_id,
        novel_id=novel.id,
        version_id=version_id,
        question_text=question,
        through_chapter=progress.cutoff_chapter_number,
        snapshot_hash=snapshot.snapshot_hash,
        full_book_authorized=progress.full_book,
        whole_book=whole_book,
        selection=selection,
        dimensions=(QueryDimension.WORLD_PROJECTION,),
    )
    cutoff = None if progress.full_book else progress.cutoff_chapter_number
    retrieval = await retrieve_visible_evidence(
        session,
        novel=novel,
        owner_id=owner_id,
        selection_chapter_id=selection.chapter_id,
        selection_start=selection.source_start,
        selection_end=selection.source_end,
        cutoff_chapter=cutoff,
        full_book=progress.full_book,
        relationship_reader=relationship_reader,
        max_evidence=24,
    )
    dimension_results = chat_retrieval_dimension_results(retrieval, snapshot)
    # Phase 40：问答按需分析物化的 world_model_knowledge 候选 → world_projection
    # 维度（resolver 从域表读 claims）。无 claims 时 adapter 诚实报 unavailable。
    from app.services.queryplan.adapters import (
        DEFAULT_ADAPTERS,
        run_world_projection_adapter,
    )
    from app.services.queryplan.evidence import effective_through_chapter

    try:
        plan = QueryPlanService.parse_consumer_request(payload)
        wp_result = await run_world_projection_adapter(
            DEFAULT_ADAPTERS[QueryDimension.WORLD_PROJECTION],
            source=snapshot,
            through_chapter=effective_through_chapter(plan, snapshot),
            resolver=_build_world_projection_resolver(session),
            question=question,
        )
        dimension_results = tuple(dimension_results) + (wp_result,)
        return await QueryPlanService().execute_consumer_manifest(
            payload, source=snapshot, dimension_results=dimension_results
        )
    except ConsumerPlanBlocked as exc:
        raise SelectionValidationError(
            _consumer_blocked_code(exc.reason_code),
            exc.result.message,
        ) from exc
