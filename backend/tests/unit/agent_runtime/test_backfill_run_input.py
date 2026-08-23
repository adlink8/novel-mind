"""chat_backfill run input 血缘锚定（Slice A / Phase 40 扩展）。

detect-key-scenes 的 SceneCandidateSetContract 要求 source_snapshot_hash /
cutoff_chapter 等血缘字段——这些必须由程序产出（模型无法重放 sha256）。
run 创建时后端把真实 source snapshot hash 与阅读 cutoff 写进 run input，
agent-service envelope builder 随后按引用投影进信封。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.agent_runtime import SkillRegistry, SkillVersion
from app.models.novel import Chapter, Novel
from app.models.reader_chat import ReaderConversation, ReaderMessage
from app.models.user import User
from app.services.agent_runtime.backfill import create_backfill_runs
from app.services.key_scenes.boundaries import (
    ChapterRecord,
    compute_source_snapshot_hash,
)
from app.services.visual_bible.evidence import (
    compute_source_snapshot_hash as compute_visual_snapshot_hash,
)

pytestmark = pytest.mark.unit

CHAPTER_1 = "夜色笼罩着庭院，林安握紧了剑。"
CHAPTER_2 = "清晨的钟声从远山传来，使者抵达城门。"


async def _seed_novel_with_chapters(
    session: AsyncSession, *, with_skill: bool = True
) -> tuple[User, Novel, int]:
    owner = User(
        username="backfill_owner",
        email="backfill_owner@example.com",
        hashed_password=hash_password("pass12345"),
    )
    session.add(owner)
    await session.flush()
    novel = Novel(
        title="Backfill Novel",
        owner_id=owner.id,
        status="ready",
        reading_progress={},
        chapter_count=2,
        word_count=100,
    )
    session.add(novel)
    await session.flush()
    session.add(
        Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content=CHAPTER_1,
        )
    )
    session.add(
        Chapter(
            novel_id=novel.id,
            chapter_number=2,
            title="第二章",
            content=CHAPTER_2,
        )
    )
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
        body="这个问题触发 backfill",
    )
    session.add(message)
    await session.flush()
    if with_skill:
        registry = SkillRegistry(
            owner_id=owner.id,
            novel_id=novel.id,
            name="detect-key-scenes",
            status="active",
        )
        session.add(registry)
        await session.flush()
        session.add(
            SkillVersion(
                registry_id=registry.id,
                owner_id=owner.id,
                novel_id=novel.id,
                name=registry.name,
                version="1.0.0",
                yaml_checksum="a" * 64,
                allowed_tools=["get_evidence_span"],
                read_permissions=[],
                write_permissions=[],
                forbidden_spaces=[],
                budget={},
                approval_required_for=[],
                input_schema={},
                output_schema={},
                status="active",
            )
        )
        await session.flush()
    return owner, novel, message.id


def _expected_snapshot_hash(owner_id: int, novel_id: int) -> str:
    chapters = (
        ChapterRecord(chapter_id=0, chapter_number=1, content=CHAPTER_1),
        ChapterRecord(chapter_id=0, chapter_number=2, content=CHAPTER_2),
    )
    return compute_source_snapshot_hash(
        owner_id=owner_id, novel_id=novel_id, chapters=chapters
    )


async def test_detect_key_scenes_run_input_carries_source_snapshot(
    db_session: AsyncSession,
):
    """raw_text 维度触发的 detect-key-scenes run：input 必须带真实 snapshot hash。"""
    owner, novel, message_id = await _seed_novel_with_chapters(db_session)

    runs = await create_backfill_runs(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        user_message_id=message_id,
        question="这一幕发生在什么时候？",
        unavailable_dimensions=["raw_text"],
    )

    assert len(runs) == 1
    snapshot = runs[0].input.get("source_snapshot")
    assert snapshot is not None, "run input 缺少 source_snapshot"
    assert snapshot["snapshot_hash"] == _expected_snapshot_hash(owner.id, novel.id)


async def test_detect_key_scenes_run_input_carries_cutoff_chapter(
    db_session: AsyncSession,
):
    """无阅读进度时 cutoff 落首章（D20：绝不默认全书）。"""
    owner, novel, message_id = await _seed_novel_with_chapters(db_session)

    runs = await create_backfill_runs(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        user_message_id=message_id,
        question="这一幕发生在什么时候？",
        unavailable_dimensions=["raw_text"],
    )

    assert len(runs) == 1
    assert runs[0].input.get("cutoff_chapter") == 1


async def test_visual_bible_run_input_uses_visual_snapshot_namespace(
    db_session: AsyncSession,
):
    """build-visual-bible 的 snapshot hash 必须是 visual_bible 命名空间。

    key_scene 与 visual_bible 的 compute_source_snapshot_hash 只有 kind 不同，
    但物化器按各自命名空间重放（stale_snapshot_lineage 检查）；用错命名空间
    的 run 会在 materialize 时永远 stale（E2E run 102 实测）。
    """
    owner, novel, message_id = await _seed_novel_with_chapters(
        db_session, with_skill=False
    )
    await _seed_visual_bible_skill(db_session, owner, novel)

    runs = await create_backfill_runs(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        user_message_id=message_id,
        question="慕师靖长什么样？",
        unavailable_dimensions=["relations"],
    )

    assert len(runs) == 1
    chapters = (
        ChapterRecord(chapter_id=0, chapter_number=1, content=CHAPTER_1),
        ChapterRecord(chapter_id=0, chapter_number=2, content=CHAPTER_2),
    )
    expected = compute_visual_snapshot_hash(
        owner_id=owner.id, novel_id=novel.id, chapters=chapters
    )
    assert runs[0].input["source_snapshot"]["snapshot_hash"] == expected


async def test_world_model_run_input_not_polluted_by_scene_snapshot(
    db_session: AsyncSession,
):
    """snapshot/cutoff 锚定只服务 detect-key-scenes；其它 skill input 不携带。"""
    owner, novel, message_id = await _seed_novel_with_chapters(
        db_session, with_skill=False
    )
    registry = SkillRegistry(
        owner_id=owner.id,
        novel_id=novel.id,
        name="propose-world-model-candidates",
        status="active",
    )
    db_session.add(registry)
    await db_session.flush()
    db_session.add(
        SkillVersion(
            registry_id=registry.id,
            owner_id=owner.id,
            novel_id=novel.id,
            name=registry.name,
            version="1.0.0",
            yaml_checksum="a" * 64,
            allowed_tools=["get_events"],
            read_permissions=[],
            write_permissions=[],
            forbidden_spaces=[],
            budget={},
            approval_required_for=[],
            input_schema={},
            output_schema={},
            status="active",
        )
    )
    await db_session.flush()

    runs = await create_backfill_runs(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        user_message_id=message_id,
        question="林安的目标是什么？",
        unavailable_dimensions=["world_projection"],
    )

    assert len(runs) == 1
    assert "source_snapshot" not in runs[0].input


async def _seed_visual_bible_skill(
    session: AsyncSession, owner: User, novel: Novel
) -> None:
    registry = SkillRegistry(
        owner_id=owner.id,
        novel_id=novel.id,
        name="build-visual-bible",
        status="active",
    )
    session.add(registry)
    await session.flush()
    session.add(
        SkillVersion(
            registry_id=registry.id,
            owner_id=owner.id,
            novel_id=novel.id,
            name=registry.name,
            version="1.0.0",
            yaml_checksum="b" * 64,
            allowed_tools=["get_evidence_span"],
            read_permissions=[],
            write_permissions=[],
            forbidden_spaces=[],
            budget={},
            approval_required_for=[],
            input_schema={},
            output_schema={},
            status="active",
        )
    )
    await session.flush()


async def test_visual_bible_run_input_carries_snapshot_and_cutoff(
    db_session: AsyncSession,
):
    """relations 维度触发的 build-visual-bible run：input 必须锚定真实血缘。

    VisualBibleVersionContract 的 source_snapshot_hash / cutoff_chapter 与
    claim_hash / manifest_hash 都是程序产出字段（模型无法重放 sha256），
    与 detect-key-scenes 同一纪律（Slice A 扩展）。
    """
    owner, novel, message_id = await _seed_novel_with_chapters(
        db_session, with_skill=False
    )
    await _seed_visual_bible_skill(db_session, owner, novel)

    runs = await create_backfill_runs(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        user_message_id=message_id,
        question="慕师靖长什么样？",
        unavailable_dimensions=["relations"],
    )

    assert len(runs) == 1
    snapshot = runs[0].input.get("source_snapshot")
    assert snapshot is not None, "run input 缺少 source_snapshot"
    # visual_bible 命名空间（物化器按此重放；key_scene 哈希会永远 stale）
    chapters = (
        ChapterRecord(chapter_id=0, chapter_number=1, content=CHAPTER_1),
        ChapterRecord(chapter_id=0, chapter_number=2, content=CHAPTER_2),
    )
    assert snapshot["snapshot_hash"] == compute_visual_snapshot_hash(
        owner_id=owner.id, novel_id=novel.id, chapters=chapters
    )
    assert runs[0].input.get("cutoff_chapter") == 1
