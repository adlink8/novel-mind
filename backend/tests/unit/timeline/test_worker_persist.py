"""Timeline worker chapter persistence FK fail-soft (Bug 1).

无角色数据（novel 没有 characters 行）时，LLM 臆造的 participant.entity_id
不能触发 TimelineParticipant.entity_id → characters.id 的 IntegrityError；
未知/跨 novel 的 entity_id 一律置 None，mention 文本保留。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.analysis import AnalysisRun, AnalysisVersion
from app.models.character import Character
from app.models.novel import Chapter, Novel
from app.models.timeline import MachineTimelineEvent, TimelineParticipant
from app.models.user import User
from app.schemas.timeline import (
    EventCandidate,
    EvidenceRef,
    Participant,
    StoryTime,
    TimelineExtraction,
)
from app.services.timeline.worker import (
    _load_character_ids,
    _load_character_registry,
    _persist_chapter,
    _sanitize_participant_entity_ids,
)

pytestmark = pytest.mark.unit


def _extraction_with_participants(*participants: Participant) -> TimelineExtraction:
    return TimelineExtraction(
        events=[
            EventCandidate(
                candidate_id="e1",
                title="事件",
                description="描述",
                event_type="plot",
                narrative_chapter_number=1,
                narrative_index=0,
                participants=list(participants),
                story_time=StoryTime(precision="unknown"),
                evidence=[
                    EvidenceRef(
                        chapter_id=1,
                        evidence_id="ev-1",
                        source_start=0,
                        source_end=1,
                        content_hash="0" * 64,
                    )
                ],
                confidence=0.9,
            )
        ],
        story_time_constraints=[],
    )


def test_sanitize_participant_entity_ids_nullifies_unknown_but_keeps_mention():
    extraction = _extraction_with_participants(
        Participant(mention="幽灵", entity_id=1),
        Participant(mention="未绑定", entity_id=None),
        Participant(mention="真角色", entity_id=42),
    )
    _sanitize_participant_entity_ids(extraction, known_ids={42})

    participants = extraction.events[0].participants
    assert participants[0].entity_id is None  # 臆造 id → 置 None
    assert participants[0].mention == "幽灵"  # mention 保留
    assert participants[1].entity_id is None  # 原本就是 None
    assert participants[2].entity_id == 42  # 已注册 id 保留


def test_sanitize_participant_entity_ids_empty_registry_nullifies_everything():
    extraction = _extraction_with_participants(
        Participant(mention="阿宁", entity_id=7),
        Participant(mention="路人", entity_id=8),
    )
    _sanitize_participant_entity_ids(extraction, known_ids=set())
    assert [p.entity_id for p in extraction.events[0].participants] == [None, None]


async def _seed_persist_scope(db_session, *, with_characters: bool):
    owner = User(
        username="bug1-owner",
        email="bug1-owner@example.com",
        hashed_password="x",
    )
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="无角色书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id, chapter_number=1, title="第一章", content="阿宁推开了门。"
    )
    db_session.add(chapter)
    await db_session.flush()
    version = AnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="bug1-version",
        status="candidate",
        source_snapshot_hash="a" * 64,
        hierarchy_build_id="build-1",
        hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={
            "chapter_extract": {"provider": "test", "model_id": "m", "revision": "r1"},
            "cross_chapter_reconcile": {
                "provider": "test",
                "model_id": "m",
                "revision": "r1",
            },
        },
        decoding_hash="e" * 64,
        config_hash="f" * 64,
        price_snapshot={},
        manifest={},
    )
    db_session.add(version)
    await db_session.flush()
    run = AnalysisRun(
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        status="running",
    )
    db_session.add(run)
    await db_session.flush()
    if with_characters:
        db_session.add(
            Character(
                novel_id=novel.id,
                name="阿宁",
                aliases="阿宁, 宁宁",
                role="protagonist",
            )
        )
        await db_session.flush()
    await db_session.commit()
    return owner, novel, chapter, run, version


@pytest.mark.asyncio
async def test_persist_chapter_without_characters_survives_llm_entity_id(db_session):
    """无 characters 行 + 事件参与者带臆造 entity_id → 持久化不抛 IntegrityError。"""
    _, novel, chapter, run, version = await _seed_persist_scope(
        db_session, with_characters=False
    )
    extraction = _extraction_with_participants(
        Participant(mention="阿宁", entity_id=1),
        Participant(mention="路人", entity_id=2),
    )

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await _persist_chapter(
        sessions, run, version, chapter, f"chapter_extract:{chapter.id}", extraction
    )

    version_id = version.id
    novel_id = novel.id
    db_session.expire_all()
    participants = list((await db_session.scalars(select(TimelineParticipant))).all())
    assert len(participants) == 2
    assert {p.mention for p in participants} == {"阿宁", "路人"}
    assert all(p.entity_id is None for p in participants)
    # 事件本身也已持久化（不因参与者 FK 回滚）
    events = list(
        (
            await db_session.scalars(
                select(MachineTimelineEvent).where(
                    MachineTimelineEvent.version_id == version_id
                )
            )
        ).all()
    )
    assert len(events) == 1
    assert events[0].novel_id == novel_id


@pytest.mark.asyncio
async def test_persist_chapter_keeps_known_character_and_nullifies_unknown(db_session):
    """novel 有 characters 行时：已注册 id 保留，未注册 id 置 None。"""
    _, novel, chapter, run, version = await _seed_persist_scope(
        db_session, with_characters=True
    )
    known_id = await db_session.scalar(
        select(Character.id).where(Character.novel_id == novel.id)
    )
    extraction = _extraction_with_participants(
        Participant(mention="阿宁", entity_id=known_id),
        Participant(mention="幽灵", entity_id=999_999),
    )

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await _persist_chapter(
        sessions, run, version, chapter, f"chapter_extract:{chapter.id}", extraction
    )

    db_session.expire_all()
    participants = list((await db_session.scalars(select(TimelineParticipant))).all())
    by_mention = {p.mention: p.entity_id for p in participants}
    assert by_mention["阿宁"] == known_id
    assert by_mention["幽灵"] is None


@pytest.mark.asyncio
async def test_character_loaders_are_novel_scoped(db_session):
    """_load_character_ids / _load_character_registry 只返回该 novel 的角色。"""
    owner, novel, _, _, _ = await _seed_persist_scope(db_session, with_characters=True)
    known_id = await db_session.scalar(
        select(Character.id).where(Character.novel_id == novel.id)
    )
    other_novel = Novel(owner_id=owner.id, title="另一本", status="ready")
    db_session.add(other_novel)
    await db_session.flush()
    db_session.add(Character(novel_id=other_novel.id, name="异书角色"))
    await db_session.commit()

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    ids = await _load_character_ids(sessions, novel.id)
    assert ids == {known_id}
    registry = await _load_character_registry(sessions, novel.id)
    assert len(registry) == 1
    assert registry[0]["name"] == "阿宁"
    assert registry[0]["aliases"] == ["阿宁", "宁宁"]
