"""Analysis Chat QueryPlan adapter — structure-range anchor (REQ-QP-04, D-10).

Phase 26-04: the Analysis Chat entry shares the same QueryPlan / retrieval /
evidence core as Reader Chat; the only difference is the anchor. Reader uses a
``selection`` anchor; Analysis uses an inclusive ``chapter_range`` anchor.

Authority boundaries:

- Server re-validates owner / cutoff / spoiler / budget / evidence against the
  frozen snapshot; the chapter interval is narrowed to the reading cutoff before
  parsing (the parser rejects an end beyond cutoff with ``SCOPE_ESCAPE``).
- Whole-book still requires the per-novel switch (D-12), enforced by the parser
  (``WHOLE_BOOK_UNAUTHORIZED`` / ``CONTRADICTORY``).
- A blocked plan raises the stable ``SelectionValidationError`` (same type the
  reader chat context uses) and never produces an answer, trace or DB write.
- No NM promotion / active-pointer / consumer cutover path exists (D-14).
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
from app.services.queryplan.adapters import SourceSnapshot
from app.services.queryplan.schemas import (
    ChapterRangeAnchor,
    QueryDimension,
    QueryPlanIntent,
)
from app.services.queryplan.service import (
    ConsumerManifestResult,
    ConsumerPlanBlocked,
    ConsumerQueryPlanView,
    QueryPlanAnswer,
    QueryPlanService,
)
from app.services.reader_chat.context import (
    ProgressSnapshot,
    SelectionValidationError,
    _consumer_blocked_code,
    narrow_chapter_range,
    resolve_progress_snapshot,
)
from app.services.reader_chat.retrieval import build_source_snapshot


def build_analysis_consumer_request(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    question_text: str,
    through_chapter: int,
    snapshot_hash: str,
    chapter_start: int,
    chapter_end: int,
    full_book_authorized: bool = False,
    whole_book: bool = False,
    source: str = "analysis_chat",
    dimensions: Sequence[QueryDimension] | None = None,
) -> dict[str, Any]:
    """Analysis-consumer plan payload (inclusive chapter_range anchor; D-10)."""
    return QueryPlanService.build_consumer_request(
        intent=QueryPlanIntent.ANALYSIS,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        question_text=question_text,
        through_chapter=through_chapter,
        snapshot_hash=snapshot_hash,
        full_book_authorized=full_book_authorized,
        whole_book=whole_book,
        chapter_range=ChapterRangeAnchor(
            kind="chapter_range",
            chapter_start=chapter_start,
            chapter_end=chapter_end,
        ),
        source=source,
        dimensions=dimensions,
    )


class AnalysisQueryPlanAdapter:
    """Shared QueryPlan core consumed by the Analysis Chat structure anchor."""

    def __init__(self, service: QueryPlanService | None = None) -> None:
        self._service = service or QueryPlanService()

    async def resolve_scope(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        version_id: int,
        question: str,
        chapter_start: int,
        chapter_end: int,
        whole_book: bool = False,
        dimensions: Sequence[QueryDimension] | None = None,
    ) -> tuple[dict[str, Any], SourceSnapshot, ProgressSnapshot]:
        """Re-validate scope and freeze the shared payload + snapshot.

        The requested end is narrowed to the reading cutoff (``narrow_chapter_range``);
        a range starting beyond the cutoff raises ``chapter_beyond_cutoff``.
        """
        progress = await resolve_progress_snapshot(session, novel)
        effective_end = narrow_chapter_range(
            chapter_start,
            chapter_end,
            cutoff_chapter_number=progress.cutoff_chapter_number,
            full_book=progress.full_book,
        )
        snapshot = await build_source_snapshot(
            session,
            novel=novel,
            owner_id=owner_id,
            version_id=version_id,
            full_book_authorized=progress.full_book,
        )
        payload = build_analysis_consumer_request(
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            question_text=question,
            through_chapter=progress.cutoff_chapter_number,
            snapshot_hash=snapshot.snapshot_hash,
            chapter_start=chapter_start,
            chapter_end=int(effective_end),
            full_book_authorized=progress.full_book,
            whole_book=whole_book,
            dimensions=dimensions,
        )
        return payload, snapshot, progress

    async def execute_manifest(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        version_id: int,
        question: str,
        chapter_start: int,
        chapter_end: int,
        whole_book: bool = False,
    ) -> tuple[ConsumerManifestResult, ConsumerQueryPlanView]:
        """Context-build path: freeze the analysis retrieval/evidence graph."""
        payload, snapshot, _ = await self.resolve_scope(
            session,
            novel=novel,
            owner_id=owner_id,
            version_id=version_id,
            question=question,
            chapter_start=chapter_start,
            chapter_end=chapter_end,
            whole_book=whole_book,
            dimensions=(QueryDimension.WORLD_PROJECTION,),
        )
        # Phase 40：问答按需分析物化的 world_model_knowledge 候选 →
        # world_projection 维度（resolver 从域表读 claims）。
        from app.services.queryplan.adapters import (
            DEFAULT_ADAPTERS,
            run_world_projection_adapter,
        )
        from app.services.queryplan.evidence import effective_through_chapter
        from app.services.reader_chat.context import _build_world_projection_resolver

        try:
            plan = QueryPlanService.parse_consumer_request(payload)
            wp_result = await run_world_projection_adapter(
                DEFAULT_ADAPTERS[QueryDimension.WORLD_PROJECTION],
                source=snapshot,
                through_chapter=effective_through_chapter(plan, snapshot),
                resolver=_build_world_projection_resolver(session),
                question=question,
            )
            return await self._service.execute_consumer_manifest(
                payload,
                source=snapshot,
                dimension_results=(wp_result,),
            )
        except ConsumerPlanBlocked as exc:
            raise SelectionValidationError(
                _consumer_blocked_code(exc.reason_code),
                exc.result.message,
            ) from exc

    async def execute(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        version_id: int,
        question: str,
        chapter_start: int,
        chapter_end: int,
        whole_book: bool = False,
        answer_producer,
    ) -> tuple[QueryPlanAnswer, ConsumerQueryPlanView]:
        """Full path: analysis retrieval -> freeze -> leaf-only cited-answer gate."""
        payload, snapshot, _ = await self.resolve_scope(
            session,
            novel=novel,
            owner_id=owner_id,
            version_id=version_id,
            question=question,
            chapter_start=chapter_start,
            chapter_end=chapter_end,
            whole_book=whole_book,
        )
        return await self._service.execute_consumer(
            payload,
            source=snapshot,
            answer_producer=answer_producer,
        )


analysis_query_plan_adapter = AnalysisQueryPlanAdapter()
