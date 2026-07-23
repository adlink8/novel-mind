"""PostgreSQL generation worker: dual budgets, cancel/retry, frozen manifest."""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.security import create_access_token, hash_password
from app.models.novel import Chapter, Novel
from app.models.reader_chat import (
    ReaderBudgetLedger,
    ReaderGenerationJob,
    ReaderMessage,
    ReaderMessageCitation,
    ReaderModelCallAttempt,
)
from app.models.user import User
from app.services.reader_chat.budget import (
    BudgetPolicy,
    DualBudgetRepository,
)
from app.services.reader_chat.gateway import ModelDeployment, ReaderChatGateway
from app.services.reader_chat.worker import (
    ReaderChatWorkerRuntime,
    run_reader_chat_worker,
)

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上。"
HEX64 = hashlib.sha256(CHAPTER_CONTENT.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _no_background_dispatch(monkeypatch):
    """本文件全部用例手动运行 worker；屏蔽 API 的后台自动派发，保证任务保持 queued。"""

    async def _noop(job_id: int) -> None:
        return None

    monkeypatch.setattr("app.api.reader_chat.dispatch_reader_chat_job", _noop)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed(sync_url: str, *, suffix: str) -> dict[str, Any]:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"gen_owner_{suffix}",
            email=f"gen_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(owner)
        session.flush()
        novel = Novel(
            title=f"Gen Novel {suffix}",
            owner_id=owner.id,
            status="ready",
            reading_progress={},
            chapter_count=1,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add(novel)
        session.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content=CHAPTER_CONTENT,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add(chapter)
        session.commit()
        data = {
            "owner_id": owner.id,
            "novel_id": novel.id,
            "chapter_id": chapter.id,
            "owner_token": create_access_token({"sub": str(owner.id)}),
        }
    engine.dispose()
    return data


def _selection(chapter_id: int, start: int = 0, end: int = 8) -> dict[str, Any]:
    text_slice = CHAPTER_CONTENT[start:end]
    return {
        "chapter_id": chapter_id,
        "source_start": start,
        "source_end": end,
        "selection_text": text_slice,
        "selection_text_hash": _sha256(text_slice),
        "chapter_content_hash": HEX64,
    }


def _valid_answer(evidence_key: str) -> str:
    return json.dumps(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [
                {
                    "block_id": "b1",
                    "text": "阿宁走进竹林。",
                    "evidence_refs": [evidence_key],
                }
            ],
            "clarifying_question": None,
            "uncertainty": None,
            "suggestion_candidates": [],
        },
        ensure_ascii=False,
    )


class RecordingTransport:
    def __init__(self, responses=None, *, hang_until=None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []
        self.hang_until = hang_until

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.hang_until is not None:
            await self.hang_until
        if not self.responses:
            raise RuntimeError("no response queued")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _deployment(*, priced: bool = True) -> ModelDeployment:
    return ModelDeployment(
        provider="test",
        model_id="reader-balanced",
        revision="r1",
        supports_structured_output=True,
        input_price_per_million=Decimal("1") if priced else None,
        output_price_per_million=Decimal("2") if priced else None,
    )


async def _create_queued_job(
    client, ids: dict[str, Any], *, body: str = "这段是什么意思？"
):
    headers = {"Authorization": f"Bearer {ids['owner_token']}"}
    base = f"/api/novels/{ids['novel_id']}/conversations"
    created = await client.post(base, json={"title": "gen"}, headers=headers)
    assert created.status_code == 201, created.text
    conv_id = created.json()["id"]
    payload = {
        "client_message_id": f"cm-{uuid.uuid4().hex}",
        "body": body,
        "selection": _selection(ids["chapter_id"]),
    }
    accepted = await client.post(
        f"{base}/{conv_id}/messages", json=payload, headers=headers
    )
    assert accepted.status_code == 202, accepted.text
    data = accepted.json()
    return conv_id, data["job"]["id"], data["message"]["id"], headers, base


def _runtime(factory, transport, deployment=None, **policy_kwargs):
    sessions = factory
    dep = deployment or _deployment()
    conv_policy = policy_kwargs.get(
        "conversation_policy",
        BudgetPolicy(10, 100_000, 20_000, Decimal("10")),
    )
    novel_policy = policy_kwargs.get(
        "novel_policy",
        BudgetPolicy(10, 100_000, 20_000, Decimal("10")),
    )
    return ReaderChatWorkerRuntime(
        sessions=sessions,
        gateway=ReaderChatGateway(
            transport,
            persistence=DualBudgetRepository(
                sessions,
                conversation_policy=conv_policy,
                novel_policy=novel_policy,
            ),
        ),
        deployment=dep,
        conversation_policy=conv_policy,
        novel_policy=novel_policy,
        system_prompt="test reader chat system prompt",
    )


@pytest.mark.asyncio
async def test_worker_publishes_cited_assistant_and_settles_dual_ledgers(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    conv_id, job_id, user_msg_id, headers, base = await _create_queued_job(client, ids)

    # Discover evidence key from committed manifest.
    async with factory() as session:
        from app.models.reader_chat import (
            ReaderContextEvidenceRef,
            ReaderContextManifest,
        )

        manifest = await session.scalar(
            select(ReaderContextManifest).where(
                ReaderContextManifest.user_message_id == user_msg_id
            )
        )
        ref = await session.scalar(
            select(ReaderContextEvidenceRef).where(
                ReaderContextEvidenceRef.manifest_id == manifest.id
            )
        )
        evidence_key = ref.evidence_key
        frozen_checksum = manifest.manifest_checksum

    transport = RecordingTransport(
        [
            {
                "id": "prov-1",
                "content": _valid_answer(evidence_key),
                "usage": {"input_tokens": 12, "output_tokens": 4},
            }
        ]
    )
    await run_reader_chat_worker(job_id, runtime=_runtime(factory, transport))

    assert len(transport.calls) == 1
    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        assert job.status == "completed"
        assert job.context_manifest_checksum == frozen_checksum
        assert job.response_hash
        assistant = await session.scalar(
            select(ReaderMessage).where(
                ReaderMessage.role == "assistant",
                ReaderMessage.reply_to_message_id == user_msg_id,
            )
        )
        assert assistant is not None
        citations = list(
            (
                await session.scalars(
                    select(ReaderMessageCitation).where(
                        ReaderMessageCitation.assistant_message_id == assistant.id
                    )
                )
            ).all()
        )
        assert len(citations) >= 1
        attempts = list(
            (
                await session.scalars(
                    select(ReaderModelCallAttempt).where(
                        ReaderModelCallAttempt.generation_job_id == job_id
                    )
                )
            ).all()
        )
        assert any(a.status == "succeeded" for a in attempts)
        ledgers = list(
            (
                await session.scalars(
                    select(ReaderBudgetLedger).where(
                        ReaderBudgetLedger.novel_id == ids["novel_id"]
                    )
                )
            ).all()
        )
        assert len(ledgers) == 2
        for ledger in ledgers:
            assert ledger.settled_calls == 1
            assert ledger.reserved_calls == 0
            assert ledger.settled_input_tokens == 12

    # Job status API
    status = await client.get(f"{base}/{conv_id}/jobs/{job_id}", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_unknown_pricing_makes_zero_provider_calls(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    _, job_id, _, _, _ = await _create_queued_job(client, ids)
    transport = RecordingTransport([{"content": "{}", "usage": {}}])
    await run_reader_chat_worker(
        job_id,
        runtime=_runtime(factory, transport, deployment=_deployment(priced=False)),
    )
    assert transport.calls == []
    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        assert job.status == "paused_budget"
        assert job.error_code == "unknown_pricing"


@pytest.mark.asyncio
async def test_either_ledger_ceiling_blocks_before_call(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    _, job_id, _, _, _ = await _create_queued_job(client, ids)
    transport = RecordingTransport([{"content": "{}", "usage": {}}])
    tiny = BudgetPolicy(0, 1, 1, Decimal("0"))
    await run_reader_chat_worker(
        job_id,
        runtime=_runtime(
            factory,
            transport,
            conversation_policy=tiny,
            novel_policy=BudgetPolicy(10, 1000, 500, Decimal("1")),
        ),
    )
    assert transport.calls == []
    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        assert job.status == "paused_budget"


@pytest.mark.asyncio
async def test_cancel_before_call_publishes_no_assistant(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    conv_id, job_id, user_msg_id, headers, base = await _create_queued_job(client, ids)

    cancelled = await client.post(
        f"{base}/{conv_id}/jobs/{job_id}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    transport = RecordingTransport(
        [{"content": _valid_answer("selection:x"), "usage": {}}]
    )
    await run_reader_chat_worker(job_id, runtime=_runtime(factory, transport))
    assert transport.calls == []
    async with factory() as session:
        assistants = await session.scalar(
            select(func.count())
            .select_from(ReaderMessage)
            .where(
                ReaderMessage.role == "assistant",
                ReaderMessage.reply_to_message_id == user_msg_id,
            )
        )
        assert assistants == 0


@pytest.mark.asyncio
async def test_cancel_during_call_settles_and_discards(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    conv_id, job_id, user_msg_id, headers, base = await _create_queued_job(client, ids)

    async with factory() as session:
        from app.models.reader_chat import (
            ReaderContextEvidenceRef,
            ReaderContextManifest,
        )

        manifest = await session.scalar(
            select(ReaderContextManifest).where(
                ReaderContextManifest.user_message_id == user_msg_id
            )
        )
        ref = await session.scalar(
            select(ReaderContextEvidenceRef).where(
                ReaderContextEvidenceRef.manifest_id == manifest.id
            )
        )
        evidence_key = ref.evidence_key

    class CancelMidTransport(RecordingTransport):
        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            # Request cancel while "in flight".
            async with factory() as session:
                job = await session.get(ReaderGenerationJob, job_id)
                job.cancel_requested = True
                await session.commit()
            return {
                "id": "late",
                "content": _valid_answer(evidence_key),
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }

    transport = CancelMidTransport()
    await run_reader_chat_worker(job_id, runtime=_runtime(factory, transport))
    assert len(transport.calls) == 1
    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        assert job.status == "cancelled"
        assistants = await session.scalar(
            select(func.count())
            .select_from(ReaderMessage)
            .where(
                ReaderMessage.role == "assistant",
                ReaderMessage.reply_to_message_id == user_msg_id,
            )
        )
        assert assistants == 0
        # Usage settled on dual ledgers.
        ledgers = list(
            (
                await session.scalars(
                    select(ReaderBudgetLedger).where(
                        ReaderBudgetLedger.novel_id == ids["novel_id"]
                    )
                )
            ).all()
        )
        assert any(ledger.settled_calls >= 1 for ledger in ledgers)


@pytest.mark.asyncio
async def test_retry_reuses_frozen_manifest_checksum(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    conv_id, job_id, user_msg_id, headers, base = await _create_queued_job(client, ids)

    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        original_checksum = job.context_manifest_checksum
        job.status = "failed"
        job.status_reason = "forced"
        await session.commit()

    retried = await client.post(
        f"{base}/{conv_id}/jobs/{job_id}/retry", headers=headers
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"
    assert retried.json()["retry_count"] >= 1

    async with factory() as session:
        from app.models.reader_chat import (
            ReaderContextEvidenceRef,
            ReaderContextManifest,
        )

        job = await session.get(ReaderGenerationJob, job_id)
        assert job.context_manifest_checksum == original_checksum
        manifest = await session.scalar(
            select(ReaderContextManifest).where(
                ReaderContextManifest.user_message_id == user_msg_id
            )
        )
        ref = await session.scalar(
            select(ReaderContextEvidenceRef).where(
                ReaderContextEvidenceRef.manifest_id == manifest.id
            )
        )
        evidence_key = ref.evidence_key

    transport = RecordingTransport(
        [
            {
                "content": _valid_answer(evidence_key),
                "usage": {"input_tokens": 3, "output_tokens": 1},
            }
        ]
    )
    await run_reader_chat_worker(job_id, runtime=_runtime(factory, transport))
    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        assert job.status == "completed"
        assert job.context_manifest_checksum == original_checksum
        assistants = await session.scalar(
            select(func.count())
            .select_from(ReaderMessage)
            .where(
                ReaderMessage.role == "assistant",
                ReaderMessage.reply_to_message_id == user_msg_id,
            )
        )
        assert assistants == 1


@pytest.mark.asyncio
async def test_idempotent_completion_creates_one_assistant(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    _, job_id, user_msg_id, _, _ = await _create_queued_job(client, ids)

    async with factory() as session:
        from app.models.reader_chat import (
            ReaderContextEvidenceRef,
            ReaderContextManifest,
        )

        manifest = await session.scalar(
            select(ReaderContextManifest).where(
                ReaderContextManifest.user_message_id == user_msg_id
            )
        )
        ref = await session.scalar(
            select(ReaderContextEvidenceRef).where(
                ReaderContextEvidenceRef.manifest_id == manifest.id
            )
        )
        evidence_key = ref.evidence_key

    transport = RecordingTransport(
        [
            {
                "content": _valid_answer(evidence_key),
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
            {
                "content": _valid_answer(evidence_key),
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        ]
    )
    runtime = _runtime(factory, transport)
    await run_reader_chat_worker(job_id, runtime=runtime)
    # Second run: job already completed — claim should no-op.
    await run_reader_chat_worker(job_id, runtime=runtime)
    assert len(transport.calls) == 1
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ReaderMessage)
            .where(
                ReaderMessage.role == "assistant",
                ReaderMessage.reply_to_message_id == user_msg_id,
            )
        )
        assert count == 1


@pytest.mark.asyncio
async def test_validation_failure_publishes_no_assistant(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    _, job_id, user_msg_id, _, _ = await _create_queued_job(client, ids)
    transport = RecordingTransport(
        [{"content": "{}", "usage": {}}, {"content": "{}", "usage": {}}]
    )
    await run_reader_chat_worker(job_id, runtime=_runtime(factory, transport))
    assert len(transport.calls) == 2
    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        assert job.status == "failed_validation"
        count = await session.scalar(
            select(func.count())
            .select_from(ReaderMessage)
            .where(
                ReaderMessage.role == "assistant",
                ReaderMessage.reply_to_message_id == user_msg_id,
            )
        )
        assert count == 0


@pytest.mark.asyncio
async def test_outcome_unknown_pauses_without_assistant(api_client):
    client, factory, sync_url = api_client
    ids = _seed(sync_url, suffix=uuid.uuid4().hex[:8])
    _, job_id, user_msg_id, _, _ = await _create_queued_job(client, ids)
    transport = RecordingTransport([TimeoutError("provider hung")])
    await run_reader_chat_worker(job_id, runtime=_runtime(factory, transport))
    assert len(transport.calls) == 1
    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        assert job.status == "paused_dependency"
        attempt = await session.scalar(
            select(ReaderModelCallAttempt).where(
                ReaderModelCallAttempt.generation_job_id == job_id
            )
        )
        assert attempt is not None
        assert attempt.status == "outcome_unknown"
        count = await session.scalar(
            select(func.count())
            .select_from(ReaderMessage)
            .where(
                ReaderMessage.role == "assistant",
                ReaderMessage.reply_to_message_id == user_msg_id,
            )
        )
        assert count == 0
