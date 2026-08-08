"""Context-graph builders for reader-chat conversations.

Sibling module of ``conversations`` (service lifecycle). Holds the builder
family extracted from the facade: ``anchor_view_from_prompt_inputs`` /
``EvidenceRefDraft`` / ``ContextGraph`` / ``ContextBuilder`` (Protocol) /
``ProductionContextBuilder`` / ``DeterministicContextBuilder`` plus the
``_sha256_text`` / ``_sha256_json`` hashing helpers. ``conversations``
re-exports every symbol so the public import surface is unchanged.

拆分说明（refactor split）：本模块对应原 ``conversations.py`` 行 59-452 的
上下文构建器族。``context`` / ``retrieval`` 的导入保持在方法体内 lazy import，
避免与本模块顶层 import 成环（``conversations`` -> ``conversations_builders``，
builders 仅在运行时按需加载 ``context`` / ``retrieval``）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.novel import Chapter, Novel
from app.schemas.reader_chat import (
    ChapterRange,
    ChapterRangeAnchor,
    SelectionCoordinate,
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return _sha256_text(canonical)


def anchor_view_from_prompt_inputs(
    prompt_inputs: dict[str, Any] | None,
) -> ChapterRangeAnchor | None:
    """Echo the persisted chapter_range anchor (narrowed values) if present."""

    raw = (prompt_inputs or {}).get("anchor")
    if not isinstance(raw, dict) or raw.get("kind") != "chapter_range":
        return None
    try:
        return ChapterRangeAnchor(
            chapter_start=int(raw["chapter_start"]),
            chapter_end=int(raw["chapter_end"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class EvidenceRefDraft:
    evidence_key: str
    source_type: str
    source_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    excerpt: str
    sort_order: int = 0
    version_lineage: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextGraph:
    """Validated selection + immutable manifest graph ready for one transaction."""

    selection_text: str | None
    selection_text_hash: str | None
    chapter_content_hash: str | None
    hierarchy_build_id: str
    hierarchy_checksum: str
    reading_progress_snapshot: dict[str, Any]
    full_book: bool
    cutoff_chapter_number: int
    analysis_version_id: int | None
    manifest_checksum: str
    prompt_inputs: dict[str, Any]
    omitted_evidence_counts: dict[str, Any]
    evidence_refs: list[EvidenceRefDraft]
    prompt_hash: str
    schema_hash: str
    decoding_hash: str
    config_hash: str
    model_lineage: dict[str, Any]
    price_snapshot: dict[str, Any]


class ContextBuilder(Protocol):
    async def build(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        selection: SelectionCoordinate | None,
        body: str,
        chapter_id: int | None = None,
        chapter_range: ChapterRange | None = None,
    ) -> ContextGraph: ...


class ProductionContextBuilder:
    """Plan 03 production assembly: exact selection + visible-set-first manifest."""

    async def build(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        selection: SelectionCoordinate | None,
        body: str,
        chapter_id: int | None = None,
        chapter_range: ChapterRange | None = None,
    ) -> ContextGraph:
        from app.services.reader_chat.context import (
            SelectionValidationError,
            assemble_context_manifest,
            assemble_range_context_manifest,
            run_reader_queryplan,
            validate_chapter_context,
            validate_chapter_range_context,
            validate_selection,
        )
        from app.services.reader_chat.retrieval import (
            Phase09RelationshipObservationReader,
            resolve_active_analysis_version,
        )

        try:
            if chapter_range is not None:
                validated_range = await validate_chapter_range_context(
                    db,
                    novel=novel,
                    owner_id=owner_id,
                    chapter_start=chapter_range.chapter_start,
                    chapter_end=chapter_range.chapter_end,
                )
                queryplan_view = None
                version_id = await resolve_active_analysis_version(
                    db, owner_id=owner_id, novel_id=novel.id
                )
                if version_id is not None:
                    from app.services.analysis_chat.query_adapter import (
                        AnalysisQueryPlanAdapter,
                    )

                    (
                        _,
                        queryplan_view,
                    ) = await AnalysisQueryPlanAdapter().execute_manifest(
                        db,
                        novel=novel,
                        owner_id=owner_id,
                        version_id=version_id,
                        question=body,
                        chapter_start=chapter_range.chapter_start,
                        chapter_end=chapter_range.chapter_end,
                    )
                manifest = await assemble_range_context_manifest(
                    db,
                    novel=novel,
                    owner_id=owner_id,
                    chapter_range=validated_range,
                    question=body,
                    relationship_reader=Phase09RelationshipObservationReader(),
                    queryplan_view=queryplan_view,
                )
            else:
                if selection is not None:
                    validated = await validate_selection(
                        db,
                        novel=novel,
                        owner_id=owner_id,
                        selection=selection,
                    )
                else:
                    if chapter_id is None:
                        raise SelectionValidationError(
                            "missing_chapter", "chapter_id is required"
                        )
                    validated = await validate_chapter_context(
                        db,
                        novel=novel,
                        owner_id=owner_id,
                        chapter_id=chapter_id,
                    )
                queryplan_view = None
                version_id = await resolve_active_analysis_version(
                    db, owner_id=owner_id, novel_id=novel.id
                )
                if version_id is not None:
                    _, queryplan_view = await run_reader_queryplan(
                        db,
                        novel=novel,
                        owner_id=owner_id,
                        version_id=version_id,
                        question=body,
                        selection=validated,
                        relationship_reader=Phase09RelationshipObservationReader(),
                    )
                manifest = await assemble_context_manifest(
                    db,
                    novel=novel,
                    owner_id=owner_id,
                    selection=validated,
                    question=body,
                    relationship_reader=Phase09RelationshipObservationReader(),
                    selection_bound=selection is not None,
                    queryplan_view=queryplan_view,
                )
        except SelectionValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

        evidence_refs = [
            EvidenceRefDraft(
                evidence_key=entry.evidence_key,
                source_type=entry.source_type,
                source_id=entry.source_id,
                chapter_id=entry.chapter_id,
                chapter_number=entry.chapter_number,
                source_start=entry.source_start,
                source_end=entry.source_end,
                content_hash=entry.content_hash,
                excerpt=entry.excerpt,
                sort_order=entry.sort_order,
                version_lineage=dict(entry.version_lineage),
            )
            for entry in manifest.evidence
        ]
        frozen = "d" * 64
        prompt_inputs = dict(manifest.prompt_inputs)
        prompt_inputs["conversation_id"] = conversation_id
        prompt_inputs["owner_id"] = owner_id
        prompt_inputs["builder"] = "production-plan03"
        # Persist source_status so 10-04 freeze_manifest_from_stored can rehydrate checksum.
        prompt_inputs["source_status"] = dict(manifest.source_status)
        return ContextGraph(
            selection_text=validated.selection_text if selection is not None else None,
            selection_text_hash=(
                validated.selection_text_hash if selection is not None else None
            ),
            chapter_content_hash=(
                validated.chapter_content_hash if selection is not None else None
            ),
            hierarchy_build_id=manifest.hierarchy_build_id,
            hierarchy_checksum=manifest.hierarchy_checksum,
            reading_progress_snapshot=dict(manifest.reading_progress_snapshot),
            full_book=manifest.full_book,
            cutoff_chapter_number=manifest.cutoff_chapter_number,
            analysis_version_id=manifest.analysis_version_id,
            manifest_checksum=manifest.manifest_checksum,
            prompt_inputs=prompt_inputs,
            omitted_evidence_counts=dict(manifest.omitted_evidence_counts),
            evidence_refs=evidence_refs,
            prompt_hash=frozen,
            schema_hash=frozen,
            decoding_hash=frozen,
            config_hash=frozen,
            model_lineage={"builder": "production-plan03"},
            price_snapshot={},
        )


class DeterministicContextBuilder:
    """
    Plan-02 stub retained for tests and fallback isolation.

    Validates selection against owned Chapter.content and commits a minimal
    selection-only evidence graph. Never invents domain facts.
    """

    PLACEHOLDER_HIERARCHY_BUILD = "pending-hierarchy"
    PLACEHOLDER_HIERARCHY_CHECKSUM = "0" * 64

    async def build(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        selection: SelectionCoordinate | None,
        body: str,
        chapter_id: int | None = None,
        chapter_range: ChapterRange | None = None,
    ) -> ContextGraph:
        if chapter_range is not None:
            raise HTTPException(
                status_code=422,
                detail="chapter_range messages require the production context builder",
            )
        if selection is None:
            raise HTTPException(
                status_code=422,
                detail="chapter-only messages require the production context builder",
            )
        result = await db.execute(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(
                Chapter.id == selection.chapter_id,
                Chapter.novel_id == novel.id,
            )
        )
        chapter = result.scalar_one_or_none()
        if chapter is None:
            raise HTTPException(status_code=422, detail="selection chapter not found")

        content = chapter.content or ""
        content_hash = _sha256_text(content)
        if selection.chapter_content_hash != content_hash:
            raise HTTPException(status_code=422, detail="stale chapter content hash")

        if selection.source_start < 0 or selection.source_end > len(content):
            raise HTTPException(
                status_code=422, detail="selection offsets out of range"
            )
        if selection.source_end <= selection.source_start:
            raise HTTPException(status_code=422, detail="invalid selection range")

        sliced = content[selection.source_start : selection.source_end]
        if sliced != selection.selection_text:
            raise HTTPException(status_code=422, detail="selection text mismatch")

        selection_hash = _sha256_text(sliced)
        if selection.selection_text_hash != selection_hash:
            raise HTTPException(status_code=422, detail="selection text hash mismatch")

        progress = dict(novel.reading_progress or {})
        full_book = bool(progress.get("timeline_full_book") is True)
        cutoff = int(chapter.chapter_number)
        if not full_book:
            # Without production cutoff resolver (Plan 03), default to selected chapter
            # and never expand beyond first chapter when progress is empty.
            chapter_id = progress.get("chapter_id")
            if chapter_id is None:
                cutoff = 1
            else:
                prog_ch = (
                    await db.execute(
                        select(Chapter).where(
                            Chapter.id == int(chapter_id),
                            Chapter.novel_id == novel.id,
                        )
                    )
                ).scalar_one_or_none()
                cutoff = int(prog_ch.chapter_number) if prog_ch else 1
            if chapter.chapter_number > cutoff:
                raise HTTPException(
                    status_code=422,
                    detail="selection chapter exceeds reading cutoff",
                )

        evidence_key = (
            f"selection:{chapter.id}:{selection.source_start}:{selection.source_end}"
        )
        evidence = EvidenceRefDraft(
            evidence_key=evidence_key,
            source_type="selection",
            source_id=str(chapter.id),
            chapter_id=chapter.id,
            chapter_number=int(chapter.chapter_number),
            source_start=selection.source_start,
            source_end=selection.source_end,
            content_hash=selection_hash,
            excerpt=sliced[:500],
            sort_order=0,
            version_lineage={},
        )
        manifest_payload = {
            "full_book": full_book,
            "cutoff_chapter_number": cutoff,
            "reading_progress_snapshot": progress,
            "hierarchy_build_id": self.PLACEHOLDER_HIERARCHY_BUILD,
            "hierarchy_checksum": self.PLACEHOLDER_HIERARCHY_CHECKSUM,
            "evidence_keys": [evidence.evidence_key],
            "body_hash": _sha256_text(body),
            "conversation_id": conversation_id,
            "owner_id": owner_id,
        }
        manifest_checksum = _sha256_json(manifest_payload)
        prompt_inputs = {
            "selection_evidence_key": evidence_key,
            "builder": "deterministic-plan02",
        }
        frozen = "d" * 64
        return ContextGraph(
            selection_text=sliced,
            selection_text_hash=selection_hash,
            chapter_content_hash=content_hash,
            hierarchy_build_id=self.PLACEHOLDER_HIERARCHY_BUILD,
            hierarchy_checksum=self.PLACEHOLDER_HIERARCHY_CHECKSUM,
            reading_progress_snapshot=progress,
            full_book=full_book,
            cutoff_chapter_number=cutoff,
            analysis_version_id=None,
            manifest_checksum=manifest_checksum,
            prompt_inputs=prompt_inputs,
            omitted_evidence_counts={},
            evidence_refs=[evidence],
            prompt_hash=frozen,
            schema_hash=frozen,
            decoding_hash=frozen,
            config_hash=frozen,
            model_lineage={"builder": "deterministic-plan02"},
            price_snapshot={},
        )
