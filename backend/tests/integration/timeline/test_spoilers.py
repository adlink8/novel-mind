import pytest

from app.models.analysis import AnalysisVersion
from app.models.novel import Chapter, Novel
from app.models.timeline import (
    MachineTimelineEvent, TimelineActivePointer, TimelineCausalEdge,
    TimelineOverride, TimelineParticipant,
)
from app.models.user import User
from app.schemas.timeline import TimelineOrdering, TimelineVersionSource
from app.services.timeline.query import build_version_view

pytestmark = pytest.mark.integration


async def _seed(db_session):
    owner = User(username="spoiler-owner", email="spoiler@example.test", hashed_password="x")
    other = User(username="spoiler-other", email="other@example.test", hashed_password="x")
    db_session.add_all([owner, other]); await db_session.flush()
    novel = Novel(owner_id=owner.id, title="防剧透", status="ready", reading_progress={})
    db_session.add(novel); await db_session.flush()
    chapters = [Chapter(novel_id=novel.id, chapter_number=n, title=f"第{n}章", content="正文") for n in (1, 2)]
    db_session.add_all(chapters); await db_session.flush()
    version = AnalysisVersion(owner_id=owner.id, novel_id=novel.id, version_key="v1", status="active",
        source_snapshot_hash="a"*64, hierarchy_build_id="build", hierarchy_checksum="b"*64,
        prompt_hash="c"*64, schema_hash="d"*64, model_lineage={}, decoding_hash="e"*64,
        config_hash="f"*64, price_snapshot={}, manifest={})
    db_session.add(version); await db_session.flush()
    first = MachineTimelineEvent(version_id=version.id, owner_id=owner.id, novel_id=novel.id,
        logical_event_id="first", title="可见", description="第一章", event_type="plot",
        time_precision="unknown", narrative_chapter_number=1, narrative_index=1, story_rank=2,
        story_constraints=[], confidence=.9, prompt_hash="c"*64, schema_hash="d"*64,
        model_lineage={}, publication_status="published")
    future = MachineTimelineEvent(version_id=version.id, owner_id=owner.id, novel_id=novel.id,
        logical_event_id="future", title="未来", description="第二章秘密", event_type="plot",
        time_precision="unknown", narrative_chapter_number=2, narrative_index=0, story_rank=1,
        story_constraints=[], confidence=.9, prompt_hash="c"*64, schema_hash="d"*64,
        model_lineage={}, publication_status="published")
    db_session.add_all([first, future]); await db_session.flush()
    db_session.add_all([
        TimelineParticipant(event_id=first.id, mention="阿宁"),
        TimelineParticipant(event_id=future.id, mention="隐藏人物"),
        TimelineCausalEdge(version_id=version.id, source_event_id=first.id, target_event_id=future.id,
                           edge_type="causes", confidence=.8, evidence_refs=[]),
        TimelineOverride(owner_id=owner.id, novel_id=novel.id, logical_event_id="future",
                         field_name="title", value="SECRET OVERRIDE"),
        TimelineActivePointer(owner_id=owner.id, novel_id=novel.id, version_id=version.id,
                              revision=1, manifest_checksum="0"*64),
    ])
    await db_session.commit()
    return owner, novel, chapters


async def _view(db_session, owner, novel, **kwargs):
    return await build_version_view(db_session, novel=novel, owner_id=owner.id,
        source=TimelineVersionSource.ACTIVE, ordering=kwargs.pop("ordering", TimelineOrdering.NARRATIVE),
        person=kwargs.pop("person", None), include_causal=kwargs.pop("causal", True),
        request_full_book=kwargs.pop("full_book", False))


@pytest.mark.asyncio
async def test_d20_no_progress_defaults_to_first_chapter_and_derives_everything_visible_first(db_session):
    owner, novel, _ = await _seed(db_session)
    view = await _view(db_session, owner, novel)
    assert [event.title for event in view.events] == ["可见"]
    assert view.causal_edges == []
    assert view.counts.events == 1 and view.counts.participants == 1
    assert view.aggregates == {"阿宁": 1}
    assert view.previews == ["可见"]
    assert "SECRET" not in view.model_dump_json()
    assert "隐藏人物" not in view.model_dump_json()


@pytest.mark.asyncio
async def test_full_book_requires_persisted_preference_and_story_person_filters_do_not_leak(db_session):
    owner, novel, chapters = await _seed(db_session)
    novel.reading_progress = {"chapter_id": chapters[0].id, "timeline_full_book": False}
    await db_session.commit()
    denied = await _view(db_session, owner, novel, full_book=True, person="隐藏人物")
    assert denied.events == [] and denied.counts.events == 0 and denied.aggregates == {}
    assert denied.previews == []

    novel.reading_progress = {"chapter_id": chapters[0].id, "timeline_full_book": True}
    await db_session.commit()
    allowed = await _view(db_session, owner, novel, full_book=True,
                          person="隐藏人物", ordering=TimelineOrdering.STORY)
    assert [event.title for event in allowed.events] == ["SECRET OVERRIDE"]
    assert allowed.counts.events == 1
