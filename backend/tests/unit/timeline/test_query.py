"""Owner-scoped, visible-set-first timeline reads (query layer)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisRun, AnalysisVersion
from app.models.novel import Chapter, Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineCausalEdge,
    TimelineEvidenceRef,
    TimelineOverride,
    TimelineParticipant,
)
from app.models.user import User
from app.schemas.timeline import TimelineOrdering, TimelineVersionSource
from app.services.timeline.query import (
    build_version_view,
    effective_narrative_bounds,
    resolve_chapter_cutoff,
    resolve_version_id,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# effective_narrative_bounds (pure)
# ---------------------------------------------------------------------------


def test_bounds_hide_all_when_spoiler_closed_and_no_cutoff():
    assert effective_narrative_bounds(spoiler_cutoff=None, spoiler_open=False) == (
        True,
        None,
        None,
    )


def test_bounds_open_without_cutoff_is_unbounded():
    assert effective_narrative_bounds(spoiler_cutoff=None, spoiler_open=True) == (
        False,
        None,
        None,
    )


def test_bounds_applies_spoiler_cutoff_as_upper():
    assert effective_narrative_bounds(spoiler_cutoff=10, spoiler_open=False) == (
        False,
        None,
        10,
    )


def test_bounds_chapter_end_narrows_upper_via_min():
    assert effective_narrative_bounds(
        spoiler_cutoff=10, spoiler_open=False, chapter_end=5
    ) == (False, None, 5)
    assert effective_narrative_bounds(
        spoiler_cutoff=10, spoiler_open=False, chapter_end=15
    ) == (False, None, 10)


def test_bounds_chapter_start_is_lower_floor():
    assert effective_narrative_bounds(
        spoiler_cutoff=10, spoiler_open=False, chapter_start=3
    ) == (False, 3, 10)


def test_bounds_open_spoiler_with_chapter_range():
    assert effective_narrative_bounds(
        spoiler_cutoff=None, spoiler_open=True, chapter_start=1, chapter_end=8
    ) == (False, 1, 8)


# ---------------------------------------------------------------------------
# resolve_version_id
# ---------------------------------------------------------------------------


async def _seed_reader_scope(db_session: AsyncSession, *, progress=None):
    owner = User(username="query-owner", email="query@example.com", hashed_password="x")
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(
        owner_id=owner.id,
        title="查询书",
        status="ready",
        reading_progress=progress,
    )
    db_session.add(novel)
    await db_session.flush()
    version = AnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="query-v1",
        status="active",
        source_snapshot_hash="a" * 64,
        hierarchy_build_id="build-1",
        hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={},
        decoding_hash="e" * 64,
        config_hash="f" * 64,
        price_snapshot={},
        manifest={},
    )
    db_session.add(version)
    await db_session.flush()
    return owner, novel, version


@pytest.mark.asyncio
async def test_resolve_version_id_active_without_pointer_returns_none(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    await db_session.commit()
    result = await resolve_version_id(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        source=TimelineVersionSource.ACTIVE,
    )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_version_id_active_with_pointer(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    db_session.add(
        TimelineActivePointer(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            revision=1,
            manifest_checksum="0" * 64,
        )
    )
    await db_session.commit()
    version_id, status, progress = await resolve_version_id(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        source=TimelineVersionSource.ACTIVE,
    )
    assert version_id == version.id
    assert status == "active"


@pytest.mark.asyncio
async def test_resolve_version_id_running_candidate(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    db_session.add(
        AnalysisRun(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            active_key="active",
            status="running",
            progress={"completed_chapters": 2},
        )
    )
    await db_session.commit()
    version_id, status, progress = await resolve_version_id(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        source=TimelineVersionSource.RUNNING_CANDIDATE,
    )
    assert version_id == version.id
    assert status == "running"
    assert progress == {"completed_chapters": 2}


@pytest.mark.asyncio
async def test_resolve_version_id_ignores_completed_runs(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    db_session.add(
        AnalysisRun(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            active_key="active",
            status="completed",
            progress={},
        )
    )
    await db_session.commit()
    result = await resolve_version_id(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        source=TimelineVersionSource.RUNNING_CANDIDATE,
    )
    assert result is None


# ---------------------------------------------------------------------------
# resolve_chapter_cutoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_chapter_cutoff_uses_reading_progress_chapter(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    c1 = Chapter(novel_id=novel.id, chapter_number=1, title="一", content="正文")
    c2 = Chapter(novel_id=novel.id, chapter_number=2, title="二", content="正文")
    c3 = Chapter(novel_id=novel.id, chapter_number=3, title="三", content="正文")
    db_session.add_all([c1, c2, c3])
    await db_session.flush()
    novel.reading_progress = {"chapter_id": c2.id}
    await db_session.commit()
    cutoff = await resolve_chapter_cutoff(db_session, novel)
    assert cutoff == 2


@pytest.mark.asyncio
async def test_resolve_chapter_cutoff_falls_back_to_first_chapter(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    c1 = Chapter(novel_id=novel.id, chapter_number=1, title="一", content="正文")
    c2 = Chapter(novel_id=novel.id, chapter_number=2, title="二", content="正文")
    db_session.add_all([c1, c2])
    await db_session.commit()
    cutoff = await resolve_chapter_cutoff(db_session, novel)
    assert cutoff == 1


@pytest.mark.asyncio
async def test_resolve_chapter_cutoff_returns_none_without_chapters(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    await db_session.commit()
    assert await resolve_chapter_cutoff(db_session, novel) is None


# ---------------------------------------------------------------------------
# build_version_view
# ---------------------------------------------------------------------------


def _add_event(
    db_session,
    *,
    version_id,
    owner_id,
    novel_id,
    logical_event_id,
    narrative_chapter_number,
    narrative_index,
    story_rank=None,
    title="事件",
):
    event = MachineTimelineEvent(
        version_id=version_id,
        owner_id=owner_id,
        novel_id=novel_id,
        logical_event_id=logical_event_id,
        title=title,
        description="描述",
        event_type="plot",
        time_precision="unknown",
        narrative_chapter_number=narrative_chapter_number,
        narrative_index=narrative_index,
        story_rank=story_rank,
        story_constraints=[],
        confidence=0.9,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={},
        publication_status="provisional",
    )
    db_session.add(event)
    return event


@pytest.mark.asyncio
async def test_build_version_view_active_with_full_book_gate_off(db_session):
    owner, novel, version = await _seed_reader_scope(
        db_session, progress={"chapter_id": None, "timeline_full_book": False}
    )
    c1 = Chapter(novel_id=novel.id, chapter_number=1, title="一", content="正文")
    c2 = Chapter(novel_id=novel.id, chapter_number=2, title="二", content="正文")
    db_session.add_all([c1, c2])
    await db_session.flush()
    db_session.add(
        TimelineActivePointer(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            revision=1,
            manifest_checksum="0" * 64,
        )
    )
    ev1 = _add_event(
        db_session,
        version_id=version.id,
        owner_id=owner.id,
        novel_id=novel.id,
        logical_event_id="1:e1",
        narrative_chapter_number=1,
        narrative_index=0,
    )
    ev2 = _add_event(
        db_session,
        version_id=version.id,
        owner_id=owner.id,
        novel_id=novel.id,
        logical_event_id="2:e2",
        narrative_chapter_number=2,
        narrative_index=0,
    )
    await db_session.flush()
    db_session.add(
        TimelineEvidenceRef(
            event_id=ev1.id,
            chapter_id=c1.id,
            evidence_id="ev-1",
            source_start=0,
            source_end=1,
            content_hash="0" * 64,
        )
    )
    db_session.add(TimelineParticipant(event_id=ev1.id, entity_id=None, mention="阿宁"))
    db_session.add(TimelineParticipant(event_id=ev2.id, entity_id=None, mention="阿宁"))
    db_session.add(
        TimelineCausalEdge(
            version_id=version.id,
            source_event_id=ev1.id,
            target_event_id=ev2.id,
            edge_type="causes",
            confidence=0.7,
            evidence_refs=[],
        )
    )
    await db_session.commit()

    view = await build_version_view(
        db_session,
        novel=novel,
        owner_id=owner.id,
        source=TimelineVersionSource.ACTIVE,
        ordering=TimelineOrdering.NARRATIVE,
        person=None,
        include_causal=True,
        request_full_book=False,
    )
    assert view is not None
    # spoiler gate hides chapter 2 (cutoff from first chapter when no progress)
    assert [e.logical_event_id for e in view.events] == ["1:e1"]
    assert view.events[0].participants[0].mention == "阿宁"
    assert view.counts.events == 1
    assert view.aggregates == {"阿宁": 1}
    assert view.previews == ["事件"]


@pytest.mark.asyncio
async def test_build_version_view_running_candidate_bypasses_spoiler(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    db_session.add(
        AnalysisRun(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            active_key="active",
            status="running",
            progress={},
        )
    )
    c1 = Chapter(novel_id=novel.id, chapter_number=1, title="一", content="正文")
    c2 = Chapter(novel_id=novel.id, chapter_number=2, title="二", content="正文")
    db_session.add_all([c1, c2])
    await db_session.flush()
    _add_event(
        db_session,
        version_id=version.id,
        owner_id=owner.id,
        novel_id=novel.id,
        logical_event_id="2:e2",
        narrative_chapter_number=2,
        narrative_index=0,
    )
    await db_session.commit()
    view = await build_version_view(
        db_session,
        novel=novel,
        owner_id=owner.id,
        source=TimelineVersionSource.RUNNING_CANDIDATE,
        ordering=TimelineOrdering.NARRATIVE,
        person=None,
        include_causal=False,
        request_full_book=False,
    )
    assert view is not None
    assert [e.logical_event_id for e in view.events] == ["2:e2"]
    assert view.counts.causal_edges == 0


@pytest.mark.asyncio
async def test_build_version_view_person_filter_and_override(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    c1 = Chapter(novel_id=novel.id, chapter_number=1, title="一", content="正文")
    db_session.add(c1)
    await db_session.flush()
    db_session.add(
        TimelineActivePointer(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            revision=1,
            manifest_checksum="0" * 64,
        )
    )
    ev_a = _add_event(
        db_session,
        version_id=version.id,
        owner_id=owner.id,
        novel_id=novel.id,
        logical_event_id="1:ea",
        narrative_chapter_number=1,
        narrative_index=0,
        title="阿宁事件",
    )
    _add_event(
        db_session,
        version_id=version.id,
        owner_id=owner.id,
        novel_id=novel.id,
        logical_event_id="1:eb",
        narrative_chapter_number=1,
        narrative_index=1,
        title="路人事件",
    )
    await db_session.flush()
    db_session.add(
        TimelineParticipant(event_id=ev_a.id, entity_id=None, mention="阿宁")
    )
    db_session.add(
        TimelineOverride(
            owner_id=owner.id,
            novel_id=novel.id,
            logical_event_id="1:ea",
            field_name="title",
            value="改过",
            status="active",
            needs_relink=False,
        )
    )
    await db_session.commit()

    view = await build_version_view(
        db_session,
        novel=novel,
        owner_id=owner.id,
        source=TimelineVersionSource.ACTIVE,
        ordering=TimelineOrdering.STORY,
        person="阿宁",
        include_causal=False,
        request_full_book=True,  # full book granted by flag
    )
    assert view is not None
    assert [e.logical_event_id for e in view.events] == ["1:ea"]
    assert view.events[0].title == "改过"
    assert view.events[0].provenance == {"title": "manual"}


@pytest.mark.asyncio
async def test_build_version_view_returns_none_without_pointer(db_session):
    owner, novel, version = await _seed_reader_scope(db_session)
    await db_session.commit()
    view = await build_version_view(
        db_session,
        novel=novel,
        owner_id=owner.id,
        source=TimelineVersionSource.ACTIVE,
        ordering=TimelineOrdering.NARRATIVE,
        person=None,
        include_causal=False,
        request_full_book=False,
    )
    assert view is None
