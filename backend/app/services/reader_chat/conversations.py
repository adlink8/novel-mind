"""
Owner-scoped multi-conversation lifecycle and durable message submission.

PostgreSQL is the authority for sequence, idempotency, manifests and jobs.
Context assembly is injected so Plan 03 can replace the deterministic builder
without changing lifecycle semantics.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.novel import Chapter, Novel
from app.models.reader_chat import (
    READER_JOB_NONTERMINAL_STATUSES,
    ReaderContextEvidenceRef,
    ReaderContextManifest,
    ReaderConversation,
    ReaderGenerationJob,
    ReaderMessage,
    ReaderMessageCitation,
    ReaderMessageSelection,
)
from app.schemas.reader_chat import (
    ConversationCreate,
    ConversationDetail,
    ConversationListItem,
    ConversationPatch,
    ConversationStatus,
    GenerationJobStatus,
    GenerationJobView,
    MessageAccepted,
    MessageCreate,
    MessageRole,
    MessageView,
    SelectionCoordinate,
    SelectionSummary,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return _sha256_text(canonical)


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

    selection_text: str
    selection_text_hash: str
    chapter_content_hash: str
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
        selection: SelectionCoordinate,
        body: str,
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
        selection: SelectionCoordinate,
        body: str,
    ) -> ContextGraph:
        from app.services.reader_chat.context import (
            SelectionValidationError,
            assemble_context_manifest,
            validate_selection,
        )
        from app.services.reader_chat.retrieval import (
            Phase09RelationshipObservationReader,
        )

        try:
            validated = await validate_selection(
                db,
                novel=novel,
                owner_id=owner_id,
                selection=selection,
            )
            manifest = await assemble_context_manifest(
                db,
                novel=novel,
                owner_id=owner_id,
                selection=validated,
                question=body,
                relationship_reader=Phase09RelationshipObservationReader(),
            )
        except SelectionValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.message) from exc

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
        return ContextGraph(
            selection_text=validated.selection_text,
            selection_text_hash=validated.selection_text_hash,
            chapter_content_hash=validated.chapter_content_hash,
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
        selection: SelectionCoordinate,
        body: str,
    ) -> ContextGraph:
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
            raise HTTPException(status_code=422, detail="selection offsets out of range")
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


class ConversationService:
    def __init__(self, context_builder: ContextBuilder | None = None) -> None:
        self._context_builder = context_builder or ProductionContextBuilder()

    # ------------------------------------------------------------------
    # Conversation CRUD
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        data: ConversationCreate,
    ) -> ConversationDetail:
        conv = ReaderConversation(
            owner_id=owner_id,
            novel_id=novel.id,
            title=data.title,
            status=ConversationStatus.ACTIVE.value,
            next_sequence=1,
            last_opened_at=_utc_now(),
        )
        db.add(conv)
        await db.flush()
        await db.refresh(conv)
        return await self._to_detail(db, conv)

    async def list_conversations(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[ConversationListItem], int]:
        filters = [
            ReaderConversation.owner_id == owner_id,
            ReaderConversation.novel_id == novel.id,
        ]
        if status in ("active", "archived"):
            filters.append(ReaderConversation.status == status)

        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(ReaderConversation).where(*filters)
                )
            ).scalar_one()
        )
        rows = (
            await db.execute(
                select(ReaderConversation)
                .where(*filters)
                .order_by(
                    ReaderConversation.last_opened_at.desc().nullslast(),
                    ReaderConversation.id.desc(),
                )
                .offset(skip)
                .limit(limit)
            )
        ).scalars().all()

        items = [await self._to_list_item(db, row) for row in rows]
        return items, total

    async def get_conversation(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        touch: bool = True,
    ) -> ConversationDetail:
        conv = await self._require_conversation(
            db, novel=novel, owner_id=owner_id, conversation_id=conversation_id
        )
        if touch:
            conv.last_opened_at = _utc_now()
            await db.flush()
            await db.refresh(conv)
        return await self._to_detail(db, conv)

    async def patch_conversation(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        data: ConversationPatch,
    ) -> ConversationDetail:
        conv = await self._require_conversation(
            db, novel=novel, owner_id=owner_id, conversation_id=conversation_id
        )
        if data.title is not None:
            conv.title = data.title
        if data.status is not None:
            conv.status = data.status.value
        await db.flush()
        await db.refresh(conv)
        return await self._to_detail(db, conv)

    async def delete_conversation(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
    ) -> None:
        conv = await self._require_conversation(
            db, novel=novel, owner_id=owner_id, conversation_id=conversation_id
        )
        # Cancel nonterminal jobs first (audit), then hard-delete cascade.
        jobs = (
            await db.execute(
                select(ReaderGenerationJob).where(
                    ReaderGenerationJob.conversation_id == conv.id,
                    ReaderGenerationJob.owner_id == owner_id,
                    ReaderGenerationJob.novel_id == novel.id,
                    ReaderGenerationJob.status.in_(READER_JOB_NONTERMINAL_STATUSES),
                )
            )
        ).scalars().all()
        for job in jobs:
            job.cancel_requested = True
            job.status = GenerationJobStatus.CANCELLED.value
            job.status_reason = "conversation_deleted"
            job.error_code = "conversation_deleted"
        await db.flush()
        await db.delete(conv)
        await db.flush()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def list_messages(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        after_sequence: int = 0,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[MessageView], int]:
        await self._require_conversation(
            db, novel=novel, owner_id=owner_id, conversation_id=conversation_id
        )
        filters = [
            ReaderMessage.owner_id == owner_id,
            ReaderMessage.novel_id == novel.id,
            ReaderMessage.conversation_id == conversation_id,
            ReaderMessage.sequence > after_sequence,
        ]
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(ReaderMessage).where(*filters)
                )
            ).scalar_one()
        )
        rows = (
            await db.execute(
                select(ReaderMessage)
                .where(*filters)
                .order_by(ReaderMessage.sequence.asc())
                .offset(skip)
                .limit(limit)
            )
        ).scalars().all()
        views = [await self._to_message_view(db, row) for row in rows]
        return views, total

    async def create_message(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        data: MessageCreate,
    ) -> MessageAccepted:
        # Idempotent fast path: existing client message.
        existing = (
            await db.execute(
                select(ReaderMessage).where(
                    ReaderMessage.conversation_id == conversation_id,
                    ReaderMessage.owner_id == owner_id,
                    ReaderMessage.novel_id == novel.id,
                    ReaderMessage.client_message_id == data.client_message_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return await self._accepted_from_message(db, existing)

        # Lock conversation row for monotonic sequence allocation.
        conv = (
            await db.execute(
                select(ReaderConversation)
                .where(
                    ReaderConversation.id == conversation_id,
                    ReaderConversation.owner_id == owner_id,
                    ReaderConversation.novel_id == novel.id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if conv.status == ConversationStatus.ARCHIVED.value:
            raise HTTPException(status_code=409, detail="archived conversation rejects messages")

        # Re-check idempotency under lock (concurrent duplicate).
        existing = (
            await db.execute(
                select(ReaderMessage).where(
                    ReaderMessage.conversation_id == conversation_id,
                    ReaderMessage.client_message_id == data.client_message_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return await self._accepted_from_message(db, existing)

        graph = await self._context_builder.build(
            db,
            novel=novel,
            owner_id=owner_id,
            conversation_id=conversation_id,
            selection=data.selection,
            body=data.body,
        )

        sequence = int(conv.next_sequence)
        message = ReaderMessage(
            conversation_id=conv.id,
            owner_id=owner_id,
            novel_id=novel.id,
            sequence=sequence,
            role=MessageRole.USER.value,
            body=data.body,
            client_message_id=data.client_message_id,
            content_hash=_sha256_text(data.body),
        )
        db.add(message)
        # IntegrityError (duplicate client_message_id) propagates to
        # create_message_safe's nested savepoint for idempotent recovery.
        await db.flush()

        conv.next_sequence = sequence + 1
        conv.last_opened_at = _utc_now()

        selection_row = ReaderMessageSelection(
            user_message_id=message.id,
            conversation_id=conv.id,
            chapter_id=data.selection.chapter_id,
            source_start=data.selection.source_start,
            source_end=data.selection.source_end,
            selection_text=graph.selection_text,
            selection_text_hash=graph.selection_text_hash,
            chapter_content_hash=graph.chapter_content_hash,
            hierarchy_build_id=graph.hierarchy_build_id,
            hierarchy_checksum=graph.hierarchy_checksum,
        )
        db.add(selection_row)

        manifest = ReaderContextManifest(
            user_message_id=message.id,
            conversation_id=conv.id,
            reading_progress_snapshot=graph.reading_progress_snapshot,
            full_book=graph.full_book,
            cutoff_chapter_number=graph.cutoff_chapter_number,
            analysis_version_id=graph.analysis_version_id,
            hierarchy_build_id=graph.hierarchy_build_id,
            hierarchy_checksum=graph.hierarchy_checksum,
            manifest_checksum=graph.manifest_checksum,
            prompt_inputs=graph.prompt_inputs,
            omitted_evidence_counts=graph.omitted_evidence_counts,
        )
        db.add(manifest)
        await db.flush()

        for ref in graph.evidence_refs:
            db.add(
                ReaderContextEvidenceRef(
                    manifest_id=manifest.id,
                    evidence_key=ref.evidence_key,
                    source_type=ref.source_type,
                    source_id=ref.source_id,
                    chapter_id=ref.chapter_id,
                    chapter_number=ref.chapter_number,
                    source_start=ref.source_start,
                    source_end=ref.source_end,
                    content_hash=ref.content_hash,
                    excerpt=ref.excerpt,
                    sort_order=ref.sort_order,
                    version_lineage=ref.version_lineage,
                )
            )

        job = ReaderGenerationJob(
            conversation_id=conv.id,
            owner_id=owner_id,
            novel_id=novel.id,
            user_message_id=message.id,
            status=GenerationJobStatus.QUEUED.value,
            prompt_hash=graph.prompt_hash,
            schema_hash=graph.schema_hash,
            context_manifest_checksum=graph.manifest_checksum,
            model_lineage=graph.model_lineage,
            decoding_hash=graph.decoding_hash,
            config_hash=graph.config_hash,
            price_snapshot=graph.price_snapshot,
        )
        db.add(job)
        await db.flush()
        await db.refresh(message)
        await db.refresh(job)

        return MessageAccepted(
            message=await self._to_message_view(db, message),
            job=self._to_job_view(job),
        )

    async def create_message_safe(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        data: MessageCreate,
    ) -> MessageAccepted:
        """create_message with IntegrityError recovery for client idempotency."""

        try:
            async with db.begin_nested():
                return await self.create_message(
                    db,
                    novel=novel,
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    data=data,
                )
        except IntegrityError:
            existing = (
                await db.execute(
                    select(ReaderMessage).where(
                        ReaderMessage.conversation_id == conversation_id,
                        ReaderMessage.owner_id == owner_id,
                        ReaderMessage.novel_id == novel.id,
                        ReaderMessage.client_message_id == data.client_message_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise HTTPException(status_code=409, detail="message conflict") from None
            return await self._accepted_from_message(db, existing)

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    async def get_job(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        job_id: int,
    ) -> GenerationJobView:
        job = await self._require_job(
            db,
            novel=novel,
            owner_id=owner_id,
            conversation_id=conversation_id,
            job_id=job_id,
        )
        return self._to_job_view(job)

    async def cancel_job(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        job_id: int,
    ) -> GenerationJobView:
        job = await self._require_job(
            db,
            novel=novel,
            owner_id=owner_id,
            conversation_id=conversation_id,
            job_id=job_id,
        )
        job.cancel_requested = True
        if job.status in READER_JOB_NONTERMINAL_STATUSES:
            job.status = GenerationJobStatus.CANCELLED.value
            job.status_reason = "user_cancel"
        await db.flush()
        await db.refresh(job)
        return self._to_job_view(job)

    async def retry_job(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        job_id: int,
    ) -> GenerationJobView:
        """
        Resume an eligible terminal/paused job with the original manifest.

        Full worker semantics arrive in Plan 04; lifecycle surface re-queues safely.
        """
        job = await self._require_job(
            db,
            novel=novel,
            owner_id=owner_id,
            conversation_id=conversation_id,
            job_id=job_id,
        )
        eligible = {
            GenerationJobStatus.CANCELLED.value,
            GenerationJobStatus.FAILED.value,
            GenerationJobStatus.FAILED_VALIDATION.value,
            GenerationJobStatus.PAUSED_BUDGET.value,
            GenerationJobStatus.PAUSED_DEPENDENCY.value,
        }
        if job.status not in eligible:
            raise HTTPException(status_code=409, detail="job not eligible for retry")
        # Ensure original manifest still exists (immutable retry contract).
        manifest = (
            await db.execute(
                select(ReaderContextManifest).where(
                    ReaderContextManifest.user_message_id == job.user_message_id
                )
            )
        ).scalar_one_or_none()
        if manifest is None:
            raise HTTPException(status_code=409, detail="original manifest missing")
        if manifest.manifest_checksum != job.context_manifest_checksum:
            raise HTTPException(status_code=409, detail="manifest checksum mismatch")

        # One nonterminal job per user message (partial unique index).
        other = (
            await db.execute(
                select(ReaderGenerationJob).where(
                    ReaderGenerationJob.user_message_id == job.user_message_id,
                    ReaderGenerationJob.id != job.id,
                    ReaderGenerationJob.status.in_(READER_JOB_NONTERMINAL_STATUSES),
                )
            )
        ).scalar_one_or_none()
        if other is not None:
            raise HTTPException(status_code=409, detail="nonterminal job already exists")

        job.status = GenerationJobStatus.QUEUED.value
        job.status_reason = "retry"
        job.cancel_requested = False
        job.error_code = None
        job.retry_count = int(job.retry_count or 0) + 1
        job.lease_id = None
        job.lease_expires_at = None
        await db.flush()
        await db.refresh(job)
        return self._to_job_view(job)

    # ------------------------------------------------------------------
    # Scope helpers
    # ------------------------------------------------------------------

    async def _require_conversation(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
    ) -> ReaderConversation:
        conv = (
            await db.execute(
                select(ReaderConversation).where(
                    ReaderConversation.id == conversation_id,
                    ReaderConversation.owner_id == owner_id,
                    ReaderConversation.novel_id == novel.id,
                )
            )
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return conv

    async def _require_job(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        conversation_id: int,
        job_id: int,
    ) -> ReaderGenerationJob:
        await self._require_conversation(
            db, novel=novel, owner_id=owner_id, conversation_id=conversation_id
        )
        job = (
            await db.execute(
                select(ReaderGenerationJob).where(
                    ReaderGenerationJob.id == job_id,
                    ReaderGenerationJob.conversation_id == conversation_id,
                    ReaderGenerationJob.owner_id == owner_id,
                    ReaderGenerationJob.novel_id == novel.id,
                )
            )
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    async def _last_message_meta(
        self, db: AsyncSession, conversation_id: int
    ) -> tuple[int | None, str | None, datetime | None]:
        row = (
            await db.execute(
                select(ReaderMessage)
                .where(ReaderMessage.conversation_id == conversation_id)
                .order_by(ReaderMessage.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None, None, None
        return row.sequence, row.role, row.created_at

    async def _to_list_item(
        self, db: AsyncSession, conv: ReaderConversation
    ) -> ConversationListItem:
        last_seq, last_role, last_at = await self._last_message_meta(db, conv.id)
        return ConversationListItem(
            id=conv.id,
            novel_id=conv.novel_id,
            title=conv.title,
            status=ConversationStatus(conv.status),
            next_sequence=conv.next_sequence,
            last_opened_at=conv.last_opened_at,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            last_message_sequence=last_seq,
            last_message_role=MessageRole(last_role) if last_role else None,
            last_message_at=last_at,
        )

    async def _to_detail(
        self, db: AsyncSession, conv: ReaderConversation
    ) -> ConversationDetail:
        item = await self._to_list_item(db, conv)
        return ConversationDetail.model_validate(item.model_dump())

    def _to_job_view(self, job: ReaderGenerationJob) -> GenerationJobView:
        return GenerationJobView(
            id=job.id,
            user_message_id=job.user_message_id,
            status=GenerationJobStatus(job.status),
            status_reason=job.status_reason,
            cancel_requested=bool(job.cancel_requested),
            retry_count=int(job.retry_count or 0),
            error_code=job.error_code,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    async def _to_message_view(
        self, db: AsyncSession, message: ReaderMessage
    ) -> MessageView:
        selection_summary: SelectionSummary | None = None
        if message.role == MessageRole.USER.value:
            sel = (
                await db.execute(
                    select(ReaderMessageSelection).where(
                        ReaderMessageSelection.user_message_id == message.id
                    )
                )
            ).scalar_one_or_none()
            if sel is not None:
                selection_summary = SelectionSummary(
                    chapter_id=sel.chapter_id,
                    source_start=sel.source_start,
                    source_end=sel.source_end,
                    selection_text_hash=sel.selection_text_hash,
                    chapter_content_hash=sel.chapter_content_hash,
                )

        job_view: GenerationJobView | None = None
        if message.role == MessageRole.USER.value:
            job = (
                await db.execute(
                    select(ReaderGenerationJob)
                    .where(ReaderGenerationJob.user_message_id == message.id)
                    .order_by(ReaderGenerationJob.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if job is not None:
                job_view = self._to_job_view(job)

        citations: list = []
        if message.role == MessageRole.ASSISTANT.value:
            # Citations loaded for replay; citation rows join evidence for keys.
            cite_rows = (
                await db.execute(
                    select(ReaderMessageCitation).where(
                        ReaderMessageCitation.assistant_message_id == message.id
                    )
                )
            ).scalars().all()
            for cite in cite_rows:
                ref = await db.get(ReaderContextEvidenceRef, cite.context_evidence_ref_id)
                if ref is None:
                    continue
                from app.schemas.reader_chat import CitationView

                citations.append(
                    CitationView(
                        block_id=cite.block_id,
                        evidence_key=ref.evidence_key,
                        context_evidence_ref_id=ref.id,
                        chapter_id=ref.chapter_id,
                        source_start=ref.source_start,
                        source_end=ref.source_end,
                    )
                )

        return MessageView(
            id=message.id,
            conversation_id=message.conversation_id,
            sequence=message.sequence,
            role=MessageRole(message.role),
            body=message.body,
            client_message_id=message.client_message_id,
            reply_to_message_id=message.reply_to_message_id,
            selection=selection_summary,
            citations=citations,
            generation_job=job_view,
            created_at=message.created_at,
        )

    async def _accepted_from_message(
        self, db: AsyncSession, message: ReaderMessage
    ) -> MessageAccepted:
        job = (
            await db.execute(
                select(ReaderGenerationJob)
                .where(ReaderGenerationJob.user_message_id == message.id)
                .order_by(ReaderGenerationJob.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=500, detail="message missing generation job")
        return MessageAccepted(
            message=await self._to_message_view(db, message),
            job=self._to_job_view(job),
        )


conversation_service = ConversationService()
