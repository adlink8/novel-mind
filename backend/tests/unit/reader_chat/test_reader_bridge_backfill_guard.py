"""reader_bridge 空维度守卫：无 backfill 映射的不可用维度必须诚实失败。

回归背景（2026-08-13 现网事故）：manifest source_status 含 knowledge /
relationship_observation 等未映射维度时，required 为空元组，任务被置为
``paused_dependency`` + ``waiting_analysis:``（空列表），零 backfill run
被创建，reconcile 永远等不到任何维度 → 任务永久停摆。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.novel import Novel
from app.models.reader_chat import (
    ReaderContextManifest,
    ReaderConversation,
    ReaderGenerationJob,
    ReaderMessage,
)
from app.models.user import User
from app.services.agent_runtime.reader_bridge import (
    _enqueue_reader_skill_run_in_session,
)

pytestmark = pytest.mark.unit


async def _seed_job(
    session: AsyncSession, *, source_status: dict[str, str]
) -> ReaderGenerationJob:
    owner = User(
        username="bridge_owner",
        email="bridge_owner@example.com",
        hashed_password=hash_password("pass12345"),
    )
    session.add(owner)
    await session.flush()
    novel = Novel(
        title="Bridge Novel",
        owner_id=owner.id,
        status="ready",
        reading_progress={},
        chapter_count=1,
        word_count=100,
    )
    session.add(novel)
    await session.flush()
    conversation = ReaderConversation(
        owner_id=owner.id, novel_id=novel.id, title="t", status="active"
    )
    session.add(conversation)
    await session.flush()
    message = ReaderMessage(
        conversation_id=conversation.id,
        owner_id=owner.id,
        novel_id=novel.id,
        sequence=1,
        role="user",
        body="这个问题需要缺失维度的证据",
    )
    session.add(message)
    await session.flush()
    manifest = ReaderContextManifest(
        user_message_id=message.id,
        conversation_id=conversation.id,
        reading_progress_snapshot={"chapter_id": 1},
        full_book=False,
        cutoff_chapter_number=1,
        hierarchy_build_id="b" * 64,
        hierarchy_checksum="c" * 64,
        manifest_checksum="d" * 64,
        prompt_inputs={"source_status": source_status},
        omitted_evidence_counts={},
    )
    session.add(manifest)
    job = ReaderGenerationJob(
        conversation_id=conversation.id,
        owner_id=owner.id,
        novel_id=novel.id,
        user_message_id=message.id,
        status="queued",
        prompt_hash="p" * 64,
        schema_hash="s" * 64,
        context_manifest_checksum="d" * 64,
        decoding_hash="e" * 64,
        config_hash="f" * 64,
    )
    session.add(job)
    await session.flush()
    return job


@pytest.mark.asyncio
async def test_unmapped_unavailable_dimensions_fail_honestly(db_session):
    """knowledge/relationship_observation 无 backfill 映射 → failed，绝不永久等待。"""
    job = await _seed_job(
        db_session,
        source_status={
            "knowledge": "absent",
            "relationship_observation": "unavailable",
        },
    )

    result = await _enqueue_reader_skill_run_in_session(db_session, job)

    assert result is None
    assert job.status == "failed"
    assert job.status_reason == "backfill_unavailable"
    assert job.error_code == "backfill_unavailable"


@pytest.mark.asyncio
async def test_mapped_unavailable_dimension_never_parks_with_empty_wait_list(
    db_session,
):
    """有映射的维度：waiting_analysis 必须携带非空维度列表（或诚实失败）。"""
    job = await _seed_job(db_session, source_status={"raw_text": "unavailable"})

    await _enqueue_reader_skill_run_in_session(db_session, job)

    if job.status == "paused_dependency":
        dims = str(job.status_reason).split(":", 1)[1]
        assert dims and all(dim for dim in dims.split(","))
    else:
        # 未注册 active skill 版本时诚实失败同样是可接受的终态。
        assert job.status == "failed"
        assert job.error_code == "backfill_unavailable"
