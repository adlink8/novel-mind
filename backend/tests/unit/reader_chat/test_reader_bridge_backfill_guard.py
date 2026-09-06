"""reader_bridge 空维度守卫：无 backfill 映射的不可用维度必须诚实失败。

回归背景（2026-08-13 现网事故）：manifest source_status 含 knowledge /
relationship_observation 等未映射维度时，required 为空元组，任务被置为
``paused_dependency`` + ``waiting_analysis:``（空列表），零 backfill run
被创建，reconcile 永远等不到任何维度 → 任务永久停摆。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.agent_runtime import SkillRegistry, SkillRun, SkillVersion
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


@pytest.mark.asyncio
async def test_satisfied_backfill_dimension_fails_honestly_after_rebuild(db_session):
    """重建后所需维度已有 completed+materialized run → 诚实失败，绝不重建 run。

    digest 类产物（story_arc）不写域表，重建 manifest 后对应证据源仍不可用；
    若无此守卫，每轮 reconcile 都会为已满足维度新建 run，无限烧预算。
    """
    job = await _seed_job(db_session, source_status={"timeline": "absent"})
    registry = SkillRegistry(
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        name="build-story-arc",
        description="t",
        status="active",
    )
    db_session.add(registry)
    await db_session.flush()
    version = SkillVersion(
        registry_id=registry.id,
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        name="build-story-arc",
        version="1.0.0",
        yaml_checksum="a" * 64,
        status="active",
    )
    db_session.add(version)
    await db_session.flush()
    db_session.add(
        SkillRun(
            owner_id=job.owner_id,
            novel_id=job.novel_id,
            skill_version_id=version.id,
            status="completed",
            status_reason="materialized:story_arc",
            input={},
            input_hash="a" * 64,
            origin="chat_backfill",
            backfill_dimension="timeline",
            user_message_id=job.user_message_id,
        )
    )
    await db_session.flush()

    result = await _enqueue_reader_skill_run_in_session(db_session, job)

    assert result is None
    assert job.status == "failed"
    assert job.status_reason == "backfill_unavailable"
    assert job.error_code == "backfill_unavailable"
    runs = (
        await db_session.scalars(
            select(SkillRun).where(
                SkillRun.owner_id == job.owner_id,
                SkillRun.novel_id == job.novel_id,
                SkillRun.origin == "chat_backfill",
            )
        )
    ).all()
    assert len(runs) == 1, "已满足维度不得再新建 backfill run"


@pytest.mark.asyncio
async def test_backfill_run_input_carries_chapter_anchor(db_session):
    """run input 必须携带提问时的章节锚点，否则模型无法取到任何证据。

    build-story-arc 的 allowlist 没有 get_novel/search_novel_text，run input
    不带 chapter_id 时模型只能盲猜 get_chapter id（全部 404）→ 零证据 abstain。
    锚点优先取消息选区章节，回落阅读进度章节。
    """
    job = await _seed_job(db_session, source_status={"timeline": "absent"})
    manifest = await db_session.scalar(
        select(ReaderContextManifest).where(
            ReaderContextManifest.user_message_id == job.user_message_id
        )
    )
    assert manifest is not None
    manifest.reading_progress_snapshot = {"chapter_id": 777}
    registry = SkillRegistry(
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        name="build-story-arc",
        description="t",
        status="active",
    )
    db_session.add(registry)
    await db_session.flush()
    version = SkillVersion(
        registry_id=registry.id,
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        name="build-story-arc",
        version="1.0.0",
        yaml_checksum="a" * 64,
        status="active",
    )
    db_session.add(version)
    await db_session.flush()

    result = await _enqueue_reader_skill_run_in_session(db_session, job)

    assert result is None
    assert job.status == "paused_dependency"
    run = (
        await db_session.scalars(
            select(SkillRun).where(
                SkillRun.owner_id == job.owner_id,
                SkillRun.novel_id == job.novel_id,
                SkillRun.origin == "chat_backfill",
            )
        )
    ).one()
    assert run.input["chapter_id"] == 777
