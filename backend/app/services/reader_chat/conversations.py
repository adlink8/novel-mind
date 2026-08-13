"""
Owner-scoped multi-conversation lifecycle and durable message submission.

PostgreSQL is the authority for sequence, idempotency, manifests and jobs.
Context assembly is injected so Plan 03 can replace the deterministic builder
without changing lifecycle semantics.

拆分说明（refactor split）：本模块保留服务本体 —— ``ConversationService``
（CRUD / 消息 / 作业 / 视图）与全局单例 ``conversation_service``。上下文构建
器族（``anchor_view_from_prompt_inputs`` / ``EvidenceRefDraft`` /
``ContextGraph`` / ``ContextBuilder`` / ``ProductionContextBuilder`` /
``DeterministicContextBuilder`` + ``_sha256_*`` 哈希工具）拆到同目录
``conversations_builders`` 模块；本模块显式 re-export 全部同名符号，
``from app.services.reader_chat.conversations import X`` 的 import surface 不变。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
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
from app.services.user_preference_memory import extract_from_persisted_message
from app.schemas.reader_chat import (
    ChapterRangeAnchor,
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
    QueryPlanTraceView,
    SelectionSummary,
)

# ────────────────────────── 上下文构建器族（拆分后 re-export） ──────────────────────────
from app.services.reader_chat.conversations_builders import (
    ContextBuilder,
    ContextGraph,
    DeterministicContextBuilder,
    EvidenceRefDraft,
    ProductionContextBuilder,
    _sha256_json,
    _sha256_text,
    anchor_view_from_prompt_inputs,
)

__all__ = [
    "_sha256_text",
    "_sha256_json",
    "anchor_view_from_prompt_inputs",
    "EvidenceRefDraft",
    "ContextGraph",
    "ContextBuilder",
    "ProductionContextBuilder",
    "DeterministicContextBuilder",
    "ConversationService",
    "conversation_service",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
            (
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
            )
            .scalars()
            .all()
        )

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
            (
                await db.execute(
                    select(ReaderGenerationJob).where(
                        ReaderGenerationJob.conversation_id == conv.id,
                        ReaderGenerationJob.owner_id == owner_id,
                        ReaderGenerationJob.novel_id == novel.id,
                        ReaderGenerationJob.status.in_(READER_JOB_NONTERMINAL_STATUSES),
                    )
                )
            )
            .scalars()
            .all()
        )
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
            (
                await db.execute(
                    select(ReaderMessage)
                    .where(*filters)
                    .order_by(ReaderMessage.sequence.asc())
                    .offset(skip)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
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
            raise HTTPException(
                status_code=409, detail="archived conversation rejects messages"
            )

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

        build_kwargs: dict[str, Any] = {
            "novel": novel,
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "selection": data.selection,
            "body": data.body,
        }
        if data.selection is None:
            build_kwargs["chapter_id"] = data.chapter_id
        if data.chapter_range is not None:
            build_kwargs["chapter_range"] = data.chapter_range
        graph = await self._context_builder.build(db, **build_kwargs)

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

        if data.selection is not None:
            if (
                graph.selection_text is None
                or graph.selection_text_hash is None
                or graph.chapter_content_hash is None
            ):
                raise HTTPException(
                    status_code=500, detail="selection context incomplete"
                )
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
        await extract_from_persisted_message(db, message.id)

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
                raise HTTPException(
                    status_code=409, detail="message conflict"
                ) from None
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
        if (
            job.status == GenerationJobStatus.PAUSED_DEPENDENCY.value
            and str(job.status_reason or "").startswith("waiting_analysis:")
        ):
            # The GET path is also a bounded recovery checkpoint: a stale
            # queued/running backfill cannot leave the public reader job
            # waiting forever when no new poller event arrives.
            from app.models.agent_runtime import SkillRun
            from app.services.agent_runtime.reader_bridge import (
                _reconcile_reader_chat_after_backfill_in_session,
            )

            trigger = await db.scalar(
                select(SkillRun)
                .where(
                    SkillRun.origin == "chat_backfill",
                    SkillRun.user_message_id == job.user_message_id,
                )
                .order_by(SkillRun.id.desc())
            )
            if trigger is not None:
                await _reconcile_reader_chat_after_backfill_in_session(db, trigger)
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
            raise HTTPException(
                status_code=409, detail="nonterminal job already exists"
            )

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

    async def _queryplan_view(
        self, db: AsyncSession, user_message_id: int
    ) -> QueryPlanTraceView | None:
        """Rehydrate the shared QueryPlan trace from the frozen manifest (26-04)."""
        manifest_row = (
            await db.execute(
                select(ReaderContextManifest).where(
                    ReaderContextManifest.user_message_id == user_message_id
                )
            )
        ).scalar_one_or_none()
        if manifest_row is None:
            return None
        raw = (manifest_row.prompt_inputs or {}).get("queryplan")
        if not isinstance(raw, dict):
            return None
        try:
            return QueryPlanTraceView.model_validate(raw)
        except ValidationError:
            return None

    async def _to_message_view(
        self, db: AsyncSession, message: ReaderMessage
    ) -> MessageView:
        selection_summary: SelectionSummary | None = None
        anchor: ChapterRangeAnchor | None = None
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
            else:
                # chapter_range anchors persist inside the frozen manifest's
                # prompt_inputs (no new columns / migrations); echo them back.
                manifest_row = (
                    await db.execute(
                        select(ReaderContextManifest).where(
                            ReaderContextManifest.user_message_id == message.id
                        )
                    )
                ).scalar_one_or_none()
                if manifest_row is not None:
                    anchor = anchor_view_from_prompt_inputs(
                        dict(manifest_row.prompt_inputs or {})
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
        queryplan: QueryPlanTraceView | None = None
        if message.role == MessageRole.USER.value:
            queryplan = await self._queryplan_view(db, message.id)
        elif message.reply_to_message_id is not None:
            queryplan = await self._queryplan_view(db, message.reply_to_message_id)
        if message.role == MessageRole.ASSISTANT.value:
            # Citations loaded for replay; citation rows join evidence for keys.
            cite_rows = (
                (
                    await db.execute(
                        select(ReaderMessageCitation).where(
                            ReaderMessageCitation.assistant_message_id == message.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for cite in cite_rows:
                ref = await db.get(
                    ReaderContextEvidenceRef, cite.context_evidence_ref_id
                )
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

        # 问答按需分析（chat_backfill）：该 user message 触发的后台分析 run。
        backfill_runs: list = []
        if message.role == MessageRole.USER.value:
            from app.models.agent_runtime import SkillRun, SkillVersion
            from app.schemas.reader_chat import BackfillRunView

            backfill_rows = (
                (
                    await db.execute(
                        select(SkillRun)
                        .where(
                            SkillRun.user_message_id == message.id,
                            SkillRun.origin == "chat_backfill",
                        )
                        .order_by(SkillRun.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            for run in backfill_rows:
                skill_name = "answer-reading-question"
                if run.skill_version_id is not None:
                    sv = await db.get(SkillVersion, run.skill_version_id)
                    if sv is not None:
                        skill_name = sv.name
                backfill_runs.append(
                    BackfillRunView(
                        run_id=run.id,
                        skill_name=skill_name,
                        status=run.status,
                        backfill_dimension=run.backfill_dimension,
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
            anchor=anchor,
            citations=citations,
            generation_job=job_view,
            queryplan=queryplan,
            backfill_runs=backfill_runs,
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
            raise HTTPException(
                status_code=500, detail="message missing generation job"
            )
        return MessageAccepted(
            message=await self._to_message_view(db, message),
            job=self._to_job_view(job),
        )


conversation_service = ConversationService()
