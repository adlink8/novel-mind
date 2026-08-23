"""Reader Chat -> Pi SkillRun bridge.

The reader tables remain the public conversation/job API.  This module only
creates the runtime hand-off and projects a completed cited-answer artifact
back into those tables; it never calls a model provider.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.models.agent_runtime import SkillRegistry, SkillRun, SkillVersion
from app.models.reader_chat import (
    ReaderContextEvidenceRef,
    ReaderContextManifest,
    ReaderConversation,
    ReaderGenerationJob,
    ReaderMessage,
    ReaderMessageSelection,
)
from app.schemas.reader_chat import ReaderAnswerEnvelope
from app.services.agent_runtime.backfill import create_backfill_runs
from app.services.agent_runtime.registry import canonical_input_hash
from app.services.user_preference_memory import build_preference_context

READER_CHAT_ORIGIN = "reader_chat"
WAITING_FOR_EVIDENCE = "waiting_for_evidence"
WAITING_ANALYSIS = "waiting_analysis"
BACKFILL_TIMEOUT = timedelta(minutes=30)


@dataclass(frozen=True)
class BackfillRecoveryDecision:
    """Stable state-machine result used by the reader job recovery seam."""

    state: str
    reason: str
    pending_dimensions: tuple[str, ...] = ()


def evaluate_backfill_recovery(
    *,
    required_dimensions: Iterable[str],
    runs: Iterable[Mapping[str, Any] | Any],
    now: datetime | None = None,
    timeout: timedelta = BACKFILL_TIMEOUT,
) -> BackfillRecoveryDecision:
    """Classify backfill state without publishing an answer.

    A completed run is usable only after its domain materializer records a
    ``materialized:...`` reason.  This deliberately makes finalize and
    materialize two observable gates instead of treating artifact creation as
    sufficient evidence.
    """

    current = now or datetime.now(timezone.utc)
    required = tuple(dict.fromkeys(str(item) for item in required_dimensions))
    latest: dict[str, Mapping[str, Any] | Any] = {}
    for run in runs:
        dimension = _run_value(run, "backfill_dimension")
        if not dimension or dimension not in required:
            continue
        previous = latest.get(dimension)
        if previous is None or _run_time(run) >= _run_time(previous):
            latest[dimension] = run

    for dimension in required:
        run = latest.get(dimension)
        if run is None:
            return BackfillRecoveryDecision(
                "waiting", f"missing:{dimension}", (dimension,)
            )
        status = _run_value(run, "status")
        if status in {"failed", "cancelled"}:
            return BackfillRecoveryDecision("failed", f"backfill_{status}")
        if status == "completed":
            reason = str(_run_value(run, "status_reason") or "")
            if not reason.startswith("materialized:"):
                return BackfillRecoveryDecision(
                    "failed", "backfill_materialization_failed"
                )
            continue
        updated_at = _run_time(run)
        if updated_at and _as_utc(current) - updated_at > timeout:
            return BackfillRecoveryDecision("failed", "backfill_timeout")
        return BackfillRecoveryDecision("waiting", f"pending:{dimension}", (dimension,))

    return BackfillRecoveryDecision("ready", "all_backfills_materialized")


def _run_value(run: Mapping[str, Any] | Any, name: str) -> Any:
    if isinstance(run, Mapping):
        return run.get(name)
    return getattr(run, name, None)


def _run_time(run: Mapping[str, Any] | Any) -> datetime:
    value = _run_value(run, "updated_at") or _run_value(run, "created_at")
    return _as_utc(value) if value else datetime.min.replace(tzinfo=timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def enqueue_reader_chat_skill_run(
    job_id: int,
    *,
    sessions: async_sessionmaker[AsyncSession] = async_session_factory,
) -> SkillRun | None:
    """Turn one ordinary reader job into a queued Pi SkillRun.

    The function is intentionally the backend public hand-off seam.  It keeps
    ReaderGenerationJob durable and user-visible, while SkillRun is the only
    execution record consumed by agent-service.
    """

    async with sessions.begin() as session:
        job = await session.get(ReaderGenerationJob, job_id, with_for_update=True)
        if job is None or job.status in ("cancelled", "completed", "failed"):
            return None
        return await _enqueue_reader_skill_run_in_session(session, job)


async def _enqueue_reader_skill_run_in_session(
    session: AsyncSession, job: ReaderGenerationJob
) -> SkillRun | None:
    existing = await session.scalar(
        select(SkillRun)
        .where(
            SkillRun.origin == READER_CHAT_ORIGIN,
            SkillRun.user_message_id == job.user_message_id,
            SkillRun.status.in_(("queued", "running", "completed")),
        )
        .order_by(SkillRun.id.desc())
    )
    if existing is not None:
        return existing

    manifest = await session.scalar(
        select(ReaderContextManifest).where(
            ReaderContextManifest.user_message_id == job.user_message_id
        )
    )
    if manifest is None:
        job.status = "failed"
        job.status_reason = "reader_context_manifest_missing"
        job.error_code = "context_missing"
        return None

    unavailable = _unavailable_dimensions(manifest)
    if unavailable:
        required = _backfill_dimensions(unavailable)
        if not required:
            # 不可用维度没有任何 backfill 映射（如 knowledge /
            # relationship_observation）：诚实失败。绝不置 waiting_analysis:<空>
            # ——空维度列表永远不会 reconcile，任务将永久停摆。
            job.status = "failed"
            job.status_reason = "backfill_unavailable"
            job.error_code = "backfill_unavailable"
            return None
        await _create_evidence_backfill_runs(session, job, manifest)
        runs = await _backfill_runs_for_message(session, job, required)
        if set(required) - {run.backfill_dimension for run in runs}:
            job.status = "failed"
            job.status_reason = "backfill_unavailable"
            job.error_code = "backfill_unavailable"
            return None
        job.status = "paused_dependency"
        job.status_reason = f"{WAITING_ANALYSIS}:{','.join(required)}"
        job.error_code = None
        return None

    refs = list(
        (
            await session.scalars(
                select(ReaderContextEvidenceRef)
                .where(ReaderContextEvidenceRef.manifest_id == manifest.id)
                .order_by(ReaderContextEvidenceRef.sort_order)
            )
        ).all()
    )
    if not refs:
        job.status = "paused_dependency"
        job.status_reason = WAITING_FOR_EVIDENCE
        job.error_code = "evidence_unavailable"
        return None

    version = await session.scalar(
        select(SkillVersion)
        .join(SkillRegistry, SkillVersion.registry_id == SkillRegistry.id)
        .where(
            SkillVersion.owner_id == job.owner_id,
            SkillVersion.novel_id == job.novel_id,
            SkillVersion.name == "answer-reading-question",
            SkillVersion.status == "active",
            SkillRegistry.status == "active",
        )
        .order_by(SkillVersion.id.desc())
    )
    if version is None:
        job.status = "paused_dependency"
        job.status_reason = "answer_reading_question_skill_missing"
        job.error_code = "skill_unavailable"
        return None

    input_payload = await build_reader_skill_input(session, job)
    preference_context = input_payload.get("preference_context") or {}
    preference_memory_ids = list(preference_context.get("memory_ids") or [])
    input_hash = canonical_input_hash(input_payload)
    token = secrets.token_urlsafe(32)
    run = SkillRun(
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        skill_version_id=version.id,
        status="queued",
        branch=None,
        input=input_payload,
        input_hash=input_hash,
        frozen_manifest={
            "evidence_refs": [ref.evidence_key for ref in refs],
            "preference_memory_ids": preference_memory_ids,
        },
        budget_snapshot=dict(version.budget or {}),
        internal_token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        origin=READER_CHAT_ORIGIN,
        user_message_id=job.user_message_id,
    )
    session.add(run)
    await session.flush()
    job.status = "running"
    job.status_reason = f"pi_skill_run_queued:{run.id}"
    job.error_code = None
    job.model_lineage = {
        **dict(job.model_lineage or {}),
        "runtime": "pi",
        "skill_run_id": run.id,
        "preference_memory_ids": preference_memory_ids,
    }
    return run


async def build_reader_skill_input(
    session: AsyncSession, job: ReaderGenerationJob
) -> dict[str, Any]:
    message = await session.get(ReaderMessage, job.user_message_id)
    if message is None:
        raise ValueError("reader user message missing")

    payload: dict[str, Any] = {
        "question": message.body[:1000],
        "novel_id": job.novel_id,
    }
    preference_context = await build_preference_context(
        session, owner_id=message.owner_id
    )
    if preference_context["items"]:
        payload["preference_context"] = preference_context
    selection = await session.scalar(
        select(ReaderMessageSelection).where(
            ReaderMessageSelection.user_message_id == job.user_message_id
        )
    )
    if selection is not None:
        payload["selection"] = {
            "kind": "selection",
            "chapter_id": selection.chapter_id,
            "source_start": selection.source_start,
            "source_end": selection.source_end,
            "chapter_content_hash": selection.chapter_content_hash,
        }
    return payload


async def _create_evidence_backfill_runs(
    session: AsyncSession,
    job: ReaderGenerationJob,
    manifest: ReaderContextManifest,
) -> None:
    prompt_inputs = dict(manifest.prompt_inputs or {})
    source_status = dict(prompt_inputs.get("source_status") or {})
    unavailable = [
        dimension
        for dimension, state in source_status.items()
        if state in ("unavailable", "absent")
    ]
    if not unavailable:
        return
    await create_backfill_runs(
        session,
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        user_message_id=job.user_message_id,
        question=(await session.get(ReaderMessage, job.user_message_id)).body[:1000],
        unavailable_dimensions=unavailable,
    )


def _unavailable_dimensions(manifest: ReaderContextManifest) -> list[str]:
    source_status = dict(dict(manifest.prompt_inputs or {}).get("source_status") or {})
    return [
        dimension
        for dimension, state in source_status.items()
        if state in ("unavailable", "absent")
    ]


def _backfill_dimensions(unavailable: list[str]) -> tuple[str, ...]:
    from app.services.agent_runtime.backfill import pick_backfill_skills

    return tuple(dimension for _, dimension in pick_backfill_skills(unavailable))


async def _backfill_runs_for_message(
    session: AsyncSession,
    job: ReaderGenerationJob,
    dimensions: Iterable[str],
) -> list[SkillRun]:
    wanted = tuple(dimensions)
    if not wanted:
        return []
    return list(
        (
            await session.scalars(
                select(SkillRun).where(
                    SkillRun.owner_id == job.owner_id,
                    SkillRun.novel_id == job.novel_id,
                    SkillRun.user_message_id == job.user_message_id,
                    SkillRun.origin == "chat_backfill",
                    SkillRun.backfill_dimension.in_(wanted),
                )
            )
        ).all()
    )


async def reconcile_reader_chat_after_backfill(
    run_id: int,
    *,
    sessions: async_sessionmaker[AsyncSession] = async_session_factory,
) -> str:
    """Advance the waiting reader job after one backfill reaches a terminal state."""

    async with sessions.begin() as session:
        run = await session.get(SkillRun, run_id, with_for_update=True)
        if run is None:
            return "skipped:run_missing"
        return await _reconcile_reader_chat_after_backfill_in_session(session, run)


async def _reconcile_reader_chat_after_backfill_in_session(
    session: AsyncSession, run: SkillRun
) -> str:
    if run.origin != "chat_backfill" or run.user_message_id is None:
        return "skipped:not_chat_backfill"

    job = await session.scalar(
        select(ReaderGenerationJob)
        .where(
            ReaderGenerationJob.user_message_id == run.user_message_id,
            ReaderGenerationJob.owner_id == run.owner_id,
            ReaderGenerationJob.novel_id == run.novel_id,
        )
        .with_for_update()
    )
    if job is None:
        return "skipped:reader_job_missing"
    if not (
        job.status == "paused_dependency"
        and str(job.status_reason or "").startswith(f"{WAITING_ANALYSIS}:")
    ):
        return "skipped:reader_job_not_waiting"

    required = tuple(
        dim for dim in str(job.status_reason).split(":", 1)[1].split(",") if dim
    )
    if not required:
        # 历史遗留的 waiting_analysis:<空>：无维度可等待，诚实失败。
        job.status = "failed"
        job.status_reason = "backfill_unavailable"
        job.error_code = "backfill_unavailable"
        return "backfill_unavailable"
    runs = await _backfill_runs_for_message(session, job, required)
    decision = evaluate_backfill_recovery(required_dimensions=required, runs=runs)
    if decision.state == "failed":
        job.status = "failed"
        job.status_reason = f"{decision.reason}:{run.id}"[:160]
        job.error_code = decision.reason
        return decision.reason
    if decision.state == "waiting":
        pending = decision.pending_dimensions or required
        job.status = "paused_dependency"
        job.status_reason = f"{WAITING_ANALYSIS}:{','.join(pending)}"
        job.error_code = None
        return job.status_reason

    try:
        await _rebuild_reader_context_manifest(session, job)
    except Exception as exc:  # noqa: BLE001 - reader job records honest failure
        job.status = "failed"
        job.status_reason = "manifest_rebuild_failed"
        job.error_code = f"manifest_rebuild:{type(exc).__name__}"[:80]
        return job.status_reason

    queued = await _enqueue_reader_skill_run_in_session(session, job)
    if queued is None and job.status == "paused_dependency":
        # A fresh QueryPlan may reveal another missing dimension.  That is a
        # new observable waiting cycle, not an abstain answer.
        return job.status_reason or WAITING_ANALYSIS
    return (
        f"reader_skill_requeued:{queued.id}"
        if queued is not None
        else "reader_skill_not_queued"
    )


async def _rebuild_reader_context_manifest(
    session: AsyncSession, job: ReaderGenerationJob
) -> None:
    from app.models.novel import Novel
    from app.services.reader_chat.conversations import conversation_service
    from app.schemas.reader_chat import SelectionCoordinate

    novel = await session.get(Novel, job.novel_id)
    message = await session.get(ReaderMessage, job.user_message_id)
    manifest = await session.scalar(
        select(ReaderContextManifest).where(
            ReaderContextManifest.user_message_id == job.user_message_id
        )
    )
    if novel is None or message is None or manifest is None:
        raise ValueError("reader context inputs missing")

    selection_row = await session.scalar(
        select(ReaderMessageSelection).where(
            ReaderMessageSelection.user_message_id == job.user_message_id
        )
    )
    selection = None
    if selection_row is not None:
        selection = SelectionCoordinate(
            chapter_id=selection_row.chapter_id,
            source_start=selection_row.source_start,
            source_end=selection_row.source_end,
            selection_text=selection_row.selection_text,
            selection_text_hash=selection_row.selection_text_hash,
            chapter_content_hash=selection_row.chapter_content_hash,
        )

    progress = dict(manifest.reading_progress_snapshot or {})
    chapter_id = progress.get("chapter_id")
    if chapter_id is None:
        old_ref = await session.scalar(
            select(ReaderContextEvidenceRef)
            .where(ReaderContextEvidenceRef.manifest_id == manifest.id)
            .order_by(ReaderContextEvidenceRef.sort_order)
        )
        chapter_id = old_ref.chapter_id if old_ref is not None else None
    if selection is None and chapter_id is None:
        raise ValueError("chapter anchor unavailable for manifest rebuild")

    graph = await conversation_service._context_builder.build(  # noqa: SLF001
        session,
        novel=novel,
        owner_id=job.owner_id,
        conversation_id=job.conversation_id,
        selection=selection,
        body=message.body,
        chapter_id=None if selection is not None else int(chapter_id),
    )
    manifest.reading_progress_snapshot = dict(graph.reading_progress_snapshot)
    manifest.full_book = graph.full_book
    manifest.cutoff_chapter_number = graph.cutoff_chapter_number
    manifest.analysis_version_id = graph.analysis_version_id
    manifest.hierarchy_build_id = graph.hierarchy_build_id
    manifest.hierarchy_checksum = graph.hierarchy_checksum
    manifest.manifest_checksum = graph.manifest_checksum
    manifest.prompt_inputs = dict(graph.prompt_inputs)
    manifest.omitted_evidence_counts = dict(graph.omitted_evidence_counts)

    old_refs = {
        ref.evidence_key: ref
        for ref in (
            await session.scalars(
                select(ReaderContextEvidenceRef).where(
                    ReaderContextEvidenceRef.manifest_id == manifest.id
                )
            )
        ).all()
    }
    new_keys = {draft.evidence_key for draft in graph.evidence_refs}
    for key, ref in old_refs.items():
        if key not in new_keys:
            await session.delete(ref)
    for draft in graph.evidence_refs:
        ref = old_refs.get(draft.evidence_key)
        if ref is None:
            ref = ReaderContextEvidenceRef(
                manifest_id=manifest.id, evidence_key=draft.evidence_key
            )
            session.add(ref)
        ref.source_type = draft.source_type
        ref.source_id = draft.source_id
        ref.chapter_id = draft.chapter_id
        ref.chapter_number = draft.chapter_number
        ref.source_start = draft.source_start
        ref.source_end = draft.source_end
        ref.content_hash = draft.content_hash
        ref.excerpt = draft.excerpt
        ref.sort_order = draft.sort_order
        ref.version_lineage = dict(draft.version_lineage)

    job.context_manifest_checksum = graph.manifest_checksum
    job.prompt_hash = graph.prompt_hash
    job.schema_hash = graph.schema_hash
    job.decoding_hash = graph.decoding_hash
    job.config_hash = graph.config_hash
    job.model_lineage = dict(graph.model_lineage)
    job.price_snapshot = dict(graph.price_snapshot)


async def materialize_reader_chat_answer(
    session: AsyncSession,
    *,
    run: SkillRun,
    content: dict[str, Any],
) -> str:
    """Project a cited-answer artifact into its original ReaderConversation."""

    if run.origin != READER_CHAT_ORIGIN:
        return "skipped:not_reader_chat"
    if content.get("type") != "cited_answer":
        return "skipped:not_cited_answer"

    answer = ReaderAnswerEnvelope.model_validate(content.get("answer") or {})
    job = await session.scalar(
        select(ReaderGenerationJob)
        .where(
            ReaderGenerationJob.user_message_id == run.user_message_id,
            ReaderGenerationJob.owner_id == run.owner_id,
            ReaderGenerationJob.novel_id == run.novel_id,
        )
        .with_for_update()
    )
    if job is None:
        return "skipped:reader_job_missing"
    if job.status == "completed":
        return "materialized:reader_chat"

    manifest = await session.scalar(
        select(ReaderContextManifest).where(
            ReaderContextManifest.user_message_id == job.user_message_id
        )
    )
    refs = (
        list(
            (
                await session.scalars(
                    select(ReaderContextEvidenceRef)
                    .where(ReaderContextEvidenceRef.manifest_id == manifest.id)
                    .order_by(ReaderContextEvidenceRef.sort_order)
                )
            ).all()
        )
        if manifest is not None
        else []
    )
    ref_ids = {ref.evidence_key: ref.id for ref in refs}

    conversation = await session.get(
        ReaderConversation, job.conversation_id, with_for_update=True
    )
    if conversation is None:
        return "skipped:reader_conversation_missing"
    body = _answer_body(answer)
    assistant = ReaderMessage(
        conversation_id=job.conversation_id,
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        sequence=int(conversation.next_sequence),
        role="assistant",
        body=body,
        client_message_id=None,
        reply_to_message_id=job.user_message_id,
        content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )
    conversation.next_sequence = int(conversation.next_sequence) + 1
    session.add(assistant)
    await session.flush()
    for block in answer.answer_blocks:
        for evidence_key in block.evidence_refs:
            ref_id = ref_ids.get(evidence_key)
            if ref_id is not None:
                from app.models.reader_chat import ReaderMessageCitation

                session.add(
                    ReaderMessageCitation(
                        assistant_message_id=assistant.id,
                        block_id=block.block_id,
                        context_evidence_ref_id=ref_id,
                    )
                )
    job.status = "completed"
    job.status_reason = "published_by_pi"
    job.response_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    job.error_code = None
    return "materialized:reader_chat"


def _answer_body(answer: ReaderAnswerEnvelope) -> str:
    parts = [block.text for block in answer.answer_blocks]
    if answer.clarifying_question:
        parts.append(answer.clarifying_question)
    if answer.uncertainty is not None:
        parts.append(answer.uncertainty.explanation)
    for suggestion in answer.suggestion_candidates:
        parts.append(f"[suggestion:{suggestion.candidate_type}] {suggestion.proposal}")
    return "\n\n".join(parts) if parts else "(no content)"


__all__ = [
    "READER_CHAT_ORIGIN",
    "WAITING_FOR_EVIDENCE",
    "WAITING_ANALYSIS",
    "BackfillRecoveryDecision",
    "evaluate_backfill_recovery",
    "enqueue_reader_chat_skill_run",
    "reconcile_reader_chat_after_backfill",
    "materialize_reader_chat_answer",
]
