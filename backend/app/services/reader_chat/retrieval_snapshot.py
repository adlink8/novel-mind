"""Reader Chat visible-evidence merge and QueryPlan source snapshot layer.

可见性合并（``retrieve_visible_evidence``）与快照层（``_snapshot_hash`` /
``build_source_snapshot`` / ``chat_retrieval_dimension_results``）。依赖方向：
本模块只依赖 ``retrieval_types``（契约层）与 ``retrieval_sources``（来源层），
不反向 import context / conversations。

拆分说明（refactor split）：原 ``retrieval.py`` 按职责域拆为 ``retrieval_types`` /
``retrieval_sources`` / ``retrieval_snapshot`` 三模块，``retrieval.py`` 保留为门面
并显式 re-export 全部顶层符号。``SOURCE_PRIORITY`` 单例定义在 ``retrieval_types``，
此处只读不重定义。
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.novel import Chapter, Novel
from app.services.queryplan.adapters import (
    ChapterRecord,
    DimensionResult,
    SourceSnapshot,
    chapter_content_hash,
)
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    EvidenceRef,
    FallbackStage,
    QueryDimension,
)

from .retrieval_sources import (
    fetch_hierarchy_evidence,
    fetch_knowledge_evidence,
    fetch_relationship_evidence,
    fetch_timeline_evidence,
    resolve_active_analysis_version,
    resolve_active_hierarchy,
)
from .retrieval_types import (
    DEFAULT_MAX_EVIDENCE,
    DEFAULT_MAX_PER_SOURCE,
    RelationshipObservationReader,
    RetrievalResult,
    RetrievedEvidence,
    SourceStatus,
)


async def retrieve_visible_evidence(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    selection_chapter_id: int,
    selection_start: int,
    selection_end: int,
    cutoff_chapter: int | None,
    full_book: bool,
    relationship_reader: RelationshipObservationReader | None = None,
    max_evidence: int = DEFAULT_MAX_EVIDENCE,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
) -> RetrievalResult:
    """Owner/novel/cutoff filtered retrieval; never trusts client evidence IDs."""

    source_status: dict[str, str] = {
        "hierarchy": SourceStatus.ABSENT,
        "timeline": SourceStatus.ABSENT,
        "knowledge": SourceStatus.ABSENT,
        "relationship_observation": SourceStatus.ABSENT,
    }
    omitted: dict[str, int] = {
        "hierarchy": 0,
        "timeline": 0,
        "knowledge": 0,
        "relationship_observation": 0,
    }

    hierarchy_meta = await resolve_active_hierarchy(session, novel_id=novel.id)
    hierarchy_build_id = ""
    hierarchy_checksum = ""
    hierarchy_items: list[RetrievedEvidence] = []
    if hierarchy_meta is None:
        source_status["hierarchy"] = SourceStatus.ABSENT
    else:
        hierarchy_build_id, hierarchy_checksum = hierarchy_meta
        hierarchy_items, omitted["hierarchy"] = await fetch_hierarchy_evidence(
            session,
            novel_id=novel.id,
            build_id=hierarchy_build_id,
            cutoff_chapter=cutoff_chapter,
            full_book=full_book,
            selection_chapter_id=selection_chapter_id,
            selection_start=selection_start,
            selection_end=selection_end,
            max_items=max_per_source,
        )
        source_status["hierarchy"] = SourceStatus.OK

    version_id = await resolve_active_analysis_version(
        session, owner_id=owner_id, novel_id=novel.id
    )

    chapters = list(
        (
            await session.scalars(select(Chapter).where(Chapter.novel_id == novel.id))
        ).all()
    )
    chapters_by_id = {c.id: c for c in chapters}
    chapters_by_number = {c.chapter_number: c for c in chapters}

    (
        timeline_items,
        omitted["timeline"],
        source_status["timeline"],
    ) = await fetch_timeline_evidence(
        session,
        owner_id=owner_id,
        novel_id=novel.id,
        version_id=version_id,
        cutoff_chapter=cutoff_chapter,
        full_book=full_book,
        chapters_by_id=chapters_by_id,
        max_items=max_per_source,
    )

    # 问答按需分析（chat_backfill）物化的域表 candidate 证据（Phase 40）。
    # 有候选行 → OK（带 candidate:True 标记）；无 → ABSENT（不虚造）。
    (
        knowledge_items,
        omitted["knowledge"],
        source_status["knowledge"],
    ) = await fetch_knowledge_evidence(
        session,
        owner_id=owner_id,
        novel_id=novel.id,
        version_id=version_id,
        cutoff_chapter=cutoff_chapter,
        full_book=full_book,
        chapters_by_number=chapters_by_number,
        max_items=max_per_source,
    )

    (
        rel_items,
        omitted["relationship_observation"],
        source_status["relationship_observation"],
    ) = await fetch_relationship_evidence(
        session,
        novel=novel,
        owner_id=owner_id,
        version_id=version_id,
        cutoff_chapter=cutoff_chapter,
        full_book=full_book,
        reader=relationship_reader,
        chapters_by_number=chapters_by_number,
        max_items=max_per_source,
    )

    packed = hierarchy_items + knowledge_items + timeline_items + rel_items
    packed.sort(
        key=lambda item: (
            item.priority,
            item.rank_key,
            item.evidence_key,
        )
    )
    if len(packed) > max_evidence:
        # Truncate between complete entries only.
        overflow = packed[max_evidence:]
        for item in overflow:
            omitted[item.source_type] = omitted.get(item.source_type, 0) + 1
        packed = packed[:max_evidence]

    return RetrievalResult(
        items=packed,
        omitted_counts=omitted,
        source_status=source_status,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        analysis_version_id=version_id,
    )


# ---------------------------------------------------------------------------
# Shared QueryPlan consumer seam (Phase 26-04 / D-07, D-10, D-15)
# ---------------------------------------------------------------------------


def _snapshot_hash(
    novel_id: int, version_id: int, records: tuple[ChapterRecord, ...]
) -> str:
    """Deterministic content address of the frozen chapter snapshot.

    Covers the owner/novel/version lineage (via novel+version) and every
    chapter's content hash so both chat consumers share the same leaf/raw
    evidence authority. Never includes client- or model-supplied text.
    """
    body = {
        "novel_id": int(novel_id),
        "version_id": int(version_id),
        "chapters": [
            {
                "chapter_id": rec.chapter_id,
                "chapter_number": rec.chapter_number,
                "content_hash": rec.content_hash,
            }
            for rec in records
        ],
    }
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def build_source_snapshot(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    version_id: int,
    full_book_authorized: bool = False,
) -> SourceSnapshot:
    """Freeze the novel's chapters into a QueryPlan SourceSnapshot (D-07).

    Both Reader and Analysis Chat build their QueryPlan source from this one
    frozen snapshot, so evidence refs always re-slice the same leaf/raw text.
    """
    rows = list(
        (
            await session.scalars(
                select(Chapter)
                # content 是 deferred 大文本列：快照必须含正文，强制加载避免
                # async 上下文触发 lazy IO（MissingGreenlet）。
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == novel.id)
                .order_by(Chapter.chapter_number.asc())
            )
        ).all()
    )
    records = tuple(
        ChapterRecord(
            chapter_id=int(ch.id),
            chapter_number=int(ch.chapter_number),
            content=ch.content or "",
            content_hash=chapter_content_hash(ch.content or ""),
        )
        for ch in rows
    )
    return SourceSnapshot(
        owner_id=owner_id,
        novel_id=int(novel.id),
        version_id=version_id,
        snapshot_hash=_snapshot_hash(novel.id, version_id, records),
        chapters=records,
        full_book_authorized=full_book_authorized,
    )


def chat_retrieval_dimension_results(
    retrieval: RetrievalResult,
    snapshot: SourceSnapshot,
) -> tuple[DimensionResult, ...]:
    """Adapt the chat retrieval stack into the QueryPlan raw_text dimension.

    The retrieval selects candidate spans (shared recall); every EvidenceRef is
    then re-sliced from the frozen snapshot chapter so the content hash is the
    exact leaf slice (D-07). Candidates that cannot re-slice to a leaf (out of
    bounds / empty / stale) never become evidence (D-15).
    """
    chapters_by_id = {rec.chapter_id: rec for rec in snapshot.chapters}
    refs: list[EvidenceRef] = []
    for item in retrieval.items:
        chapter = chapters_by_id.get(item.chapter_id)
        if chapter is None:
            continue
        start, end = item.source_start, item.source_end
        if start < 0 or end <= start or end > len(chapter.content):
            continue
        excerpt = chapter.content[start:end]
        if not excerpt:
            continue
        refs.append(
            EvidenceRef(
                chapter_id=chapter.chapter_id,
                chapter_number=chapter.chapter_number,
                source_start=start,
                source_end=end,
                content_hash=chapter_content_hash(excerpt),
                source_snapshot_hash=snapshot.snapshot_hash,
            )
        )
    if refs:
        return (
            DimensionResult(
                dimension=QueryDimension.RAW_TEXT,
                status=AvailabilityStatus.AVAILABLE,
                reason="reader_ok",
                provenance="reader_chat_retrieval_v1",
                stage=FallbackStage.EXACT_READER,
                refs=tuple(refs),
            ),
        )
    return (
        DimensionResult(
            dimension=QueryDimension.RAW_TEXT,
            status=AvailabilityStatus.UNAVAILABLE,
            reason="reader_zero_hits_in_scope",
            provenance="reader_chat_retrieval_v1",
            stage=FallbackStage.STABLE_UNAVAILABLE,
        ),
    )
