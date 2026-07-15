"""Durable cited-answer worker for reader-chat generation jobs.

Authority boundaries:
- Reads frozen context manifests only (never rebuilds on retry).
- Writes only reader_* chat tables (messages, citations, jobs, attempts, budgets).
- No timeline / relationship / clue domain mutation imports or tool calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.models.reader_chat import (
    ReaderContextEvidenceRef,
    ReaderContextManifest,
    ReaderGenerationJob,
    ReaderMessage,
    ReaderMessageCitation,
    ReaderModelCallAttempt,
)
from app.schemas.reader_chat import ReaderAnswerEnvelope
from app.services.reader_chat.budget import (
    DEFAULT_CONVERSATION_POLICY,
    DEFAULT_NOVEL_POLICY,
    BudgetExceeded,
    BudgetPolicy,
    DualBudgetGate,
    DualBudgetRepository,
)
from app.services.reader_chat.gateway import (
    DependencyPaused,
    ModelCallFailed,
    ModelDeployment,
    ReaderChatGateway,
    StructuredOutputRejected,
    UnknownPricing,
    business_validate_answer,
    canonical_hash,
)

logger = logging.getLogger(__name__)

# Hashes / IDs only — never raw prompt, selection, evidence, or model output.
_SAFE_LOG = logging.getLogger("reader_chat.worker.audit")

PROMPT_PATH = (
    Path(__file__).resolve().parents[3] / "prompts" / "reader_chat_answer.v1.txt"
)
SCHEMA_VERSION = "reader-answer.v1"
MAX_INPUT_TOKENS = 8_000
MAX_OUTPUT_TOKENS = 2_000
LEASE_MINUTES = 5
DECODING = {"temperature": 0.1, "max_tokens": MAX_OUTPUT_TOKENS}


class ReaderChatWorkerError(RuntimeError):
    pass


class ReaderChatCancellationRequested(RuntimeError):
    pass


@dataclass(frozen=True)
class ReaderChatWorkerRuntime:
    sessions: async_sessionmaker[AsyncSession]
    gateway: ReaderChatGateway
    deployment: ModelDeployment
    system_prompt: str = field(default_factory=lambda: _load_prompt())
    conversation_policy: BudgetPolicy = field(
        default_factory=lambda: DEFAULT_CONVERSATION_POLICY
    )
    novel_policy: BudgetPolicy = field(default_factory=lambda: DEFAULT_NOVEL_POLICY)
    max_input_tokens: int = MAX_INPUT_TOKENS
    max_output_tokens: int = MAX_OUTPUT_TOKENS


class _LiteLLMTransport:
    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        from app.services import ai_service as ai_service_module

        model = kwargs.get("model")
        messages = list(kwargs.get("messages") or [])
        timeout = float(kwargs.get("timeout") or 60)
        max_tokens = int(kwargs.get("max_tokens") or MAX_OUTPUT_TOKENS)
        # Explicit: no remote conversation/thread id, no retries, no stream.
        # Do not pass provider-specific kwargs (timeout/thread ids); keep one frozen call surface.
        _ = timeout
        response = await ai_service_module.ai_service.chat(
            messages=messages,
            model=model,
            temperature=float(DECODING["temperature"]),
            max_tokens=max_tokens,
            stream=False,
        )
        usage = getattr(response, "usage", {}) or {}
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        content = ""
        if hasattr(response, "choices") and response.choices:
            content = response.choices[0].message.content or ""
        elif isinstance(response, dict):
            content = str(response.get("content") or "")
            usage = response.get("usage") or usage
        return {
            "id": getattr(response, "id", None),
            "content": content,
            "usage": usage,
        }


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def resolve_reader_chat_deployment() -> ModelDeployment:
    """Resolve and freeze one balanced deployment for reader_chat (no fallback)."""

    from app.services.ai_router import ai_router

    tier = ai_router.route_task("reader_chat")
    provider = tier.provider
    model_id = tier.model_id
    # Approximate per-million from cost_per_1k (×1000).
    per_million = Decimal(str(tier.cost_per_1k)) * Decimal(1000)
    return ModelDeployment(
        provider=provider,
        model_id=model_id,
        revision=model_id,
        supports_structured_output=True,
        input_price_per_million=per_million if per_million > 0 else Decimal("0.15"),
        output_price_per_million=(
            per_million * Decimal(3) if per_million > 0 else Decimal("0.60")
        ),
    )


def production_runtime() -> ReaderChatWorkerRuntime:
    deployment = resolve_reader_chat_deployment()
    sessions = async_session_factory
    return ReaderChatWorkerRuntime(
        sessions=sessions,
        gateway=ReaderChatGateway(
            _LiteLLMTransport(),
            persistence=DualBudgetRepository(sessions),
        ),
        deployment=deployment,
    )


async def dispatch_reader_chat_job(job_id: int) -> None:
    """BackgroundTasks entrypoint; durable lease makes repeated dispatch safe."""

    await run_reader_chat_worker(job_id, runtime=production_runtime())


async def run_reader_chat_worker(
    job_id: int, *, runtime: ReaderChatWorkerRuntime
) -> None:
    lease_id = uuid.uuid4().hex
    if not await _claim_job(runtime.sessions, job_id, lease_id):
        return
    try:
        await _raise_if_cancel_requested(runtime.sessions, job_id)
        context = await _load_frozen_context(runtime.sessions, job_id)
        await _raise_if_cancel_requested(runtime.sessions, job_id)

        # Exact recovery: prior succeeded attempt with envelope, no assistant yet.
        recovered = await _try_exact_recovery(runtime, job_id, context)
        if recovered:
            return

        messages, prompt_hash, schema_hash, decoding_hash, config_hash = (
            _build_messages(runtime, context)
        )
        await _freeze_lineage(
            runtime.sessions,
            job_id,
            runtime.deployment,
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
            decoding_hash=decoding_hash,
            config_hash=config_hash,
            context_manifest_checksum=context["manifest_checksum"],
        )

        await _raise_if_cancel_requested(runtime.sessions, job_id)

        budget = DualBudgetGate(
            conversation_policy=runtime.conversation_policy,
            novel_policy=runtime.novel_policy,
        )
        try:
            result = await runtime.gateway.generate(
                deployment=runtime.deployment,
                messages=messages,
                allowed_evidence_ids=set(context["allowed_evidence_ids"]),
                budget=budget,
                job_id=job_id,
                max_input_tokens=runtime.max_input_tokens,
                max_output_tokens=runtime.max_output_tokens,
                cache_key=f"reader_chat:{job_id}:{prompt_hash[:16]}",
            )
        except UnknownPricing as exc:
            await _finish_job(
                runtime.sessions, job_id, "paused_budget", "unknown_pricing", str(exc)
            )
            return
        except BudgetExceeded as exc:
            await _finish_job(
                runtime.sessions, job_id, "paused_budget", "budget_exceeded", str(exc)
            )
            return
        except DependencyPaused as exc:
            await _finish_job(
                runtime.sessions,
                job_id,
                "paused_dependency",
                "dependency_paused",
                str(exc),
            )
            return
        except StructuredOutputRejected as exc:
            await _finish_job(
                runtime.sessions,
                job_id,
                "failed_validation",
                "validation_failed",
                str(exc)[:160],
            )
            return
        except ModelCallFailed as exc:
            await _finish_job(
                runtime.sessions,
                job_id,
                "paused_dependency",
                "provider_outcome_unknown",
                str(exc)[:160],
            )
            return

        # Post-call cancel: settle already done in gateway; discard response.
        if await _is_cancel_requested(runtime.sessions, job_id):
            await _finish_job(
                runtime.sessions,
                job_id,
                "cancelled",
                "user_cancel",
                "cancelled_after_call",
            )
            _SAFE_LOG.info(
                "reader_chat job cancelled after call job_id=%s response_hash=%s",
                job_id,
                result.response_hash,
            )
            return

        await _publish_assistant(
            runtime.sessions,
            job_id=job_id,
            envelope=result.output,
            response_hash=result.response_hash,
            evidence_key_to_ref_id=context["evidence_key_to_ref_id"],
            user_message_id=context["user_message_id"],
            conversation_id=context["conversation_id"],
            owner_id=context["owner_id"],
            novel_id=context["novel_id"],
        )
        _SAFE_LOG.info(
            "reader_chat job completed job_id=%s response_hash=%s blocks=%s",
            job_id,
            result.response_hash,
            len(result.output.answer_blocks),
        )
    except ReaderChatCancellationRequested:
        await _finish_job(
            runtime.sessions, job_id, "cancelled", "user_cancel", "cancel_requested"
        )
    except Exception as exc:
        await _finish_job(
            runtime.sessions, job_id, "failed", type(exc).__name__, type(exc).__name__
        )
        raise


async def _claim_job(
    sessions: async_sessionmaker[AsyncSession], job_id: int, lease_id: str
) -> bool:
    async with sessions.begin() as session:
        job = await session.get(ReaderGenerationJob, job_id, with_for_update=True)
        if job is None:
            return False
        claimable = {
            "queued",
            "running",
            "paused_budget",
            "paused_dependency",
        }
        if job.status not in claimable:
            return False
        now = datetime.now(UTC)
        if (
            job.status == "running"
            and job.lease_id
            and job.lease_id != lease_id
            and job.lease_expires_at
            and job.lease_expires_at > now
        ):
            # Active lease held by another worker.
            return False
        if job.cancel_requested:
            job.status = "cancelled"
            job.status_reason = "user_cancel"
            job.error_code = "user_cancel"
            return False
        job.lease_id = lease_id
        job.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
        job.heartbeat_at = now
        job.status = "running"
        return True


async def _is_cancel_requested(
    sessions: async_sessionmaker[AsyncSession], job_id: int
) -> bool:
    async with sessions() as session:
        flag = await session.scalar(
            select(ReaderGenerationJob.cancel_requested).where(
                ReaderGenerationJob.id == job_id
            )
        )
    return bool(flag)


async def _raise_if_cancel_requested(
    sessions: async_sessionmaker[AsyncSession], job_id: int
) -> None:
    if await _is_cancel_requested(sessions, job_id):
        raise ReaderChatCancellationRequested


async def _load_frozen_context(
    sessions: async_sessionmaker[AsyncSession], job_id: int
) -> dict[str, Any]:
    async with sessions() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        if job is None:
            raise ReaderChatWorkerError("job missing")
        user_message = await session.get(ReaderMessage, job.user_message_id)
        if user_message is None:
            raise ReaderChatWorkerError("user message missing")
        manifest = await session.scalar(
            select(ReaderContextManifest).where(
                ReaderContextManifest.user_message_id == job.user_message_id
            )
        )
        if manifest is None:
            raise ReaderChatWorkerError("manifest missing")
        if manifest.manifest_checksum != job.context_manifest_checksum:
            raise ReaderChatWorkerError("manifest checksum mismatch")
        refs = list(
            (
                await session.scalars(
                    select(ReaderContextEvidenceRef)
                    .where(ReaderContextEvidenceRef.manifest_id == manifest.id)
                    .order_by(ReaderContextEvidenceRef.sort_order)
                )
            ).all()
        )
        # Load frozen rows only — never re-assemble against current reading progress.
        evidence = [
            {
                "evidence_key": r.evidence_key,
                "source_type": r.source_type,
                "source_id": r.source_id,
                "chapter_id": r.chapter_id,
                "chapter_number": r.chapter_number,
                "source_start": r.source_start,
                "source_end": r.source_end,
                "content_hash": r.content_hash,
                "excerpt": r.excerpt,
                "sort_order": r.sort_order,
                "version_lineage": dict(r.version_lineage or {}),
            }
            for r in refs
        ]
        prompt_inputs = dict(manifest.prompt_inputs or {})
        allowed = list(
            prompt_inputs.get("allowed_evidence_ids")
            or [r.evidence_key for r in refs]
        )
        # Dialogue framing is non-evidence (D-05).
        dialogue = list(
            prompt_inputs.get("prior_dialogue")
            or (prompt_inputs.get("dialogue_framing") or {}).get("messages")
            or []
        )
        return {
            "job_id": job.id,
            "user_message_id": user_message.id,
            "user_body": user_message.body,
            "conversation_id": job.conversation_id,
            "owner_id": job.owner_id,
            "novel_id": job.novel_id,
            "manifest_checksum": manifest.manifest_checksum,
            "evidence": evidence,
            "allowed_evidence_ids": list(allowed),
            "evidence_key_to_ref_id": {r.evidence_key: r.id for r in refs},
            "dialogue": dialogue,
            "prompt_inputs": prompt_inputs,
        }


def _build_messages(
    runtime: ReaderChatWorkerRuntime, context: dict[str, Any]
) -> tuple[list[dict[str, str]], str, str, str, str]:
    evidence_payload = list(context["evidence"])
    user_payload = {
        "question": context["user_body"],
        "allowed_evidence_ids": context["allowed_evidence_ids"],
        "evidence": evidence_payload,
        "conversational_framing_not_evidence": context["dialogue"],
        "schema_version": SCHEMA_VERSION,
    }
    user_content = (
        "UNTRUSTED_DATA_BEGIN\n"
        + json.dumps(user_payload, ensure_ascii=False, sort_keys=True)
        + "\nUNTRUSTED_DATA_END\n"
        "Return exactly one JSON object matching reader-answer.v1."
    )
    messages = [
        {"role": "system", "content": runtime.system_prompt},
        {"role": "user", "content": user_content},
    ]
    prompt_hash = canonical_hash(
        {
            "system": runtime.system_prompt,
            "user_payload": user_payload,
            "manifest_checksum": context["manifest_checksum"],
        }
    )
    schema_hash = hashlib.sha256(
        json.dumps(
            ReaderAnswerEnvelope.model_json_schema(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    decoding_hash = canonical_hash(DECODING)
    config_hash = canonical_hash(
        {
            "max_input_tokens": runtime.max_input_tokens,
            "max_output_tokens": runtime.max_output_tokens,
            "task": "reader_chat",
        }
    )
    return messages, prompt_hash, schema_hash, decoding_hash, config_hash


async def _freeze_lineage(
    sessions: async_sessionmaker[AsyncSession],
    job_id: int,
    deployment: ModelDeployment,
    *,
    prompt_hash: str,
    schema_hash: str,
    decoding_hash: str,
    config_hash: str,
    context_manifest_checksum: str,
) -> None:
    async with sessions.begin() as session:
        job = await session.get(ReaderGenerationJob, job_id, with_for_update=True)
        if job is None:
            return
        # Preserve original context checksum; never rewrite from live progress.
        if job.context_manifest_checksum != context_manifest_checksum:
            raise ReaderChatWorkerError("frozen manifest checksum changed")
        job.prompt_hash = prompt_hash
        job.schema_hash = schema_hash
        job.decoding_hash = decoding_hash
        job.config_hash = config_hash
        job.model_lineage = deployment.as_dict()
        job.price_snapshot = deployment.price_snapshot()


async def _try_exact_recovery(
    runtime: ReaderChatWorkerRuntime, job_id: int, context: dict[str, Any]
) -> bool:
    """Reuse a prior succeeded envelope when lineage matches; no provider call."""

    async with runtime.sessions() as session:
        # Already published assistant for this user message?
        existing_assistant = await session.scalar(
            select(ReaderMessage).where(
                ReaderMessage.conversation_id == context["conversation_id"],
                ReaderMessage.role == "assistant",
                ReaderMessage.reply_to_message_id == context["user_message_id"],
            )
        )
        if existing_assistant is not None:
            job = await session.get(ReaderGenerationJob, job_id)
            if job and job.status != "completed":
                async with runtime.sessions.begin() as s2:
                    j2 = await s2.get(ReaderGenerationJob, job_id, with_for_update=True)
                    if j2 is not None:
                        j2.status = "completed"
                        j2.status_reason = "idempotent_completion"
                        j2.response_hash = existing_assistant.content_hash
            return True

        attempts = list(
            (
                await session.scalars(
                    select(ReaderModelCallAttempt)
                    .where(
                        ReaderModelCallAttempt.generation_job_id == job_id,
                        ReaderModelCallAttempt.status == "succeeded",
                    )
                    .order_by(ReaderModelCallAttempt.attempt_number.desc())
                )
            ).all()
        )
        for attempt in attempts:
            usage = dict(attempt.usage or {})
            envelope_raw = usage.get("envelope")
            if not isinstance(envelope_raw, dict):
                continue
            try:
                envelope = ReaderAnswerEnvelope.model_validate(envelope_raw)
                business_validate_answer(
                    envelope,
                    allowed_evidence_ids=set(context["allowed_evidence_ids"]),
                )
            except Exception:
                continue
            response_hash = attempt.response_hash or canonical_hash(envelope_raw)
            # Record cache_hit audit without a new budget reservation.
            if runtime.gateway.persistence is not None:
                await runtime.gateway.persistence.record_cache_hit(
                    job_id=job_id,
                    cache_key=attempt.cache_key or f"recover:{attempt.id}",
                    source_attempt_id=attempt.id,
                    response_hash=response_hash,
                    request_hash=attempt.request_hash,
                )
            await _publish_assistant(
                runtime.sessions,
                job_id=job_id,
                envelope=envelope,
                response_hash=response_hash,
                evidence_key_to_ref_id=context["evidence_key_to_ref_id"],
                user_message_id=context["user_message_id"],
                conversation_id=context["conversation_id"],
                owner_id=context["owner_id"],
                novel_id=context["novel_id"],
            )
            return True
    return False


async def _publish_assistant(
    sessions: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    envelope: ReaderAnswerEnvelope,
    response_hash: str,
    evidence_key_to_ref_id: dict[str, int],
    user_message_id: int,
    conversation_id: int,
    owner_id: int,
    novel_id: int,
) -> None:
    # Final cancel check immediately before publication.
    if await _is_cancel_requested(sessions, job_id):
        await _finish_job(
            sessions, job_id, "cancelled", "user_cancel", "cancelled_before_publish"
        )
        return

    body_parts: list[str] = []
    for block in envelope.answer_blocks:
        body_parts.append(block.text)
    if envelope.clarifying_question:
        body_parts.append(envelope.clarifying_question)
    if envelope.uncertainty is not None:
        body_parts.append(envelope.uncertainty.explanation)
    if envelope.suggestion_candidates:
        for s in envelope.suggestion_candidates:
            body_parts.append(f"[suggestion:{s.candidate_type}] {s.proposal}")
    body = "\n\n".join(body_parts) if body_parts else "(no content)"
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    async with sessions.begin() as session:
        job = await session.get(ReaderGenerationJob, job_id, with_for_update=True)
        if job is None:
            return
        if job.cancel_requested:
            job.status = "cancelled"
            job.status_reason = "user_cancel"
            job.error_code = "cancelled_before_publish"
            return
        if job.status == "completed":
            return

        from app.models.reader_chat import ReaderConversation

        conv = await session.get(
            ReaderConversation, conversation_id, with_for_update=True
        )
        if conv is None:
            raise ReaderChatWorkerError("conversation missing")
        sequence = int(conv.next_sequence)
        conv.next_sequence = sequence + 1

        assistant = ReaderMessage(
            conversation_id=conversation_id,
            owner_id=owner_id,
            novel_id=novel_id,
            sequence=sequence,
            role="assistant",
            body=body,
            client_message_id=None,
            reply_to_message_id=user_message_id,
            content_hash=content_hash,
        )
        session.add(assistant)
        await session.flush()

        for block in envelope.answer_blocks:
            for ref_key in block.evidence_refs:
                ref_id = evidence_key_to_ref_id.get(ref_key)
                if ref_id is None:
                    raise ReaderChatWorkerError(f"citation ref missing: {ref_key}")
                session.add(
                    ReaderMessageCitation(
                        assistant_message_id=assistant.id,
                        block_id=block.block_id,
                        context_evidence_ref_id=ref_id,
                    )
                )
        for suggestion in envelope.suggestion_candidates:
            for ref_key in suggestion.evidence_refs:
                ref_id = evidence_key_to_ref_id.get(ref_key)
                if ref_id is None:
                    continue
                session.add(
                    ReaderMessageCitation(
                        assistant_message_id=assistant.id,
                        block_id=f"suggestion:{suggestion.candidate_type}",
                        context_evidence_ref_id=ref_id,
                    )
                )

        job.status = "completed"
        job.status_reason = "published"
        job.response_hash = response_hash
        job.error_code = None


async def _finish_job(
    sessions: async_sessionmaker[AsyncSession],
    job_id: int,
    status: str,
    error_code: str | None,
    status_reason: str,
) -> None:
    async with sessions.begin() as session:
        job = await session.get(ReaderGenerationJob, job_id, with_for_update=True)
        if job is None:
            return
        if job.status == "completed":
            return
        job.status = status
        job.error_code = error_code
        job.status_reason = status_reason[:160]
